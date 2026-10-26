"""Can the worst-outcome crossing rule bleed money through repetition?

The owner's precondition for `L5_TS_COMPLETION_CROSS=YES`: verify the rule
cannot repeatedly create excessive total losses through PARTIAL COMPLETIONS,
FEES, or MULTIPLE ATTEMPTS. These tests answer it on the deployed predicate
rather than by argument.

The safety argument the simulation checks:

  Each cross must pass TWO bars, not one. `top_up` requires the worse
  settlement outcome to improve; then `improvement > taker_fee` requires that
  improvement to exceed the fee paid for it. The module's own comment explains
  why the second bar exists -- worst-case improvement alone is "very nearly
  VACUOUS once a book is one-sided", passing at 0.35 through 0.95 on a 5-share
  0.59 leg.

  Because each step's `worst_before` is the previous step's `worst_after`, the
  improvements telescope: sum(improvement) = worst_final - worst_initial, and
  since every step had improvement_i > fee_i, the total improvement strictly
  exceeds the total fees. Repetition cannot invert that.

  It also terminates: each cross reduces the deficit by at least `min_qty`,
  and a matched book returns ALREADY_MATCHED.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_two_sided_probe as TSP


def _blend(qty: D, px: D, add_qty: D, add_px: D) -> D:
    """Blended own cost after adding `add_qty` at `add_px`."""
    tot = qty + add_qty
    return ((qty * px) + (add_qty * add_px)) / tot if tot > 0 else D(0)


def _simulate(*, up_qty, up_px, cross_px, depth_per_tick, cash,
              ceiling="2.00", min_qty="0.01", max_steps=200):
    """Repeatedly take whatever the deployed rule offers, applying each fill.

    `depth_per_tick` is deliberately small to FORCE partial completions --
    the case the owner asked about.
    """
    a, apx = D(up_qty), D(up_px)
    b, bpx = D(0), D(0)
    fees_paid, cash_left = D(0), D(cash)
    first_worst = None
    steps = []
    for _ in range(max_steps):
        dec = TSP.completion_decision(
            up_qty=a, dn_qty=b, up_cost_px=apx, dn_cost_px=bpx,
            taker_price=D(cross_px), available_depth=D(depth_per_tick),
            tick="0.01", min_qty=min_qty, cash_headroom=cash_left,
            worst_case_ceiling=ceiling, book_age_s="0.5")
        if not dec.get("ok"):
            steps.append({"stop": dec.get("reason")})
            break
        if first_worst is None:
            first_worst = D(dec["worst_before"])
        qty = D(dec["qty"])
        fee = D(dec["taker_fee_usd"])
        assert D(dec["worst_improvement_usd"]) > fee, "bar 2 violated"
        # apply the fill to the deficient side
        if dec["side"] == TSP.DOWN:
            bpx = _blend(b, bpx, qty, D(cross_px)); b += qty
        else:
            apx = _blend(a, apx, qty, D(cross_px)); a += qty
        fees_paid += fee
        cash_left -= (qty * D(cross_px) + fee)
        steps.append({"qty": str(qty), "fee": str(fee),
                      "worst_after": dec["worst_after"],
                      "partial": dec["partial"]})
    return {"steps": steps, "fees_paid": fees_paid, "up": a, "dn": b,
            "first_worst": first_worst, "cash_left": cash_left}


def test_repeated_partial_completions_terminate():
    """Each cross shrinks the deficit, so the loop cannot run forever."""
    out = _simulate(up_qty="1.56", up_px="0.44", cross_px="0.50",
                    depth_per_tick="0.20", cash="5")
    assert out["steps"][-1].get("stop"), "never terminated"
    assert len(out["steps"]) < 200
    # it genuinely partialled rather than completing in one go
    assert any(s.get("partial") for s in out["steps"][:-1])


def test_cumulative_improvement_strictly_exceeds_cumulative_fees():
    """THE OWNER'S QUESTION. Improvements telescope, every step paid for
    itself, so the total cannot invert however many partials occur."""
    out = _simulate(up_qty="1.56", up_px="0.44", cross_px="0.50",
                    depth_per_tick="0.20", cash="5")
    taken = [s for s in out["steps"] if "qty" in s]
    assert len(taken) >= 2, "need several partials to be a real test"
    final_worst = D(taken[-1]["worst_after"])
    total_improvement = final_worst - out["first_worst"]
    assert total_improvement > out["fees_paid"], (
        total_improvement, out["fees_paid"])


def test_fees_can_never_exceed_the_cash_headroom():
    """Sizing shrinks on the venue grid until principal AND fee fit, so the
    run can never spend past the headroom it was given."""
    out = _simulate(up_qty="1.56", up_px="0.44", cross_px="0.50",
                    depth_per_tick="0.20", cash="0.40")
    assert out["cash_left"] >= 0, out["cash_left"]


def test_the_fee_bar_does_NOT_constrain_dear_completions():
    """MEASURED, AND IT CONTRADICTS THE MODULE'S OWN COMMENT.

    The second bar (`improvement > taker_fee`) was added because worst-case
    improvement alone is "very nearly VACUOUS once a book is one-sided". It
    does not fix that. The taker fee is Theta*C*p*(1-p), which COLLAPSES
    toward zero at extreme prices, while the worst-case improvement stays
    positive -- so the dearer the cross, the weaker the bar guarding it.

    At campaign scale (1.56 UP at 0.44) with the deployed $2.00 worst-case
    ceiling, every price from 0.40 to 0.95 is permitted. A cross at 0.95
    makes the pair 1.39 and books a CERTAIN -$0.6484 -- 94% of the $0.6864
    acquisition -- and still passes, because holding's worst case is
    -$0.6864 and the fee is only $0.01.
    """
    permitted = []
    for px in ("0.40", "0.55", "0.70", "0.80", "0.90", "0.95"):
        dec = TSP.completion_decision(
            up_qty="1.56", dn_qty="0", up_cost_px="0.44", dn_cost_px="0",
            taker_price=px, available_depth="38", tick="0.01",
            min_qty="0.01", cash_headroom="5", worst_case_ceiling="2.00",
            book_age_s="0.5")
        if dec.get("ok"):
            permitted.append(px)
    assert permitted == ["0.40", "0.55", "0.70", "0.80", "0.90", "0.95"], (
        permitted)
    # and the dear one really does lock a near-total certain loss
    dear = TSP.completion_decision(
        up_qty="1.56", dn_qty="0", up_cost_px="0.44", dn_cost_px="0",
        taker_price="0.95", available_depth="38", tick="0.01",
        min_qty="0.01", cash_headroom="5", worst_case_ceiling="2.00",
        book_age_s="0.5")
    assert dear["ok"] is True
    assert D(dear["worst_after"]) == D("-0.6484")
    assert D(dear["taker_fee_usd"]) == D("0.01")


def test_the_worst_case_deficit_ceiling_is_the_control_that_actually_binds():
    """So the gate that makes L5_TS_COMPLETION_CROSS safe is the EXISTING
    `max_worst_case_deficit_usd`, which at the deployed $2.00 is about three
    times the whole acquisition cost at campaign scale and therefore never
    binds. Tightened to $0.10 it admits exactly the sound exits and refuses
    every ruinous one. No code change -- it only tightens.
    """
    def permitted(ceiling):
        out = []
        for px in ("0.40", "0.50", "0.55", "0.60", "0.70", "0.80", "0.95"):
            dec = TSP.completion_decision(
                up_qty="1.56", dn_qty="0", up_cost_px="0.44",
                dn_cost_px="0", taker_price=px, available_depth="38",
                tick="0.01", min_qty="0.01", cash_headroom="5",
                worst_case_ceiling=ceiling, book_age_s="0.5")
            if dec.get("ok"):
                out.append(px)
        return out

    assert permitted("2.00") == ["0.40", "0.50", "0.55", "0.60", "0.70",
                                 "0.80", "0.95"]
    # at $0.10 only pairs at or below 0.99 survive -- true locks
    assert permitted("0.10") == ["0.40", "0.50", "0.55"]
    # the real 1945z exit instant is inside it; the ruinous ones are not
    assert "0.55" in permitted("0.10")          # pair 0.99
    assert "0.60" not in permitted("0.10")      # pair 1.04
    assert "0.95" not in permitted("0.10")      # pair 1.39


def test_a_cross_that_does_not_pay_for_itself_is_named_not_silent():
    """Refusals must be machine-readable, as every other gate's are."""
    seen = set()
    for px in ("0.50", "0.70", "0.85", "0.94"):
        dec = TSP.completion_decision(
            up_qty="0.02", dn_qty="0", up_cost_px="0.59", dn_cost_px="0",
            taker_price=px, available_depth="10", tick="0.01",
            min_qty="0.01", cash_headroom="20", worst_case_ceiling="10",
            book_age_s="0.5")
        if not dec.get("ok"):
            seen.add(dec["reason"])
    assert seen, "expected at least one refusal on a tiny leg"
    for r in seen:
        assert r == r.upper() and " " not in r, r


def test_the_worst_case_ceiling_is_never_breached_by_an_improvement():
    """An improvement that breaches the authorized bound is not permission
    to breach it."""
    dec = TSP.completion_decision(
        up_qty="5", dn_qty="0", up_cost_px="0.59", dn_cost_px="0",
        taker_price="0.40", available_depth="10", tick="0.01",
        min_qty="0.01", cash_headroom="20", worst_case_ceiling="0.01",
        book_age_s="0.5")
    assert dec.get("ok") is False
    assert dec["reason"] == "COMPLETION_EXCEEDS_WORST_CASE_CEILING"


def test_attempts_are_throttled_and_class_budgeted():
    """Beyond the economics, two independent counters bound repetition: the
    runner's own 10 s throttle and the campaign's COMPLETE class budget."""
    import re
    src = open("src/l5_pmus_live_runner.py").read()
    assert re.search(r"TS_COMPLETION_EVERY_S\s*=\s*10\.0", src)
    assert "if now - self.last_ts_completion_t < self.TS_COMPLETION_EVERY_S:" in src
    auth = open("src/l5_live_authorization.py").read()
    assert '"COMPLETE": "L5_LIVE_AUTH_MAX_COMPLETE"' in auth
