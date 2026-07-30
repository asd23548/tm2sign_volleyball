"""Coach / staff career analytics with linked team performance."""

from __future__ import annotations

import pandas as pd

from src.analytics import program_performance_metrics, load_matches_enriched
from src.db import get_connection


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params or [])
    finally:
        conn.close()


def _team_perf(matches: pd.DataFrame, team_id: str) -> dict:
    if matches.empty or not team_id:
        return {"matches": 0, "wins": 0, "win_rate": 0.0}
    sub = matches.loc[(matches["team_a_id"] == team_id) | (matches["team_b_id"] == team_id)]
    if sub.empty:
        return {"matches": 0, "wins": 0, "win_rate": 0.0}
    wins = int((sub["winner_id"] == team_id).sum())
    return {
        "matches": int(len(sub)),
        "wins": wins,
        "win_rate": float(wins / len(sub)) if len(sub) else 0.0,
    }


def load_coaches() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT
            st.staff_id,
            st.full_name,
            st.first_name,
            st.last_name,
            st.gender,
            COUNT(DISTINCT ss.event_id) AS seasons,
            COUNT(DISTINCT ss.team_id) AS teams,
            COUNT(DISTINCT COALESCE(ss.club_id, t.club_id)) AS clubs,
            MIN(ss.season_year) AS first_year,
            MAX(ss.season_year) AS last_year,
            SUM(CASE WHEN ss.position = 'head_coach' THEN 1 ELSE 0 END) AS head_coach_stints,
            SUM(CASE WHEN ss.position = 'asst_coach' THEN 1 ELSE 0 END) AS asst_coach_stints,
            GROUP_CONCAT(DISTINCT ss.position) AS roles,
            GROUP_CONCAT(DISTINCT c.club_name) AS club_names
        FROM staff st
        LEFT JOIN staff_season_stints ss ON ss.staff_id = st.staff_id
        LEFT JOIN teams t ON t.team_id = ss.team_id
        LEFT JOIN clubs c ON c.club_id = COALESCE(ss.club_id, t.club_id)
        GROUP BY st.staff_id, st.full_name, st.first_name, st.last_name, st.gender
        HAVING seasons > 0
        ORDER BY seasons DESC, st.last_name, st.first_name
        """
    )


def coach_search(query: str, limit: int = 50) -> pd.DataFrame:
    q = f"%{(query or '').strip()}%"
    return _read_sql(
        """
        SELECT
            st.staff_id,
            st.full_name,
            COUNT(DISTINCT ss.event_id) AS seasons,
            COUNT(DISTINCT ss.team_id) AS teams,
            MIN(ss.season_year) AS first_year,
            MAX(ss.season_year) AS last_year,
            SUM(CASE WHEN ss.position = 'head_coach' THEN 1 ELSE 0 END) AS head_coach_stints,
            GROUP_CONCAT(DISTINCT c.club_name) AS clubs,
            GROUP_CONCAT(DISTINCT ss.position) AS roles
        FROM staff st
        JOIN staff_season_stints ss ON ss.staff_id = st.staff_id
        LEFT JOIN teams t ON t.team_id = ss.team_id
        LEFT JOIN clubs c ON c.club_id = COALESCE(ss.club_id, t.club_id)
        WHERE st.full_name LIKE ? OR st.last_name LIKE ? OR st.first_name LIKE ?
        GROUP BY st.staff_id, st.full_name
        ORDER BY seasons DESC, head_coach_stints DESC, st.full_name
        LIMIT ?
        """,
        [q, q, q, limit],
    )


def load_coach_career(staff_id: str, matches: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per coach stint with team performance + ranking."""
    stints = _read_sql(
        """
        SELECT
            ss.staff_id,
            ss.event_id,
            ss.team_id,
            ss.program_id,
            ss.season_year,
            ss.position AS role,
            st.full_name,
            t.team_name,
            t.program_label,
            t.age_group,
            t.age_num,
            c.club_name,
            e.event_name,
            e.start_date,
            r.initial_seed,
            r.final_rank,
            r.bracket_finish
        FROM staff_season_stints ss
        JOIN staff st ON st.staff_id = ss.staff_id
        LEFT JOIN teams t ON t.team_id = ss.team_id
        LEFT JOIN clubs c ON c.club_id = COALESCE(ss.club_id, t.club_id)
        LEFT JOIN events e ON e.event_id = ss.event_id
        LEFT JOIN rankings r
          ON r.team_id = ss.team_id AND r.event_id = ss.event_id
        WHERE ss.staff_id = ?
        ORDER BY ss.season_year, t.age_num, ss.position
        """,
        [staff_id],
    )
    if stints.empty:
        return stints

    # Dedup ranking joins (multiple division ranking rows rare) keep best finish
    stints = (
        stints.sort_values(["season_year", "team_id", "role", "final_rank"], na_position="last")
        .drop_duplicates(["staff_id", "event_id", "team_id", "role"], keep="first")
        .reset_index(drop=True)
    )

    if matches is None:
        matches = load_matches_enriched()

    perfs = []
    for _, row in stints.iterrows():
        perf = _team_perf(matches, row["team_id"])
        # Also program-level if available
        prog = {"program_matches": 0, "program_win_rate": 0.0}
        if row.get("program_id") and row.get("season_year"):
            year_matches = matches.loc[
                (matches["program_a_id"] == row["program_id"])
                | (matches["program_b_id"] == row["program_id"])
            ]
            # filter to this event when possible
            if "event_id" in year_matches.columns:
                year_matches = year_matches.loc[year_matches["event_id"].astype(str) == str(row["event_id"])]
            pperf = program_performance_metrics(year_matches, row["program_id"])
            prog = {
                "program_matches": pperf["matches"],
                "program_win_rate": pperf["win_rate"],
            }
        perfs.append({**perf, **prog})

    perf_df = pd.DataFrame(perfs)
    out = pd.concat([stints.reset_index(drop=True), perf_df], axis=1)
    return out


def coach_career_summary(career: pd.DataFrame) -> dict:
    if career.empty:
        return {
            "seasons": 0,
            "teams": 0,
            "clubs": 0,
            "head_coach_stints": 0,
            "asst_coach_stints": 0,
            "career_matches": 0,
            "career_wins": 0,
            "career_win_rate": 0.0,
            "avg_finish": None,
            "gold": 0,
            "top4": 0,
        }
    # Unique team-event rows for performance (avoid double-count if multiple roles)
    team_events = career.drop_duplicates(["event_id", "team_id"])
    matches = int(team_events["matches"].fillna(0).sum())
    wins = int(team_events["wins"].fillna(0).sum())
    finishes = team_events["final_rank"].dropna()
    return {
        "seasons": int(career["season_year"].nunique()),
        "teams": int(career["team_id"].nunique()),
        "clubs": int(career["club_name"].dropna().nunique()),
        "head_coach_stints": int((career["role"] == "head_coach").sum()),
        "asst_coach_stints": int((career["role"] == "asst_coach").sum()),
        "career_matches": matches,
        "career_wins": wins,
        "career_win_rate": float(wins / matches) if matches else 0.0,
        "avg_finish": float(finishes.mean()) if len(finishes) else None,
        "gold": int((finishes == 1).sum()) if len(finishes) else 0,
        "top4": int((finishes <= 4).sum()) if len(finishes) else 0,
    }


def coach_year_rollup(career: pd.DataFrame) -> pd.DataFrame:
    """Aggregate coach performance by season year."""
    if career.empty:
        return pd.DataFrame()
    te = career.drop_duplicates(["season_year", "event_id", "team_id"]).copy()
    return (
        te.groupby("season_year", as_index=False)
        .agg(
            teams=("team_id", "nunique"),
            clubs=("club_name", "nunique"),
            matches=("matches", "sum"),
            wins=("wins", "sum"),
            avg_finish=("final_rank", "mean"),
            best_finish=("final_rank", "min"),
            gold=("final_rank", lambda s: int((s == 1).sum())),
        )
        .assign(win_rate=lambda x: x["wins"] / x["matches"].replace(0, pd.NA))
        .sort_values("season_year")
    )
