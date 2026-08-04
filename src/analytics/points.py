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


POINTS_DETAIL_COLS = [
    "team_code",
    "overall_place",
    "plq_place",
    "l1_place",
    "l1_division",
    "l1_points",
    "l2_place",
    "l2_division",
    "l2_points",
    "l3_place",
    "l3_division",
    "l3_points",
    "region_place",
    "region_points",
    "season_total",
    "bid_notes",
]


def load_points_for_program(program_id: str) -> pd.DataFrame:
    """Points rows for a program lineage, matched on USAV team code + age."""
    return _read_sql(
        """
        SELECT DISTINCT
            p.*,
            t.team_id,
            t.program_id,
            t.program_label,
            t.team_name AS tm2_team_name
        FROM power_league_points p
        JOIN teams t
          ON t.alt_code = p.team_code
         AND t.age_num = p.age_num
        WHERE t.program_id = ?
        ORDER BY p.season_year DESC, p.age_num, p.season_total DESC
        """,
        [program_id],
    )


def attach_points_to_trajectory(traj: pd.DataFrame, program_id: str) -> pd.DataFrame:
    """
    Join NCVA Power League points onto a program trajectory by season_year + age_num.

    Prefer points place/total over sparse TM2 initial_seed / final_rank.
    """
    pts = load_points_for_program(program_id)
    detail = [c for c in POINTS_DETAIL_COLS if pts.empty or c in pts.columns]
    if pts.empty:
        out = traj.copy() if traj is not None else pd.DataFrame()
        for c in detail:
            if c not in out.columns:
                out[c] = pd.NA
        return out.drop(columns=["initial_seed", "final_rank"], errors="ignore")

    pts_u = (
        pts[["season_year", "age_num", *detail]]
        .drop_duplicates(["season_year", "age_num"], keep="first")
        .copy()
    )
    if traj is None or traj.empty:
        out = pts_u.copy()
        out["age_group"] = out["age_num"].map(
            lambda a: f"{int(a)}U" if pd.notna(a) else None
        )
        return out.sort_values(["season_year", "age_num"], na_position="last").reset_index(drop=True)

    out = traj.drop(columns=["initial_seed", "final_rank"], errors="ignore").copy()
    # Avoid duplicate columns if re-attaching
    out = out.drop(columns=[c for c in detail if c in out.columns], errors="ignore")
    out = out.merge(pts_u, on=["season_year", "age_num"], how="left")
    return out.sort_values(["season_year", "age_num"], na_position="last").reset_index(drop=True)


def attach_points_by_team_code(df: pd.DataFrame) -> pd.DataFrame:
    """Left-join points onto rows that already have season_year, age_num, and alt_code/team_code."""
    if df is None or df.empty:
        return df
    code_col = "alt_code" if "alt_code" in df.columns else ("team_code" if "team_code" in df.columns else None)
    if code_col is None or "season_year" not in df.columns:
        return df.drop(columns=["initial_seed", "final_rank"], errors="ignore")

    codes = [c for c in df[code_col].dropna().astype(str).unique().tolist() if c]
    if not codes:
        return df.drop(columns=["initial_seed", "final_rank"], errors="ignore")

    placeholders = ",".join("?" * len(codes))
    pts = _read_sql(
        f"""
        SELECT *
        FROM power_league_points
        WHERE team_code IN ({placeholders})
        ORDER BY season_year DESC, age_num, season_total DESC
        """,
        codes,
    )
    detail = [c for c in POINTS_DETAIL_COLS if c in pts.columns]
    out = df.drop(columns=["initial_seed", "final_rank"], errors="ignore").copy()
    if pts.empty:
        for c in detail:
            if c not in out.columns:
                out[c] = pd.NA
        return out

    pts_u = pts[["season_year", "age_num", "team_code", *[c for c in detail if c != "team_code"]]].drop_duplicates(
        ["season_year", "age_num", "team_code"], keep="first"
    )
    merge_right = pts_u.rename(columns={"team_code": code_col}) if code_col != "team_code" else pts_u
    # Drop overlapping detail cols before merge
    drop_cols = [c for c in detail if c in out.columns and c != code_col]
    out = out.drop(columns=drop_cols, errors="ignore")
    keys = ["season_year", code_col]
    if "age_num" in out.columns:
        keys = ["season_year", "age_num", code_col]
    return out.merge(merge_right, on=keys, how="left")


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
