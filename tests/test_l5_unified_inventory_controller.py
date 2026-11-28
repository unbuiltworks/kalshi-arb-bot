"""ONE controller over the whole action set, and unequal inventory priced.

PHASE 1 asked for a single controller responsible for opening, accumulating
the favoured side, acquiring the unfavoured side, completing pairs,
preserving resting orders, repricing, replenishing, reducing exposure and
holding -- with no competing controllers and no duplicated decision logic.

`decide_inventory` is that entry point and it is a DISPATCHER. It owns one
thing: which action FAMILY the current inventory state puts us in. Every
price is deferred to `decide`, `reopen_decision` and
`weighted_inventory_decision`, each called at most once. These tests pin
that it dispatches correctly, defers rather than re-derives, and fails
closed.

PHASE 3 asked for probability-aware favourite accumulation. The arithmetic
settles most of it before any replay: resting each leg `delta` behind its
touch earns `delta` per leg, so under a fair market expectancy scales with
the TOTAL contract count and weighting buys VARIANCE, not edge. The replay
in `analysis/l5/issue195/inventory_weighting_compare.py` then measured the
only independent signal our recordings contain -- top-of-book size
imbalance -- and found it ANTI-predictive: where it disagrees with the mid
it calls the direction correctly 14.0% of the time in sample, 20.1% out of
sample. So the action is PRICED and refused for want of a signal, which is
different from being unable to consider it.

1230z IS THE WORST CASE OF AN UNEQUAL ROUND, and it is pinned below against
the venue's own basis: UP 2 @ 0.9100 and DOWN 1 @ 0.5000 pays -$0.4100 if
DOWN wins and +$0.5900 if UP does. PR #207 asserts the same two numbers
from an independent fixture.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_unmatched_disposition as UD
from src import l5_two_sided_probe as TS

BOOK = {"bid": D("0.4800"), "ask": D("0.4900")}


def reopen_kw(**over):
    out = dict(worst_leg_own_px=D("0.4900"), qty=D("1"),
               bid=BOOK["bid"], ask=BOOK["ask"], tick=D("0.01"),
               depth_ticks=2, now_s=0.0, window_end_s=900.0,
               exposure_headroom_usd=D("1.00"))
    out.update(over)
    return out


def disp_kw(**over):
    out = dict(held_cost_usd=D("0.5000"), deficient_qty=D("0"),
               deficient_cost_usd=D("0"),
               bid=BOOK["bid"], ask=BOOK["ask"], tick=D("0.01"),
               min_qty=D("0.01"), now_s=60.0, window_start_s=0.0,
               window_end_s=900.0, taker_enabled=False)
    out.update(over)
    return out


def _no_market(kw):
    """`decide_inventory` supplies `market` itself."""
    out = dict(kw)
    out.pop("market", None)
    return out


def weight_kw(**over):
    out = dict(market="m", favoured_side="UP",
               n_favoured=D("2"), n_other=D("1"),
               up_own_px=D("0.4600"), down_own_px=D("0.4900"),
               bid=BOOK["bid"], ask=BOOK["ask"],
               exposure_headroom_usd=D("2.00"))
    out.update(over)
    return out


# ============================================= 1. ONE CONTROLLER, NOT TWO

def test_the_unified_action_set_is_the_whole_phase_one_list():
    """Every action Phase 1 named must be decidable by one controller."""
    for a in ("OPEN", "ACCUMULATE_FAVORED", "ACQUIRE_UNFAVORED",
              "COMPLETE_MAKER", "PRESERVE", "REPRICE", "REPLENISH",
              "REDUCE", "HOLD"):
        assert a in UD.UNIFIED_ACTIONS, a
    # and the disposition set is a subset, not a rival
    for a in UD.ACTIONS:
        assert a in UD.UNIFIED_ACTIONS


def test_it_defers_and_never_reprices_the_dispositions_itself():
    """A dispatcher that recomputed a price would be a second opinion."""
    import inspect
    src = inspect.getsource(UD.decide_inventory)
    # it may CALL the pricers, but must not do arithmetic of its own
    assert "decide(" in src and "reopen_decision(" in src
    for banned in ("TAKER_THETA", "max_pair_cost", "queue_ahead",
                   "completion_requote"):
        assert banned not in src, banned


def test_holding_inventory_dispatches_to_the_disposition_family():
    r = UD.decide_inventory(market="m", held_side="DOWN", held_qty=D("1"),
                            disposition_kwargs=disp_kw())
    assert r["inventory_family"] == "DISPOSITION"
    assert r["selected"] in UD.ACTIONS


def test_the_disposition_record_keeps_every_key_it_had():
    """MERGED, NOT NESTED: the tape and `controller_preserves` read these."""
    direct = UD.decide(market="m", held_side="DOWN", held_qty=D("1"),
                       **disp_kw())
    via = UD.decide_inventory(market="m", held_side="DOWN", held_qty=D("1"),
                              disposition_kwargs=disp_kw())
    for k in direct:
        assert k in via, k
    assert via["selected"] == direct["selected"]
    assert via["selected_reason"] == direct["selected_reason"]
    assert via["label"] == "L5_UNMATCHED_DISPOSITION"


def test_being_flat_dispatches_to_the_opening_family():
    r = UD.decide_inventory(market="m", held_qty=D("0"), rounds_used=0,
                            reopen_kwargs=reopen_kw())
    assert r["family"] == "OPENING"
    assert r["selected"] == UD.OPEN


def test_the_first_round_is_OPEN_and_a_later_one_is_REPLENISH():
    """Replenishment after a completed pair is the same priced question."""
    first = UD.decide_inventory(market="m", held_qty=D("0"), rounds_used=0,
                                reopen_kwargs=reopen_kw())
    assert first["selected"] == UD.OPEN
    later = UD.decide_inventory(
        market="m", held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reopen_kw(single_outcome_usd=D("-0.03")))
    assert later["selected"] == UD.REPLENISH
    assert later["selected_reason"] == "BALANCED_ROUND_IS_JUSTIFIED"


def test_an_unjustified_reopening_selects_nothing_at_all():
    """A refused round is refused outright; no action replaces it.

    Driven by capacity, which is the bound that still binds now that the
    single-leg outcome is sourced from real fills rather than from a
    PROVEN sampling artefact.
    """
    r = UD.decide_inventory(
        market="m", held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reopen_kw(exposure_headroom_usd=D("0.10")))
    assert r["selected"] is None
    assert r["selected_reason"].startswith("REOPENING_NOT_JUSTIFIED:")
    assert "EXPOSURE_HEADROOM_BELOW_WORST_CASE" in r["selected_reason"]


def test_a_justified_reopening_selects_REPLENISH():
    """THROUGHPUT: repeated rounds are the normal case, not the exception."""
    r = UD.decide_inventory(market="m", held_qty=D("0"), rounds_used=1,
                            reopen_kwargs=reopen_kw())
    assert r["selected"] == UD.REPLENISH
    assert r["selected_reason"] == "BALANCED_ROUND_IS_JUSTIFIED"


def test_state_decides_the_branch_not_preference():
    """Holding and flat are answers to DIFFERENT questions.

    EV(hold) and EV(complete) are equal exactly, which is why `decide`
    ranks on the worst case. Ranking a disposition against an opening
    would be comparing answers to different questions.
    """
    held = UD.decide_inventory(market="m", held_side="DOWN",
                               held_qty=D("1"),
                               disposition_kwargs=disp_kw(),
                               reopen_kwargs=reopen_kw())
    assert held["inventory_family"] == "DISPOSITION"
    assert held["selected"] in UD.ACTIONS, "inventory is disposed of first"


@pytest.mark.parametrize("kw,why", [
    ({"held_qty": "junk"}, "HELD_QTY_UNREADABLE"),
    ({"held_qty": D("1")}, "DISPOSITION_INPUTS_MISSING"),
    ({"held_qty": D("0")}, "OPENING_INPUTS_MISSING"),
])
def test_an_unreadable_state_takes_no_action(kw, why):
    r = UD.decide_inventory(market="m", **kw)
    assert r["selected"] is None
    assert r["selected_reason"] == why


def test_a_raising_pricer_selects_nothing_rather_than_defaulting(
        monkeypatch):
    monkeypatch.setattr(UD, "reopen_decision",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = UD.decide_inventory(market="m", held_qty=D("0"),
                            reopen_kwargs=reopen_kw())
    assert r["selected"] is None
    assert r["selected_reason"] == "REOPEN_DECISION_RAISED"


def test_a_raising_disposition_selects_nothing(monkeypatch):
    monkeypatch.setattr(UD, "decide",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = UD.decide_inventory(market="m", held_side="UP", held_qty=D("1"),
                            disposition_kwargs=disp_kw())
    assert r["selected"] is None
    assert r["selected_reason"] == "DISPOSITION_RAISED"


def test_nothing_in_the_dispatcher_can_submit():
    r = UD.decide_inventory(market="m", held_qty=D("0"),
                            reopen_kwargs=reopen_kw())
    for banned in ("order_id", "coid", "transport", "submit"):
        assert banned not in r


# ================================ 2. UNEQUAL INVENTORY IS NOT RISK-FREE

def test_the_1230z_unequal_position_prices_to_the_settled_numbers():
    """UP 2 @ 0.9100 basis, DOWN 1 @ 0.5000. PR #207 asserts the same."""
    r = UD.weighted_inventory_decision(
        **weight_kw(up_own_px=D("0.4550"), down_own_px=D("0.5000"),
                    p_up_independent=None))
    assert D(r["spend_usd"]) == D("1.4100")
    assert D(r["if_down_wins_usd"]) == D("-0.4100")
    assert D(r["if_up_wins_usd"]) == D("0.5900")
    assert r["unequal_inventory_is_not_risk_free"] is True


def test_without_an_independent_estimate_it_is_refused():
    """This is the CURRENT state of the evidence, and it is the default."""
    r = UD.weighted_inventory_decision(**weight_kw(p_up_independent=None))
    assert r["take"] is False
    assert r["reason"] == "NO_INDEPENDENT_PROBABILITY_ESTIMATE"


def test_the_market_mid_is_not_an_independent_estimate():
    """Weighting toward the mid pays spread to repeat the price's opinion.

    The mid IS the market's probability, so an estimate equal to it carries
    no information and must not clear the floor.
    """
    mid = (BOOK["bid"] + BOOK["ask"]) / 2
    r = UD.weighted_inventory_decision(**weight_kw(p_up_independent=mid))
    assert r["take"] is False
    assert r["reason"] in ("PROBABILITY_EDGE_BELOW_FLOOR",
                           "ESTIMATE_DISAGREES_WITH_THE_OVERWEIGHTED_SIDE")


def test_an_estimate_that_disagrees_with_the_overweighted_side_refuses():
    r = UD.weighted_inventory_decision(**weight_kw(p_up_independent=D("0.25")))
    assert r["take"] is False
    assert r["reason"] == "ESTIMATE_DISAGREES_WITH_THE_OVERWEIGHTED_SIDE"


@pytest.mark.parametrize("p", ["0.50", "0.51", "0.52"])
def test_a_thin_probability_edge_is_not_enough(p):
    r = UD.weighted_inventory_decision(**weight_kw(p_up_independent=D(p)))
    assert r["take"] is False
    assert r["reason"] == "PROBABILITY_EDGE_BELOW_FLOOR"


def test_a_real_independent_edge_does_admit_the_trade():
    """It is a PRICE, not a prohibition -- the mechanism exists."""
    r = UD.weighted_inventory_decision(**weight_kw(p_up_independent=D("0.75")))
    assert r["take"] is True
    assert D(r["expectancy_over_balanced_usd"]) > D(r["min_edge_usd"])


def test_capacity_refuses_an_unequal_round_whatever_the_estimate():
    r = UD.weighted_inventory_decision(
        **weight_kw(p_up_independent=D("0.95"),
                    exposure_headroom_usd=D("0.10")))
    assert r["take"] is False
    assert r["reason"] == "EXPOSURE_HEADROOM_BELOW_WORST_CASE"


def test_the_balanced_alternative_is_always_on_the_record():
    """An unequal round must BEAT balanced, never the other way round."""
    r = UD.weighted_inventory_decision(**weight_kw(p_up_independent=None))
    assert D(r["balanced_alternative_usd"]) == \
        D("1") - (D("0.4600") + D("0.4900"))


def test_an_equal_round_is_not_this_functions_business():
    r = UD.weighted_inventory_decision(
        **weight_kw(n_favoured=D("1"), n_other=D("1")))
    assert r["take"] is False
    assert r["reason"] == "NOT_AN_UNEQUAL_ROUND"


@pytest.mark.parametrize("kw,why", [
    ({"n_favoured": D("0")}, "WEIGHTS_UNREADABLE"),
    ({"n_other": None}, "WEIGHTS_UNREADABLE"),
    ({"up_own_px": None}, "LEG_PRICES_UNREADABLE"),
    ({"bid": D("0.50"), "ask": D("0.49")}, "BOOK_NOT_TWO_SIDED"),
    ({"exposure_headroom_usd": None}, "EXPOSURE_BUDGET_UNREADABLE"),
    ({"favoured_side": "SIDEWAYS"}, "FAVOURED_SIDE_UNREADABLE"),
    ({"p_up_independent": D("1.5")}, "INDEPENDENT_PROBABILITY_OUT_OF_RANGE"),
])
def test_every_unreadable_weighting_input_refuses(kw, why):
    r = UD.weighted_inventory_decision(**weight_kw(**kw))
    assert r["take"] is False, (kw, r.get("reason"))
    assert r["reason"] == why


def test_the_dispatcher_keeps_balanced_when_weighting_is_refused():
    """A refused weighting must not refuse the ROUND -- balanced still goes."""
    r = UD.decide_inventory(
        market="m", held_qty=D("0"), rounds_used=1,
        reopen_kwargs=reopen_kw(single_outcome_usd=D("-0.03")),
        weighting_kwargs=_no_market(weight_kw(p_up_independent=None)))
    assert r["selected"] == UD.REPLENISH
    assert r["weighting"]["take"] is False
    assert r["weighting"]["reason"] == "NO_INDEPENDENT_PROBABILITY_ESTIMATE"


def test_the_dispatcher_selects_accumulation_only_on_a_real_edge():
    r = UD.decide_inventory(
        market="m", held_qty=D("0"), rounds_used=0,
        reopen_kwargs=reopen_kw(),
        weighting_kwargs=_no_market(
            weight_kw(p_up_independent=D("0.80"))))
    assert r["selected"] == UD.ACCUMULATE_FAVORED
    assert r["selected_reason"] == "UNEQUAL_ROUND_BEATS_BALANCED"


def test_overweighting_the_cheaper_side_is_the_unfavoured_acquisition():
    """Heavier on the side the estimate does NOT favour is a hedge."""
    r = UD.decide_inventory(
        market="m", held_qty=D("0"), rounds_used=0,
        reopen_kwargs=reopen_kw(),
        weighting_kwargs=_no_market(
            weight_kw(favoured_side="UP", n_favoured=D("1"),
                      n_other=D("2"), p_up_independent=D("0.80"))))
    if r["weighting"].get("take"):
        assert r["selected"] == UD.ACQUIRE_UNFAVORED
    else:
        assert r["selected"] == UD.OPEN


def test_a_weighting_that_raises_cannot_select_an_unequal_round(monkeypatch):
    monkeypatch.setattr(UD, "weighted_inventory_decision",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = UD.decide_inventory(
        market="m", held_qty=D("0"), rounds_used=0,
        reopen_kwargs=reopen_kw(),
        weighting_kwargs=_no_market(weight_kw()))
    assert r["selected"] == UD.OPEN
    assert r["weighting"]["reason"] == "WEIGHTING_RAISED"


def test_the_sides_come_from_the_probe_module_not_a_local_string():
    assert TS.UP == "UP" and TS.DOWN == "DOWN"
    r = UD.weighted_inventory_decision(**weight_kw(favoured_side="up",
                                                   p_up_independent=None))
    assert r["favoured_side"] == TS.UP, "case is normalised"
