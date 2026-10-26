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


# ============ POINT 2: the $0.10 ceiling, on the REAL 1945z book
#
# Every price below is a complement own price that was ACTUALLY EXECUTABLE
# against displayed depth in cpc-btc-updown-15m-2026-10-25-1945z, taken from
# the window's own L5_RESIDUAL_BOOK_SAMPLE rows (109 executable instants of
# 121). Held inventory is the venue-confirmed 1.56 UP at own cost 0.44,
# acquisition $0.6864.
#
# The cheap complements all occur EARLY (0.55 at +204s, 0.56 at +120s,
# 0.58-0.68 between +126s and +526s); the dear ones only LATE (0.89-0.99
# between +606s and +819s). So the ceiling has to admit the early, sound
# exits and refuse the late, ruinous ones.

REAL_1945Z = [
    # (complement own px, first offset seen, pair own cost)
    ("0.55", 204, "0.99"), ("0.56", 120, "1.00"), ("0.58", 398, "1.02"),
    ("0.60", 307, "1.04"), ("0.63", 146, "1.07"), ("0.68", 133, "1.12"),
    ("0.89", 606, "1.33"), ("0.94", 566, "1.38"), ("0.99", 812, "1.43"),
]


def _real(px, ceiling):
    return TSP.completion_decision(
        up_qty="1.56", dn_qty="0", up_cost_px="0.44", dn_cost_px="0",
        taker_price=px, available_depth="38", tick="0.01", min_qty="0.01",
        cash_headroom="4.69", worst_case_ceiling=ceiling, book_age_s="0.5")


def test_the_010_ceiling_admits_the_beneficial_real_exits():
    """Risk-reducing and economically beneficial: pair at or below $1.00, so
    the exit books no certain loss beyond the fee."""
    for px, off, pair in REAL_1945Z:
        if D(pair) <= D("1.00"):
            dec = _real(px, "0.10")
            assert dec.get("ok") is True, (px, pair, dec.get("reason"))
            # it strictly improves the worse outcome, by more than its fee
            assert D(dec["worst_improvement_usd"]) > D(dec["taker_fee_usd"])
            # and it reduces, never increases, unmatched inventory
            assert D(dec["qty"]) <= D("1.56")


def test_the_010_ceiling_refuses_every_ruinous_real_exit():
    """A pair above ~1.02 books a certain loss bigger than the bound. At
    $0.10 each is refused BY THE CEILING, by name, and each of these prices
    was only executable late in the window."""
    for px, off, pair in REAL_1945Z:
        if D(pair) >= D("1.04") and D(px) <= TSP.MAX_PRICE:
            dec = _real(px, "0.10")
            assert dec.get("ok") is False, (px, pair)
            assert dec["reason"] == "COMPLETION_EXCEEDS_WORST_CASE_CEILING"


def test_the_dearest_real_complements_are_already_refused_by_the_band():
    """Measured, and it narrows the claim honestly: complements at or above
    0.96 are refused by the deployed price band (MIN_PRICE..MAX_PRICE =
    0.05..0.95) at EITHER ceiling, so the $2.00 gap is the 0.56-0.95 range
    rather than everything. 0.99 was executable at +812s."""
    assert TSP.MAX_PRICE == D("0.95")
    for ceiling in ("2.00", "0.10"):
        dec = _real("0.99", ceiling)
        assert dec.get("ok") is False
        assert dec["reason"] == "COMPLETION_PRICE_OUT_OF_BAND"


def test_the_deployed_200_ceiling_admits_the_in_band_ruinous_exits():
    """Which is why $2.00 is not a control at this size: every in-band price
    passes, including 0.95 -- pair 1.39, a certain -$0.6484 on a $0.6864
    leg, i.e. 94% of acquisition locked in."""
    for px, off, pair in REAL_1945Z:
        if D(px) <= TSP.MAX_PRICE:
            assert _real(px, "2.00").get("ok") is True, (px, pair)
    worst = _real("0.95", "2.00")
    assert D(worst["worst_after"]) == D("-0.6484")
    # and the same price is refused under the proposal
    assert _real("0.95", "0.10").get("ok") is False


def test_what_the_010_bound_actually_permits_is_bounded_downside():
    """The permitted set is exactly 'leaves at most $0.10 of certain
    downside', and each one is a large improvement on holding."""
    hold_worst = -(D("1.56") * D("0.44"))          # -0.6864
    permitted = []
    for px, off, pair in REAL_1945Z:
        dec = _real(px, "0.10")
        if dec.get("ok"):
            permitted.append(px)
            assert D(dec["worst_after"]) >= D("-0.10")
            assert D(dec["worst_after"]) > hold_worst
            assert D(dec["worst_improvement_usd"]) > D(dec["taker_fee_usd"])
    assert permitted == ["0.55", "0.56", "0.58"]


def test_the_ceiling_refuses_exposure_INCREASING_orders_beyond_the_limit():
    """The other half of the proof: the same bound must refuse an order that
    would leave the book able to lose more than it permits. Asserted on the
    deployed gate, with an exposure-increasing first leg."""
    from src import l5_live_validation_gates as VG
    g = VG.ValidationGates(max_cash_deployed_usd=D("4.69"),
                           max_unmatched_contracts=D("5"),
                           max_worst_case_deficit_usd=D("0.10"))
    # a NEW one-sided leg: nothing held, so the worst outcome loses it all
    why = g.refuse(order_qty=D("1.56"), order_price=D("0.44"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   filled_legs=(), outstanding=(),
                   unmatched_after=D("1.56"),
                   cumulative_realized=D(0),
                   cumulative_unrealized_loss=D(0),
                   inventory={"up_qty": "0", "dn_qty": "0",
                              "up_cost": "0", "dn_cost": "0"})
    assert why is not None, "a $0.69 one-sided leg must not clear a $0.10 bound"
    assert "WORST_CASE" in why or "DEFICIT" in why, why
