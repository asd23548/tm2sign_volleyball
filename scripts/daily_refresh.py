"""Incremental daily refresh of all NCVA Power League sources into SQLite.

Sources:
  1. TM2Sign public API — matches / teams / rankings (current + prior season)
  2. ncva.com Power League points (Google Sheets + PDFs)
  3. TM2 rosters — new teams, plus optional re-fetch of the current season

Usage:
    python scripts/daily_refresh.py
    python scripts/daily_refresh.py --min-year 2025 --refresh-current-rosters
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SUMMARY_PATH = PROJECT_ROOT / "data" / "daily_refresh_summary.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_step(name: str, fn) -> dict[str, Any]:
    started = utc_now()
    try:
        result = fn()
        return {
            "ok": True,
            "started_at": started,
            "finished_at": utc_now(),
            "result": result,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "started_at": started,
            "finished_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc()[-2000:],
        }


def run_refresh(
    *,
    min_year: int,
    workers: int = 4,
    roster_workers: int = 2,
    skip_matches: bool = False,
    skip_points: bool = False,
    skip_rosters: bool = False,
    refresh_current_rosters: bool = True,
) -> dict[str, Any]:
    current_year = datetime.now(timezone.utc).year
    summary: dict[str, Any] = {
        "started_at": utc_now(),
        "min_year": min_year,
        "current_year": current_year,
        "steps": {},
    }

    if not skip_matches:
        from scripts.load_power_league import load_power_league

        print(f"\n=== TM2 Power League matches ({min_year}+, no reset) ===", flush=True)
        summary["steps"]["matches"] = _safe_step(
            "matches",
            lambda: load_power_league(
                reset=False,
                workers=workers,
                min_year=min_year,
                include_future_without_matches=True,
            ),
        )
        matches_step = summary["steps"]["matches"]
        if matches_step.get("ok"):
            # Keep the summary JSON small for git
            res = matches_step.get("result") or {}
            matches_step["result"] = {
                "events_loaded": len(res.get("events") or []),
                "db_counts": res.get("db_counts"),
                "error_count": len(res.get("errors") or []),
                "errors": (res.get("errors") or [])[:8],
            }

    if not skip_points:
        from scripts.load_ncva_points import load_ncva_points

        print(f"\n=== NCVA Power League points ({min_year}+) ===", flush=True)
        summary["steps"]["points"] = _safe_step(
            "points",
            lambda: load_ncva_points(min_year=min_year),
        )

    if not skip_rosters:
        from scripts.backfill_rosters import backfill_rosters

        print(f"\n=== Rosters: new teams since {min_year} ===", flush=True)
        summary["steps"]["rosters_new"] = _safe_step(
            "rosters_new",
            lambda: backfill_rosters(
                workers=roster_workers,
                resume=True,
                min_year=min_year,
            ),
        )
        if refresh_current_rosters:
            print(f"\n=== Rosters: refresh current season {current_year} ===", flush=True)
            summary["steps"]["rosters_current"] = _safe_step(
                "rosters_current",
                lambda: backfill_rosters(
                    workers=roster_workers,
                    resume=False,
                    min_year=current_year,
                ),
            )

    summary["finished_at"] = utc_now()
    summary["ok"] = all(step.get("ok", False) for step in summary["steps"].values()) if summary["steps"] else False
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    current_year = datetime.now(timezone.utc).year
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-year",
        type=int,
        default=current_year - 1,
        help="Reload TM2 events / points / missing rosters from this year onward (default: prior year)",
    )
    parser.add_argument("--workers", type=int, default=4, help="TM2 match/team fetch workers")
    parser.add_argument("--roster-workers", type=int, default=2)
    parser.add_argument("--skip-matches", action="store_true")
    parser.add_argument("--skip-points", action="store_true")
    parser.add_argument("--skip-rosters", action="store_true")
    parser.add_argument(
        "--no-refresh-current-rosters",
        action="store_true",
        help="Only backfill teams still missing rosters (do not re-fetch current season)",
    )
    args = parser.parse_args()
    summary = run_refresh(
        min_year=args.min_year,
        workers=args.workers,
        roster_workers=args.roster_workers,
        skip_matches=args.skip_matches,
        skip_points=args.skip_points,
        skip_rosters=args.skip_rosters,
        refresh_current_rosters=not args.no_refresh_current_rosters,
    )
    print(json.dumps({k: summary[k] for k in ("started_at", "finished_at", "ok", "min_year", "current_year") if k in summary}, indent=2))
    if not summary.get("ok"):
        for name, step in (summary.get("steps") or {}).items():
            if not step.get("ok"):
                print(f"FAILED {name}: {step.get('error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
