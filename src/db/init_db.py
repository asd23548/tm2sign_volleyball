"""Initialize SQLite database from schema.sql."""

from __future__ import annotations

from pathlib import Path

from .connection import get_connection, DB_PATH

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_database(db_path: Path | str | None = None) -> Path:
    path = Path(db_path) if db_path else DB_PATH
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_connection(path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    created = init_database()
    print(f"Initialized database at {created}")