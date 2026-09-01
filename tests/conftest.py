import pytest


@pytest.fixture(autouse=True)
def _sandbox_bounded_rescue_state(request, monkeypatch, tmp_path):
    """Keep bounded-rescue durable-state writes out of /state during unit tests only."""
    if request.module.__name__.endswith("test_gemini_l5_bounded_rescue_q1"):
        monkeypatch.setenv("GEMINI_L5_PAIR_STATE_FILE", str(tmp_path / "pair-state.json"))
