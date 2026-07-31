"""Backfill player/coach rosters for all Power League scheduler teams."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import get_connection
from src.etl.roster_transform import player_rows_from_roster, staff_rows_from_roster
from src.etl.tm2_client import TM2Client

SUMMARY = PROJECT_ROOT / "data" / "roster_backfill_summary.json"


def ensure_roster_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            player_id    TEXT PRIMARY KEY,
            full_name    TEXT NOT NULL,
            first_name   TEXT,
            last_name    TEXT,
            gender       TEXT,
            grad_year    INTEGER
        );
        CREATE TABLE IF NOT EXISTS player_season_stints (
            player_id       TEXT NOT NULL,
            event_id        TEXT NOT NULL,
            team_id         TEXT NOT NULL,
            program_id      TEXT,
            age_group       TEXT,
            season_year     INTEGER,
            club_id         TEXT,
            uniform_number  INTEGER,
            role            TEXT DEFAULT 'player',
            PRIMARY KEY (player_id, event_id, team_id)
        );
        CREATE TABLE IF NOT EXISTS staff (
            staff_id     TEXT PRIMARY KEY,
            full_name    TEXT NOT NULL,
            first_name   TEXT,
            last_name    TEXT,
            gender       TEXT
        );
        CREATE TABLE IF NOT EXISTS staff_season_stints (
            staff_id     TEXT NOT NULL,
            event_id     TEXT NOT NULL,
            team_id      TEXT NOT NULL,
            program_id   TEXT,
            season_year  INTEGER,
            club_id      TEXT,
            position     TEXT,
            PRIMARY KEY (staff_id, event_id, team_id, position)
        );
        CREATE INDEX IF NOT EXISTS idx_player_stints_name ON player_season_stints(player_id);
        CREATE INDEX IF NOT EXISTS idx_player_stints_year ON player_season_stints(season_year);
        CREATE INDEX IF NOT EXISTS idx_player_stints_program ON player_season_stints(program_id);
        CREATE INDEX IF NOT EXISTS idx_staff_stints_staff ON staff_season_stints(staff_id);
        """
    )
    # migrate older players table if columns missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(players)").fetchall()}
    for col, typ in (("first_name", "TEXT"), ("last_name", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE players ADD COLUMN {col} {typ}")
    stint_cols = {r[1] for r in conn.execute("PRAGMA table_info(player_season_stints)").fetchall()}
    for col, typ in (
        ("club_id", "TEXT"),
        ("uniform_number", "INTEGER"),
        ("role", "TEXT"),
    ):
        if col not in stint_cols:
            conn.execute(f"ALTER TABLE player_season_stints ADD COLUMN {col} {typ}")
    conn.commit()


def upsert(conn, table: str, rows: list[dict], pk: list[str]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(pk)}) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


def team_context(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT
            t.team_id,
            t.team_name,
            t.program_id,
            t.program_label,
            t.age_group,
            t.gender_code,
            t.club_id,
            COALESCE(
                t.event_id,
                (SELECT d.event_id FROM rankings r
                 JOIN divisions d ON d.division_id = r.division_id
                 WHERE r.team_id = t.team_id LIMIT 1),
                (SELECT m.event_id FROM matches m
                 WHERE m.team_a_id = t.team_id OR m.team_b_id = t.team_id
                 LIMIT 1)
            ) AS event_id
        FROM teams t
        WHERE t.team_id LIKE 'ST-%'
          AND COALESCE(t.status, 'completed') != 'registered'
        """
    ).fetchall()
    # attach season year from events
    events = {
        str(r["event_id"]): dict(r)
        for r in conn.execute("SELECT event_id, start_date, event_name FROM events").fetchall()
    }
    out = []
    for r in rows:
        eid = r["event_id"]
        if not eid:
            continue
        ev = events.get(str(eid)) or {}
        year = None
        if ev.get("start_date"):
            try:
                year = int(str(ev["start_date"])[:4])
            except Exception:
                year = None
        out.append({**dict(r), "event_id": str(eid), "season_year": year})
    # Newest seasons first — older TM2 seasons often return empty rosters
    out.sort(key=lambda t: (t.get("season_year") or 0, t.get("event_id") or ""), reverse=True)
    return out


def backfill_rosters(
    workers: int = 3,
    limit: int | None = None,
    resume: bool = True,
    min_year: int | None = None,
) -> dict:
    import time

    conn = get_connection()
    ensure_roster_schema(conn)
    teams = team_context(conn)
    if min_year is not None:
        teams = [t for t in teams if (t.get("season_year") or 0) >= min_year]
    if resume:
        # Only skip teams that already have persisted stints.
        # Do not skip "empty" log rows — older runs marked rate-limits / old seasons
        # as empty before newer seasons were prioritized.
        done_ids = {
            r["team_id"]
            for r in conn.execute(
                """
                SELECT DISTINCT team_id FROM player_season_stints
                UNION
                SELECT DISTINCT team_id FROM staff_season_stints
                """
            ).fetchall()
        }
        before = len(teams)
        teams = [t for t in teams if t["team_id"] not in done_ids]
        print(f"Resume: skipping {before - len(teams)} already persisted", flush=True)
    if limit:
        teams = teams[:limit]
    years = sorted({t.get("season_year") for t in teams if t.get("season_year")}, reverse=True)
    print(
        f"Fetching rosters for {len(teams)} teams (workers={workers}, years={years[:6]})…",
        flush=True,
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roster_fetch_log (
            team_id TEXT PRIMARY KEY,
            status TEXT,
            players INTEGER,
            staff INTEGER,
            fetched_at TEXT,
            player_count INTEGER,
            staff_count INTEGER,
            error TEXT
        )
        """
    )
    # Align Sprint-2 schema (player_count/staff_count) with backfill columns (players/staff)
    log_cols = {r[1] for r in conn.execute("PRAGMA table_info(roster_fetch_log)").fetchall()}
    for col, typ in (
        ("players", "INTEGER"),
        ("staff", "INTEGER"),
        ("status", "TEXT"),
        ("fetched_at", "TEXT"),
        ("player_count", "INTEGER"),
        ("staff_count", "INTEGER"),
        ("error", "TEXT"),
    ):
        if col not in log_cols:
            conn.execute(f"ALTER TABLE roster_fetch_log ADD COLUMN {col} {typ}")
    conn.commit()

    players_all = []
    player_stints = []
    staff_all = []
    staff_stints = []
    errors = []
    nonempty = 0
    empty = 0

    def fetch_one(ctx: dict):
        sid = ctx["team_id"].removeprefix("ST-")
        time.sleep(0.15)  # gentle pacing per worker
        with TM2Client(timeout=90.0, max_workers=1) as client:
            roster = client.scheduler_team_roster(sid)
        return ctx, roster

    done = 0
    chunk = 40
    for start in range(0, len(teams), chunk):
        batch = teams[start : start + chunk]
        chunk_players: list[dict] = []
        chunk_player_stints: list[dict] = []
        chunk_staff: list[dict] = []
        chunk_staff_stints: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_one, ctx) for ctx in batch]
            for fut in as_completed(futures):
                done += 1
                try:
                    ctx, roster = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append({"team_id": None, "error": str(exc)})
                    if done % 20 == 0 or done == len(teams):
                        print(f"  {done}/{len(teams)} errors={len(errors)}", flush=True)
                    time.sleep(2)
                    continue
                n_p = len(roster.get("players") or [])
                n_s = len(roster.get("staff") or [])
                status = "ok" if (n_p or n_s) else "empty"
                if n_p or n_s:
                    nonempty += 1
                else:
                    empty += 1
                p_rows, p_stints = player_rows_from_roster(
                    roster,
                    team_id=ctx["team_id"],
                    event_id=ctx["event_id"],
                    program_id=ctx.get("program_id"),
                    age_group=ctx.get("age_group"),
                    season_year=ctx.get("season_year"),
                    gender_code=ctx.get("gender_code"),
                    club_id=ctx.get("club_id"),
                )
                s_rows, s_stints = staff_rows_from_roster(
                    roster,
                    team_id=ctx["team_id"],
                    event_id=ctx["event_id"],
                    program_id=ctx.get("program_id"),
                    season_year=ctx.get("season_year"),
                    gender_code=ctx.get("gender_code"),
                    club_id=ctx.get("club_id"),
                )
                chunk_players.extend(p_rows)
                chunk_player_stints.extend(p_stints)
                chunk_staff.extend(s_rows)
                chunk_staff_stints.extend(s_stints)
                players_all.extend(p_rows)
                player_stints.extend(p_stints)
                staff_all.extend(s_rows)
                staff_stints.extend(s_stints)
                conn.execute(
                    """
                    INSERT INTO roster_fetch_log(team_id, status, players, staff, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(team_id) DO UPDATE SET
                        status=excluded.status,
                        players=excluded.players,
                        staff=excluded.staff,
                        fetched_at=excluded.fetched_at
                    """,
                    (
                        ctx["team_id"],
                        status,
                        n_p,
                        n_s,
                        datetime.utcnow().isoformat() + "Z",
                    ),
                )
                if done % 20 == 0 or done == len(teams):
                    print(
                        f"  {done}/{len(teams)} ok={nonempty} empty={empty} "
                        f"players={len(players_all)} staff={len(staff_all)} errors={len(errors)}",
                        flush=True,
                    )
        # Persist each chunk so a crash does not lose rosters already fetched
        players_by_id = {p["player_id"]: p for p in chunk_players}
        staff_by_id = {s["staff_id"]: s for s in chunk_staff}
        upsert(conn, "players", list(players_by_id.values()), ["player_id"])
        upsert(conn, "player_season_stints", chunk_player_stints, ["player_id", "event_id", "team_id"])
        upsert(conn, "staff", list(staff_by_id.values()), ["staff_id"])
        upsert(
            conn,
            "staff_season_stints",
            chunk_staff_stints,
            ["staff_id", "event_id", "team_id", "position"],
        )
        conn.commit()
        db_p = conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
        db_s = conn.execute("SELECT COUNT(*) c FROM staff").fetchone()["c"]
        print(
            f"  flushed chunk start={start}: "
            f"+players={len(players_by_id)} +staff={len(staff_by_id)} "
            f"db_players={db_p} db_staff={db_s}",
            flush=True,
        )
        # pause between chunks to stay under rate limit
        time.sleep(3)

    summary = {
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "teams_attempted": len(teams),
        "teams_with_roster": nonempty,
        "players": conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"],
        "player_stints": conn.execute("SELECT COUNT(*) c FROM player_season_stints").fetchone()["c"],
        "staff": conn.execute("SELECT COUNT(*) c FROM staff").fetchone()["c"],
        "staff_stints": conn.execute("SELECT COUNT(*) c FROM staff_season_stints").fetchone()["c"],
        "errors": errors[:20],
        "error_count": len(errors),
    }
    conn.close()
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-year", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            backfill_rosters(
                workers=args.workers,
                limit=args.limit,
                resume=not args.no_resume,
                min_year=args.min_year,
            ),
            indent=2,
        )
    )