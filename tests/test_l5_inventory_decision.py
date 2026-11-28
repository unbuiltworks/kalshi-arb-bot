"""The ONE inventory-aware decision process, and the properties it must hold.

Every number in the "real windows" section is taken verbatim from the live
records of 2026-11-06 archived under `analysis/l5/issue195/`, so these are
not invented scenarios: they are the two windows that settled -$0.97.

Nothing here opens a socket, uses a credential or submits anything.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_inventory_decision as ID
from src import l5_two_sided_probe as TSP


# ---------------------------------------------------------------- helpers

def _rank(**kw):
    base = dict(
        up_qty="2", dn_qty="1", up_cost_px="0.62", dn_cost_px="0.35",
        deficient_side="DOWN", deficit="1", min_qty="0.01",
        taker_rungs=[["0.33", "448"], ["0.34", "516"], ["0.35", "870"]],
        depth_haircut="0.25", maker_own_px="0.32",
        maker_queue_ahead="120.84", maker_fill_rate="0.3",
        seconds_to_settlement="760", up_mid_wire="0.675",
        cash_headroom="10.41", worst_case_ceiling="1.50",
        book_age_s="0.06",
    )
    base.update(kw)
    return ID.rank(**base)


def _close(got, want, tol="0.01"):
    """Decimal-safe approximate equality. pytest.approx will not subtract a
    Decimal from a float, and every figure here is a Decimal on purpose."""
    return abs(D(str(got)) - D(str(want))) <= D(str(tol))


def _alt(out, name):
    for a in out["alternatives"]:
        if a["alternative"] == name:
            return a
    raise AssertionError("%s was not priced at all" % name)


# =====================================================================
# 1. THE FOUR ALTERNATIVES ARE ALWAYS PRICED OR ALWAYS NAMED UNAVAILABLE.
# =====================================================================

def test_every_alternative_appears_in_every_record():
    out = _rank()
    assert [a["alternative"] for a in out["alternatives"]] == [
        ID.HOLD, ID.TAKER_COMPLETE, ID.TAKER_PARTIAL, ID.MAKER_COMPLETE]
    assert set(ID.ALTERNATIVES) == {ID.HOLD, ID.MAKER_COMPLETE,
                                    ID.TAKER_COMPLETE, ID.TAKER_PARTIAL}


def test_an_unavailable_alternative_is_named_never_silently_dropped():
    out = _rank(taker_rungs=None, taker_own_px=None,
                taker_displayed_qty=None)
    t = _alt(out, ID.TAKER_COMPLETE)
    assert t["available"] is False
    assert t["reason"] == "TAKER_BOOK_UNREADABLE"


def test_the_maker_alternative_refuses_rather_than_invent_a_fill_rate():
    out = _rank(maker_fill_rate=None)
    m = _alt(out, ID.MAKER_COMPLETE)
    assert m["available"] is False
    assert m["reason"] == "MAKER_FILL_RATE_UNKNOWN"


@pytest.mark.parametrize("missing,reason", [
    ({"seconds_to_settlement": None}, "SETTLEMENT_TIME_UNKNOWN"),
    ({"maker_queue_ahead": None}, "QUEUE_AHEAD_UNKNOWN"),
    ({"maker_own_px": None}, "MAKER_PRICE_UNREADABLE"),
])
def test_each_missing_maker_input_has_its_own_named_refusal(missing, reason):
    assert _alt(_rank(**missing), ID.MAKER_COMPLETE)["reason"] == reason


# =====================================================================
# 2. HOLD IS THE BASELINE AND WINS UNLESS SOMETHING STRICTLY BEATS IT.
# =====================================================================

def test_hold_wins_when_no_alternative_improves_the_worse_outcome():
    """Completing at 0.99 own price on a leg bought at 0.05 cannot help."""
    out = _rank(up_qty="1", dn_qty="0", up_cost_px="0.05", dn_cost_px="0",
                taker_rungs=[["0.99", "500"]], maker_own_px="0.99")
    assert out["chosen"] == ID.HOLD
    assert "strictly improves" in out["why_hold"]
    assert _alt(out, ID.TAKER_COMPLETE)["strictly_improves_worst"] is False


def test_an_alternative_that_does_not_improve_is_never_chosen():
    """Nothing improves the worse outcome when the complement is dearer than
    the dollar the pair redeems net of the first leg."""
    out = _rank(up_qty="1", dn_qty="0", up_cost_px="0.90", dn_cost_px="0",
                deficit="1", taker_rungs=[["0.99", "500"]],
                maker_own_px="0.99", maker_fill_rate="1",
                maker_queue_ahead="0", cash_headroom="1000")
    for name in (ID.TAKER_COMPLETE, ID.MAKER_COMPLETE):
        a = _alt(out, name)
        if a["available"] and a["strictly_improves_worst"]:
            pytest.fail("%s should not improve here: %r" % (name, a))
    assert out["chosen"] == ID.HOLD


def test_a_tiny_worst_case_trim_that_costs_the_whole_upside_is_refused():
    """WHY THE EV BUDGET IS NOT OPTIONAL, with the arithmetic that shows it.

    Hold 1 UP bought at 0.10. The worse outcome is only -$0.11, because a
    cheap leg cannot lose much. Completing at 0.97 trims that to -$0.09 -- a
    STRICT improvement of two cents -- and gives up $0.655 of expected value
    to do it, because the cheap leg's upside was the whole position.

    So worst-case ranking alone would take it. The completion-loss budget is
    what declines it, and without the budget this module would be a machine
    for paying ninety-seven cents to buy two cents of certainty.
    """
    kw = dict(up_qty="1", dn_qty="0", up_cost_px="0.10", dn_cost_px="0",
              deficit="1", taker_rungs=[["0.97", "500"]],
              maker_own_px=None, cash_headroom="1000")
    unbudgeted = _rank(**kw)
    t = _alt(unbudgeted, ID.TAKER_COMPLETE)
    assert t["strictly_improves_worst"] is True
    assert _close(t["worst_improvement_usd"], "0.02")
    assert _close(t["ev_giveup_usd"], "0.655", "0.01")
    assert unbudgeted["chosen"] == ID.TAKER_COMPLETE      # worst case alone

    budgeted = _rank(max_ev_giveup_usd="0.10", **kw)
    assert _alt(budgeted, ID.TAKER_COMPLETE)["reason"] == (
        "EV_GIVEUP_ABOVE_BUDGET")
    assert budgeted["chosen"] == ID.HOLD


def test_hold_is_never_itself_a_runner():
    out = _rank()
    assert ID.HOLD not in out["considered"]
    assert _alt(out, ID.HOLD)["strictly_improves_worst"] is False


# =====================================================================
# 3. THE RANKING KEY IS EXPECTED WORST CASE, NOT EXPECTED VALUE.
#    This is the bug the first version of this module had.
# =====================================================================

def test_ranking_is_on_expected_worst_case_and_says_so():
    out = _rank()
    assert out["ranking_basis"] == ID.BASIS_EXPECTED_WORST
    assert "BOUND, not the objective" in out["expected_value_role"]


def test_a_hopeful_maker_quote_loses_to_a_certain_completion():
    """THE REGRESSION THIS PINS. Ranking by expected value chose a maker
    quote with a 0.24% fill probability over a certain completion, because
    holding has positive EV for a programme with no view on outcomes."""
    out = _rank()
    maker, taker = _alt(out, ID.MAKER_COMPLETE), _alt(out, ID.TAKER_COMPLETE)
    # the maker price is BETTER if it fills ...
    assert D(maker["worst_after"]) > D(taker["worst_after"])
    # ... and its EV is better too, which is exactly the trap
    assert D(maker["ev_giveup_usd"]) < D(taker["ev_giveup_usd"])
    # but it removes almost no exposure, so it loses
    assert D(maker["expected_worst_improvement_usd"]) < D("0.01")
    assert D(taker["expected_worst_improvement_usd"]) > D("0.6")
    assert out["chosen"] == ID.TAKER_COMPLETE


def test_a_maker_quote_that_actually_fills_is_preferred():
    """Maker-first is not abandoned: with a credible fill probability and a
    materially cheaper price, resting wins on the same key."""
    out = _rank(maker_own_px="0.20", maker_queue_ahead="0",
                maker_fill_rate="0.99", expected_volume="500")
    m, t = _alt(out, ID.MAKER_COMPLETE), _alt(out, ID.TAKER_COMPLETE)
    assert D(m["fill_probability"]) > D("0.9")
    assert D(m["expected_worst_after"]) > D(t["expected_worst_after"])
    assert out["chosen"] == ID.MAKER_COMPLETE


def test_expected_worst_case_is_the_fill_weighted_improvement():
    out = _rank()
    for a in out["alternatives"]:
        if not a.get("available") or a["alternative"] == ID.HOLD:
            continue
        p = D(a["fill_probability"])
        wb, wa = D(a["worst_before"]), D(a["worst_after"])
        assert _close(a["expected_worst_after"], p * wa + (D(1) - p) * wb,
                      "0.000000001")


# =====================================================================
# 4. THE COMPLETION-LOSS BUDGET.
# =====================================================================

def test_the_ev_giveup_budget_refuses_an_otherwise_good_completion():
    out = _rank(max_ev_giveup_usd="0.001", maker_own_px=None)
    t = _alt(out, ID.TAKER_COMPLETE)
    assert t["available"] is False
    assert t["reason"] == "EV_GIVEUP_ABOVE_BUDGET"
    assert out["chosen"] == ID.HOLD


def test_a_refused_alternative_is_still_priced_in_the_record():
    """The owner's question -- what did removing that worst case cost in
    expected value -- cannot be answered from a row never computed."""
    t = _alt(_rank(max_ev_giveup_usd="0.001", maker_own_px=None),
             ID.TAKER_COMPLETE)
    assert t["qty"] == "1" and t["own_px"] == "0.33"
    assert D(t["worst_improvement_usd"]) > D("0.6")
    assert t["ev_giveup_usd"] is not None


def test_an_unset_budget_is_reported_as_unset_not_as_satisfied():
    out = _rank(max_ev_giveup_usd=None)
    assert out["ev_budget_applied"] is False
    assert out["max_ev_giveup_usd"] is None


def test_a_budget_with_no_mid_to_measure_against_refuses():
    """A bound we cannot evaluate is not permission to act."""
    out = _rank(up_mid_wire=None, max_ev_giveup_usd="0.10")
    t = _alt(out, ID.TAKER_COMPLETE)
    assert t["available"] is False
    assert t["reason"] == "EV_GIVEUP_UNMEASURABLE_NO_MID"
    assert out["chosen"] == ID.HOLD


def test_with_no_budget_and_no_mid_the_worst_case_still_decides():
    out = _rank(up_mid_wire=None)
    assert out["chosen"] == ID.TAKER_COMPLETE
    assert out["settlement_probability_up"] is None


# =====================================================================
# 5. "REDUCE THE ORIGINAL EXPOSURE" IS THE SAME ORDER ON THIS VENUE.
# =====================================================================

def test_reducing_and_completing_are_the_same_order_on_this_venue():
    """The book is quoted in YES terms and a BUY of DOWN is wired as a SELL
    of YES, so selling the held UP into the bid at b and buying the DOWN
    complement at own price 1-b are the same arithmetic. Asserted rather
    than left as a claim in the docstring."""
    pu, bid = D("0.62"), D("0.67")
    # (a) sell 1 UP into the bid
    sell_net = bid - pu
    # (b) buy 1 DOWN at own price 1 - bid, hold the matched pair to $1
    own_down = D(1) - bid
    buy_net = D(1) - pu - own_down
    assert sell_net == buy_net
    # and the ranker's own figure agrees: a matched pair's worse outcome is
    # the same number on both settlement branches
    out = _rank(up_qty="1", dn_qty="0", up_cost_px=str(pu), dn_cost_px="0",
                deficit="1", taker_rungs=[[str(own_down), "500"]],
                maker_own_px=None, up_mid_wire=str(bid))
    t = _alt(out, ID.TAKER_COMPLETE)
    # OUTCOME INDEPENDENCE IS THE IDENTITY SHOWING ITSELF: once matched, the
    # two settlement branches pay the same, so there is no residual risk left
    # to distinguish "reduced" from "completed".
    assert D(t["up_pnl_after"]) == D(t["dn_pnl_after"])
    # and the level agrees with the gross identity once the shared
    # arithmetic's fee treatment is applied, which charges BOTH legs as
    # takers -- deliberately conservative, since the first leg already filled
    # as a maker.
    fees = (TSP.fee(TSP.TAKER_THETA, D(1), pu)
            + TSP.fee(TSP.TAKER_THETA, D(1), own_down))
    assert _close(t["worst_after"], sell_net - fees, "0.000000001")


def test_a_matched_pair_is_outcome_independent():
    out = _rank(up_qty="1", dn_qty="0", up_cost_px="0.40", dn_cost_px="0",
                deficit="1", taker_rungs=[["0.55", "900"]],
                maker_own_px=None)
    t = _alt(out, ID.TAKER_COMPLETE)
    assert D(t["up_pnl_after"]) == D(t["dn_pnl_after"])
    assert t["both_positive_after"] in (True, False)


# =====================================================================
# 6. PARTIAL SIZE IS A REAL ALTERNATIVE, NOT A CONSOLATION PRIZE.
# =====================================================================

def test_a_partial_cross_is_priced_at_a_size_the_haircut_book_serves():
    out = _rank(deficit="100", up_qty="100", dn_qty="0", up_cost_px="0.50",
                cash_headroom="1000", worst_case_ceiling="1000",
                maker_own_px=None,
                taker_rungs=[["0.33", "40"], ["0.60", "4000"]])
    p = _alt(out, ID.TAKER_PARTIAL)
    assert p["available"] is True
    assert D(p["qty"]) < D("100")
    # a smaller cross at the cheap rung beats dragging the VWAP up the book
    assert D(p["own_px"]) < D(_alt(out, ID.TAKER_COMPLETE)["own_px"])


def test_a_partial_cross_can_be_the_choice_when_the_full_size_is_not():
    out = _rank(deficit="100", up_qty="100", dn_qty="0", up_cost_px="0.50",
                cash_headroom="1000", worst_case_ceiling="1000",
                taker_rungs=[["0.30", "80"], ["0.95", "100000"]],
                maker_own_px=None)
    assert out["chosen"] in (ID.TAKER_PARTIAL, ID.TAKER_COMPLETE)
    full, part = _alt(out, ID.TAKER_COMPLETE), _alt(out, ID.TAKER_PARTIAL)
    if out["chosen"] == ID.TAKER_PARTIAL:
        assert D(part["expected_worst_after"]) > D(
            full["expected_worst_after"])


def test_no_cross_ever_exceeds_the_deficit():
    out = _rank(deficit="1", taker_rungs=[["0.33", "100000"]])
    for name in (ID.TAKER_COMPLETE, ID.TAKER_PARTIAL, ID.MAKER_COMPLETE):
        a = _alt(out, name)
        if a.get("available"):
            assert D(a["qty"]) <= D("1"), name


def test_the_haircut_is_applied_before_depth_is_believed():
    out = _rank(deficit="100", up_qty="100", dn_qty="0", up_cost_px="0.50",
                cash_headroom="1000", worst_case_ceiling="1000",
                taker_rungs=[["0.33", "200"]],
                depth_haircut="0.25", maker_own_px=None)
    t = _alt(out, ID.TAKER_COMPLETE)
    assert D(t["qty"]) == D("50")          # 200 * 0.25, not 100
    assert t["clears_in_full"] is False
    assert D(t["fill_probability"]) == D("0.5")


# =====================================================================
# 7. ADVERSE SELECTION: THE SIGN, AND NO FREE LUNCH.
# =====================================================================

@pytest.mark.parametrize("p_fill", ["0.1", "0.3", "0.5", "0.9", "0.99"])
def test_the_adverse_shift_preserves_the_unconditional_prior(p_fill):
    prior, pf, shift = D("0.60"), D(p_fill), D("0.05")
    c = ID.adverse_conditioned(prior, pf, shift)
    assert _close(pf * c["given_fill"] + (D(1) - pf) * c["given_no_fill"],
                  prior, "0.000000001")


def test_a_fill_is_the_adverse_branch_and_a_non_fill_the_favourable_one():
    c = ID.adverse_conditioned(D("0.60"), D("0.4"), D("0.05"))
    assert c["given_fill"] < D("0.60") < c["given_no_fill"]


def test_a_certain_fill_has_no_adverse_selection_to_price():
    """There is no selection when there is no selecting, and a full
    completion is outcome-independent anyway."""
    c = ID.adverse_conditioned(D("0.60"), D(1), D("0.05"))
    assert c["given_fill"] == c["given_no_fill"] == D("0.60")


def test_the_adverse_shift_is_reported_as_an_assumption():
    a = _alt(_rank(), ID.MAKER_COMPLETE)["adverse_selection"]
    assert a["shift"] == "0.05"
    assert "ASSUMED" in a["basis"]


def test_a_zero_shift_leaves_both_branches_at_the_prior():
    out = _rank(adverse_shift="0")
    a = _alt(out, ID.MAKER_COMPLETE)["adverse_selection"]
    assert D(a["p_up_given_fill"]) == D(a["prior_up"])
    assert D(a["p_up_given_no_fill"]) == D(a["prior_up"])
    assert D(a["shift_applied"]) == D(0)


def test_the_shift_is_tightened_rather_than_clamped_at_a_high_fill_rate():
    """THE BUG THIS PINS. Clamping the balancing branch at 1.0 broke the
    martingale and quietly invented expected value."""
    c = ID.adverse_conditioned(D("0.60"), D("0.99"), D("0.05"))
    assert c["shift_applied"] < D("0.05")
    assert c["given_no_fill"] <= D(1)
    assert _close(D("0.99") * c["given_fill"]
                  + D("0.01") * c["given_no_fill"], D("0.60"),
                  "0.000000001")


# =====================================================================
# 8. STALE BOOKS, LIMITS, AND EVERY OTHER FAIL-CLOSED PATH.
# =====================================================================

def test_a_gapped_book_makes_every_action_unavailable():
    out = _rank(gap_suspected=True)
    assert out["book_refused"] == "BOOK_GAP_SUSPECTED"
    assert out["chosen"] == ID.HOLD
    for name in (ID.TAKER_COMPLETE, ID.MAKER_COMPLETE):
        assert _alt(out, name)["available"] is False


def test_a_stale_book_makes_every_action_unavailable():
    out = _rank(book_age_s="30", max_book_age_s="5")
    assert out["book_refused"].startswith("BOOK_STALE")
    assert out["chosen"] == ID.HOLD


def test_an_unknown_book_age_fails_closed():
    out = _rank(book_age_s=None)
    assert out["book_refused"] == "BOOK_AGE_UNKNOWN"
    assert out["chosen"] == ID.HOLD


def test_the_cash_headroom_refuses_rather_than_resizes():
    out = _rank(cash_headroom="0.01")
    t = _alt(out, ID.TAKER_COMPLETE)
    assert t["available"] is False
    assert t["reason"] == "CASH_HEADROOM_EXCEEDED"


def test_the_worst_case_ceiling_is_read_not_computed():
    out = _rank(up_qty="100", dn_qty="0", up_cost_px="0.90", deficit="100",
                taker_rungs=[["0.99", "100000"]], worst_case_ceiling="1.00",
                maker_own_px=None, cash_headroom="100000")
    t = _alt(out, ID.TAKER_COMPLETE)
    assert t["available"] is False
    assert t["reason"] == "WORST_CASE_CEILING_EXCEEDED"


@pytest.mark.parametrize("bad,reason", [
    ({"deficient_side": "SIDEWAYS"}, "DEFICIENT_SIDE_UNKNOWN"),
    ({"up_cost_px": "nonsense"}, "HELD_INVENTORY_UNREADABLE"),
    ({"deficit": "0"}, "NO_DEFICIT_TO_COMPLETE"),
    ({"min_qty": "0"}, "QUANTITY_INCREMENT_UNKNOWN"),
])
def test_an_unreadable_input_refuses_with_its_own_name(bad, reason):
    out = _rank(**bad)
    assert out["ok"] is False and out["reason"] == reason
    assert out["chosen"] == ID.HOLD


def test_unreadable_rungs_are_not_an_empty_book():
    out = _rank(taker_rungs=[["not-a-price", "1"]])
    assert _alt(out, ID.TAKER_COMPLETE)["reason"] == "TAKER_BOOK_UNREADABLE"


def test_rank_never_raises_on_any_junk_input():
    for junk in (None, "", "x", [], {}, object()):
        out = ID.rank(up_qty=junk, dn_qty=junk, up_cost_px=junk,
                      dn_cost_px=junk, deficient_side=junk, deficit=junk,
                      min_qty=junk)
        assert out["chosen"] == ID.HOLD and out["ok"] is False


# =====================================================================
# 9. THE REAL WINDOWS. Numbers from analysis/l5/issue195/.
# =====================================================================

def test_0300z_at_plus_132s_chooses_a_completion_that_removes_63_cents():
    """L5_TS_COMPLETION, 2026-11-06T03:02:12Z, verbatim:

        up_qty 2 @ 0.6200, dn_qty 1 @ 0.3500, deficit 1 DOWN,
        price 0.3500, displayed_depth 1312.39, taker_fee_usd 0.02,
        worst_before -0.6500, worst_after -0.0200,
        worst_improvement_usd 0.6300
    """
    out = _rank(up_qty="2", dn_qty="1", up_cost_px="0.6200",
                dn_cost_px="0.3500", deficit="1",
                taker_rungs=[["0.3500", "1312.39"]], maker_own_px=None,
                max_ev_giveup_usd="0.10")
    assert out["chosen"] == ID.TAKER_COMPLETE
    t = _alt(out, ID.TAKER_COMPLETE)
    assert _close(out["worst_before"], "-0.65")
    assert _close(t["worst_after"], "-0.02")
    assert _close(t["worst_improvement_usd"], "0.63")
    assert _close(t["fee_usd"], "0.0162", "0.005")


def test_0315z_decays_from_thirty_five_cents_to_four_and_the_record_shows_it():
    """Both ends of the live window, from the same archived decisions:
    the first evaluation offered $0.35 of worst-case improvement at a 0.65
    completion, the last offered $0.04 at 0.95."""
    early = _rank(up_qty="1", dn_qty="0", up_cost_px="0.3800",
                  dn_cost_px="0", deficit="1",
                  taker_rungs=[["0.6300", "23.34"]], maker_own_px=None,
                  depth_haircut="1")
    late = _rank(up_qty="1", dn_qty="0", up_cost_px="0.3800",
                 dn_cost_px="0", deficit="1",
                 taker_rungs=[["0.9500", "809"]], maker_own_px=None)
    e = _alt(early, ID.TAKER_COMPLETE)
    l = _alt(late, ID.TAKER_COMPLETE)
    assert _close(e["worst_improvement_usd"], "0.35", "0.02")
    assert _close(l["worst_improvement_usd"], "0.04", "0.02")
    # acting early is worth nearly nine times acting late
    assert D(e["worst_improvement_usd"]) > D(l["worst_improvement_usd"]) * 8


def test_the_0315z_late_completion_fails_the_deployed_margin_floor():
    """The layers compose: the ranker offers it, and the taker policy's
    $0.25 margin floor is what declines a four-cent rescue."""
    from src import l5_taker_completion_policy as TCP
    late = _rank(up_qty="1", dn_qty="0", up_cost_px="0.3800",
                 dn_cost_px="0", deficit="1",
                 taker_rungs=[["0.9500", "809"]], maker_own_px=None)
    t = _alt(late, ID.TAKER_COMPLETE)
    assert D(t["worst_after"]) < TCP.DEFAULT_MARGIN_FLOOR_USD
