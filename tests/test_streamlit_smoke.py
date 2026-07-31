"""Import / syntax smoke tests for the Streamlit app module."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "streamlit_app.py"


def test_streamlit_app_parses() -> None:
    src = APP.read_text(encoding="utf-8")
    ast.parse(src)


def test_streamlit_helpers_importable(monkeypatch) -> None:
    # Avoid launching Streamlit runtime side effects beyond import
    sys.path.insert(0, str(ROOT))
    import app.streamlit_app as app  # noqa: F401

    assert callable(app.filter_by_gender)
    assert callable(app.jump_to_team_deep_dive)
    assert callable(app.jump_to_club)
    assert callable(app.jump_to_player)
    assert callable(app.jump_to_coach)
    assert callable(app.apply_pending_jumps)
    assert callable(app.render_cross_links)

    # filter_by_gender basic behavior
    import pandas as pd

    df = pd.DataFrame({"gender": ["Girls", "Boys", "Girls"], "x": [1, 2, 3]})
    out = app.filter_by_gender(df, "Girls")
    assert len(out) == 2
    assert set(out["gender"]) == {"Girls"}
