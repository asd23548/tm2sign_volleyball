"""
NCVA Power League incremental crawler (Sprint 2).

Usage:
  python scripts/ncva_crawler.py                  # all NCVA Power League events
  python scripts/ncva_crawler.py --event-id 2136  # single event
  python scripts/ncva_crawler.py --reset          # wipe DB then full load
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
from src.etl.team_identity import age_label, parse_alt_code, parse_team_name  # noqa: E402
from src.etl.tm2_client import BASE, TM2Client  # noqa: E402

REQUEST_INTERVAL_SEC = 1.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sid(*vals: Any) -> Optional[str]:
    for v in vals:
        if v is None or v == "":
            continue
        return str(v)
    return None


def _age_from_name(name: str) -> Optional[str]:
    m = re.search(r"(1[1-9]|1[0-9])\s*U", name, re.I)
    if m:
        return f"{m.group(1)}U"
    m = re.search(r"\b(1[1-9])\b", name)
    if m:
        return f"{m.group(1)}U"
    return None


def _gender_from_name(name: str) -> Optional[str]:
    lower = name.lower()
    if "girl" in lower:
        return "Girls"
    if "boy" in lower:
        return "Boys"
    return None


def _season_year(event: dict[str, Any]) -> Optional[int]:
    for key in ("end_date", "start_date"):
        raw = event.get(key)
        if raw:
            try:
                return int(str(raw)[:4])
            except ValueError:
                pass
    name = str(event.get("name") or "")
    years = [int(y) for y in re.findall(r"(20\d{2})", name)]
    return max(years) if years else None


def parse_match_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Derive point totals + deciding/tight-set flags from TM2 set scores."""
    sets: list[dict[str, Any]] = []
    for idx, key in enumerate(["one", "two", "three", "four", "five"], start=1):
        sa = raw.get(f"position_one_score_{key}")
        sb = raw.get(f"position_two_score_{key}")
        if sa is None and sb is None:
            continue
        try:
            a = int(sa) if sa is not None else 0
            b = int(sb) if sb is not None else 0
        except (TypeError, ValueError):
            continue
        sets.append({"set": idx, "a": a, "b": b})

    pts_a = sum(s["a"] for s in sets)
    pts_b = sum(s["b"] for s in sets)
    deciding = 1 if len(sets) >= 3 else 0
    tight = 1 if any(abs(s["a"] - s["b"]) <= 2 for s in sets) else 0
    return {
        "set_scores": json.dumps(sets) if sets else None,
        "team_a_pts_won": pts_a if sets else None,
        "team_b_pts_won": pts_b if sets else None,
        "is_deciding_set_played": deciding,
        "is_tight_set": tight,
    }


class RateLimitedClient:
    """Sequential TM2 client with a fixed inter-request delay."""

    def __init__(self, interval: float = REQUEST_INTERVAL_SEC) -> None:
        self.interval = interval
        self._last = 0.0
        self.inner = TM2Client(timeout=90.0, max_workers=1)
        self._raw_get = self.inner.get_json

    def close(self) -> None:
        self.inner.close()

    def get_json(self, url: str) -> Any:
        elapsed = time.monotonic() - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        data = self._raw_get(url)
        self._last = time.monotonic()
        return data

    def paginate(self, url_for_page) -> list[dict[str, Any]]:
        first = self.get_json(url_for_page(1))
        if isinstance(first, list):
            return first
        rows = list(first.get("data") or [])
        last_page = int(first.get("last_page") or 1)
        for page in range(2, last_page + 1):
            payload = self.get_json(url_for_page(page))
            if isinstance(payload, list):
                rows.extend(payload)
            else:
                rows.extend(list(payload.get("data") or []))
        return rows

    def event_detail(self, event_id: int | str) -> dict[str, Any]:
        return self.get_json(f"{BASE}/events/{event_id}?include[]=eventDivisions")

    def scheduler_teams(self, event_id: int | str) -> list[dict[str, Any]]:
        return self.paginate(
            lambda page: (
                f"{BASE}/scheduler-teams?filter[event_id]={event_id}"
                f"&page={page}&per_page=100"
            )
        )

    def scheduler_matches(self, event_id: int | str) -> list[dict[str, Any]]:
        return self.paginate(
            lambda page: (
                f"{BASE}/scheduler-matches?filter[event_id]={event_id}"
                f"&page={page}&per_page=100"
            )
        )

    def find_power_league_events(self) -> list[dict[str, Any]]:
        # Route TM2Client discovery through the rate-limited getter (no recursion).
        self.inner.get_json = self.get_json  # type: ignore[method-assign]
        try:
            return self.inner.find_power_league_events()
        finally:
            self.inner.get_json = self._raw_get  # type: ignore[method-assign]

UPSERT_REGION = """
INSERT INTO regions (region_id, region_name, state)
VALUES (:region_id, :region_name, :state)
ON CONFLICT(region_id) DO UPDATE SET
    region_name=excluded.region_name,
    state=COALESCE(excluded.state, regions.state)
"""

UPSERT_CLUB = """
INSERT INTO clubs (club_id, club_name, region_id)
VALUES (:club_id, :club_name, :region_id)
ON CONFLICT(club_id) DO UPDATE SET
    club_name=excluded.club_name,
    region_id=COALESCE(excluded.region_id, clubs.region_id)
"""

UPSERT_PROGRAM = """
INSERT INTO programs (program_id, program_label, club_id, gender_code, tier_label)
VALUES (:program_id, :program_label, :club_id, :gender_code, :tier_label)
ON CONFLICT(program_id) DO UPDATE SET
    program_label=excluded.program_label,
    club_id=COALESCE(excluded.club_id, programs.club_id),
    gender_code=COALESCE(excluded.gender_code, programs.gender_code),
    tier_label=COALESCE(excluded.tier_label, programs.tier_label)
"""

UPSERT_EVENT = """
INSERT INTO events (
    event_id, event_name, start_date, end_date, location,
    season_year, gender, region_id, updated_at
) VALUES (
    :event_id, :event_name, :start_date, :end_date, :location,
    :season_year, :gender, :region_id, :updated_at
)
ON CONFLICT(event_id) DO UPDATE SET
    event_name=excluded.event_name,
    start_date=excluded.start_date,
    end_date=excluded.end_date,
    location=excluded.location,
    season_year=excluded.season_year,
    gender=excluded.gender,
    region_id=excluded.region_id,
    updated_at=excluded.updated_at
"""

UPSERT_DIVISION = """
INSERT INTO divisions (division_id, event_id, division_name, age_group, gender)
VALUES (:division_id, :event_id, :division_name, :age_group, :gender)
ON CONFLICT(division_id) DO UPDATE SET
    event_id=excluded.event_id,
    division_name=excluded.division_name,
    age_group=excluded.age_group,
    gender=excluded.gender
"""

UPSERT_TEAM = """
INSERT INTO teams (
    team_id, event_id, division_id, team_name, club_name, club_id, region_id,
    age_group, age_num, cohort_year, alt_code, gender_code, tier_label, program_id, program_label,
    initial_seed, final_rank, status, updated_at
) VALUES (
    :team_id, :event_id, :division_id, :team_name, :club_name, :club_id, :region_id,
    :age_group, :age_num, :cohort_year, :alt_code, :gender_code, :tier_label, :program_id, :program_label,
    :initial_seed, :final_rank, :status, :updated_at
)
ON CONFLICT(team_id) DO UPDATE SET
    event_id=excluded.event_id,
    division_id=excluded.division_id,
    team_name=excluded.team_name,
    club_name=excluded.club_name,
    club_id=excluded.club_id,
    region_id=excluded.region_id,
    age_group=excluded.age_group,
    age_num=excluded.age_num,
    cohort_year=excluded.cohort_year,
    alt_code=excluded.alt_code,
    gender_code=excluded.gender_code,
    tier_label=excluded.tier_label,
    program_id=excluded.program_id,
    program_label=excluded.program_label,
    initial_seed=excluded.initial_seed,
    final_rank=excluded.final_rank,
    status=excluded.status,
    updated_at=excluded.updated_at
"""

UPSERT_MATCH = """
INSERT INTO matches (
    match_id, event_id, division_id, match_date, stage,
    team_a_id, team_b_id, raw_team_a_id, raw_team_b_id,
    team_a_score, team_b_score, set_scores, winner_id,
    seed_a, seed_b, team_a_pts_won, team_b_pts_won,
    is_deciding_set_played, is_tight_set, updated_at
) VALUES (
    :match_id, :event_id, :division_id, :match_date, :stage,
    :team_a_id, :team_b_id, :raw_team_a_id, :raw_team_b_id,
    :team_a_score, :team_b_score, :set_scores, :winner_id,
    :seed_a, :seed_b, :team_a_pts_won, :team_b_pts_won,
    :is_deciding_set_played, :is_tight_set, :updated_at
)
ON CONFLICT(match_id) DO UPDATE SET
    event_id=excluded.event_id,
    division_id=excluded.division_id,
    match_date=excluded.match_date,
    stage=excluded.stage,
    team_a_id=excluded.team_a_id,
    team_b_id=excluded.team_b_id,
    raw_team_a_id=excluded.raw_team_a_id,
    raw_team_b_id=excluded.raw_team_b_id,
    team_a_score=excluded.team_a_score,
    team_b_score=excluded.team_b_score,
    set_scores=excluded.set_scores,
    winner_id=excluded.winner_id,
    seed_a=excluded.seed_a,
    seed_b=excluded.seed_b,
    team_a_pts_won=excluded.team_a_pts_won,
    team_b_pts_won=excluded.team_b_pts_won,
    is_deciding_set_played=excluded.is_deciding_set_played,
    is_tight_set=excluded.is_tight_set,
    updated_at=excluded.updated_at
"""

UPSERT_STANDING = """
INSERT INTO standings (
    event_id, division_id, team_id, initial_seed, final_rank, bracket_finish, updated_at
) VALUES (
    :event_id, :division_id, :team_id, :initial_seed, :final_rank, :bracket_finish, :updated_at
)
ON CONFLICT(event_id, division_id, team_id) DO UPDATE SET
    initial_seed=excluded.initial_seed,
    final_rank=excluded.final_rank,
    bracket_finish=excluded.bracket_finish,
    updated_at=excluded.updated_at
"""


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    venues = event.get("venues") or []
    location = None
    if isinstance(venues, list) and venues:
        v0 = venues[0] if isinstance(venues[0], dict) else {}
        location = v0.get("name") or v0.get("city") or event.get("state")
    else:
        location = event.get("state")
    name = str(event.get("name") or f"Event {event['id']}")
    return {
        "event_id": str(event["id"]),
        "event_name": name,
        "start_date": str(event.get("start_date") or "")[:10] or None,
        "end_date": str(event.get("end_date") or "")[:10] or None,
        "location": location,
        "season_year": _season_year(event),
        "gender": _gender_from_name(name),
        "region_id": "R-NCVA",
        "updated_at": _now(),
    }


def _division_rows(event: dict[str, Any]) -> list[dict[str, Any]]:
    divs = event.get("eventDivisions") or event.get("event_divisions") or []
    rows = []
    for d in divs:
        name = str(d.get("name") or f"Division {d.get('id')}")
        rows.append(
            {
                "division_id": str(d["id"]),
                "event_id": str(d.get("event_id") or event["id"]),
                "division_name": name,
                "age_group": _age_from_name(name),
                "gender": _gender_from_name(name),
            }
        )
    return rows


def _team_row(team: dict[str, Any], event_id: str, age_by_div: dict[str, str]) -> dict[str, Any]:
    club_name = team.get("club_name")
    region = team.get("region") or team.get("state") or "NC"
    slug = re.sub(r"[^A-Za-z0-9]+", "", str(club_name or "CLUB"))[:24] or "CLUB"
    club_id = f"C-{slug}-{region}" if club_name else None
    region_id = f"R-{region}" if region else None
    age = age_by_div.get(str(team.get("event_division_id")))
    parsed = parse_team_name(
        str(team.get("name") or ""),
        club_name=club_name,
        alt_code=team.get("alternate_identifier"),
    )
    alt = parse_alt_code(team.get("alternate_identifier"))
    if not age and parsed.age_num:
        age = age_label(parsed.age_num)
    seed = team.get("starting_seed_number")
    finish = team.get("final_finish_position_number")
    # Provisional status; refresh_derived_tables finalizes from matches
    if finish is not None:
        status = "completed"
    elif seed is not None:
        status = "scheduled"
    else:
        status = "registered"
    return {
        "team_id": f"ST-{team['id']}",
        "event_id": event_id,
        "division_id": str(team["event_division_id"]) if team.get("event_division_id") is not None else None,
        "team_name": str(team.get("name") or f"Team {team['id']}"),
        "club_name": club_name,
        "club_id": club_id,
        "region_id": region_id,
        "age_group": age,
        "age_num": parsed.age_num,
        "cohort_year": None,
        "alt_code": alt.get("alt_code") or team.get("alternate_identifier"),
        "gender_code": parsed.gender_code,
        "tier_label": parsed.tier,
        "program_id": parsed.program_key,
        "program_label": parsed.program_label,
        "initial_seed": seed,
        "final_rank": finish,
        "status": status,
        "updated_at": _now(),
    }


def _match_row(match: dict[str, Any], event_id: str) -> Optional[dict[str, Any]]:
    mid = match.get("id")
    did = match.get("event_division_id")
    if mid is None or did is None:
        return None
    a = match.get("position_one_scheduler_team_id")
    b = match.get("position_two_scheduler_team_id")
    winner = match.get("winning_scheduler_team_id")
    stage_raw = (match.get("pool_bracket_type") or "").lower()
    if "bracket" in stage_raw or "playoff" in stage_raw:
        stage = "Bracket"
    elif "pool" in stage_raw:
        stage = "Pool"
    else:
        stage = stage_raw.title() or None
    match_date = None
    if match.get("start_time"):
        try:
            match_date = datetime.fromtimestamp(int(match["start_time"]), tz=timezone.utc).isoformat()
        except Exception:
            match_date = str(match.get("start_time"))
    metrics = parse_match_metrics(match)
    return {
        "match_id": str(mid),
        "event_id": event_id,
        "division_id": str(did),
        "match_date": match_date,
        "stage": stage,
        "team_a_id": f"ST-{a}" if a else None,
        "team_b_id": f"ST-{b}" if b else None,
        "raw_team_a_id": str(a) if a is not None else None,
        "raw_team_b_id": str(b) if b is not None else None,
        "team_a_score": match.get("position_one_match_set_wins"),
        "team_b_score": match.get("position_two_match_set_wins"),
        "set_scores": metrics["set_scores"],
        "winner_id": f"ST-{winner}" if winner else None,
        "seed_a": match.get("position_one_seed_number"),
        "seed_b": match.get("position_two_seed_number"),
        "team_a_pts_won": metrics["team_a_pts_won"],
        "team_b_pts_won": metrics["team_b_pts_won"],
        "is_deciding_set_played": metrics["is_deciding_set_played"],
        "is_tight_set": metrics["is_tight_set"],
        "updated_at": _now(),
    }


def _standing_row(team: dict[str, Any], event_id: str) -> Optional[dict[str, Any]]:
    tid = team.get("id")
    did = team.get("event_division_id")
    if tid is None or did is None:
        return None
    return {
        "event_id": event_id,
        "division_id": str(did),
        "team_id": f"ST-{tid}",
        "initial_seed": team.get("starting_seed_number"),
        "final_rank": team.get("final_finish_position_number"),
        "bracket_finish": team.get("final_finish_note"),
        "updated_at": _now(),
    }


def _executemany(conn, sql: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn.executemany(sql, rows)
    return len(rows)


def crawl_event(conn, client: RateLimitedClient, event_id: int | str) -> dict[str, int]:
    detail = client.event_detail(event_id)
    if not isinstance(detail, dict) or not detail.get("id"):
        raise RuntimeError(f"Event {event_id} not found")

    eid = str(detail["id"])
    event = _event_row(detail)
    divisions = _division_rows(detail)
    age_by_div = {d["division_id"]: d["age_group"] for d in divisions if d.get("age_group")}

    print(f"  [{eid}] {event['event_name']}: fetching teams…")
    raw_teams = client.scheduler_teams(eid)
    print(f"  [{eid}] teams={len(raw_teams)}; fetching matches…")
    raw_matches = client.scheduler_matches(eid)
    print(f"  [{eid}] matches={len(raw_matches)}; upserting…")

    team_rows = [_team_row(t, eid, age_by_div) for t in raw_teams]
    known_divs = {d["division_id"] for d in divisions}
    for tr in team_rows:
        did = tr.get("division_id")
        if did and did not in known_divs:
            divisions.append(
                {
                    "division_id": did,
                    "event_id": eid,
                    "division_name": f"Division {did}",
                    "age_group": age_by_div.get(did),
                    "gender": event.get("gender"),
                }
            )
            known_divs.add(did)

    known = {t["team_id"] for t in team_rows}
    match_rows: list[dict[str, Any]] = []
    for raw in raw_matches:
        row = _match_row(raw, eid)
        if not row:
            continue
        match_rows.append(row)
        for tid, seed in ((row.get("team_a_id"), row.get("seed_a")), (row.get("team_b_id"), row.get("seed_b"))):
            if tid and tid not in known:
                team_rows.append(
                    {
                        "team_id": tid,
                        "event_id": eid,
                        "division_id": row["division_id"],
                        "team_name": tid,
                        "club_name": None,
                        "club_id": None,
                        "region_id": None,
                        "age_group": age_by_div.get(row["division_id"]),
                        "age_num": None,
                        "cohort_year": None,
                        "alt_code": None,
                        "gender_code": None,
                        "tier_label": None,
                        "program_id": None,
                        "program_label": None,
                        "initial_seed": seed,
                        "final_rank": None,
                        "status": "unknown_side",
                        "updated_at": _now(),
                    }
                )
                known.add(tid)
        if row["division_id"] not in known_divs:
            divisions.append(
                {
                    "division_id": row["division_id"],
                    "event_id": eid,
                    "division_name": f"Division {row['division_id']}",
                    "age_group": None,
                    "gender": event.get("gender"),
                }
            )
            known_divs.add(row["division_id"])

    standing_rows = [s for t in raw_teams if (s := _standing_row(t, eid))]

    # Identity parents before teams (FK order)
    region_rows = [{"region_id": "R-NCVA", "region_name": "Northern California", "state": "CA"}]
    club_rows = []
    program_rows = []
    seen_clubs: set[str] = set()
    seen_programs: set[str] = set()
    for tr in team_rows:
        rid = tr.get("region_id")
        if rid:
            region_rows.append(
                {"region_id": rid, "region_name": rid.replace("R-", "", 1), "state": "CA" if rid in ("R-NC", "R-NCVA", "R-CA") else None}
            )
        if tr.get("club_id") and tr["club_id"] not in seen_clubs and tr.get("club_name"):
            club_rows.append(
                {"club_id": tr["club_id"], "club_name": tr["club_name"], "region_id": tr.get("region_id")}
            )
            seen_clubs.add(tr["club_id"])
        if tr.get("program_id") and tr["program_id"] not in seen_programs and tr.get("program_label"):
            program_rows.append(
                {
                    "program_id": tr["program_id"],
                    "program_label": tr["program_label"],
                    "club_id": tr.get("club_id"),
                    "gender_code": tr.get("gender_code"),
                    "tier_label": tr.get("tier_label"),
                }
            )
            seen_programs.add(tr["program_id"])

    _executemany(conn, UPSERT_REGION, region_rows)
    conn.execute(UPSERT_EVENT, event)
    _executemany(conn, UPSERT_DIVISION, divisions)
    _executemany(conn, UPSERT_CLUB, club_rows)
    _executemany(conn, UPSERT_PROGRAM, program_rows)
    _executemany(conn, UPSERT_TEAM, team_rows)
    _executemany(conn, UPSERT_MATCH, match_rows)
    _executemany(conn, UPSERT_STANDING, standing_rows)
    conn.commit()

    return {
        "event_id": eid,
        "divisions": len(divisions),
        "teams": len(team_rows),
        "matches": len(match_rows),
        "standings": len(standing_rows),
        "deciding_sets": sum(1 for m in match_rows if m["is_deciding_set_played"]),
        "tight_sets": sum(1 for m in match_rows if m["is_tight_set"]),
    }


def backup_db(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = path.with_name(f"volleyball_backup_{stamp}.db")
    shutil.copy2(path, dest)
    return dest


def restore_roster_from_backup(live: Path, backup: Path) -> dict[str, int]:
    """Copy roster tables from pre-Sprint1 backup for teams that still exist."""
    if not backup.exists() or not live.exists():
        return {}
    conn = get_connection(live)
    try:
        conn.execute("ATTACH DATABASE ? AS bak", (str(backup),))
        restored: dict[str, int] = {}
        # Only copy if backup has the table
        bak_tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM bak.sqlite_master WHERE type='table'"
            ).fetchall()
        }
        copies = [
            (
                "players",
                """
                INSERT OR IGNORE INTO players
                SELECT * FROM bak.players
                WHERE player_id IN (
                  SELECT DISTINCT player_id FROM bak.player_season_stints
                  WHERE team_id IN (SELECT team_id FROM main.teams)
                )
                """,
            ),
            (
                "player_season_stints",
                """
                INSERT OR IGNORE INTO player_season_stints
                SELECT * FROM bak.player_season_stints
                WHERE team_id IN (SELECT team_id FROM main.teams)
                  AND event_id IN (SELECT event_id FROM main.events)
                """,
            ),
            (
                "staff",
                """
                INSERT OR IGNORE INTO staff
                SELECT * FROM bak.staff
                WHERE staff_id IN (
                  SELECT DISTINCT staff_id FROM bak.staff_season_stints
                  WHERE team_id IN (SELECT team_id FROM main.teams)
                )
                """,
            ),
            (
                "staff_season_stints",
                """
                INSERT OR IGNORE INTO staff_season_stints
                SELECT * FROM bak.staff_season_stints
                WHERE team_id IN (SELECT team_id FROM main.teams)
                  AND event_id IN (SELECT event_id FROM main.events)
                """,
            ),
            (
                "roster_fetch_log",
                """
                INSERT OR IGNORE INTO roster_fetch_log
                SELECT * FROM bak.roster_fetch_log
                WHERE team_id IN (SELECT team_id FROM main.teams)
                """,
            ),
        ]
        for name, sql in copies:
            if name not in bak_tables:
                continue
            try:
                cur = conn.execute(sql)
                restored[name] = cur.rowcount
            except Exception as exc:
                restored[name] = -1
                print(f"  roster restore skip {name}: {exc}")
        conn.commit()
        conn.execute("DETACH DATABASE bak")
        return restored
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="NCVA Power League incremental crawler")
    parser.add_argument("--event-id", type=int, action="append", help="Crawl one or more event IDs")
    parser.add_argument("--reset", action="store_true", help="Wipe DB and recreate Sprint 1 schema")
    parser.add_argument("--no-vacuum", action="store_true")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL_SEC, help="Seconds between HTTP requests")
    args = parser.parse_args()

    bak: Optional[Path] = None
    if args.reset:
        bak = backup_db(DB_PATH)
        if bak:
            print(f"Backed up existing DB → {bak}")
        init_db(reset=True)
        print(f"Initialized empty Sprint 2 schema at {DB_PATH}")
    else:
        init_db(reset=False)
        print("Ensuring Sprint 2 schema / migrating if needed…")
        migrate_sprint2()

    client = RateLimitedClient(interval=args.interval)
    summary = {"events": [], "errors": []}
    try:
        if args.event_id:
            event_ids = args.event_id
        else:
            cache = PROJECT_ROOT / "data" / "ncva_power_league_events.json"
            if cache.exists():
                cached = json.loads(cache.read_text(encoding="utf-8"))
                event_ids = [int(e["id"]) for e in cached]
                print(f"Using cached Power League event list ({len(event_ids)}): {event_ids}")
            else:
                print("Discovering NCVA Power League events…")
                found = client.find_power_league_events()
                event_ids = [int(e["id"]) for e in found]
                print(f"Found {len(event_ids)} events: {event_ids}")

        conn = get_connection()
        try:
            for eid in event_ids:
                try:
                    stats = crawl_event(conn, client, eid)
                    summary["events"].append(stats)
                    print(
                        f"  OK event={eid} div={stats['divisions']} teams={stats['teams']} "
                        f"matches={stats['matches']} standings={stats['standings']} "
                        f"deciding={stats['deciding_sets']} tight={stats['tight_sets']}"
                    )
                except Exception as exc:
                    summary["errors"].append({"event_id": eid, "error": str(exc)})
                    print(f"  ERROR event={eid}: {exc}")
        finally:
            conn.close()
    finally:
        client.close()

    print("Refreshing derived tables (match_sets / clubs / programs / team_season_stats)…")
    conn_derived = get_connection()
    try:
        derived = refresh_derived_tables(conn_derived)
    finally:
        conn_derived.close()
    print(f"  derived: {derived}")

    roster_restored = {}
    if bak:
        print(f"Restoring roster tables from {bak.name}…")
        roster_restored = restore_roster_from_backup(DB_PATH, bak)
        print(f"  restored: {roster_restored}")

    if not args.no_vacuum:
        vacuum_db()
        print("VACUUM complete.")

    # Final completeness report
    conn = get_connection()
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in (
                "events",
                "divisions",
                "teams",
                "matches",
                "match_sets",
                "standings",
                "team_season_stats",
                "clubs",
                "programs",
            )
        }
        scored = conn.execute(
            """
            SELECT
              COUNT(*) AS matches,
              SUM(CASE WHEN team_a_pts_won IS NOT NULL THEN 1 ELSE 0 END) AS with_points,
              SUM(is_deciding_set_played) AS deciding,
              SUM(is_tight_set) AS tight
            FROM matches
            """
        ).fetchone()
        status_counts = {
            r[0]: r[1]
            for r in conn.execute("SELECT status, COUNT(*) FROM teams GROUP BY status")
        }
        roster_counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("players", "player_season_stints", "staff", "staff_season_stints")
        }
    finally:
        conn.close()

    out = {
        "finished_at": _now(),
        "counts": counts,
        "team_status": status_counts,
        "match_metrics": {
            "matches": scored[0],
            "with_points": scored[1],
            "deciding_sets": scored[2],
            "tight_sets": scored[3],
        },
        "derived": derived,
        "roster_counts": roster_counts,
        "roster_restored": roster_restored,
        "events": summary["events"],
        "errors": summary["errors"],
    }
    out_path = PROJECT_ROOT / "data" / "ncva_crawler_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
