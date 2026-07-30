"""Seed roster tables from a saved sample (and optional live fetch) for one team."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import get_connection
from src.etl.roster_transform import player_rows_from_roster, staff_rows_from_roster
from scripts.backfill_rosters import ensure_roster_schema, upsert

SAMPLE = PROJECT_ROOT / "data" / "sample_roster.json"
TEAM_ID = "ST-249672"


def main() -> None:
    roster = json.loads(SAMPLE.read_text(encoding="utf-8"))
    conn = get_connection()
    ensure_roster_schema(conn)
    row = conn.execute(
        """
        SELECT t.team_id, t.program_id, t.age_group, t.gender_code, t.club_id,
               COALESCE(
                 (SELECT d.event_id FROM matches m JOIN divisions d ON d.division_id=m.division_id
                  WHERE m.team_a_id=t.team_id OR m.team_b_id=t.team_id LIMIT 1),
                 (SELECT event_id FROM rankings WHERE team_id=t.team_id LIMIT 1)
               ) AS event_id
        FROM teams t WHERE t.team_id=?
        """,
        (TEAM_ID,),
    ).fetchone()
    if not row:
        # Fallback: known page context event 2136 division 10257
        ctx = {
            "team_id": TEAM_ID,
            "event_id": "2136",
            "program_id": None,
            "age_group": None,
            "season_year": 2026,
            "gender_code": "G",
            "club_id": None,
        }
        # try enrich
        t = conn.execute("SELECT * FROM teams WHERE team_id=?", (TEAM_ID,)).fetchone()
        if t:
            ctx.update(
                {
                    "program_id": t["program_id"],
                    "age_group": t["age_group"],
                    "gender_code": t["gender_code"] or "G",
                    "club_id": t["club_id"],
                }
            )
    else:
        ctx = dict(row)
        ctx["season_year"] = 2026
        if not ctx.get("event_id"):
            ctx["event_id"] = "2136"

    players, pstints = player_rows_from_roster(
        roster,
        team_id=ctx["team_id"],
        event_id=str(ctx["event_id"]),
        program_id=ctx.get("program_id"),
        age_group=ctx.get("age_group"),
        season_year=ctx.get("season_year"),
        gender_code=ctx.get("gender_code") or "G",
        club_id=ctx.get("club_id"),
    )
    staff, sstints = staff_rows_from_roster(
        roster,
        team_id=ctx["team_id"],
        event_id=str(ctx["event_id"]),
        program_id=ctx.get("program_id"),
        season_year=ctx.get("season_year"),
        gender_code=ctx.get("gender_code") or "G",
        club_id=ctx.get("club_id"),
    )
    upsert(conn, "players", players, ["player_id"])
    upsert(conn, "player_season_stints", pstints, ["player_id", "event_id", "team_id"])
    upsert(conn, "staff", staff, ["staff_id"])
    upsert(conn, "staff_season_stints", sstints, ["staff_id", "event_id", "team_id", "position"])
    conn.commit()
    print(
        json.dumps(
            {
                "team_id": TEAM_ID,
                "players": len(players),
                "staff": len(staff),
                "names": [p["full_name"] for p in players],
                "coaches": [s["full_name"] + ":" + sstints[i]["position"] for i, s in enumerate(staff)],
            },
            indent=2,
        )
    )
    conn.close()


if __name__ == "__main__":
    main()
