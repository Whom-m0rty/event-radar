"""SQLite / Turso (libSQL) access layer. Plain SQL, no ORM (spec section 1).

Two backends, chosen by env:
  * default        -> local sqlite3 file (dev + tests).
  * TURSO_*         -> a libSQL embedded replica synced to Turso (serverless).

libSQL returns rows as plain tuples and its cursor is not iterable, so the Turso
path is wrapped to behave like sqlite3 (row["col"], iteration, executescript,
lastrowid). The replica pulls on connect and pushes on close, so a stateless
GitHub Actions run reads the latest state and writes back to Turso.

Migrations are forward-only .sql files applied in filename order.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


# -- libSQL (Turso) wrappers ---------------------------------------------


class _Row:
    """Dict- and index-addressable row over a libSQL tuple + column names."""

    def __init__(self, columns: list[str], values: tuple):
        self._columns = columns
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._columns.index(key)]

    def __iter__(self):
        return iter(self._values)

    def keys(self):
        return list(self._columns)


class _Cursor:
    def __init__(self, raw_cursor):
        self._raw = raw_cursor
        description = getattr(raw_cursor, "description", None)
        self._columns = [column[0] for column in description] if description else []

    @property
    def lastrowid(self):
        return self._raw.lastrowid

    def fetchone(self):
        value = self._raw.fetchone()
        return _Row(self._columns, value) if value is not None else None

    def fetchall(self):
        return [_Row(self._columns, value) for value in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _LibsqlConnection:
    def __init__(self, raw):
        self._raw = raw
        self.row_factory = None  # accepted for API parity, ignored

    def execute(self, sql: str, params=()):
        return _Cursor(self._raw.execute(sql, params))

    def executescript(self, script: str):
        self._raw.executescript(script)
        return self

    def commit(self):
        self._raw.commit()

    def sync(self):
        self._raw.sync()

    def close(self):
        # Push local writes to Turso. Best-effort so a sync hiccup isn't fatal.
        try:
            self._raw.sync()
        except Exception:  # noqa: BLE001
            pass


def _connect_turso(url: str, token: str):
    import libsql_experimental as libsql

    replica_path = os.environ.get("TURSO_REPLICA_PATH", ".turso_replica.db")
    raw = libsql.connect(replica_path, sync_url=url, auth_token=token)
    raw.sync()  # pull the latest state (incl. feedback the bot wrote) before we run
    return _LibsqlConnection(raw)


# -- public API -----------------------------------------------------------


def connect(db_path: str):
    """Open the database (Turso if configured, else local), apply migrations."""
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if url and token:
        connection = _connect_turso(url, token)
    else:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
    _apply_migrations(connection)
    return connection


def _apply_migrations(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    applied = set()
    for row in connection.execute("SELECT name FROM schema_migrations"):
        applied.add(row["name"])

    for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if migration_path.name in applied:
            continue
        sql = migration_path.read_text(encoding="utf-8")
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations (name) VALUES (?)",
            (migration_path.name,),
        )
        connection.commit()
