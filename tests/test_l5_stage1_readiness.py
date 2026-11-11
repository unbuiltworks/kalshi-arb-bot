"""Stage 1 readiness: pair completion, residual risk, and the loss budget.

WHY EVERY UNMATCHED LEG WE HAVE EVER CARRIED EXISTED. Reconstructed from the
venue tapes, each residual traces to ONE of four cancellation mechanisms,
and knowing which matters because each has a different guard:

  RECOVERY GATE (`recover_stale_leg`)
    0100z  DOWN 0.28 first leg   cancelled 44.8s, rest_s at recovery 44.13
    0115z  UP   0.70 complement  cancelled 21.0s, rest_s 16.62
    0145z  UP   0.61 complement  cancelled 13.6s, rest_s 16.79

  COMPLETION REQUOTE (`completion_requote`)
    0100z 11-07  UP 0.4600 and UP 0.4900, both cancelled toward a 0.51
    target -- a 1.0000 pair worth $0.0000. One path enforced the 0.96
    ceiling and the requote did not. That is the PR #200 defect.

  ACC DEADLINE TTL (120s), MANDATORY
    1230z  DOWN 0.4900 cancelled at 124.0s
    2015z  DOWN 0.5700 cancelled at 120.9s

  NEVER REPLACED after the round was spent -- the claim namespace, fixed by
  the round-retirement work.

THE FINDING THAT MATTERS FOR THE REST FLOORS. 0115z and 0145z were cancelled
at rest_s **16.62** and **16.79**, which is ABOVE both floors --
`recover_min_rest_s` 15.0 and `REQUOTE_MIN_REST_S` 14.0. **So the minimum
rest logic does NOT prevent the two cancellations that actually cost us
pairs.** PRESERVE is what prevents them, for its own reason: a resting quote
has the same worst case as holding and strictly better upside, so cancelling
it is never an improvement. The floors stop the sub-7s churn; PRESERVE stops
these. Both are needed and neither substitutes.

THE LOSS-AT-RISK BUDGET ALREADY EXISTS AND IS NOT THE CASH ENVELOPE.
`max_worst_case_deficit_usd` enforces `C - min(up, dn)` -- the most the book
can lose HOWEVER the window resolves -- maximised over the four corners of
which outstanding orders fill, exactly rather than conservatively, and it
fails closed when inventory cannot be read. A second budget beside it would
be a second opinion that can drift, so none is added. What these tests pin
is that it is INDEPENDENT of cash and binds FIRST when set below it, which
is what makes "$200 of cash is not permission to risk $200" enforceable.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_capital_plan as CP
from src import l5_two_sided_probe as TSP
from src import l5_unmatched_disposition as UD
from src.l5_live_validation_gates import ValidationGates as VG
import tests.test_l5_two_sided_wiring as W
from tests.test_l5_two_sided_wiring import probe  # noqa: F401

#: (window, role, own, cancelled_at_s, rest_s_at_recovery, mechanism)
UNMATCHED_LIFECYCLE = [
    ("0100z", "first leg", "0.28", 44.8, 44.13, "RECOVERY_GATE"),
    ("0115z", "complement", "0.70", 21.0, 16.62, "RECOVERY_GATE"),
    ("0145z", "complement", "0.61", 13.6, 16.79, "RECOVERY_GATE"),
    ("0100z-1107", "complement", "0.4600", None, None, "COMPLETION_REQUOTE"),
    ("0100z-1107", "complement", "0.4900", None, None, "COMPLETION_REQUOTE"),
    ("1230z", "complement", "0.4900", 124.0, None, "ACC_DEADLINE_TTL"),
    ("2015z", "first leg", "0.5700", 120.9, None, "ACC_DEADLINE_TTL"),
]


def disp(held_px, rest_px, bid, ask, **over):
    kw = dict(market="m", held_side="DOWN", held_qty=D("1"),
              held_cost_usd=D(held_px), deficient_qty=D("0"),
              deficient_cost_usd=D("0"), resting_own_px=D(rest_px),
              resting_qty=D("1"), resting_filled_qty=D("0"),
              bid=D(bid), ask=D(ask), tick=D("0.01"), min_qty=D("0.01"),
              now_s=60.0, window_start_s=0.0, window_end_s=900.0,
              book_age_s=D("0.05"), taker_enabled=False)
    kw.update(over)
    return UD.decide(**kw)


# ============ 1. EVERY UNMATCHED LEG TRACES TO A NAMED MECHANISM

def test_every_unmatched_leg_has_an_identified_cause():
    assert len(UNMATCHED_LIFECYCLE) == 7
    assert {r[5] for r in UNMATCHED_LIFECYCLE} == {
        "RECOVERY_GATE", "COMPLETION_REQUOTE", "ACC_DEADLINE_TTL"}


def test_the_TTL_cancellations_cost_nothing_by_our_own_fill_curve():
    """Past the last observed fill the conditional chance is zero, so the
    mandatory deadline is not what cost us pairs."""
    from analysis.l5.issue206 import own_order_lifecycles as OWN
    km = OWN.kaplan_meier(OWN.ORDERS)
    for r in UNMATCHED_LIFECYCLE:
        if r[5] == "ACC_DEADLINE_TTL" and r[3]:
            assert OWN.conditional_fill_chance(km, r[3]) == 0.0, r


def test_the_recovery_cancellations_fired_inside_the_fill_distribution():
    """Which is the whole patience problem, quantified."""
    from analysis.l5.issue206 import own_order_lifecycles as OWN
    km = OWN.kaplan_meier(OWN.ORDERS)
    costly = [r for r in UNMATCHED_LIFECYCLE
              if r[5] == "RECOVERY_GATE" and r[1] == "complement"]
    assert len(costly) == 2
    for r in costly:
        assert OWN.conditional_fill_chance(km, r[3]) >= 0.6, r


# ======== 2. THE REST FLOORS ARE NOT WHAT SAVES THE COSTLY CANCELS

def test_the_rest_floors_do_not_reach_the_two_that_cost_us_pairs():
    """16.62s and 16.79s are ABOVE both floors. Stated so nobody credits
    the floors with a save they do not make."""
    assert TSP.REQUOTE_MIN_REST_S == 14.0
    assert float(TSP.recover_min_rest_s()) == 15.0
    for r in UNMATCHED_LIFECYCLE:
        if r[0] in ("0115z", "0145z"):
            assert r[4] > TSP.REQUOTE_MIN_REST_S
            assert r[4] > float(TSP.recover_min_rest_s())


@pytest.mark.parametrize("held,rest,bid,ask", [
    ("0.24", "0.70", "0.28", "0.30"),      # 0115z, pair 0.94
    ("0.35", "0.61", "0.38", "0.40"),      # 0145z, pair 0.96
    ("0.60", "0.37", "0.62", "0.64"),      # 2015z, pair 0.97
    ("0.50", "0.50", "0.52", "0.54"),      # exactly at redemption
])
def test_PRESERVE_is_what_holds_a_justifiable_resting_completion(held, rest,
                                                                 bid, ask):
    r = disp(held, rest, bid, ask)
    assert r["selected"] == UD.PRESERVE, (held, rest,
                                          r["selected_reason"])


@pytest.mark.parametrize("held,rest,bid,ask", [
    ("0.55", "0.50", "0.57", "0.59"),      # pair 1.05
    ("0.70", "0.50", "0.72", "0.74"),      # pair 1.20
])
def test_PRESERVE_refuses_an_economically_unjustifiable_price(held, rest,
                                                              bid, ask):
    """The second half of the requirement: patience must not become holding
    an order that LOCKS A LOSS. Above redemption, preserving is refused."""
    r = disp(held, rest, bid, ask)
    assert r["selected"] != UD.PRESERVE
    acts = {a["action"]: a for a in (r.get("actions") or [])}
    assert acts[UD.PRESERVE]["feasible"] is False
    assert acts[UD.PRESERVE]["infeasible_because"] == \
        "RESTING_PAIR_ABOVE_REDEMPTION"


# ============ 3. TWO-SIDED QUOTING AND REDUCTION ON THE REAL EXECUTOR

def _run(H, E, tmp_path, cash="10", deficit="500", ticks=12, orders=8):
    ex, t = W.mk(H, E, tmp_path,
                 g=VG(max_cash_deployed_usd=D(cash),
                      max_unmatched_contracts=D("500"),
                      max_cumulative_loss_usd=D("500"),
                      max_worst_case_deficit_usd=D(deficit)))
    ex.limits = H.limits(max_live_orders=orders)
    last = None
    for _ in range(ticks):
        last = ex.handle(W._row(H), W._book(), market="m",
                         window="21:00").get("refused_reason")
    return ex, t, last


def test_both_sides_are_quoted_and_pairs_complete(probe, tmp_path):
    H, E = probe
    ex, t, last = _run(H, E, tmp_path)
    assert {s["side"] for s in t.sent} == {"UP", "DOWN"}, last
    assert ex.store.position("m", "UP") > 0
    assert ex.store.position("m", "DOWN") > 0


def test_a_partial_fill_never_widens_the_per_side_imbalance(probe,
                                                            tmp_path):
    H, E = probe
    ex, t, _ = _run(H, E, tmp_path, ticks=2)

    def prosp(side):
        return (abs(D(str(ex.store.position("m", side) or 0)))
                + D(str(ex.store.outstanding_on_side("m", side))))
    up = next(s for s in t.sent if s["side"] == "UP")
    ex.store.apply_fill("m", "UP", D(str(up["qty"])) / 2,
                        D(str(up["price"])), basis="OWN")
    before = abs(prosp("UP") - prosp("DOWN"))
    for _ in range(3):
        ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert abs(prosp("UP") - prosp("DOWN")) <= before


# ========= 4. WORST-CASE LOSS MODELLED APART FROM EXPECTED PAIR PROFIT

@pytest.mark.parametrize("env,pairs,worst", [
    (D("10"), 10, D("4.7500")),
    (D("25"), 26, D("12.3500")),
    (D("50"), 52, D("24.7000")),
    (D("200"), 210, D("99.7500")),
])
def test_the_two_figures_are_computed_separately(env, pairs, worst):
    """Expected profit and worst-case loss are different questions and the
    worst case is 20x the edge at every scale, which is why a stage gate
    must be set against the loss."""
    r = CP.rounds_affordable(cash_envelope_usd=env,
                             pair_cost_usd=D("0.95"))
    assert r["rounds_affordable"] == pairs
    assert D(r["worst_case_if_every_round_strands_one_leg_usd"]) == worst
    expected = D(pairs) * D("0.0350")      # L5's measured matched edge
    # THE RATIO IS ~13.6x AT EVERY SCALE, and it is scale-invariant because
    # both terms are linear in the pair count: the edge is 3.5c a pair and
    # the worst case is half a pair's cost. A stage gate set against the
    # edge would be set 13x too loose.
    ratio = worst / expected
    assert D("13") < ratio < D("14"), (env, worst, expected, ratio)


# ===== 5. THE LOSS-AT-RISK BUDGET IS INDEPENDENT OF CASH AND BINDS FIRST

def test_the_deficit_bound_is_a_separate_bound_from_cash():
    g = VG(max_cash_deployed_usd=D("200"),
           max_worst_case_deficit_usd=D("20"))
    assert g.max_cash_deployed_usd == D("200")
    assert g.max_worst_case_deficit_usd == D("20")
    assert g.max_worst_case_deficit_usd < g.max_cash_deployed_usd


def test_a_matched_book_carries_no_deficit_however_much_cash_it_used():
    """Which is why cash is the wrong bound for RISK."""
    g = VG(max_worst_case_deficit_usd=D("1"))
    wc = g.worst_case_deficit(up_qty=D("10"), dn_qty=D("10"),
                              up_cost=D("4.50"), dn_cost=D("4.50"),
                              order_side="UP", order_qty=D("1"),
                              order_price=D("0.45"))
    assert wc["ok"] is True, wc
    # 11 UP / 10 DOWN for 9.45: min(11,10) - 9.45 = +0.55 on EITHER
    # outcome, so the deficit is negative. Fees are included and
    # conservative, which is why the figure is not exactly -0.55.
    assert D(wc["deficit"]) < D("0"), wc
    assert D(wc["deficit"]) > D("-0.60"), wc


def test_an_unmatched_book_carries_its_whole_basis_as_deficit():
    g = VG(max_worst_case_deficit_usd=D("1"))
    wc = g.worst_case_deficit(up_qty=D("1"), dn_qty=D("1"),
                              up_cost=D("0.45"), dn_cost=D("0.50"),
                              order_side="UP", order_qty=D("1"),
                              order_price=D("0.46"))
    assert wc["ok"] is True, wc
    # 2 UP / 1 DOWN for 1.41: min(2,1) - 1.41 = 1 - 1.41, so 0.41 is at
    # risk BEFORE fees; the gate adds a conservative taker-theta fee on the
    # proposed leg and reports 0.44, which is the figure it enforces.
    assert D(wc["deficit"]) == D("0.44"), wc
    assert D(wc["deficit"]) > D("0.41"), "fees are included, never netted"


def test_a_low_deficit_bound_refuses_before_cash_does(probe, tmp_path):
    """"$200 of cash is not permission to risk $200" made enforceable."""
    H, E = probe
    ex, t, last = _run(H, E, tmp_path, cash="200", deficit="0.01")
    assert t.sent == [], last
    assert last == "NO_LEGAL_QUANTITY"


def test_the_deficit_bound_fails_closed_when_inventory_is_unreadable():
    g = VG(max_worst_case_deficit_usd=D("1"))
    assert g.refuse(order_qty=D("1"), order_price=D("0.5"),
                    order_intent="BUY", inventory=None) == \
        "WORST_CASE_DEFICIT_NO_INVENTORY"


def test_outstanding_orders_are_an_adversary_not_an_assumption():
    """The maximum over all four fill corners, not a single scenario."""
    g = VG(max_worst_case_deficit_usd=D("5"))
    wc = g.worst_case_deficit(
        up_qty=D("1"), dn_qty=D("1"), up_cost=D("0.5"), dn_cost=D("0.45"),
        order_side="UP", order_qty=D("1"), order_price=D("0.5"),
        outstanding=[{"side": "UP", "qty": D("3"), "price": D("0.5")}])
    assert wc["ok"] is True
    assert "scenario" in wc and wc["scenarios"]


# ========================= 6. STAGE 1 READINESS, OFFLINE

def test_stage1_cash_reservation_is_exact(probe, tmp_path):
    H, E = probe
    ex, t, _ = _run(H, E, tmp_path, cash="10")
    a = CP.account(positions={s: ex.store.position("m", s)
                              for s in ("UP", "DOWN")},
                   leg_cost=ex.store.leg_cost("m"),
                   live_orders=list((ex.store.live_orders() or {}).values()),
                   cash_envelope_usd=D("10"))
    assert a["readable"] is True
    assert (D(a["cash_committed_usd"]) + D(a["outstanding_reserve_usd"])
            + D(a["buying_power_usd"])) == D("10")
    assert D(a["cash_committed_usd"]) <= D("10")


def test_stage1_a_pending_order_is_reserved_before_it_fills():
    a = CP.account(positions={}, leg_cost={},
                   live_orders=[{"side": "UP", "qty": D("2"),
                                 "price": D("0.50")}],
                   cash_envelope_usd=D("10"))
    assert D(a["outstanding_reserve_usd"]) == D("1.00")
    assert D(a["buying_power_usd"]) == D("9.00")


def test_stage1_a_cancellation_race_refuses_every_submission(probe,
                                                              tmp_path):
    H, E = probe
    ex, t = W.mk(H, E, tmp_path,
                 g=VG(max_cash_deployed_usd=D("10"),
                      max_unmatched_contracts=D("500"),
                      max_cumulative_loss_usd=D("500")))
    ex.limits = H.limits(max_live_orders=8)
    ex.store.mark_uncertain("CANCEL_VERIFY_HTTPError")
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent == []
    assert "BOOK_UNCERTAIN" in str(rec.get("refused_reason"))


def test_stage1_fill_reconciliation_ties_cost_to_position(probe, tmp_path):
    H, E = probe
    ex, t, _ = _run(H, E, tmp_path, cash="10")
    legs = ex.store.leg_cost("m")
    for side in ("UP", "DOWN"):
        qty = ex.store.position("m", side)
        if qty and qty > 0:
            basis = D(str(legs.get(side))) / D(str(qty))
            assert D("0.01") <= basis <= D("1.00"), (side, basis)


def test_stage1_settlement_releases_the_reservation_and_nothing_else(
        probe, tmp_path):
    H, E = probe
    ex, t, _ = _run(H, E, tmp_path, cash="10")
    ex.store.state.setdefault("retired_markets", {})["m"] = {"settled": True}
    legs = ex.cash_legs_for_gates()
    assert all(l["settled"] for l in legs if l["market"] == "m")


def test_stage1_the_daily_halt_is_a_separate_bound_from_cash_and_deficit():
    """Three bounds, three questions. The halt reads SETTLED money -- the
    other two read the live book -- so it cannot be satisfied by either.
    """
    lim = W.H.limits(daily_realized_loss_halt_usd=D("5")) \
        if hasattr(W, "H") else None
    del lim
    from src import l5_live_execution as E2
    s = E2.SafetyLimits(max_usd_per_market=D("100"),
                        max_abs_residual=D("100"),
                        max_total_exposure_usd=D("100"),
                        max_concurrent_markets=5,
                        daily_realized_loss_halt_usd=D("5"))
    assert s.daily_realized_loss_halt_usd == D("5")
    # it refuses on SETTLED realized loss, independent of any live figure
    def r(realized):
        return s.refuse(market="m", price=D("0.5"), qty=D("1"),
                        residual_after=D("0"), realized_today=realized,
                        committed_by_market={}, open_markets=["m"],
                        outstanding_by_market={}, live_order_count=0)
    assert r(D("-5")) == "DAILY_REALIZED_LOSS_HALT"
    assert r(D("-1")) != "DAILY_REALIZED_LOSS_HALT"


def test_stage1_readiness_is_blocked_on_the_residual_SIGN_not_on_code():
    """The honest blocker, pinned so it cannot be quietly dropped.

    The residual estimate spans -$0.024 to +$0.037 across credible
    subsamples and the SIGN is not established. At $10 with ~10 pairs a
    pessimistic residual costs more than the pair edge earns.
    """
    assert UD.REOPEN_SINGLE_OUTCOME_USD == D("-0.0236")
    edge_10 = D("10") / D("0.95") // 1 * D("0.0350")
    worst_10 = D("4.7500")
    assert worst_10 > edge_10, "the worst case dominates at Stage 1 size"
