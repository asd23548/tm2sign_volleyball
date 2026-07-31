"""NCVA Power League points analytics."""

from __future__ import annotations

import pandas as pd

from src.db import get_connection


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params or [])
    finally:
        conn.close()


def load_power_league_points(
    *,
    season_year: int | None = None,
    age_num: int | None = None,
    gender: str | None = None,
    team_code: str | None = None,
) -> pd.DataFrame:
    clauses = []
    params: list = []
    if season_year is not None:
        clauses.append("p.season_year = ?")
        params.append(season_year)
    if age_num is not None:
        clauses.append("p.age_num = ?")
        params.append(age_num)
    if gender is not None and gender != "All":
        clauses.append("p.gender = ?")
        params.append(gender)
    if team_code:
        clauses.append("p.team_code = ?")
        params.append(team_code)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return _read_sql(
        f"""
        SELECT
            p.*,
            t.team_id,
            t.program_id,
            t.program_label,
            t.club_id,
            c.club_name
        FROM power_league_points p
        LEFT JOIN teams t
          ON t.alt_code = p.team_code
         AND t.age_num = p.age_num
        LEFT JOIN clubs c ON c.club_id = t.club_id
        {where}
        ORDER BY p.season_year DESC, p.age_num, p.season_total DESC, p.overall_place
        """,
        params,
    )


def load_points_leaderboard(
    season_year: int,
    age_num: int,
    gender: str = "Girls",
) -> pd.DataFrame:
    """One best-matched team row per points team_code for a season/age."""
    df = _read_sql(
        """
        SELECT
            p.*,
            t.team_id,
            t.program_id,
            t.program_label,
            t.club_id,
            c.club_name AS club_name
        FROM power_league_points p
        LEFT JOIN teams t
          ON t.alt_code = p.team_code
         AND t.age_num = p.age_num
        LEFT JOIN clubs c ON c.club_id = t.club_id
        WHERE p.season_year = ?
          AND p.age_num = ?
          AND p.gender = ?
        ORDER BY
          CASE WHEN p.season_total IS NULL THEN 1 ELSE 0 END,
          p.season_total DESC,
          CASE WHEN p.overall_place IS NULL THEN 1 ELSE 0 END,
          p.overall_place ASC,
          p.team_name
        """,
        [season_year, age_num, gender],
    )
    if df.empty:
        return df
    return df.drop_duplicates("team_code", keep="first").reset_index(drop=True)


def load_points_for_program(program_id: str) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT DISTINCT
            p.*,
            t.team_id,
            t.program_id,
            t.program_label,
            t.team_name AS tm2_team_name
        FROM power_league_points p
        JOIN teams t ON t.alt_code = p.team_code
        WHERE t.program_id = ?
        ORDER BY p.season_year DESC, p.age_num, p.season_total DESC
        """,
        [program_id],
    )


def load_points_years(gender: str | None = "Girls") -> list[int]:
    params = []
    sql = "SELECT DISTINCT season_year FROM power_league_points"
    if gender and gender != "All":
        sql += " WHERE gender = ?"
        params.append(gender)
    sql += " ORDER BY season_year DESC"
    df = _read_sql(sql, params)
    return [int(y) for y in df["season_year"].tolist()] if not df.empty else []


def load_points_ages(season_year: int, gender: str = "Girls") -> list[int]:
    df = _read_sql(
        """
        SELECT DISTINCT age_num FROM power_league_points
        WHERE season_year = ? AND gender = ?
        ORDER BY age_num
        """,
        [season_year, gender],
    )
    return [int(a) for a in df["age_num"].tolist()] if not df.empty else []
