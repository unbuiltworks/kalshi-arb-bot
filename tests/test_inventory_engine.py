"""Deterministic tests for the signed-inventory action evaluator.

Every test fixes every input, so a failure names a changed decision rule rather
than a changed market.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution.inventory_engine import (
    BUY_DOWN, BUY_UP, CANCEL, MAKER, REPRICE, TAKER, WAIT,
    Book, Config, Inventory, Limits, Prob, RestingOrder,
    evaluate, fee_dollars, maker_fill_prob, settlement_risk,
    MAKER_THETA, TAKER_THETA,
)

# A balanced book: yes_bid 0.50 / no_bid 0.49, so yes_ask = 0.51, spread 1c.
BOOK = Book(yes_bid=D("0.50"), yes_bid_qty=D(100),
            no_bid=D("0.49"), no_bid_qty=D(100), ts=0.0)
FLAT = Inventory()
TTE = 600.0


def _cfg(**kw):
    return Config(**kw)


def _prob(p, sigma="0.01", **kw):
    return Prob(p_up=D(p), sigma=D(sigma), source="TEST", **kw)


def _ev(inv=FLAT, book=BOOK, prob=None, resting=(), limits=None, cfg=None,
        tte=TTE, t=0.0):
    return evaluate(market="TEST-1", t=t, tte=tte, inv=inv, book=book,
                    prob=prob or _prob("0.50"), resting=resting,
                    limits=limits or Limits(), cfg=cfg or _cfg())


# ---------------------------------------------------------------- fee + risk
def test_fee_rounds_against_us_both_directions():
    # taker fee rounds UP
    assert fee_dollars(TAKER_THETA, D(1), D("0.50")) == D("0.02")
    # maker rebate rounds DOWN in magnitude (never rounds in our favour)
    assert fee_dollars(MAKER_THETA, D(1), D("0.50")) == D("-0.00")
    assert fee_dollars(MAKER_THETA, D(100), D("0.50")) == D("-0.31")
    # zero at the boundaries, convex in between
    assert fee_dollars(TAKER_THETA, D(10), D(0)) == D(0)
    assert fee_dollars(TAKER_THETA, D(10), D(1)) == D(0)
    assert (fee_dollars(TAKER_THETA, D(10), D("0.50"))
            > fee_dollars(TAKER_THETA, D(10), D("0.10")))


def test_matched_pairs_carry_no_settlement_risk():
    inv = Inventory(up_qty=D(5), dn_qty=D(5), up_cost=D("2.5"), dn_cost=D("2.45"))
    assert inv.resid == 0
    assert settlement_risk(inv.resid, D("0.5"), D("0.02")) == 0
    # a residual does, and it scales with size and with our own uncertainty
    assert settlement_risk(D(10), D("0.5"), D(0)) == D(5)
    assert settlement_risk(D(10), D("0.5"), D("0.02")) == D("5.2")
    assert settlement_risk(D(-10), D("0.5"), D(0)) == D(5)


def test_settlement_cashflows_are_complete():
    # 5 UP at 0.40 and 5 DOWN at 0.55, 0.02 of fees: a complete pair.
    inv = Inventory(up_qty=D(5), dn_qty=D(5), up_cost=D("2.00"),
                    dn_cost=D("2.75"), fees_paid=D("0.02"))
    # matched 5 redeems 5.00 either way; cost 4.75 + fees 0.02
    assert inv.settle(1) == D("0.23")
    assert inv.settle(0) == D("0.23")
    assert inv.worst_case_settlement() == D("0.23")
    # a long-UP residual pays only when YES wins
    long_up = Inventory(up_qty=D(5), dn_qty=D(0), up_cost=D("2.00"),
                        fees_paid=D("0.01"))
    assert long_up.settle(1) == D("2.99")
    assert long_up.settle(0) == D("-2.01")
    assert long_up.worst_case_settlement() == D("-2.01")
    # and symmetrically for a long-DOWN residual
    long_dn = Inventory(dn_qty=D(5), dn_cost=D("2.00"), fees_paid=D("0.01"))
    assert long_dn.settle(0) == D("2.99")
    assert long_dn.settle(1) == D("-2.01")


# ------------------------------------------------------------------- WAIT
def test_no_probability_signal_means_wait_not_a_fabricated_edge():
    d = _ev(prob=Prob(p_up=D("0.95"), sigma=D("0.01"), stale=True, source="T"))
    assert d.action == WAIT
    assert d.reason == "PROB_STALE"
    # the rejected candidates are still recorded, with the refusal reason
    assert all(c.reason == "PROB_STALE" for c in d.candidates)
    assert len(d.candidates) == 4


def test_uncertain_or_old_probability_means_wait():
    d = _ev(prob=_prob("0.95", sigma="0.30"))
    assert (d.action, d.reason) == (WAIT, "PROB_UNCERTAIN")
    d = _ev(prob=_prob("0.95", age_s=120.0))
    assert (d.action, d.reason) == (WAIT, "PROB_TOO_OLD")


def test_fair_market_has_no_edge_so_the_engine_waits():
    # p_up == mid: both sides cost more than they are worth once fees are paid.
    d = _ev(prob=_prob("0.505"))
    assert d.action == WAIT
    assert any("EDGE_BELOW_BAR" in c.reason for c in d.candidates)


def test_insufficient_edge_is_rejected_with_the_bar_named():
    # a 1c edge on a 5-lot is 0.05 gross, under the 0.06 extension bar.
    d = _ev(prob=_prob("0.52"), cfg=_cfg(min_edge_extend=D("0.06"),
                                        adverse_shift=D(0), kappa=D(0)))
    assert d.action == WAIT
    assert any("EDGE_BELOW_BAR" in c.reason for c in d.candidates)


# ------------------------------------------------------------- EXTENSION
def test_extension_allowed_only_when_marginal_risk_adjusted_ev_is_positive():
    # a large, confident mispricing: YES worth 0.70, lift-able at 0.51
    d = _ev(prob=_prob("0.70", sigma="0.01"))
    assert d.action == BUY_UP
    assert d.chosen.extends is True
    assert d.chosen.risk_adj_ev > 0
    assert d.chosen.resid_after > d.chosen.resid_before
    # the same mispricing with heavy risk aversion is refused
    d2 = _ev(prob=_prob("0.70", sigma="0.01"), cfg=_cfg(kappa=D(5)))
    assert d2.action == WAIT


def test_extension_bar_is_strictly_higher_than_the_reduction_bar():
    cfg = _cfg(kappa=D(0), adverse_shift=D(0), min_edge=D("0.02"),
               min_edge_extend=D("0.50"))
    # flat: buying UP extends, and is held to the higher bar
    flat = _ev(prob=_prob("0.56"), cfg=cfg)
    assert flat.action == WAIT
    # holding DOWN: the same buy now REDUCES, and clears the lower bar
    short = Inventory(dn_qty=D(5), dn_cost=D("2.45"))
    red = _ev(inv=short, prob=_prob("0.56"), cfg=cfg, tte=TTE)
    assert red.action == BUY_UP
    assert red.chosen.extends is False
    assert abs(red.chosen.resid_after) < abs(red.chosen.resid_before)


def test_extension_is_refused_late_in_the_window_but_reduction_is_not():
    cfg = _cfg(kappa=D(0), adverse_shift=D(0), tte_min_extend_s=300.0)
    d = _ev(prob=_prob("0.70"), cfg=cfg, tte=120.0)
    assert d.action == WAIT
    assert any(c.reason == "TOO_LATE_TO_EXTEND" for c in d.candidates)
    short = Inventory(dn_qty=D(5), dn_cost=D("2.45"))
    d2 = _ev(inv=short, prob=_prob("0.70"), cfg=cfg, tte=120.0)
    assert d2.action == BUY_UP


def test_pairs_are_not_force_completed():
    """A residual is held when completing it is the worse marginal trade.

    This is the behavioural difference from the pairing controller: holding a
    one-sided position is a legitimate outcome, not a failure to be repaired.
    """
    # long UP, and UP is still the cheap side relative to a confident p_up
    inv = Inventory(up_qty=D(5), up_cost=D("2.00"))
    d = _ev(inv=inv, prob=_prob("0.70", sigma="0.01"))
    assert d.action == BUY_UP          # extends, rather than buying DOWN to pair
    dn = [c for c in d.candidates if c.action == BUY_DOWN]
    assert dn and all(not c.ok for c in dn)


# ------------------------------------------------------------- HARD LIMITS
def test_residual_limit_caps_the_clip_and_then_refuses():
    lim = Limits(max_resid_qty=D(7))
    inv = Inventory(up_qty=D(5), up_cost=D("2.00"))
    d = _ev(inv=inv, prob=_prob("0.70"), limits=lim)
    assert d.action == BUY_UP
    assert d.chosen.qty == D(2)                       # clipped to the cap
    assert abs(d.chosen.resid_after) == D(7)
    at_cap = Inventory(up_qty=D(7), up_cost=D("2.80"))
    d2 = _ev(inv=at_cap, prob=_prob("0.70"), limits=lim)
    assert (d2.action, d2.reason) == (WAIT, "RESID_LIMIT")


def test_worst_case_loss_and_capital_limits_refuse():
    inv = Inventory(up_qty=D(20), up_cost=D("8.00"))
    d = _ev(inv=inv, prob=_prob("0.70"),
            limits=Limits(max_worst_case_loss=D("8.00"), max_resid_qty=D(100)))
    assert (d.action, d.reason) == (WAIT, "WORST_CASE_LIMIT")
    d2 = _ev(prob=_prob("0.70"), limits=Limits(max_capital=D("0.10")))
    assert d2.action == WAIT


def test_daily_loss_halt_refuses_everything():
    d = _ev(prob=_prob("0.70"), limits=Limits(daily_loss_remaining=D(0)))
    assert (d.action, d.reason) == (WAIT, "DAILY_LOSS_HALT")


def test_concurrent_order_limit_holds_the_slot():
    # the resting order's own reason still holds (low bar), so it is kept and
    # the single order slot is not released for a new one.
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5), t_live=0.0)
    d = _ev(prob=_prob("0.70"), resting=(o,), t=10.0,
            cfg=_cfg(min_edge_extend=D("0.01")),
            limits=Limits(max_concurrent_orders=1))
    assert d.action == WAIT
    assert d.reason in ("ORDER_SLOT_HELD", "KEEP")
    # no new buy candidate was even sized while the slot was held
    assert not any(c.action in (BUY_UP, BUY_DOWN) for c in d.candidates)


# --------------------------------------------------- PARTIAL / PENDING FILLS
def test_executable_quantity_is_bounded_by_book_depth_not_by_the_quote():
    thin = Book(yes_bid=D("0.50"), yes_bid_qty=D(100),
                no_bid=D("0.49"), no_bid_qty=D(2))
    d = _ev(book=thin, prob=_prob("0.70"), cfg=_cfg(clip_qty=D(5)))
    assert d.action == BUY_UP
    assert d.chosen.kind == TAKER
    assert d.chosen.qty == D(2)                 # a partial fill, not the clip


def test_a_pending_partial_fill_reduces_what_is_left_to_work():
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5),
                     filled=D(3), t_live=0.0)
    assert o.remaining == D(2)
    d = _ev(inv=Inventory(up_qty=D(3), up_cost=D("1.50")), prob=_prob("0.50"),
            resting=(o,), t=10.0)
    keep = [c for c in d.candidates if c.action in (CANCEL, REPRICE)]
    assert keep and keep[0].qty == D(2)         # only the unfilled remainder


def test_maker_fill_probability_is_never_credited_to_a_quote_we_are_behind():
    cfg = _cfg()
    # at the touch, with queue ahead: a fraction, never 1.0
    p = maker_fill_prob(BOOK, "UP", D("0.50"), D(5), 600.0, cfg)
    assert D(0) < p < D(1)
    # behind the touch: zero
    assert maker_fill_prob(BOOK, "UP", D("0.49"), D(5), 600.0, cfg) == 0
    # crossing: not a maker order
    assert maker_fill_prob(BOOK, "UP", D("0.60"), D(5), 600.0, cfg) == 0
    # no time left: zero
    assert maker_fill_prob(BOOK, "UP", D("0.50"), D(5), 0.0, cfg) == 0
    # a thinner queue ahead of us fills more readily
    thin = Book(yes_bid=D("0.50"), yes_bid_qty=D(1),
                no_bid=D("0.49"), no_bid_qty=D(100))
    assert maker_fill_prob(thin, "UP", D("0.50"), D(5), 600.0, cfg) > p


def test_adverse_selection_shifts_probability_against_the_filled_side():
    cfg_no_adv = _cfg(adverse_shift=D(0), allow_taker=False, kappa=D(0))
    cfg_adv = _cfg(adverse_shift=D("0.05"), allow_taker=False, kappa=D(0))
    good = _ev(prob=_prob("0.60"), cfg=cfg_no_adv)
    worse = _ev(prob=_prob("0.60"), cfg=cfg_adv)
    gu = next(c for c in good.candidates if c.action == BUY_UP)
    wu = next(c for c in worse.candidates if c.action == BUY_UP)
    assert wu.gross_ev < gu.gross_ev


# --------------------------------------------------------- CANCEL / REPRICE
def test_resting_order_is_cancelled_when_its_ttl_expires():
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5), t_live=0.0)
    d = _ev(prob=_prob("0.50"), resting=(o,), t=200.0,
            cfg=_cfg(order_ttl_s=120.0))
    assert (d.action, d.reason) == (CANCEL, "TTL_EXPIRED")


def test_resting_order_is_repriced_when_the_touch_moves_away():
    moved = Book(yes_bid=D("0.54"), yes_bid_qty=D(100),
                 no_bid=D("0.45"), no_bid_qty=D(100))
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5), t_live=0.0)
    d = _ev(book=moved, prob=_prob("0.70"), resting=(o,), t=10.0)
    assert (d.action, d.reason) == (REPRICE, "TOUCH_MOVED_AWAY")


def test_resting_order_is_cancelled_when_its_edge_evaporates():
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5), t_live=0.0)
    # placed on a 0.70 view; the view is now 0.50, so the order is stale
    d = _ev(prob=_prob("0.50"), resting=(o,), t=10.0)
    assert d.action == CANCEL
    assert d.reason.startswith("EDGE_GONE")


def test_resting_order_is_cancelled_when_probability_becomes_unusable():
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5), t_live=0.0)
    d = _ev(prob=Prob(p_up=D("0.70"), sigma=D("0.01"), stale=True, source="T"),
            resting=(o,), t=10.0)
    assert (d.action, d.reason) == (CANCEL, "PROB_UNUSABLE_WHILE_RESTING")


def test_resting_order_is_kept_while_its_reason_still_holds():
    o = RestingOrder(side="UP", kind=MAKER, own=D("0.50"), qty=D(5), t_live=0.0)
    d = _ev(prob=_prob("0.70"), resting=(o,), t=10.0,
            cfg=_cfg(min_edge_extend=D("0.01")))
    assert d.action == WAIT
    assert any(c.reason == "KEEP" for c in d.candidates)


# ------------------------------------------------------------------ LEDGER
def test_every_decision_records_the_rejected_alternatives_and_their_economics():
    d = _ev(prob=_prob("0.70"))
    row = d.as_row()
    assert row["action"] == BUY_UP
    assert row["p_up"] == "0.70" and row["prob_source"] == "TEST"
    assert row["inv_resid"] == "0" and row["worst_case_before"] == "0"
    assert row["chosen"]["qty"] and row["chosen"]["risk_adj_ev"]
    assert len(row["rejected"]) == 3
    for r in row["rejected"]:
        assert r["reason"]                         # never an unexplained refusal
        assert r["gross_ev"] is not None and r["worst_case_after"] is not None


def test_buy_down_is_chosen_on_a_down_side_mispricing():
    d = _ev(prob=_prob("0.30", sigma="0.01"))
    assert d.action == BUY_DOWN
    assert d.chosen.resid_after < 0


def test_buy_imbalance_is_not_treated_as_inherently_bad():
    """Two extensions on the same side in a row are permitted when each one's
    own marginal test passes. The engine has no rule against being long."""
    inv = FLAT
    cfg = _cfg(kappa=D("0.10"), min_edge_extend=D("0.02"))
    prob = _prob("0.75", sigma="0.01")
    seen = []
    for _ in range(3):
        d = _ev(inv=inv, prob=prob, cfg=cfg, limits=Limits(max_resid_qty=D(50)))
        if d.action != BUY_UP:
            break
        seen.append(d.chosen.qty)
        inv = inv.add("UP", d.chosen.qty, d.chosen.own, d.chosen.fee)
    assert len(seen) >= 2
    assert inv.resid > 0
