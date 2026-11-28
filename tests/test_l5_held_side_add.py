"""With a completion resting, nothing may quietly add to the side held.

FOUND BY REPLAYING RECORDED VENUE BOOKS THROUGH THE REAL RUNNER
(`analysis/l5/runner_replay_20261111.py`). With completions resting at their
target, a completion order is on the book for as long as inventory is
unmatched. The controller's opening path read that order as the first leg of
a new pair and granted the "complement" its slot -- on the side ALREADY
HELD. On the 2026-11-03 00:30Z book it held 0.62 DOWN bought at 0.50, UP
rested at 0.48 with the market at 0.79/0.80, and it sent DOWN 0.28 at 0.18.
That order filled. Nothing had decided to add to a losing position.

`FaithfulExecutor.held_side_add_refusal` refuses it at the submission
boundary. Inert unless completions rest at their target; then it only ever
refuses, and an add the portfolio controller's steer asks for still goes.
"""
from __future__ import annotations

import importlib.util
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_intent_gate as IG
from src import l5_live_execution as E
from src import l5_two_sided_probe as TS

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "runner_replay_20261111",
    ROOT / "analysis" / "l5" / "runner_replay_20261111.py")
RR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RR)

RAN_AWAY = "cpc-btc-updown-15m-2026-11-03-0030z"     # DOWN fills, UP runs off
STRANDED = "cpc-btc-updown-15m-2026-11-08-0130z"
SWITCHES = (TS.INVENTORY_RECOVERY_ENV, TS.COMPLETION_RESTS_AT_CEILING_ENV)


# ---------------------------------------------------------------- the gate
class Store:
    def __init__(self, residual):
        self._r = residual

    def residual(self, market):
        if isinstance(self._r, BaseException):
            raise self._r
        return self._r


def gate(residual, side, steer=None):
    ex = type("X", (), {})()
    ex.store, ex.portfolio_steer = Store(residual), steer
    return E.FaithfulExecutor.held_side_add_refusal(ex, "m", {"side": side})


@pytest.fixture
def resting(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(TS.INVENTORY_RECOVERY_ENV, "ON")


class TestTheGate:

    def test_off_by_default_it_refuses_nothing(self, monkeypatch):
        for k in SWITCHES:
            monkeypatch.delenv(k, raising=False)
        assert gate(D("-0.62"), "DOWN") is None
        assert gate(ValueError("unreadable"), "DOWN") is None

    @pytest.mark.parametrize("switch", SWITCHES)
    def test_either_switch_that_rests_a_completion_arms_it(
            self, monkeypatch, switch):
        for k in SWITCHES:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv(switch, "ON")
        assert gate(D("-0.62"), "DOWN")["why"] == \
            "HELD_SIDE_ADD_WHILE_UNMATCHED"

    @pytest.mark.parametrize("residual,side", [
        (D("0.62"), "UP"), (D("-0.62"), "DOWN"), (D("0.01"), "UP")])
    def test_more_of_the_held_side_is_refused(self, resting, residual, side):
        bad = gate(residual, side)
        assert bad["why"] == "HELD_SIDE_ADD_WHILE_UNMATCHED"
        assert D(bad["unmatched_contracts"]) == abs(residual)

    @pytest.mark.parametrize("residual,side", [
        (D("0.62"), "DOWN"), (D("-0.62"), "UP")])
    def test_the_completing_side_is_never_refused(self, resting, residual,
                                                  side):
        assert gate(residual, side) is None

    @pytest.mark.parametrize("side", ["UP", "DOWN"])
    def test_flat_it_refuses_nothing_so_both_sides_can_open(self, resting,
                                                            side):
        assert gate(D("0"), side) is None

    def test_an_add_the_steer_asks_for_still_goes(self, resting):
        steer = {"sides": {"DOWN": {"wanted": True, "role": "ADD"},
                           "UP": {"wanted": True}}}
        assert gate(D("-0.62"), "DOWN", steer) is None

    @pytest.mark.parametrize("steer", [
        None, {}, {"sides": {}}, {"sides": {"DOWN": {"wanted": False}}},
        {"sides": {"DOWN": {"wanted": "yes"}}}, {"sides": None}, "STEER"])
    def test_a_steer_that_does_not_ask_for_it_does_not_admit_it(
            self, resting, steer):
        assert gate(D("-0.62"), "DOWN", steer)

    @pytest.mark.parametrize("residual", [
        ValueError("x"), D("NaN"), D("Infinity"), None, "abc"])
    def test_unreadable_inventory_refuses(self, resting, residual):
        assert gate(residual, "DOWN")["why"] == \
            "HELD_SIDE_ADD_INVENTORY_UNREADABLE"

    def test_the_refusal_is_in_the_executors_inventory(self):
        import inspect
        assert "HELD_SIDE_ADD" in inspect.getsource(IG)
        src = inspect.getsource(E.FaithfulExecutor.handle)
        assert src.index("held_side_add_refusal") < src.index(
            "pair_cost_refusal(market, dec, tr)")


# ------------------------------------------- the real runner, recorded books
def rows(slug):
    return RR.books()[slug]["rows"]


def adds_to_held(slug, env, end):
    """Every order sent on the side already held unmatched."""
    seen = []

    def on_submit(h, intent):
        net = h.pos("UP") - h.pos("DOWN")
        held = "UP" if net > 0 else "DOWN" if net < 0 else None
        if held and str(intent["side"]) == held:
            seen.append((str(intent["side"]), D(str(intent["price"])),
                         D(str(intent["qty"]))))
    res = RR.replay(rows(slug), env, end=end, on_submit=on_submit)
    return seen, res


class TestThroughTheRealRunner:

    def test_0030z_without_the_gate_it_added_to_the_losing_side(
            self, monkeypatch):
        """Failing-before: the order the gate now refuses."""
        monkeypatch.setattr(E.FaithfulExecutor, "held_side_add_refusal",
                            lambda self, market, dec: None)
        seen, res = adds_to_held(RAN_AWAY, RR.CONFIGS["recovery_0.98"], 345)
        assert seen == [("DOWN", D("0.1800"), D("0.28"))]
        assert res["filled"]["DOWN"] == D("0.90")     # and it filled

    def test_0030z_with_the_gate_nothing_is_added(self):
        seen, res = adds_to_held(RAN_AWAY, RR.CONFIGS["recovery_0.98"], 345)
        assert seen == []
        assert res["filled"] == {"UP": D("0"), "DOWN": D("0.62")}
        # the completing side kept working the whole time
        p = RR.presence(res["log"])
        assert p["unmatched:completion_resting"] > \
            9 * p["unmatched:no_completion_order"]

    def test_0030z_as_deployed_is_untouched_by_it(self):
        seen, res = adds_to_held(RAN_AWAY, RR.CONFIGS["as_deployed"], 345)
        assert seen == []
        assert res["filled"] == {"UP": D("0"), "DOWN": D("0.62")}

    def test_both_sides_rest_together_before_anything_fills(self):
        for name in ("as_deployed", "recovery_0.98"):
            res = RR.replay(rows(STRANDED), RR.CONFIGS[name], end=40)
            first = res["log"][0]
            assert first["up_rest"] and first["dn_rest"], name
            pair = D(first["up_rest"][0]) + D(first["dn_rest"][0])
            assert pair <= D("0.96"), name

    def test_0130z_the_gap_and_what_closes_it(self):
        """As deployed, the unmatched leg has no completion order most of
        the time; with recovery it has one almost throughout."""
        dep = RR.presence(RR.replay(rows(STRANDED),
                                    RR.CONFIGS["as_deployed"])["log"])
        rec = RR.presence(RR.replay(rows(STRANDED),
                                    RR.CONFIGS["recovery_0.98"])["log"])
        assert dep["unmatched:no_completion_order"] > \
            dep["unmatched:completion_resting"]
        assert rec["unmatched:completion_resting"] > \
            20 * rec["unmatched:no_completion_order"]

    def test_no_completion_is_ever_priced_above_a_dollar_pair(self):
        over = []

        def on_submit(h, intent):
            net = h.pos("UP") - h.pos("DOWN")
            if net == 0:
                return
            held = "UP" if net > 0 else "DOWN"
            if str(intent["side"]) == held:
                return
            legs = h.r.store.leg_cost(h.venue.market_slug)
            avg = D(str(legs[held])) / h.pos(held)
            if avg + D(str(intent["price"])) > D("1.00"):
                over.append((str(avg), str(intent["price"])))
        for slug in (RAN_AWAY, STRANDED):
            for name in ("recovery_0.98", "recovery_1.00"):
                RR.replay(rows(slug), RR.CONFIGS[name], on_submit=on_submit)
        assert over == []
