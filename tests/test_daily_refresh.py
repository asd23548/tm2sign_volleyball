"""Smoke tests for daily refresh orchestration (no network)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_daily_refresh_script_parses() -> None:
    src = (ROOT / "scripts" / "daily_refresh.py").read_text(encoding="utf-8")
    ast.parse(src)


def test_load_power_league_year_filter() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from scripts.load_power_league import event_start_year

    assert event_start_year({"start_date": "2026-01-15"}) == 2026
    assert event_start_year({"start_date": "2025-11-01T00:00:00Z"}) == 2025
    assert event_start_year({}) is None
