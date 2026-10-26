"""The frozen rule must stay frozen, and must refuse to score itself.

A threshold chosen on data and then evaluated on that same data is not
evidence. These tests pin the frozen values so a later edit is visible, and
pin that `evaluate` drops every window the rule was selected on.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_pmus_entry_rule_frozen as F
from src import l5_two_sided_probe as TS


def test_the_frozen_values_are_the_frozen_values():
    assert F.FROZEN_FIRST_LEG_FLOOR == D("0.45")
    assert F.FROZEN_PAIR_CEILING == D("0.96")
    assert F.FROZEN_FILL_MODE == "STRICT"
    assert F.FROZEN_COMPLETION_POLICY == "JOIN"
    assert F.FROZEN_IMPROVE_TICKS == 0
    assert F.FROZEN_REDUCE_AFTER_S is None
    assert F.FROZEN_MAX_LIVE_ORDERS == 1
    assert F.FROZEN_OPEN_MAKER is True


def test_it_does_not_touch_the_live_floor():
    """The deployed default must stay 0.20 whatever this research rule says."""
    assert F.LIVE_FLOOR_UNCHANGED == D("0.20")
    assert TS.MIN_FIRST_LEG_OWN_PRICE == D("0.20")
    assert F.FROZEN_FIRST_LEG_FLOOR != F.LIVE_FLOOR_UNCHANGED


def test_the_frozen_floor_is_inside_the_configurable_range():
    """A rule the deployed gate could not express would be untestable live."""
    assert D(0) <= F.FROZEN_FIRST_LEG_FLOOR <= D("0.50")


def test_every_window_the_rule_was_selected_on_is_declared():
    assert len(F.SELECTION_WINDOWS) == 26
    assert len(F.PRIOR_EXAMINED_WINDOWS) == 8
    assert len(F.EXAMINED) == 34
    assert set(F.SELECTION_WINDOWS).isdisjoint(F.PRIOR_EXAMINED_WINDOWS)


def test_untouched_drops_examined_windows():
    rows = [{"market": F.SELECTION_WINDOWS[0]},
            {"market": F.PRIOR_EXAMINED_WINDOWS[0]},
            {"market": "cpc-btc-updown-15m-2026-10-27-0000z"}]
    out = F.untouched(rows)
    assert [r["market"] for r in out] == [
        "cpc-btc-updown-15m-2026-10-27-0000z"]


def test_evaluate_refuses_to_score_the_selection_corpus_by_default():
    """Scoring the selection windows would report the fit as a result."""
    rows = [{"market": w, "side": "UP", "bid": D("0.40"), "ask": D("0.41"),
             "t": "2026-10-26T00:00:00Z", "elapsed_s": 10.0}
            for w in F.SELECTION_WINDOWS]
    out = F.evaluate(rows, {})
    assert out["windows_scored"] == []
    assert out["examined_windows_excluded"] is True
    assert out["n_completed"] == 0 and out["n_stranded"] == 0


def test_evaluate_can_produce_a_no_floor_control_on_the_same_rows():
    """The control must use the SAME code path, or it is not a control."""
    rows = [{"market": "cpc-btc-updown-15m-2026-10-27-0000z", "side": "UP",
             "bid": D("0.10"), "ask": D("0.11"),
             "t": "2026-10-26T00:00:00Z", "elapsed_s": 10.0}]
    gated = F.evaluate(rows, {})
    ctrl = F.evaluate(rows, {}, floor="none")
    # own maker cost 0.10 is below the 0.45 floor, so the gate refuses it
    assert gated["refused_on_first_leg_floor"] >= 1
    assert ctrl["refused_on_first_leg_floor"] == 0
    assert ctrl["min_first_leg_own_px"] is None
    assert D(gated["min_first_leg_own_px"]) == F.FROZEN_FIRST_LEG_FLOOR


def test_the_rule_changes_exactly_one_thing_against_the_baseline():
    """Ceiling, fill model, completion policy and reduction are baseline, so
    any difference it shows is attributable to the entry floor alone."""
    from src import l5_maker_placement_replay as M
    from src import l5_maker_pair_gate as G
    assert F.FROZEN_PAIR_CEILING == G.TAKER_ERA_CEILING
    assert F.FROZEN_FILL_MODE == M.FILL_STRICT
    assert F.FROZEN_IMPROVE_TICKS == 0
    assert F.FROZEN_REDUCE_AFTER_S is None
