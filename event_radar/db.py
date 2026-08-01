"""SQLite access layer. Plain sqlite3, no ORM (spec section 1).

Migrations are simple forward-only .sql files applied in filename order. A
`schema_migrations` table records which ones ran, so `connect()` is safe to call
on every command and only applies what is missing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str) -> sqlite3.Connection:
    """Open the database, apply any pending migrations, return the connection."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _apply_migrations(connection)
    return connection


def _apply_migrations(connection: sqlite3.Connection) -> None:
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
