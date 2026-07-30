"""Player/staff analytics helpers."""

from __future__ import annotations

import pandas as pd

from src.db import get_connection


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params or [])
    finally:
        conn.close()


def load_players() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT p.*,
               COUNT(DISTINCT s.event_id) AS seasons,
               COUNT(DISTINCT s.team_id) AS teams,
               MIN(s.season_year) AS first_year,
               MAX(s.season_year) AS last_year
        FROM players p
        LEFT JOIN player_season_stints s ON s.player_id = p.player_id
        GROUP BY p.player_id, p.full_name, p.first_name, p.last_name, p.gender, p.grad_year
        ORDER BY p.last_name, p.first_name
        """
    )


def load_player_stints(player_id: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT
            s.player_id,
            s.event_id,
            s.team_id,
            s.program_id,
            s.age_group,
            s.season_year,
            s.club_id,
            s.uniform_number,
            p.full_name,
            p.first_name,
            p.last_name,
            t.team_name,
            t.program_label,
            t.age_num,
            t.program_id AS team_program_id,
            c.club_name,
            e.event_name,
            e.start_date,
            r.initial_seed,
            r.final_rank,
            r.bracket_finish
        FROM player_season_stints s
        JOIN players p ON p.player_id = s.player_id
        LEFT JOIN teams t ON t.team_id = s.team_id
        LEFT JOIN clubs c ON c.club_id = COALESCE(s.club_id, t.club_id)
        LEFT JOIN events e ON e.event_id = s.event_id
        LEFT JOIN rankings r
          ON r.team_id = s.team_id AND r.event_id = s.event_id
    """
    if player_id:
        sql += " WHERE s.player_id = ?"
        df = _read_sql(sql + " ORDER BY s.season_year, t.age_num, r.final_rank", [player_id])
    else:
        df = _read_sql(sql + " ORDER BY p.last_name, p.first_name, s.season_year")
    if df.empty:
        return df
    # One ranking row per stint (best finish if duplicates)
    df = (
        df.sort_values(["player_id", "event_id", "team_id", "final_rank"], na_position="last")
        .drop_duplicates(["player_id", "event_id", "team_id"], keep="first")
        .reset_index(drop=True)
    )
    # Prefer stint program_id, else team program_id
    if "program_id" in df.columns:
        df["program_id"] = df["program_id"].fillna(df.get("team_program_id"))
    return df


def enrich_player_stints_with_team_perf(stints: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Add team match/win/WR columns for each player stint."""
    if stints.empty:
        return stints.assign(matches=0, wins=0, win_rate=0.0)
    rows = []
    for _, row in stints.iterrows():
        tid = row["team_id"]
        eid = str(row["event_id"]) if row.get("event_id") is not None else None
        sub = matches.loc[(matches["team_a_id"] == tid) | (matches["team_b_id"] == tid)]
        if eid is not None and "event_id" in sub.columns:
            sub = sub.loc[sub["event_id"].astype(str) == eid]
        n = int(len(sub))
        wins = int((sub["winner_id"] == tid).sum()) if n else 0
        rows.append(
            {
                "matches": n,
                "wins": wins,
                "win_rate": float(wins / n) if n else 0.0,
            }
        )
    return pd.concat([stints.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def load_staff() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT st.*,
               COUNT(DISTINCT ss.event_id) AS seasons,
               COUNT(DISTINCT ss.team_id) AS teams,
               MIN(ss.season_year) AS first_year,
               MAX(ss.season_year) AS last_year
        FROM staff st
        LEFT JOIN staff_season_stints ss ON ss.staff_id = st.staff_id
        GROUP BY st.staff_id, st.full_name, st.first_name, st.last_name, st.gender
        ORDER BY st.last_name, st.first_name
        """
    )


def load_staff_stints(staff_id: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT
            ss.*,
            st.full_name,
            t.team_name,
            t.program_label,
            c.club_name,
            e.event_name,
            e.start_date
        FROM staff_season_stints ss
        JOIN staff st ON st.staff_id = ss.staff_id
        LEFT JOIN teams t ON t.team_id = ss.team_id
        LEFT JOIN clubs c ON c.club_id = COALESCE(ss.club_id, t.club_id)
        LEFT JOIN events e ON e.event_id = ss.event_id
    """
    if staff_id:
        return _read_sql(sql + " WHERE ss.staff_id = ? ORDER BY ss.season_year", [staff_id])
    return _read_sql(sql + " ORDER BY st.last_name, ss.season_year")


def player_search(query: str, limit: int = 50) -> pd.DataFrame:
    q = f"%{(query or '').strip()}%"
    return _read_sql(
        """
        SELECT p.player_id, p.full_name, p.gender,
               COUNT(DISTINCT s.event_id) AS seasons,
               MIN(s.season_year) AS first_year,
               MAX(s.season_year) AS last_year,
               GROUP_CONCAT(DISTINCT t.program_label) AS programs
        FROM players p
        LEFT JOIN player_season_stints s ON s.player_id = p.player_id
        LEFT JOIN teams t ON t.team_id = s.team_id
        WHERE p.full_name LIKE ? OR p.last_name LIKE ? OR p.first_name LIKE ?
        GROUP BY p.player_id, p.full_name, p.gender
        ORDER BY seasons DESC, p.last_name, p.first_name
        LIMIT ?
        """,
        [q, q, q, limit],
    )
