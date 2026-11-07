"""The requote cancelled a +$0.05 pair to chase a $0.00 one. 0100z, twice.

VENUE-CONFIRMED FACTS, from the engine's own `L5_TS_REQUOTE` records for
`cpc-btc-updown-15m-2026-11-07-0100z` (one authorized window, $1 maker-only):

  01:00:39          DOWN 1 fills, maker, own 0.4900
  01:00:40.593      UP own 0.4600 CANCELLED -> target 0.5100
                    venue DJHDVD5014ET  ORDER_STATE_CANCELED  cum_qty 0
                    touch 0.5100  completion_ceiling 0.95  redemption_cap
                    0.5100  redemption_cap_binds FALSE  extra_cash_usd 0.0500
  01:01:00.697      UP own 0.4900 CANCELLED -> target 0.5100
                    venue DJHE328054EX  ORDER_STATE_CANCELED  cum_qty 0
                    touch 0.7100  redemption_cap_binds TRUE
                    extra_cash_usd 0.0200
  01:15             settled. matched_qty 0. residual 1 DOWN. UP won.
                    settlement_pnl -0.4900. fees 0.0000. rebates 0.00.

Held DOWN at own 0.4900, so the order it cancelled was a pair of 0.9500 --
+$0.05 of real matched edge -- and the target was a pair of EXACTLY 1.0000,
which earns $0.0000 even when it fills. The engine computed the giveaway and
logged it as `extra_cash_usd` while making the move.

AND IT ALREADY KNEW. The same tick's `L5_MISSED_PAIR` priced that completion
`pair_own_cost 1.0000`, `inside_entry_ceiling NO`, `gain_per_contract_usd
0.0000`, verdict `missed_profitable_pair NO` -- it DECLINED to complete
there. One path knew the pair was out of bounds and worth nothing; the other
cancelled toward it 450ms later. The owner's authorization for this campaign
capped pair cost at 0.96.

ROOT CAUSE. `completion_requote`'s target is
`min(touch, completion_ceiling, redeem_cap)`. `redeem_cap` is BREAKEVEN
(1 - held_cost) and `completion_ceiling` is a SINGLE-LEG own-price cap. The
one term that bounds the PAIR is `edge_cap = completion_max_pair()`, which
defaulted to 1.00 -- the redemption line -- and whose variable the campaign
never set. So no term in that `min()` preserved any pair margin.

NO FILL IS CLAIMED ANYWHERE BELOW. Both cancelled orders show `cum_qty 0`,
and whether the 0.46 quote would have filled had it been left alone is
UNKNOWN: it rested 5 ticks behind the touch with 2923.02 contracts of
measured depth ahead. These tests assert what the controller DECIDES, never
what the venue would have done.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS


# ========================================== THE RECORDED STATE, AS RECORDED
#
# Every value here is copied from the venue-confirmed records above. `ask`
# and `residual_budget_usd` are the two the record does not carry: the ask
# only has to sit above the touch for the book to be two-sided and uncrossed,
# and the residual budget only has to be wide enough not to be the binding
# refusal -- which is the point, since the real one did not bind either.
WINDOW_START = 1794013200.0            # 2026-11-07 01:00:00Z
FILL_OWN_PX = D("0.4900")              # the DOWN first leg, maker


def _state(**over):
    st = dict(
        side="UP",
        resting_own_px=D("0.4600"),
        resting_qty=D("1"),
        resting_filled_qty=D("0"),
        bid=D("0.5100"),               # UP rests on the bid; own == wire
        ask=D("0.5300"),
        tick=D("0.01"),
        min_qty=D("1"),
        held_qty=D("1"),               # DOWN
        held_cost_usd=FILL_OWN_PX,
        deficient_qty=D("0"),
        deficient_cost_usd=D("0"),
        residual_budget_usd=D("5"),
        now_s=1794013240.1147778,      # 01:00:40.115, the real decision clock
        window_start_s=WINDOW_START,
        window_end_s=TS.window_end(WINDOW_START),
        book_age_s=0.061,
        gap_suspected=False,
        orders_uncertain=None,
        queue_ahead_fn=lambda px: (D("2923.0200") if D(str(px)) <= D("0.46")
                                   else D("561.0200")),
        require_queue_evidence=True,
        cancel_unresolved=None,
    )
    st.update(over)
    return st


def _pair(target: D, held_cost: D = FILL_OWN_PX) -> D:
    """Own-cost of the matched pair this target would buy."""
    return (D(str(target)) + held_cost).quantize(D("0.0001"))


# ============================================================ 1. THE DEFECT
#
# Reproduced from the recorded inputs with the OLD default restored, so the
# fix is measured against observed behaviour and not against a guess at it.

def test_baseline_reproduces_the_recorded_0100z_cancel(monkeypatch):
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "1.00")
    out = TS.completion_requote(**_state())
    assert out["act"] is True, out
    assert out["to_price"] == "0.51"
    assert out["from_price"] == "0.4600"
    assert out["ticks_gained"] == "5.00"
    assert out["touch"] == "0.5100"
    assert out["completion_ceiling"] == "0.95"
    assert out["redemption_cap"] == "0.5100"
    assert out["redemption_cap_binds"] is False
    assert out["extra_cash_usd"] == "0.0500"
    # and the pair it buys is exactly the redemption line: zero edge
    assert _pair(D(out["to_price"])) == D("1.0000")


def test_baseline_reproduces_the_second_recorded_cancel(monkeypatch):
    """01:01:00.697. Resting 0.4900, touch 0.7100, redemption cap binds."""
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "1.00")
    out = TS.completion_requote(**_state(
        resting_own_px=D("0.4900"), bid=D("0.7100"), ask=D("0.7300"),
        now_s=1794013259.164029,
        queue_ahead_fn=lambda px: (D("13803.3700")
                                   if D(str(px)) <= D("0.49")
                                   else D("13002.3700"))))
    assert out["act"] is True, out
    assert out["to_price"] == "0.51"
    assert out["ticks_gained"] == "2.00"
    assert out["redemption_cap_binds"] is True
    assert out["extra_cash_usd"] == "0.0200"
    assert _pair(D(out["to_price"])) == D("1.0000")


# ============================================================== 2. THE FIX

def test_the_fix_refuses_the_cancel_and_leaves_the_cheap_order_resting():
    """Default ceiling = the approved opening ceiling, so 0.46 survives."""
    out = TS.completion_requote(**_state())
    assert out["act"] is False
    assert out["reason"] == "IMPROVEMENT_BELOW_MIN_TICKS"
    # the target it would have moved to, had it been 2 ticks better
    assert out["target"] == "0.47"
    assert _pair(D("0.47")) == D("0.9600")
    # NOTHING IS WITHDRAWN BY A REFUSAL. The caller cancels only on act.
    assert "cancel" not in out


def test_the_fix_refuses_the_second_cancel_too():
    out = TS.completion_requote(**_state(
        resting_own_px=D("0.4900"), bid=D("0.7100"), ask=D("0.7300"),
        now_s=1794013259.164029))
    assert out["act"] is False
    assert out["reason"] == "NO_IMPROVEMENT"
    assert out["target"] == "0.47"


def test_the_margin_the_fix_preserves_is_the_one_the_window_lost():
    """Arithmetic only. No fill is asserted, in either direction."""
    kept = D("1") - _pair(D("0.4600"))          # if the 0.46 quote fills
    chased = D("1") - _pair(D("0.5100"))        # if the 0.51 quote fills
    assert kept == D("0.0500")
    assert chased == D("0.0000")
    # and the realized outcome of neither filling, which is what happened
    assert -FILL_OWN_PX == D("-0.4900")


# =================================================== 3. IT ONLY EVER TIGHTENS
#
# The safety claim in one property: the fix can REMOVE a requote and can
# never ADD one, and where it still acts it never aims DEARER than before.

@pytest.mark.parametrize("held", ["0.20", "0.30", "0.40", "0.49", "0.60"])
@pytest.mark.parametrize("rest", ["0.10", "0.25", "0.46", "0.55"])
@pytest.mark.parametrize("touch", ["0.30", "0.51", "0.71", "0.88"])
def test_fix_acts_only_where_the_old_default_acted(monkeypatch, held,
                                                   rest, touch):
    st = _state(held_cost_usd=D(held), resting_own_px=D(rest),
                bid=D(touch), ask=(D(touch) + D("0.02")),
                queue_ahead_fn=lambda px: D("10"))
    fixed = TS.completion_requote(**st)
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "1.00")
    old = TS.completion_requote(**st)
    if fixed["act"]:
        assert old["act"], "the fix ADDED a requote"
        assert D(fixed["to_price"]) <= D(old["to_price"])


@pytest.mark.parametrize("held", ["0.20", "0.35", "0.49"])
@pytest.mark.parametrize("touch", ["0.40", "0.55", "0.80"])
def test_no_requote_ever_targets_a_pair_above_the_ceiling_in_force(held,
                                                                  touch):
    out = TS.completion_requote(**_state(
        held_cost_usd=D(held), resting_own_px=D("0.10"),
        bid=D(touch), ask=(D(touch) + D("0.02")),
        queue_ahead_fn=lambda px: D("10")))
    if out["act"]:
        cap = D(out["pair_ceiling_in_force"])
        assert D(out["pair_own_cost_at_target"]) <= cap, out
        assert cap <= D("1.00")


# ================================= 4. IT DID NOT DISABLE COMPLETION PRICING
#
# The failure mode of a margin floor is that it refuses everything. These
# pin requotes that MUST still happen.

def test_a_requote_that_still_creates_real_edge_is_still_permitted():
    """Held 0.30, resting 0.40, touch 0.60 -> target 0.60, pair 0.90."""
    out = TS.completion_requote(**_state(
        held_cost_usd=D("0.30"), resting_own_px=D("0.40"),
        bid=D("0.60"), ask=D("0.62"),
        queue_ahead_fn=lambda px: D("10")))
    assert out["act"] is True, out
    assert out["to_price"] == "0.60"
    assert _pair(D("0.60"), D("0.30")) == D("0.9000")


def test_the_ceiling_binds_at_exactly_the_configured_pair():
    """Held 0.49, ceiling 0.96 -> the dearest permitted target is 0.47."""
    out = TS.completion_requote(**_state(
        resting_own_px=D("0.20"), bid=D("0.90"), ask=D("0.92"),
        queue_ahead_fn=lambda px: D("10")))
    assert out["act"] is True, out
    assert out["to_price"] == "0.47"
    assert D(out["pair_own_cost_at_target"]) == D("0.9600")


def test_an_owner_may_still_widen_it_for_the_stranded_leg_case(monkeypatch):
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "0.99")
    out = TS.completion_requote(**_state(
        resting_own_px=D("0.20"), bid=D("0.90"), ask=D("0.92"),
        queue_ahead_fn=lambda px: D("10")))
    assert out["act"] is True
    assert out["to_price"] == "0.50"
    assert D(out["pair_own_cost_at_target"]) == D("0.9900")


# ============================================== 5. THE OTHER GATES STILL BIND
#
# The fix must not have become the only thing refusing.

def test_uncertain_orders_still_refuse_before_anything_is_priced():
    out = TS.completion_requote(**_state(orders_uncertain=True))
    assert out["act"] is False and out["reason"] == "ORDERS_UNCERTAIN"


def test_a_stale_book_still_refuses():
    out = TS.completion_requote(**_state(book_age_s=30.0))
    assert out["act"] is False and out["reason"] == "BOOK_STALE"


def test_a_suspected_gap_still_refuses():
    out = TS.completion_requote(**_state(gap_suspected=True))
    assert out["act"] is False and out["reason"] == "GAP_SUSPECTED"


def test_absent_queue_evidence_still_fails_closed():
    out = TS.completion_requote(**_state(
        held_cost_usd=D("0.30"), resting_own_px=D("0.40"),
        bid=D("0.60"), ask=D("0.62"), queue_ahead_fn=lambda px: None))
    assert out["act"] is False and out["reason"] == "QUEUE_EVIDENCE_ABSENT"


def test_the_cancel_margin_still_closes_the_window():
    """Runway is measured to `cancel_cutoff`, not to the entry cutoff.

    Derived from the gate itself rather than assumed, because the deployed
    margin is configurable: 0100z recorded `runway_s 559.9` at T+40.1, which
    is a 300s margin, while the module default is 180s.
    """
    end = TS.window_end(WINDOW_START)
    out = TS.completion_requote(**_state(
        now_s=TS.cancel_cutoff(end) - (TS.REQUOTE_MIN_RUNWAY_S - 1.0),
        held_cost_usd=D("0.30"), resting_own_px=D("0.40"),
        bid=D("0.60"), ask=D("0.62")))
    assert out["act"] is False
    assert out["reason"] == "INSUFFICIENT_REQUOTE_RUNWAY"


def test_a_partially_filled_resting_order_is_priced_on_what_still_works():
    """resting 1 with 1 already filled leaves nothing working to reprice."""
    out = TS.completion_requote(**_state(resting_filled_qty=D("1")))
    assert out["act"] is False
    assert out["reason"] == "NOTHING_STILL_WORKING"


def test_nothing_held_is_nothing_to_complete():
    out = TS.completion_requote(**_state(held_qty=D("0")))
    assert out["act"] is False
    assert out["reason"] in ("NOTHING_TO_COMPLETE", "HELD_COST_UNKNOWN")


# ================================================= 6. THE RECORD IS HONEST
#
# 0100z printed `redemption_cap_binds: false` and a 1.0000 pair in the same
# record. A reader could not see that the pair bound was inert.

def test_the_record_names_the_pair_bound_and_the_pair_it_would_buy():
    monkeypatch_free = TS.completion_requote(**_state(
        held_cost_usd=D("0.30"), resting_own_px=D("0.40"),
        bid=D("0.60"), ask=D("0.62"), queue_ahead_fn=lambda px: D("10")))
    assert monkeypatch_free["pair_ceiling_in_force"] == "0.96"
    assert monkeypatch_free["pair_own_cost_at_target"] == "0.9000"


def test_the_knob_digest_reports_effective_values_not_constants(monkeypatch):
    """L5_TS_OPENING_LEG_MAX_CONTRACTS=1 with a module constant of 3."""
    monkeypatch.setenv(TS.OPENING_LEG_MAX_ENV, "1")
    knobs = TS.strategy_config_fingerprint()["knobs"]
    assert TS.OPENING_LEG_MAX_CONTRACTS == D("3")
    assert knobs["opening_leg_max_contracts"] == "1"
    assert knobs["completion_max_pair"] == "0.96"


# ====================== 7. CANCEL SAFETY, UNCERTAIN ACKS, AND SCOPING
#
# The fix removes cancels; it must not weaken the protections around the
# cancels that remain.

def test_an_unresolved_prior_cancel_refuses_before_pricing():
    """A cancel whose outcome the venue has not confirmed must not be
    followed by another. Otherwise a requote races a live order."""
    out = TS.completion_requote(**_state(
        cancel_unresolved=True,
        held_cost_usd=D("0.30"), resting_own_px=D("0.40"),
        bid=D("0.60"), ask=D("0.62")))
    assert out["act"] is False
    assert out["reason"] == "CANCEL_UNRESOLVED"


def test_uncertain_orders_outrank_even_a_profitable_reprice():
    """Ordering matters: uncertainty is read before any economics."""
    out = TS.completion_requote(**_state(
        orders_uncertain=True, cancel_unresolved=True,
        held_cost_usd=D("0.30"), resting_own_px=D("0.40"),
        bid=D("0.60"), ask=D("0.62")))
    assert out["act"] is False
    assert out["reason"] == "ORDERS_UNCERTAIN"


def test_the_caller_cancels_exactly_one_named_order_by_venue_id():
    """0100z scoped each cancel to one client_order_id and proved the
    terminal state by re-read (`verify_attempts 2`). Pinned on code lines,
    not on prose, since the docstrings discuss cancellation at length.
    """
    import inspect

    from src import l5_pmus_live_runner as R
    lines = [ln.strip() for ln
             in inspect.getsource(R.Runner.maybe_completion_requote
                                  ).splitlines()]
    code = [ln for ln in lines if ln and not ln.startswith("#")]
    body = "\n".join(code)
    assert "only=[coid]" in body
    # and it refuses to guess which of several orders to withdraw
    assert "MULTIPLE_RESTING_ORDERS_IN_MARKET" in body
    assert "NO_VENUE_ID" in body


def test_a_refusal_withdraws_nothing_at_all():
    """Every refusal path returns before the caller's cancel, so the
    resting order survives. Asserted on the contract, for every refusal
    this fix can produce."""
    for st in (_state(),                                   # the 0100z state
               _state(resting_own_px=D("0.4900"), bid=D("0.7100"),
                      ask=D("0.7300"))):
        out = TS.completion_requote(**st)
        assert out["act"] is False
        assert out.get("to_price") is None
        assert "cancel" not in out


# ============== 8. THE OTHER CANCEL PATH DOES NOT DEFEAT THE FIX
#
# 0100z cancelled THREE UP orders through TWO mechanisms, not one:
#
#   l5f-b0b2...9bb2  DJHDVD5014ET  own 0.4600  01:00:40.456
#                    L5_TS_REQUOTE, reason completion_requote
#   l5f-dc6b...f904  DJHE2YA0144P              01:00:59.510
#                    L5_ACC_DEADLINE, reason acc_order_deadline,
#                    stale_by_distance, ttl_s 120.0, recoveries_used 1
#   l5f-3df3...e403  DJHE328054EX  own 0.4900  01:01:00.551
#                    L5_TS_REQUOTE, reason completion_requote
#
# All three `cum_qty 0`, all venue-confirmed ORDER_STATE_CANCELED. So a fix
# to the requote alone is worth nothing if `recover_stale_leg` -- which
# takes NO inventory and NO cost basis, and judges distance only -- would
# cancel the quote the requote now preserves. Measured, it does not.

def _recover(own_px, bid, now_s, **over):
    kw = dict(side="UP", own_px=D(str(own_px)), qty=D("1"),
              remaining=D("1"), state="LIVE",
              submitted_t=WINDOW_START, now=now_s,
              bid=D(str(bid)), ask=(D(str(bid)) + D("0.02")),
              tick=D("0.01"), depth_ticks=TS.depth_ticks(),
              book_age_s=0.061, recoveries_used=0,
              open_budget_remaining=None)
    kw.update(over)
    return TS.recover_stale_leg(**kw)


def test_the_distance_recovery_leaves_the_preserved_0100z_quote_alone():
    """The exact post-fill state: resting 0.4600, touch 0.5100, T+40s."""
    v = _recover("0.4600", "0.51", 1794013240.1147778)
    assert v["recover"] is False
    assert v["reason"] == "WITHIN_TOLERANCE"
    assert v["drift_ticks"] == "3.00"
    assert D(v["allowed_ticks"]) == D("4")
    # the price a replacement would be quoted at, two ticks behind the touch
    assert v["would_quote_now"] == "0.49"


def test_the_order_the_recovery_did_cancel_is_the_one_that_drifted():
    """01:00:59: the 0.4900 replacement against a touch already at 0.7100.
    20 ticks past a 4-tick tolerance, which is a real stale leg."""
    v = _recover("0.4900", "0.71", 1794013259.5)
    assert v["recover"] is True, v
    assert D(v["drift_ticks"]) == D("20.00")


def test_a_quote_that_became_more_competitive_is_never_cancelled():
    v = _recover("0.4600", "0.44", 1794013240.1147778)
    assert v["recover"] is False
    assert v["reason"] == "NOT_DRIFTED_AWAY"


def test_the_0100z_tolerance_refusal_comes_before_any_pair_test():
    """Stated, not assumed, because it bounds what THIS fix can claim.

    `recover_stale_leg` has since gained the pair test
    (`RESTING_PAIR_WORTH_KEEPING`, a separate PR), so it is no longer true
    that it knows nothing about pair economics. What is still true, and is
    the bound on the requote fix, is that the pair test never ran at 0100z:
    3 ticks of drift is inside the 4-tick tolerance, so the distance rule
    declined on DISTANCE and the 0.46 quote owed its survival to the
    requote ceiling alone.
    """
    import inspect
    params = set(inspect.signature(TS.recover_stale_leg).parameters)
    assert {"held_qty", "held_cost_usd", "pair_ceiling"} <= params
    v = TS.recover_stale_leg(
        side="UP", own_px=D("0.4600"), qty=D("1"), remaining=D("1"),
        state="LIVE", submitted_t=WINDOW_START, now=1794013240.1147778,
        bid=D("0.51"), ask=D("0.53"), tick=D("0.01"),
        depth_ticks=TS.depth_ticks(), book_age_s=0.061, recoveries_used=0,
        held_qty=D("1"), held_cost_usd=FILL_OWN_PX)
    assert v["recover"] is False
    assert v["reason"] == "WITHIN_TOLERANCE"
    # the pair was never priced, which is the point
    assert "pair_at_resting" not in v
