#!/usr/bin/env python3
"""Initialize the Mattermost archive SQLite database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/mattermost.sqlite")
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "mattermost_archiver" / "schema.sql"


def resolve_db_path() -> Path:
    """Return the database path from env or the project default."""
    return Path(os.environ.get("ARCHIVER_DB_PATH", DEFAULT_DB_PATH)).expanduser()


def init_db(db_path: Path) -> None:
    """Create or migrate the SQLite schema idempotently."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema_sql)
        conn.commit()


def main() -> None:
    db_path = resolve_db_path()
    init_db(db_path)
    print(f"Initialized SQLite database: {db_path}")


if __name__ == "__main__":
    main()
