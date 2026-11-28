"""BOOK-RELATIVE TWO-SIDED QUOTING, AND THE EIGHT INVENTORY SCENARIOS.

WHY THIS EXISTS. Every inventory behaviour was implemented and none of it
could run. Two-sided quoting lived only in STRADDLE; STRADDLE priced from
a constant by design; and every rung was bounded to (0, 0.50) EXCLUSIVE.
Since UP + DOWN is about 1.00, one side is ALWAYS at or above 0.50, so
quoting both sides near the market was arithmetically impossible, not
merely unconfigured. Live proof, window 1500z 2026-11-17: rungs rested at
0.08 against a 0.30 book, UP filled twice, and the completion needed
about $2.66 against a $1.60 envelope -- so inventory management never
engaged and the window settled -$0.2992.

THE BOUND WAS A GENERALISATION ERROR. "A pair costs twice this and pays
1.00" is true only for a SYMMETRIC pair. UP 0.30 + DOWN 0.69 = 0.99 is a
valid pair the per-leg rule forbids. The correct invariant is on the
COMBINED cost.

WHAT THESE TESTS DO NOT CLAIM. Nothing here says book-relative quoting is
profitable, and nothing here measures a fill rate. A combined quote under
1.00 is NECESSARY for matched-pair economics before costs and is nowhere
near SUFFICIENT: it promises nothing about fills or realized P&L. The
entry-EV gate still has to admit every rung, and
`straddle_fill_model_measured` refuses the whole mode while the fill
model for these prices is unmeasured -- see
`TestTheFillModelIsUnmeasured`, which is the honest default.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C

#: the executor harness. `clean_env` is deliberately NOT imported: it is
#: autouse in its own module and would clear the controller switch for
#: the pure-function tests above.
from test_l5_btc5m_execution import armed, rig        # noqa: F401


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


#: UP 0.30/0.31 -> DOWN 0.69/0.70. Joining both bids costs 0.30 + 0.69 =
#: 0.99, which the per-leg 0.50 rule forbids and the pair ceiling allows.
BOOK = {"bid": "0.30", "ask": "0.31", "age_s": 0.1}
FLAT = {C.UP: {"qty": "0", "cost": "0"}, C.DOWN: {"qty": "0", "cost": "0"}}


def inv(up=("0", "0"), dn=("0", "0")):
    return {C.UP: {"qty": up[0], "cost": up[1]},
            C.DOWN: {"qty": dn[0], "cost": dn[1]}}


def params(**kw):
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("2.00"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("8.00"),
                straddle_book_relative=True,
                straddle_fill_model_measured=True,
                straddle_pair_ceiling=D("0.99"),
                straddle_join_ticks=0,
                straddle_repeat_enabled=True,
                straddle_max_side_usd=D("4.00"),
                straddle_max_unmatched_usd=D("3.00"),
                straddle_completion_pair_cost=D("0.90"),
                #: ---------------------------------------------------
                #: SYNTHETIC RATES, FOR MECHANISM ONLY.
                #:
                #: The MEASURED rates (0.070 / 0.131 / 0.0981) were taken
                #: at 0.08 rungs and the EV gate REFUSES with
                #: EV_NOT_POSITIVE when they are applied to a 0.30/0.69
                #: pair -- see TestTheMeasuredRatesDoNotTransfer, which
                #: pins exactly that. These higher numbers are NOT
                #: measured, NOT a claim of edge, and exist only so the
                #: downstream mechanism (placement, repetition,
                #: repricing, completion, caps) can be exercised at all.
                #: In production the gate refuses, which is why
                #: measurement mode needs separate authorization.
                straddle_p_pair_natural=D("0.95"),
                straddle_p_complete=D("0"),
                straddle_p_lone_win=D("0.50"),
                straddle_min_ev_per_share=D("0"))
    base.update(kw)
    return C.Params(**base)


def decide(book=BOOK, inventory=None, resting=(), t=30.0, **kw):
    return C.decide(book=book, t_in_window_s=t,
                    inventory=FLAT if inventory is None else inventory,
                    resting=list(resting), params=params(**kw))


def legs(res, side=None):
    return [o for o in res["orders"]
            if o.get("reason") == "STRADDLE_LEG"
            and (side is None or o["side"] == side)]


def comp(res):
    for o in res["orders"]:
        if o.get("reason") == "STRADDLE_COMPLETION":
            return o
    return None


def rec(res):
    return res["evaluation"]["book_relative"]


def resting_order(side, price, qty, oid="o1", age_s=1.0):
    return {"id": oid, "side": side, "price": D(price), "qty": D(qty),
            "age_s": age_s}


# ============================================ the bound that blocked it

class TestTheCombinedBoundReplacesThePerLegOne:

    def test_both_sides_quote_near_the_book(self):
        r = decide()
        ups, dns = legs(r, C.UP), legs(r, C.DOWN)
        assert ups and dns, r["evaluation"]
        assert ups[0]["price"] == D("0.30")    # joins UP's bid
        assert dns[0]["price"] == D("0.69")    # joins DOWN's bid, 1-ask

    def test_the_down_leg_is_above_the_old_per_leg_ceiling(self):
        #: THE WHOLE POINT. 0.69 > 0.50, so the old rule made this
        #: unreachable however it was configured.
        assert legs(decide(), C.DOWN)[0]["price"] > D("0.50")

    def test_the_pair_quote_cost_is_reported(self):
        assert rec(decide())["pair_quote_cost"] == "0.99"

    def test_a_pair_over_the_ceiling_refuses_both_legs(self):
        #: necessary condition only, and it binds on the TWO TOGETHER --
        #: one leg at a time cannot see a pair that costs too much.
        r = decide(straddle_pair_ceiling=D("0.95"))
        assert legs(r) == []
        assert rec(r)["why"] == "PAIR_ABOVE_CEILING"

    def test_a_ceiling_at_or_above_one_refuses_the_mode(self):
        for bad in (D("1.00"), D("1.20"), D("0")):
            r = decide(straddle_pair_ceiling=bad)
            assert r["why"] == "PARAMS_UNREADABLE", bad

    def test_quotes_never_cross(self):
        #: built from our own bid; a join_ticks that would reach our own
        #: offer is refused rather than crossing.
        r = decide(straddle_join_ticks=1)
        for o in legs(r):
            assert o["post_only"] is True
        r2 = decide(straddle_join_ticks=50)
        assert legs(r2) == []
        assert rec(r2)["why"] == "QUOTE_NOT_MAKEABLE"

    def test_improving_the_bid_on_a_one_tick_spread_would_cross(self):
        #: the book is 0.30/0.31. Improving UP's bid by a tick IS the
        #: offer, so it refuses rather than crossing: this mode never
        #: initiates.
        r = decide(straddle_join_ticks=1)
        assert legs(r) == []
        assert rec(r)["why"] == "QUOTE_NOT_MAKEABLE"

    def test_improving_the_bid_is_possible_on_a_wider_spread(self):
        wide = {"bid": "0.28", "ask": "0.33", "age_s": 0.1}
        r = decide(book=wide, straddle_join_ticks=1,
                   straddle_pair_ceiling=D("0.99"))
        ups = legs(r, C.UP)
        assert ups and ups[0]["price"] == D("0.29"), r["evaluation"]
        #: DOWN's bid is 1 - 0.33 = 0.67, improved to 0.68; the pair is
        #: 0.29 + 0.68 = 0.97, inside the ceiling.
        assert rec(r)["pair_quote_cost"] == "0.97"


# ======================================= the honest default: unmeasured

class TestTheFillModelIsUnmeasured:
    """The gate's three rates were measured at 0.08 rungs. Fed a 0.30
    rung they do not refuse -- they return a confident WRONG number. So
    book-relative quoting is refused until the fill model for these
    prices is measured and that is stated explicitly. This is not a
    bypass of the EV gate: the gate still runs and still has to admit."""

    def test_the_default_refuses(self):
        r = decide(straddle_fill_model_measured=False)
        assert legs(r) == []
        assert rec(r)["why"] == "FILL_MODEL_UNMEASURED"

    def test_the_ev_gate_is_still_applied_when_measured(self):
        #: a rung that cannot clear the edge floor is still refused, so
        #: declaring the model measured does not disable the gate.
        r = decide(straddle_min_ev_per_share=D("5.00"))
        assert legs(r) == []
        assert "ENTRY_EV" in str(r["why"])

    def test_the_record_states_the_permission(self):
        assert rec(decide())["fill_model_measured"] is True
        assert rec(decide(straddle_fill_model_measured=False))[
            "fill_model_measured"] is False


# ========================================== the completion reservation

class TestCompletionReservation:
    """Window 1500z: completing a 0.08 entry needed ~9x the entry and was
    unfundable, so it never fired. Buying q at p and completing at up to
    (ceiling - p) costs at most ceiling * q, so the reserve follows the
    actual entry price without guessing the completion price."""

    def test_it_shrinks_the_clip_rather_than_blocking(self):
        #: $1.60 of room at a 0.99 ceiling completes about 1.61 shares.
        #: Refusing outright would stop all accumulation on a small
        #: envelope, which is the opposite of the intent.
        r = decide(max_exposure_usd=D("1.60"), clip_usd=D("2.00"),
                   straddle_completion_reserve=True)
        res = r["evaluation"]["completion_reserve"]
        assert res["applied"] is True
        assert D(res["qty_after"]) < D(res["clip_qty"])
        assert legs(r), "accumulation must not be silently prevented"

    def test_the_reserved_size_is_completable_within_the_room(self):
        room = D("1.60")
        r = decide(max_exposure_usd=room, clip_usd=D("2.00"),
                   straddle_completion_reserve=True)
        q = D(r["evaluation"]["completion_reserve"]["qty_after"])
        assert q * D("0.99") <= room

    def test_only_a_size_below_the_venue_minimum_refuses_and_says_so(self):
        r = decide(max_exposure_usd=D("0.005"), clip_usd=D("2.00"),
                   straddle_completion_reserve=True)
        assert legs(r) == []
        assert r["evaluation"]["completion_reserve"]["why"] == (
            "COMPLETION_UNFUNDABLE")

    def test_it_is_off_by_default_and_changes_nothing(self):
        assert decide()["evaluation"]["completion_reserve"]["applied"] is False

    def test_reserving_does_not_imply_a_completion_will_happen(self):
        #: reserved capital is capital, not a fill. With no opposite
        #: inventory there is nothing to complete and no completion is
        #: emitted, reserve or not.
        r = decide(straddle_completion_reserve=True)
        assert comp(r) is None


# ============================== the measured rates do not reach here

class TestThePairIsPricedAsTheRealPair:
    """A CORRECTION TO AN EARLIER CONCLUSION OF MINE.

    This class used to assert that the measured rates "do not transfer"
    to book-relative prices, because the gate refused every such quote
    with EV_NOT_POSITIVE. That refusal was real but its cause was NOT a
    transfer problem -- it was two wrong inputs of mine:

      1. `p_lone_win` was the 0.08-measured constant 0.0981. A leg
         bought at 0.69 does not win 9.8% of the time; it wins about
         69%, because the price IS the implied probability. The 0.69
         rung priced at -0.534367 a share on that constant.
      2. `other_price` came from the FIXED ladder, so the gate valued
         the pair at 0.30 + 0.08 = 0.38 while the real quoted pair was
         0.99 -- inflating EV about sixtyfold once (1) was fixed, and
         admitting rungs on a pair that did not exist.

    Both are fixed: the lone leg's win rate is the rung's own price
    (which makes its contribution exactly zero -- conservative), and the
    opposite leg is the other side's BOOK quote. The gate now prices the
    pair that is actually being quoted.

    WHAT REMAINS UNMEASURED, and it is narrower than I claimed:
    `p_pair_natural` -- how often the second leg also fills at its quote
    -- was measured at 0.08 rungs and its value at the bid is unknown.
    That single number is what the discovery campaign exists to measure,
    and it is why the FILL_MODEL token is still warranted.
    """

    MEASURED = dict(straddle_p_pair_natural=D("0.070"),
                    straddle_p_complete=D("0.131"),
                    straddle_p_lone_win=D("0.0981"),
                    straddle_min_ev_per_share=D("0"))

    #: THE ARITHMETIC, SINCE 9c9a2a47b ("charge the unmatched leg for
    #: adverse selection"):
    #:
    #:   ev = p_pair x (1 - pair cost)  -  (1 - p_pair) x A
    #:
    #: A = `adverse_selection_per_share` (default 0.06), charged on the
    #: branch where the leg stays alone. The pair term is unchanged from
    #: what this class always asserted; with A = 0 the two are identical.
    #: Each fact is therefore pinned twice: the pair term alone (A = 0),
    #: and the figure the gate actually returns (default A), which is
    #: NEGATIVE at these rates, so the gate refuses.
    NO_CHARGE = dict(adverse_selection_per_share=D("0"))
    A = C.Params().adverse_selection_per_share

    def test_the_gate_prices_the_real_pair(self):
        r = decide(straddle_completion_pair_cost=D("0"), **self.MEASURED,
                   **self.NO_CHARGE)
        assert legs(r), r["evaluation"]["entry"]
        assert rec(r)["pair_quote_cost"] == "0.99"

    def test_with_the_charge_the_same_quote_is_refused(self):
        r = decide(straddle_completion_pair_cost=D("0"), **self.MEASURED)
        assert legs(r) == []
        assert r["why"] == "ENTRY_EV_EV_NOT_POSITIVE"
        assert rec(r)["pair_quote_cost"] == "0.99"

    def test_the_pair_term_is_p_pair_natural_times_the_spread(self):
        #: a matched pair costing 1 - spread pays 1.00, so the edge is the
        #: spread, weighted by how often the pair actually completes.
        r = decide(straddle_completion_pair_cost=D("0"), **self.MEASURED,
                   **self.NO_CHARGE)
        for g in r["evaluation"]["entry_ev"]["rungs"]:
            assert D(g["ev_per_share"]) == D("0.070") * D("0.01"), g

    def test_ev_is_the_pair_term_less_the_lone_leg_charge(self):
        assert self.A == D("0.06")
        r = decide(straddle_completion_pair_cost=D("0"), **self.MEASURED)
        want = D("0.070") * D("0.01") - (1 - D("0.070")) * self.A
        assert want == D("-0.0551")
        for g in r["evaluation"]["entry_ev"]["rungs"]:
            assert D(g["ev_per_share"]) == want, g
            assert g["admitted"] is False

    def test_a_wider_spread_earns_proportionally_more(self):
        wide = {"bid": "0.28", "ask": "0.31", "age_s": 0.1}
        r = decide(book=wide, straddle_pair_ceiling=D("0.99"),
                   straddle_completion_pair_cost=D("0"), **self.MEASURED,
                   **self.NO_CHARGE)
        assert rec(r)["pair_quote_cost"] == "0.97"
        for g in r["evaluation"]["entry_ev"]["rungs"]:
            assert D(g["ev_per_share"]) == D("0.070") * D("0.03"), g

    def test_a_wider_spread_does_not_outweigh_the_charge(self):
        wide = {"bid": "0.28", "ask": "0.31", "age_s": 0.1}
        r = decide(book=wide, straddle_pair_ceiling=D("0.99"),
                   straddle_completion_pair_cost=D("0"), **self.MEASURED)
        want = D("0.070") * D("0.03") - (1 - D("0.070")) * self.A
        for g in r["evaluation"]["entry_ev"]["rungs"]:
            assert D(g["ev_per_share"]) == want == D("-0.0537"), g
        assert legs(r) == []

    def test_both_legs_are_priced_symmetrically(self):
        #: a pair is one trade. If the two legs disagreed on what the
        #: pair costs, one of them is being valued against a leg that
        #: does not exist -- which is the bug this class records.
        r = decide(straddle_completion_pair_cost=D("0"), **self.MEASURED)
        evs = {g["ev_per_share"] for g in r["evaluation"]["entry_ev"]["rungs"]}
        assert len(evs) == 1, evs

    def test_the_lone_leg_contributes_exactly_zero(self):
        #: pw == px makes pw*(1-px) - (1-pw)*px vanish, so we neither
        #: credit ourselves the half-spread we buy at the bid nor charge
        #: a loss the market does not imply. The pair carries the
        #: decision.
        lone = dict(straddle_completion_pair_cost=D("0"),
                    straddle_p_pair_natural=D("0"),
                    straddle_p_complete=D("0"),
                    straddle_p_lone_win=D("0.0981"),
                    straddle_min_ev_per_share=D("0"))
        r = decide(**lone, **self.NO_CHARGE)
        for g in r["evaluation"]["entry_ev"]["rungs"]:
            assert D(g["ev_per_share"]) == D("0"), g
        assert legs(r) == [], "zero EV must not be admitted"

    def test_a_leg_certain_to_stay_alone_is_worth_minus_the_charge(self):
        r = decide(straddle_completion_pair_cost=D("0"),
                   straddle_p_pair_natural=D("0"),
                   straddle_p_complete=D("0"),
                   straddle_p_lone_win=D("0.0981"),
                   straddle_min_ev_per_share=D("0"))
        for g in r["evaluation"]["entry_ev"]["rungs"]:
            assert D(g["ev_per_share"]) == -self.A, g
        assert legs(r) == []

    def test_an_unset_pair_rate_still_refuses(self):
        #: the remaining unmeasured input is still mandatory.
        r = decide(straddle_completion_pair_cost=D("0"),
                   straddle_p_pair_natural=None,
                   straddle_p_complete=D("0"),
                   straddle_p_lone_win=D("0.0981"))
        assert legs(r) == []
        assert "P_PAIR_NATURAL_NOT_SET" in str(r["why"])

    def test_the_fixed_008_rung_is_unaffected(self):
        #: fixed mode keeps the measured constant, because that is where
        #: it was measured.
        r = decide(straddle_book_relative=False, straddle_price=D("0.08"),
                   straddle_rungs=(D("0.08"),), clip_usd=D("0.40"),
                   **self.MEASURED)
        assert legs(r), r["evaluation"]["entry"]


# ================================================= the eight scenarios

class TestTheEightInventoryScenarios:
    """Each asserts the controller's ACTUAL action, price, size and
    rationale -- not that a code path exists."""

    def test_1_up_fill_with_no_down_inventory(self):
        #: 2 UP held at 0.30. DOWN is the deficient side: it must still
        #: be quoted, and the unmatched leg must be visible.
        #: COMPLETION OFF here on purpose. With it on, the completion is
        #: the action and the cheap DOWN rung is deliberately WITHDRAWN
        #: -- exactly one route to a pair, which is the 1925z fix. The
        #: two routes are tested separately because they are mutually
        #: exclusive by design, not because one is broken.
        r = decide(inventory=inv(up=("2", "0.60")),
                   straddle_completion_pair_cost=D("0"))
        assert r["exposure"]["unmatched_side"] == C.UP
        assert D(r["exposure"]["unmatched_qty"]) == D("2")
        assert legs(r, C.DOWN), "the light side must be quoted"

    def test_2_down_fill_with_no_up_inventory(self):
        r = decide(inventory=inv(dn=("2", "1.38")),
                   straddle_completion_pair_cost=D("0"))
        assert r["exposure"]["unmatched_side"] == C.DOWN
        assert legs(r, C.UP), "the light side must be quoted"

    def test_3_multiple_fills_on_one_side(self):
        #: repetition replaces the one-fill lockout, and the SIDE CAP --
        #: not a lockout -- is what finally stops it.
        a = decide(inventory=inv(up=("2", "0.60")),
                   straddle_completion_pair_cost=D("0"))
        assert legs(a, C.UP), "a filled side is re-quoted, not locked out"
        b = decide(inventory=inv(up=("13", "3.90")),
                   straddle_completion_pair_cost=D("0"))
        assert legs(b, C.UP) == []
        assert b["evaluation"]["entry"]["why"] in (
            "SIDE_CAP_REACHED", "UNMATCHED_CEILING")

    def test_4_partial_pair_completion(self):
        #: 3 UP against 1 DOWN: one pair is matched, two UP are not, and
        #: the deficit is what the completion is sized on.
        r = decide(inventory=inv(up=("3", "0.90"), dn=("1", "0.69")))
        assert D(r["exposure"]["matched_qty"]) == D("1")
        assert D(r["exposure"]["unmatched_qty"]) == D("2")
        assert r["exposure"]["unmatched_side"] == C.UP

    def test_5_market_moves_against_unmatched_inventory(self):
        #: UP bought at 0.30, now bid 0.10/ask 0.11. The worst-case
        #: settlement loss is the cost of the unmatched leg and must be
        #: reported, and repricing must shade the heavy side DOWN.
        moved = {"bid": "0.10", "ask": "0.11", "age_s": 0.1}
        r = decide(book=moved, inventory=inv(up=("2", "0.60")),
                   straddle_pair_ceiling=D("0.98"),
                   straddle_reprice_ticks_per_clip=1)
        assert D(r["exposure"]["worst_case_settlement_loss_usd"]) == D("0.60")
        ups = legs(r, C.UP)
        if ups:
            assert ups[0]["price"] < D("0.10"), "heavy side must bid lower"

    def test_6_both_sides_filling_repeatedly(self):
        #: balanced accumulation: neither side is the deficient one, the
        #: pair is matched, and both sides stay quotable.
        r = decide(inventory=inv(up=("2", "0.60"), dn=("2", "1.38")),
                   straddle_completion_pair_cost=D("0"))
        assert D(r["exposure"]["matched_qty"]) == D("2")
        assert D(r["exposure"]["unmatched_qty"]) == D("0")
        assert legs(r, C.UP) and legs(r, C.DOWN)

    def test_7_a_pair_that_became_uneconomic_is_not_completed(self):
        #: UP accumulated at an average of 0.92 against a 0.90 pair
        #: target: there is NO opposite price that completes it inside
        #: the target, so the completion must refuse rather than buy a
        #: losing leg to look balanced.
        r = decide(inventory=inv(up=("2", "1.84")))
        assert comp(r) is None
        assert r["evaluation"]["completion"]["why"] in (
            "PRICE_OUT_OF_RANGE", "PAIR_ABOVE_TARGET")

    def test_7b_a_pair_that_is_still_economic_is_completed_patiently(self):
        #: UP at 0.30 average leaves 0.60 for DOWN inside the 0.90
        #: target. DOWN currently OFFERS 0.69, so the completion rests at
        #: 0.60 and waits -- it does not chase the offer, and it does not
        #: cross.
        r = decide(inventory=inv(up=("2", "0.60")))
        o = comp(r)
        assert o is not None, r["evaluation"]["completion"]
        assert o["side"] == C.DOWN
        assert o["price"] == D("0.60"), "rests at the budget, does not chase"
        assert o["post_only"] is True, "taker stays disabled"
        assert D(r["evaluation"]["completion"]["pair_cost"]) <= D("0.90")

    def test_8_a_stale_or_adversely_selected_resting_order(self):
        #: a rung resting at a price the book has left is not re-quoted
        #: at that price; the live book decides where the quote belongs.
        stale = [resting_order(C.UP, "0.30", "2", "old")]
        moved = {"bid": "0.20", "ask": "0.21", "age_s": 0.1}
        r = decide(book=moved, resting=stale,
                   straddle_pair_ceiling=D("0.99"))
        for o in legs(r, C.UP):
            assert o["price"] == D("0.20"), "quote follows the book"

    def test_8b_a_stale_book_quotes_nothing_at_all(self):
        r = decide(book={"bid": "0.30", "ask": "0.31", "age_s": 99.0})
        assert r["orders"] == [] or all(o.get("op") == "CANCEL"
                                        for o in r["orders"])
        assert r["why"] == "BOOK_STALE"


# ================================================== nothing regressed

class TestFixedModeIsUntouched:

    def test_fixed_mode_still_bounds_each_leg_to_half(self):
        #: the symmetric rule is correct for a symmetric pair and stays.
        r = decide(straddle_book_relative=False, straddle_price=D("0.60"))
        assert r["why"] == "PARAMS_UNREADABLE"

    def test_fixed_mode_prices_from_the_constant_not_the_book(self):
        r = decide(straddle_book_relative=False, straddle_price=D("0.08"),
                   straddle_rungs=(D("0.08"),))
        for o in legs(r):
            assert o["price"] == D("0.08")

    def test_book_relative_is_off_by_default(self):
        assert C.Params().straddle_book_relative is False
        assert C.Params().straddle_pair_ceiling == D("0")
        assert C.Params().straddle_fill_model_measured is False
        assert C.Params().straddle_completion_reserve is False


# ================================ through the real executor, with fills

class TestThroughTheRealExecutorPath:
    """Controller -> adapter -> scripted venue -> ledger -> inventory ->
    next decision. Not the pure function: the path that actually trades.

    The env readers are exercised here too, because a param with no
    reader sits at its dataclass default forever -- the defect that kept
    the entry-EV rates unset and made the whole mode silent.
    """

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
        monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
        #: 0.40, not the 2.00 this fixture was written with: the executor has
        #: since refused any clip above its mode ceiling
        #: (CLIP_USD_ABOVE_MODE_CEILING, 0.45), so at 2.00 both legs were
        #: refused before reaching the venue.
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.40")
        monkeypatch.setenv(X.STRADDLE_BOOK_RELATIVE_ENV, "YES")
        monkeypatch.setenv(X.STRADDLE_PAIR_CEILING_ENV, "0.99")
        monkeypatch.setenv(X.STRADDLE_JOIN_TICKS_ENV, "0")
        monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
        monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "4.00")
        monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "3.00")
        monkeypatch.setenv(X.STRADDLE_FILL_MODEL_ENV, X.FILL_MODEL_TOKEN)
        #: synthetic, mechanism-only -- see the note in `params()`
        monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.95")
        monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.0001")
        monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.50")
        #: attested as measured conditional on a fill (required since
        #: eed10118c); without it every decision held and nothing reached
        #: the venue, so these four tests exercised no order path at all
        monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)

    def test_the_token_is_required_not_merely_YES(self, rig, armed):
        from src import l5_btc5m_execution as X
        import os as _os
        _os.environ[X.STRADDLE_FILL_MODEL_ENV] = "YES"
        rep = rig.step(bid="0.30", ask="0.31")
        assert rep["placed"] == []
        _os.environ[X.STRADDLE_FILL_MODEL_ENV] = X.FILL_MODEL_TOKEN
        assert rig.tick(2).step(bid="0.30", ask="0.31")["placed"]

    def test_both_legs_reach_the_venue_at_book_relative_prices(
            self, rig, armed):
        rep = rig.step(bid="0.30", ask="0.31")
        sent = {s: (D(p), D(q)) for s, p, q in rig.sent()}
        assert C.UP in sent and C.DOWN in sent, rep
        assert sent[C.UP][0] == D("0.30")
        assert sent[C.DOWN][0] == D("0.69")
        #: equal sizes, so a filled pair matches exactly 1:1
        assert sent[C.UP][1] == sent[C.DOWN][1]

    def test_a_fill_changes_inventory_and_the_next_decision_sees_it(
            self, rig, armed):
        rig.step(bid="0.30", ask="0.31")
        up = [o for o, i in rig.venue.by_oid.items() if i["side"] == C.UP][0]
        rig.venue.fill(up)
        rig.tick(2)
        rep = rig.step(bid="0.30", ask="0.31")
        #: VENUE-CONFIRMED before anything acts on it
        assert any(D(str(f["qty"])) > 0 for f in rep["fills"]), rep
        invt = rep["inventory"]
        assert D(str(invt[C.UP]["qty"])) > 0
        assert rep["exposure"]["btc5m"]["unmatched_side"] == C.UP

    def test_a_partial_fill_is_a_normal_state(self, rig, armed):
        rig.step(bid="0.30", ask="0.31")
        up = [o for o, i in rig.venue.by_oid.items() if i["side"] == C.UP][0]
        want = D(str(rig.venue.by_oid[up]["qty"]))
        rig.venue.fill(up, want / 2)
        rig.tick(2)
        rep = rig.step(bid="0.30", ask="0.31")
        held = D(str(rep["inventory"][C.UP]["qty"]))
        assert 0 < held < want, rep
        assert rep["halted_reason"] is None

    def test_the_quote_follows_the_book_between_steps(self, rig, armed):
        rig.step(bid="0.30", ask="0.31")
        n = len(rig.venue.sent)
        rig.tick(2)
        rig.step(bid="0.20", ask="0.21")
        fresh = [s for s in rig.venue.sent[n:]]
        if fresh:
            ups = [D(str(s["price"])) for s in fresh if s["side"] == C.UP]
            assert all(p == D("0.20") for p in ups), fresh

    def test_nothing_crosses_the_spread(self, rig, armed):
        rig.step(bid="0.30", ask="0.31")
        for s in rig.venue.sent:
            assert s.get("post_only") is True, s


# ============ the discovery permission overrides NOTHING but itself

class TestDiscoveryPermissionCannotOverrideProtections:
    """`straddle_fill_model_measured` says one thing only: that the rates
    being fed to the EV gate are believed to describe these prices. It is
    not a master switch, and these pin that it cannot reach any other
    protection. Every case below has the permission GRANTED and is still
    refused."""

    def test_it_does_not_override_the_entry_ev_gate(self):
        r = decide(straddle_min_ev_per_share=D("5.00"))
        assert legs(r) == [] and "ENTRY_EV" in str(r["why"])

    def test_it_does_not_override_the_pair_ceiling(self):
        r = decide(straddle_pair_ceiling=D("0.50"))
        assert legs(r) == [] and rec(r)["why"] == "PAIR_ABOVE_CEILING"

    def test_it_does_not_override_the_exposure_cap(self):
        r = decide(max_exposure_usd=D("0.05"))
        assert legs(r) == []

    def test_it_does_not_override_the_per_side_cap(self):
        r = decide(inventory=inv(up=("13", "3.90")),
                   straddle_completion_pair_cost=D("0"))
        assert legs(r, C.UP) == []

    def test_it_does_not_override_the_unmatched_ceiling(self):
        r = decide(inventory=inv(up=("9", "2.70")),
                   straddle_max_side_usd=D("40.00"),
                   straddle_max_unmatched_usd=D("0.50"),
                   straddle_completion_pair_cost=D("0"))
        assert legs(r, C.UP) == []
        assert r["evaluation"]["entry"]["why"] in (
            "UNMATCHED_CEILING", "SIDE_CAP_REACHED", "NO_EXPOSURE_ROOM")

    def test_it_does_not_override_stale_market_data(self):
        r = decide(book={"bid": "0.30", "ask": "0.31", "age_s": 99.0})
        assert r["why"] == "BOOK_STALE"
        assert legs(r) == []

    def test_it_does_not_override_an_unusable_book(self):
        for bad in ({"bid": "0", "ask": "0.31", "age_s": 0.1},
                    {"bid": "0.31", "ask": "0.30", "age_s": 0.1},
                    {"bid": "0.30", "ask": "1.00", "age_s": 0.1}):
            r = decide(book=bad)
            assert r["why"] == "BOOK_UNUSABLE", bad

    def test_it_does_not_override_the_entry_cutoff(self):
        r = decide(t=290.0)
        assert legs(r) == []
        assert r["evaluation"]["entry"]["why"] == "ENTRY_WINDOW_CLOSED"

    def test_it_does_not_enable_taker_anywhere(self):
        #: post_only on every order, at every setting, with the
        #: permission granted and taker_allowed left False.
        for kw in ({}, {"inventory": inv(up=("2", "0.60"))},
                   {"straddle_completion_reserve": True}):
            r = decide(**kw)
            for o in r["orders"]:
                if o.get("op") == "PLACE":
                    assert o["post_only"] is True, (kw, o)

    def test_it_does_not_grant_itself_without_the_token(self):
        from src import l5_btc5m_execution as X
        import os as _os
        prev = _os.environ.get(X.STRADDLE_FILL_MODEL_ENV)
        try:
            for v in ("YES", "1", "TRUE", "yes", "MEASURED", ""):
                _os.environ[X.STRADDLE_FILL_MODEL_ENV] = v
                assert X.straddle_fill_model_measured() is False, v
            _os.environ[X.STRADDLE_FILL_MODEL_ENV] = X.FILL_MODEL_TOKEN
            assert X.straddle_fill_model_measured() is True
        finally:
            if prev is None:
                _os.environ.pop(X.STRADDLE_FILL_MODEL_ENV, None)
            else:
                _os.environ[X.STRADDLE_FILL_MODEL_ENV] = prev

    def test_the_pair_ceiling_is_clamped_down_never_up(self):
        from src import l5_btc5m_execution as X
        import os as _os
        prev = _os.environ.get(X.STRADDLE_PAIR_CEILING_ENV)
        try:
            for asked in ("1.00", "1.50", "9.99"):
                _os.environ[X.STRADDLE_PAIR_CEILING_ENV] = asked
                assert X.straddle_pair_ceiling() == X.STRADDLE_PAIR_CEILING_MAX
            _os.environ[X.STRADDLE_PAIR_CEILING_ENV] = "0"
            assert X.straddle_pair_ceiling() == D("0"), "zero disables"
        finally:
            if prev is None:
                _os.environ.pop(X.STRADDLE_PAIR_CEILING_ENV, None)
            else:
                _os.environ[X.STRADDLE_PAIR_CEILING_ENV] = prev

    def test_join_ticks_are_clamped_so_the_quote_cannot_be_pushed_up(self):
        from src import l5_btc5m_execution as X
        import os as _os
        prev = _os.environ.get(X.STRADDLE_JOIN_TICKS_ENV)
        try:
            _os.environ[X.STRADDLE_JOIN_TICKS_ENV] = "99"
            assert X.straddle_join_ticks() == X.STRADDLE_JOIN_TICKS_CEILING
        finally:
            if prev is None:
                _os.environ.pop(X.STRADDLE_JOIN_TICKS_ENV, None)
            else:
                _os.environ[X.STRADDLE_JOIN_TICKS_ENV] = prev
