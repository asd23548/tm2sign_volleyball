"""Load NCVA Girls Power League points (PDF + Google Sheets) into SQLite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from database.init_sqlite import DB_PATH, get_connection, init_db
from src.etl.ncva_points import (
    GIRLS_POINTS_URL,
    discover_girls_points_links,
    fetch_and_parse_source,
    points_id,
)

SUMMARY = PROJECT_ROOT / "data" / "ncva_points_load_summary.json"

POINTS_COLS = [
    "points_id",
    "season_year",
    "gender",
    "age_num",
    "team_code",
    "team_name",
    "overall_division",
    "overall_place",
    "overall_rank",
    "plq_place",
    "l1_place",
    "l1_division",
    "l1_points",
    "l2_place",
    "l2_division",
    "l2_points",
    "l3_place",
    "l3_division",
    "l3_points",
    "region_place",
    "region_division",
    "region_points",
    "season_total",
    "bid_notes",
    "source_url",
    "source_type",
    "fetched_at",
]


def ensure_points_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS power_league_points (
            points_id         TEXT PRIMARY KEY,
            season_year       INTEGER NOT NULL,
            gender            TEXT NOT NULL,
            age_num           INTEGER NOT NULL,
            team_code         TEXT NOT NULL,
            team_name         TEXT,
            overall_division  TEXT,
            overall_place     INTEGER,
            overall_rank      INTEGER,
            plq_place         INTEGER,
            l1_place          INTEGER,
            l1_division       TEXT,
            l1_points         REAL,
            l2_place          INTEGER,
            l2_division       TEXT,
            l2_points         REAL,
            l3_place          INTEGER,
            l3_division       TEXT,
            l3_points         REAL,
            region_place      INTEGER,
            region_division   TEXT,
            region_points     REAL,
            season_total      REAL,
            bid_notes         TEXT,
            source_url        TEXT,
            source_type       TEXT,
            fetched_at        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pl_points_year ON power_league_points(season_year, age_num);
        CREATE INDEX IF NOT EXISTS idx_pl_points_code ON power_league_points(team_code);
        CREATE INDEX IF NOT EXISTS idx_pl_points_total ON power_league_points(season_total);

        CREATE TABLE IF NOT EXISTS power_league_points_sources (
            source_id    TEXT PRIMARY KEY,
            season_year  INTEGER NOT NULL,
            gender       TEXT NOT NULL,
            age_num      INTEGER,
            label        TEXT,
            source_url   TEXT NOT NULL,
            source_type  TEXT NOT NULL,
            status       TEXT,
            rows_loaded  INTEGER,
            fetched_at   TEXT
        );
        """
    )
    conn.commit()


def upsert_points(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    placeholders = ", ".join(["?"] * len(POINTS_COLS))
    updates = ", ".join(f"{c}=excluded.{c}" for c in POINTS_COLS if c != "points_id")
    sql = (
        f"INSERT INTO power_league_points ({', '.join(POINTS_COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(points_id) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, [tuple(r.get(c) for c in POINTS_COLS) for r in rows])
    return len(rows)


def source_id(src: dict) -> str:
    age = src.get("age_num") if src.get("age_num") is not None else "x"
    return f"{src['season_year']}|{src.get('gender','Girls')}|{age}|{src['source_type']}|{src['source_url']}"


def load_ncva_points(
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    ages: list[int] | None = None,
    limit_sources: int | None = None,
    source_types: list[str] | None = None,
) -> dict:
    init_db(DB_PATH, reset=False)
    conn = get_connection(DB_PATH)
    ensure_points_schema(conn)

    print(f"Discovering links from {GIRLS_POINTS_URL} …", flush=True)
    sources = discover_girls_points_links()
    if min_year is not None:
        sources = [s for s in sources if s["season_year"] >= min_year]
    if max_year is not None:
        sources = [s for s in sources if s["season_year"] <= max_year]
    if ages:
        age_set = set(ages)
        sources = [s for s in sources if s.get("age_num") in age_set]
    if source_types:
        st = set(source_types)
        sources = [s for s in sources if s["source_type"] in st]
    if limit_sources is not None:
        sources = sources[:limit_sources]

    print(f"Loading {len(sources)} sources…", flush=True)
    total_rows = 0
    ok = 0
    empty = 0
    errors: list[dict] = []
    matched_codes = 0

    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (compatible; tm2sign-ncva-points/1.0)"}, follow_redirects=True, timeout=90) as client:
        for i, src in enumerate(sources, start=1):
            sid = source_id(src)
            try:
                rows = fetch_and_parse_source(src, client=client)
                upsert_points(conn, rows)
                status = "ok" if rows else "empty"
                if rows:
                    ok += 1
                    total_rows += len(rows)
                else:
                    empty += 1
                conn.execute(
                    """
                    INSERT INTO power_league_points_sources(
                        source_id, season_year, gender, age_num, label, source_url,
                        source_type, status, rows_loaded, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(source_id) DO UPDATE SET
                        status=excluded.status,
                        rows_loaded=excluded.rows_loaded,
                        fetched_at=excluded.fetched_at,
                        label=excluded.label
                    """,
                    (
                        sid,
                        src["season_year"],
                        src.get("gender") or "Girls",
                        src.get("age_num"),
                        src.get("label"),
                        src["source_url"],
                        src["source_type"],
                        status,
                        len(rows),
                    ),
                )
                conn.commit()
                print(
                    f"  {i}/{len(sources)} {src['season_year']} {src.get('age_num')}U "
                    f"{src['source_type']} rows={len(rows)}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                empty += 1
                errors.append({"source": src["source_url"], "error": str(exc)})
                conn.execute(
                    """
                    INSERT INTO power_league_points_sources(
                        source_id, season_year, gender, age_num, label, source_url,
                        source_type, status, rows_loaded, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'error', 0, datetime('now'))
                    ON CONFLICT(source_id) DO UPDATE SET
                        status='error', rows_loaded=0, fetched_at=excluded.fetched_at
                    """,
                    (
                        sid,
                        src["season_year"],
                        src.get("gender") or "Girls",
                        src.get("age_num"),
                        src.get("label"),
                        src["source_url"],
                        src["source_type"],
                    ),
                )
                conn.commit()
                print(f"  {i}/{len(sources)} ERROR {src['source_url']}: {exc}", flush=True)

    matched_codes = conn.execute(
        """
        SELECT COUNT(DISTINCT p.team_code)
        FROM power_league_points p
        JOIN teams t ON t.alt_code = p.team_code
        """
    ).fetchone()[0]
    n_points = conn.execute("SELECT COUNT(*) FROM power_league_points").fetchone()[0]
    summary = {
        "sources_attempted": len(sources),
        "sources_ok": ok,
        "sources_empty_or_error": empty,
        "rows_upserted_this_run": total_rows,
        "points_rows_total": n_points,
        "team_codes_matched_to_teams": matched_codes,
        "errors": errors[:25],
        "error_count": len(errors),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    conn.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-year", type=int, default=None)
    parser.add_argument("--max-year", type=int, default=None)
    parser.add_argument("--ages", type=str, default=None, help="Comma ages e.g. 14,15,16")
    parser.add_argument("--limit-sources", type=int, default=None)
    parser.add_argument(
        "--types",
        type=str,
        default=None,
        help="Comma source types: pdf,google,csv",
    )
    args = parser.parse_args()
    ages = [int(x) for x in args.ages.split(",")] if args.ages else None
    types = [x.strip() for x in args.types.split(",")] if args.types else None
    summary = load_ncva_points(
        min_year=args.min_year,
        max_year=args.max_year,
        ages=ages,
        limit_sources=args.limit_sources,
        source_types=types,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
