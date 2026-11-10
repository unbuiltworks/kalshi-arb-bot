"""The portfolio controller: accounting, valuation, quoting, and its seam.

OWNER DIRECTION, 2026-11-10: one continuous portfolio-level controller
combining pair accumulation, favored-side accumulation, inventory balancing
and adaptive maker repricing -- scored on total portfolio settlement, not
isolated pair profit -- with every existing limit preserved.

Three parts. (1) The pure decision module, exhaustively. (2) The runner
stage, through a real Runner and FaithfulExecutor with a scripted venue:
it records and can place or cancel nothing. (3) Isolation: there is no
switch that lets this module's output reach the venue.
"""
import ast
import inspect
import itertools
import json
import math
import os
import random
from decimal import Decimal as D

import pytest

import tests.test_l5_runner_pair_and_cancel_state as T
from src import l5_pmus_live_runner as R
from src import l5_portfolio_controller as PC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP, DOWN = PC.UP, PC.DOWN
LIM = PC.Limits(cash_usd=100.0, exposure_usd=100.0, max_orders=2)
ZERO = PC.Params(margin=0.0, adverse=0.0, risk=0.0)


def port(u=0, cu=0, d=0, cd=0, orders=()):
    return PC.portfolio(up_qty=u, up_cost=cu, dn_qty=d, dn_cost=cd,
                        orders=orders)


def quotes(dec):
    return {q["side"]: q for q in dec["quotes"]}


def why(dec, side):
    return [s["reason"] for s in dec["skipped"] if s["side"] == side]


# ======================================================================
# 1. THE PORTFOLIO: inventory, exact cost basis, pairs, exposure, orders
# ======================================================================
class TestThePortfolio:
    def test_a_flat_book_is_flat(self):
        p = port()
        assert p["matched_pairs"] == 0 and p["unmatched"] == 0
        assert p["worst_case"] == 0 and p["pair_cost"] is None
        assert p["unmatched_side"] is None

    def test_pairs_are_the_smaller_side_and_the_rest_is_unmatched(self):
        p = port(u=5, cu=2.25, d=3, cd=1.50)
        assert p["matched_pairs"] == 3
        assert p["unmatched"] == 2 and p["unmatched_side"] == UP
        assert p["avg_up"] == pytest.approx(0.45)
        assert p["avg_dn"] == pytest.approx(0.50)
        assert p["pair_cost"] == pytest.approx(0.95)
        assert p["locked_pair_pnl"] == pytest.approx(3 * 0.05)
        assert p["unmatched_cost"] == pytest.approx(2 * 0.45)

    def test_settlement_is_exact_on_both_outcomes(self):
        p = port(u=5, cu=2.25, d=3, cd=1.50)
        assert p["settles_if_up"] == pytest.approx(5 - 3.75)
        assert p["settles_if_down"] == pytest.approx(3 - 3.75)
        assert p["worst_case"] == pytest.approx(-0.75)

    def test_resting_orders_are_counted_per_side_and_in_the_worst_case(self):
        p = port(u=1, cu=0.40, orders=[
            {"id": "a", "side": "UP", "price": 0.38, "qty": 2},
            {"id": "b", "side": "DOWN", "price": 0.55, "qty": 1}])
        assert p["resting"][UP] == {"qty": 2, "notional": pytest.approx(0.76),
                                    "orders": 1}
        assert p["resting"][DOWN]["qty"] == 1
        # the UP orders fill and DOWN wins: lose the 0.40 held and the 0.76
        assert p["worst_case_with_resting"] == pytest.approx(-0.40 - 0.76)
        assert p["committed_usd"] == pytest.approx(0.40 + 0.76 + 0.55)

    def test_the_worst_case_with_orders_is_the_true_minimum(self):
        """Brute force over which orders fill and who wins."""
        rng = random.Random(5)
        for _ in range(200):
            u, d = rng.randint(0, 4), rng.randint(0, 4)
            cu, cd = u * rng.uniform(0.1, 0.9), d * rng.uniform(0.1, 0.9)
            orders = [{"id": i, "side": rng.choice((UP, DOWN)),
                       "price": round(rng.uniform(0.05, 0.95), 2),
                       "qty": rng.randint(1, 3)} for i in range(3)]
            p = port(u, cu, d, cd, orders)
            worst = math.inf
            for mask in itertools.product((0, 1), repeat=3):
                uu, dd, cc = u, d, cu + cd
                for on, o in zip(mask, orders):
                    if on:
                        cc += o["price"] * o["qty"]
                        if o["side"] == UP:
                            uu += o["qty"]
                        else:
                            dd += o["qty"]
                worst = min(worst, uu - cc, dd - cc)
            assert p["worst_case_with_resting"] == pytest.approx(worst)

    @pytest.mark.parametrize("bad", [
        dict(u="x"), dict(u=-1), dict(cu=float("nan")), dict(u=0, cu=0.5),
        dict(orders=[{"id": 1, "side": "SIDEWAYS", "price": 0.5, "qty": 1}]),
        dict(orders=[{"id": 1, "side": "UP", "price": 1.2, "qty": 1}]),
        dict(orders=[{"id": 1, "side": "UP", "price": None, "qty": 1}]),
        dict(orders=[{"id": 1, "side": "UP", "price": 0.5, "qty": "?"}]),
    ])
    def test_an_unreadable_portfolio_is_an_error_not_a_flat_one(self, bad):
        with pytest.raises(ValueError):
            port(**bad)

    def test_a_fully_filled_order_row_is_not_resting(self):
        p = port(orders=[{"id": 1, "side": "UP", "price": 0.5, "qty": 0}])
        assert p["orders"] == [] and p["resting"][UP]["orders"] == 0


# ======================================================================
# 2. VALUATION: total portfolio settlement, not pair profit
# ======================================================================
class TestValuation:
    def test_expected_settlement_is_the_sum_over_contracts(self):
        """The identity the design rests on: pairing does not appear."""
        rng = random.Random(9)
        for _ in range(100):
            p_up = rng.uniform(0.05, 0.95)
            buys = [(rng.choice((UP, DOWN)), rng.uniform(0.05, 0.95))
                    for _i in range(rng.randint(1, 12))]
            u = sum(1 for s, _q in buys if s == UP)
            d = len(buys) - u
            cu = sum(q for s, q in buys if s == UP)
            cd = sum(q for s, q in buys if s == DOWN)
            per_contract = sum((p_up if s == UP else 1 - p_up) - q
                               for s, q in buys)
            assert PC.expected_settlement(port(u, cu, d, cd), p_up) == \
                pytest.approx(per_contract)

    def test_a_pair_has_the_same_expectation_whatever_we_believe(self):
        p = port(u=1, cu=0.45, d=1, cd=0.50)
        assert PC.expected_settlement(p, 0.1) == pytest.approx(0.05)
        assert PC.expected_settlement(p, 0.9) == pytest.approx(0.05)

    def test_the_risk_charge_is_on_unmatched_contracts_only(self):
        pr = PC.Params(risk=0.2)
        paired = port(u=2, cu=0.9, d=2, cd=0.9)
        assert PC.value(paired, 0.5, pr) == pytest.approx(
            PC.expected_settlement(paired, 0.5))
        heavy = port(u=3, cu=1.35, d=1, cd=0.45)
        assert PC.value(heavy, 0.5, pr) == pytest.approx(
            PC.expected_settlement(heavy, 0.5) - 0.2 * 0.5 * 2)

    def test_an_actions_effect_is_the_change_in_the_whole_portfolio(self):
        pr = PC.Params(margin=0.02, adverse=0.03, risk=0.1)
        before = port(u=2, cu=0.90)
        eff = PC.action_effect(before, side=DOWN, price=0.50, qty=1,
                               p_up=0.55, params=pr)
        after = port(u=2, cu=0.90, d=1, cd=0.50)
        assert eff["role"] == "HEDGE" and eff["pairs_added"] == 1
        assert eff["expected_settlement_change"] == pytest.approx(
            PC.expected_settlement(after, 0.55)
            - PC.expected_settlement(before, 0.55))
        assert eff["settles_if_up_after"] == pytest.approx(
            after["settles_if_up"])
        assert eff["settles_if_down_after"] == pytest.approx(
            after["settles_if_down"])
        assert eff["worst_case_after"] == pytest.approx(after["worst_case"])
        # the conservative line takes margin and adverse selection off
        assert eff["conservative_change"] == pytest.approx(
            0.45 - 0.02 - 0.03 - 0.50)
        # and hedging earns the risk charge back
        assert eff["risk_charge_change"] == pytest.approx(
            -0.1 * math.sqrt(0.55 * 0.45))
        assert eff["value_change"] == pytest.approx(
            eff["conservative_change"] - eff["risk_charge_change"])

    def test_adding_to_the_heavy_side_pays_the_risk_charge(self):
        pr = PC.Params(risk=0.1)
        eff = PC.action_effect(port(u=2, cu=0.9), side=UP, price=0.40, qty=1,
                               p_up=0.5, params=pr)
        assert eff["role"] == "ADD"
        assert eff["risk_charge_change"] == pytest.approx(0.1 * 0.5)

    def test_the_win_probability_follows_strike_distance_and_time(self):
        kw = dict(sigma_per_sqrt_second=0.0001)
        at = PC.win_probability(spot=100, strike=100, seconds_left=600, **kw)
        above = PC.win_probability(spot=100.2, strike=100, seconds_left=600,
                                   **kw)
        later = PC.win_probability(spot=100.2, strike=100, seconds_left=60,
                                   **kw)
        assert at == pytest.approx(0.5, abs=0.01)
        assert 0.5 < above < later < 1.0
        assert PC.win_probability(spot=99.8, strike=100, seconds_left=600,
                                  **kw) == pytest.approx(1 - above, abs=0.01)

    @pytest.mark.parametrize("bad", [
        dict(spot=0), dict(strike=-1), dict(seconds_left=0),
        dict(sigma_per_sqrt_second=0), dict(spot="x")])
    def test_an_unusable_input_is_an_error_not_a_probability(self, bad):
        kw = dict(spot=100, strike=100, seconds_left=600,
                  sigma_per_sqrt_second=0.0001)
        kw.update(bad)
        with pytest.raises(ValueError):
            PC.win_probability(**kw)

    def test_no_probability_is_invented(self):
        assert PC.belief(p_up=0.7, bid=0.4, ask=0.42) == {
            "p_up": 0.7, "source": "MODEL"}
        assert PC.belief(bid=0.40, ask=0.42) == {
            "p_up": pytest.approx(0.41), "source": "BOOK"}
        for junk in (None, 0, 1, 1.5, "x", float("nan")):
            assert PC.belief(p_up=junk, bid=0.40, ask=0.42)["source"] == \
                "BOOK"
        assert PC.belief(bid=0.5, ask=0.5)["source"] == "NONE"
        assert PC.belief()["p_up"] is None


# ======================================================================
# 3. THE DECISION: both sides, favored side, hedge, skew, reprice
# ======================================================================
class TestBothSidesQuoteConcurrently:
    def test_a_flat_book_is_quoted_on_both_sides(self):
        dec = PC.decide(port(), bid=0.49, ask=0.51, t_off=30, params=ZERO,
                        limits=LIM)
        q = quotes(dec)
        assert set(q) == {UP, DOWN}
        assert q[UP]["price"] == pytest.approx(0.49)
        assert q[DOWN]["price"] == pytest.approx(0.49)

    def test_unmatched_inventory_does_not_block_new_orders(self):
        """An UP-heavy book still quotes BOTH sides while under its cap."""
        dec = PC.decide(port(u=1, cu=0.45), bid=0.49, ask=0.51, t_off=60,
                        params=ZERO, limits=LIM)
        assert set(quotes(dec)) == {UP, DOWN}

    def test_the_haircut_moves_both_quotes_back_from_the_touch(self):
        pr = PC.Params(margin=0.02, adverse=0.02, risk=0.0)
        q = quotes(PC.decide(port(), bid=0.49, ask=0.51, t_off=30, params=pr,
                             limits=LIM))
        # mid 0.50 less four cents
        assert q[UP]["price"] == pytest.approx(0.46)
        assert q[UP]["ticks_behind_touch"] == 3
        assert q[UP]["price"] + q[DOWN]["price"] == pytest.approx(0.92)

    def test_a_quote_never_crosses_and_never_improves_the_best_bid(self):
        rng = random.Random(2)
        for _ in range(500):
            bid = round(rng.uniform(0.02, 0.96), 2)
            ask = round(bid + rng.choice((0.01, 0.02, 0.03)), 2)
            dec = PC.decide(port(), bid=bid, ask=ask,
                            p_up=rng.uniform(0.01, 0.99), t_off=30,
                            params=ZERO, limits=LIM)
            for q in dec["quotes"]:
                touch = bid if q["side"] == UP else round(1 - ask, 10)
                cross = ask if q["side"] == UP else round(1 - bid, 10)
                assert q["price"] <= touch + 1e-9
                assert q["price"] < cross - 1e-9
                assert q["price"] >= 0.01 - 1e-9


class TestFavoredSideAccumulation:
    PR = PC.Params(margin=0.03, adverse=0.02, risk=0.0, size_per_cent=0.25,
                   max_qty=3, max_unmatched=6)

    def test_the_favored_side_is_quoted_at_the_touch_and_sized_up(self):
        dec = PC.decide(port(), bid=0.59, ask=0.61, p_up=0.75, t_off=120,
                        params=self.PR, limits=LIM)
        q = quotes(dec)
        assert q[UP]["price"] == pytest.approx(0.59)
        assert q[UP]["ticks_behind_touch"] == 0
        # 0.75 - 0.05 - 0.59 = 11 cents of conservative value
        assert q[UP]["qty"] == pytest.approx(3.0)
        assert q[UP]["conservative_change"] > 0

    def test_the_unfavored_side_drops_back_or_out(self):
        dec = PC.decide(port(), bid=0.59, ask=0.61, p_up=0.75, t_off=120,
                        params=self.PR, limits=LIM)
        # DOWN is worth 0.25 - 0.05 = 0.20 against a 0.39 touch: 19 ticks
        assert DOWN not in quotes(dec)
        assert why(dec, DOWN) == ["NOT_COMPETITIVE"]

    def test_the_margin_is_what_stands_between_a_belief_and_a_bet(self):
        """Two cents of model edge is inside a five-cent haircut: the quote
        sits behind the touch instead of joining it."""
        dec = PC.decide(port(), bid=0.59, ask=0.61, p_up=0.62, t_off=120,
                        params=self.PR, limits=LIM)
        q = quotes(dec)[UP]
        assert q["price"] == pytest.approx(0.57)
        assert q["ticks_behind_touch"] == 2 and q["qty"] == 1.0

    def test_without_a_model_no_side_can_be_favored(self):
        dec = PC.decide(port(), bid=0.59, ask=0.61, t_off=120,
                        params=self.PR, limits=LIM)
        assert dec["belief"]["source"] == "BOOK"
        q = quotes(dec)
        assert all(x["ticks_behind_touch"] >= 4 and x["qty"] == 1.0
                   for x in q.values())

    def test_time_to_expiry_reaches_the_quote_through_the_probability(self):
        kw = dict(spot=100.1, strike=100, sigma_per_sqrt_second=0.0001)
        early = PC.win_probability(seconds_left=840, **kw)
        late = PC.win_probability(seconds_left=120, **kw)
        qe = quotes(PC.decide(port(), bid=0.59, ask=0.61, p_up=early,
                              t_off=60, params=self.PR, limits=LIM))
        ql = quotes(PC.decide(port(), bid=0.59, ask=0.61, p_up=late,
                              t_off=60, params=self.PR, limits=LIM))
        assert late > early
        assert ql[UP]["value_change"] > qe[UP]["value_change"]


class TestInventoryBalancing:
    PR = PC.Params(margin=0.01, adverse=0.01, risk=0.10)

    def test_the_hedge_is_bid_up_and_the_heavy_side_bid_down(self):
        flat = quotes(PC.decide(port(), bid=0.49, ask=0.51, t_off=60,
                                params=self.PR, limits=LIM))
        heavy = quotes(PC.decide(port(u=2, cu=0.90), bid=0.49, ask=0.51,
                                 t_off=60, params=self.PR, limits=LIM))
        assert heavy[DOWN]["role"] == "HEDGE"
        assert heavy[DOWN]["price"] > flat[DOWN]["price"]
        assert heavy[UP]["price"] <= flat[UP]["price"]
        # the skew between the two sides is twice the risk charge
        r_up = PC.reservation_price(port(u=2, cu=0.9), side=UP, p_up=0.5,
                                    params=self.PR)
        r_dn = PC.reservation_price(port(u=2, cu=0.9), side=DOWN, p_up=0.5,
                                    params=self.PR)
        assert r_dn - r_up == pytest.approx(2 * 0.10 * 0.5)

    def test_the_hedge_is_sized_to_the_imbalance(self):
        q = quotes(PC.decide(port(u=3, cu=1.35), bid=0.49, ask=0.51,
                             t_off=60, params=self.PR, limits=LIM))
        assert q[DOWN]["qty"] == pytest.approx(3.0)
        assert q[DOWN]["unmatched_after"] == pytest.approx(0.0)
        assert q[DOWN]["pairs_added"] == pytest.approx(3.0)

    def test_the_heavy_side_stops_at_the_unmatched_cap(self):
        pr = PC.with_overrides(self.PR, max_unmatched=2)
        dec = PC.decide(port(u=2, cu=0.90), bid=0.49, ask=0.51, t_off=60,
                        params=pr, limits=LIM)
        assert UP not in quotes(dec) and why(dec, UP) == ["UNMATCHED_CAP"]
        assert DOWN in quotes(dec)

    def test_an_add_is_sized_to_the_room_under_the_cap(self):
        pr = PC.Params(margin=0, adverse=0, risk=0, size_per_cent=1.0,
                       max_qty=5, max_unmatched=3)
        q = quotes(PC.decide(port(u=2, cu=0.8), bid=0.49, ask=0.51, p_up=0.8,
                             t_off=60, params=pr, limits=LIM))
        assert q[UP]["qty"] == pytest.approx(1.0)

    def test_a_hedge_is_still_quoted_after_the_entry_cutoff(self):
        dec = PC.decide(port(u=1, cu=0.45), bid=0.49, ask=0.51, t_off=500,
                        params=self.PR, limits=LIM)
        assert set(quotes(dec)) == {DOWN}
        assert why(dec, UP) == ["AFTER_ENTRY_CUTOFF"]

    def test_nothing_is_quoted_after_the_completion_cutoff_or_before_open(
            self):
        for t in (700, 2):
            dec = PC.decide(port(u=1, cu=0.45), bid=0.49, ask=0.51, t_off=t,
                            params=self.PR, limits=LIM)
            assert dec["quotes"] == []

    def test_a_pair_ceiling_bounds_what_a_hedge_may_pay(self):
        pr = PC.with_overrides(ZERO, pair_ceiling=0.99)
        q = quotes(PC.decide(port(u=1, cu=0.60), bid=0.39, ask=0.41,
                             t_off=60, params=pr, limits=LIM))
        # DOWN touch is 0.59; the ceiling allows 0.99 - 0.60
        assert q[DOWN]["price"] == pytest.approx(0.39)

    def test_a_quote_that_lowers_portfolio_value_is_not_made(self):
        """Every quote the controller makes has a non-negative effect on
        conservative portfolio value, for any inventory and belief."""
        rng = random.Random(4)
        for _ in range(800):
            u, d = rng.randint(0, 5), rng.randint(0, 5)
            pr = PC.Params(margin=rng.choice((0, .02, .05)),
                           adverse=rng.choice((0, .03)),
                           risk=rng.choice((0, .1, .3)),
                           size_per_cent=rng.choice((0, .25)),
                           max_unmatched=rng.choice((1, 3, 6)))
            bid = round(rng.uniform(0.03, 0.95), 2)
            dec = PC.decide(port(u, u * 0.5, d, d * 0.5), bid=bid,
                            ask=round(bid + 0.02, 2),
                            p_up=rng.choice((None, rng.uniform(0.02, 0.98))),
                            t_off=rng.uniform(0, 700), params=pr, limits=LIM)
            for q in dec["quotes"]:
                assert q["value_change"] >= -1e-9
                assert q["qty"] > 0


class TestTheLimitsStillBind:
    def test_a_cap_that_was_not_supplied_refuses_everything(self):
        for lim in (PC.Limits(), PC.Limits(cash_usd=10),
                    PC.Limits(exposure_usd=10), None):
            dec = PC.decide(port(), bid=0.49, ask=0.51, t_off=30,
                            params=ZERO, limits=lim)
            assert dec["quotes"] == []
            assert set(why(dec, UP) + why(dec, DOWN)) == {
                "LIMITS_NOT_SUPPLIED"}

    def test_the_cash_cap_counts_what_is_held_and_what_is_quoted(self):
        lim = PC.Limits(cash_usd=0.90, exposure_usd=100, max_orders=2)
        dec = PC.decide(port(), bid=0.49, ask=0.51, t_off=30, params=ZERO,
                        limits=lim)
        assert len(dec["quotes"]) == 1          # 0.49 + 0.49 > 0.90
        assert "CASH_LIMIT" in why(dec, UP) + why(dec, DOWN)
        held = PC.decide(port(u=1, cu=0.45), bid=0.49, ask=0.51, t_off=30,
                         params=ZERO, limits=lim)
        assert len(held["quotes"]) == 0         # 0.45 + 0.49 > 0.90

    def test_the_exposure_cap_is_the_worst_case_settlement_loss(self):
        lim = PC.Limits(cash_usd=100, exposure_usd=1.00, max_orders=2)
        dec = PC.decide(port(u=2, cu=0.90), bid=0.49, ask=0.51, t_off=30,
                        params=PC.with_overrides(ZERO, max_unmatched=6),
                        limits=lim)
        # another UP at 0.49 would make the DOWN-wins loss 1.39
        assert "EXPOSURE_LIMIT" in why(dec, UP)
        assert DOWN in quotes(dec)

    def test_the_order_limit_keeps_the_more_valuable_quote(self):
        lim = PC.Limits(cash_usd=100, exposure_usd=100, max_orders=1)
        dec = PC.decide(port(u=2, cu=0.90), bid=0.49, ask=0.51, t_off=30,
                        params=PC.Params(margin=0, adverse=0, risk=0.1),
                        limits=lim)
        assert [q["side"] for q in dec["quotes"]] == [DOWN]
        assert why(dec, UP) == ["ORDER_LIMIT"]

    def test_no_proposal_ever_exceeds_the_caps(self):
        rng = random.Random(8)
        for _ in range(600):
            lim = PC.Limits(cash_usd=rng.uniform(0.2, 4),
                            exposure_usd=rng.uniform(0.2, 4),
                            max_orders=rng.randint(0, 2))
            u, d = rng.randint(0, 3), rng.randint(0, 3)
            p = port(u, u * 0.45, d, d * 0.45)
            bid = round(rng.uniform(0.05, 0.93), 2)
            dec = PC.decide(p, bid=bid, ask=round(bid + 0.02, 2),
                            p_up=rng.uniform(0.05, 0.95), t_off=60,
                            params=PC.Params(
                                margin=0, adverse=0, risk=0, max_qty=3,
                                size_per_cent=0.5, max_unmatched=6),
                            limits=lim)
            assert len(dec["quotes"]) <= lim.max_orders
            spend = sum(q["qty"] * q["price"] for q in dec["quotes"])
            if dec["quotes"]:
                assert p["cost"] + spend <= lim.cash_usd + 1e-9
            for side, lose_if in ((UP, "settles_if_down"),
                                  (DOWN, "settles_if_up")):
                s = sum(q["qty"] * q["price"] for q in dec["quotes"]
                        if q["side"] == side)
                if s:
                    assert -(p[lose_if] - s) <= lim.exposure_usd + 1e-9

    @pytest.mark.parametrize("bid,ask", [(None, 0.5), (0.5, 0.5), (0.6, 0.5),
                                         (0, 0.5), (0.5, 1), ("x", "y")])
    def test_an_unusable_book_quotes_nothing(self, bid, ask):
        dec = PC.decide(port(), bid=bid, ask=ask, p_up=0.5, t_off=30,
                        params=ZERO, limits=LIM)
        assert dec["quotes"] == []
        assert set(why(dec, UP)) == {"BOOK_UNUSABLE"}

    def test_bad_parameters_are_refused_not_defaulted(self):
        for kw in (dict(margin=-0.1), dict(tick=0), dict(base_qty=0),
                   dict(risk=float("nan")), dict(min_value=float("inf"))):
            with pytest.raises(ValueError):
                PC.decide(port(), bid=0.49, ask=0.51, t_off=30,
                          params=PC.Params(**kw), limits=LIM)


class TestRepricingAndReplenishment:
    def q(self, side, price, qty=1.0):
        return {"side": side, "price": price, "qty": qty, "role": "ADD",
                "value_change": 0.01}

    def test_a_resting_order_at_the_target_is_kept(self):
        acts = PC.order_actions(
            [self.q(UP, 0.47)],
            [{"id": "a", "side": "UP", "price": 0.47, "qty": 1}],
            params=PC.Params())
        assert [(a["op"], a["id"]) for a in acts] == [("KEEP", "a")]

    def test_a_moved_target_cancels_by_exact_id_then_places(self):
        acts = PC.order_actions(
            [self.q(UP, 0.45)],
            [{"id": "a", "side": "UP", "price": 0.47, "qty": 1}],
            params=PC.Params())
        assert acts[0] == {"op": "CANCEL", "id": "a", "side": UP,
                           "price": 0.47, "reason": "REPRICE"}
        assert acts[1]["op"] == "PLACE" and acts[1]["price"] == 0.45
        assert acts[1]["after_cancel_confirmed"] is True

    def test_a_side_no_longer_wanted_is_cancelled_and_not_replaced(self):
        acts = PC.order_actions(
            [], [{"id": "a", "side": "DOWN", "price": 0.47, "qty": 1}],
            params=PC.Params())
        assert acts == [{"op": "CANCEL", "id": "a", "side": DOWN,
                         "price": 0.47, "reason": "SIDE_NOT_WANTED"}]

    def test_a_filled_side_is_replenished(self):
        acts = PC.order_actions([self.q(UP, 0.47), self.q(DOWN, 0.50)],
                                [{"id": "b", "side": "DOWN", "price": 0.50,
                                  "qty": 1}], params=PC.Params())
        ops = sorted((a["op"], a["side"]) for a in acts)
        assert ops == [("KEEP", DOWN), ("PLACE", UP)]
        place = [a for a in acts if a["op"] == "PLACE"][0]
        assert place["after_cancel_confirmed"] is False

    def test_never_two_orders_kept_on_one_side(self):
        acts = PC.order_actions(
            [self.q(UP, 0.47)],
            [{"id": "a", "side": "UP", "price": 0.47, "qty": 1},
             {"id": "b", "side": "UP", "price": 0.47, "qty": 1}],
            params=PC.Params())
        assert [a["op"] for a in acts] == ["KEEP", "CANCEL"]

    def test_the_reprice_band_avoids_churn(self):
        pr = PC.Params(reprice_ticks=2)
        acts = PC.order_actions(
            [self.q(UP, 0.46)],
            [{"id": "a", "side": "UP", "price": 0.47, "qty": 1}], params=pr)
        assert [a["op"] for a in acts] == ["KEEP"]

    def test_the_quote_follows_the_book_and_the_inventory(self):
        """A fill and a book move both change the next decision."""
        pr = PC.Params(margin=0.01, adverse=0.01, risk=0.10)
        first = quotes(PC.decide(port(), bid=0.49, ask=0.51, t_off=30,
                                 params=pr, limits=LIM))
        after_fill = quotes(PC.decide(port(u=1, cu=first[UP]["price"]),
                                      bid=0.44, ask=0.46, t_off=40,
                                      params=pr, limits=LIM))
        assert after_fill[DOWN]["role"] == "HEDGE"
        assert after_fill[DOWN]["price"] > first[DOWN]["price"]
        assert after_fill[UP]["price"] < first[UP]["price"]


# ======================================================================
# 4. THROUGH THE RUNNER: recorded from the real ledger, and nothing placed
# ======================================================================
NEW_ENV = ("L5_PORTFOLIO_CONTROLLER",)


def harness(tmp_path, monkeypatch, **env):
    for k, v in T.PRODUCTION.items():
        monkeypatch.setenv(k, v)
    for k in T.ABSENT + NEW_ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    return T.Harness(tmp_path, monkeypatch, root="live")


def records(h):
    """Decision records the runner emitted (the harness captures `emit`)."""
    return [json.loads(json.dumps(rec, default=str))
            for label, rec in h.emitted if label == "L5_PORTFOLIO_DECISION"]


class TestTheRunnerStage:
    @pytest.mark.parametrize("val", [None, "", "ON", "1", "LIVE", "shadow",
                                     "TRUE"])
    def test_it_is_off_unless_the_owner_sets_shadow(self, tmp_path,
                                                    monkeypatch, val):
        env = {} if val is None else {"L5_PORTFOLIO_CONTROLLER": val}
        h = harness(tmp_path, monkeypatch, **env)
        h.at(30.0)
        assert h.r.maybe_portfolio_decision(T.WS + 30.0) is None
        assert PC.mode() == PC.OFF
        assert records(h) == []

    def test_shadow_records_the_real_ledgers_portfolio(self, tmp_path,
                                                       monkeypatch):
        h = harness(tmp_path, monkeypatch, L5_PORTFOLIO_CONTROLLER="SHADOW")
        T.opened(h)                       # a real order resting at the venue
        doc = h.r.maybe_portfolio_decision(h.r.now_t)
        assert doc is not None and doc["mode"] == "SHADOW"
        rec = records(h)
        assert len(rec) == 1 and rec[0]["market"] == T.SLUG
        live = h.r.store.live_orders()
        assert sorted(o["id"] for o in doc["resting"]) == sorted(live)
        assert doc["portfolio"]["up_qty"] == 0
        assert doc["belief"]["source"] == "BOOK"
        assert "placed and cancelled nothing" in doc["labels"]
        assert doc["reference"]["reason"] == "REFERENCE_DISABLED"
        assert {a["op"] for a in doc["actions"]} <= {"KEEP", "CANCEL",
                                                     "PLACE"}

    def test_shadow_places_and_cancels_nothing(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, L5_PORTFOLIO_CONTROLLER="SHADOW")
        T.opened(h)
        posts, cancels = list(h.venue.sent), list(h.venue.cancelled)
        before = json.dumps(h.r.store.state, sort_keys=True, default=str)
        for i in range(5):
            h.r.maybe_portfolio_decision(h.r.now_t + 6.0 * i)
        assert h.venue.sent == posts and h.venue.cancelled == cancels
        assert len(records(h)) == 5
        assert json.dumps(h.r.store.state, sort_keys=True,
                          default=str) == before

    def test_it_is_throttled(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, L5_PORTFOLIO_CONTROLLER="SHADOW")
        h.at(30.0)
        for dt_ in (0.0, 1.0, 2.0, 4.9, 5.1):
            h.r.maybe_portfolio_decision(T.WS + 30.0 + dt_)
        assert len(records(h)) == 2

    def test_a_failure_is_a_counter_and_never_an_exception(self, tmp_path,
                                                           monkeypatch):
        h = harness(tmp_path, monkeypatch, L5_PORTFOLIO_CONTROLLER="SHADOW")
        h.at(30.0)

        def boom(*a, **k):
            raise RuntimeError("ledger unreadable")
        monkeypatch.setattr(h.r.store, "leg_cost", boom)
        assert h.r.maybe_portfolio_decision(T.WS + 30.0) is None
        assert h.r.stats["portfolio_decision_error:RuntimeError"] == 1

    def test_the_tick_calls_it(self):
        src = inspect.getsource(R.Runner.tick)
        assert src.count("self.maybe_portfolio_decision(now)") == 1


# ======================================================================
# 5. THERE IS NO WAY FROM THIS MODULE TO THE VENUE
# ======================================================================
class TestIsolation:
    def test_the_module_has_three_modes_and_no_others(self, monkeypatch):
        for val in ("ON", "LIVE", "ARMED", "1", "true", "SHADOW ", "steer"):
            monkeypatch.setenv("L5_PORTFOLIO_CONTROLLER", val)
            assert PC.mode() == PC.OFF
        monkeypatch.delenv("L5_PORTFOLIO_CONTROLLER")
        assert PC.mode() == PC.OFF
        for val in ("SHADOW", "STEER"):
            monkeypatch.setenv("L5_PORTFOLIO_CONTROLLER", val)
            assert PC.mode() == val

    def test_the_module_imports_nothing_that_can_trade(self):
        tree = ast.parse(open(PC.__file__).read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        assert mods <= {"__future__", "math", "os", "dataclasses", "typing"}

    def test_the_runner_stage_calls_nothing_that_submits_or_cancels(self):
        src = inspect.getsource(R.Runner.maybe_portfolio_decision)
        code = src.split('"""', 2)[2]
        for needle in (".handle(", "submit", ".cancel", "record_submit",
                       "apply_fill", ".save(", "terminate_order", "arm",
                       "L5_LIVE_HALT", "L5_SHADOW_ONLY",
                       "L5_TS_OWN_EVIDENCE_GATE"):
            assert needle not in code, needle

    def test_only_the_runner_uses_the_module(self):
        users = []
        for base, _d, files in os.walk(os.path.join(ROOT, "src")):
            for f in files:
                if f.endswith(".py") and f != "l5_portfolio_controller.py":
                    if "l5_portfolio_controller" in open(
                            os.path.join(base, f), errors="ignore").read():
                        users.append(f)
        assert users == ["l5_pmus_live_runner.py"]

    def test_the_gates_and_authorization_were_not_touched(self):
        for name in ("l5_two_sided_probe.py", "l5_unmatched_disposition.py",
                     "l5_live_authorization.py"):
            txt = open(os.path.join(ROOT, "src", name)).read()
            assert "portfolio_controller" not in txt
            assert "portfolio_steer" not in txt

    def test_the_controller_itself_never_reaches_the_venue(self):
        """The module decides; the runner and executor act. Nothing in the
        module can submit, cancel or write."""
        code = open(PC.__file__).read().split('"""', 2)[2]
        for needle in ("submit(", ".cancel(", "deadman", "urllib", "socket",
                       ".save(", "record_submit", "transport"):
            assert needle not in code, needle


def test_a_missing_module_costs_a_counter_and_never_a_tick(tmp_path,
                                                           monkeypatch):
    """The image-closure guard caught this module missing from the image.
    It is copied now -- and the stage must survive even if it were not."""
    import builtins
    h = harness(tmp_path, monkeypatch, L5_PORTFOLIO_CONTROLLER="SHADOW")
    h.at(30.0)
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src" and fromlist and \
                "l5_portfolio_controller" in fromlist:
            raise ImportError("not in the image")
        return real(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert h.r.maybe_portfolio_decision(T.WS + 30.0) is None
    assert h.r.stats["portfolio_decision_error:ImportError"] == 1


def test_the_module_is_in_the_executor_image():
    df = open(os.path.join(ROOT, "deploy", "l5_pmus_live",
                           "Dockerfile")).read()
    assert ("COPY src/l5_portfolio_controller.py "
            "src/l5_portfolio_controller.py") in df


# ======================================================================
# 6. THE STEER: what the existing pair engine may be told
# ======================================================================
MODEL = PC.Limits(cash_usd=100.0, exposure_usd=100.0, max_orders=2)


def steer_for(p_up, port_=None, bid=0.52, ask=0.54, approved=2, **kw):
    pr = PC.production_params() if not kw else PC.Params(**kw)
    dec = PC.decide(port_ or port(), bid=bid, ask=ask, p_up=p_up, t_off=60,
                    params=pr, limits=MODEL)
    return PC.steer(dec, approved_depth_ticks=approved)


class TestTheSteer:
    def test_a_strongly_favored_side_is_the_only_one_opened(self):
        st = steer_for(0.90)
        assert st["sides"][UP]["wanted"] is True
        assert st["sides"][DOWN]["wanted"] is False
        assert st["sides"][DOWN]["reason"] in ("NOT_COMPETITIVE",
                                               "NO_POSITIVE_PRICE")
        assert st["any_wanted"] and st["extra_depth_ticks"] == 0
        assert st["belief"]["source"] == "MODEL"
        mirror = steer_for(0.10)
        assert mirror["sides"][DOWN]["wanted"] and \
            not mirror["sides"][UP]["wanted"]

    def test_without_a_clear_edge_nothing_is_opened(self):
        for p in (0.50, 0.53, 0.60):
            st = steer_for(p)
            assert st["any_wanted"] is False, p

    def test_without_a_reference_nothing_is_opened(self):
        st = steer_for(None)
        assert st["belief"]["source"] == "BOOK"
        assert st["any_wanted"] is False

    def test_it_can_deepen_the_plan_and_never_shallow_it(self):
        rng = random.Random(6)
        for _ in range(500):
            approved = rng.randint(0, 4)
            bid = round(rng.uniform(0.05, 0.9), 2)
            st = steer_for(rng.uniform(0.02, 0.98), bid=bid,
                           ask=round(bid + 0.02, 2), approved=approved,
                           margin=rng.choice((0, .02, .04)),
                           adverse=rng.choice((0, .02)),
                           risk=rng.choice((0, .1)))
            assert st["extra_depth_ticks"] >= 0
            assert st["plan_depth_ticks"] >= approved
            for v in st["sides"].values():
                if v["wanted"] and v["role"] == "ADD":
                    assert v["ticks_behind_touch"] <= st["plan_depth_ticks"]

    def test_a_side_wanted_only_deeper_than_the_plan_is_not_opened(self):
        # UP is worth the touch, DOWN only four ticks back: the plan sits
        # at the approved depth and DOWN is left out of it.
        st = steer_for(0.56, margin=0.0, adverse=0.0, risk=0.0, approved=1)
        assert st["sides"][UP]["wanted"] is True
        assert st["sides"][DOWN]["wanted"] is False
        assert st["sides"][DOWN]["reason"] == \
            "WANTED_ONLY_DEEPER_THAN_THE_PLAN"

    def test_both_sides_deep_moves_the_whole_plan_back(self):
        st = steer_for(0.53, margin=0.02, adverse=0.02, risk=0.0, approved=1)
        assert st["extra_depth_ticks"] >= 1
        assert st["plan_depth_ticks"] == 1 + st["extra_depth_ticks"]

    def test_production_uses_the_setting_the_replay_chose(self):
        with open(os.path.join(ROOT, "analysis", "l5",
                               "integrated_replay.json")) as fh:
            tuned = json.load(fh)["tuned_on_IS"]["params"]
        assert PC.PRODUCTION == tuned
        pr = PC.production_params("0.01")
        assert (pr.margin, pr.adverse, pr.risk, pr.tick) == (
            tuned["margin"], tuned["adverse"], tuned["risk"], 0.01)
        assert PC.production_params(None).tick == 0.01


class TestWithdrawals:
    PR = PC.production_params()

    def book(self, *orders, u=0, cu=0, d=0, cd=0):
        return port(u, cu, d, cd, orders=[
            {"id": i, "side": sd, "price": px, "qty": 1}
            for i, (sd, px) in enumerate(orders)])

    def test_an_order_the_portfolio_would_still_pay_for_stays(self):
        w = PC.withdrawals(self.book((UP, 0.50)), p_up=0.90, params=self.PR)
        assert w == []

    def test_an_order_priced_above_what_it_would_pay_is_named(self):
        w = PC.withdrawals(self.book((UP, 0.50)), p_up=0.55, params=self.PR)
        assert [x["id"] for x in w] == [0]
        assert w[0]["reason"] == "PRICED_ABOVE_WHAT_THE_PORTFOLIO_WOULD_PAY"
        assert w[0]["reservation"] < 0.50

    def test_the_band_stops_place_and_pull_on_one_threshold(self):
        p = self.book((UP, 0.50))
        res_at = lambda q: PC.reservation_price(p, side=UP, p_up=q,
                                                params=self.PR)
        # find the belief at which the reservation is exactly the price
        lo, hi = 0.5, 0.99
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if res_at(mid) < 0.50 else (lo, mid)
        just_under = lo - 0.01      # reservation a tick under the price
        assert PC.withdrawals(p, p_up=just_under, params=self.PR) == []
        assert PC.withdrawals(p, p_up=lo - 0.05, params=self.PR) != []

    def test_a_hedge_is_never_named(self):
        p = self.book((DOWN, 0.60), (UP, 0.50), u=2, cu=0.9)
        for belief in (0.99, 0.50, 0.01, None):
            named = PC.withdrawals(p, p_up=belief, params=self.PR)
            assert all(x["side"] == UP for x in named), belief

    def test_with_no_belief_every_opening_order_comes_down(self):
        p = self.book((UP, 0.50), (DOWN, 0.44))
        for junk in (None, 0, 1, float("nan"), "x"):
            w = PC.withdrawals(p, p_up=junk, params=self.PR)
            assert sorted(x["side"] for x in w) == [DOWN, UP]
            assert {x["reason"] for x in w} == {"NO_BELIEF"}


# ======================================================================
# 7. THE EXECUTOR HOOK: it narrows, it never widens
# ======================================================================
class TestTheExecutorHook:
    def ex(self, steer=None, residual="0"):
        from src import l5_live_execution as E

        class Store:
            def residual(self, market):
                if residual == "boom":
                    raise RuntimeError("unreadable")
                return D(residual)
        e = E.FaithfulExecutor.__new__(E.FaithfulExecutor)
        e.store = Store()
        e.portfolio_steer = steer
        return e

    def test_no_steer_changes_nothing(self):
        e = self.ex(None)
        assert e.portfolio_steer_extra_depth() == 0
        assert e.portfolio_steer_refusal("UP", None) is None
        assert e.portfolio_steer_refusal("DOWN", "UP") is None

    def test_the_class_default_is_no_steer(self):
        from src import l5_live_execution as E
        assert E.FaithfulExecutor.portfolio_steer is None

    @pytest.mark.parametrize("raw,want", [
        (0, 0), (3, 3), (-4, 0), (99, 10), ("2", 2), (None, 0), ("x", 0),
        (2.9, 2)])
    def test_extra_depth_only_deepens_and_is_bounded(self, raw, want):
        e = self.ex({"extra_depth_ticks": raw, "sides": {}})
        assert e.portfolio_steer_extra_depth() == want

    def test_an_unwanted_opening_is_refused_by_name(self):
        e = self.ex({"sides": {"UP": {"wanted": True},
                               "DOWN": {"wanted": False}}})
        assert e.portfolio_steer_refusal("UP", None) is None
        assert e.portfolio_steer_refusal("DOWN", None) == \
            "PORTFOLIO_SIDE_NOT_WANTED"

    def test_a_leg_that_reduces_filled_inventory_is_never_refused(self):
        e = self.ex({"sides": {"UP": {"wanted": False},
                               "DOWN": {"wanted": False}}})
        assert e.portfolio_steer_refusal("DOWN", "DOWN") is None
        assert e.portfolio_steer_refusal("UP", "DOWN") == \
            "PORTFOLIO_SIDE_NOT_WANTED"

    @pytest.mark.parametrize("steer", [
        {}, {"sides": None}, {"sides": {"UP": None}}, {"sides": {}},
        {"sides": {"UP": {"wanted": "yes"}}}])
    def test_an_unreadable_steer_is_not_permission(self, steer):
        e = self.ex(steer)
        assert e.portfolio_steer_refusal("UP", None) in (
            "PORTFOLIO_STEER_UNREADABLE", "PORTFOLIO_SIDE_NOT_WANTED")

    def test_only_filled_inventory_makes_a_hedge(self):
        assert self.ex(residual="0").portfolio_held_reducing_side("m") is None
        assert self.ex(residual="0.6").portfolio_held_reducing_side("m") \
            == "DOWN"
        assert self.ex(residual="-0.6").portfolio_held_reducing_side("m") \
            == "UP"
        assert self.ex(residual="boom").portfolio_held_reducing_side("m") \
            is None


# ======================================================================
# 8. STEER THROUGH THE REAL RUNNER AND EXECUTOR
# ======================================================================
STEER_ENV = {"L5_PORTFOLIO_CONTROLLER": "STEER",
             "L5_BTC_REFERENCE": "VENUE_INDEX"}


class Feed:
    """Stands in for `l5_btc_reference.Reference`: no thread, no network."""

    def __init__(self, p):
        self.p, self.calls = p, 0

    def estimate(self, slug, ws, now=None):
        self.calls += 1
        if self.p is None:
            return {"ok": False, "p_up": None, "reason": "INDEX_STALE",
                    "decision_t": now, "index_age_s": 9.0}
        return {"ok": True, "p_up": self.p, "reason": None,
                "decision_t": now, "index_age_s": 0.4, "seconds_left": 800.0,
                "distance": 50.0, "bucket_t": int(now) - 1}


def steered(tmp_path, monkeypatch, p, **env):
    for k in ("L5_PORTFOLIO_CONTROLLER", "L5_BTC_REFERENCE"):
        monkeypatch.delenv(k, raising=False)
    h = harness(tmp_path, monkeypatch, **env)
    h.r._btc_reference = Feed(p)
    return h


def ticks(h, *offs):
    for off in offs:
        h.tick(off)
    return h


class TestSteerThroughTheRunner:
    def test_unset_the_engine_opens_exactly_as_before(self, tmp_path,
                                                      monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90), 7.0, 8.0, 9.0)
        assert sorted(h.sent()) == [("DOWN", D("0.44"), D("0.6")),
                                    ("UP", D("0.50"), D("0.6"))]
        assert h.r._btc_reference.calls == 0
        assert not [k for k in h.r.stats if "portfolio" in k.lower()]

    def test_the_favored_side_opens_alone_at_the_approved_depth(
            self, tmp_path, monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90, **STEER_ENV),
                  7.0, 8.0, 9.0, 10.0)
        assert h.sent() == [("UP", D("0.50"), D("0.6"))]
        assert h.r.stats[
            "controller_open:pass2:PORTFOLIO_SIDE_NOT_WANTED"] == 1
        h2 = ticks(steered(tmp_path / "b", monkeypatch, 0.10, **STEER_ENV),
                   7.0, 8.0, 9.0, 10.0)
        assert h2.sent() == [("DOWN", D("0.44"), D("0.6"))]

    @pytest.mark.parametrize("p", [0.50, 0.53, 0.60])
    def test_without_a_clear_edge_nothing_is_opened(self, tmp_path,
                                                    monkeypatch, p):
        h = ticks(steered(tmp_path, monkeypatch, p, **STEER_ENV), 7.0, 8.0)
        assert h.sent() == []
        assert h.r.stats["controller_open:PORTFOLIO_NOTHING_WANTED"] == 2

    def test_a_stale_reference_opens_nothing(self, tmp_path, monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, None, **STEER_ENV),
                  7.0, 8.0)
        assert h.sent() == []

    def test_steer_without_a_reference_opens_nothing(self, tmp_path,
                                                     monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90,
                          L5_PORTFOLIO_CONTROLLER="STEER"), 7.0, 8.0)
        assert h.sent() == []
        assert h.r._btc_reference.calls == 0

    def test_an_unreadable_portfolio_opens_nothing(self, tmp_path,
                                                   monkeypatch):
        h = steered(tmp_path, monkeypatch, 0.90, **STEER_ENV)

        def boom(*a, **k):
            raise RuntimeError("ledger unreadable")
        monkeypatch.setattr(h.r.store, "leg_cost", boom)
        ticks(h, 7.0, 8.0)
        assert h.sent() == []
        assert h.r.stats["portfolio_steer_error:RuntimeError"] >= 2

    def test_the_steer_exists_for_one_call_only(self, tmp_path, monkeypatch):
        h = steered(tmp_path, monkeypatch, 0.90, **STEER_ENV)
        seen = []
        real = h.r.executor.handle

        def spy(*a, **k):
            seen.append(h.r.executor.portfolio_steer is not None)
            return real(*a, **k)
        monkeypatch.setattr(h.r.executor, "handle", spy)
        ticks(h, 7.0, 8.0)
        assert seen and all(seen)
        assert h.r.executor.portfolio_steer is None
        # and an L5 action afterwards is not steered
        h.at(11.0).l5_action()
        assert seen[-1] is False

    def test_the_steer_is_cleared_even_if_the_executor_raises(
            self, tmp_path, monkeypatch):
        h = steered(tmp_path, monkeypatch, 0.90, **STEER_ENV)

        def boom(*a, **k):
            raise RuntimeError("executor failed")
        monkeypatch.setattr(h.r.executor, "handle", boom)
        with pytest.raises(RuntimeError):
            h.r.maybe_controller_open(T.WS + 7.0)
        assert h.r.executor.portfolio_steer is None

    def test_a_filled_leg_is_still_hedged_when_nothing_is_wanted(
            self, tmp_path, monkeypatch):
        """The favored UP leg fills; the belief then goes flat, so the
        controller wants no opening -- and the DOWN hedge is placed all the
        same, because a hedge is never the steer's to stop."""
        h = ticks(steered(tmp_path, monkeypatch, 0.90, **STEER_ENV), 7.0)
        assert h.sent() == [("UP", D("0.50"), D("0.6"))]
        oid = [o for o in h.venue.by_oid][0]
        h.venue.fill(oid)
        h.r._btc_reference.p = 0.52
        for off in (10.5, 14.0, 17.5, 21.0, 24.5, 28.0):
            h.tick(off)
        assert h.r.store.position(T.SLUG, "UP") == D("0.6")
        downs = [x for x in h.sent() if x[0] == "DOWN"]
        assert downs, (h.sent(), dict(h.r.stats))

    def test_the_decision_record_carries_the_reference_and_its_clocks(
            self, tmp_path, monkeypatch):
        h = steered(tmp_path, monkeypatch, 0.90, **STEER_ENV)
        h.at(30.0)
        doc = h.r.maybe_portfolio_decision(T.WS + 30.0)
        assert doc["mode"] == "STEER"
        assert doc["belief"] == {"p_up": 0.90, "source": "MODEL"}
        ref = doc["reference"]
        # the estimate is stamped with the WALL clock at the instant it is
        # used, not the tick's `now`, and the gap between them is recorded
        import time
        assert ref["ok"] and abs(ref["decision_t"] - time.time()) < 5.0
        assert ref["tick_now"] == T.WS + 30.0
        assert ref["tick_lag_s"] == pytest.approx(
            ref["decision_t"] - (T.WS + 30.0))
        assert ref["index_age_s"] == 0.4
        h.r._btc_reference.p = None
        doc = h.r.maybe_portfolio_decision(T.WS + 36.0)
        assert doc["belief"]["source"] == "BOOK"
        assert doc["reference"]["reason"] == "INDEX_STALE"


class TestTheWithdrawStage:
    def test_it_does_nothing_outside_steer(self, tmp_path, monkeypatch):
        for env in ({}, {"L5_PORTFOLIO_CONTROLLER": "SHADOW",
                         "L5_BTC_REFERENCE": "VENUE_INDEX"}):
            h = steered(tmp_path / str(len(env)), monkeypatch, 0.5, **env)
            T.opened(h)
            assert h.r.maybe_portfolio_withdraw(h.r.now_t + 1) is None
            assert h.venue.cancelled == []

    def test_an_opening_the_reference_turned_against_is_cancelled_by_id(
            self, tmp_path, monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90, **STEER_ENV), 7.0)
        assert h.sent() == [("UP", D("0.50"), D("0.6"))]
        assert h.r.maybe_portfolio_withdraw(T.WS + 8.0) is None   # still good
        assert h.venue.cancelled == []
        h.r._btc_reference.p = 0.50
        out = h.r.maybe_portfolio_withdraw(T.WS + 9.0)
        assert out is not None and len(h.venue.cancelled) == 1
        assert [w["side"] for w in out["withdrawn"]] == ["UP"]
        assert out["withdrawn"][0]["reason"] == \
            "PRICED_ABOVE_WHAT_THE_PORTFOLIO_WOULD_PAY"
        rec = [r for label, r in h.emitted
               if label == "L5_PORTFOLIO_WITHDRAW"]
        assert len(rec) == 1 and "CANCELLATION" in rec[0]["labels"]
        assert h.r.store.live_orders() == {}

    def test_a_lost_reference_takes_the_opening_down(self, tmp_path,
                                                     monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90, **STEER_ENV), 7.0)
        h.r._btc_reference.p = None
        out = h.r.maybe_portfolio_withdraw(T.WS + 9.0)
        assert out["withdrawn"][0]["reason"] == "NO_BELIEF"
        assert len(h.venue.cancelled) == 1

    def test_a_hedge_is_never_withdrawn(self, tmp_path, monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90, **STEER_ENV), 7.0)
        h.venue.fill([o for o in h.venue.by_oid][0])
        h.r._btc_reference.p = 0.52
        for off in (10.5, 14.0, 17.5, 21.0, 24.5, 28.0):
            h.tick(off)
        assert [x for x in h.sent() if x[0] == "DOWN"]
        before = list(h.venue.cancelled)
        h.r._btc_reference.p = None                 # the worst belief
        assert h.r.maybe_portfolio_withdraw(T.WS + 30.0) is None
        assert h.venue.cancelled == before

    def test_a_failure_is_a_counter_and_no_cancellation(self, tmp_path,
                                                        monkeypatch):
        h = ticks(steered(tmp_path, monkeypatch, 0.90, **STEER_ENV), 7.0)

        def boom(*a, **k):
            raise RuntimeError("ledger unreadable")
        monkeypatch.setattr(h.r.store, "leg_cost", boom)
        assert h.r.maybe_portfolio_withdraw(T.WS + 9.0) is None
        assert h.venue.cancelled == []
        assert h.r.stats["portfolio_withdraw_error:RuntimeError"] == 1

    def test_it_cancels_through_the_existing_deadman_and_nothing_else(self):
        src = inspect.getsource(R.Runner.maybe_portfolio_withdraw)
        code = src.split('"""', 2)[2]
        assert code.count("LX.deadman(") == 1 and "only=ids" in code
        for needle in (".handle(", "submit", "record_submit", "apply_fill",
                       "L5_LIVE_HALT", "L5_SHADOW_ONLY",
                       "L5_TS_OWN_EVIDENCE_GATE"):
            assert needle not in code, needle

    def test_the_tick_calls_it_after_the_decision_record(self):
        src = inspect.getsource(R.Runner.tick)
        assert src.count("self.maybe_portfolio_withdraw(now)") == 1
        assert src.index("self.maybe_portfolio_decision(now)") < \
            src.index("self.maybe_portfolio_withdraw(now)")


def test_the_estimate_is_never_stamped_with_the_ticks_stale_clock(
        tmp_path, monkeypatch):
    """REGRESSION, production 2026-11-10 19:22Z. The stage ran 5.4 s after
    the tick captured `now`; stamping the estimate with `now` made the
    newest index second look 5.1 s in the future and every estimate was
    refused CLOCK_MISALIGNED. The real feed must be asked with the wall
    clock."""
    import time
    from src import l5_btc_reference as BR
    h = steered(tmp_path, monkeypatch, 0.90, **STEER_ENV)
    asked = []

    class Real:
        def estimate(self, slug, ws, now=None):
            asked.append(now)
            series = [(int(now) - 1200 + i, 73000.0) for i in range(1200)]
            return BR.probability_up(series=series, strike=72950.0,
                                     window_end=now + 500.0, now=now)
    h.r._btc_reference = Real()
    h.at(30.0)
    stale_tick_clock = T.WS + 30.0          # far from the wall clock
    doc = h.r.maybe_portfolio_decision(stale_tick_clock)
    assert abs(asked[0] - time.time()) < 5.0
    assert doc["reference"]["ok"] is True
    assert doc["reference"]["reason"] is None
    assert doc["belief"]["source"] == "MODEL"


def test_the_portfolio_stages_run_before_the_slow_diagnostics():
    src = inspect.getsource(R.Runner.tick)
    diag = src.index("self.drain_roll_diagnostics(now)")
    assert src.index("self.maybe_portfolio_decision(now)") < diag
    assert src.index("self.maybe_portfolio_withdraw(now)") < diag
    assert src.index("self.maybe_controller_open(now)") < src.index(
        "self.maybe_portfolio_decision(now)")
