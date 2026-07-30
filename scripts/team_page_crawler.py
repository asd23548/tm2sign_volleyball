"""
Canonical NCVA Power League loader — mirrors the TM2 team results page.

Discovery:
  Search events for "NCVA Girls Power League" (optional Boys).

Per team (same APIs the page uses):
  GET /scheduler-teams/{id}
  GET /scheduler-teams/{id}/roster
  GET /scheduler-matches?filter[event_id]=&filter[team_id]=…

Usage:
  python scripts/team_page_crawler.py --gender girls
  python scripts/team_page_crawler.py --event-id 2136 --limit-teams 5
  python scripts/team_page_crawler.py --gender girls --resume
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.init_sqlite import (  # noqa: E402
    DB_PATH,
    get_connection,
    init_db,
    migrate_sprint2,
    refresh_derived_tables,
    vacuum_db,
)
from scripts.ncva_crawler import (  # noqa: E402
    UPSERT_CLUB,
    UPSERT_DIVISION,
    UPSERT_EVENT,
    UPSERT_MATCH,
    UPSERT_PROGRAM,
    UPSERT_REGION,
    UPSERT_STANDING,
    UPSERT_TEAM,
    RateLimitedClient,
    _division_rows,
    _event_row,
    _executemany,
    _match_row,
    _now,
    _standing_row,
    _team_row,
)
from src.etl.roster_transform import player_rows_from_roster, staff_rows_from_roster  # noqa: E402
from src.etl.tm2_client import BASE  # noqa: E402

SUMMARY = PROJECT_ROOT / "data" / "team_page_crawler_summary.json"
GIRLS_QUERY = "NCVA Girls Power League"
BOYS_QUERY = "NCVA Boys Power League"


def discover_events(client: RateLimitedClient, gender: str) -> list[dict[str, Any]]:
    """Find Power League events the same way the /app/events search does."""
    queries = []
    if gender in ("girls", "all"):
        queries.append(GIRLS_QUERY)
    if gender in ("boys", "all"):
        queries.append(BOYS_QUERY)

    found: dict[Any, dict[str, Any]] = {}
    for q in queries:
        for date_range in ("past", "future"):
            events = client.inner.iter_events(
                date_range=date_range,
                max_pages=10,
                search=q,
                include_divisions=True,
            )
            # Route through rate limit
            for ev in events:
                name = (ev.get("name") or "").lower()
                if "power league" not in name or "ncva" not in name:
                    continue
                if gender == "girls" and "girl" not in name:
                    continue
                if gender == "boys" and "boy" not in name:
                    continue
                found[ev["id"]] = ev
    return sorted(found.values(), key=lambda e: (e.get("start_date") or "", e.get("id") or 0))


def ensure_divisions_for_teams(event: dict[str, Any], teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    divisions = _division_rows(event)
    known = {d["division_id"] for d in divisions}
    gender = "Girls" if "girl" in (event.get("name") or "").lower() else (
        "Boys" if "boy" in (event.get("name") or "").lower() else None
    )
    for t in teams:
        did = t.get("event_division_id")
        if did is None:
            continue
        did_s = str(did)
        if did_s not in known:
            divisions.append(
                {
                    "division_id": did_s,
                    "event_id": str(event["id"]),
                    "division_name": f"Division {did_s}",
                    "age_group": None,
                    "gender": gender,
                }
            )
            known.add(did_s)
    return divisions


def fetch_team_matches(client: RateLimitedClient, event_id: str, team_id: str) -> list[dict[str, Any]]:
    """Same match query the team results page fires."""
    sid = str(team_id).removeprefix("ST-")
    rows: list[dict[str, Any]] = []

    def url_for_page(page: int) -> str:
        return (
            f"{BASE}/scheduler-matches?filter[event_id]={event_id}"
            f"&filter[team_id]={sid}"
            f"&include[]=teamOne&include[]=teamTwo&include[]=workTeam"
            f"&include[]=schedulerCourt&include[]=schedulerRound"
            f"&page={page}&per_page=100"
        )

    return client.paginate(url_for_page)


def upsert_roster(conn, roster: dict[str, Any], team_row: dict[str, Any]) -> tuple[int, int]:
    p_rows, p_stints = player_rows_from_roster(
        roster,
        team_id=team_row["team_id"],
        event_id=team_row["event_id"],
        program_id=team_row.get("program_id"),
        age_group=team_row.get("age_group"),
        season_year=None,
        gender_code=team_row.get("gender_code"),
        club_id=team_row.get("club_id"),
    )
    # season_year from events
    ey = conn.execute(
        "SELECT season_year FROM events WHERE event_id = ?", (team_row["event_id"],)
    ).fetchone()
    season_year = ey[0] if ey else None
    for s in p_stints:
        s["season_year"] = season_year

    s_rows, s_stints = staff_rows_from_roster(
        roster,
        team_id=team_row["team_id"],
        event_id=team_row["event_id"],
        program_id=team_row.get("program_id"),
        season_year=season_year,
        gender_code=team_row.get("gender_code"),
        club_id=team_row.get("club_id"),
    )

    def _upsert(table: str, rows: list[dict], pk: list[str]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(pk)}) DO UPDATE SET {updates}"
        )
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])

    _upsert("players", p_rows, ["player_id"])
    _upsert("player_season_stints", p_stints, ["player_id", "event_id", "team_id"])
    _upsert("staff", s_rows, ["staff_id"])
    _upsert("staff_season_stints", s_stints, ["staff_id", "event_id", "team_id", "position"])
    return len(p_rows), len(s_rows)


def crawl_team(
    conn,
    client: RateLimitedClient,
    *,
    event_id: str,
    raw_team: dict[str, Any],
    age_by_div: dict[str, str],
) -> dict[str, int]:
    tid = str(raw_team["id"])
    # Prefer live detail payload (same as page)
    try:
        detail = client.get_json(f"{BASE}/scheduler-teams/{tid}")
        if isinstance(detail, dict) and detail.get("id"):
            raw_team = {**raw_team, **detail}
    except Exception:
        pass

    team_row = _team_row(raw_team, event_id, age_by_div)
    # Identity parents
    region_rows = [{"region_id": "R-NCVA", "region_name": "Northern California", "state": "CA"}]
    if team_row.get("region_id"):
        region_rows.append(
            {
                "region_id": team_row["region_id"],
                "region_name": team_row["region_id"].replace("R-", "", 1),
                "state": "CA" if team_row["region_id"] in ("R-NC", "R-NCVA", "R-CA") else None,
            }
        )
    club_rows = []
    if team_row.get("club_id") and team_row.get("club_name"):
        club_rows.append(
            {
                "club_id": team_row["club_id"],
                "club_name": team_row["club_name"],
                "region_id": team_row.get("region_id"),
            }
        )
    program_rows = []
    if team_row.get("program_id") and team_row.get("program_label"):
        program_rows.append(
            {
                "program_id": team_row["program_id"],
                "program_label": team_row["program_label"],
                "club_id": team_row.get("club_id"),
                "gender_code": team_row.get("gender_code"),
                "tier_label": team_row.get("tier_label"),
            }
        )

    _executemany(conn, UPSERT_REGION, region_rows)
    _executemany(conn, UPSERT_CLUB, club_rows)
    _executemany(conn, UPSERT_PROGRAM, program_rows)
    _executemany(conn, UPSERT_TEAM, [team_row])
    standing = _standing_row(raw_team, event_id)
    if standing:
        _executemany(conn, UPSERT_STANDING, [standing])

    # Matches (team page)
    raw_matches = fetch_team_matches(client, event_id, tid)
    match_rows = []
    for raw in raw_matches:
        row = _match_row(raw, event_id)
        if row:
            match_rows.append(row)
            # Ensure opponent teams exist as stubs if missing
            for side, seed in ((row.get("team_a_id"), row.get("seed_a")), (row.get("team_b_id"), row.get("seed_b"))):
                if not side:
                    continue
                exists = conn.execute("SELECT 1 FROM teams WHERE team_id = ?", (side,)).fetchone()
                if exists:
                    continue
                stub = {
                    "team_id": side,
                    "event_id": event_id,
                    "division_id": row["division_id"],
                    "team_name": side,
                    "club_name": None,
                    "club_id": None,
                    "region_id": None,
                    "age_group": age_by_div.get(row["division_id"]),
                    "age_num": None,
                    "cohort_year": None,
                    "alt_code": None,
                    "gender_code": team_row.get("gender_code"),
                    "tier_label": None,
                    "program_id": None,
                    "program_label": None,
                    "initial_seed": seed,
                    "final_rank": None,
                    "status": "unknown_side",
                    "updated_at": _now(),
                }
                _executemany(conn, UPSERT_TEAM, [stub])
    _executemany(conn, UPSERT_MATCH, match_rows)

    # Roster (team page)
    n_p = n_s = 0
    try:
        roster = client.get_json(f"{BASE}/scheduler-teams/{tid}/roster")
        if isinstance(roster, dict):
            n_p, n_s = upsert_roster(conn, roster, team_row)
            status = "ok" if (n_p or n_s) else "empty"
        else:
            status = "empty"
    except Exception as exc:
        status = "error"
        conn.execute(
            """
            INSERT INTO roster_fetch_log(team_id, status, players, staff, fetched_at, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                status=excluded.status, players=excluded.players, staff=excluded.staff,
                fetched_at=excluded.fetched_at, error=excluded.error
            """,
            (team_row["team_id"], status, 0, 0, _now(), str(exc)[:300]),
        )
    else:
        conn.execute(
            """
            INSERT INTO roster_fetch_log(team_id, status, players, staff, player_count, staff_count, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                status=excluded.status, players=excluded.players, staff=excluded.staff,
                player_count=excluded.player_count, staff_count=excluded.staff_count,
                fetched_at=excluded.fetched_at, error=NULL
            """,
            (team_row["team_id"], status, n_p, n_s, n_p, n_s, _now()),
        )

    conn.commit()
    return {"matches": len(match_rows), "players": n_p, "staff": n_s, "roster_status": status}  # type: ignore[dict-item]


def main() -> None:
    parser = argparse.ArgumentParser(description="Team-page-centric NCVA Power League crawler")
    parser.add_argument("--gender", choices=["girls", "boys", "all"], default="girls")
    parser.add_argument("--event-id", type=int, action="append")
    parser.add_argument("--limit-teams", type=int, default=None)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--resume", action="store_true", help="Skip teams already in roster_fetch_log")
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args()

    init_db(reset=False)
    migrate_sprint2()

    client = RateLimitedClient(interval=args.interval)
    summary: dict[str, Any] = {"events": [], "errors": []}
    try:
        if args.event_id:
            events = []
            for eid in args.event_id:
                detail = client.event_detail(eid)
                events.append(detail)
        else:
            print(f"Discovering events via search ({args.gender})…")
            # Use rate-limited get for discovery
            client.inner.get_json = client.get_json  # type: ignore[method-assign]
            try:
                events = discover_events(client, args.gender)
            finally:
                client.inner.get_json = client._raw_get  # type: ignore[method-assign]
            print(f"Found {len(events)} events: {[e.get('id') for e in events]}")

        conn = get_connection()
        # Ensure roster log columns exist
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roster_fetch_log (
                team_id TEXT PRIMARY KEY,
                status TEXT,
                players INTEGER,
                staff INTEGER,
                player_count INTEGER,
                staff_count INTEGER,
                fetched_at TEXT,
                error TEXT
            )
            """
        )
        log_cols = {r[1] for r in conn.execute("PRAGMA table_info(roster_fetch_log)").fetchall()}
        for col, typ in (("players", "INTEGER"), ("staff", "INTEGER"), ("error", "TEXT")):
            if col not in log_cols:
                conn.execute(f"ALTER TABLE roster_fetch_log ADD COLUMN {col} {typ}")
        conn.commit()

        done_ids: set[str] = set()
        if args.resume:
            done_ids = {
                r[0] for r in conn.execute("SELECT team_id FROM roster_fetch_log").fetchall()
            }
            print(f"Resume: {len(done_ids)} teams already logged")

        try:
            for ev in events:
                eid = str(ev["id"])
                event_row = _event_row(ev)
                divisions = ensure_divisions_for_teams(ev, [])
                conn.execute(UPSERT_REGION, {"region_id": "R-NCVA", "region_name": "Northern California", "state": "CA"})
                conn.execute(UPSERT_EVENT, event_row)

                print(f"[{eid}] {event_row['event_name']}: listing teams…")
                raw_teams = client.scheduler_teams(eid)
                divisions = ensure_divisions_for_teams(ev, raw_teams)
                _executemany(conn, UPSERT_DIVISION, divisions)
                conn.commit()
                age_by_div = {d["division_id"]: d["age_group"] for d in divisions if d.get("age_group")}

                if args.resume:
                    raw_teams = [t for t in raw_teams if f"ST-{t['id']}" not in done_ids]
                if args.limit_teams is not None:
                    raw_teams = raw_teams[: args.limit_teams]

                print(f"[{eid}] crawling {len(raw_teams)} team pages…")
                ev_stats = {"event_id": eid, "teams": 0, "matches": 0, "players": 0, "staff": 0, "ok": 0, "empty": 0}
                for i, t in enumerate(raw_teams, start=1):
                    try:
                        stats = crawl_team(
                            conn,
                            client,
                            event_id=eid,
                            raw_team=t,
                            age_by_div=age_by_div,
                        )
                        ev_stats["teams"] += 1
                        ev_stats["matches"] += int(stats["matches"])
                        ev_stats["players"] += int(stats["players"])
                        ev_stats["staff"] += int(stats["staff"])
                        if stats["roster_status"] == "ok":
                            ev_stats["ok"] += 1
                        elif stats["roster_status"] == "empty":
                            ev_stats["empty"] += 1
                        if i % 20 == 0 or i == len(raw_teams):
                            print(
                                f"  {i}/{len(raw_teams)} ok={ev_stats['ok']} empty={ev_stats['empty']} "
                                f"players={ev_stats['players']} matches={ev_stats['matches']}",
                                flush=True,
                            )
                    except Exception as exc:
                        summary["errors"].append({"event_id": eid, "team_id": t.get("id"), "error": str(exc)})
                        print(f"  ERROR team={t.get('id')}: {exc}", flush=True)
                summary["events"].append(ev_stats)
                print(f"[{eid}] done {ev_stats}")
        finally:
            print("Refreshing derived tables…")
            refresh_derived_tables(conn)
            conn.close()
    finally:
        client.close()

    if not args.no_vacuum:
        vacuum_db()

    conn = get_connection()
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in (
                "events",
                "teams",
                "matches",
                "standings",
                "players",
                "player_season_stints",
                "staff",
                "staff_season_stints",
            )
        }
    finally:
        conn.close()

    out = {"finished_at": _now(), "counts": counts, **summary}
    SUMMARY.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
