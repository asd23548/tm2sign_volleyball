"""Backfill team identity fields + programs table without reloading all matches."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import get_connection, init_database
from src.etl.team_identity import age_label, parse_alt_code, parse_team_name
from src.etl.tm2_client import TM2Client

TEAM_COLS = [
    ("club_team_id", "TEXT"),
    ("alt_code", "TEXT"),
    ("age_num", "INTEGER"),
    ("tier_label", "TEXT"),
    ("gender_code", "TEXT"),
    ("program_id", "TEXT"),
    ("program_label", "TEXT"),
    ("age_team_key", "TEXT"),
]


def ensure_schema(conn) -> None:
    # Avoid full schema replay if legacy tables exist without new columns —
    # only create missing tables, then ALTER teams.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS programs (
            program_id     TEXT PRIMARY KEY,
            program_label  TEXT NOT NULL,
            club_id        TEXT,
            gender_code    TEXT,
            tier_label     TEXT
        );
        CREATE TABLE IF NOT EXISTS players (
            player_id    TEXT PRIMARY KEY,
            full_name    TEXT NOT NULL,
            gender       TEXT,
            grad_year    INTEGER
        );
        CREATE TABLE IF NOT EXISTS player_season_stints (
            player_id    TEXT NOT NULL,
            event_id     TEXT NOT NULL,
            team_id      TEXT,
            program_id   TEXT,
            age_group    TEXT,
            season_year  INTEGER,
            PRIMARY KEY (player_id, event_id, team_id)
        );
        """
    )
    existing = {r[1] for r in conn.execute("PRAGMA table_info(teams)").fetchall()}
    for col, typ in TEAM_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE teams ADD COLUMN {col} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_program ON teams(program_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_alt ON teams(alt_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_programs_label ON programs(program_label)")
    conn.commit()


def fetch_scheduler_team_meta(client: TM2Client, event_ids: list[str]) -> dict[str, dict]:
    """Map ST-{id} -> scheduler team payload fields."""
    out: dict[str, dict] = {}
    for eid in event_ids:
        print(f"  teams meta event {eid}")
        rows = client.scheduler_teams(eid)
        for t in rows:
            out[f"ST-{t['id']}"] = t
        print(f"    got {len(rows)}")
    return out


def backfill(refresh_from_api: bool = True) -> dict:
    conn = get_connection()
    ensure_schema(conn)
    try:
        event_ids = [
            str(r["event_id"])
            for r in conn.execute("SELECT event_id FROM events ORDER BY start_date").fetchall()
        ]
        meta: dict[str, dict] = {}
        if refresh_from_api:
            print("Fetching scheduler-team identity fields from TM2…")
            with TM2Client(max_workers=8) as client:
                meta = fetch_scheduler_team_meta(client, event_ids)

        # Division gender map
        div_gender = {
            str(r["division_id"]): r["gender"]
            for r in conn.execute("SELECT division_id, gender FROM divisions").fetchall()
        }
        # Infer gender from event name when division gender missing
        event_gender = {}
        for r in conn.execute("SELECT event_id, event_name FROM events").fetchall():
            name = (r["event_name"] or "").lower()
            if "girl" in name:
                event_gender[str(r["event_id"])] = "G"
            elif "boy" in name:
                event_gender[str(r["event_id"])] = "B"

        team_event_gender = {}
        for r in conn.execute(
            """
            SELECT DISTINCT m.team_a_id AS team_id, d.event_id, d.division_id
            FROM matches m JOIN divisions d ON d.division_id = m.division_id
            UNION
            SELECT DISTINCT m.team_b_id, d.event_id, d.division_id
            FROM matches m JOIN divisions d ON d.division_id = m.division_id
            UNION
            SELECT r.team_id, r.event_id, r.division_id FROM rankings r
            """
        ).fetchall():
            tid = r["team_id"]
            if not tid:
                continue
            g = div_gender.get(str(r["division_id"])) or event_gender.get(str(r["event_id"]))
            if g:
                team_event_gender[tid] = g

        teams = conn.execute(
            """
            SELECT team_id, team_name, club_id, age_group,
                   club_team_id, alt_code, program_id, program_label,
                   age_num, tier_label, gender_code, age_team_key
            FROM teams
            """
        ).fetchall()
        programs: dict[str, dict] = {}
        updated = 0
        for t in teams:
            tid = t["team_id"]
            payload = meta.get(tid) or {}
            alt = payload.get("alternate_identifier") or t["alt_code"]
            club_team_id = payload.get("club_team_id")
            if club_team_id is None:
                club_team_id = t["club_team_id"]
            club_name = payload.get("club_name")
            gender_hint = team_event_gender.get(tid) or t["gender_code"]
            parsed = parse_team_name(
                t["team_name"],
                club_name=club_name,
                alt_code=alt,
                gender_hint=gender_hint,
            )
            alt_info = parse_alt_code(alt)
            age_num = parsed.age_num if parsed.age_num is not None else t["age_num"]
            # Prefer parsed age from team name / alt code over noisy division labels
            if age_num is not None:
                age_group = age_label(int(age_num))
            else:
                age_group = t["age_group"]

            # Prefer alt-derived program key; keep prior DB program_id if parse is weaker
            program_id = parsed.program_key or t["program_id"]
            program_label = parsed.program_label or t["program_label"]
            if program_id and program_label:
                programs[program_id] = {
                    "program_id": program_id,
                    "program_label": program_label,
                    "club_id": t["club_id"],
                    "gender_code": parsed.gender_code or t["gender_code"],
                    "tier_label": parsed.tier or t["tier_label"],
                }

            conn.execute(
                """
                UPDATE teams SET
                    club_team_id = ?,
                    alt_code = ?,
                    age_num = ?,
                    tier_label = ?,
                    gender_code = ?,
                    program_id = ?,
                    program_label = ?,
                    age_team_key = ?,
                    age_group = COALESCE(?, age_group)
                WHERE team_id = ?
                """,
                (
                    str(club_team_id) if club_team_id is not None else None,
                    alt_info.get("alt_code") or alt,
                    age_num,
                    parsed.tier or t["tier_label"],
                    parsed.gender_code or t["gender_code"],
                    program_id,
                    program_label,
                    alt_info.get("age_team_key") or t["age_team_key"],
                    age_group,
                    tid,
                ),
            )
            updated += 1

        for p in programs.values():
            conn.execute(
                """
                INSERT INTO programs (program_id, program_label, club_id, gender_code, tier_label)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(program_id) DO UPDATE SET
                    program_label=excluded.program_label,
                    club_id=COALESCE(excluded.club_id, programs.club_id),
                    gender_code=COALESCE(excluded.gender_code, programs.gender_code),
                    tier_label=COALESCE(excluded.tier_label, programs.tier_label)
                """,
                (
                    p["program_id"],
                    p["program_label"],
                    p["club_id"],
                    p["gender_code"],
                    p["tier_label"],
                ),
            )
        conn.commit()

        # Deduplicate CT-* registration stubs that never appear in matches/rankings
        orphan_ct = conn.execute(
            """
            DELETE FROM teams
            WHERE team_id LIKE 'CT-%'
              AND team_id NOT IN (SELECT team_a_id FROM matches WHERE team_a_id IS NOT NULL)
              AND team_id NOT IN (SELECT team_b_id FROM matches WHERE team_b_id IS NOT NULL)
              AND team_id NOT IN (SELECT winner_id FROM matches WHERE winner_id IS NOT NULL)
              AND team_id NOT IN (SELECT team_id FROM rankings)
            """
        ).rowcount

        # Stats
        stats = {
            "teams_updated": updated,
            "programs": conn.execute("SELECT COUNT(*) c FROM programs").fetchone()["c"],
            "teams_remaining": conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"],
            "orphan_ct_removed": orphan_ct,
            "with_program": conn.execute(
                "SELECT COUNT(*) c FROM teams WHERE program_id IS NOT NULL"
            ).fetchone()["c"],
            "distinct_program_labels": conn.execute(
                "SELECT COUNT(DISTINCT program_label) c FROM teams WHERE program_label IS NOT NULL"
            ).fetchone()["c"],
        }
        # Absolute Black sanity
        abs_rows = conn.execute(
            """
            SELECT team_id, team_name, age_group, program_id, program_label, alt_code, age_num
            FROM teams
            WHERE lower(program_label) LIKE '%absolute%black%'
               OR lower(team_name) LIKE '%absolute%black%'
            ORDER BY age_num, team_name
            LIMIT 30
            """
        ).fetchall()
        stats["absolute_black_sample"] = [dict(r) for r in abs_rows]
        Path("data/team_identity_backfill.json").write_text(
            json.dumps(stats, indent=2), encoding="utf-8"
        )
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(backfill(refresh_from_api=True), indent=2))
