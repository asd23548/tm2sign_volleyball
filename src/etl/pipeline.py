"""ETL orchestration: discover schema → live TM2Sign load → demo fallback."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import get_connection, init_database  # noqa: E402
from src.etl.tm2_client import TM2Client, write_known_schema  # noqa: E402
from src.etl.tm2_transform import (  # noqa: E402
    club_from_registration,
    club_from_scheduler_team,
    division_rows,
    event_row,
    match_row,
    ranking_row,
    region_from_event,
    region_from_team,
    team_from_registration,
    team_from_scheduler_team,
)

SCHEMA_PATH = PROJECT_ROOT / "data" / "api_schema_discovered.json"


def upsert_many(conn, table: str, rows: list[dict[str, Any]], pk_cols: list[str]) -> int:
    rows = [r for r in rows if r]
    if not rows:
        return 0
    # union of keys for sparse rows
    cols: list[str] = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    placeholders = ", ".join(["?"] * len(cols))
    col_sql = ", ".join(cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk_cols)
    if updates:
        sql = (
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(pk_cols)}) DO UPDATE SET {updates}"
        )
    else:
        sql = f"INSERT OR IGNORE INTO {table} ({col_sql}) VALUES ({placeholders})"
    cur = conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return cur.rowcount


def load_event_bundle(
    conn,
    client: TM2Client,
    event: dict[str, Any],
    preloaded_matches: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    eid = event["id"]
    # Ensure divisions present
    if not (event.get("eventDivisions") or event.get("event_divisions")):
        try:
            event = client.event_detail(eid)
        except Exception:
            pass

    regions = []
    clubs = []
    teams = []
    events = [event_row(event)]
    divisions = division_rows(event)
    matches = []
    rankings = []

    re = region_from_event(event)
    if re:
        regions.append(re)

    age_by_div = {d["division_id"]: d.get("age_group") for d in divisions if d.get("age_group")}

    try:
        sched_teams = client.scheduler_teams(eid)
    except Exception:
        sched_teams = []
    try:
        regs = client.team_registrations(eid)
    except Exception:
        regs = []
    if preloaded_matches is not None:
        raw_matches = preloaded_matches
    else:
        try:
            raw_matches = client.scheduler_matches(eid)
        except Exception:
            raw_matches = []

    for t in sched_teams:
        r = region_from_team(t)
        if r:
            regions.append(r)
        c = club_from_scheduler_team(t)
        if c:
            clubs.append(c)
        teams.append(team_from_scheduler_team(t, age_by_div))
        rk = ranking_row(t, eid)
        if rk:
            rankings.append(rk)

    for reg in regs:
        c = club_from_registration(reg)
        if c:
            clubs.append(c)
            if c.get("region_id"):
                regions.append(
                    {
                        "region_id": c["region_id"],
                        "region_name": c["region_id"].replace("R-", "", 1),
                        "state": None,
                    }
                )
        tm = team_from_registration(reg)
        if tm:
            teams.append(tm)

    # Ensure division rows exist even if event payload omitted them
    seen_divs = {d["division_id"] for d in divisions}
    for m in raw_matches:
        did = m.get("event_division_id")
        if did is not None and str(did) not in seen_divs:
            divisions.append(
                {
                    "division_id": str(did),
                    "event_id": str(eid),
                    "division_name": f"Division {did}",
                    "age_group": None,
                    "gender": None,
                }
            )
            seen_divs.add(str(did))
        row = match_row(m)
        if row:
            # Ensure both teams exist as stubs
            for tid in (row.get("team_a_id"), row.get("team_b_id"), row.get("winner_id")):
                if tid and not any(t["team_id"] == tid for t in teams):
                    teams.append(
                        {
                            "team_id": tid,
                            "club_id": None,
                            "team_name": tid,
                            "age_group": age_by_div.get(row["division_id"]),
                            "cohort_year": None,
                        }
                    )
            matches.append(row)

    counts = {
        "regions": upsert_many(conn, "regions", regions, ["region_id"]),
        "clubs": upsert_many(conn, "clubs", clubs, ["club_id"]),
        "teams": upsert_many(conn, "teams", teams, ["team_id"]),
        "events": upsert_many(conn, "events", events, ["event_id"]),
        "divisions": upsert_many(conn, "divisions", divisions, ["division_id"]),
        "matches": upsert_many(conn, "matches", matches, ["match_id"]),
        "rankings": upsert_many(conn, "rankings", rankings, ["event_id", "division_id", "team_id"]),
    }
    return {k: max(v, 0) for k, v in counts.items()} | {
        "raw_matches": len(raw_matches),
        "raw_teams": len(sched_teams),
    }


def fetch_and_load_live(
    max_pages: int = 2,
    per_page: int = 50,
    max_events: int = 40,
    date_range: str = "past",
    min_matches: int = 1,
) -> dict[str, Any]:
    write_known_schema(SCHEMA_PATH)
    init_database()
    totals = {k: 0 for k in ("regions", "clubs", "teams", "events", "divisions", "matches", "rankings")}
    processed = []
    skipped = []
    errors = []

    conn = get_connection()
    try:
        with TM2Client() as client:
            events = client.iter_events(date_range=date_range, max_pages=max_pages, per_page=per_page)
            loaded_with_matches = 0
            for event in events:
                if loaded_with_matches >= max_events:
                    break
                eid = event.get("id")
                try:
                    # Cheap prefilter: skip events with no published schedule
                    raw_matches = client.scheduler_matches(eid)
                    if len(raw_matches) < min_matches:
                        skipped.append(eid)
                        continue
                    counts = load_event_bundle(conn, client, event, preloaded_matches=raw_matches)
                    conn.commit()
                    processed.append({"event_id": eid, "name": event.get("name"), **counts})
                    for k in totals:
                        totals[k] += counts.get(k, 0)
                    if counts.get("raw_matches", 0) >= min_matches:
                        loaded_with_matches += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append({"event_id": eid, "error": str(exc)})
                    conn.rollback()
    finally:
        conn.close()

    return {
        "mode": "live_tm2sign",
        "events_seen": len(processed),
        "totals": totals,
        "errors": errors[:20],
        "sample_processed": processed[:10],
        "db": str(PROJECT_ROOT / "database" / "volleyball.db"),
        "ran_at": datetime.utcnow().isoformat() + "Z",
    }


def seed_demo_if_empty() -> bool:
    conn = get_connection()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
        if n > 0:
            return False
    finally:
        conn.close()
    from src.etl.demo_seed import seed_demo_data

    seed_demo_data()
    return True


def run_etl(
    use_demo_fallback: bool = True,
    max_pages: int = 2,
    max_events: int = 40,
) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_path": str(SCHEMA_PATH)}
    try:
        result["live"] = fetch_and_load_live(max_pages=max_pages, max_events=max_events)
    except Exception as exc:  # noqa: BLE001
        result["live_error"] = str(exc)

    live_totals = (result.get("live") or {}).get("totals") or {}
    got_data = (live_totals.get("matches") or 0) > 0
    if use_demo_fallback and not got_data:
        result["demo_seeded"] = seed_demo_if_empty()
    else:
        result["demo_seeded"] = False
    return result


if __name__ == "__main__":
    print(json.dumps(run_etl(), indent=2))
