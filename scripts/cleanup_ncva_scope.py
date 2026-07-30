"""Audit + purge non-NCVA / orphan noise from volleyball.db.

Keeps NCVA Power League events and any team that appears in matches,
rankings, or roster stints. Drops registration-only (CT-*) orphans,
unused clubs/programs/players/staff, and unused regions.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "database" / "volleyball.db"

NCVA_PL_EVENT_SQL = """
    LOWER(event_name) LIKE '%power%league%'
    AND LOWER(event_name) LIKE '%ncva%'
"""

ACTIVE_TEAM_SQL = """
    SELECT team_a_id AS team_id FROM matches WHERE team_a_id IS NOT NULL
    UNION
    SELECT team_b_id FROM matches WHERE team_b_id IS NOT NULL
    UNION
    SELECT winner_id FROM matches WHERE winner_id IS NOT NULL
    UNION
    SELECT team_id FROM rankings
    UNION
    SELECT team_id FROM player_season_stints WHERE team_id IS NOT NULL
    UNION
    SELECT team_id FROM staff_season_stints WHERE team_id IS NOT NULL
"""


def counts(con: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "regions",
        "clubs",
        "teams",
        "programs",
        "events",
        "divisions",
        "matches",
        "rankings",
        "players",
        "player_season_stints",
        "staff",
        "staff_season_stints",
        "roster_fetch_log",
    ]
    out: dict[str, int] = {}
    for t in tables:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            out[t] = -1
    return out


def cleanup(con: sqlite3.Connection) -> dict[str, int]:
    deleted: dict[str, int] = {}

    # 1) Non-NCVA Power League events (and dependents)
    non_pl = [
        r[0]
        for r in con.execute(
            f"SELECT event_id FROM events WHERE NOT ({NCVA_PL_EVENT_SQL})"
        ).fetchall()
    ]
    if non_pl:
        placeholders = ",".join("?" * len(non_pl))
        for sql in [
            f"DELETE FROM player_season_stints WHERE event_id IN ({placeholders})",
            f"DELETE FROM staff_season_stints WHERE event_id IN ({placeholders})",
            f"DELETE FROM rankings WHERE event_id IN ({placeholders})",
            f"""
            DELETE FROM matches WHERE division_id IN (
              SELECT division_id FROM divisions WHERE event_id IN ({placeholders})
            )
            """,
            f"DELETE FROM divisions WHERE event_id IN ({placeholders})",
            f"DELETE FROM events WHERE event_id IN ({placeholders})",
        ]:
            cur = con.execute(sql, non_pl)
            key = sql.strip().split()[2]
            deleted[f"non_pl_{key}"] = deleted.get(f"non_pl_{key}", 0) + cur.rowcount

    # 2) Orphan teams (no match / ranking / stint activity)
    cur = con.execute(
        f"""
        DELETE FROM teams
        WHERE team_id NOT IN (SELECT team_id FROM ({ACTIVE_TEAM_SQL}))
        """
    )
    deleted["orphan_teams"] = cur.rowcount

    # 3) Roster fetch log for deleted / unknown teams
    cur = con.execute(
        """
        DELETE FROM roster_fetch_log
        WHERE team_id NOT IN (SELECT team_id FROM teams)
        """
    )
    deleted["orphan_roster_log"] = cur.rowcount

    # 4) Orphan programs (no remaining teams)
    cur = con.execute(
        """
        DELETE FROM programs
        WHERE program_id NOT IN (
          SELECT DISTINCT program_id FROM teams WHERE program_id IS NOT NULL
        )
        """
    )
    deleted["orphan_programs"] = cur.rowcount

    # 5) Orphan clubs (no remaining teams or programs)
    cur = con.execute(
        """
        DELETE FROM clubs
        WHERE club_id NOT IN (
          SELECT club_id FROM teams WHERE club_id IS NOT NULL
          UNION
          SELECT club_id FROM programs WHERE club_id IS NOT NULL
        )
        """
    )
    deleted["orphan_clubs"] = cur.rowcount

    # 6) Players / staff with no stints left
    cur = con.execute(
        """
        DELETE FROM players
        WHERE player_id NOT IN (SELECT DISTINCT player_id FROM player_season_stints)
        """
    )
    deleted["orphan_players"] = cur.rowcount
    cur = con.execute(
        """
        DELETE FROM staff
        WHERE staff_id NOT IN (SELECT DISTINCT staff_id FROM staff_season_stints)
        """
    )
    deleted["orphan_staff"] = cur.rowcount

    # 7) Normalize event region to R-NCVA; drop unused regions
    con.execute(
        """
        INSERT OR IGNORE INTO regions (region_id, region_name, state)
        VALUES ('R-NCVA', 'Northern California', 'CA')
        """
    )
    con.execute("UPDATE events SET region_id = 'R-NCVA'")
    # Keep club regions that still have clubs (typically R-NC), plus R-NCVA for events
    cur = con.execute(
        """
        DELETE FROM regions
        WHERE region_id NOT IN (
          SELECT DISTINCT region_id FROM clubs WHERE region_id IS NOT NULL
          UNION
          SELECT DISTINCT region_id FROM events WHERE region_id IS NOT NULL
          UNION
          SELECT 'R-NCVA'
        )
        """
    )
    deleted["unused_regions"] = cur.rowcount

    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM after cleanup")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = OFF")
    before = counts(con)

    non_pl = con.execute(
        f"SELECT event_id, event_name FROM events WHERE NOT ({NCVA_PL_EVENT_SQL})"
    ).fetchall()
    orphan_teams = con.execute(
        f"""
        SELECT COUNT(*) FROM teams
        WHERE team_id NOT IN (SELECT team_id FROM ({ACTIVE_TEAM_SQL}))
        """
    ).fetchone()[0]
    ct_orphans = con.execute(
        f"""
        SELECT COUNT(*) FROM teams
        WHERE team_id LIKE 'CT-%'
          AND team_id NOT IN (SELECT team_id FROM ({ACTIVE_TEAM_SQL}))
        """
    ).fetchone()[0]

    print("Before:", before)
    print(f"Non-NCVA-PL events: {len(non_pl)}")
    for eid, name in non_pl:
        print(f"  - {eid}: {name}")
    print(f"Orphan teams: {orphan_teams} (CT-* orphans: {ct_orphans})")

    if args.dry_run:
        print("Dry run — no changes.")
        return

    deleted = cleanup(con)
    con.commit()
    after = counts(con)
    print("Deleted:", deleted)
    print("After:", after)

    if args.vacuum:
        con.execute("VACUUM")
        print("VACUUM done.")

    con.close()


if __name__ == "__main__":
    main()
