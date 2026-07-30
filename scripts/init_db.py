"""Initialize DB and optionally seed demo data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import init_database
from src.etl.demo_seed import seed_demo_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Seed demo dataset")
    args = parser.parse_args()
    path = init_database()
    print(f"DB ready: {path}")
    if args.demo:
        seed_demo_data()
        print("Demo data seeded.")


if __name__ == "__main__":
    main()
