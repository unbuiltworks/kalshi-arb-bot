"""The two-sided controller: state machine and the accounting proven live.

No test here places an order; the module holds no transport and no order path.
"""
from decimal import Decimal as D

import pytest

from src import pmus_fees as FEES
from src.l5_two_sided import (
    ARM_ADAPTED, ARM_REPLICATION, COMPLETE_NOW, EXIT_NOW, ONE, TICK, WAIT,
    Pair, TwoSided, best_action, compare_actions, completion_budget)


def _ladder(bid, ask, bid_qty="100", ask_qty="100"):
    return {"bids": {D(str(bid)): D(str(bid_qty))},
            "offers": {D(str(ask)): D(str(ask_qty))}}


def _book(bid, ask, **kw):
    return {"bid": D(str(bid)), "ask": D(str(ask)),
            "bid_qty": D("100"), "ask_qty": D("100"),
            "_ladder": _ladder(bid, ask, **kw)}


# ------------------------------------------- the module cannot place orders

def test_the_module_holds_no_order_path():
    import inspect

    from src import l5_two_sided as M
    src = inspect.getsource(M)
    for forbidden in ("/v1/orders", "/v1/order/", "Transport", "requests.",
                      "urlopen", "PMUS_API_SECRET_KEY", "submit("):
        assert forbidden not in src, forbidden


# ------------------------------------------------------- completion budget

def test_the_budget_is_searched_on_the_tick_grid():
    """Every returned budget is a quotable price, not an arithmetic artefact."""
    for held in ("0.20", "0.35", "0.55", "0.70"):
        b = completion_budget(D(held), D(1), held_maker=True,
                              completion_maker=True)
        assert b is not None
        assert (b / TICK) == (b / TICK).to_integral_value(), b


def test_the_budget_actually_locks_the_floor_it_promises():
    held, qty, floor = D("0.35"), D(1), D("0.01")
    b = completion_budget(held, qty, held_maker=True, completion_maker=True,
                          pair_floor=floor)
    locked = qty - held * qty - b * qty
    assert locked >= floor
    # and one tick higher would break it
    assert (qty - held * qty - (b + TICK) * qty) < floor


def test_a_taker_completion_budget_is_tighter_than_a_maker_one():
    mk = completion_budget(D("0.35"), D(1), held_maker=True,
                           completion_maker=True)
    tk = completion_budget(D("0.35"), D(1), held_maker=True,
                           completion_maker=False)
    assert tk < mk, (tk, mk)


def test_the_cash_basis_budget_can_differ_from_the_bound_basis():
    """Cent rounding is not cosmetic: it is a different number at some prices."""
    diffs = 0
    for cents_px in range(20, 81):
        held = D(cents_px) / D(100)
        a = completion_budget(held, D(1), held_maker=False,
                              completion_maker=False, cash=False)
        b = completion_budget(held, D(1), held_maker=False,
                              completion_maker=False, cash=True)
        if a != b:
            diffs += 1
    assert diffs > 0, "cash rounding never changed the budget; check the model"


# ------------------------------------------------ the three-way comparison

def test_the_comparison_uses_executable_prices_never_a_midpoint():
    c = compare_actions(held_side="up", held_px=D("0.35"), held_qty=D(1),
                        held_maker=True, comp_ask=D("0.57"),
                        own_bid=D("0.34"))
    # COMPLETE_NOW pays the ASK, not the midpoint of 0.34/0.57
    assert c[COMPLETE_NOW]["price"] == "0.57"
    # EXIT_NOW receives the BID
    assert c[EXIT_NOW]["price"] == "0.34"


def test_the_comparison_reproduces_the_live_q1_economics():
    """held YES 0.35, complement ask 0.57 -> +0.0630, as realized live."""
    c = compare_actions(held_side="up", held_px=D("0.35"), held_qty=D(1),
                        held_maker=True, comp_ask=D("0.57"), own_bid=D("0.34"))
    assert D(c[COMPLETE_NOW]["value"]) == D("0.0630")


def test_wait_reports_a_budget_and_never_asserts_a_value():
    c = compare_actions(held_side="up", held_px=D("0.35"), held_qty=D(1),
                        held_maker=True, comp_ask=None, own_bid=None)
    assert c[WAIT]["value"] is None
    assert c[WAIT]["completion_budget"] is not None
    assert c[COMPLETE_NOW]["executable"] is False
    assert c[EXIT_NOW]["executable"] is False


def test_exit_is_labelled_shadow_only():
    c = compare_actions(held_side="up", held_px=D("0.35"), held_qty=D(1),
                        held_maker=True, comp_ask=D("0.57"), own_bid=D("0.34"))
    assert "shadow only" in c[EXIT_NOW]["note"]


def test_the_arms_differ_only_in_the_chosen_action():
    """A bad completion and a tolerable loss: replication waits, adapted exits."""
    c = compare_actions(held_side="up", held_px=D("0.70"), held_qty=D(1),
                        held_maker=True, comp_ask=D("0.95"), own_bid=D("0.60"))
    assert D(c[COMPLETE_NOW]["value"]) < 0
    rep = best_action(c, arm=ARM_REPLICATION, residual_tolerance=D("0.05"))
    ada = best_action(c, arm=ARM_ADAPTED, residual_tolerance=D("0.05"))
    assert rep in (WAIT, EXIT_NOW)
    assert ada == EXIT_NOW


def test_replication_prefers_patience_when_nothing_is_good():
    c = compare_actions(held_side="up", held_px=D("0.40"), held_qty=D(1),
                        held_maker=True, comp_ask=D("0.65"), own_bid=D("0.39"))
    assert best_action(c, arm=ARM_REPLICATION) == WAIT


# ------------------------------------------------------ state machine rules

def test_rule_1_both_sides_are_quoted_when_flat():
    c = TwoSided(arm=ARM_REPLICATION)
    c.on_book(_book("0.40", "0.60"), 0.0)
    assert c.legs["up"] is not None, c.blocked
    assert c.legs["down"] is not None, c.blocked
    assert c.legs["up"].px == D("0.40")
    assert c.legs["down"].px == ONE - D("0.60")


def test_rule_2_each_leg_is_tracked_independently():
    c = TwoSided(arm=ARM_REPLICATION)
    c.on_book(_book("0.40", "0.60"), 0.0)
    up, dn = c.legs["up"], c.legs["down"]
    assert up.side == "up" and dn.side == "down"
    assert up.qty == dn.qty == c.clip
    assert up.filled == dn.filled == D(0)
    assert up.maker is dn.maker is True
    assert up.role_source == "our_action_record"


def test_rule_3_both_sides_filling_banks_a_pair_and_opens_a_new_cycle():
    c = TwoSided(arm=ARM_REPLICATION)
    c.on_book(_book("0.40", "0.60"), 0.0)
    # a print at 0.40 fills our UP; at 0.60 fills our DOWN (own terms 0.40)
    c.legs["up"].ahead = D(0)
    c.legs["down"].ahead = D(0)
    c.on_trade(D("0.40"), D(1), 1.0)
    c.on_trade(D("0.60"), D(1), 2.0)
    assert c.matched() == D(1), (c.qty, c.pairs)
    assert c.residual() == D(0)
    assert c.cycles_completed == 1
    assert c.cycle == 1


def test_rule_4_the_heavy_side_is_not_added_to_by_default():
    c = TwoSided(arm=ARM_REPLICATION)          # allow_extend False
    c.on_book(_book("0.40", "0.60"), 0.0)
    c.legs["up"].ahead = D(0)
    c.on_trade(D("0.40"), D(1), 1.0)           # long UP now
    assert c.residual() == D(1)
    c.on_book(_book("0.40", "0.60"), 2.0)
    assert c.legs["up"] is None, "added to the heavy side"
    assert "heavy_side_extend_disabled" in c.blocked


def test_rule_5_the_deficient_side_is_worked_inside_the_budget():
    c = TwoSided(arm=ARM_REPLICATION, allow_reactive_take=False)
    c.on_book(_book("0.40", "0.60"), 0.0)
    c.legs["up"].ahead = D(0)
    c.on_trade(D("0.40"), D(1), 1.0)
    c.on_book(_book("0.40", "0.60"), 2.0)
    dn = c.legs["down"]
    assert dn is not None
    budget = completion_budget(D("0.40"), D(1), held_maker=True,
                               completion_maker=True)
    assert dn.px <= budget


def test_rule_8_no_new_cycle_opens_near_settlement():
    c = TwoSided(arm=ARM_REPLICATION, no_new_cycle_s=120.0)
    c.window_end = 1000.0
    c.on_book(_book("0.40", "0.60"), 950.0)     # 50s left
    assert c.legs["up"] is None and c.legs["down"] is None
    assert "no_new_cycle_near_settlement" in c.blocked


def test_rule_8_residual_tolerance_tightens_near_settlement():
    c = TwoSided(arm=ARM_ADAPTED, tighten_residual_s=180.0)
    c.window_end = 1000.0
    c.now = 100.0
    assert c.residual_tolerance() == D("0.50")
    c.now = 900.0
    assert c.residual_tolerance() == D("0.05")


# ------------------------------------------------- accounting invariants

def _deficient_up(clip=D(1)):
    """A controller already long DOWN, so UP is the deficient side and its
    quote stays valid across fills instead of being pulled as the heavy side."""
    c = TwoSided(arm=ARM_REPLICATION, clip=clip, allow_reactive_take=False)
    c.qty["down"] = D(1)
    c.cost["down"] = D("0.55")
    return c


def test_fills_book_only_the_delta_and_never_cumulative_twice():
    c = _deficient_up()
    c.on_book(_book("0.40", "0.60"), 0.0)
    assert c.legs["up"] is not None, c.blocked
    c.legs["up"].ahead = D(0)
    c.on_trade(D("0.40"), D("0.4"), 1.0)        # partial
    assert c.qty["up"] == D("0.4")
    c.on_trade(D("0.40"), D("0.6"), 2.0)        # completes the clip
    assert c.qty["up"] == D("1.0"), "cumulative was double-booked"
    assert c.matched() == D(1)


def test_a_partial_on_the_deficient_side_keeps_its_queue_position():
    c = _deficient_up()
    c.on_book(_book("0.40", "0.60"), 0.0)
    leg = c.legs["up"]
    leg.ahead = D(0)
    c.on_trade(D("0.40"), D("0.4"), 1.0)
    assert c.legs["up"] is leg, "the remainder was re-placed"
    assert c.legs["up"].filled == D("0.4")
    assert c.legs["up"].remaining() == D("0.6")
    assert c.legs["up"].ahead0 == leg.ahead0


def test_a_partial_that_turns_its_own_side_heavy_is_pulled_not_extended():
    """Rule 4 beats queue preservation: a remainder that would DEEPEN the
    imbalance is given up, which is not the same as moving for a better touch."""
    c = TwoSided(arm=ARM_REPLICATION)
    c.on_book(_book("0.40", "0.60"), 0.0)
    c.legs["up"].ahead = D(0)
    c.on_trade(D("0.40"), D("0.4"), 1.0)        # now long UP: UP is heavy
    assert c.qty["up"] == D("0.4")
    assert c.legs["up"] is None
    assert "heavy_side_extend_disabled" in c.blocked


def test_the_no_side_is_inverted_exactly_once():
    """A DOWN quote at own-terms p sits on the ladder at 1-p, and only there."""
    c = TwoSided(arm=ARM_REPLICATION)
    c.on_book(_book("0.40", "0.60"), 0.0)
    dn = c.legs["down"]
    assert dn.px == D("0.40")                    # own terms
    # a print at wire 0.60 is own-terms 0.40 and reaches us
    dn.ahead = D(0)
    c.on_trade(D("0.60"), D(1), 1.0)
    assert c.qty["down"] == D(1)
    assert c.basis("down") == D("0.40")


def test_a_print_before_placement_can_never_fill_a_leg():
    c = TwoSided(arm=ARM_REPLICATION)
    c.on_book(_book("0.40", "0.60"), 10.0)
    c.legs["up"].ahead = D(0)
    c.on_trade(D("0.40"), D(1), 5.0)            # BEFORE placement
    assert c.qty["up"] == D(0)


def test_queue_is_consumed_only_by_prints_at_or_through_our_price():
    c = _deficient_up()
    c.on_book(_book("0.40", "0.60"), 0.0)
    leg = c.legs["up"]
    leg.ahead = D(50)
    # A print ABOVE our bid traded without reaching us: queue untouched.
    c.on_trade(D("0.45"), D(10), 1.0)
    assert leg.ahead == D(50)
    # At our price: queue is consumed.
    c.on_trade(D("0.40"), D(10), 2.0)
    assert leg.ahead == D(40)
    # THROUGH our price (below a bid) means our level was reached too.
    c.on_trade(D("0.35"), D(10), 3.0)
    assert leg.ahead == D(30)


def test_fees_use_unrounded_bounds_and_cent_cashflow_separately():
    p = Pair(D(1), D("0.35"), D("0.57"), up_maker=True, down_maker=False,
             cycle=0, t=0.0)
    assert p.fee_bound == FEES.taker_fee(D("0.57"), D(1))
    assert p.fee_cash == FEES.taker_fee(D("0.57"), D(1), cents=True)
    assert p.fee_cash == D("0.02") and p.fee_bound == D("0.0170")
    assert p.rebate_bound == FEES.maker_rebate(D("0.35"), D(1))
    assert p.rebate_cash == D("0.00")
    assert p.cost() == D("0.92")
    assert p.gross_edge() == D("0.08")


def test_a_taker_leg_never_receives_a_maker_rebate():
    p = Pair(D(1), D("0.35"), D("0.57"), up_maker=True, down_maker=False,
             cycle=0, t=0.0)
    assert p.rebate_bound == FEES.maker_rebate(D("0.35"), D(1))
    assert p.rebate_bound != (FEES.maker_rebate(D("0.35"), D(1))
                              + FEES.maker_rebate(D("0.57"), D(1)))


def test_rebates_are_reported_separately_and_never_netted():
    c = TwoSided(arm=ARM_REPLICATION)
    c.pairs.append(Pair(D(1), D("0.35"), D("0.57"), up_maker=True,
                        down_maker=False, cycle=0, t=0.0))
    s = c.settle("down")
    assert D(s["matched_gross_edge"]) == D("0.08")   # gross of any rebate
    assert D(s["maker_rebates_modeled_cashflow"]) == D("0.00")
    assert "never assumed credited" in s["rebate_note"]
    # the source-settled total excludes the modeled rebate
    assert D(s["source_settled_total"]) == D("0.08") - D("0.02")


def test_settlement_pays_a_matched_pair_either_way():
    c = TwoSided(arm=ARM_REPLICATION)
    c.pairs.append(Pair(D(1), D("0.35"), D("0.57"), up_maker=True,
                        down_maker=False, cycle=0, t=0.0))
    up = c.settle("up")
    dn = c.settle("down")
    assert up["source_settled_total"] == dn["source_settled_total"]


def test_a_residual_settles_as_a_directional_bet_on_its_own_line():
    c = TwoSided(arm=ARM_REPLICATION)
    c.qty["up"] = D(1)
    c.cost["up"] = D("0.40")
    win = c.settle("up")
    lose = c.settle("down")
    assert D(win["residual_settlement_pnl"]) == D("0.60")
    assert D(lose["residual_settlement_pnl"]) == D("-0.40")
    assert win["matched_contracts"] == "0"


def test_flatten_pnl_is_reported_on_its_own_line_and_marked_shadow():
    c = TwoSided(arm=ARM_ADAPTED)
    c.flatten_pnl = D("-0.03")
    s = c.settle("down")
    assert D(s["flatten_pnl_shadow"]) == D("-0.03")
    assert c.report(winner="down")["shadow_only"] is True


def test_totals_are_none_without_an_authoritative_winner():
    c = TwoSided(arm=ARM_REPLICATION)
    c.qty["up"] = D(1)
    c.cost["up"] = D("0.40")
    s = c.settle(None)
    assert s["source_settled_total"] is None
    assert s["shadow_total_with_flatten"] is None


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN BUG, deliberately not patched yet (owner instruction). In "
    "decide_completion's EXIT_NOW branch, `self.qty[held] -= held_qty` runs "
    "BEFORE `self.cost[held] -= self.basis(held) * held_qty`, so basis is "
    "recomputed on the ALREADY-REDUCED quantity. A flatten therefore changes "
    "the average cost of what remains, and a FULL flatten leaves the cost "
    "behind entirely because basis() returns 0 when qty is 0. The stale cost "
    "then inflates the next acquisition's basis without bound, which is how "
    "pairs priced above $1 and negative matched gross edge arise."))
def test_a_flatten_must_not_change_the_average_cost_of_what_remains():
    from src.l5_two_sided import ARM_REPLICATION, TwoSided
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    c.qty["up"] = D(2)
    c.cost["up"] = D(2) * D("0.40")
    before = c.basis("up")
    c.qty["up"] -= D(1)
    c.cost["up"] -= c.basis("up") * D(1)
    assert c.basis("up") == before
