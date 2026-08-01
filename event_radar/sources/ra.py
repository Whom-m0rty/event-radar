"""Resident Advisor source.

RA has no public API. This talks to the same undocumented GraphQL endpoint the
ra.co frontend uses (POST https://ra.co/graphql). robots.txt allows the generic
`User-agent: *` on /graphql; we still behave: an honest self-identifying UA with
a contact, >=2s between requests, exponential-backoff retries, and a disk cache
so debugging never hammers their server.

The schema is unofficial and may change without notice. Every per-event parse is
isolated: a shape change drops that one event with a log, the batch survives.
"""
from __future__ import annotations

import logging
import re
import time

import requests
import requests_cache
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from event_radar.models import RawEvent

logger = logging.getLogger(__name__)

RA_ENDPOINT = "https://ra.co/graphql"

# Verified working listing query (fields confirmed live, Aug 2026). Kept verbatim
# — do not hand-edit GraphQL you did not capture; __typename removed only because
# we never read it. Note: the listing has no price/description; those need a
# separate detail pass (see fetch_detail, TODO for the price phase).
_LISTINGS_QUERY = (
    "query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, "
    "$filterOptions: FilterOptionsInputDtoInput, $page: Int, $pageSize: Int) {"
    "eventListings(filters: $filters, filterOptions: $filterOptions, "
    "pageSize: $pageSize, page: $page) {"
    "data { id listingDate event { id date startTime endTime title contentUrl "
    "flyerFront isTicketed attending cost tickets { priceRetail validType } "
    "venue { id name contentUrl live } "
    "artists { id name } } } totalResults } }"
)

# RA's `cost` is free text ("0-5-10€", "50", "0", "Free", ""). Parse a floor price.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def parse_cost(cost: str | None, is_ticketed: bool | None, ticket_prices=None):
    """Return (price_min, is_free) from RA's messy cost string + any ticket prices."""
    ticket_prices = ticket_prices or []
    text = (cost or "").strip().lower()

    if any(word in text for word in ("free", "gratis", "gratuito")):
        return 0.0, True

    numbers = [float(match.replace(",", ".")) for match in _NUMBER.findall(text)]
    for price in ticket_prices:
        if price is not None:
            numbers.append(float(price))

    if numbers:
        price_min = min(numbers)
        is_free = all(value == 0 for value in numbers)
        return price_min, is_free

    # No numbers anywhere: fall back to the isTicketed hint (weak).
    if is_ticketed is False:
        return None, True
    return None, None

_AREA_QUERY = "{ areas(searchTerm: \"%s\", limit: 5) { id name country { name } } }"

# RA caps pageSize; 20 is the value its own frontend sends.
_PAGE_SIZE = 20


class ResidentAdvisor:
    name = "ra"

    def __init__(
        self,
        contact: str,
        area_id: int,
        user_agent_template: str = "EventRadar/0.1 (+mailto:{contact})",
        request_delay_seconds: float = 2.0,
        cache_expire_hours: float = 6.0,
        max_retries: int = 4,
        cache_path: str = "http_cache",
    ) -> None:
        self.area_id = area_id
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max_retries
        self._last_network_call = 0.0

        user_agent = user_agent_template.format(contact=contact)
        self.session = requests_cache.CachedSession(
            cache_name=cache_path,
            backend="sqlite",
            expire_after=cache_expire_hours * 3600,
            allowable_methods=("GET", "POST"),
        )
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": user_agent,
                # RA rejects some requests without a matching Referer.
                "Referer": "https://ra.co/events",
            }
        )

    # -- public API -------------------------------------------------------

    def fetch(self, city: str, date_from: str, date_to: str) -> list[RawEvent]:
        """All Milan events in [date_from, date_to] (ISO 'YYYY-MM-DD' dates)."""
        gte = f"{date_from}T00:00:00.000Z"
        lte = f"{date_to}T00:00:00.000Z"

        events: list[RawEvent] = []
        page = 1
        total_results = None
        while True:
            payload = self._post(self._listings_payload(gte, lte, page))
            listing = (payload.get("data") or {}).get("eventListings") or {}
            if total_results is None:
                total_results = listing.get("totalResults")
            rows = listing.get("data") or []
            if not rows:
                break

            for row in rows:
                parsed = self._parse_row(row, city)
                if parsed is not None:
                    events.append(parsed)

            if total_results is not None and len(events) >= total_results:
                break
            if len(rows) < _PAGE_SIZE:
                break
            page += 1

        logger.info("RA: fetched %d events for %s (%s..%s)", len(events), city, date_from, date_to)
        return events

    def resolve_area(self, search_term: str) -> int | None:
        """Map a city slug/name to RA's numeric area id (e.g. 'milan' -> 347)."""
        payload = self._post({"query": _AREA_QUERY % search_term})
        areas = (payload.get("data") or {}).get("areas") or []
        if not areas:
            return None
        return int(areas[0]["id"])

    # -- internals --------------------------------------------------------

    def _listings_payload(self, gte: str, lte: str, page: int) -> dict:
        return {
            "operationName": "GET_EVENT_LISTINGS",
            "variables": {
                "filters": {
                    "areas": {"eq": self.area_id},
                    "listingDate": {"gte": gte, "lte": lte},
                },
                "filterOptions": {"genre": True},
                "pageSize": _PAGE_SIZE,
                "page": page,
            },
            "query": _LISTINGS_QUERY,
        }

    def _post(self, body: dict) -> dict:
        self._respect_rate_limit()
        response = self._send(body)
        # Only real network calls count toward the rate limit; cache hits are free.
        if not getattr(response, "from_cache", False):
            self._last_network_call = time.monotonic()
        response.raise_for_status()
        return response.json()

    def _send(self, body: dict) -> requests.Response:
        # tenacity needs to see the instance's max_retries, so build the retrier here.
        retrier = retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(
                (requests.ConnectionError, requests.Timeout, _RetryableStatus)
            ),
        )

        @retrier
        def do_send() -> requests.Response:
            response = self.session.post(RA_ENDPOINT, json=body, timeout=30)
            if response.status_code >= 500 or response.status_code == 429:
                raise _RetryableStatus(response.status_code)
            return response

        return do_send()

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_network_call
        wait = self.request_delay_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def _parse_row(self, row: dict, city: str) -> RawEvent | None:
        """Map one listing row to a RawEvent. Never raises: logs and returns None."""
        try:
            event = row["event"]
            ra_id = str(event["id"])
            venue = event.get("venue") or {}
            venue_name = venue.get("name")
            lineup = []
            for artist in event.get("artists") or []:
                name = artist.get("name")
                if name:
                    lineup.append(name)

            content_url = event.get("contentUrl") or ""
            url = f"https://ra.co{content_url}" if content_url else None

            # Price comes from the free-text `cost` field plus any ticket prices.
            ticket_prices = []
            for ticket in event.get("tickets") or []:
                ticket_prices.append(ticket.get("priceRetail"))
            price_min, is_free = parse_cost(
                event.get("cost"), event.get("isTicketed"), ticket_prices
            )

            return RawEvent(
                ra_id=ra_id,
                title=event.get("title"),
                venue_name=venue_name,
                city=city,
                starts_at=event.get("startTime"),
                ends_at=event.get("endTime"),
                price_min=price_min,
                is_free=is_free,
                url=url,
                description_raw=None,
                lineup=lineup,
                is_open_air=guess_open_air(event.get("title"), venue_name),
            )
        except (KeyError, TypeError) as error:
            logger.warning("RA: skipping unparseable event row: %s", error)
            return None


class _RetryableStatus(Exception):
    """Raised for 5xx/429 so tenacity retries with backoff."""


# Kept module-level and importable so it can be unit-tested directly.
_DEFAULT_OPEN_AIR_MARKERS = (
    "open air",
    "open-air",
    "openair",
    "terrazza",
    "terrace",
    "rooftop",
    "giardino",
    "outdoor",
    "spiaggia",
    "beach",
)


def guess_open_air(title: str | None, venue_name: str | None, markers=_DEFAULT_OPEN_AIR_MARKERS) -> bool:
    """Best-effort open-air flag: RA exposes no clean field (spec note).

    Matches marker substrings against title + venue. It is a guess, not truth —
    treated as a weak signal in scoring and logged as heuristic-derived.
    """
    haystack = " ".join(part for part in (title, venue_name) if part).lower()
    for marker in markers:
        if marker in haystack:
            return True
    return False
