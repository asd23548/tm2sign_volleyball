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
    coach_browse_clubs,
    coach_browse_coaches,
    coach_browse_seasons,
    coach_browse_teams,
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
    player_browse_clubs,
    player_browse_players,
    player_browse_seasons,
    player_browse_teams,
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
def cached_matches(_cache_ver: int = 2) -> pd.DataFrame:
    init_database()
    return load_matches_enriched()


@st.cache_data(show_spinner=False)
def cached_rankings() -> pd.DataFrame:
    return load_rankings_enriched()


@st.cache_data(show_spinner=False)
def cached_teams(_cache_ver: int = 2) -> pd.DataFrame:
    return load_teams()


@st.cache_data(show_spinner=False)
def cached_programs() -> pd.DataFrame:
    return load_programs()


@st.cache_data(ttl=30, show_spinner=False)
def cached_players(_cache_ver: int = 4) -> pd.DataFrame:
    return load_players()


@st.cache_data(ttl=30, show_spinner=False)
def cached_coaches(_cache_ver: int = 4) -> pd.DataFrame:
    return load_coaches()


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


def filter_by_gender(df: pd.DataFrame, gender: str) -> pd.DataFrame:
    if df is None or df.empty or gender == "All":
        return df
    if "gender" in df.columns:
        # Players/staff store codes G/B; events/matches store Girls/Boys
        sample = set(df["gender"].dropna().astype(str).unique())
        if sample & {"G", "B"} and not (sample & {"Girls", "Boys"}):
            code = "G" if gender == "Girls" else "B"
            return df.loc[df["gender"] == code].copy()
        return df.loc[df["gender"] == gender].copy()
    if "gender_code" in df.columns:
        code = "G" if gender == "Girls" else "B"
        return df.loc[df["gender_code"] == code].copy()
    if "event_gender" in df.columns:
        return df.loc[df["event_gender"] == gender].copy()
    return df


def _notice(tab_name: str) -> None:
    st.session_state["cross_link_notice"] = tab_name


def jump_to_team_deep_dive(
    program_id: str | None,
    *,
    event_id: str | None = None,
    age_group: str | None = None,
    ages: list[str] | None = None,
) -> None:
    """Queue a Deep-Dive filter jump for the next run (before widgets instantiate)."""
    if not program_id:
        return
    st.session_state["_pending_deep_dive"] = {
        "program": program_id,
        "season": str(event_id) if event_id else "All",
        "age": age_group if (ages and age_group in ages) else "All",
        "year": "All",
        "stage": "All",
    }
    _notice("Team / Program Deep-Dive")
    st.rerun()


def jump_to_club(club_id: str | None) -> None:
    if not club_id:
        return
    st.session_state["_pending_club"] = {"club_id": str(club_id)}
    _notice("Club Panorama")
    st.rerun()


def jump_to_player(
    player_id: str | None = None,
    *,
    club_id: str | None = None,
    event_id: str | None = None,
    team_id: str | None = None,
    query: str = "",
) -> None:
    """Jump to Player View — either a specific player or browse club→season→team."""
    if player_id:
        st.session_state["_pending_player"] = {
            "mode": "Search",
            "player_id": player_id,
            "query": query or "",
        }
    elif club_id and event_id and team_id:
        st.session_state["_pending_player"] = {
            "mode": "Browse club → season → team",
            "club_id": str(club_id),
            "event_id": str(event_id),
            "team_id": str(team_id),
        }
    elif club_id:
        st.session_state["_pending_player"] = {
            "mode": "Browse club → season → team",
            "club_id": str(club_id),
        }
    else:
        return
    _notice("Player View")
    st.rerun()


def jump_to_coach(
    staff_id: str | None = None,
    *,
    club_id: str | None = None,
    event_id: str | None = None,
    team_id: str | None = None,
    query: str = "",
) -> None:
    """Jump to Coach View — either a specific coach or browse club→season→team."""
    if staff_id:
        st.session_state["_pending_coach"] = {
            "mode": "Search",
            "staff_id": staff_id,
            "query": query or "",
        }
    elif club_id and event_id and team_id:
        st.session_state["_pending_coach"] = {
            "mode": "Browse club → season → team",
            "club_id": str(club_id),
            "event_id": str(event_id),
            "team_id": str(team_id),
        }
    elif club_id:
        st.session_state["_pending_coach"] = {
            "mode": "Browse club → season → team",
            "club_id": str(club_id),
        }
    else:
        return
    _notice("Coach View")
    st.rerun()


def apply_pending_jumps() -> None:
    """Apply queued cross-view jumps before any target widgets are created."""
    pending = st.session_state.pop("_pending_deep_dive", None)
    if pending:
        st.session_state["team_deep_dive_program"] = pending["program"]
        st.session_state["team_deep_dive_season"] = pending.get("season", "All")
        st.session_state["team_deep_dive_age"] = pending.get("age", "All")
        st.session_state["team_deep_dive_year"] = pending.get("year", "All")
        st.session_state["team_deep_dive_stage"] = pending.get("stage", "All")

    pending = st.session_state.pop("_pending_club", None)
    if pending and pending.get("club_id"):
        st.session_state["club_panorama_focus"] = pending["club_id"]

    pending = st.session_state.pop("_pending_player", None)
    if pending:
        st.session_state["player_pick_mode"] = pending.get("mode", "Search")
        if pending.get("player_id"):
            st.session_state["player_view_player"] = pending["player_id"]
            st.session_state["player_search_q"] = pending.get("query", "")
        if pending.get("club_id"):
            st.session_state["player_browse_club"] = pending["club_id"]
        if pending.get("event_id"):
            st.session_state["player_browse_season"] = str(pending["event_id"])
        if pending.get("team_id"):
            st.session_state["player_browse_team"] = pending["team_id"]

    pending = st.session_state.pop("_pending_coach", None)
    if pending:
        st.session_state["coach_pick_mode"] = pending.get("mode", "Search")
        if pending.get("staff_id"):
            st.session_state["coach_view_staff"] = pending["staff_id"]
            st.session_state["coach_search_q"] = pending.get("query", "")
        if pending.get("club_id"):
            st.session_state["coach_browse_club"] = pending["club_id"]
        if pending.get("event_id"):
            st.session_state["coach_browse_season"] = str(pending["event_id"])
        if pending.get("team_id"):
            st.session_state["coach_browse_team"] = pending["team_id"]


def resolve_row_context(
    row: pd.Series | dict,
    *,
    teams_df: pd.DataFrame | None = None,
    programs_df: pd.DataFrame | None = None,
    default_program_id: str | None = None,
) -> dict:
    """Fill club_id / team_id / program_id from row + lookup tables when missing."""
    get = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
    program_id = get("program_id") or get("team_program_id") or default_program_id
    event_id = str(get("event_id")) if pd.notna(get("event_id")) else None
    age_group = get("age_group")
    if not age_group and pd.notna(get("age_num")):
        age_group = f"{int(get('age_num'))}U"
    club_id = get("club_id")
    team_id = get("team_id")

    if (not club_id) and program_id is not None and programs_df is not None and not programs_df.empty:
        hit = programs_df.loc[programs_df["program_id"] == program_id]
        if not hit.empty and pd.notna(hit.iloc[0].get("club_id")):
            club_id = hit.iloc[0]["club_id"]

    if teams_df is not None and not teams_df.empty:
        if (not team_id) and program_id and event_id:
            hit = teams_df.loc[
                (teams_df["program_id"] == program_id)
                & (teams_df["event_id"].astype(str) == str(event_id))
            ]
            if age_group and "age_group" in hit.columns:
                aged = hit.loc[hit["age_group"] == age_group]
                if not aged.empty:
                    hit = aged
            if not hit.empty:
                team_id = hit.iloc[0]["team_id"]
                if not club_id and pd.notna(hit.iloc[0].get("club_id")):
                    club_id = hit.iloc[0]["club_id"]
        elif team_id and not club_id:
            hit = teams_df.loc[teams_df["team_id"] == team_id]
            if not hit.empty and pd.notna(hit.iloc[0].get("club_id")):
                club_id = hit.iloc[0]["club_id"]
            if not program_id and not hit.empty:
                program_id = hit.iloc[0].get("program_id")

    return {
        "program_id": program_id,
        "event_id": event_id,
        "age_group": age_group,
        "club_id": club_id if pd.notna(club_id) else None,
        "team_id": team_id if pd.notna(team_id) else None,
    }


def render_cross_links(    rows: pd.DataFrame,
    *,
    key_prefix: str,
    program_ids: set[str],
    ages: list[str],
    teams_df: pd.DataFrame | None = None,
    programs_df: pd.DataFrame | None = None,
    default_program_id: str | None = None,
    show_players: bool = True,
    show_coaches: bool = True,
    show_club: bool = True,
) -> None:
    """Render Team / Club / Players / Coaches jump buttons per season row."""
    if rows is None or rows.empty:
        return
    st.markdown("**Cross-links**")
    for i, row in rows.iterrows():
        ctx = resolve_row_context(
            row,
            teams_df=teams_df,
            programs_df=programs_df,
            default_program_id=default_program_id,
        )
        team_name = row.get("team_name") or ctx["team_id"] or "Team"
        season_year = row.get("season_year")
        bits = [str(int(season_year)) if pd.notna(season_year) else "?", str(team_name)]
        if ctx["age_group"]:
            bits.append(str(ctx["age_group"]))
        if pd.notna(row.get("final_rank")):
            bits.append(f"finish {int(row['final_rank'])}")
        if pd.notna(row.get("win_rate")):
            bits.append(f"WR {float(row['win_rate']):.0%}")
        label = " · ".join(bits)

        n_btns = 1 + int(show_club) + int(show_players) + int(show_coaches)
        cols = st.columns([3.6] + [1] * n_btns)
        cols[0].write(label)
        bi = 1
        can_team = bool(ctx["program_id"]) and ctx["program_id"] in program_ids
        if can_team and cols[bi].button(
            "Team",
            key=f"{key_prefix}_t_{i}_{ctx['team_id']}_{ctx['event_id']}",
            help="Open Team / Program Deep-Dive",
        ):
            jump_to_team_deep_dive(
                ctx["program_id"],
                event_id=ctx["event_id"],
                age_group=ctx["age_group"],
                ages=ages,
            )
        elif not can_team:
            cols[bi].caption("—")
        bi += 1
        if show_club:
            if ctx["club_id"] and cols[bi].button(
                "Club",
                key=f"{key_prefix}_c_{i}_{ctx['club_id']}_{ctx['event_id']}",
                help="Open Club Panorama",
            ):
                jump_to_club(ctx["club_id"])
            else:
                cols[bi].caption("—")
            bi += 1
        if show_players:
            can_p = bool(ctx["club_id"] and ctx["event_id"] and ctx["team_id"])
            if can_p and cols[bi].button(
                "Players",
                key=f"{key_prefix}_p_{i}_{ctx['team_id']}_{ctx['event_id']}",
                help="Browse roster in Player View",
            ):
                jump_to_player(
                    club_id=ctx["club_id"],
                    event_id=ctx["event_id"],
                    team_id=ctx["team_id"],
                )
            else:
                cols[bi].caption("—")
            bi += 1
        if show_coaches:
            can_c = bool(ctx["club_id"] and ctx["event_id"] and ctx["team_id"])
            if can_c and cols[bi].button(
                "Coaches",
                key=f"{key_prefix}_s_{i}_{ctx['team_id']}_{ctx['event_id']}",
                help="Browse staff in Coach View",
            ):
                jump_to_coach(
                    club_id=ctx["club_id"],
                    event_id=ctx["event_id"],
                    team_id=ctx["team_id"],
                )
            else:
                cols[bi].caption("—")




def main() -> None:
    st.title("NCVA Power League Analytics")
    st.caption("Full historical Power League — program lineage across ages/years")

    # Apply queued cross-view jumps before target widgets exist
    apply_pending_jumps()

    matches_all = cached_matches()
    rankings_all = cached_rankings()
    programs_all = cached_programs()
    teams_all = cached_teams()
    coaches_df = cached_coaches()
    players_df = cached_players()

    if matches_all.empty:
        st.warning(
            "Database is empty. Load NCVA Power League history first:\n\n"
            "`python scripts/ncva_crawler.py --reset`"
        )
        return

    st.sidebar.markdown("### NCVA Power League")
    gender = st.sidebar.radio(
        "Gender",
        options=["Girls", "Boys", "All"],
        index=0,
        horizontal=True,
        key="global_gender",
        help="Applies to every tab and metric",
    )

    matches = filter_by_gender(matches_all, gender)
    rankings = filter_by_gender(rankings_all, gender)
    teams = filter_by_gender(teams_all, gender)
    players_df = filter_by_gender(players_df, gender)
    coaches_df = filter_by_gender(coaches_df, gender)
    if gender == "All":
        programs = programs_all
    else:
        code = "G" if gender == "Girls" else "B"
        programs = programs_all.loc[programs_all["gender_code"] == code].copy()

    years = sorted(
        {
            int(y)
            for y in pd.to_datetime(matches["start_date"], errors="coerce").dt.year.dropna().tolist()
        }
    )
    event_opts = (
        matches[["event_id", "event_name", "start_date"]]
        .copy()
    )
    # Defensive: duplicate join labels can make a column selection return a DataFrame
    if isinstance(event_opts["event_id"], pd.DataFrame):
        event_opts["event_id"] = event_opts["event_id"].iloc[:, 0]
    if isinstance(event_opts["event_name"], pd.DataFrame):
        event_opts["event_name"] = event_opts["event_name"].iloc[:, 0]
    if isinstance(event_opts["start_date"], pd.DataFrame):
        event_opts["start_date"] = event_opts["start_date"].iloc[:, 0]
    event_opts = event_opts.drop_duplicates("event_id").sort_values("start_date")
    age_nums = (
        sorted([int(a) for a in matches["age_num"].dropna().unique().tolist()])
        if "age_num" in matches.columns
        else []
    )
    ages = ["All"] + [f"{a}U" for a in age_nums]
    stages = ["All", "Pool", "Bracket"]

    # Deduped program dropdown (Absolute Black once, not once per season/age)
    prog = programs.dropna(subset=["program_id", "program_label"]).copy()
    if prog.empty:
        st.error("No programs for this gender filter. Try All / the other gender.")
        return
    # Prefer programs that actually played matches
    played_programs = set(
        pd.concat([matches["program_a_id"], matches["program_b_id"]], ignore_index=True)
        .dropna()
        .unique()
    )
    prog = prog.loc[prog["program_id"].isin(played_programs)].sort_values("program_label")
    if prog.empty:
        st.error("No played programs for this gender filter.")
        return

    program_id_set = set(prog["program_id"])

    # Default Absolute Black / Absolute White depending on gender
    default_prog = "G|ABSOL|1|NC" if gender != "Boys" else "B|ABSOL|1|NC"
    if "team_deep_dive_program" in st.session_state:
        default_idx = 0
        cur = st.session_state["team_deep_dive_program"]
        if cur in program_id_set:
            default_idx = int(prog["program_id"].tolist().index(cur))
        elif gender != "All":
            # Jump landed on other gender — reset to a sensible default
            st.session_state.pop("team_deep_dive_program", None)
            default_idx = (
                int(prog["program_id"].tolist().index(default_prog))
                if default_prog in program_id_set
                else 0
            )
    elif default_prog in program_id_set:
        default_idx = int(prog["program_id"].tolist().index(default_prog))
    else:
        pc = pd.concat([matches["program_a_id"], matches["program_b_id"]]).dropna().value_counts()
        top = pc.index[0] if len(pc) else prog["program_id"].iloc[0]
        default_idx = int(prog["program_id"].tolist().index(top)) if top in program_id_set else 0

    notice = st.session_state.pop("cross_link_notice", None)
    if notice is None and st.session_state.pop("deep_dive_jump_notice", None):
        notice = "Team / Program Deep-Dive"
    if notice:
        st.success(f"Filters updated — open the **{notice}** tab.")

    st.sidebar.metric("Matches", f"{len(matches):,}")
    st.sidebar.metric("Programs", f"{len(prog):,}")
    st.sidebar.metric("Players", f"{len(players_df):,}")
    st.sidebar.metric("Coaches", f"{len(coaches_df):,}")
    st.sidebar.metric("Seasons", f"{len(event_opts):,}")
    st.sidebar.caption(f"Filter: **{gender}**")

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
        if not traj.empty:
            traj = traj.copy()
            traj["program_id"] = program_id

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
            render_cross_links(
                traj,
                key_prefix="traj_dd",
                program_ids=program_id_set,
                ages=ages,
                teams_df=teams,
                programs_df=programs,
                default_program_id=program_id,
                show_players=False,
                show_coaches=False,
            )

        # --- Team roster (players + coaches) for the focused team ---
        st.subheader("Team roster")
        st.caption("Players and coaches for the selected program / season / age — click through to their views.")
        team_pool = teams.loc[teams["program_id"] == program_id].copy() if "program_id" in teams.columns else pd.DataFrame()
        if not team_pool.empty:
            if season != "All" and "event_id" in team_pool.columns:
                team_pool = team_pool.loc[team_pool["event_id"].astype(str) == str(season)]
            if age != "All" and "age_group" in team_pool.columns:
                team_pool = team_pool.loc[team_pool["age_group"] == age]
            if year_val is not None and "event_id" in team_pool.columns and "start_date" in event_opts.columns:
                year_events = set(
                    event_opts.loc[
                        pd.to_datetime(event_opts["start_date"], errors="coerce").dt.year == year_val,
                        "event_id",
                    ]
                    .astype(str)
                    .tolist()
                )
                team_pool = team_pool.loc[team_pool["event_id"].astype(str).isin(year_events)]
            # Newest seasons first for the default pick
            if "event_id" in team_pool.columns and "start_date" in event_opts.columns:
                ev_year = event_opts[["event_id", "start_date"]].copy()
                ev_year["event_id"] = ev_year["event_id"].astype(str)
                ev_year["_yr"] = pd.to_datetime(ev_year["start_date"], errors="coerce").dt.year
                team_pool = team_pool.copy()
                team_pool["_eid"] = team_pool["event_id"].astype(str)
                team_pool = team_pool.merge(
                    ev_year[["event_id", "_yr"]].rename(columns={"event_id": "_eid"}),
                    on="_eid",
                    how="left",
                ).sort_values(["_yr", "age_group", "team_name"], ascending=[False, True, True])
        if team_pool.empty:
            st.info("No team rows for these Deep-Dive filters (try All seasons / ages, or pick a season with roster data).")
        else:
            def _team_label(tid: str) -> str:
                row = team_pool.loc[team_pool["team_id"] == tid].iloc[0]
                bits = []
                if pd.notna(row.get("_yr")):
                    bits.append(str(int(row["_yr"])))
                elif pd.notna(row.get("event_id")):
                    ev = event_opts.loc[event_opts["event_id"].astype(str) == str(row["event_id"])]
                    if not ev.empty and pd.notna(ev.iloc[0].get("start_date")):
                        try:
                            bits.append(str(int(str(ev.iloc[0]["start_date"])[:4])))
                        except Exception:
                            pass
                bits.append(str(row.get("team_name") or tid))
                if pd.notna(row.get("age_group")):
                    bits.append(str(row["age_group"]))
                return " · ".join(bits)

            team_ids = team_pool["team_id"].tolist()
            if st.session_state.get("deep_dive_roster_team") not in team_ids:
                st.session_state["deep_dive_roster_team"] = team_ids[0]

            roster_team_id = st.selectbox(
                "Team",
                options=team_ids,
                format_func=_team_label,
                key="deep_dive_roster_team",
            )
            tmeta = team_pool.loc[team_pool["team_id"] == roster_team_id].iloc[0]
            club_id = tmeta.get("club_id")
            event_id = str(tmeta["event_id"]) if pd.notna(tmeta.get("event_id")) else None
            club_name = tmeta.get("club_name")

            top = st.columns(3)
            top[0].markdown(f"**{tmeta.get('team_name') or roster_team_id}**")
            if club_id and top[1].button(
                f"View club · {club_name or club_id}",
                key=f"dd_roster_club_{roster_team_id}",
            ):
                jump_to_club(club_id)
            top[2].caption(f"Event `{event_id}` · {tmeta.get('age_group') or ''}")

            players = player_browse_players(roster_team_id, event_id)
            coaches = coach_browse_coaches(roster_team_id, event_id)

            left, right = st.columns(2)
            with left:
                st.markdown(f"**Players** ({len(players)})")
                if players.empty:
                    st.caption("No roster loaded for this team/season.")
                else:
                    for i, prow in players.iterrows():
                        cols = st.columns([3.5, 1])
                        jersey = (
                            f"#{int(prow['uniform_number'])} "
                            if pd.notna(prow.get("uniform_number"))
                            else ""
                        )
                        cols[0].write(f"{jersey}{prow['full_name']}")
                        if cols[1].button(
                            "View",
                            key=f"dd_player_{roster_team_id}_{prow['player_id']}_{i}",
                            help="Open Player View",
                        ):
                            jump_to_player(
                                prow["player_id"],
                                query=str(prow["full_name"]).split()[-1],
                            )
            with right:
                st.markdown(f"**Coaches / staff** ({len(coaches)})")
                if coaches.empty:
                    st.caption("No staff loaded for this team/season.")
                else:
                    for i, crow in coaches.iterrows():
                        cols = st.columns([3.5, 1])
                        role = f" ({crow['role']})" if pd.notna(crow.get("role")) else ""
                        cols[0].write(f"{crow['full_name']}{role}")
                        if cols[1].button(
                            "View",
                            key=f"dd_coach_{roster_team_id}_{crow['staff_id']}_{i}",
                            help="Open Coach View",
                        ):
                            jump_to_coach(
                                crow["staff_id"],
                                query=str(crow["full_name"]).split()[-1],
                            )

        if not traj.empty:
            show = traj.copy()
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
            club_opts = panorama["club_id"].tolist()
            focus_default = st.session_state.get("club_panorama_focus")
            if focus_default not in club_opts and club_opts:
                # keep invalid pending from breaking selectbox
                if "club_panorama_focus" in st.session_state:
                    del st.session_state["club_panorama_focus"]
            focus_club = st.selectbox(
                "Focus club",
                options=club_opts,
                format_func=lambda cid: panorama.loc[panorama["club_id"] == cid, "club_name"].iloc[0],
                key="club_panorama_focus",
                help="Jump targets and related links use this club",
            )
            focus_row = panorama.loc[panorama["club_id"] == focus_club].iloc[0]
            fc1, fc2, fc3, fc4 = st.columns(4)
            fc1.metric("Gold", int(focus_row["gold"]))
            fc2.metric("Silver", int(focus_row["silver"]))
            fc3.metric("Entries", int(focus_row["entries"]))
            fc4.metric("Open-tier ratio", f"{float(focus_row['open_tier_ratio']):.1%}")

            # Programs under this club → Deep-Dive
            club_progs = programs.loc[programs["club_id"] == focus_club].copy() if "club_id" in programs.columns else pd.DataFrame()
            if not club_progs.empty:
                st.markdown("**Programs in this club**")
                for i, prow in club_progs.iterrows():
                    cols = st.columns([3.5, 1, 1, 1])
                    cols[0].write(
                        f"{prow.get('program_label') or prow['program_id']}"
                        + (f" ({prow.get('gender_code')})" if pd.notna(prow.get("gender_code")) else "")
                    )
                    if prow["program_id"] in program_id_set and cols[1].button(
                        "Team",
                        key=f"club_prog_dd_{prow['program_id']}",
                    ):
                        jump_to_team_deep_dive(prow["program_id"], ages=ages)
                    elif prow["program_id"] not in program_id_set:
                        cols[1].caption("—")
                    if cols[2].button("Players", key=f"club_prog_p_{prow['program_id']}"):
                        jump_to_player(club_id=focus_club)
                    if cols[3].button("Coaches", key=f"club_prog_c_{prow['program_id']}"):
                        jump_to_coach(club_id=focus_club)
            else:
                bc1, bc2 = st.columns(2)
                if bc1.button("Browse players for this club", key=f"club_focus_players_{focus_club}"):
                    jump_to_player(club_id=focus_club)
                if bc2.button("Browse coaches for this club", key=f"club_focus_coaches_{focus_club}"):
                    jump_to_coach(club_id=focus_club)

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
        for row in sample_teams.itertuples(index=False):
            tid = getattr(row, "team_id", None)
            tname = getattr(row, "program_label", None) or tid
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
        c2.metric("Gender scope", gender)

        if player_n == 0:
            st.warning(
                "No roster rows loaded yet. Run:\n\n"
                "`python scripts/backfill_rosters.py --workers 2`\n\n"
                "Endpoint: `/api/public/scheduler-teams/{id}/roster`"
            )
        else:
            pick_mode = st.radio(
                "Find player",
                options=["Search", "Browse club → season → team"],
                horizontal=True,
                key="player_pick_mode",
            )
            pid = None
            g_filter = None if gender == "All" else gender

            if pick_mode == "Search":
                q = st.text_input("Search player", placeholder="e.g. Carr, Paige Din", key="player_search_q")
                hits = player_search(q, gender=g_filter) if q.strip() else players_df.head(100)
                # Keep a jumped-to player visible even if outside the current hit list
                sel = st.session_state.get("player_view_player")
                if sel and (hits.empty or sel not in set(hits["player_id"])):
                    extra = players_df.loc[players_df["player_id"] == sel]
                    hits = pd.concat([extra, hits], ignore_index=True).drop_duplicates("player_id")
                if hits.empty:
                    st.info("No players matched.")
                else:
                    labels = {
                        r.player_id: (
                            f"{r.full_name} ({int(r.first_year or 0)}–{int(r.last_year or 0)}, "
                            f"{int(r.seasons or 0)} seasons)"
                        )
                        for r in hits.itertuples(index=False)
                    }
                    opts = list(labels.keys())
                    if sel in opts:
                        st.session_state["player_view_player"] = sel
                    pid = st.selectbox(
                        "Player",
                        options=opts,
                        format_func=lambda x: labels.get(x, x),
                        key="player_view_player",
                    )
            else:
                clubs = player_browse_clubs(g_filter)
                if clubs.empty:
                    st.info("No club roster rows for this gender filter.")
                else:
                    club_ids = clubs["club_id"].tolist()
                    pending_club = st.session_state.get("player_browse_club")
                    if pending_club and pending_club not in club_ids:
                        club_ids = [pending_club] + club_ids
                    club_id = st.selectbox(
                        "Club",
                        options=club_ids,
                        format_func=lambda cid: (
                            clubs.loc[clubs["club_id"] == cid, "club_name"].iloc[0]
                            if cid in set(clubs["club_id"])
                            else str(cid)
                        ),
                        key="player_browse_club",
                    )
                    bc1, bc2 = st.columns(2)
                    if bc1.button("View this club", key="player_browse_to_club"):
                        jump_to_club(club_id)
                    if bc2.button("Browse coaches at this club", key="player_browse_to_coaches"):
                        jump_to_coach(club_id=club_id)
                    seasons = player_browse_seasons(club_id, g_filter)
                    if seasons.empty:
                        st.info("No seasons for this club.")
                    else:
                        season_ids = [str(x) for x in seasons["event_id"].tolist()]
                        pending_season = st.session_state.get("player_browse_season")
                        if pending_season and str(pending_season) not in season_ids:
                            season_ids = [str(pending_season)] + season_ids
                        event_id = st.selectbox(
                            "Season",
                            options=season_ids,
                            format_func=lambda eid: (
                                f"{int(seasons.loc[seasons['event_id'].astype(str)==str(eid),'season_year'].iloc[0]) if (seasons['event_id'].astype(str)==str(eid)).any() and pd.notna(seasons.loc[seasons['event_id'].astype(str)==str(eid),'season_year'].iloc[0]) else '?'} — "
                                f"{seasons.loc[seasons['event_id'].astype(str)==str(eid),'event_name'].iloc[0] if (seasons['event_id'].astype(str)==str(eid)).any() else eid}"
                            ),
                            key="player_browse_season",
                        )
                        team_opts = player_browse_teams(club_id, str(event_id), g_filter)
                        if team_opts.empty:
                            st.info("No teams for this club/season.")
                        else:
                            team_ids = team_opts["team_id"].tolist()
                            pending_team = st.session_state.get("player_browse_team")
                            if pending_team and pending_team not in team_ids:
                                team_ids = [pending_team] + team_ids
                            team_id = st.selectbox(
                                "Team",
                                options=team_ids,
                                format_func=lambda tid: (
                                    f"{team_opts.loc[team_opts['team_id']==tid,'team_name'].iloc[0]}"
                                    + (
                                        f" ({team_opts.loc[team_opts['team_id']==tid,'age_group'].iloc[0]})"
                                        if tid in set(team_opts["team_id"]) and pd.notna(team_opts.loc[team_opts['team_id']==tid,'age_group'].iloc[0])
                                        else ""
                                    )
                                    if tid in set(team_opts["team_id"])
                                    else str(tid)
                                ),
                                key="player_browse_team",
                            )
                            trow = (
                                team_opts.loc[team_opts["team_id"] == team_id].iloc[0]
                                if team_id in set(team_opts["team_id"])
                                else None
                            )
                            b1, b2, b3 = st.columns(3)
                            if trow is not None and b1.button("Open team Deep-Dive", key="player_browse_team_dd"):
                                jump_to_team_deep_dive(
                                    trow.get("program_id"),
                                    event_id=str(event_id),
                                    age_group=trow.get("age_group"),
                                    ages=ages,
                                )
                            if b2.button("View club", key="player_browse_team_club"):
                                jump_to_club(club_id)
                            if b3.button("Team coaches", key="player_browse_team_coaches"):
                                jump_to_coach(
                                    club_id=club_id,
                                    event_id=str(event_id),
                                    team_id=team_id,
                                )
                            people = player_browse_players(team_id, str(event_id))
                            if people.empty:
                                st.info("No players on this roster.")
                            else:
                                labels = {
                                    r.player_id: (
                                        f"{r.full_name}"
                                        + (f" #{int(r.uniform_number)}" if pd.notna(r.uniform_number) else "")
                                    )
                                    for r in people.itertuples(index=False)
                                }
                                pid = st.selectbox(
                                    "Player",
                                    options=list(labels.keys()),
                                    format_func=lambda x: labels.get(x, x),
                                    key="player_browse_player",
                                )

            if pid:
                stints = enrich_player_stints_with_team_perf(load_player_stints(pid), matches)
                if gender != "All" and "event_id" in stints.columns:
                    # Keep stints that overlap current gender events
                    gender_events = set(event_opts["event_id"].astype(str))
                    stints = stints.loc[stints["event_id"].astype(str).isin(gender_events)].copy()
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
                render_cross_links(
                    stints,
                    key_prefix=f"player_dd_{pid}",
                    program_ids=program_id_set,
                    ages=ages,
                    teams_df=teams,
                    programs_df=programs,
                    show_players=False,
                )
                st.caption("Use **Team / Club / Coaches** to jump to related views for that season.")

                if not show.empty and show["season_year"].notna().any():
                    fig = px.scatter(
                        show.dropna(subset=["season_year"]),
                        x="season_year",
                        y="final_rank" if show["final_rank"].notna().any() else "age_group",
                        size="matches" if "matches" in show.columns else None,
                        hover_data=[
                            c
                            for c in [
                                "team_name",
                                "program_label",
                                "club_name",
                                "initial_seed",
                                "win_rate",
                                "age_group",
                            ]
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
                # remove old caption — replaced above

    with tab6:
        st.subheader("Coach View")
        coach_n = len(coaches_df)
        c1, c2, c3 = st.columns(3)
        c1.metric("Coaches / Staff", f"{coach_n:,}")
        c2.metric("Gender scope", gender)
        if not coaches_df.empty:
            c3.metric("Head-coach stints", f"{int(coaches_df['head_coach_stints'].sum()):,}")

        if coach_n == 0:
            st.warning("No coach rows loaded. Run `python scripts/backfill_rosters.py --workers 2`")
        else:
            pick_mode = st.radio(
                "Find coach",
                options=["Search", "Browse club → season → team"],
                horizontal=True,
                key="coach_pick_mode",
            )
            sid = None
            g_filter = None if gender == "All" else gender

            if pick_mode == "Search":
                q = st.text_input("Search coach", placeholder="e.g. Gill, Braga, Mackin", key="coach_search_q")
                hits = coach_search(q, gender=g_filter) if q.strip() else coaches_df.head(100)
                sel = st.session_state.get("coach_view_staff")
                if sel and (hits.empty or sel not in set(hits["staff_id"])):
                    extra = coaches_df.loc[coaches_df["staff_id"] == sel]
                    hits = pd.concat([extra, hits], ignore_index=True).drop_duplicates("staff_id")
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
                    if sel in coach_opts:
                        default_idx = coach_opts.index(sel)
                    else:
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
            else:
                clubs = coach_browse_clubs(g_filter)
                if clubs.empty:
                    st.info("No club coach rows for this gender filter.")
                else:
                    club_ids = clubs["club_id"].tolist()
                    pending_club = st.session_state.get("coach_browse_club")
                    if pending_club and pending_club not in club_ids:
                        club_ids = [pending_club] + club_ids
                    club_id = st.selectbox(
                        "Club",
                        options=club_ids,
                        format_func=lambda cid: (
                            clubs.loc[clubs["club_id"] == cid, "club_name"].iloc[0]
                            if cid in set(clubs["club_id"])
                            else str(cid)
                        ),
                        key="coach_browse_club",
                    )
                    bc1, bc2 = st.columns(2)
                    if bc1.button("View this club", key="coach_browse_to_club"):
                        jump_to_club(club_id)
                    if bc2.button("Browse players at this club", key="coach_browse_to_players"):
                        jump_to_player(club_id=club_id)
                    seasons = coach_browse_seasons(club_id, g_filter)
                    if seasons.empty:
                        st.info("No seasons for this club.")
                    else:
                        season_ids = [str(x) for x in seasons["event_id"].tolist()]
                        pending_season = st.session_state.get("coach_browse_season")
                        if pending_season and str(pending_season) not in season_ids:
                            season_ids = [str(pending_season)] + season_ids
                        event_id = st.selectbox(
                            "Season",
                            options=season_ids,
                            format_func=lambda eid: (
                                f"{int(seasons.loc[seasons['event_id'].astype(str)==str(eid),'season_year'].iloc[0]) if (seasons['event_id'].astype(str)==str(eid)).any() and pd.notna(seasons.loc[seasons['event_id'].astype(str)==str(eid),'season_year'].iloc[0]) else '?'} — "
                                f"{seasons.loc[seasons['event_id'].astype(str)==str(eid),'event_name'].iloc[0] if (seasons['event_id'].astype(str)==str(eid)).any() else eid}"
                            ),
                            key="coach_browse_season",
                        )
                        team_opts = coach_browse_teams(club_id, str(event_id), g_filter)
                        if team_opts.empty:
                            st.info("No teams for this club/season.")
                        else:
                            team_ids = team_opts["team_id"].tolist()
                            pending_team = st.session_state.get("coach_browse_team")
                            if pending_team and pending_team not in team_ids:
                                team_ids = [pending_team] + team_ids
                            team_id = st.selectbox(
                                "Team",
                                options=team_ids,
                                format_func=lambda tid: (
                                    f"{team_opts.loc[team_opts['team_id']==tid,'team_name'].iloc[0]}"
                                    + (
                                        f" ({team_opts.loc[team_opts['team_id']==tid,'age_group'].iloc[0]})"
                                        if tid in set(team_opts["team_id"]) and pd.notna(team_opts.loc[team_opts['team_id']==tid,'age_group'].iloc[0])
                                        else ""
                                    )
                                    if tid in set(team_opts["team_id"])
                                    else str(tid)
                                ),
                                key="coach_browse_team",
                            )
                            trow = (
                                team_opts.loc[team_opts["team_id"] == team_id].iloc[0]
                                if team_id in set(team_opts["team_id"])
                                else None
                            )
                            b1, b2, b3 = st.columns(3)
                            if trow is not None and b1.button("Open team Deep-Dive", key="coach_browse_team_dd"):
                                jump_to_team_deep_dive(
                                    trow.get("program_id"),
                                    event_id=str(event_id),
                                    age_group=trow.get("age_group"),
                                    ages=ages,
                                )
                            if b2.button("View club", key="coach_browse_team_club"):
                                jump_to_club(club_id)
                            if b3.button("Team players", key="coach_browse_team_players"):
                                jump_to_player(
                                    club_id=club_id,
                                    event_id=str(event_id),
                                    team_id=team_id,
                                )
                            people = coach_browse_coaches(team_id, str(event_id))
                            if people.empty:
                                st.info("No coaches on this staff list.")
                            else:
                                labels = {
                                    r.staff_id: f"{r.full_name} ({r.role or 'staff'})"
                                    for r in people.itertuples(index=False)
                                }
                                sid = st.selectbox(
                                    "Coach",
                                    options=list(labels.keys()),
                                    format_func=lambda x: labels.get(x, x),
                                    key="coach_browse_staff",
                                )

            if sid:
                career = load_coach_career(sid, matches=matches)
                if gender != "All" and "event_id" in career.columns:
                    gender_events = set(event_opts["event_id"].astype(str))
                    career = career.loc[career["event_id"].astype(str).isin(gender_events)].copy()
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
                    show["win_rate"] = show["win_rate"].map(
                        lambda x: round(float(x), 3) if pd.notna(x) else x
                    )
                st.dataframe(show, width="stretch", hide_index=True)
                render_cross_links(
                    career,
                    key_prefix=f"coach_dd_{sid}",
                    program_ids=program_id_set,
                    ages=ages,
                    teams_df=teams,
                    programs_df=programs,
                    show_coaches=False,
                )
                st.caption("Use **Team / Club / Players** to jump to related views for that season.")

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
                    if not club_summary.empty and "club_name" in career.columns:
                        # Link first few clubs from career
                        club_ids = (
                            career.dropna(subset=["club_id"])[["club_id", "club_name"]]
                            .drop_duplicates("club_id")
                            .head(8)
                        )
                        if not club_ids.empty:
                            st.markdown("**Jump to club**")
                            cols = st.columns(min(4, len(club_ids)))
                            for i, (_, crow) in enumerate(club_ids.iterrows()):
                                if cols[i % len(cols)].button(
                                    str(crow["club_name"])[:28],
                                    key=f"coach_club_jump_{sid}_{crow['club_id']}",
                                ):
                                    jump_to_club(crow["club_id"])


if __name__ == "__main__":
    main()
