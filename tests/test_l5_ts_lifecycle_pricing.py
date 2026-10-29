"""The side/price transformation, and the two lifecycle decisions.

PURE FUNCTIONS ONLY. `plan_executable` and `complement_order` read prices and
nothing else -- no file, no clock, no order path -- so they are pinned here
against the ACTUAL 0330z production numbers before anything is wired to them.
"""
from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as FT
from src import l5_two_sided_probe as TS

# the 0330z plan and the book it was frozen against, from the record
P_UP, P_DN = D("0.4000"), D("0.5400")
PLAN = {"legs": [{"side": "UP", "price": "0.4000", "qty": "3.32"},
                 {"side": "DOWN", "price": "0.5400", "qty": "3.32"}],
        "pair_cost": "0.9400", "qty_per_leg": "3.32"}
FROZEN_BID, FROZEN_ASK = "0.42", "0.44"     # the touch it was priced off
MOVED_BID, MOVED_ASK = "0.91", "0.92"       # where the book went


# ============================================== item 4: the transformation

def test_a_DOWN_buy_at_0_54_IS_a_YES_ask_at_0_46():
    """THE RECONCILIATION. The plan says DOWN 0.5400 and the queue row says
    wire 0.4600; they are the same order. A DOWN buy at own cost q IS a sell
    of YES at 1-q, resting as an ASK."""
    assert TS.wire_price("DOWN", "0.5400") == D("0.46")
    assert D("0.5400") + TS.wire_price("DOWN", "0.5400") == D(1)
    assert TS.resting_side("DOWN") == "ASK"
    # and UP is quoted and rested at the same number, as a BID
    assert TS.wire_price("UP", "0.4000") == D("0.40")
    assert TS.resting_side("UP") == "BID"


def test_behind_the_touch_means_a_LOWER_own_price_on_BOTH_sides():
    """And on DOWN that is a HIGHER wire price -- two ticks ABOVE the ask,
    which is away from the touch exactly as `bid - 2 ticks` is."""
    up = TS.quote_own_price("UP", bid=FROZEN_BID, ask=FROZEN_ASK,
                            tick="0.01", depth_ticks=2)
    dn = TS.quote_own_price("DOWN", bid=FROZEN_BID, ask=FROZEN_ASK,
                            tick="0.01", depth_ticks=2)
    assert up == P_UP                       # 0.42 - 0.02
    assert dn == P_DN                       # (1 - 0.44) - 0.02
    assert TS.wire_price("DOWN", dn) == D("0.46") == D(FROZEN_ASK) + D("0.02")
    assert up < TS.touch_own_price("UP", bid=FROZEN_BID, ask=FROZEN_ASK)
    assert dn < TS.touch_own_price("DOWN", bid=FROZEN_BID, ask=FROZEN_ASK)


def test_the_derived_prices_are_EXACTLY_what_build_orders_produces(monkeypatch):
    """One arithmetic, two call sites. If these ever disagree a complement
    would be priced on a different rule from the leg it completes."""
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "X")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    o = TS.build_orders(market="m", bid=FROZEN_BID, ask=FROZEN_ASK,
                        bid_depth="900", ask_depth="900", tick="0.01",
                        min_qty="0.01", depth_ticks=2, cash_envelope="4.65",
                        max_worst_case_loss="5", book_age_s="0.05")
    built = {l["side"]: D(l["price"]) for l in o["legs"]}
    for side in ("UP", "DOWN"):
        assert built[side] == TS.quote_own_price(
            side, bid=FROZEN_BID, ask=FROZEN_ASK, tick="0.01", depth_ticks=2)


@pytest.mark.parametrize("side,own,bid,ask,crosses", [
    # the 0330z case: a YES ask at 0.46 with the bid at 0.91 is THROUGH it
    ("DOWN", "0.5400", MOVED_BID, MOVED_ASK, True),
    # the UP leg is merely left behind, not crossing
    ("UP", "0.4000", MOVED_BID, MOVED_ASK, False),
    # at the book it was priced for, neither crosses
    ("DOWN", "0.5400", FROZEN_BID, FROZEN_ASK, False),
    ("UP", "0.4000", FROZEN_BID, FROZEN_ASK, False),
    # a bid AT the ask crosses; one a tick below does not
    ("UP", "0.44", FROZEN_BID, FROZEN_ASK, True),
    ("UP", "0.43", FROZEN_BID, FROZEN_ASK, False),
    # an ask AT the bid crosses: own 0.58 -> wire 0.42 == bid
    ("DOWN", "0.58", FROZEN_BID, FROZEN_ASK, True),
    ("DOWN", "0.57", FROZEN_BID, FROZEN_ASK, False),
])
def test_crossing_is_decided_on_the_WIRE_price(side, own, bid, ask, crosses):
    assert TS.leg_crosses(side, own, bid=bid, ask=ask) is crosses


# ====================================== item 1: is the frozen plan still live

def test_the_frozen_plan_is_executable_at_the_book_it_was_priced_for():
    assert TS.plan_executable(PLAN, bid=FROZEN_BID, ask=FROZEN_ASK,
                              tick="0.01", depth_ticks=2) is None


def test_the_0330z_plan_is_retired_once_the_book_has_left_it():
    why = TS.plan_executable(PLAN, bid=MOVED_BID, ask=MOVED_ASK,
                             tick="0.01", depth_ticks=2)
    assert why == "LEG_WOULD_CROSS:DOWN"


def test_a_fall_crosses_the_UP_leg_instead():
    """Symmetry. A bid resting at 0.40 is THROUGH an ask of 0.21."""
    why = TS.plan_executable(PLAN, bid="0.20", ask="0.21", tick="0.01",
                             depth_ticks=2)
    assert why == "LEG_WOULD_CROSS:UP"


def test_a_WIDENING_SPREAD_retires_the_round_with_nothing_crossing():
    """THE DRIFT CASE, and it is not a corner. On a two-cent book a leg
    cannot drift six ticks without the other one crossing, so drift fires
    when the SPREAD opens: the touch the complement was priced off walks
    away while neither leg crosses. That is precisely when completing stops
    paying, so retiring the round and re-planning is the right move.

    Here the bid has not moved at all -- the UP leg is still exactly right --
    and the ask has gone from 0.44 to 0.60, which moves DOWN's fair price
    from 0.54 to 0.38, sixteen ticks away.
    """
    why = TS.plan_executable(PLAN, bid="0.42", ask="0.60", tick="0.01",
                             depth_ticks=2)
    assert why == "PRICE_LEFT_BEHIND:DOWN:16_ticks"
    assert TS.leg_crosses("UP", P_UP, bid="0.42", ask="0.60") is False
    assert TS.leg_crosses("DOWN", P_DN, bid="0.42", ask="0.60") is False
    # and completing at the new fair price would NOT pay against the 0.40 leg
    out = TS.complement_order(side="DOWN", qty="3.32", cost_px="0.40",
                              bid="0.42", ask="0.60", tick="0.01",
                              min_qty="0.01", depth_ticks=2,
                              book_age_s="0.05")
    assert out["ok"] is True          # 0.40 + 0.38 = 0.78, cheaper still
    assert D(out["pair_cost"]) == D("0.78")


def test_a_small_move_inside_the_tolerance_keeps_the_round():
    for bid, ask in (("0.43", "0.45"), ("0.40", "0.42"), ("0.45", "0.47")):
        assert TS.plan_executable(PLAN, bid=bid, ask=ask, tick="0.01",
                                  depth_ticks=2) is None


def test_no_book_judges_NOTHING_rather_than_retiring_on_ignorance():
    for bid, ask in ((None, None), ("0", "0"), ("0.50", "0.50"),
                     ("0.60", "0.40")):
        assert TS.plan_executable(PLAN, bid=bid, ask=ask, tick="0.01",
                                  depth_ticks=2) is None


def test_the_drift_tolerance_can_be_tightened_and_never_widened(monkeypatch):
    monkeypatch.setenv(TS.STALE_DRIFT_ENV, "1")
    assert TS.stale_drift_ticks() == 1
    monkeypatch.setenv(TS.STALE_DRIFT_ENV, "999")
    assert TS.stale_drift_ticks() == TS.STALE_DRIFT_TICKS
    monkeypatch.setenv(TS.STALE_DRIFT_ENV, "nonsense")
    assert TS.stale_drift_ticks() == TS.STALE_DRIFT_TICKS


# ============================ item 2: the complement, from the current book

def test_the_complement_is_priced_off_the_CURRENT_book_not_the_frozen_plan():
    """The UP leg filled at 0.40; the book has since moved to 0.50/0.52. The
    complement must be quoted behind TODAY's touch, not at the frozen 0.54."""
    out = TS.complement_order(side="DOWN", qty="3.32", cost_px="0.40",
                              bid="0.50", ask="0.52", tick="0.01",
                              min_qty="0.01", depth_ticks=2,
                              book_age_s="0.05")
    assert out["ok"] is True
    assert D(out["leg"]["price"]) == D("0.46")      # (1 - 0.52) - 0.02
    assert D(out["leg"]["price"]) != P_DN
    assert D(out["leg"]["wire_price"]) == D("0.54")
    assert out["leg"]["resting_side"] == "ASK"
    assert out["leg"]["mode"] == FT.MAKER and out["leg"]["post_only"] is True
    # economics on what we ACTUALLY paid plus this quote
    assert D(out["pair_cost"]) == D("0.86")
    assert D(out["up_pnl"]) > 0 and D(out["dn_pnl"]) > 0


def test_the_complement_matches_the_FILLED_quantity_and_never_more():
    out = TS.complement_order(side="DOWN", qty="1.00", cost_px="0.40",
                              bid="0.50", ask="0.52", tick="0.01",
                              min_qty="0.01", depth_ticks=2,
                              book_age_s="0.05")
    assert D(out["leg"]["qty"]) == D("1.00")
    # a partial fill below the grid leaves nothing to match
    tiny = TS.complement_order(side="DOWN", qty="0.004", cost_px="0.40",
                               bid="0.50", ask="0.52", tick="0.01",
                               min_qty="0.01", depth_ticks=2,
                               book_age_s="0.05")
    assert tiny["ok"] is False
    assert tiny["reason"] == "NOTHING_TO_MATCH_ON_GRID"


def test_a_complement_that_no_longer_PAYS_is_refused_not_repriced():
    """THE DECISION THAT MATTERS. The first leg cost 0.40 and the book has
    run away, so the only available complement would put the pair at or above
    a dollar. That is refused -- the residual is carried under the existing
    limits rather than completed into a certain loss."""
    out = TS.complement_order(side="DOWN", qty="3.32", cost_px="0.40",
                              bid="0.36", ask="0.38", tick="0.01",
                              min_qty="0.01", depth_ticks=2,
                              book_age_s="0.05")
    assert out["ok"] is False
    assert out["reason"] in ("COMPLEMENT_BELOW_FEE_FLOOR",
                             "COMPLEMENT_NOT_POSITIVE_ON_BOTH_OUTCOMES",
                             "COMPLEMENT_PAIR_ABOVE_CAP")
    assert D(out["pair_cost"]) >= D("0.96")


def test_the_complement_is_judged_on_the_REALISED_cost_not_the_planned_one():
    """Leg one filled DEARER than planned. The complement's floor must move
    with it, or a pair that never paid would read as profitable."""
    cheap = TS.complement_order(side="DOWN", qty="3.32", cost_px="0.40",
                                bid="0.50", ask="0.52", tick="0.01",
                                min_qty="0.01", depth_ticks=2,
                                book_age_s="0.05")
    dear = TS.complement_order(side="DOWN", qty="3.32", cost_px="0.55",
                               bid="0.50", ask="0.52", tick="0.01",
                               min_qty="0.01", depth_ticks=2,
                               book_age_s="0.05")
    assert cheap["ok"] is True and dear["ok"] is False
    assert D(cheap["first_leg_cost"]) == D("0.40")


@pytest.mark.parametrize("kw,reason", [
    ({"book_age_s": None}, "BOOK_STALE"),
    ({"book_age_s": "9"}, "BOOK_STALE"),
    ({"gap_suspected": True}, "GAP_SUSPECTED"),
    ({"bid": None}, "BOOK_NOT_TWO_SIDED"),
    ({"ask": None}, "BOOK_NOT_TWO_SIDED"),
    ({"bid": "0.60", "ask": "0.50"}, "BOOK_CROSSED_OR_LOCKED"),
    ({"tick": "0"}, "TICK_UNKNOWN"),
    ({"min_qty": None}, "MIN_QTY_UNKNOWN"),
    ({"qty": "0"}, "NOTHING_TO_MATCH"),
    ({"cost_px": None}, "FIRST_LEG_COST_UNKNOWN"),
    ({"side": "SIDEWAYS"}, "COMPLEMENT_SIDE_UNKNOWN"),
])
def test_the_complement_fails_CLOSED_on_every_missing_input(kw, reason):
    base = dict(side="DOWN", qty="3.32", cost_px="0.40", bid="0.50",
                ask="0.52", tick="0.01", min_qty="0.01", depth_ticks=2,
                book_age_s="0.05")
    base.update(kw)
    out = TS.complement_order(**base)
    assert out["ok"] is False and out["reason"] == reason


def test_an_UP_complement_against_a_filled_DOWN_leg_is_symmetric():
    out = TS.complement_order(side="UP", qty="2.00", cost_px="0.54",
                              bid="0.42", ask="0.44", tick="0.01",
                              min_qty="0.01", depth_ticks=2,
                              book_age_s="0.05")
    assert out["ok"] is True
    assert D(out["leg"]["price"]) == D("0.40")
    assert out["leg"]["resting_side"] == "BID"
    assert D(out["pair_cost"]) == D("0.94")


# ======================================================= the pair-cost cap

def test_a_pair_above_the_0_96_SPRING_TARGET_is_now_refused(monkeypatch):
    """C3, CLOSED. This was the gap a one-variable change could open: at 0
    depth ticks a one-cent book gives a 0.99 pair, which `fee_floor` passes
    because it judges POST-ONLY execution -- `net_if_taker` is negative there
    and `both_positive_if_taker` is False. Its profitability depended entirely
    on a rebate, and L5_OPERATING_MODEL forbids assuming one."""
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "X")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    for dticks, pair in ((0, "0.99"), (1, "0.97")):
        o = TS.build_orders(market="m", bid="0.50", ask="0.51",
                            bid_depth="900", ask_depth="900", tick="0.01",
                            min_qty="0.01", depth_ticks=dticks,
                            cash_envelope="4.65", max_worst_case_loss="5",
                            book_age_s="0.05")
        assert o["ok"] is False
        assert o["reason"] == "PAIR_COST_ABOVE_CAP"
        assert D(o["pair_cost"]) == D(pair)
    # and the deployed setting is comfortably inside it
    ok = TS.build_orders(market="m", bid="0.50", ask="0.51",
                         bid_depth="900", ask_depth="900", tick="0.01",
                         min_qty="0.01", depth_ticks=2, cash_envelope="4.65",
                         max_worst_case_loss="5", book_age_s="0.05")
    assert ok["ok"] is True and D(ok["pair_cost"]) == D("0.95")
    assert ok["both_positive_if_taker"] is True


def test_the_cap_may_be_tightened_and_never_loosened(monkeypatch):
    monkeypatch.setenv(TS.MAX_PAIR_COST_ENV, "0.90")
    assert TS.max_pair_cost() == D("0.90")
    for bad in ("0.99", "1.50", "0", "-1", "nonsense", ""):
        monkeypatch.setenv(TS.MAX_PAIR_COST_ENV, bad)
        assert TS.max_pair_cost() == TS.MAX_PAIR_COST


def test_a_dear_first_leg_tightens_the_complement_against_the_same_cap():
    """The realised cost is what counts: 0.60 paid leaves at most 0.36 for
    the complement, and a 0.46 quote puts the pair at 1.06."""
    out = TS.complement_order(side="DOWN", qty="3.32", cost_px="0.60",
                              bid="0.50", ask="0.52", tick="0.01",
                              min_qty="0.01", depth_ticks=2,
                              book_age_s="0.05")
    assert out["ok"] is False
    assert out["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"
    assert D(out["pair_cost"]) == D("1.06")
    assert D(out["max_pair_cost"]) == D("0.96")
