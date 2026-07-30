"""
TM2Sign Volleyball Analytics Dashboard

Run:
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.analytics import (  # noqa: E402
    club_panorama,
    club_vs_club,
    favorite_and_nemesis,
    filter_matches,
    geography_metrics,
    inter_region_matrix,
    intra_club_derbies,
    load_matches_enriched,
    load_programs,
    load_rankings_enriched,
    load_teams,
    program_h2h_matrix,
    program_performance_metrics,
    program_season_trajectory,
    seed_accuracy,
    upset_index,
)
from src.analytics.coaches import (  # noqa: E402
    coach_career_summary,
    coach_search,
    coach_year_rollup,
    load_coach_career,
    load_coaches,
)
from src.analytics.players import (  # noqa: E402
    enrich_player_stints_with_team_perf,
    load_player_stints,
    load_players,
    player_search,
)
from src.db import init_database  # noqa: E402

st.set_page_config(
    page_title="NCVA Power League Analytics",
    page_icon="🏐",
    layout="wide",
)

# Atmosphere via CSS (avoid purple/default AI look)
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Archivo+Black&family=Source+Sans+3:wght@400;600;700&display=swap');
      .stApp {
        background:
          radial-gradient(1200px 600px at 10% -10%, #d9efe8 0%, transparent 55%),
          radial-gradient(900px 500px at 100% 0%, #f3e2c8 0%, transparent 50%),
          linear-gradient(180deg, #f7faf8 0%, #eef3f0 100%);
      }
      html, body, [class*="css"]  { font-family: 'Source Sans 3', sans-serif; }
      h1, h2, h3 { font-family: 'Archivo Black', sans-serif !important; letter-spacing: 0.02em; color: #14352c; }
      div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.55);
        border: 1px solid rgba(20,53,44,0.08);
        padding: 0.75rem 1rem;
        border-radius: 4px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def cached_matches() -> pd.DataFrame:
    init_database()
    return load_matches_enriched()


@st.cache_data(show_spinner=False)
def cached_rankings() -> pd.DataFrame:
    return load_rankings_enriched()


@st.cache_data(show_spinner=False)
def cached_teams() -> pd.DataFrame:
    return load_teams()


@st.cache_data(show_spinner=False)
def cached_programs() -> pd.DataFrame:
    return load_programs()


@st.cache_data(show_spinner=False)
def cached_players() -> pd.DataFrame:
    try:
        return load_players()
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def cached_coaches() -> pd.DataFrame:
    try:
        return load_coaches()
    except Exception:
        return pd.DataFrame()


def kpi_row(metrics: dict, keys: list[tuple[str, str, str]]):
    cols = st.columns(len(keys))
    for col, (label, key, fmt) in zip(cols, keys):
        val = metrics.get(key, 0)
        if fmt == "pct":
            col.metric(label, f"{val:.1%}")
        elif fmt == "ratio":
            col.metric(label, f"{val:.3f}")
        else:
            col.metric(label, f"{val}")


def main() -> None:
    st.title("NCVA Power League Analytics")
    st.caption("Full historical Power League — program lineage across ages/years")

    matches = cached_matches()
    rankings = cached_rankings()
    programs = cached_programs()
    teams = cached_teams()
    coaches_df = cached_coaches()
    players_df = cached_players()

    if matches.empty:
        st.warning(
            "Database is empty. Load NCVA Power League history first:\n\n"
            "`python scripts/load_power_league.py`"
        )
        return

    years = sorted(
        {
            int(y)
            for y in pd.to_datetime(matches["start_date"], errors="coerce").dt.year.dropna().tolist()
        }
    )
    event_opts = (
        matches[["event_id", "event_name", "start_date"]]
        .drop_duplicates("event_id")
        .sort_values("start_date")
    )
    age_nums = sorted([int(a) for a in matches["age_num"].dropna().unique().tolist()]) if "age_num" in matches.columns else []
    ages = ["All"] + [f"{a}U" for a in age_nums]
    stages = ["All", "Pool", "Bracket"]

    # Deduped program dropdown (Absolute Black once, not once per season/age)
    prog = programs.dropna(subset=["program_id", "program_label"]).copy()
    if prog.empty:
        st.error("Program identity missing. Run: `python scripts/backfill_team_identity.py`")
        return
    # Prefer programs that actually played matches
    played_programs = set(
        pd.concat([matches["program_a_id"], matches["program_b_id"]], ignore_index=True).dropna().unique()
    )
    prog = prog.loc[prog["program_id"].isin(played_programs)].sort_values("program_label")
    # Default Absolute Black if present (unless a deep-dive jump already set session state)
    default_prog = "G|ABSOL|1|NC"
    if "team_deep_dive_program" in st.session_state:
        default_idx = 0
        cur = st.session_state["team_deep_dive_program"]
        if cur in set(prog["program_id"]):
            default_idx = int(prog["program_id"].tolist().index(cur))
    elif default_prog in set(prog["program_id"]):
        default_idx = int(prog["program_id"].tolist().index(default_prog))
    else:
        # most matches
        pc = pd.concat([matches["program_a_id"], matches["program_b_id"]]).dropna().value_counts()
        default_prog = pc.index[0]
        default_idx = int(prog["program_id"].tolist().index(default_prog)) if default_prog in set(prog["program_id"]) else 0

    if st.session_state.pop("deep_dive_jump_notice", None):
        st.success(
            "Deep-Dive filters updated from Player View — open the **Team / Program Deep-Dive** tab."
        )

    st.sidebar.markdown("### NCVA Power League")
    st.sidebar.metric("Matches", f"{len(matches):,}")
    st.sidebar.metric("Programs", f"{len(prog):,}")
    st.sidebar.metric("Players", f"{len(players_df):,}")
    st.sidebar.metric("Coaches", f"{len(coaches_df):,}")
    st.sidebar.metric("Seasons", f"{len(event_opts):,}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "Team / Program Deep-Dive",
            "Club Panorama",
            "Region & Geography",
            "Event & Seed Dynamics",
            "Player View",
            "Coach View",
        ]
    )

    with tab1:
        c0, c1, c2, c3, c4 = st.columns([2.4, 2.2, 1, 1, 1])
        season_labels = {
            str(r.event_id): f"{r.start_date} — {r.event_name}"
            for r in event_opts.itertuples(index=False)
        }
        season = c0.selectbox(
            "Season",
            options=["All"] + [str(x) for x in event_opts["event_id"].tolist()],
            format_func=lambda x: "All seasons" if x == "All" else season_labels.get(x, x),
            key="team_deep_dive_season",
        )
        prog_labels = {
            pid: f"{label}" + (f" ({g})" if g else "")
            for pid, label, g in zip(prog["program_id"], prog["program_label"], prog["gender_code"])
        }
        program_id = c1.selectbox(
            "Program (tracks across ages/years)",
            options=prog["program_id"].tolist(),
            index=default_idx,
            key="team_deep_dive_program",
            format_func=lambda pid: prog_labels.get(pid, pid),
        )
        year = c2.selectbox("Year", options=["All"] + years, index=0, key="team_deep_dive_year")
        age = c3.selectbox("Age", options=ages, key="team_deep_dive_age")
        stage = c4.selectbox("Stage", options=stages, key="team_deep_dive_stage")

        year_val = None if year == "All" else int(year)
        filtered = filter_matches(
            matches,
            year=year_val,
            age_group=age,
            stage=stage,
            event_id=None if season == "All" else season,
            program_id=program_id,
        )
        perf = program_performance_metrics(filtered, program_id)
        traj = program_season_trajectory(matches, rankings, program_id)

        st.subheader("Career Performance (selected filters)")
        kpi_row(
            perf,
            [
                ("Matches", "matches", "int"),
                ("Win Rate", "win_rate", "pct"),
                ("Set Win Rate", "set_win_rate", "pct"),
                ("Point Diff Ratio", "point_diff_ratio", "ratio"),
            ],
        )

        st.subheader("Age-up Trajectory")
        st.caption("Same program lineage across seasons — e.g. Absolute Black 17U → 18U")
        if traj.empty:
            st.info("No trajectory rows for this program.")
        else:
            show = traj.copy()
            st.dataframe(
                show[
                    [
                        c
                        for c in [
                            "season_year",
                            "age_group",
                            "team_name",
                            "event_name",
                            "matches",
                            "wins",
                            "win_rate",
                            "initial_seed",
                            "final_rank",
                        ]
                        if c in show.columns
                    ]
                ],
                width="stretch",
                hide_index=True,
            )
            if show["season_year"].notna().any() and show["win_rate"].notna().any():
                fig = px.line(
                    show.dropna(subset=["season_year"]),
                    x="season_year",
                    y="win_rate",
                    color="age_group",
                    markers=True,
                    hover_data=["team_name", "event_name", "matches"],
                    title="Win rate by season / age",
                )
                fig.update_layout(height=360, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig, width="stretch")
            if "final_rank" in show.columns and show["final_rank"].notna().any():
                fig = px.line(
                    show.dropna(subset=["season_year", "final_rank"]),
                    x="season_year",
                    y="final_rank",
                    color="age_group",
                    markers=True,
                    hover_data=["team_name", "event_name", "initial_seed"],
                    title="Finish by season (lower is better)",
                )
                fig.update_yaxes(autorange="reversed")
                fig.update_layout(height=340, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig, width="stretch")

        h2h = program_h2h_matrix(filtered, program_id)
        fav, nem = favorite_and_nemesis(h2h)
        left, right = st.columns(2)
        with left:
            st.markdown("**Program Head-to-Head**")
            st.dataframe(h2h.drop(columns=["opponent_id"], errors="ignore").head(40), width="stretch", hide_index=True)
            if not h2h.empty:
                fig = px.bar(
                    h2h.head(25),
                    x="opponent",
                    y="win_rate",
                    color="played",
                    title="H2H Win Rate by Opponent Program",
                    color_continuous_scale=["#8fbfb0", "#1f6f5b"],
                )
                fig.update_layout(xaxis_tickangle=-35, height=360, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig, width="stretch")
        with right:
            st.markdown("**Favorite Targets** (≥75% WR)")
            st.dataframe(fav.drop(columns=["opponent_id"], errors="ignore"), width="stretch", hide_index=True)
            st.markdown("**Nemesis Programs** (≤25% WR)")
            st.dataframe(nem.drop(columns=["opponent_id"], errors="ignore"), width="stretch", hide_index=True)

    with tab2:
        st.subheader("Cross-age medals & open-tier ratio")
        panorama = club_panorama(rankings, matches)
        if panorama.empty:
            st.info("No ranking rows available.")
        else:
            st.dataframe(panorama, width="stretch", hide_index=True)
            fig = px.bar(
                panorama,
                x="club_name",
                y=["gold", "silver"],
                barmode="group",
                title="Gold / Silver by Club",
                color_discrete_sequence=["#c9a227", "#8a8f98"],
            )
            fig.update_layout(height=380, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")

            fig2 = px.scatter(
                panorama,
                x="entries",
                y="open_tier_ratio",
                size="gold",
                hover_name="club_name",
                title="Open-tier ratio vs entries",
                color="club_name",
            )
            fig2.update_layout(height=360, showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, width="stretch")

        st.subheader("Multi-year cohort trajectory")
        if not rankings.empty and rankings["cohort_year"].notna().any():
            cohort = (
                rankings.dropna(subset=["cohort_year", "final_rank"])
                .groupby(["club_name", "cohort_year"], as_index=False)
                .agg(avg_finish=("final_rank", "mean"), gold=("final_rank", lambda s: int((s == 1).sum())))
            )
            fig = px.line(
                cohort,
                x="cohort_year",
                y="avg_finish",
                color="club_name",
                markers=True,
                title="Average finish by cohort year",
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=380, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")

        st.subheader("Intra-club derbies")
        derbies = intra_club_derbies(matches)
        st.dataframe(derbies, width="stretch", hide_index=True)

        st.subheader("Club vs Club multi-age rivalry volume")
        cvc = club_vs_club(matches)
        st.dataframe(cvc.head(25), width="stretch", hide_index=True)

    with tab3:
        st.subheader("Inter-region dominance")
        ir = inter_region_matrix(matches)
        if ir.empty:
            st.info("Not enough cross-region matches.")
        else:
            # Win counts region vs region
            records = []
            for _, row in ir.iterrows():
                a, b = sorted([row["region_a"], row["region_b"]])
                records.append({"winner": row["winner_region"], "loser": b if row["winner_region"] == a else a})
            tmp = pd.DataFrame(records)
            pivot = pd.crosstab(tmp["winner"], tmp["loser"])
            st.dataframe(pivot, width="stretch")
            if pivot.size:
                fig = px.imshow(
                    pivot,
                    text_auto=True,
                    color_continuous_scale=["#f7faf8", "#1f6f5b"],
                    title="Wins by region (rows beat columns)",
                )
                fig.update_layout(height=420, paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig, width="stretch")

        st.subheader("Home vs Away travel win rates (by team region vs event region)")
        # Deduped by program_label to avoid season duplicate rows in the table
        sample_teams = (
            teams.loc[teams["team_id"].isin(pd.concat([matches["team_a_id"], matches["team_b_id"]]).dropna().unique())]
            .sort_values(["program_label", "age_num"])
            .drop_duplicates("program_id")
        )
        rows = []
        for tid, tname, reg in sample_teams[["team_id", "program_label", "region_id"]].itertuples(index=False):
            g = geography_metrics(matches, tid)
            rows.append(
                {
                    "program": tname or tid,
                    "in_region_wr": g["in_region_win_rate"],
                    "out_region_wr": g["out_region_win_rate"],
                    "in_n": g["in_n"],
                    "out_n": g["out_n"],
                }
            )
        geo_df = pd.DataFrame(rows).sort_values("out_region_wr", ascending=False)
        st.dataframe(geo_df, width="stretch", hide_index=True)
        fig = px.scatter(
            geo_df,
            x="in_region_wr",
            y="out_region_wr",
            size=(geo_df["in_n"] + geo_df["out_n"]).clip(lower=1),
            hover_name="program",
            title="In-region vs Out-of-region win rate",
            color_discrete_sequence=["#c46b2b"],
        )
        fig.add_shape(type="line", x0=0, x1=1, y0=0, y1=1, line=dict(dash="dash", color="#888"))
        fig.update_layout(height=420, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")

    with tab4:
        st.subheader("Upset index & seed-to-finish correlation")
        scored = seed_accuracy(rankings)
        ui = upset_index(rankings)
        c1, c2, c3 = st.columns(3)
        c1.metric("Upset Index (avg |seed−finish|)", f"{ui:.2f}")
        if not scored.empty:
            corr = scored["initial_seed"].corr(scored["final_rank"])
            c2.metric("Seed↔Finish correlation", f"{corr:.3f}")
            c3.metric("Better-than-seed finishes", f"{scored['upset'].mean():.1%}")

            fig = px.scatter(
                scored,
                x="initial_seed",
                y="final_rank",
                color="age_group",
                hover_data=["team_name", "event_name", "club_name"],
                title="Seed vs Final Finish",
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_xaxes(autorange="reversed")
            fig.update_layout(height=460, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")

            by_event = (
                scored.groupby("event_name", as_index=False)
                .agg(upset_index=("seed_error", lambda s: s.abs().mean()), entries=("team_id", "size"))
                .sort_values("upset_index", ascending=False)
            )
            fig2 = px.bar(
                by_event,
                x="event_name",
                y="upset_index",
                title="Upset index by event",
                color="upset_index",
                color_continuous_scale=["#f3e2c8", "#c46b2b"],
            )
            fig2.update_layout(height=360, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, width="stretch")
        else:
            st.info("No seed/finish ranking rows available.")

    with tab5:
        st.subheader("Player View")
        player_n = len(players_df)
        c1, c2 = st.columns(2)
        c1.metric("Players", f"{player_n:,}")
        c2.metric("Source", "TM2 roster API")

        if player_n == 0:
            st.warning(
                "No roster rows loaded yet. Run:\n\n"
                "`python scripts/backfill_rosters.py`\n\n"
                "Endpoint: `/api/public/scheduler-teams/{id}/roster`"
            )
        else:
            q = st.text_input("Search player", placeholder="e.g. Carr, Paige Din", key="player_search_q")
            hits = player_search(q) if q.strip() else players_df.head(100)
            if hits.empty:
                st.info("No players matched.")
            else:
                labels = {
                    r.player_id: f"{r.full_name} ({int(r.first_year or 0)}–{int(r.last_year or 0)}, {int(r.seasons or 0)} seasons)"
                    for r in hits.itertuples(index=False)
                }
                pid = st.selectbox(
                    "Player",
                    options=list(labels.keys()),
                    format_func=lambda x: labels.get(x, x),
                    key="player_view_player",
                )
                stints = enrich_player_stints_with_team_perf(load_player_stints(pid), matches)
                st.markdown("**Season / team history**")
                show = stints[
                    [
                        c
                        for c in [
                            "season_year",
                            "age_group",
                            "team_name",
                            "program_label",
                            "club_name",
                            "uniform_number",
                            "matches",
                            "wins",
                            "win_rate",
                            "initial_seed",
                            "final_rank",
                            "bracket_finish",
                            "event_name",
                        ]
                        if c in stints.columns
                    ]
                ].copy()
                if "win_rate" in show.columns:
                    show["win_rate"] = show["win_rate"].map(
                        lambda x: round(float(x), 3) if pd.notna(x) else x
                    )
                st.dataframe(show, width="stretch", hide_index=True)

                st.markdown("**Open team season in Deep-Dive**")
                for i, row in stints.iterrows():
                    season_year = row.get("season_year")
                    team_name = row.get("team_name") or row.get("team_id")
                    age_group = row.get("age_group") or (
                        f"{int(row['age_num'])}U" if pd.notna(row.get("age_num")) else "All"
                    )
                    seed = row.get("initial_seed")
                    finish = row.get("final_rank")
                    wr = row.get("win_rate")
                    rank_bits = []
                    if pd.notna(seed):
                        rank_bits.append(f"seed {int(seed)}")
                    if pd.notna(finish):
                        rank_bits.append(f"finish {int(finish)}")
                    if pd.notna(wr):
                        rank_bits.append(f"WR {float(wr):.0%}")
                    rank_txt = " · ".join(rank_bits) if rank_bits else "no rank yet"
                    label = f"{int(season_year) if pd.notna(season_year) else '?'} · {team_name} · {rank_txt}"
                    prog_id = row.get("program_id") or row.get("team_program_id")
                    event_id = str(row["event_id"]) if pd.notna(row.get("event_id")) else "All"
                    cols = st.columns([4, 1])
                    cols[0].write(label)
                    if prog_id and prog_id in set(prog["program_id"]):
                        if cols[1].button("Deep-Dive", key=f"player_dd_{pid}_{i}_{row.get('team_id')}"):
                            st.session_state["team_deep_dive_program"] = prog_id
                            st.session_state["team_deep_dive_season"] = event_id
                            if age_group in ages:
                                st.session_state["team_deep_dive_age"] = age_group
                            else:
                                st.session_state["team_deep_dive_age"] = "All"
                            st.session_state["team_deep_dive_year"] = "All"
                            st.session_state["team_deep_dive_stage"] = "All"
                            st.session_state["deep_dive_jump_notice"] = True
                            st.rerun()
                    else:
                        cols[1].caption("n/a")

                if not show.empty and show["season_year"].notna().any():
                    fig = px.scatter(
                        show.dropna(subset=["season_year"]),
                        x="season_year",
                        y="final_rank" if show["final_rank"].notna().any() else "age_group",
                        size="matches" if "matches" in show.columns else None,
                        hover_data=[
                            c
                            for c in ["team_name", "program_label", "club_name", "initial_seed", "win_rate", "age_group"]
                            if c in show.columns
                        ],
                        title="Team finish by season (lower is better)"
                        if show["final_rank"].notna().any()
                        else "Where they played by year / age",
                        color="program_label",
                    )
                    if show["final_rank"].notna().any():
                        fig.update_yaxes(autorange="reversed", title="Final rank")
                    fig.update_layout(height=360, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig, width="stretch")
                st.caption(
                    "Ranks/seeds are the player's team results that season. "
                    "Deep-Dive jumps set Program + Season (+ Age) filters."
                )

    with tab6:
        st.subheader("Coach View")
        coach_n = len(coaches_df)
        c1, c2, c3 = st.columns(3)
        c1.metric("Coaches / Staff", f"{coach_n:,}")
        if not coaches_df.empty:
            c2.metric("Head-coach stints", f"{int(coaches_df['head_coach_stints'].sum()):,}")
            c3.metric("Multi-season careers", f"{int((coaches_df['seasons'] >= 2).sum()):,}")

        if coach_n == 0:
            st.warning("No coach rows loaded. Run `python scripts/backfill_rosters.py`")
        else:
            q = st.text_input("Search coach", placeholder="e.g. Gill, Braga, Mackin", key="coach_search_q")
            hits = coach_search(q) if q.strip() else coaches_df.head(100)
            if hits.empty:
                st.info("No coaches matched.")
            else:
                labels = {
                    r.staff_id: (
                        f"{r.full_name} ({int(r.first_year or 0)}–{int(r.last_year or 0)}, "
                        f"{int(r.seasons or 0)} seasons, {int(getattr(r, 'head_coach_stints', 0) or 0)} HC)"
                    )
                    for r in hits.itertuples(index=False)
                }
                coach_opts = list(labels.keys())
                default_idx = next(
                    (i for i, cid in enumerate(coach_opts) if "gill|aaron" in str(cid).lower()),
                    0,
                )
                sid = st.selectbox(
                    "Coach",
                    options=coach_opts,
                    index=min(default_idx, len(coach_opts) - 1),
                    format_func=lambda x: labels.get(x, x),
                    key="coach_view_staff",
                )

                career = load_coach_career(sid, matches=matches)
                summary = coach_career_summary(career)
                k1, k2, k3, k4, k5, k6 = st.columns(6)
                k1.metric("Seasons", summary["seasons"])
                k2.metric("Clubs", summary["clubs"])
                k3.metric("Teams", summary["teams"])
                k4.metric("Career WR", f"{summary['career_win_rate']:.1%}")
                k5.metric("Gold finishes", summary["gold"])
                k6.metric(
                    "Avg finish",
                    f"{summary['avg_finish']:.1f}" if summary["avg_finish"] is not None else "—",
                )

                st.markdown("**Career timeline** (year · club · role · team · results)")
                show_cols = [
                    c
                    for c in [
                        "season_year",
                        "role",
                        "club_name",
                        "team_name",
                        "program_label",
                        "age_group",
                        "matches",
                        "wins",
                        "win_rate",
                        "initial_seed",
                        "final_rank",
                        "event_name",
                    ]
                    if c in career.columns
                ]
                show = career[show_cols].copy()
                if "win_rate" in show.columns:
                    show["win_rate"] = show["win_rate"].map(lambda x: round(float(x), 3) if pd.notna(x) else x)
                st.dataframe(show, width="stretch", hide_index=True)

                year_roll = coach_year_rollup(career)
                left, right = st.columns(2)
                with left:
                    if not year_roll.empty and year_roll["win_rate"].notna().any():
                        fig = px.line(
                            year_roll.dropna(subset=["win_rate"]),
                            x="season_year",
                            y="win_rate",
                            markers=True,
                            title="Team win rate by season (coach's teams)",
                        )
                        fig.update_traces(line_color="#1f6f5b")
                        fig.update_layout(height=340, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig, width="stretch")
                    if not career.empty:
                        role_club = (
                            career.groupby(["season_year", "club_name", "role"], as_index=False)
                            .size()
                            .rename(columns={"size": "stints"})
                        )
                        fig = px.bar(
                            role_club,
                            x="season_year",
                            y="stints",
                            color="role",
                            hover_data=["club_name"],
                            title="Roles by year",
                            barmode="stack",
                            color_discrete_sequence=["#c46b2b", "#1f6f5b", "#8a8f98"],
                        )
                        fig.update_layout(height=340, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig, width="stretch")
                with right:
                    if not year_roll.empty and year_roll["avg_finish"].notna().any():
                        fig = px.line(
                            year_roll.dropna(subset=["avg_finish"]),
                            x="season_year",
                            y="avg_finish",
                            markers=True,
                            title="Average finish by season (lower is better)",
                        )
                        fig.update_yaxes(autorange="reversed")
                        fig.update_traces(line_color="#c46b2b")
                        fig.update_layout(height=340, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig, width="stretch")
                    club_summary = (
                        career.drop_duplicates(["event_id", "team_id"])
                        .groupby("club_name", as_index=False)
                        .agg(
                            seasons=("season_year", "nunique"),
                            matches=("matches", "sum"),
                            wins=("wins", "sum"),
                            gold=("final_rank", lambda s: int((s == 1).sum())),
                        )
                        .assign(win_rate=lambda x: x["wins"] / x["matches"].replace(0, pd.NA))
                        .sort_values(["seasons", "matches"], ascending=False)
                    )
                    st.markdown("**By club**")
                    st.dataframe(club_summary, width="stretch", hide_index=True)

                st.caption(
                    "Performance is the coached team's Power League results for that season "
                    "(matches/WR from schedule; seed/finish from rankings)."
                )


if __name__ == "__main__":
    main()
