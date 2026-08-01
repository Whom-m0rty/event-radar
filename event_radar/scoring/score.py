"""Score an event from its features. All weights/thresholds come from config.

breakdown is a dict {factor -> contribution}. It is persisted with every score:
without it there is no way to see WHY an event scored what it did, and no way to
hand-tune the weights. It is the whole point of a transparent formula over a model.

Music is a booster: an event earns `base` plus practical factors regardless of
taste, and any music match adds on top. So the calendar is never empty even when
taste doesn't overlap the local scene.
"""
from __future__ import annotations


def score_event(features: dict, config: dict) -> tuple[float, dict]:
    weights = config["weights"]
    music_cfg = config["music"]
    price_cfg = config["price"]
    weekend_days = config.get("weekend_days", [4, 5])
    venue_whitelist = config.get("venue_whitelist", []) or []

    breakdown: dict[str, float] = {}

    breakdown["base"] = float(weights.get("base", 0))

    music_component = (
        music_cfg["max_weight"] * features["music_max"]
        + music_cfg["mean_weight"] * features["music_mean"]
    )
    if music_component > 0:
        breakdown["music"] = round(weights["music"] * music_component, 2)

    # Price: free wins outright; otherwise cheap-bonus OR over-threshold penalty.
    # These branches are mutually exclusive so a free event never also gets cheap.
    if features["is_free"]:
        breakdown["free"] = float(weights["bonus_free"])
    else:
        price = features["price_min"]
        if price is not None:
            if price <= price_cfg["cheap_threshold_eur"]:
                breakdown["cheap"] = float(weights["bonus_cheap"])
            elif price > price_cfg["penalty_above_eur"]:
                over = price - price_cfg["penalty_above_eur"]
                breakdown["price_penalty"] = -round(weights["penalty_price"] * over, 2)

    if features["is_open_air"]:
        breakdown["open_air"] = float(weights["bonus_openair"])

    if features["weekday"] in weekend_days:
        breakdown["weekend"] = float(weights["bonus_weekend"])

    if features["venue_name"] and features["venue_name"] in venue_whitelist:
        breakdown["venue"] = float(weights["bonus_venue"])

    raw = sum(breakdown.values())
    score = max(0.0, min(100.0, raw))
    return round(score, 2), breakdown
