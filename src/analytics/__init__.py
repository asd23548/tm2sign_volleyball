"""Analytics engine: performance, resilience, rivalry, geography, seed dynamics."""

from __future__ import annotations

import json
from typing import Optional

import numpy as np
import pandas as pd

from src.db import get_connection


def _read_sql(sql: str, params: tuple | list | None = None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params or [])
    finally:
        conn.close()


def load_matches_enriched() -> pd.DataFrame:
    sql = """
    SELECT
        m.*,
        ta.team_name AS team_a_name,
        tb.team_name AS team_b_name,
        ta.age_group AS age_group,
        ta.cohort_year AS cohort_year,
        ta.age_num AS age_num,
        ta.program_id AS program_a_id,
        tb.program_id AS program_b_id,
        ta.program_label AS program_a_label,
        tb.program_label AS program_b_label,
        ta.club_id AS club_a_id,
        tb.club_id AS club_b_id,
        ca.club_name AS club_a_name,
        cb.club_name AS club_b_name,
        ca.region_id AS region_a_id,
        cb.region_id AS region_b_id,
        ra.region_name AS region_a_name,
        rb.region_name AS region_b_name,
        d.division_name,
        d.event_id,
        e.event_name,
        e.location,
        e.region_id AS event_region_id,
        e.start_date,
        er.region_name AS event_region_name
    FROM matches m
    LEFT JOIN teams ta ON ta.team_id = m.team_a_id
    LEFT JOIN teams tb ON tb.team_id = m.team_b_id
    LEFT JOIN clubs ca ON ca.club_id = ta.club_id
    LEFT JOIN clubs cb ON cb.club_id = tb.club_id
    LEFT JOIN regions ra ON ra.region_id = ca.region_id
    LEFT JOIN regions rb ON rb.region_id = cb.region_id
    LEFT JOIN divisions d ON d.division_id = m.division_id
    LEFT JOIN events e ON e.event_id = d.event_id
    LEFT JOIN regions er ON er.region_id = e.region_id
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["set_scores_parsed"] = df["set_scores"].apply(_parse_sets)
    return df


def load_programs() -> pd.DataFrame:
    df = _read_sql(
        """
        SELECT
            p.program_id,
            p.program_label,
            p.gender_code,
            p.tier_label,
            p.club_id,
            c.club_name,
            COUNT(DISTINCT t.team_id) AS season_entries,
            COUNT(DISTINCT t.age_num) AS ages_seen
        FROM programs p
        LEFT JOIN teams t ON t.program_id = p.program_id
        LEFT JOIN clubs c ON c.club_id = COALESCE(p.club_id, t.club_id)
        WHERE p.program_label IS NOT NULL AND TRIM(p.program_label) != ''
        GROUP BY p.program_id, p.program_label, p.gender_code, p.tier_label, p.club_id, c.club_name
        HAVING season_entries > 0
        ORDER BY p.program_label
        """
    )
    if df.empty:
        return df
    # Prefer TM2 alt-code program ids (G|CLUB|slot|RG) when labels collide
    df["_pref"] = df["program_id"].astype(str).str.contains(r"\|", regex=True).astype(int)
    df = (
        df.sort_values(["program_label", "_pref", "season_entries"], ascending=[True, False, False])
        .drop_duplicates("program_label", keep="first")
        .drop(columns=["_pref"])
        .sort_values("program_label")
        .reset_index(drop=True)
    )
    return df


def load_teams() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT t.*, c.club_name, c.region_id, r.region_name, r.state
        FROM teams t
        LEFT JOIN clubs c ON c.club_id = t.club_id
        LEFT JOIN regions r ON r.region_id = c.region_id
        WHERE t.team_id LIKE 'ST-%'
        ORDER BY t.team_name
        """
    )


def load_rankings_enriched() -> pd.DataFrame:
    sql = """
    SELECT
        r.*,
        t.team_name,
        t.age_group,
        t.age_num,
        t.cohort_year,
        t.program_id,
        t.program_label,
        t.club_id,
        c.club_name,
        c.region_id,
        reg.region_name,
        d.division_name,
        e.event_name,
        e.start_date
    FROM rankings r
    JOIN teams t ON t.team_id = r.team_id
    LEFT JOIN clubs c ON c.club_id = t.club_id
    LEFT JOIN regions reg ON reg.region_id = c.region_id
    LEFT JOIN divisions d ON d.division_id = r.division_id
    LEFT JOIN events e ON e.event_id = r.event_id
    """
    return _read_sql(sql)


def _parse_sets(raw) -> list[dict]:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return []
    if isinstance(raw, list):
        return raw
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def _team_matches(df: pd.DataFrame, team_id: str) -> pd.DataFrame:
    if df.empty:
        return df
    mask = (df["team_a_id"] == team_id) | (df["team_b_id"] == team_id)
    return df.loc[mask].copy()


def _program_matches(df: pd.DataFrame, program_id: str) -> pd.DataFrame:
    if df.empty or not program_id:
        return df.iloc[0:0].copy()
    mask = (df.get("program_a_id") == program_id) | (df.get("program_b_id") == program_id)
    return df.loc[mask].copy()


def filter_matches(
    df: pd.DataFrame,
    year: Optional[int] = None,
    age_group: Optional[str] = None,
    stage: Optional[str] = None,
    team_id: Optional[str] = None,
    event_id: Optional[str] = None,
    program_id: Optional[str] = None,
) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    if event_id and event_id != "All":
        out = out.loc[out["event_id"].astype(str) == str(event_id)]
    if year is not None and "start_date" in out.columns:
        years = pd.to_datetime(out["start_date"], errors="coerce").dt.year
        out = out.loc[years == year]
    if age_group and age_group != "All":
        # Prefer numeric age when available
        if "age_num" in out.columns and str(age_group).endswith("U"):
            try:
                num = int(str(age_group).replace("U", ""))
                out = out.loc[out["age_num"] == num]
            except Exception:
                out = out.loc[out["age_group"] == age_group]
        else:
            out = out.loc[out["age_group"] == age_group]
    if stage and stage != "All":
        out = out.loc[out["stage"].fillna("").str.contains(stage, case=False, na=False)]
    if program_id:
        out = _program_matches(out, program_id)
    elif team_id:
        out = _team_matches(out, team_id)
    return out.reset_index(drop=True)


def performance_metrics(df: pd.DataFrame, team_id: str) -> dict:
    sub = _team_matches(df, team_id)
    return _perf_from_sub(sub, lambda row: row["winner_id"] == team_id, team_id)


def program_performance_metrics(df: pd.DataFrame, program_id: str) -> dict:
    sub = _program_matches(df, program_id)
    if sub.empty:
        return {
            "matches": 0,
            "wins": 0,
            "win_rate": 0.0,
            "set_win_rate": 0.0,
            "point_diff_ratio": 0.0,
        }

    def is_win(row) -> bool:
        if row["winner_id"] == row["team_a_id"] and row.get("program_a_id") == program_id:
            return True
        if row["winner_id"] == row["team_b_id"] and row.get("program_b_id") == program_id:
            return True
        return False

    return _perf_from_sub(sub, is_win, program_id, program_mode=True)


def _perf_from_sub(sub: pd.DataFrame, is_win, subject_id: str, program_mode: bool = False) -> dict:
    if sub.empty:
        return {
            "matches": 0,
            "wins": 0,
            "win_rate": 0.0,
            "set_win_rate": 0.0,
            "point_diff_ratio": 0.0,
        }
    wins = sum(1 for _, row in sub.iterrows() if is_win(row))
    sets_won = sets_lost = pts_for = pts_against = 0
    for _, row in sub.iterrows():
        if program_mode:
            is_a = row.get("program_a_id") == subject_id
        else:
            is_a = row["team_a_id"] == subject_id
        for s in row.get("set_scores_parsed") or []:
            a = int(s.get("a", s.get("team_a", 0)) or 0)
            b = int(s.get("b", s.get("team_b", 0)) or 0)
            mine, opp = (a, b) if is_a else (b, a)
            pts_for += mine
            pts_against += opp
            if mine > opp:
                sets_won += 1
            elif opp > mine:
                sets_lost += 1
    total_sets = sets_won + sets_lost
    return {
        "matches": int(len(sub)),
        "wins": int(wins),
        "win_rate": float(wins / len(sub)) if len(sub) else 0.0,
        "set_win_rate": float(sets_won / total_sets) if total_sets else 0.0,
        "point_diff_ratio": float(pts_for / pts_against) if pts_against else 0.0,
    }


def program_season_trajectory(matches: pd.DataFrame, rankings: pd.DataFrame, program_id: str) -> pd.DataFrame:
    """One row per season/age for a program lineage (e.g. Absolute Black)."""
    sub = _program_matches(matches, program_id)
    rows = []
    if not sub.empty:
        sub = sub.copy()
        sub["season_year"] = pd.to_datetime(sub["start_date"], errors="coerce").dt.year
        for (year, age, event_id, event_name), g in sub.groupby(
            ["season_year", "age_num", "event_id", "event_name"], dropna=False
        ):
            perf = program_performance_metrics(g, program_id)
            # representative team name that season
            names = pd.concat([g["team_a_name"], g["team_b_name"]], ignore_index=True)
            # prefer names on this program side
            prog_names = []
            for _, row in g.iterrows():
                if row.get("program_a_id") == program_id:
                    prog_names.append(row["team_a_name"])
                if row.get("program_b_id") == program_id:
                    prog_names.append(row["team_b_name"])
            label = pd.Series(prog_names).mode().iloc[0] if prog_names else None
            rows.append(
                {
                    "season_year": int(year) if pd.notna(year) else None,
                    "age_num": int(age) if pd.notna(age) else None,
                    "age_group": f"{int(age)}U" if pd.notna(age) else None,
                    "event_id": event_id,
                    "event_name": event_name,
                    "team_name": label,
                    "matches": perf["matches"],
                    "wins": perf["wins"],
                    "win_rate": perf["win_rate"],
                    "set_win_rate": perf["set_win_rate"],
                    "point_diff_ratio": perf["point_diff_ratio"],
                }
            )
    traj = pd.DataFrame(rows)
    if rankings is not None and not rankings.empty and "program_id" in rankings.columns:
        rk = rankings.loc[rankings["program_id"] == program_id].copy()
        if not rk.empty:
            rk["season_year"] = pd.to_datetime(rk["start_date"], errors="coerce").dt.year
            finish = (
                rk.groupby(["season_year", "event_id", "age_num"], as_index=False)
                .agg(
                    final_rank=("final_rank", "min"),
                    initial_seed=("initial_seed", "min"),
                )
            )
            if not traj.empty:
                traj = traj.merge(finish, on=["season_year", "event_id", "age_num"], how="left")
            else:
                traj = finish
    if traj.empty:
        return traj
    return traj.sort_values(["season_year", "age_num"], na_position="last").reset_index(drop=True)


def resilience_metrics(df: pd.DataFrame, team_id: str) -> dict:
    sub = _team_matches(df, team_id)
    dec_wins = dec_total = tight_wins = tight_total = come_wins = come_total = 0
    for _, row in sub.iterrows():
        sets = row.get("set_scores_parsed") or []
        if not sets:
            continue
        is_a = row["team_a_id"] == team_id
        normalized = []
        for s in sets:
            a = int(s.get("a", s.get("team_a", 0)) or 0)
            b = int(s.get("b", s.get("team_b", 0)) or 0)
            mine, opp = (a, b) if is_a else (b, a)
            normalized.append((mine, opp))
        # deciding set = 3rd set when present
        if len(normalized) >= 3:
            dec_total += 1
            if normalized[2][0] > normalized[2][1]:
                dec_wins += 1
        for mine, opp in normalized:
            if abs(mine - opp) <= 2:
                tight_total += 1
                if mine > opp:
                    tight_wins += 1
        if len(normalized) >= 2 and normalized[0][0] < normalized[0][1]:
            come_total += 1
            if row["winner_id"] == team_id:
                come_wins += 1
    return {
        "deciding_set_win_rate": float(dec_wins / dec_total) if dec_total else 0.0,
        "tight_set_win_rate": float(tight_wins / tight_total) if tight_total else 0.0,
        "comeback_rate": float(come_wins / come_total) if come_total else 0.0,
        "deciding_sets": dec_total,
        "tight_sets": tight_total,
        "comeback_opportunities": come_total,
    }


def h2h_matrix(df: pd.DataFrame, team_id: str) -> pd.DataFrame:
    sub = _team_matches(df, team_id)
    rows = []
    for _, row in sub.iterrows():
        opp = row["team_b_id"] if row["team_a_id"] == team_id else row["team_a_id"]
        opp_name = row["team_b_name"] if row["team_a_id"] == team_id else row["team_a_name"]
        rows.append(
            {
                "opponent_id": opp,
                "opponent": opp_name,
                "win": int(row["winner_id"] == team_id),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["opponent", "played", "wins", "win_rate"])
    tmp = pd.DataFrame(rows)
    out = (
        tmp.groupby(["opponent_id", "opponent"], as_index=False)
        .agg(played=("win", "size"), wins=("win", "sum"))
        .assign(win_rate=lambda x: x["wins"] / x["played"])
        .sort_values(["played", "win_rate"], ascending=[False, False])
    )
    return out


def program_h2h_matrix(df: pd.DataFrame, program_id: str) -> pd.DataFrame:
    sub = _program_matches(df, program_id)
    rows = []
    for _, row in sub.iterrows():
        if row.get("program_a_id") == program_id:
            opp_id = row.get("program_b_id")
            opp_name = row.get("program_b_label") or row.get("team_b_name")
            won = row["winner_id"] == row["team_a_id"]
        elif row.get("program_b_id") == program_id:
            opp_id = row.get("program_a_id")
            opp_name = row.get("program_a_label") or row.get("team_a_name")
            won = row["winner_id"] == row["team_b_id"]
        else:
            continue
        rows.append({"opponent_id": opp_id, "opponent": opp_name, "win": int(won)})
    if not rows:
        return pd.DataFrame(columns=["opponent", "played", "wins", "win_rate"])
    tmp = pd.DataFrame(rows)
    return (
        tmp.groupby(["opponent_id", "opponent"], as_index=False)
        .agg(played=("win", "size"), wins=("win", "sum"))
        .assign(win_rate=lambda x: x["wins"] / x["played"])
        .sort_values(["played", "win_rate"], ascending=[False, False])
    )


def favorite_and_nemesis(h2h: pd.DataFrame, min_played: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    if h2h.empty:
        empty = pd.DataFrame(columns=h2h.columns)
        return empty, empty
    eligible = h2h.loc[h2h["played"] >= min_played]
    favorites = eligible.loc[eligible["win_rate"] >= 0.75].sort_values("win_rate", ascending=False)
    nemesis = eligible.loc[eligible["win_rate"] <= 0.25].sort_values("win_rate")
    return favorites, nemesis


def club_vs_club(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df.iterrows():
        if not row.get("club_a_id") or not row.get("club_b_id"):
            continue
        if row["club_a_id"] == row["club_b_id"]:
            continue
        winner_club = row["club_a_id"] if row["winner_id"] == row["team_a_id"] else row["club_b_id"]
        rows.append(
            {
                "club_a": row["club_a_name"],
                "club_b": row["club_b_name"],
                "winner_club": row["club_a_name"] if winner_club == row["club_a_id"] else row["club_b_name"],
                "age_group": row.get("age_group"),
            }
        )
    if not rows:
        return pd.DataFrame()
    tmp = pd.DataFrame(rows)
    # Normalize unordered pair
    tmp["pair"] = tmp.apply(lambda r: " vs ".join(sorted([r["club_a"], r["club_b"]])), axis=1)
    return (
        tmp.groupby("pair", as_index=False)
        .size()
        .rename(columns={"size": "matches"})
        .sort_values("matches", ascending=False)
    )


def geography_metrics(df: pd.DataFrame, team_id: str) -> dict:
    sub = _team_matches(df, team_id)
    if sub.empty:
        return {"in_region_win_rate": 0.0, "out_region_win_rate": 0.0, "in_n": 0, "out_n": 0}
    home_region = None
    # infer team region
    sample = sub.iloc[0]
    home_region = sample["region_a_id"] if sample["team_a_id"] == team_id else sample["region_b_id"]
    in_m = sub.loc[sub["event_region_id"] == home_region]
    out_m = sub.loc[sub["event_region_id"] != home_region]
    def wr(x):
        return float((x["winner_id"] == team_id).mean()) if len(x) else 0.0
    return {
        "in_region_win_rate": wr(in_m),
        "out_region_win_rate": wr(out_m),
        "in_n": int(len(in_m)),
        "out_n": int(len(out_m)),
        "home_region": home_region,
    }


def inter_region_matrix(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df.iterrows():
        ra, rb = row.get("region_a_name"), row.get("region_b_name")
        if not ra or not rb or ra == rb:
            continue
        winner_region = ra if row["winner_id"] == row["team_a_id"] else rb
        rows.append({"region_a": ra, "region_b": rb, "winner_region": winner_region})
    if not rows:
        return pd.DataFrame()
    tmp = pd.DataFrame(rows)
    tmp["pair"] = tmp.apply(lambda r: tuple(sorted([r["region_a"], r["region_b"]])), axis=1)
    return tmp


def seed_accuracy(rankings: pd.DataFrame) -> pd.DataFrame:
    if rankings.empty:
        return rankings
    out = rankings.dropna(subset=["initial_seed", "final_rank"]).copy()
    out["seed_error"] = out["final_rank"] - out["initial_seed"]
    out["upset"] = (out["final_rank"] < out["initial_seed"]).astype(int)  # finished better than seed
    return out


def upset_index(rankings: pd.DataFrame) -> float:
    scored = seed_accuracy(rankings)
    if scored.empty:
        return 0.0
    # Average absolute seed-finish displacement, normalized
    return float(scored["seed_error"].abs().mean())


def club_panorama(rankings: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    if rankings.empty:
        return pd.DataFrame()
    g = rankings.copy()
    g["gold"] = (g["final_rank"] == 1).astype(int)
    g["silver"] = (g["final_rank"] == 2).astype(int)
    summary = (
        g.groupby(["club_id", "club_name"], as_index=False)
        .agg(gold=("gold", "sum"), silver=("silver", "sum"), entries=("team_id", "size"))
    )
    # Open-tier ratio: share of finishes in top 4
    top4 = g.assign(top4=(g["final_rank"] <= 4).astype(int))
    open_ratio = top4.groupby("club_id", as_index=False).agg(open_tier_ratio=("top4", "mean"))
    summary = summary.merge(open_ratio, on="club_id", how="left")
    return summary.sort_values(["gold", "silver"], ascending=False)


def elo_trend(df: pd.DataFrame, team_id: str, k: float = 24.0, base: float = 1500.0) -> pd.DataFrame:
    sub = _team_matches(df, team_id).sort_values("match_date")
    if sub.empty:
        return pd.DataFrame(columns=["match_date", "elo", "event_name"])
    # Simple global elo store for opponents encountered
    ratings: dict[str, float] = {}
    history = []
    for _, row in sub.iterrows():
        a, b = row["team_a_id"], row["team_b_id"]
        ratings.setdefault(a, base)
        ratings.setdefault(b, base)
        ea = 1 / (1 + 10 ** ((ratings[b] - ratings[a]) / 400))
        eb = 1 - ea
        sa = 1.0 if row["winner_id"] == a else 0.0
        sb = 1.0 - sa
        ratings[a] += k * (sa - ea)
        ratings[b] += k * (sb - eb)
        history.append(
            {
                "match_date": row["match_date"],
                "elo": ratings[team_id],
                "event_name": row.get("event_name"),
                "won": int(row["winner_id"] == team_id),
            }
        )
    return pd.DataFrame(history)


def intra_club_derbies(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    mask = (
        df["club_a_id"].notna()
        & df["club_b_id"].notna()
        & (df["club_a_id"] == df["club_b_id"])
        & (df["team_a_id"] != df["team_b_id"])
    )
    return df.loc[mask, ["match_date", "club_a_name", "team_a_name", "team_b_name", "winner_id", "age_group", "event_name"]]
