"""Load full historical NCVA Power League into a dedicated SQLite database."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import DB_PATH, get_connection, init_database  # noqa: E402
from src.etl.pipeline import upsert_many  # noqa: E402
from src.etl.tm2_client import TM2Client  # noqa: E402
from src.etl.tm2_transform import event_row  # noqa: E402

CATALOG_PATH = PROJECT_ROOT / "data" / "ncva_power_league_events.json"
SUMMARY_PATH = PROJECT_ROOT / "data" / "ncva_power_league_load_summary.json"


def reset_database(db_path: Path | None = None) -> Path:
    path = Path(db_path) if db_path else DB_PATH
    if path.exists():
        path.unlink()
    return init_database(path)


def enrich_event_from_catalog(event: dict[str, Any], catalog_row: dict[str, Any] | None) -> dict[str, Any]:
    """Ensure eventDivisions exist using catalog snapshot when detail endpoint omits them."""
    if event.get("eventDivisions") or event.get("event_divisions"):
        return event
    if not catalog_row:
        return event
    divs = catalog_row.get("divisions") or []
    if not divs:
        return event
    event = dict(event)
    event["eventDivisions"] = [
        {
            "id": d["id"],
            "event_id": event.get("id"),
            "name": d.get("name"),
            "abbreviation": d.get("name"),
        }
        for d in divs
        if d.get("id") is not None
    ]
    return event


def load_power_league(
    reset: bool = True,
    workers: int = 8,
    include_future_without_matches: bool = True,
) -> dict[str, Any]:
    if reset:
        reset_database()
    else:
        init_database()

    summary: dict[str, Any] = {
        "league": "NCVA Power League",
        "started_at": datetime.utcnow().isoformat() + "Z",
        "events": [],
        "totals": {
            "regions": 0,
            "clubs": 0,
            "teams": 0,
            "events": 0,
            "divisions": 0,
            "matches": 0,
            "rankings": 0,
        },
        "errors": [],
    }

    conn = get_connection()
    try:
        with TM2Client(max_workers=workers) as client:
            print("[discover] Finding NCVA Power League events…")
            events = client.find_power_league_events()
            CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            catalog = [
                {
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "state": e.get("state"),
                    "start_date": e.get("start_date"),
                    "end_date": e.get("end_date"),
                    "divisions": [
                        {"id": d.get("id"), "name": d.get("name")}
                        for d in (e.get("eventDivisions") or e.get("event_divisions") or [])
                    ],
                }
                for e in events
            ]
            CATALOG_PATH.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
            print(f"[discover] {len(events)} seasons found")

            catalog_by_id = {c["id"]: c for c in catalog}

            for i, event in enumerate(events, start=1):
                eid = event["id"]
                name = event.get("name")
                print(f"\n[{i}/{len(events)}] {eid} {name}")
                event = enrich_event_from_catalog(event, catalog_by_id.get(eid))

                def match_progress(done: int, last: int, total: int, _eid=eid) -> None:
                    if done == 1 or done == last or done % 10 == 0:
                        print(f"  matches pages {done}/{last} (total≈{total})")

                def team_progress(done: int, last: int, total: int, _eid=eid) -> None:
                    if done == 1 or done == last or done % 10 == 0:
                        print(f"  teams pages {done}/{last} (total≈{total})")

                try:
                    print("  fetching matches…")
                    raw_matches = client.scheduler_matches(eid, progress=match_progress)
                    print(f"  matches fetched: {len(raw_matches)}")
                    if not raw_matches and not include_future_without_matches:
                        print("  skip (no matches)")
                        continue

                    print("  fetching scheduler teams…")
                    # Monkey-patch via direct calls inside load — we'll pass through custom loader path
                    counts = _load_power_event(
                        conn,
                        client,
                        event,
                        raw_matches=raw_matches,
                        team_progress=team_progress,
                    )
                    conn.commit()
                    row = {"event_id": eid, "name": name, **counts}
                    summary["events"].append(row)
                    for k in summary["totals"]:
                        summary["totals"][k] += counts.get(k, 0)
                    print(
                        f"  stored matches={counts.get('raw_matches')} teams={counts.get('raw_teams')} "
                        f"rankings={counts.get('rankings')}"
                    )
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    err = {"event_id": eid, "name": name, "error": str(exc)}
                    summary["errors"].append(err)
                    print(f"  ERROR: {exc}")
    finally:
        conn.close()

    # Final exact counts from DB
    conn = get_connection()
    try:
        exact = {}
        for table in ("regions", "clubs", "teams", "events", "divisions", "matches", "rankings"):
            exact[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        summary["db_counts"] = exact
    finally:
        conn.close()

    summary["finished_at"] = datetime.utcnow().isoformat() + "Z"
    summary["db"] = str(DB_PATH)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _load_power_event(
    conn,
    client: TM2Client,
    event: dict[str, Any],
    raw_matches: list[dict[str, Any]],
    team_progress=None,
) -> dict[str, int]:
    """Like load_event_bundle but uses fully paginated team fetch."""
    from src.etl.tm2_transform import (
        club_from_registration,
        club_from_scheduler_team,
        division_rows,
        match_row,
        ranking_row,
        region_from_event,
        region_from_team,
        team_from_scheduler_team,
    )

    eid = event["id"]
    regions = []
    clubs = []
    teams = []
    events = [event_row(event)]
    # Tag league on event name already; also store region NCVA if possible
    if not events[0].get("region_id"):
        events[0]["region_id"] = "R-NCVA"
        regions.append({"region_id": "R-NCVA", "region_name": "Northern California", "state": "CA"})

    divisions = division_rows(event)
    matches = []
    rankings = []

    re = region_from_event(event)
    if re:
        regions.append(re)

    age_by_div = {d["division_id"]: d.get("age_group") for d in divisions if d.get("age_group")}

    sched_teams = client.scheduler_teams(eid, progress=team_progress)
    try:
        regs = client.team_registrations(eid)
    except Exception:
        regs = []

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

    # Registrations enrich club metadata only. Do not insert CT-* registration
    # teams — they clutter dropdowns and are not on the schedule/rankings.
    sched_club_ids = {c["club_id"] for c in clubs if c.get("club_id")}
    for reg in regs:
        c = club_from_registration(reg)
        if not c:
            continue
        rid = c.get("region_id") or ""
        # Keep NCVA-area clubs (R-NC / unset); skip clear out-of-region noise (e.g. R-SC)
        if rid and rid not in ("R-NC", "R-NCVA") and not rid.endswith("-NC"):
            continue
        if c.get("club_id") in sched_club_ids:
            continue
        clubs.append(c)
        sched_club_ids.add(c["club_id"])
        if rid:
            regions.append(
                {
                    "region_id": rid,
                    "region_name": rid.replace("R-", "", 1),
                    "state": None,
                }
            )

    seen_divs = {d["division_id"] for d in divisions}
    team_ids = {t["team_id"] for t in teams}
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
            for tid in (row.get("team_a_id"), row.get("team_b_id"), row.get("winner_id")):
                if tid and tid not in team_ids:
                    teams.append(
                        {
                            "team_id": tid,
                            "club_id": None,
                            "team_name": tid,
                            "age_group": age_by_div.get(row["division_id"]),
                            "cohort_year": None,
                        }
                    )
                    team_ids.add(tid)
            matches.append(row)

    # Ensure parent region for NCVA clubs
    if not any(r["region_id"] == "R-NCVA" for r in regions):
        regions.append({"region_id": "R-NCVA", "region_name": "Northern California", "state": "CA"})

    counts = {
        "regions": upsert_many(conn, "regions", regions, ["region_id"]),
        "clubs": upsert_many(conn, "clubs", clubs, ["club_id"]),
        "teams": upsert_many(conn, "teams", teams, ["team_id"]),
        "events": upsert_many(conn, "events", events, ["event_id"]),
        "divisions": upsert_many(conn, "divisions", divisions, ["division_id"]),
        "matches": upsert_many(conn, "matches", matches, ["match_id"]),
        "rankings": upsert_many(
            conn,
            "rankings",
            rankings,
            ["event_id", "division_id", "team_id"],
        ),
        "raw_matches": len(raw_matches),
        "raw_teams": len(sched_teams),
        "raw_registrations": len(regs),
    }
    return counts


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load full NCVA Power League history")
    parser.add_argument("--no-reset", action="store_true", help="Do not wipe existing DB")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--event-id", type=int, action="append", help="Load only these event IDs")
    args = parser.parse_args()

    if args.event_id:
        init_database()
        conn = get_connection()
        summary = {"events": [], "errors": []}
        try:
            with TM2Client(max_workers=args.workers) as client:
                catalog = []
                if CATALOG_PATH.exists():
                    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
                catalog_by_id = {c["id"]: c for c in catalog}
                all_events = {e["id"]: e for e in client.find_power_league_events()}
                for eid in args.event_id:
                    event = all_events.get(eid) or client.event_detail(eid)
                    event = enrich_event_from_catalog(event, catalog_by_id.get(eid))
                    print(f"Retrying event {eid} {event.get('name')}")

                    def prog(done, last, total):
                        if done == 1 or done == last or done % 5 == 0:
                            print(f"  pages {done}/{last} total≈{total}")

                    try:
                        raw_matches = client.scheduler_matches(eid, progress=prog)
                        print(f"  matches fetched: {len(raw_matches)}")
                        counts = _load_power_event(
                            conn, client, event, raw_matches=raw_matches, team_progress=prog
                        )
                        conn.commit()
                        summary["events"].append({"event_id": eid, **counts})
                        print("  ok", counts.get("raw_matches"), counts.get("raw_teams"))
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        summary["errors"].append({"event_id": eid, "error": str(exc)})
                        print("  ERROR", exc)
        finally:
            conn.close()
        conn = get_connection()
        try:
            exact = {
                t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
                for t in ("regions", "clubs", "teams", "events", "divisions", "matches", "rankings")
            }
        finally:
            conn.close()
        print(json.dumps({"db_counts": exact, **summary}, indent=2))
    else:
        out = load_power_league(reset=not args.no_reset, workers=args.workers)
        print(json.dumps({k: out[k] for k in ("league", "db_counts", "errors", "db")}, indent=2))
        print(f"Events loaded: {len(out['events'])}")
