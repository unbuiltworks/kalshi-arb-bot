"""Underwater-episode reconstruction: features precede the action they label."""
from __future__ import annotations

import ast
from pathlib import Path

from src import l5_underwater as uw

SRC = Path(uw.__file__).read_text(encoding="utf-8")


def _fill(t, outcome, price, qty, tte=600):
    return {"timestamp": str(t), "trade_seq": str(t), "side": "BUY",
            "outcome": outcome, "price": str(price), "quantity": str(qty),
            "seconds_to_settlement": str(tte), "asset": "BTC",
            "duration_minutes": "5", "window_id": "w1",
            "settlement_outcome": "Up"}


WINDOW = [_fill(100, "Up", 0.60, 10), _fill(110, "Up", 0.40, 10),
          _fill(120, "Down", 0.55, 5), _fill(130, "Up", 0.45, 10)]


def test_gain_is_measured_against_the_running_average_of_the_held_side():
    pts = uw.episode(WINDOW)
    # after two Up fills: avg 0.50, last price 0.40 -> underwater 10c
    assert abs(pts[1]["avg_cost_held"] - 0.50) < 1e-9
    assert abs(pts[1]["gain_per_unit"] + 0.10) < 1e-9
    assert pts[1]["gain_bucket"] == "-10..-5c"


def test_the_next_action_label_is_an_outcome_not_a_feature():
    pts = uw.episode(WINDOW)
    assert pts[1]["next_action"] == uw.SWITCH      # next fill is Down
    assert pts[0]["next_action"] == uw.EXTEND      # next fill is Up again
    assert pts[-1]["next_action"] == uw.HOLD       # nothing follows
    for p in pts:
        assert "next_action" in p and "gain_per_unit" in p


def test_features_do_not_change_when_later_fills_are_removed():
    """No lookahead: truncating the episode must not alter earlier state."""
    full = uw.episode(WINDOW)
    for k in range(len(WINDOW)):
        cut = uw.episode(WINDOW[:k + 1])
        for field in ("gain_per_unit", "avg_cost_held", "held_qty",
                      "residual_qty", "matched_qty", "run_length"):
            assert cut[k][field] == full[k][field], (k, field)


def test_bucket_edges_are_the_owner_specified_cuts():
    assert uw.bucket(-0.25, uw.GAIN_BUCKETS) == "<-20c"
    assert uw.bucket(-0.20, uw.GAIN_BUCKETS) == "-20..-10c"
    assert uw.bucket(-0.05, uw.GAIN_BUCKETS) == "-5..0c"
    assert uw.bucket(0.0, uw.GAIN_BUCKETS) == "0..+5c"
    assert uw.bucket(0.05, uw.GAIN_BUCKETS) == ">+5c"
    assert uw.bucket(None, uw.GAIN_BUCKETS) is None


def test_episode_summary_reports_recovery_without_inventing_it():
    s = uw.episode_summary(uw.episode(WINDOW))
    assert s["ever_underwater"] is True
    assert s["n_fills"] == 4 and s["n_switches"] >= 1
    assert s["matched_fraction"] is not None
    assert uw.episode_summary([]) is None


def test_it_is_read_only_research():
    tree = ast.parse(SRC)
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            roots.add((n.module or "").split(".")[0])
    assert roots <= {"__future__", "math", "collections", "typing"}, roots
    names = {getattr(n, "attr", None) or getattr(n, "id", None)
             for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
    for bad in ("request", "urlopen", "socket", "place_limit_buy", "open",
                "getenv", "environ", "Transport", "submit"):
        assert bad not in names, bad
