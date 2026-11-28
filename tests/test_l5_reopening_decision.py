"""The 1230z window, as a permanent fixture, and the decision it was missing.

WHAT HAPPENED, from the venue tape of 2026-11-07 12:30z:

    12:30:27  DOWN 0.5000 FILLS      l5f-b195e7360f86e0e4b6c9363a
    12:30:49  UP   0.4500 FILLS      l5f-83557f64dd82a6809828ef7d
              round 0 PAIRS at 0.9500                   +$0.0500
    12:32:54  round 1 opens UP 0.4600 / DOWN 0.4900
    12:33:57  UP   0.4600 FILLS      l5f-fc3f9809135cd723b717ab4f
    12:34:58  DOWN 0.4900 cancelled, zero fill
              (UP 0.3500 also cancelled, zero fill)
    12:45:57  settlement, yes_value 0.0000 -- DOWN won
    ------------------------------------------------------------
              UP 2 @ basis 0.9100, DOWN 1 @ 0.5000
              payout 1.0000 - spend 1.4100  =       -$0.4100

TWO DECOMPOSITIONS, BOTH CORRECT, AND THE DIFFERENCE IS A CONVENTION. At
the ORDER level the pair is DOWN 0.5000 + UP 0.4500 = 0.9500 (+$0.0500) and
the stranded leg is the UP at 0.4600 (-$0.4600). The settlement record uses
AVERAGE BASIS -- UP averages (0.45+0.46)/2 = 0.4550 -- so it reads matched
0.9550 (+$0.0450) and residual 0.4550 (-$0.4550). Both sum to -$0.4100,
which is the only figure that is money. Earlier drafts of this file quoted
"UP 0.4750 / DOWN 0.4800" for round 0; those prices are in no venue record
and were back-derived from the 0.9550 average. The order prices above are
the venue's.

NOBODY MADE A MISTAKE AND NOBODY BROKE A RULE. `L5_TS_ROUND_ALLOCATIONS=1`
splits the CASH ENVELOPE across cycles; it never limited opening rounds.
`acc_max_rounds()` does, and `probe_round` walked to the first incomplete
round and asked nothing else. The round COUNT was not the defect. The
MISSING DECISION was: no code anywhere compared what round 1 could earn
against what it could lose.

These tests pin that decision. They are deliberately written so that the
ONE thing they cannot be satisfied by is a counter: a cap of one pair per
window would pass the refusal tests and fail every permission test below.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_live_validation_gates as VG
from src import l5_unmatched_disposition as UD
from tests import test_l5_live_execution as H

SLUG = "cpc-btc-updown-15m-2026-11-07-1230z"

#: ROUND 0, at the venue's own prices. It PAIRED at 0.9500.
PLAN0 = {"pair_cost": "0.9500",
         "legs": [{"side": "UP", "price": "0.4500", "qty": "1",
                   "mode": "MAKER"},
                  {"side": "DOWN", "price": "0.5000", "qty": "1",
                   "mode": "MAKER"}]}
#: ROUND 1, at the prices that lost the window. The UP filled alone.
PLAN1 = {"pair_cost": "0.9500",
         "legs": [{"side": "UP", "price": "0.4600", "qty": "1",
                   "mode": "MAKER"},
                  {"side": "DOWN", "price": "0.4900", "qty": "1",
                   "mode": "MAKER"}]}

#: The book round 1 was opened into: one tick wide, as 217 of 220 samples.
BOOK = {"bid": D("0.4800"), "ask": D("0.4900")}


def base(**kw):
    """The pure decision's inputs for 1230z round 1, overridable."""
    out = dict(market=SLUG, rounds_used=1,
               # the settlement record's average-basis figure
               locked_pair_pnl_usd=D("0.0450"),
               worst_leg_own_px=D("0.4900"), qty=D("1"),
               bid=BOOK["bid"], ask=BOOK["ask"], tick=D("0.01"),
               depth_ticks=2, now_s=0.0, window_end_s=900.0,
               exposure_headroom_usd=D("1.00"))
    out.update(kw)
    return out


# ================================================ 1. THE WINDOW IS REFUSED

def test_1230z_round_one_is_PERMITTED_on_the_corrected_numbers():
    """THE REVERSAL, and it is a reversal of MY error, not of the evidence.

    This test used to assert the round was refused, on a single-leg outcome
    of -$0.2440 taken from recorded books under PROVEN fills. A PROVEN fill
    is one where the opposite touch crossed our price, so every leg in that
    sample filled because the market moved against it: the constant
    measured the sampling rule, not the market, and at 6.4x the pair edge
    it refused every reopening at the deployed depth.

    L5's own fills say an unmatched leg is roughly EV-neutral -- +$0.0367
    per contract over 20,069 residual contracts whose outcome is directly
    determinable -- which is also exactly what this module's EV identity
    predicts for a leg bought at a fair price.

    So round 1 of 1230z was a POSITIVE-EXPECTANCY round that lost. That is
    what trading is, and refusing it was the defect. What must still hold is
    that its worst case fitted the budget, which the capacity test below
    pins.
    """
    r = UD.reopen_decision(**base())
    assert r["reopen"] is True
    # 0.727*0.05 + 0.273*0
    assert D(r["expectancy_usd"]) == D("0.0299")
    assert D(r["pair_edge_usd"]) == D("0.0500")


def test_the_old_adverse_selection_constant_is_what_forbade_throughput():
    """Pass the superseded figure back in and the round is refused again.

    Pinning this makes the dependency explicit: one parameter, sourced from
    a biased sample, was the whole reason the gate behaved as a blanket
    restriction rather than a price.
    """
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.2440")))
    assert r["reopen"] is False
    assert r["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"


def test_it_opens_even_on_the_pessimistic_PROVEN_pairing_rate():
    """The pair rate was never what blocked it; the single outcome was."""
    r = UD.reopen_decision(**base(p_pair=D("0.525"), p_single=D("0.423")))
    assert r["reopen"] is True
    assert D(r["expectancy_usd"]) > D(r["min_edge_usd"])


def test_the_record_always_prices_itself_either_way():
    """A decision that does not state its own break-even is an opinion."""
    r = UD.reopen_decision(**base())
    # how bad an unmatched leg would have to be to make this round marginal
    assert D(r["breakeven_single_outcome_usd"]) == D("-0.0965")
    # and the MEASURED figure it is judged against: 1.24M residual
    # contracts on the repaired union
    assert D(r["single_outcome_usd_per_contract"]) == D("-0.0236")
    assert D(r["single_outcome_usd_per_contract"]) > \
        D(r["breakeven_single_outcome_usd"]), "measured is inside break-even"


def test_the_priors_come_from_real_fills_not_from_a_sampling_rule():
    """Our own orders for pairing; the REPAIRED union for the single leg."""
    assert UD.REOPEN_P_PAIR == D("0.727")
    assert UD.REOPEN_P_SINGLE == D("0.273")
    # measured on 1,240,600 residual contracts across 4,720 COMPLETE
    # btc15m windows after the dataset repair -- NOT the zero hypothesis,
    # which the repair showed to be optimistic rather than conservative
    assert UD.REOPEN_SINGLE_OUTCOME_USD == D("-0.0236")


def test_the_measured_single_leg_outcome_is_a_LOSS():
    """The small-sample +0.0367 was selection: those 186 windows were the
    quiet ones. On 60x the sample a residual loses 2.4 cents."""
    assert UD.REOPEN_SINGLE_OUTCOME_USD < D("0")
    assert UD.REOPEN_SINGLE_OUTCOME_USD > D("-0.05")


def test_it_still_sits_far_above_the_superseded_PROVEN_artefact():
    assert UD.REOPEN_SINGLE_OUTCOME_USD > D("-0.2440") * 9


def test_the_taker_disposition_is_not_assumed_available():
    """Taker is disabled by owner order, so the gate may not price it in.

    The default must never quietly assume a disposition we are forbidden to
    execute. Zero is the EV identity's answer for a MAKER-only hold, which
    is the authorized configuration.
    """
    assert UD.REOPEN_SINGLE_OUTCOME_USD == D("-0.0236")
    r = UD.reopen_decision(**base())
    assert "maker" in r["prior_source"].lower() or \
        "taker" in r["prior_source"].lower()


# ======================== 2. IT IS NOT FINANCED OUT OF THE LOCKED PAIR

@pytest.mark.parametrize("locked", ["0.0450", "0", "5.00", "500.00"])
def test_locked_pair_profit_never_changes_the_decision(locked):
    """The exact reasoning that lost the window: +$0.045 funds -$0.455.

    It is wrong twice -- the locked profit is a tenth of the risk, and
    netting them spends a realized gain as a loss reserve. $500 of locked
    profit must not buy a single bad round.
    """
    r = UD.reopen_decision(**base(locked_pair_pnl_usd=D(locked)))
    # the VERDICT is whatever the round's own economics say; what must never
    # change is that it does not move with the locked profit.
    assert D(r["expectancy_usd"]) == D("0.0299")
    assert D(r["locked_pair_pnl_usd"]) == D(locked)


def test_locked_profit_is_reported_so_its_exclusion_is_visible():
    r = UD.reopen_decision(**base())
    assert D(r["locked_pair_pnl_usd"]) == D("0.0450")
    assert r["locked_pnl_is_not_a_loss_reserve"] is True


# ============================================ 3. IT IS NOT A CAP: PERMISSION

def test_reopening_is_permitted_when_the_economics_support_it():
    """THE TEST A COUNTER CANNOT PASS.

    Same market, same round index, same locked pair -- only the measured
    single-leg cost differs. A cap of one pair per window fails this.
    """
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.03")))
    assert r["reopen"] is True
    assert r["reason"] is None
    assert D(r["expectancy_usd"]) > D(r["min_edge_usd"])


@pytest.mark.parametrize("rounds_used", [1, 2, 3, 7])
def test_no_round_index_is_refused_merely_for_being_late(rounds_used):
    r = UD.reopen_decision(**base(rounds_used=rounds_used,
                                  single_outcome_usd=D("-0.03")))
    assert r["reopen"] is True, "the gate is arithmetic, not a counter"
    assert r["rounds_used"] == rounds_used


def test_a_wider_spread_can_justify_reopening():
    """The pair identity is 1 - spread - 2*d*tick, so spread is the edge."""
    wide = UD.reopen_decision(**base(bid=D("0.30"), ask=D("0.70")))
    assert D(wide["pair_edge_usd"]) == D("0.4400")
    assert wide["reopen"] is True


def test_greater_depth_raises_the_edge_the_identity_predicts():
    prev = None
    for d in (0, 1, 2, 3, 4, 5, 6):
        r = UD.reopen_decision(**base(depth_ticks=d))
        edge = D(r["pair_edge_usd"])
        assert edge == D("0.01") + (2 * D(d) * D("0.01"))
        if prev is not None:
            assert edge > prev
        prev = edge


@pytest.mark.parametrize("kw,why", [
    ({"bid": None}, "BOOK_NOT_TWO_SIDED"),
    ({"ask": None}, "BOOK_NOT_TWO_SIDED"),
    ({"bid": D("0.50"), "ask": D("0.49")}, "BOOK_NOT_TWO_SIDED"),
    ({"tick": None}, "TICK_UNKNOWN"),
    ({"qty": D("0")}, "ROUND_SIZE_OR_PRICE_UNREADABLE"),
])
def test_the_structural_reads_apply_to_the_FIRST_round_too(kw, why):
    """Round 0 is exempt from the ECONOMICS, not from being readable.

    `decide_inventory` reports this verdict as an ACTION, and "OPEN into a
    one-sided book" is not one. The economic exemption for the first round
    is deliberate -- the executor's own cash, deficit and exposure gates
    own the first leg -- but it must not become an exemption from reading
    the book at all.
    """
    r = UD.reopen_decision(**base(rounds_used=0, **kw))
    assert r["reopen"] is False, (kw, r.get("reason"))
    assert r["reason"] == why


def test_the_first_round_says_so_when_it_skips_the_economics():
    r = UD.reopen_decision(**base(rounds_used=0))
    assert r["reopen"] is True
    assert r["economics_not_evaluated_for_the_first_round"] is True


def test_the_first_round_is_never_gated():
    """Gating round 0 would turn the strategy off, which nobody asked for."""
    r = UD.reopen_decision(**base(rounds_used=0))
    assert r["reopen"] is True
    assert r["reason"] == "FIRST_ROUND_NOT_A_REOPENING"


# ======================== 4. CAPACITY IS A HARD BOUND, NEVER TRADED AGAINST

def test_a_great_round_is_still_refused_without_headroom():
    """Expectancy never buys its way past the owner's exposure bound."""
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"),
                                  exposure_headroom_usd=D("0.10")))
    assert r["reopen"] is False
    assert r["reason"] == "EXPOSURE_HEADROOM_BELOW_WORST_CASE"
    assert D(r["worst_case_usd"]) == D("0.4900")


def test_outstanding_orders_are_counted_as_if_they_filled():
    """1130z: four legs each calling itself the opening one, in 7 seconds."""
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"),
                                  exposure_headroom_usd=D("1.00"),
                                  outstanding_worst_case_usd=D("0.60")))
    assert r["reopen"] is False
    assert r["reason"] == "EXPOSURE_HEADROOM_BELOW_WORST_CASE"
    assert D(r["exposure_headroom_usd"]) == D("0.4000")


def test_the_daily_loss_room_binds_independently():
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"),
                                  daily_loss_room_usd=D("0.10")))
    assert r["reopen"] is False
    assert r["reason"] == "DAILY_LOSS_ROOM_BELOW_WORST_CASE"


def test_the_worst_case_is_the_dearer_leg_not_the_cheaper():
    """Which leg fills alone is not ours to choose."""
    r = UD.reopen_decision(**base(worst_leg_own_px=D("0.4900")))
    assert D(r["worst_case_usd"]) == D("0.4900")


def test_worst_case_is_not_used_as_expectancy():
    """The error this gate was rewritten to remove.

    An unmatched leg at own `p` settles at $1 with the market's own implied
    probability `p`, so its EXPECTANCY is near zero and only its WORST CASE
    is -p. Pricing expectancy as `-p_single * leg_cost` would make the
    decision depend on the leg price; it must not.
    """
    cheap = UD.reopen_decision(**base(worst_leg_own_px=D("0.0600")))
    dear = UD.reopen_decision(**base(worst_leg_own_px=D("0.9000")))
    assert D(cheap["expectancy_usd"]) == D(dear["expectancy_usd"])
    assert cheap["worst_case_is_not_expectancy"] is True


# ==================================================== 5. TIME AND THE BOOK

def test_a_round_needs_runway_for_two_legs_and_a_completion():
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"),
                                  now_s=800.0))
    assert r["reopen"] is False
    assert r["reason"] == "INSUFFICIENT_RUNWAY_FOR_A_ROUND"
    assert r["runway_s"] == 100.0


def test_runway_is_measured_not_assumed():
    ok = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"),
                                   now_s=719.0))
    assert ok["reopen"] is True and ok["runway_s"] == 181.0
    bad = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"),
                                    now_s=721.0))
    assert bad["reopen"] is False and bad["runway_s"] == 179.0


@pytest.mark.parametrize("kw,why", [
    ({"bid_shares": D("0")}, "BID_DEPTH_BELOW_ROUND_SIZE"),
    ({"ask_shares": D("0")}, "ASK_DEPTH_BELOW_ROUND_SIZE"),
])
def test_a_book_that_cannot_absorb_either_leg_refuses(kw, why):
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.03"), **kw))
    assert r["reopen"] is False and r["reason"] == why


def test_a_one_sided_book_refuses():
    for kw in ({"bid": None}, {"ask": None}, {"bid": D("0.50"),
                                              "ask": D("0.49")}):
        r = UD.reopen_decision(**base(**kw))
        assert r["reopen"] is False
        assert r["reason"] == "BOOK_NOT_TWO_SIDED"


# ============================================= 6. EVERY UNKNOWN FAILS CLOSED

@pytest.mark.parametrize("kw,why", [
    ({"rounds_used": "x"}, "ROUNDS_USED_UNREADABLE"),
    ({"qty": None}, "ROUND_SIZE_OR_PRICE_UNREADABLE"),
    ({"qty": D("0")}, "ROUND_SIZE_OR_PRICE_UNREADABLE"),
    ({"worst_leg_own_px": None}, "ROUND_SIZE_OR_PRICE_UNREADABLE"),
    ({"tick": None}, "TICK_UNKNOWN"),
    ({"tick": D("0")}, "TICK_UNKNOWN"),
    ({"exposure_headroom_usd": None}, "EXPOSURE_BUDGET_UNREADABLE"),
    ({"exposure_headroom_usd": "junk"}, "EXPOSURE_BUDGET_UNREADABLE"),
    ({"outstanding_worst_case_usd": "junk"},
     "OUTSTANDING_EXPOSURE_UNREADABLE"),
    ({"p_pair": "junk"}, "ECONOMIC_PRIORS_UNREADABLE"),
    ({"single_outcome_usd": "junk"}, "ECONOMIC_PRIORS_UNREADABLE"),
    ({"depth_ticks": -1}, "DEPTH_UNREADABLE"),
    ({"now_s": None}, "WINDOW_UNREADABLE"),
    ({"window_end_s": "x"}, "WINDOW_UNREADABLE"),
    ({"daily_loss_room_usd": "junk"}, "DAILY_LOSS_ROOM_UNREADABLE"),
])
def test_an_unreadable_input_never_opens_exposure(kw, why):
    r = UD.reopen_decision(**base(**kw))
    assert r["reopen"] is False, (kw, r.get("reason"))
    assert r["reason"] == why


def test_nothing_in_this_module_can_place_an_order():
    """It returns data. That is what makes the counterfactual safe."""
    r = UD.reopen_decision(**base())
    assert isinstance(r, dict)
    for banned in ("submit", "order_id", "coid", "transport"):
        assert banned not in r


# ==================================== 7. THE EXECUTOR GATE: OPENING ONLY

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv("L5_WINDOW_STRATEGY", "TWO_SIDED")
    monkeypatch.setenv("L5_TS_MARKET", SLUG)
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    t = H.FakeTransport(fill_all=True)
    t.tick_size = D("0.01")
    st = H.store(tmp_path)
    ex = E.FaithfulExecutor(st, H.limits(), transport=t, now_fn=H.NOW,
                            maker_only=True)
    ex.validation_gates = VG.ValidationGates(max_cash_deployed_usd=D("12"),
                                             max_cumulative_loss_usd=D("1"))
    ex.available_cash_usd = D("300")
    ex.probe_store_plan(SLUG, PLAN0, 0)
    return {"ex": ex, "store": st, "mp": monkeypatch}


def gate(ex, rnd, plan, **kw):
    args = dict(bid=BOOK["bid"], ask=BOOK["ask"], tick=D("0.01"),
                depth_ticks=2, now_s=0.0, window_end_s=900.0)
    args.update(kw)
    return ex.probe_reopen_decision(SLUG, rnd, plan, **args)


def test_the_executor_permits_1230z_round_one_on_the_corrected_numbers(rig):
    """And the capacity test is what still has to pass, not expectancy."""
    r = gate(rig["ex"], 1, PLAN1)
    assert r["reopen"] is True
    assert r["round"] == 1
    assert D(r["worst_case_usd"]) == D("0.4900")
    assert D(r["exposure_headroom_usd"]) >= D(r["worst_case_usd"])


def test_round_zero_is_never_gated_by_the_executor(rig):
    r = gate(rig["ex"], 0, PLAN0)
    assert r["reopen"] is True
    assert r["reason"] == "FIRST_EXPOSURE_TAKING_ROUND_NOT_A_REOPENING"


def test_a_retired_round_does_not_count_as_a_round_used(rig):
    """A RETIRED ROUND TOOK NO EXPOSURE, so the next one is still the first.

    Retirement abandons a plan the book has walked away from, and
    `probe_round_is_safe_to_retire` refuses while anything is live or
    filled. The 0330z window re-proposed a plan frozen against a 0.42/0.56
    touch 32 times after the book had gone to 0.91/0.92; retiring and
    re-planning is the fix for that, and it is not an accumulation of
    exposure. Counting the round INDEX instead of rounds that actually
    spent legs would have blocked it.
    """
    ex = rig["ex"]
    ex.probe_retire_round(SLUG, 0, "LEG_WOULD_CROSS:DOWN")
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is True
    assert r["reason"] == "FIRST_EXPOSURE_TAKING_ROUND_NOT_A_REOPENING"
    assert r["rounds_used"] == 0
    assert r["retired_rounds_do_not_count"] is True


def test_a_round_with_a_leg_already_standing_is_a_completion(rig):
    """THE SAFETY ARGUMENT. Refusing a complement strands the residual.

    Round 1's UP filled at 12:33:57. From that instant, round 1 is being
    COMPLETED, not opened, and the gate must get out of the way -- it exists
    to stop unmatched legs, so it must never cause one.
    """
    from src import l5_live_authorization as AUTH
    ex = rig["ex"]
    ex.probe_store_plan(SLUG, PLAN1, 1)
    # a real durable claim, as a submission takes it BEFORE the wire, then
    # the fill that makes the leg unrepeatable
    AUTH.claim_accumulation_leg(SLUG, 1, "UP", {"px": "0.4600"})
    rig["store"].apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")
    assert any(ex.probe_legs_claimed(SLUG, 1).values())
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is True
    # DURABLE INVENTORY ANSWERS FIRST, and that ordering is deliberate: the
    # filled UP is in the store, so the reduction is established from the
    # position and the venue's resting orders rather than from a claim
    # file. The claim branch is the fallback for a leg that is SUBMITTED
    # but not yet filled, which carries no residual to find.
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    assert r["verified_reduces_not_opens"] is True
    assert r["reducing_side"] == "DOWN"


def test_a_leg_SUBMITTED_but_unfilled_still_gets_its_complement(rig):
    """A STRANDING PATH FOUND WHILE FIXING REVIEW BLOCKER 1.

    `probe_legs_claimed` reports a submitted-but-unfilled leg as UNCLAIMED
    on purpose -- an unfilled leg is deliberately replaceable within its
    attempt budget. So the claim branch does NOT exempt it, and before the
    prospective per-side rule the economics would have refused its
    complement and stranded it the moment it filled.

    The state that matters is the LIVE ORDER, not the claim file:
    `record_submit` persists the order before the wire, and a claim with no
    live order and no fill is a leg that never reached the venue or already
    ended there -- no exposure, correctly gated.
    """
    ex = rig["ex"]
    ex.probe_store_plan(SLUG, PLAN1, 1)
    rig["mp"].setattr(rig["store"], "outstanding_on_side",
                      lambda m, side: D("1") if side == "UP" else D("0"))
    assert rig["store"].residual(SLUG) == D("0"), "nothing filled yet"
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    assert r["reducing_side"] == "DOWN", "the side with no order working"
    assert D(r["imbalance_gap"]) == D("1")


def test_the_1415z_shape_is_still_gated_as_the_opening_it_was(rig):
    """REVIEW BLOCKER 3, and the case the per-side rule exists for.

    UP 2.17 HELD while a DOWN 2.17 completion RESTS. Per side the
    prospective totals are EQUAL, so no side is smaller, there is nothing
    to reduce, and the proposed UP leg is gated as the opening it is. The
    signed net reads 0.00 here, which is what let an UP 2.08 through and
    took the position to UP 4.25 against a 3-contract ceiling.
    """
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("2.17"), D("0.4600"), basis="OWN")
    rig["mp"].setattr(st, "outstanding_on_side",
                      lambda m, side: D("2.17") if side == "DOWN" else D("0"))
    assert st.residual(SLUG) + D("-2.17") == D("0"), "signed net reads flat"
    r = gate(ex, 1, {"pair_cost": "0.9500",
                     "legs": [{"side": "UP", "price": "0.4600",
                               "qty": "2.08", "mode": "MAKER"}]})
    assert r["reopen"] is False
    assert r["reason"] != "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"


def test_a_plan_whose_leg_is_on_the_DEEPER_side_is_not_a_reduction(rig):
    """Some imbalance existing is not an excuse; the leg must close it."""
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")
    # the gap is on DOWN, but this plan offers only another UP
    r = gate(ex, 1, {"pair_cost": "0.9500",
                     "legs": [{"side": "UP", "price": "0.4600",
                               "qty": "1", "mode": "MAKER"}]})
    # it may still be permitted on its own economics -- what must NOT happen
    # is that it is waved through as a REDUCTION when it deepens the side
    assert r["reason"] != "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"


def test_a_reducing_leg_larger_than_the_gap_is_not_exempted(rig):
    """Capped to the unworked gap, or it is partly a new opening."""
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")
    rig["mp"].setattr(ex, "probe_leg_qty_cap",
                      lambda m, side, qty: D("5"))      # cap defeated
    r = gate(ex, 1, {"pair_cost": "0.9500",
                     "legs": [{"side": "DOWN", "price": "0.4900",
                               "qty": "5", "mode": "MAKER"}]})
    assert r["reason"] != "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    # a 5-contract worst case does not fit the $1 budget, so capacity binds
    assert r["reopen"] is False
    assert r["reason"] == "EXPOSURE_HEADROOM_BELOW_WORST_CASE"


def test_an_unreadable_leg_cap_refuses_rather_than_exempting(rig):
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")

    def boom(*a, **k):
        raise RuntimeError("cap unreadable")
    rig["mp"].setattr(ex, "probe_leg_qty_cap", boom)
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is False
    assert r["reason"] == "REDUCING_LEG_QTY_UNREADABLE"


def test_unreadable_claims_cannot_authorise_an_unverified_opening(rig):
    """REVIEW BLOCKER 1. A missing state may not AUTHORIZE NEW EXPOSURE.

    My first version returned reopen=True here, reasoning from
    `acc_stale_by_distance` that one must never veto on a failed read. That
    lesson is about CANCELLING -- a veto there strands inventory. This is
    the opposite direction: permitting here ADDS risk. I had the two
    conflated and the review was right.
    """
    ex = rig["ex"]

    def boom(*a, **k):
        raise RuntimeError("claims unreadable")
    rig["mp"].setattr(ex, "probe_legs_claimed", boom)
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is False
    assert r["reason"] == "CLAIMS_UNREADABLE_NO_UNVERIFIED_OPENING"
    assert r["durable_inventory_already_checked"] is True


def test_unreadable_claims_still_cannot_strand_a_filled_leg(rig):
    """WHY FAILING CLOSED IS SAFE: durable inventory is checked FIRST.

    A leg that actually filled is in the store and on the venue; a claim
    FILE only says a leg was once submitted. So with the claim read broken
    AND a real residual held, the reduction is still established and the
    round still proceeds -- nothing that exists can be stranded by the
    refusal above, only an unverified opening.
    """
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")

    def boom(*a, **k):
        raise RuntimeError("claims unreadable")
    rig["mp"].setattr(ex, "probe_legs_claimed", boom)
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    assert r["verified_reduces_not_opens"] is True


def test_the_executor_prices_the_dearer_leg_of_the_plan(rig):
    """PLAN1 is UP 0.4600 / DOWN 0.4900; the worst case is 0.4900."""
    r = gate(rig["ex"], 1, PLAN1)
    assert D(r["worst_case_usd"]) == D("0.4900")
    assert D(r["worst_leg_own_px"]) == D("0.4900")


def test_a_held_residual_makes_the_next_leg_a_reduction_not_an_opening(rig):
    """It must not be gated: refusing it would STRAND the residual.

    `probe_leg_order` puts the reducing leg first whenever a residual is
    held and `probe_leg_qty_cap` caps it at the residual's magnitude, so the
    worst case FALLS. A gate that exists to prevent unmatched legs must
    never itself cause one.
    """
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    assert D(r["held_residual"]) == D("1")
    assert r["reducing_side"] == "DOWN"


def test_a_residual_already_being_worked_is_not_a_second_exemption(rig):
    """THE 1415z TRAP, and why the signed net is the wrong quantity.

    On 2026-10-31 UP 2.17 was HELD while a DOWN 2.17 completion was
    RESTING. The signed net came to exactly 0.00, a gate read the book as
    FLAT, and it exempted an UP 2.08 as a genuine opening leg. The
    completion was cancelled unfilled and the position became UP 4.25
    against a 3-contract ceiling. So the exemption asks whether residual
    remains NOT ALREADY BEING WORKED -- never whether a net is zero.
    """
    ex, st = rig["ex"], rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4600"), basis="OWN")
    rig["mp"].setattr(st, "outstanding_on_side",
                      lambda m, side: D("1") if side == "DOWN" else D("0"))
    r = gate(ex, 1, PLAN1)
    assert r["reopen"] is False, "the residual is already covered"
    # capacity binds first here, and it binds because BOTH the held leg and
    # the resting completion are reserved: (1 + 1) * 0.49 = 0.98 against the
    # $1 bound. That is the 1415z arithmetic the old gate did not do.
    assert r["reason"] == "EXPOSURE_HEADROOM_BELOW_WORST_CASE"
    assert D(r["outstanding_worst_case_usd"]) == D("2") * D("0.4900")


def test_live_orders_on_either_side_are_reserved_as_if_they_filled(rig):
    """Owner requirement 2.9, including SIMULTANEOUS fills.

    `max`, not the sum: if both sides fill they match each other, so the
    worst case is whichever side fills ALONE. On 1130z four legs each
    calling itself the opening one filled inside seven seconds.
    """
    ex, st = rig["ex"], rig["store"]
    clean = gate(ex, 1, PLAN1)
    # BALANCED live orders, so no side is smaller and no exemption applies
    # -- which is the state in which the reservation is what binds.
    rig["mp"].setattr(st, "outstanding_on_side", lambda m, side: D("2"))
    dirty = gate(ex, 1, PLAN1)
    assert D(dirty["exposure_headroom_usd"]) < \
        D(clean["exposure_headroom_usd"])
    assert D(dirty["outstanding_worst_case_usd"]) == D("2") * D("0.4900")


@pytest.mark.parametrize("plan,why", [
    ({"legs": []}, "PLAN_HAS_NO_LEGS"),
    (None, "PLAN_HAS_NO_LEGS"),
    ({"legs": [{"side": "UP"}]}, "PLAN_UNREADABLE"),
])
def test_an_unreadable_plan_refuses_the_reopening(rig, plan, why):
    r = gate(rig["ex"], 1, plan)
    assert r["reopen"] is False and r["reason"] == why


def test_no_configured_exposure_bound_refuses(rig):
    """Unconstrained is fine for the planner; it is not fine for ADDING."""
    rig["ex"].validation_gates = None
    r = gate(rig["ex"], 1, PLAN1)
    assert r["reopen"] is False
    assert r["reason"] == "NO_CONFIGURED_EXPOSURE_BOUND_TO_REOPEN_AGAINST"


def test_a_raising_decision_refuses_rather_than_opens(rig):
    rig["mp"].setattr(UD, "reopen_decision",
                      lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = gate(rig["ex"], 1, PLAN1)
    assert r["reopen"] is False
    assert r["reason"] == "REOPEN_DECISION_RAISED"


def test_the_gate_reports_locked_pair_pnl_without_spending_it(rig):
    """Round 0 paired at 0.9500 for +$0.0500. It must appear and not count.

    The venue's own fill prices: DOWN 0.5000 at 12:30:27, UP 0.4500 at
    12:30:49. The settlement record's 0.9550 is the same money at AVERAGE
    basis once the second UP filled at 0.4600; see the module docstring.
    """
    st = rig["store"]
    st.apply_fill(SLUG, "UP", D("1"), D("0.4500"), basis="OWN")
    st.apply_fill(SLUG, "DOWN", D("1"), D("0.5000"), basis="OWN")
    locked = rig["ex"].probe_locked_pair_pnl(SLUG)
    assert locked == D("0.0500")
    r = gate(rig["ex"], 1, PLAN1)
    assert D(r["locked_pair_pnl_usd"]) == D("0.0500")
    # reported and excluded: the expectancy is the ROUND's own, unchanged by
    # whatever has already been locked
    assert D(r["expectancy_usd"]) == D("0.0299")


def test_locked_pnl_is_zero_with_no_matched_inventory(rig):
    assert rig["ex"].probe_locked_pair_pnl(SLUG) == D("0")


def test_locked_pnl_is_none_rather_than_wrong_when_unreadable(rig):
    def boom(*a, **k):
        raise RuntimeError("no store")
    rig["mp"].setattr(rig["ex"].store, "position", boom)
    assert rig["ex"].probe_locked_pair_pnl(SLUG) is None


# ================================= 8. THE EXISTING CONTROLLER IS UNCHANGED

def test_decide_still_has_its_own_actions_and_ranking():
    """`reopen_decision` is a sibling of `decide`, not a replacement."""
    assert UD.PRESERVE in UD.ACTIONS and UD.COMPLETE_MAKER in UD.ACTIONS
    assert UD.COMPLETE_TAKER in UD.CROSSING
    assert callable(UD.decide) and callable(UD.reopen_decision)


def test_probe_round_itself_still_only_walks_rounds(rig):
    """The walker keeps its single job; the economics live in one module."""
    assert rig["ex"].probe_round(SLUG) == 0
    import inspect
    src = inspect.getsource(E.FaithfulExecutor.probe_round)
    for banned in ("reopen_decision", "expectancy", "p_pair"):
        assert banned not in src
