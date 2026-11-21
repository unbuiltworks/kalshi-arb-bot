"""The owner's favorite-only live test, default off.

OWNER, 2026-11-11: "Buy the favorite halfway through and it's 75% or more."
and "I want to test the favorite live."

`L5_PORTFOLIO_PROFILE=FAVORITE_ONLY` with `L5_PORTFOLIO_CONTROLLER=STEER`:
from the halfway point of the window, if one side's own bid is at or above
0.75, the existing engine places ONE maker order on that side at its
approved depth, and nothing else -- no second leg, no hedge, no pair. The
position is held to settlement. Every existing limit, gate and clock still
applies; the profile adds a steer that wants one side and a submission gate
that refuses everything else.

NOTHING HERE SAYS THE FAVORITE IS WORTH ITS PRICE. On 380 recorded windows
this rule lost 6.7c a contract taking the offer and 10.1c to 14.4c resting
a bid. The profile exists so the owner can measure it live at a bounded
size; it is not promoted by existing.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import tests.test_l5_continuous_config as CC
import tests.test_l5_ladder_runner as LAD
from src import l5_live_execution as E
from src import l5_portfolio_controller as PC

FAV = {"L5_MAX_CASH_DEPLOYED_USD": "0.90", "L5_TS_ROUND_ALLOCATIONS": "1",
       "L5_TS_CONCURRENT_ROUNDS": "1", "L5_MAX_LIVE_ORDERS": "4",
       "L5_ACC_MAX_ROUNDS": "8", "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON",
       "L5_HEDGE_CASH_AT_LIMIT": "ON",
       "L5_PORTFOLIO_CONTROLLER": "STEER",
       "L5_PORTFOLIO_PROFILE": "FAVORITE_ONLY",
       "L5_TS_MAX_FIRST_LEG_PRICE": "0.95"}
SLUG = CC.SLUG


# ======================================================================
# 1. THE DECISION
# ======================================================================
def port(up=0.0, dn=0.0, resting=()):
    return PC.portfolio(up_qty=up, up_cost=up * 0.78, dn_qty=dn,
                        dn_cost=dn * 0.78, orders=list(resting))


def params(monkeypatch, **env):
    monkeypatch.setenv(PC.PROFILE_ENV, "FAVORITE_ONLY")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return PC.with_overrides(PC.production_params(0.01), base_qty=0.01,
                             max_qty=1e6)


def decide(p, bid, ask, t_off, pt=None):
    return PC.decide(pt or port(), bid=bid, ask=ask, t_off=t_off, params=p,
                     limits=PC.Limits(cash_usd=0.90, exposure_usd=1.00,
                                      max_orders=4))


def wanted(dec):
    return [(q["side"], q["role"]) for q in dec["quotes"]]


def why(dec, side):
    return [x["reason"] for x in dec["skipped"] if x["side"] == side]


class TestTheProfileIsNamedExactly:

    @pytest.mark.parametrize("raw", [None, "", "favorite_only", "FAVORITE",
                                     "FAVORITE_ONLY ", "ON", "BALANCE"])
    def test_anything_else_is_not_the_favorite_test(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(PC.PROFILE_ENV, raising=False)
        else:
            monkeypatch.setenv(PC.PROFILE_ENV, raw)
        assert PC.favorite_only() is False
        assert PC.production_params(0.01).fav_only is False

    def test_the_exact_name(self, monkeypatch):
        p = params(monkeypatch)
        assert PC.favorite_only() is True and p.fav_only is True
        assert (p.fav_only_min_price, p.fav_only_start_s) == (0.75, 450.0)
        assert PC.profile_uses_model() is False

    def test_balance_is_unchanged(self, monkeypatch):
        monkeypatch.setenv(PC.PROFILE_ENV, "BALANCE")
        p = PC.production_params(0.01)
        assert p.fav_only is False and p.balance_first is True


class TestWhenTheFavoriteIsWanted:

    def test_not_before_halfway(self, monkeypatch):
        d = decide(params(monkeypatch), 0.85, 0.86, 449.0)
        assert wanted(d) == []
        assert why(d, "UP") == why(d, "DOWN") == ["FAVORITE_NOT_YET"]

    def test_at_halfway_the_side_priced_at_75_or_more(self, monkeypatch):
        d = decide(params(monkeypatch), 0.80, 0.81, 450.0)
        assert wanted(d) == [("UP", "FAVORITE")]
        assert why(d, "DOWN") == ["NOT_THE_FAVORITE"]

    def test_the_down_side_is_the_same_decision(self, monkeypatch):
        d = decide(params(monkeypatch), 0.18, 0.19, 455.0)
        assert wanted(d) == [("DOWN", "FAVORITE")]
        assert why(d, "UP") == ["NOT_THE_FAVORITE"]

    @pytest.mark.parametrize("bid,ask", [(0.74, 0.75), (0.50, 0.51),
                                         (0.26, 0.27), (0.60, 0.62)])
    def test_under_75_nothing(self, monkeypatch, bid, ask):
        assert wanted(decide(params(monkeypatch), bid, ask, 460.0)) == []

    def test_exactly_75_counts(self, monkeypatch):
        assert wanted(decide(params(monkeypatch), 0.75, 0.76, 460.0)) == [
            ("UP", "FAVORITE")]

    def test_too_dear_to_have_a_book_behind_it(self, monkeypatch):
        d = decide(params(monkeypatch), 0.95, 0.96, 460.0)
        assert wanted(d) == [] and why(d, "UP") == ["FAVORITE_TOO_DEAR"]

    def test_not_after_the_entry_cutoff(self, monkeypatch):
        d = decide(params(monkeypatch), 0.80, 0.81, 481.0)
        assert wanted(d) == [] and why(d, "UP") == ["AFTER_ENTRY_CUTOFF"]

    def test_once_held_nothing_more_and_no_hedge(self, monkeypatch):
        d = decide(params(monkeypatch), 0.80, 0.81, 460.0, port(up=0.5))
        assert wanted(d) == []
        assert why(d, "UP") == why(d, "DOWN") == ["FAVORITE_ALREADY_HELD"]

    def test_while_one_rests_nothing_more(self, monkeypatch):
        rest = [{"id": "a", "side": "UP", "price": 0.78, "qty": 0.5}]
        d = decide(params(monkeypatch), 0.80, 0.81, 460.0,
                   port(resting=rest))
        assert wanted(d) == []
        assert why(d, "UP") == ["FAVORITE_ALREADY_RESTING"]

    def test_the_steer_wants_only_the_favorite(self, monkeypatch):
        st = PC.steer(decide(params(monkeypatch), 0.80, 0.81, 460.0),
                      approved_depth_ticks=2)
        assert st["sides"]["UP"]["wanted"] is True
        assert st["sides"]["UP"]["role"] == "FAVORITE"
        assert st["sides"]["DOWN"]["wanted"] is False
        assert st["extra_depth_ticks"] == 0

    def test_it_withdraws_nothing(self, monkeypatch):
        rest = [{"id": "a", "side": "UP", "price": 0.78, "qty": 0.5}]
        assert PC.withdrawals(port(resting=rest), p_up=0.40,
                              params=params(monkeypatch), t_off=500.0) == []


class TestTheOwnersNumbers:

    def test_a_different_threshold_and_start(self, monkeypatch):
        p = params(monkeypatch, L5_FAVONLY_MIN_PRICE="0.85",
                   L5_FAVONLY_START_S="600")
        assert wanted(decide(p, 0.80, 0.81, 460.0)) == []
        p2 = PC.with_overrides(p, entry_cutoff_s=660.0)
        assert wanted(decide(p2, 0.86, 0.87, 600.0)) == [("UP", "FAVORITE")]

    @pytest.mark.parametrize("env", [
        {"L5_FAVONLY_MIN_PRICE": "0.40"}, {"L5_FAVONLY_MIN_PRICE": "abc"},
        {"L5_FAVONLY_MIN_PRICE": "0.99"}, {"L5_FAVONLY_START_S": "30"},
        {"L5_FAVONLY_START_S": "nan"}, {"L5_FAVONLY_MAX_PRICE": "0.99"},
        {"L5_FAVONLY_MIN_PRICE": "0.90", "L5_FAVONLY_MAX_PRICE": "0.80"}])
    def test_one_bad_number_and_it_wants_nothing_ever(self, monkeypatch,
                                                      env):
        p = params(monkeypatch, **env)
        for t in (10.0, 450.0, 470.0, 479.0):
            assert wanted(decide(p, 0.80, 0.81, t)) == []


# ======================================================================
# 2. THE SUBMISSION GATE
# ======================================================================
def gate(steer, side):
    ex = type("X", (), {})()
    ex.portfolio_steer = steer
    fn = next(getattr(c, "favorite_only_refusal") for c in vars(E).values()
              if isinstance(c, type) and hasattr(c, "favorite_only_refusal"))
    return fn(ex, {"side": side})


FAV_STEER = {"sides": {"UP": {"wanted": True, "role": "FAVORITE"},
                       "DOWN": {"wanted": False,
                                "reason": "NOT_THE_FAVORITE"}}}


class TestTheGateOnlyAdmitsTheFavorite:

    def test_the_executor_and_the_controller_read_the_same_token(self):
        """The executor does not import the controller, so the name of the
        profile is stated twice. They must be the same statement."""
        assert E.FAVORITE_ONLY_PROFILE_ENV == PC.PROFILE_ENV
        assert E.FAVORITE_ONLY_PROFILE == PC.FAVORITE_ONLY_PROFILE

    def test_off_unless_the_profile_is_named(self, monkeypatch):
        monkeypatch.delenv(PC.PROFILE_ENV, raising=False)
        assert gate(None, "DOWN") is None
        monkeypatch.setenv(PC.PROFILE_ENV, "BALANCE")
        assert gate(None, "DOWN") is None

    def test_the_favorite_passes(self, monkeypatch):
        monkeypatch.setenv(PC.PROFILE_ENV, "FAVORITE_ONLY")
        assert gate(FAV_STEER, "UP") is None

    def test_the_other_side_is_refused(self, monkeypatch):
        monkeypatch.setenv(PC.PROFILE_ENV, "FAVORITE_ONLY")
        assert gate(FAV_STEER, "DOWN")["why"] == \
            "FAVORITE_ONLY_NOT_THE_FAVORITE"

    @pytest.mark.parametrize("steer", [None, "x", {}, {"sides": None},
                                       {"sides": {"UP": {"wanted": True}}},
                                       {"sides": {"UP": {"wanted": True,
                                                         "role": "ADD"}}},
                                       {"sides": {"UP": {"wanted": "yes",
                                                         "role":
                                                         "FAVORITE"}}}])
    def test_anything_short_of_a_wanted_favorite_is_refused(
            self, monkeypatch, steer):
        monkeypatch.setenv(PC.PROFILE_ENV, "FAVORITE_ONLY")
        assert gate(steer, "UP") is not None


# ======================================================================
# 3. THE REAL RUNNER
# ======================================================================
def window(tmp_path, monkeypatch, at_half=("0.80", "0.81"), fill=True,
           env=FAV, half_at=440.0, early=("0.50", "0.51")):
    h = CC.steered(CC.limits(LAD.harness(tmp_path, monkeypatch, **env)))
    h.book.set(*early)
    h.at(6.5).l5_action()
    off, log = 6.8, {"early": None, "half": None}
    while off < half_at:
        off += 6.0
        h.tick(off)
    log["early"] = list(h.sent())
    h.book.set(*at_half)
    while off < 475:
        off += 3.0
        h.tick(off)
    log["half"] = list(h.sent())
    if fill:
        for oid in LAD.resting_ids(h):
            h.venue.fill(oid)
    while off < 640:
        off += 5.0
        h.tick(off)
    return h, log


class TestInTheRunner:

    def test_nothing_before_halfway_even_at_90(self, tmp_path, monkeypatch):
        _h, log = window(tmp_path, monkeypatch, early=("0.90", "0.91"),
                         at_half=("0.50", "0.51"))
        assert log["early"] == [] and log["half"] == []

    def test_one_order_on_the_favorite_two_ticks_under_its_bid(
            self, tmp_path, monkeypatch):
        h, log = window(tmp_path, monkeypatch)
        assert log["early"] == []
        assert [(s, p) for s, p, _q in log["half"]] == [("UP", D("0.78"))]
        q = log["half"][0][2]
        assert D(0) < q * D("0.78") <= D("0.90")     # inside the cash cap

    def test_the_filled_favorite_is_held_and_never_hedged(
            self, tmp_path, monkeypatch):
        h, log = window(tmp_path, monkeypatch)
        assert list(h.sent()) == log["half"]         # no second order, ever
        assert h.pos("UP") == log["half"][0][2] and h.pos("DOWN") == 0
        assert LAD.resting_ids(h) == []
        refused = sum(v for k, v in h.r.stats.items()
                      if "FAVORITE_ONLY" in k)
        assert refused >= 10                         # hedges were proposed
        assert CC.worst_loss(h) <= CC.DEFICIT
        assert h.r.store.orders_uncertain() is None

    def test_the_hedge_stays_refused_when_it_would_lock_a_profit(
            self, tmp_path, monkeypatch):
        """Favorite at 0.78, then the other side falls to 0.10: a pair at
        0.88 is on offer. The test holds the favorite regardless."""
        h, log = window(tmp_path, monkeypatch)
        h.book.set("0.90", "0.91")
        n = len(h.venue.sent)
        for off in (480.0, 500.0, 520.0, 540.0, 560.0):
            h.tick(off)
        assert len(h.venue.sent) == n

    def test_no_favorite_no_order(self, tmp_path, monkeypatch):
        h, _log = window(tmp_path, monkeypatch, at_half=("0.60", "0.61"))
        assert list(h.sent()) == [] and len(h.venue.cancelled) == 0

    def test_the_down_side_when_it_is_the_favorite(self, tmp_path,
                                                   monkeypatch):
        h, log = window(tmp_path, monkeypatch, at_half=("0.18", "0.19"))
        assert [(s, p) for s, p, _q in log["half"]] == [("DOWN", D("0.79"))]
        assert list(h.sent()) == log["half"]
        assert h.pos("DOWN") == log["half"][0][2] and h.pos("UP") == 0
        assert CC.worst_loss(h) <= CC.DEFICIT

    def test_an_unfilled_favorite_is_cancelled_and_not_replaced(
            self, tmp_path, monkeypatch):
        h, log = window(tmp_path, monkeypatch, fill=False)
        assert len(log["half"]) == 1
        assert list(h.sent()) == log["half"]
        assert len(h.venue.cancelled) == 1
        assert LAD.resting_ids(h) == []
        assert h.pos("UP") == h.pos("DOWN") == 0

    def test_a_favorite_that_appears_after_the_cutoff_is_not_bought(
            self, tmp_path, monkeypatch):
        h, _log = window(tmp_path, monkeypatch, at_half=("0.60", "0.61"))
        h2, _ = window(tmp_path / "b", monkeypatch,
                       at_half=("0.60", "0.61"))
        h2.book.set("0.85", "0.86")
        for off in (650.0, 660.0):
            h2.tick(off)
        assert list(h2.sent()) == []

    def test_without_the_steer_the_test_orders_nothing_at_all(
            self, tmp_path, monkeypatch):
        """Profile named but the controller not steering: every order is
        refused rather than the engine falling back to pairs."""
        env = dict(FAV)
        env.pop("L5_PORTFOLIO_CONTROLLER")
        h, _log = window(tmp_path, monkeypatch, env=env)
        assert list(h.sent()) == []
        assert sum(v for k, v in h.r.stats.items()
                   if "FAVORITE_ONLY_NO_STEER" in k) > 0

    def test_the_default_engine_is_untouched(self, tmp_path, monkeypatch):
        env = dict(FAV)
        env.pop("L5_PORTFOLIO_PROFILE")
        env.pop("L5_PORTFOLIO_CONTROLLER")
        env.pop("L5_TS_MAX_FIRST_LEG_PRICE")
        h, log = window(tmp_path, monkeypatch, env=env)
        sides = {s for s, _p, _q in log["early"]}
        assert sides == {"UP", "DOWN"}               # pairs, as always
        assert not any("FAVORITE_ONLY" in k for k in h.r.stats)
