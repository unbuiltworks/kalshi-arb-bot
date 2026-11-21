"""SMALL-L5 -> RAMPED-L5: the corpus claim and the trajectory statistics."""
from __future__ import annotations

import pytest

from analysis.l5.scale_history import build as B


def test_window_metrics_track_inventory_matched_and_residual():
    t = [{"oi": 0, "side": "BUY", "size": 10.0, "usd": 3.5, "ts": 1, "day": "d"},
         {"oi": 0, "side": "BUY", "size": 10.0, "usd": 3.5, "ts": 2, "day": "d"},
         {"oi": 1, "side": "BUY", "size": 12.0, "usd": 7.8, "ts": 3, "day": "d"}]
    m = B.window_metrics(t)
    assert m["n_fills"] == 3
    assert m["matched"] == 12.0            # min(up=20, dn=12)
    assert m["residual"] == 8.0            # 20 - 12
    assert m["resid_over_matched"] == pytest.approx(8.0 / 12.0)
    assert m["switches"] == 1              # one side change, up->down
    assert m["max_run_len"] == 2           # two consecutive UP fills
    assert m["peak_residual"] == 20.0      # before the down leg landed


def test_a_sell_reduces_the_side_it_sells():
    t = [{"oi": 0, "side": "BUY", "size": 10.0, "usd": 3.5, "ts": 1, "day": "d"},
         {"oi": 0, "side": "SELL", "size": 4.0, "usd": 1.6, "ts": 2, "day": "d"}]
    assert B.window_metrics(t)["residual"] == 6.0


def test_change_point_detection_finds_a_planted_step():
    series = [10.0] * 12 + [1000.0] * 12
    brk = B.cusum_breaks(series)
    assert brk and min(abs(i - 12) for i in brk) <= 1


def test_change_point_detection_reports_nothing_on_a_flat_series():
    assert B.cusum_breaks([5.0] * 30) == []


def test_a_pure_rescale_leaves_normalized_shape_unchanged():
    """The property the primary test rests on: multiplying every fill by a
    constant must move notional and leave residual/matched, switching and run
    length alone. If this failed, regime comparisons would be meaningless."""
    base = [{"oi": i % 2, "side": "BUY", "size": 10.0 + i, "usd": (10.0 + i) * .35,
             "ts": i, "day": "d"} for i in range(12)]
    scaled = [{**r, "size": r["size"] * 37.0, "usd": r["usd"] * 37.0} for r in base]
    a, b = B.window_metrics(base), B.window_metrics(scaled)
    assert b["notional"] == pytest.approx(a["notional"] * 37.0)
    assert b["resid_over_matched"] == pytest.approx(a["resid_over_matched"])
    assert b["switch_rate"] == pytest.approx(a["switch_rate"])
    assert b["mean_run_len"] == pytest.approx(a["mean_run_len"])
    assert b["median_size"] == pytest.approx(a["median_size"] * 37.0)


def test_maker_taker_is_never_fabricated_from_this_tape():
    """The public activity feed has no aggressor flag. The module must not
    invent one, because a maker share is a claim about fee economics."""
    src = open(B.__file__, encoding="utf-8").read()
    body = src.split('"""', 2)[-1]          # past the module docstring
    for token in ("maker", "taker", "is_aggressor", "isAggressor"):
        assert token not in body.lower(), token
