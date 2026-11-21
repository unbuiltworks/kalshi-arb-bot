"""Regressions for the oscillation / complement-reachability measurements."""
from __future__ import annotations

from pathlib import Path

from src import l5_oscillation as osc

ROOT = Path(__file__).resolve().parents[1]


def _row(outcome, price, qty=1.0, t=0, tte=100, side="BUY"):
    return {"outcome": outcome, "price": price, "quantity": qty,
            "timestamp": t, "seconds_to_settlement": tte, "side": side}


# ------------------------------------------------------------ what a switch is
def test_a_switch_needs_the_outcome_to_change():
    rows = [_row("Up", 0.4, t=0), _row("Up", 0.42, t=1), _row("Up", 0.44, t=2)]
    assert osc.switches(rows) == []


def test_the_switch_records_both_pair_conventions():
    """`last_pair` is what the market offered across the switch; `avg_pair`
    is what the completed pair actually cost, and only the second is the
    analogue of our COMPLETE gate."""
    rows = [_row("Up", 0.40, qty=2, t=0), _row("Up", 0.50, qty=2, t=1),
            _row("Down", 0.55, qty=1, t=2)]
    s = osc.switches(rows)[0]
    assert s["last_same_side_price"] == 0.50
    assert s["first_opposite_price"] == 0.55
    assert round(s["last_pair"], 4) == 1.05
    assert round(s["held_avg_cost"], 4) == 0.45          # running, not final
    assert round(s["avg_pair"], 4) == 1.00


def test_the_running_average_is_not_a_hindsight_average():
    """THE REGRESSION. Averaging over the whole window would price the pair
    with fills that had not happened yet."""
    rows = [_row("Up", 0.40, qty=1, t=0), _row("Down", 0.55, qty=1, t=1),
            _row("Up", 0.90, qty=10, t=2)]
    s = osc.switches(rows)[0]
    assert round(s["held_avg_cost"], 4) == 0.40          # NOT 0.855


def test_sells_are_excluded_from_switches():
    """L5's lifetime SELL volume is $2.18 and a sell is not an inventory
    side switch in the sense being measured."""
    rows = [_row("Up", 0.4, t=0), _row("Up", 0.5, t=1, side="SELL"),
            _row("Up", 0.45, t=2)]
    assert osc.switches(rows) == []


def test_run_length_and_seconds_are_measured_at_the_switch():
    rows = [_row("Up", 0.4, t=0), _row("Up", 0.41, t=10),
            _row("Up", 0.42, t=20), _row("Down", 0.6, t=30)]
    s = osc.switches(rows)[0]
    assert s["run_length_before_switch"] == 3
    assert s["run_seconds"] == 30.0


# ----------------------------------------------------------- window summary
def test_a_one_sided_window_has_no_switches_and_zero_matched():
    rows = [_row("Up", 0.4, qty=2, t=0), _row("Up", 0.45, qty=2, t=5)]
    w = osc.window_summary(rows)
    assert w["switches"] == 0 and w["matched_fraction"] == 0.0
    assert w["terminal_residual"] == 4.0


def test_a_perfectly_matched_window_reads_one():
    rows = [_row("Up", 0.4, qty=3, t=0), _row("Down", 0.55, qty=3, t=5)]
    w = osc.window_summary(rows)
    assert w["switches"] == 1 and w["matched_fraction"] == 1.0
    assert w["terminal_residual"] == 0.0


# --------------------------------------------------------------- the bands
def test_the_band_edges_are_inclusive_upper_bounds():
    assert osc.band(0.95) == "<=.95"
    assert osc.band(0.9501) == ".95-.97"
    assert osc.band(1.00) == ".99-1.00"
    assert osc.band(1.0001) == ">1.00"


def test_the_histogram_reports_the_above_par_share_explicitly():
    """Above-par pairs are the whole subject of P4 -- they must never be
    folded into a catch-all bucket."""
    h = osc.band_histogram([0.90, 0.99, 1.05, 1.20])
    assert h["counts"][">1.00"] == 2
    assert h["share_at_or_below_1_00"] == 0.5


def test_distribution_of_nothing_is_n_zero_not_zeros():
    assert osc.distribution([]) == {"n": 0}
    assert osc.distribution([None, None]) == {"n": 0}


def test_quantiles_are_order_statistics_not_interpolations():
    d = osc.distribution([1.0, 2.0, 100.0])
    assert d["p50"] == 2.0 and d["max"] == 100.0


# ------------------------------------------------------------- shadow only
def test_nothing_armed_imports_this():
    for rel in ("src/pmus_spring_l5_session.py",
                "src/pmus_l5_continuous_session.py",
                "src/pmus_sports_l5_decision.py"):
        p = ROOT / rel
        if p.exists():
            assert "l5_oscillation" not in p.read_text(), rel
