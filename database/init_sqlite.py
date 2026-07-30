"""
NCVA Sprint 2 SQLite schema.

Core: events → divisions → teams / matches / standings
Plus: regions, clubs, programs (normalized), match_sets, team_season_stats
Compat: rankings view for the Streamlit analytics layer
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "volleyball.db"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS regions (
    region_id   TEXT PRIMARY KEY,
    region_name TEXT NOT NULL,
    state       TEXT
);

CREATE TABLE IF NOT EXISTS clubs (
    club_id    TEXT PRIMARY KEY,
    club_name  TEXT NOT NULL,
    region_id  TEXT REFERENCES regions(region_id)
);

CREATE TABLE IF NOT EXISTS programs (
    program_id     TEXT PRIMARY KEY,
    program_label  TEXT NOT NULL,
    club_id        TEXT REFERENCES clubs(club_id),
    gender_code    TEXT,
    tier_label     TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    event_name   TEXT NOT NULL,
    start_date   TEXT,
    end_date     TEXT,
    location     TEXT,
    season_year  INTEGER,
    gender       TEXT,
    region_id    TEXT REFERENCES regions(region_id),
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS divisions (
    division_id   TEXT PRIMARY KEY,
    event_id      TEXT NOT NULL REFERENCES events(event_id),
    division_name TEXT NOT NULL,
    age_group     TEXT,
    gender        TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    team_id        TEXT PRIMARY KEY,
    event_id       TEXT NOT NULL REFERENCES events(event_id),
    division_id    TEXT REFERENCES divisions(division_id),
    team_name      TEXT NOT NULL,
    club_name      TEXT,
    club_id        TEXT REFERENCES clubs(club_id),
    region_id      TEXT REFERENCES regions(region_id),
    age_group      TEXT,
    age_num        INTEGER,
    cohort_year    INTEGER,
    alt_code       TEXT,
    gender_code    TEXT,
    tier_label     TEXT,
    program_id     TEXT REFERENCES programs(program_id),
    program_label  TEXT,
    initial_seed   INTEGER,
    final_rank     INTEGER,
    status         TEXT NOT NULL DEFAULT 'registered',
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    match_id               TEXT PRIMARY KEY,
    event_id               TEXT NOT NULL REFERENCES events(event_id),
    division_id            TEXT NOT NULL REFERENCES divisions(division_id),
    match_date             TEXT,
    stage                  TEXT,
    team_a_id              TEXT,
    team_b_id              TEXT,
    raw_team_a_id          TEXT,
    raw_team_b_id          TEXT,
    team_a_score           INTEGER,
    team_b_score           INTEGER,
    set_scores             TEXT,
    winner_id              TEXT,
    seed_a                 INTEGER,
    seed_b                 INTEGER,
    team_a_pts_won         INTEGER,
    team_b_pts_won         INTEGER,
    is_deciding_set_played INTEGER NOT NULL DEFAULT 0,
    is_tight_set           INTEGER NOT NULL DEFAULT 0,
    updated_at             TEXT
);

CREATE TABLE IF NOT EXISTS match_sets (
    match_id    TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    set_number  INTEGER NOT NULL,
    pts_a       INTEGER NOT NULL,
    pts_b       INTEGER NOT NULL,
    margin      INTEGER NOT NULL,
    is_tight    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (match_id, set_number)
);

-- Official TM2 seed / finish only (no computed W-L)
CREATE TABLE IF NOT EXISTS standings (
    event_id       TEXT NOT NULL REFERENCES events(event_id),
    division_id    TEXT NOT NULL REFERENCES divisions(division_id),
    team_id        TEXT NOT NULL REFERENCES teams(team_id),
    initial_seed   INTEGER,
    final_rank     INTEGER,
    bracket_finish TEXT,
    updated_at     TEXT,
    PRIMARY KEY (event_id, division_id, team_id)
);

-- Computed from matches (+ match_sets)
CREATE TABLE IF NOT EXISTS team_season_stats (
    event_id         TEXT NOT NULL REFERENCES events(event_id),
    division_id      TEXT NOT NULL REFERENCES divisions(division_id),
    team_id          TEXT NOT NULL REFERENCES teams(team_id),
    matches_played   INTEGER NOT NULL DEFAULT 0,
    wins             INTEGER NOT NULL DEFAULT 0,
    losses           INTEGER NOT NULL DEFAULT 0,
    win_rate         REAL,
    pts_won          INTEGER NOT NULL DEFAULT 0,
    pts_lost         INTEGER NOT NULL DEFAULT 0,
    pts_ratio        REAL,
    deciding_sets    INTEGER NOT NULL DEFAULT 0,
    tight_matches    INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT,
    PRIMARY KEY (event_id, division_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_divisions_event ON divisions(event_id);
CREATE INDEX IF NOT EXISTS idx_teams_event ON teams(event_id);
CREATE INDEX IF NOT EXISTS idx_teams_division ON teams(division_id);
CREATE INDEX IF NOT EXISTS idx_teams_program ON teams(program_id);
CREATE INDEX IF NOT EXISTS idx_matches_event ON matches(event_id);
CREATE INDEX IF NOT EXISTS idx_matches_division ON matches(division_id);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(team_a_id, team_b_id);
CREATE INDEX IF NOT EXISTS idx_match_sets_match ON match_sets(match_id);
CREATE INDEX IF NOT EXISTS idx_standings_team ON standings(team_id);
CREATE INDEX IF NOT EXISTS idx_team_stats_team ON team_season_stats(team_id);

CREATE VIEW IF NOT EXISTS rankings AS
SELECT
    event_id,
    division_id,
    team_id,
    initial_seed,
    final_rank,
    bracket_finish
FROM standings;

-- Roster stubs (Sprint 2+ / backfill_rosters.py)
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

CREATE TABLE IF NOT EXISTS roster_fetch_log (
    team_id      TEXT PRIMARY KEY,
    fetched_at   TEXT,
    status       TEXT,
    player_count INTEGER,
    staff_count  INTEGER,
    error        TEXT
);
"""


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_type(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone()
    return row[0] if row else None


def _ensure_columns(conn: sqlite3.Connection, table: str, cols: list[tuple[str, str]]) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col, typ in cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def refresh_derived_tables(conn: sqlite3.Connection) -> dict[str, int]:
    """Rebuild match_sets, clubs/programs/regions, team_season_stats, team.status from core rows."""
    now = conn.execute("SELECT datetime('now')").fetchone()[0]
    stats: dict[str, int] = {}

    # Regions
    conn.execute(
        """
        INSERT OR IGNORE INTO regions (region_id, region_name, state)
        VALUES ('R-NCVA', 'Northern California', 'CA')
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO regions (region_id, region_name, state)
        SELECT DISTINCT region_id, REPLACE(region_id, 'R-', ''),
               CASE WHEN region_id IN ('R-NC','R-NCVA','R-CA') THEN 'CA' ELSE NULL END
        FROM teams WHERE region_id IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO regions (region_id, region_name, state)
        SELECT DISTINCT region_id, REPLACE(region_id, 'R-', ''),
               CASE WHEN region_id IN ('R-NC','R-NCVA','R-CA') THEN 'CA' ELSE NULL END
        FROM events WHERE region_id IS NOT NULL
        """
    )

    # Clubs (physical)
    conn.execute(
        """
        INSERT INTO clubs (club_id, club_name, region_id)
        SELECT club_id, MAX(club_name), MAX(region_id)
        FROM teams
        WHERE club_id IS NOT NULL AND club_name IS NOT NULL
        GROUP BY club_id
        ON CONFLICT(club_id) DO UPDATE SET
            club_name=excluded.club_name,
            region_id=COALESCE(excluded.region_id, clubs.region_id)
        """
    )
    stats["clubs"] = conn.execute("SELECT COUNT(*) FROM clubs").fetchone()[0]

    # Programs (physical) — keyed by program_id, not label
    conn.execute(
        """
        INSERT INTO programs (program_id, program_label, club_id, gender_code, tier_label)
        SELECT
            program_id,
            MAX(program_label),
            MAX(club_id),
            MAX(gender_code),
            MAX(tier_label)
        FROM teams
        WHERE program_id IS NOT NULL AND program_label IS NOT NULL
        GROUP BY program_id
        ON CONFLICT(program_id) DO UPDATE SET
            program_label=excluded.program_label,
            club_id=COALESCE(excluded.club_id, programs.club_id),
            gender_code=COALESCE(excluded.gender_code, programs.gender_code),
            tier_label=COALESCE(excluded.tier_label, programs.tier_label)
        """
    )
    stats["programs"] = conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0]

    # match_sets from matches.set_scores JSON
    conn.execute("DELETE FROM match_sets")
    set_rows: list[tuple] = []
    for mid, raw in conn.execute(
        "SELECT match_id, set_scores FROM matches WHERE set_scores IS NOT NULL"
    ):
        try:
            sets = json.loads(raw)
        except Exception:
            continue
        if not isinstance(sets, list):
            continue
        for s in sets:
            try:
                n = int(s.get("set") or s.get("set_number") or 0)
                a = int(s.get("a") if s.get("a") is not None else s.get("pts_a"))
                b = int(s.get("b") if s.get("b") is not None else s.get("pts_b"))
            except Exception:
                continue
            if n <= 0:
                continue
            margin = abs(a - b)
            set_rows.append((mid, n, a, b, margin, 1 if margin <= 2 else 0))
    conn.executemany(
        """
        INSERT OR REPLACE INTO match_sets (match_id, set_number, pts_a, pts_b, margin, is_tight)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        set_rows,
    )
    stats["match_sets"] = len(set_rows)

    # team_season_stats from matches
    conn.execute("DELETE FROM team_season_stats")
    conn.execute(
        f"""
        INSERT INTO team_season_stats (
            event_id, division_id, team_id,
            matches_played, wins, losses, win_rate,
            pts_won, pts_lost, pts_ratio,
            deciding_sets, tight_matches, updated_at
        )
        WITH sides AS (
            SELECT event_id, division_id, team_a_id AS team_id, winner_id,
                   team_a_pts_won AS pts_for, team_b_pts_won AS pts_against,
                   is_deciding_set_played, is_tight_set
            FROM matches WHERE team_a_id IS NOT NULL
            UNION ALL
            SELECT event_id, division_id, team_b_id AS team_id, winner_id,
                   team_b_pts_won AS pts_for, team_a_pts_won AS pts_against,
                   is_deciding_set_played, is_tight_set
            FROM matches WHERE team_b_id IS NOT NULL
        ),
        agg AS (
            SELECT
                event_id,
                division_id,
                team_id,
                COUNT(*) AS matches_played,
                SUM(CASE WHEN winner_id = team_id THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN winner_id IS NOT NULL AND winner_id != team_id THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pts_for), 0) AS pts_won,
                COALESCE(SUM(pts_against), 0) AS pts_lost,
                SUM(is_deciding_set_played) AS deciding_sets,
                SUM(is_tight_set) AS tight_matches
            FROM sides
            GROUP BY event_id, division_id, team_id
        )
        SELECT
            event_id, division_id, team_id,
            matches_played, wins, losses,
            CASE WHEN matches_played > 0 THEN 1.0 * wins / matches_played END,
            pts_won, pts_lost,
            CASE WHEN pts_lost > 0 THEN 1.0 * pts_won / pts_lost END,
            deciding_sets, tight_matches, '{now}'
        FROM agg
        """
    )
    stats["team_season_stats"] = conn.execute(
        "SELECT COUNT(*) FROM team_season_stats"
    ).fetchone()[0]

    # Team status
    conn.execute(
        """
        UPDATE teams SET status = CASE
            WHEN EXISTS (
                SELECT 1 FROM matches m
                WHERE m.team_a_id = teams.team_id OR m.team_b_id = teams.team_id
            ) OR final_rank IS NOT NULL THEN 'completed'
            WHEN initial_seed IS NOT NULL THEN 'scheduled'
            ELSE 'registered'
        END
        """
    )
    for status, n in conn.execute(
        "SELECT status, COUNT(*) FROM teams GROUP BY status"
    ):
        stats[f"status_{status}"] = n

    # Official standings: drop legacy computed cols by rebuild if present
    standing_cols = {r[1] for r in conn.execute("PRAGMA table_info(standings)").fetchall()}
    if {"wins", "losses", "matches_played"} & standing_cols:
        conn.execute("ALTER TABLE standings RENAME TO standings_legacy")
        conn.execute(
            """
            CREATE TABLE standings (
                event_id       TEXT NOT NULL REFERENCES events(event_id),
                division_id    TEXT NOT NULL REFERENCES divisions(division_id),
                team_id        TEXT NOT NULL REFERENCES teams(team_id),
                initial_seed   INTEGER,
                final_rank     INTEGER,
                bracket_finish TEXT,
                updated_at     TEXT,
                PRIMARY KEY (event_id, division_id, team_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO standings (event_id, division_id, team_id, initial_seed, final_rank, bracket_finish, updated_at)
            SELECT event_id, division_id, team_id, initial_seed, final_rank, bracket_finish, updated_at
            FROM standings_legacy
            """
        )
        conn.execute("DROP TABLE standings_legacy")
        conn.execute("DROP VIEW IF EXISTS rankings")
        conn.execute(
            """
            CREATE VIEW rankings AS
            SELECT event_id, division_id, team_id, initial_seed, final_rank, bracket_finish
            FROM standings
            """
        )
        stats["standings_rebuilt"] = 1

    conn.commit()
    return stats


def migrate_sprint2(db_path: Path | str | None = None) -> dict[str, int]:
    """Upgrade an existing Sprint 1 DB to Sprint 2 without a full re-crawl."""
    path = Path(db_path) if db_path else DB_PATH
    conn = get_connection(path)
    try:
        # Replace identity views with tables
        for name in ("clubs", "programs", "regions"):
            kind = _table_type(conn, name)
            if kind == "view":
                conn.execute(f"DROP VIEW IF EXISTS {name}")

        conn.executescript(SCHEMA_SQL)

        _ensure_columns(
            conn,
            "teams",
            [("status", "TEXT NOT NULL DEFAULT 'registered'")],
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_status ON teams(status)")
        _ensure_columns(
            conn,
            "matches",
            [("raw_team_a_id", "TEXT"), ("raw_team_b_id", "TEXT")],
        )
        # Backfill raw ids from ST- prefixes when missing
        conn.execute(
            """
            UPDATE matches SET raw_team_a_id = REPLACE(team_a_id, 'ST-', '')
            WHERE team_a_id IS NOT NULL AND raw_team_a_id IS NULL
            """
        )
        conn.execute(
            """
            UPDATE matches SET raw_team_b_id = REPLACE(team_b_id, 'ST-', '')
            WHERE team_b_id IS NOT NULL AND raw_team_b_id IS NULL
            """
        )
        conn.commit()
        return refresh_derived_tables(conn)
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None, *, reset: bool = False) -> Path:
    """Create Sprint 2 tables (optionally wipe first)."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if reset and path.exists():
        path.unlink()
    conn = get_connection(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return path


def vacuum_db(db_path: Path | str | None = None) -> Path:
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("VACUUM;")
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    created = init_db(reset=False)
    result = migrate_sprint2(created)
    vacuum_db(created)
    print(f"Initialized/migrated Sprint 2 schema at {created}")
    print(result)
