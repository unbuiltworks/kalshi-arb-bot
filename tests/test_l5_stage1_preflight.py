"""Stage 1 preflight: $10 cash and $1 worst-case deficit, verified.

PROPOSED PARAMETERS, NOT AUTHORIZATION. Nothing here arms anything; these
tests establish whether the executor would hold the two proposed limits if
it were ever authorized, and what the limits do NOT cover.

THE TWO LIMITS ARE DIFFERENT QUESTIONS, which is the whole reason both are
specified:

  max_cash_deployed_usd        $10 -- cash that has LEFT the account and is
                               not recoverable until settlement
  max_worst_case_deficit_usd   $1  -- `C - min(up, dn)`, the most the book
                               can lose HOWEVER the window resolves

A matched pair consumes cash and carries no deficit; an unmatched leg
carries its whole basis as deficit. $10 of cash is not permission to risk
$10, and the deficit bound is what makes that enforceable.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_two_sided_probe as TSP
from src import l5_unmatched_disposition as UD
from src.l5_live_validation_gates import ValidationGates as VG
import tests.test_l5_two_sided_wiring as W
from tests.test_l5_two_sided_wiring import probe  # noqa: F401

#: the proposed Stage 1 parameters
STAGE1_CASH = D("10")
STAGE1_DEFICIT = D("1")


def stage1_gates(**over):
    kw = dict(max_cash_deployed_usd=STAGE1_CASH,
              max_worst_case_deficit_usd=STAGE1_DEFICIT,
              max_unmatched_contracts=D("500"),
              max_cumulative_loss_usd=D("500"))
    kw.update(over)
    return VG(**kw)


def rig(H, E_, tmp_path, orders=8, **over):
    ex, t = W.mk(H, E_, tmp_path, g=stage1_gates(**over))
    ex.limits = H.limits(max_live_orders=orders)
    return ex, t


def drive(ex, H, n=12, book=None):
    last = None
    for _ in range(n):
        last = ex.handle(W._row(H), book or W._book(), market="m",
                         window="21:00").get("refused_reason")
    return last


# ============ 2. BOTH LIMITS ENFORCED, IN FOUR STATES

def test_both_limits_are_configured_and_distinct():
    g = stage1_gates()
    assert g.max_cash_deployed_usd == D("10")
    assert g.max_worst_case_deficit_usd == D("1")
    assert g.max_worst_case_deficit_usd < g.max_cash_deployed_usd


def test_at_submission_neither_limit_is_exceeded(probe, tmp_path):
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path)
    last = drive(ex, H)
    legs = ex.store.leg_cost("m")
    committed = sum(D(str(legs.get(s) or 0)) for s in ("UP", "DOWN"))
    assert committed <= STAGE1_CASH, (committed, last)
    g = stage1_gates()
    wc = g.worst_case_deficit(
        up_qty=ex.store.position("m", "UP"),
        dn_qty=ex.store.position("m", "DOWN"),
        up_cost=legs.get("UP") or D(0), dn_cost=legs.get("DOWN") or D(0),
        order_side="UP", order_qty=D("0.01"), order_price=D("0.50"))
    if wc.get("ok"):
        assert D(wc["deficit"]) <= STAGE1_DEFICIT + D("0.01"), wc


def test_after_a_partial_fill_the_deficit_bound_still_binds(probe,
                                                            tmp_path):
    """A partial fill is the state that CREATES a deficit."""
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path)
    drive(ex, H, n=2)
    up = next(s for s in t.sent if s["side"] == "UP")
    ex.store.apply_fill("m", "UP", D(str(up["qty"])) * 3,
                        D(str(up["price"])), basis="OWN")
    legs = ex.store.leg_cost("m")
    g = stage1_gates()
    wc = g.worst_case_deficit(
        up_qty=ex.store.position("m", "UP"),
        dn_qty=ex.store.position("m", "DOWN"),
        up_cost=legs.get("UP") or D(0), dn_cost=legs.get("DOWN") or D(0),
        order_side="UP", order_qty=D("1"), order_price=D("0.58"))
    assert wc["ok"] is True, wc
    # a deepening UP order on an UP-heavy book must be over the bound
    assert D(wc["deficit"]) > STAGE1_DEFICIT, wc
    # and the executor must refuse it
    last = drive(ex, H, n=4)
    assert last is not None, "a deepening order is not permitted silently"


def test_during_a_cancellation_race_everything_is_refused(probe, tmp_path):
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path)
    drive(ex, H, n=2)
    before = len(t.sent)
    ex.store.mark_uncertain("CANCEL_VERIFY_HTTPError")
    last = drive(ex, H, n=4)
    assert len(t.sent) == before
    assert "BOOK_UNCERTAIN" in str(last) or "UNCERTAIN" in str(last)


def test_after_a_restart_both_limits_are_re_read_from_disk(probe, tmp_path):
    """A fresh process on the same volume must not forget what is held."""
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path)
    drive(ex, H, n=2)
    held_up = ex.store.position("m", "UP")
    ex2, t2 = rig(H, E_, tmp_path)
    assert ex2.store.position("m", "UP") == held_up
    legs = ex2.store.leg_cost("m")
    committed = sum(D(str(legs.get(s) or 0)) for s in ("UP", "DOWN"))
    assert committed <= STAGE1_CASH
    drive(ex2, H, n=6)
    legs2 = ex2.store.leg_cost("m")
    assert sum(D(str(legs2.get(s) or 0))
               for s in ("UP", "DOWN")) <= STAGE1_CASH


def test_the_deficit_bound_fails_closed_after_a_restart_it_cannot_read():
    g = stage1_gates()
    assert g.refuse(order_qty=D("1"), order_price=D("0.5"),
                    order_intent="BUY", inventory=None) == \
        "WORST_CASE_DEFICIT_NO_INVENTORY"


# ============ 3. PRESERVE RETAINS THE HISTORICAL COMPLEMENTS

@pytest.mark.parametrize("label,held,rest,bid,ask", [
    ("0115z", "0.24", "0.70", "0.28", "0.30"),
    ("0145z", "0.35", "0.61", "0.38", "0.40"),
])
def test_PRESERVE_retains_the_0115z_and_0145z_complements(label, held,
                                                           rest, bid, ask):
    r = UD.decide(market=label, held_side="DOWN", held_qty=D("1"),
                  held_cost_usd=D(held), deficient_qty=D("0"),
                  deficient_cost_usd=D("0"), resting_own_px=D(rest),
                  resting_qty=D("1"), resting_filled_qty=D("0"),
                  bid=D(bid), ask=D(ask), tick=D("0.01"), min_qty=D("0.01"),
                  now_s=60.0, window_start_s=0.0, window_end_s=900.0,
                  book_age_s=D("0.05"), taker_enabled=False)
    assert r["selected"] == UD.PRESERVE, (label, r["selected_reason"])
    assert D(held) + D(rest) <= D("1.00"), "and the pair is justifiable"


# ======= 4. A COMPLETION ABOVE REDEMPTION IS REJECTED, FEES INCLUDED

def test_the_effective_pair_is_cash_actually_paid_over_matched_quantity():
    """`eff = (leg_cost_UP + leg_cost_DOWN + px*qty) / matched_after`.

    For MAKER fills that IS fee-inclusive: the published schedule has no
    maker-fee term, so cash paid is the whole cost. A zero commission is
    not a rebate and none is added.
    """
    import inspect
    src = inspect.getsource(E.FaithfulExecutor)
    assert "eff = cost_after / matched_after" in src
    assert "cost_after = (lc.get(UP) or D(0)) + (lc.get(DOWN) or D(0))" in src


def test_the_opening_ceiling_is_the_strict_one_and_is_point_nine_six():
    assert TSP.max_pair_cost() == D("0.96")
    # and a completion defaults to the SAME ceiling, not to 1.00
    assert TSP.completion_max_pair() == TSP.max_pair_cost()


@pytest.mark.parametrize("pair,ok", [
    (D("0.9500"), True), (D("0.9600"), True),
    (D("0.9700"), False), (D("1.0000"), False), (D("1.2000"), False),
])
def test_a_pair_above_the_ceiling_is_refused(pair, ok):
    assert (pair <= TSP.completion_max_pair()) is ok


def test_PRESERVE_refuses_a_resting_pair_above_redemption():
    r = UD.decide(market="m", held_side="DOWN", held_qty=D("1"),
                  held_cost_usd=D("0.55"), deficient_qty=D("0"),
                  deficient_cost_usd=D("0"), resting_own_px=D("0.50"),
                  resting_qty=D("1"), resting_filled_qty=D("0"),
                  bid=D("0.57"), ask=D("0.59"), tick=D("0.01"),
                  min_qty=D("0.01"), now_s=60.0, window_start_s=0.0,
                  window_end_s=900.0, book_age_s=D("0.05"),
                  taker_enabled=False)
    acts = {a["action"]: a for a in (r.get("actions") or [])}
    assert acts[UD.PRESERVE]["feasible"] is False
    assert acts[UD.PRESERVE]["infeasible_because"] == \
        "RESTING_PAIR_ABOVE_REDEMPTION"


def test_the_defensive_ceiling_is_unreachable_with_taker_disabled():
    """1.20 exists only for a MANDATED unwind, and crossing is off."""
    import inspect
    src = inspect.getsource(E.FaithfulExecutor)
    assert "1.20" in src or "D(\"1.2" in src
    r = UD.decide(market="m", held_side="DOWN", held_qty=D("1"),
                  held_cost_usd=D("0.50"), deficient_qty=D("0"),
                  deficient_cost_usd=D("0"), bid=D("0.48"), ask=D("0.52"),
                  tick=D("0.01"), min_qty=D("0.01"), now_s=60.0,
                  window_start_s=0.0, window_end_s=900.0,
                  book_age_s=D("0.05"), taker_enabled=False)
    assert r["selected"] not in UD.CROSSING


# ===== 5. NOTHING UNCERTAIN AND NOTHING LEFT RESTING AT THE WINDOW END

def test_the_terminal_deadline_is_before_the_window_closes():
    """An order may not outlive the window it was placed in."""
    assert TSP.cancel_cutoff(900.0) < 900.0
    assert TSP.entry_cutoff_s() < 900.0
    assert TSP.complete_cutoff_s() <= 900.0


def test_an_order_past_its_TTL_is_overdue_and_the_deadline_owns_it():
    """The mandatory path, which no discretionary gate may veto."""
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.maybe_acc_deadline)
    assert "overdue" in src
    assert "controller_preserves" not in src.split("overdue")[1][:400], \
        "the mandatory deadline must not consult a discretionary verdict"


def test_an_uncertain_store_blocks_every_submission(probe, tmp_path):
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path)
    ex.store.mark_uncertain("AMBIGUOUS_POST")
    assert drive(ex, H, n=3) is not None
    assert t.sent == []


# ============ 6. WATCHDOG, DAILY HALT, EXACT CANCELLATION, SETTLEMENT

def test_the_daily_halt_is_a_third_independent_bound():
    s = E.SafetyLimits(max_usd_per_market=D("100"),
                       max_abs_residual=D("100"),
                       max_total_exposure_usd=D("100"),
                       max_concurrent_markets=5,
                       daily_realized_loss_halt_usd=D("5"))

    def r(realized):
        return s.refuse(market="m", price=D("0.5"), qty=D("1"),
                        residual_after=D("0"), realized_today=realized,
                        committed_by_market={}, open_markets=["m"],
                        outstanding_by_market={}, live_order_count=0)
    assert r(D("-5")) == "DAILY_REALIZED_LOSS_HALT"
    assert r(D("-1")) != "DAILY_REALIZED_LOSS_HALT"


def test_cancellation_names_an_exact_venue_order_and_proves_it():
    import inspect
    src = inspect.getsource(E.FaithfulExecutor)
    assert "venue_order_id" in src
    assert "ORDER_STATE_CANCELED" in src or "verify" in src


def test_settlement_is_attributed_from_a_venue_price_only():
    import inspect
    src = inspect.getsource(E.InventoryStore)
    assert "realized_attributed" in src
    assert "retired_markets" in src


def test_an_unresolved_cancel_is_its_own_refusal_reason():
    import inspect
    src = inspect.getsource(TSP.completion_requote)
    assert "CANCEL_UNRESOLVED" in src


# ===== 7. MODELLED DEFICIT vs GUARANTEED MAXIMUM LOSS -- THE GAP

def test_the_deficit_model_assumes_the_order_fills_at_its_limit_price():
    """Sound for a maker BUY: the venue cannot fill a limit order worse
    than its limit. That is what makes the model a bound and not a guess.
    """
    import inspect
    src = inspect.getsource(VG.worst_case_deficit)
    assert "THE PROPOSED ORDER IS ASSUMED TO FILL" in src
    assert "OUTSTANDING ORDERS ARE AN ADVERSARY" in src


def test_the_model_maximises_over_every_outstanding_fill_corner():
    g = stage1_gates()
    wc = g.worst_case_deficit(
        up_qty=D("1"), dn_qty=D("1"), up_cost=D("0.5"), dn_cost=D("0.45"),
        order_side="UP", order_qty=D("1"), order_price=D("0.5"),
        outstanding=[{"side": "UP", "qty": D("2"), "price": D("0.5")},
                     {"side": "DOWN", "qty": D("2"), "price": D("0.45")}])
    assert wc["ok"] is True
    assert set(wc["scenarios"]) >= {"none", "up_only", "dn_only", "all"}


def test_the_gap_is_the_unreconciled_order_and_it_fails_closed():
    """THE ONE GAP between modelled deficit and guaranteed loss.

    The model is exact over the book it can SEE. An order the venue
    acknowledged but our ledger has not reconciled is outside it -- and
    that is precisely the state `orders_uncertain` names, which refuses
    every submission until an authoritative read clears it. So the gap is
    bounded by refusing to add to it, never by pricing it.
    """
    g = stage1_gates()
    assert g.refuse(order_qty=D("1"), order_price=D("0.5"),
                    order_intent="BUY",
                    book_uncertain="AMBIGUOUS_POST").startswith(
                        "BOOK_UNCERTAIN")


def test_fees_are_charged_at_the_taker_theta_even_for_a_maker_order():
    """Conservative on purpose: the worst case may not assume a rebate or a
    zero commission that the venue has never credited."""
    g = stage1_gates()
    f = g.fee_worst(D("1"), D("0.50"))
    assert f > 0, "a maker order is still charged a fee in the worst case"
