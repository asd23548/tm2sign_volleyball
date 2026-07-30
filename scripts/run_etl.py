"""Run ETL using discovered TM2Sign API schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.etl.pipeline import run_etl


def main() -> None:
    parser = argparse.ArgumentParser(description="TM2Sign live ETL into SQLite")
    parser.add_argument("--pages", type=int, default=2, help="Event list pages to scan")
    parser.add_argument("--max-events", type=int, default=30, help="Max events with data to load")
    parser.add_argument("--no-demo-fallback", action="store_true")
    args = parser.parse_args()
    result = run_etl(
        use_demo_fallback=not args.no_demo_fallback,
        max_pages=args.pages,
        max_events=args.max_events,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
