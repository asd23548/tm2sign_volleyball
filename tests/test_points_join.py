"""Smoke tests for joining NCVA points onto team trajectory rows."""

from __future__ import annotations

import pandas as pd

from src.analytics import load_matches_enriched, load_programs, program_season_trajectory
from src.analytics.points import attach_points_to_trajectory, load_points_for_program


def test_attach_points_to_trajectory_has_place_not_seed() -> None:
    programs = load_programs()
    matches = load_matches_enriched()
    candidates = programs.loc[programs["gender_code"] == "G", "program_id"]
    pid = None
    for cand in candidates.tolist():
        pts = load_points_for_program(cand)
        if not pts.empty:
            pid = cand
            break
    if pid is None:
        # No points join available in this DB — skip softly
        return

    traj = attach_points_to_trajectory(program_season_trajectory(matches, None, pid), pid)
    assert isinstance(traj, pd.DataFrame)
    assert not traj.empty
    assert "initial_seed" not in traj.columns
    assert "final_rank" not in traj.columns
    assert "overall_place" in traj.columns or "season_total" in traj.columns
    # At least one season should join points for a known points-backed program
    joined = traj["overall_place"].notna() if "overall_place" in traj.columns else traj["season_total"].notna()
    assert bool(joined.any())
