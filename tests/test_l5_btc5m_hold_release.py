"""Cancelled quotes give their budget back inside the window; nothing else
does.

Live window 2026-11-20 14:20Z: holds were released only between windows.
Seven requote rounds reserved $0.5742 against a $0.60 envelope while $0.17
was actually at risk, every UP quote was refused ENVELOPE:AGGREGATE_EXPOSURE
from t=80 s, and two-sided quoting stopped 68 s into a 300 s window.

FOUR INVARIANTS, each pinned below:
  1. only a VENUE-CONFIRMED cancel releases; a request or a timeout does not
  2. fills and uncertain orders keep their holds; a partial fill keeps what
     it cost
  3. nothing is released twice
  4. the caps are the caps: the envelope is never exceeded
"""
from __future__ import annotations

import inspect
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_persistent_mode as P
from src import l5_shared_reservation as R
from src import l5_venue_constraints as VC

from tests.test_l5_btc5m_execution import (           # noqa: E402
    MARKET, T0, armed, clean_env,                     # noqa: F401
)
from tests.test_l5_btc5m_continuous_participation import Rig6

CAP = D("0.60")
LANE = X.STRATEGY


def held():
    a = R.aggregate()
    assert a["ok"], a
    return a["usd"]


def holds():
    with open(R.path(), encoding="utf-8") as fh:
        return {k: D(h["usd"]) for k, h in json.load(fh)["holds"].items()}


@pytest.fixture
def live(monkeypatch, tmp_path, armed):
    """The 14:20Z window's shape: two-sided resting quotes that follow the
    book, under a $0.60 standing envelope."""
    monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
    monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.05")
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "0.30")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.30")
    monkeypatch.setenv(X.STRADDLE_BOOK_RELATIVE_ENV, "YES")
    monkeypatch.setenv(X.STRADDLE_PAIR_CEILING_ENV, "0.99")
    monkeypatch.setenv(X.STRADDLE_FILL_MODEL_ENV, X.FILL_MODEL_TOKEN)
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, "2")
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "0.20")
    monkeypatch.setenv(X.STRADDLE_LEG_MAX_AGE_S_ENV, "240")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "5")
    monkeypatch.setenv(VC.REQUIRE_ENV, VC.REQUIRE_TOKEN)
    monkeypatch.setenv(P.DEPLOYMENT_ENV, "hold-release-test")
    monkeypatch.setenv(P.ENVELOPE_ENV, json.dumps({
        "approved": True, "mode": "PERSISTENT",
        "deployment": "hold-release-test", "strategies": ["STRADDLE"],
        "series": [MARKET.rsplit("-", 4)[0] + "-"], "taker_allowed": False,
        "max_aggregate_usd": str(CAP), "max_per_market_usd": str(CAP),
        "max_daily_loss_usd": str(CAP), "nonce": "hold-release-0001"}))
    #: a persistent envelope now also needs the owner's campaign loss
    #: halt; one sized to the envelope leaves these tests' bounds as they
    #: were
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, json.dumps({
        "campaign_id": "hold-release-test",
        "started_utc": (T0 - dt.timedelta(minutes=5)).isoformat(),
        "issued_utc": (T0 - dt.timedelta(minutes=5)).isoformat(),
        "expires_utc": (T0 + dt.timedelta(hours=6)).isoformat(),
        "max_loss_usd": str(CAP), "venue_results": {}}))
    monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
    P.write_state(P.ARMED, why="test", now=T0)
    return tmp_path


@pytest.fixture
def rig(tmp_path, live):
    #: the one-window behaviour probe the live window ran under, so the
    #: entry-EV gate is waived exactly as it was there and nothing else is
    return Rig6(tmp_path, max_live_orders=6,
                ev_waiver_fn=lambda **_k: {"present": True, "ok": True})


def wander(rig, steps, start=0):
    """Step a book that keeps moving, so the quotes are replaced again and
    again. Returns one record per step."""
    out = []
    for i in range(start, start + steps):
        k = (i // 2) % 2
        rep = rig.tick(5).go(t=5.0 + 5 * i, bid="0.%02d" % (50 + 6 * k),
                             ask="0.%02d" % (51 + 6 * k))
        out.append({"i": i, "placed": len(rep["placed"]),
                    "refused": [r["reason"] for r in rep["refused"]],
                    "held": held()})
    return out


def sent_notional(rig):
    return sum(D(str(s["price"])) * D(str(s["qty"])) for s in rig.venue.sent)


# =============================================== the defect, end to end

def test_quoting_is_sustained_through_a_whole_window(rig):
    """56 steps of 5 s is 280 s. The live window stalled at 68 s."""
    steps = wander(rig, 56)
    assert not [s for s in steps
                if any("AGGREGATE_EXPOSURE" in r or "RESERVATION" in r
                       for r in s["refused"])]
    #: still placing in the last fifth of the window
    assert sum(s["placed"] for s in steps[-11:]) > 0
    #: and it really did recycle: far more was submitted than the cap holds
    assert sent_notional(rig) > 3 * CAP, sent_notional(rig)


def test_the_envelope_is_never_exceeded_while_doing_it(rig):
    steps = wander(rig, 56)
    assert max(s["held"] for s in steps) <= CAP
    #: and what is held is what is working, not what was ever submitted
    live_now = sum(D(r["qty"]) * D(r["price"])
                   for r in rig.ex.live_orders().values())
    assert held() == live_now


def test_without_the_per_step_release_the_window_stalls(rig, monkeypatch):
    """The live defect, reproduced: take the per-step release away and the
    same book exhausts the same envelope."""
    monkeypatch.setattr(X.Btc5mExecutor, "sync_reservation",
                        lambda self: {"ok": True, "released": []})
    steps = wander(rig, 56)
    assert any("AGGREGATE_EXPOSURE" in r or "RESERVATION" in r
               for s in steps for r in s["refused"])
    assert sent_notional(rig) <= CAP


def test_the_cap_still_refuses_when_inventory_really_fills_it(rig):
    """Releasing cancelled quotes must not turn the cap into a suggestion:
    fills are real exposure and they keep counting."""
    for i in range(40):
        rig.tick(5).go(t=5.0 + 5 * i)
        rig.fill_all()
    assert held() <= CAP
    filled_cost = sum(D(r["filled_cost"]) for r in rig.ex.orders().values())
    assert filled_cost <= CAP
    assert held() >= filled_cost


# ============== 1. only a venue-confirmed cancel releases

def first_quotes(rig):
    rig.go(t=5.0)
    assert rig.venue.sent, "nothing was quoted"
    return dict(holds())


def move(rig, i=1):
    return rig.tick(5).go(t=5.0 + 5 * i, bid="0.56", ask="0.57")


def test_a_cancel_that_is_only_requested_releases_nothing(rig):
    before = first_quotes(rig)
    for oid in rig.venue.by_oid:
        rig.venue.lag[oid] = 3            # the venue has not confirmed yet
    move(rig)
    assert rig.venue.cancelled, "the scenario needs a cancel request"
    now = holds()
    for k, usd in before.items():
        assert now.get(k) == usd, k       # every original hold intact


def test_the_hold_goes_when_the_venue_confirms_and_not_before(rig):
    before = first_quotes(rig)
    for oid in rig.venue.by_oid:
        rig.venue.lag[oid] = 2
    move(rig, 1)
    assert set(before) <= set(holds())
    for i in range(2, 6):
        move(rig, i)
    assert not (set(before) & set(holds()))


def test_a_cancel_whose_reread_times_out_releases_nothing(rig):
    before = first_quotes(rig)
    for oid in rig.venue.by_oid:
        rig.venue.read_error[oid] = 50    # every read of it fails
    for i in range(1, 5):
        move(rig, i)
    now = holds()
    for k, usd in before.items():
        assert now.get(k) == usd, k
    assert all(rig.ex.orders()[k]["state"] in X.OPEN for k in before)


def test_a_submit_whose_outcome_is_unknown_keeps_its_hold(rig):
    rig.venue.submit_error = TimeoutError("submit timed out")
    rig.go(t=5.0)
    rig.venue.submit_error = None
    unsure = [c for c, r in rig.ex.orders().items()
              if r["state"] == X.UNCERTAIN]
    assert unsure, "the scenario needs an uncertain submission"
    for i in range(1, 4):
        rig.tick(5).go(t=5.0 + 5 * i)
    assert set(unsure) <= set(holds())


# ============== 2. fills keep their holds; a partial keeps what it cost

def test_a_filled_order_keeps_its_hold_until_settlement(rig):
    before = first_quotes(rig)
    rig.fill_all()
    for i in range(1, 6):
        move(rig, i)
    now = holds()
    for k, usd in before.items():
        assert now.get(k) == usd, k


def test_a_partial_fill_then_cancel_keeps_exactly_the_filled_cost(rig):
    before = first_quotes(rig)
    oid = next(iter(rig.venue.by_oid))
    coid = rig.venue.by_oid[oid]["client_order_id"]
    want = D(str(rig.venue.by_oid[oid]["qty"]))
    part = (want / 2).quantize(D("0.01"))
    assert D("0") < part < want
    rig.venue.fill(oid, qty=part)
    for i in range(1, 6):
        move(rig, i)
    row = rig.ex.orders()[coid]
    assert row["state"] == X.CANCELLED and D(row["filled_qty"]) == part
    assert holds()[coid] == D(row["filled_cost"])
    assert D("0") < holds()[coid] < before[coid]


def test_a_partial_fill_still_working_keeps_the_whole_hold(rig):
    before = first_quotes(rig)
    oid = next(iter(rig.venue.by_oid))
    coid = rig.venue.by_oid[oid]["client_order_id"]
    want = D(str(rig.venue.by_oid[oid]["qty"]))
    rig.venue.fill(oid, qty=(want / 2).quantize(D("0.01")))
    rig.tick(5).go(t=10.0)                 # same book: it stays working
    assert rig.ex.orders()[coid]["state"] in X.OPEN
    assert holds()[coid] == before[coid]


def test_a_fill_that_beats_the_cancel_is_held_not_released(rig):
    before = first_quotes(rig)
    rig.venue.fill_on_cancel = set(rig.venue.by_oid)
    for i in range(1, 5):
        move(rig, i)
    now = holds()
    late = [c for c in before if rig.ex.orders()[c]["state"] == X.FILLED]
    assert late, "the scenario needs a fill that beat its cancel"
    for c in late:
        assert now[c] == before[c]


def test_an_unreadable_fill_cost_keeps_the_whole_hold(rig):
    before = first_quotes(rig)
    oid = next(iter(rig.venue.by_oid))
    coid = rig.venue.by_oid[oid]["client_order_id"]
    rig.venue.fill(oid)
    rig.tick(5).go(t=10.0)
    rig.ex._ledger["orders"][coid]["filled_cost"] = "not-a-number"
    rig.ex.sync_reservation()
    assert holds()[coid] == before[coid]


# ============== 3. nothing is released twice

def test_repeating_the_reconciliation_frees_nothing_more(rig):
    first_quotes(rig)
    oid = next(iter(rig.venue.by_oid))
    rig.venue.fill(oid, qty="0.01")
    for i in range(1, 6):
        move(rig, i)
    once = holds()
    for _ in range(5):
        again = rig.ex.sync_reservation()
        assert again["ok"] and again["released"] == []
        assert again["reduced"] == [] and D(again["freed_usd"]) == 0
    assert holds() == once


def test_a_restart_frees_nothing_the_ledger_does_not_show_finished(rig):
    first_quotes(rig)
    rig.fill_all()
    for i in range(1, 4):
        move(rig, i)
    before = holds()
    ex2 = rig.restart()
    got = ex2.sync_reservation()
    assert got["ok"] and got["released"] == [] and got["reduced"] == []
    assert holds() == before


def test_a_restart_mid_cancel_keeps_the_unconfirmed_hold(rig):
    before = first_quotes(rig)
    for oid in rig.venue.by_oid:
        rig.venue.lag[oid] = 5
    move(rig)                               # cancel requested, not confirmed
    ex2 = rig.restart()
    ex2.sync_reservation()
    assert set(before) <= set(holds())


def test_another_lanes_holds_are_never_touched(rig):
    R.reserve(lane="ETH5M", key="eth-1", usd="0.10", now=T0)
    wander(rig, 12)
    assert holds()["eth-1"] == D("0.10")


# ============== the reservation primitive itself

class TestSettleTo:

    @pytest.fixture(autouse=True)
    def _file(self, tmp_path, monkeypatch):
        monkeypatch.setenv(R.PATH_ENV, str(tmp_path / "r.json"))
        for k, usd in (("a", "0.10"), ("b", "0.20"), ("c", "0.05")):
            assert R.reserve(lane="L", key=k, usd=usd, now=T0)["ok"]
        assert R.reserve(lane="OTHER", key="o", usd="0.07", now=T0)["ok"]

    def test_an_unnamed_hold_is_released_and_a_named_one_kept(self):
        got = R.settle_to(lane="L", keep={"a": None, "b": None})
        assert got["released"] == ["c"] and D(got["freed_usd"]) == D("0.05")
        assert holds() == {"a": D("0.10"), "b": D("0.20"), "o": D("0.07")}

    def test_a_hold_shrinks_to_what_is_at_risk(self):
        got = R.settle_to(lane="L", keep={"a": None, "b": "0.04", "c": None})
        assert got["reduced"] == ["b"] and D(got["freed_usd"]) == D("0.16")
        assert holds()["b"] == D("0.04")

    @pytest.mark.parametrize("amt", ["0.20", "0.50", "99"])
    def test_a_hold_never_grows(self, amt):
        R.settle_to(lane="L", keep={"a": None, "b": amt, "c": None})
        assert holds()["b"] == D("0.20")

    @pytest.mark.parametrize("amt", ["0", "-1", "x", "", float("nan")])
    def test_an_unusable_amount_leaves_the_hold_alone(self, amt):
        R.settle_to(lane="L", keep={"a": None, "b": amt, "c": None})
        assert holds()["b"] == D("0.20")

    def test_it_is_idempotent(self):
        keep = {"a": "0.03", "b": None}
        first = R.settle_to(lane="L", keep=keep)
        assert D(first["freed_usd"]) == D("0.12")
        for _ in range(4):
            again = R.settle_to(lane="L", keep=keep)
            assert again["released"] == [] and again["reduced"] == []
            assert D(again["freed_usd"]) == 0
        assert R.aggregate()["usd"] == D("0.30")

    def test_it_never_touches_another_lane(self):
        R.settle_to(lane="L", keep={})
        assert holds() == {"o": D("0.07")}

    def test_a_key_it_was_told_to_keep_but_does_not_hold_is_not_created(self):
        R.settle_to(lane="L", keep={"a": None, "b": None, "c": None,
                                    "ghost": "0.40"})
        assert "ghost" not in holds()

    def test_an_unreadable_request_releases_nothing(self):
        got = R.settle_to(lane="L", keep=None)
        assert got["ok"] is False and len(holds()) == 4

    def test_an_unset_path_releases_nothing(self, monkeypatch):
        monkeypatch.delenv(R.PATH_ENV)
        assert R.settle_to(lane="L", keep={})["ok"] is False


# ============== structure

def test_the_step_releases_before_it_decides():
    src = inspect.getsource(X.Btc5mExecutor.step)
    assert src.index("self.poll()") < src.index("self.sync_reservation()") \
        < src.index("C.decide(")


def test_the_caps_are_not_changed_by_this():
    assert inspect.signature(R.reserve).parameters["cap_usd"].default \
        == R.MAX_AGGREGATE_USD
    src = inspect.getsource(X.Btc5mExecutor.sync_reservation)
    assert "reserve(" not in src and "MAX_AGGREGATE" not in src
