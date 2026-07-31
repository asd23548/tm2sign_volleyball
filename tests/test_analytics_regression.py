"""Regression tests for analytics loaders + dashboard-critical invariants."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analytics import (  # noqa: E402
    filter_matches,
    load_matches_enriched,
    load_programs,
    load_rankings_enriched,
    load_teams,
    program_performance_metrics,
    program_season_trajectory,
    seed_accuracy,
)
from src.analytics.coaches import coach_search, load_coaches  # noqa: E402
from src.analytics.players import load_players, player_search  # noqa: E402


def _assert_unique_columns(df: pd.DataFrame, label: str) -> None:
    dups = df.columns[df.columns.duplicated()].tolist()
    assert not dups, f"{label} has duplicate columns: {dups}"


def _assert_series(df: pd.DataFrame, col: str, label: str) -> None:
    assert col in df.columns, f"{label} missing {col}"
    assert isinstance(df[col], pd.Series), f"{label}[{col}] is {type(df[col])}, expected Series"


@pytest.fixture(scope="module")
def matches() -> pd.DataFrame:
    return load_matches_enriched()


@pytest.fixture(scope="module")
def teams() -> pd.DataFrame:
    return load_teams()


@pytest.fixture(scope="module")
def rankings() -> pd.DataFrame:
    return load_rankings_enriched()


@pytest.fixture(scope="module")
def programs() -> pd.DataFrame:
    return load_programs()


class TestLoaderShapes:
    def test_matches_nonempty_unique_cols(self, matches: pd.DataFrame) -> None:
        assert not matches.empty
        _assert_unique_columns(matches, "matches")
        for col in ("event_id", "team_a_id", "team_b_id", "gender", "start_date", "event_name"):
            _assert_series(matches, col, "matches")

    def test_teams_nonempty_unique_cols(self, teams: pd.DataFrame) -> None:
        assert not teams.empty
        _assert_unique_columns(teams, "teams")
        for col in ("team_id", "program_id", "program_label", "region_id", "gender_code"):
            _assert_series(teams, col, "teams")

    def test_rankings_unique_cols(self, rankings: pd.DataFrame) -> None:
        _assert_unique_columns(rankings, "rankings")
        for col in ("team_id", "event_id", "gender"):
            _assert_series(rankings, col, "rankings")

    def test_programs_unique_cols(self, programs: pd.DataFrame) -> None:
        assert not programs.empty
        _assert_unique_columns(programs, "programs")
        _assert_series(programs, "program_id", "programs")
        _assert_series(programs, "gender_code", "programs")


class TestGenderFilter:
    def test_gender_values(self, matches: pd.DataFrame) -> None:
        vals = set(matches["gender"].dropna().unique())
        assert vals.issubset({"Girls", "Boys"})
        assert "Girls" in vals and "Boys" in vals

    def test_filter_by_gender_reduces(self, matches: pd.DataFrame) -> None:
        girls = matches.loc[matches["gender"] == "Girls"]
        boys = matches.loc[matches["gender"] == "Boys"]
        assert len(girls) + len(boys) == len(matches)
        assert 0 < len(girls) < len(matches)
        assert 0 < len(boys) < len(matches)

    def test_programs_gender_codes(self, programs: pd.DataFrame) -> None:
        codes = set(programs["gender_code"].dropna().unique())
        assert codes.issubset({"G", "B"})


class TestDashboardInvariants:
    def test_event_opts_tolist(self, matches: pd.DataFrame) -> None:
        event_opts = (
            matches[["event_id", "event_name", "start_date"]]
            .drop_duplicates("event_id")
            .sort_values("start_date")
        )
        ids = [str(x) for x in event_opts["event_id"].tolist()]
        assert len(ids) >= 2
        assert len(ids) == len(set(ids))

    def test_sample_teams_itertuples_named(self, matches: pd.DataFrame, teams: pd.DataFrame) -> None:
        played = pd.concat([matches["team_a_id"], matches["team_b_id"]]).dropna().unique()
        sample = (
            teams.loc[teams["team_id"].isin(played)]
            .sort_values(["program_label", "age_num"])
            .drop_duplicates("program_id")
            .head(20)
        )
        assert not sample.empty
        for row in sample.itertuples(index=False):
            assert getattr(row, "team_id")
            # Must not explode on attribute access
            _ = getattr(row, "program_label", None)
            _ = getattr(row, "region_id", None)

    def test_column_subset_unpack_safe(self, teams: pd.DataFrame) -> None:
        """Selecting 3 cols must yield exactly 3 fields per itertuples row."""
        sub = teams[["team_id", "program_label", "region_id"]].head(5)
        _assert_unique_columns(sub, "subset")
        for tid, tname, reg in sub.itertuples(index=False):
            assert tid is not None


class TestCoreAnalytics:
    def test_filter_and_program_metrics(self, matches: pd.DataFrame, programs: pd.DataFrame) -> None:
        pid = programs["program_id"].iloc[0]
        filtered = filter_matches(matches, program_id=pid)
        metrics = program_performance_metrics(filtered, pid)
        assert "matches" in metrics and "win_rate" in metrics
        assert metrics["matches"] >= 0

    def test_trajectory(self, matches: pd.DataFrame, rankings: pd.DataFrame, programs: pd.DataFrame) -> None:
        # Prefer a multi-season girls program if present
        candidates = programs.loc[programs["gender_code"] == "G", "program_id"]
        pid = candidates.iloc[0] if not candidates.empty else programs["program_id"].iloc[0]
        traj = program_season_trajectory(matches, rankings, pid)
        assert isinstance(traj, pd.DataFrame)

    def test_seed_accuracy(self, rankings: pd.DataFrame) -> None:
        scored = seed_accuracy(rankings)
        assert isinstance(scored, pd.DataFrame)


class TestRosterLoadersSmoke:
    def test_players_loader(self) -> None:
        df = load_players()
        assert isinstance(df, pd.DataFrame)
        _assert_unique_columns(df, "players")

    def test_coaches_loader(self) -> None:
        df = load_coaches()
        assert isinstance(df, pd.DataFrame)
        _assert_unique_columns(df, "coaches")

    def test_search_smoke(self) -> None:
        # Empty roster is OK; call must not raise
        player_search("test", limit=5, gender="Girls")
        coach_search("test", limit=5, gender="Boys")


class TestMatchMetricsPresent:
    def test_sprint2_match_fields(self, matches: pd.DataFrame) -> None:
        for col in (
            "team_a_pts_won",
            "team_b_pts_won",
            "is_deciding_set_played",
            "is_tight_set",
        ):
            assert col in matches.columns
        assert matches["team_a_pts_won"].notna().mean() > 0.9
