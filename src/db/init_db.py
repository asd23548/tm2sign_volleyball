"""Initialize SQLite database (delegates to Sprint 1 schema)."""

from __future__ import annotations

from pathlib import Path

from database.init_sqlite import init_db as _init_sprint1


def init_database(db_path: Path | str | None = None) -> Path:
    return _init_sprint1(db_path, reset=False)


if __name__ == "__main__":
    created = init_database()
    print(f"Initialized database at {created}")
