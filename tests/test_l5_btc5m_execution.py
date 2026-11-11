"""The BTC 5-minute execution adapter, against a scripted venue.

Deterministic and offline: one scripted venue, one clock, one ledger file
under pytest's tmp_path. The controller is the real pure module unless a
test says it scripts the decision, and a test that scripts it does so only
to hand the adapter an order the controller would not emit, so the
adapter's OWN refusal is what is exercised.

Nothing here is evidence about a real venue, arming on a real service, or
economics. It proves mechanism: what is sent, what is never sent, what is
booked, and what survives a restart.
"""
from __future__ import annotations

import collections
import datetime as dt
import inspect
import json
import re
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
import importlib.util as _ilu
import pathlib as _pl

# the adapter is a PROTOTYPE outside src/ (see prototypes/README.md)
_spec = _ilu.spec_from_file_location(
    "l5_btc5m_execution_prototype",
    _pl.Path(__file__).resolve().parents[1] / "prototypes"
    / "l5_btc5m_execution.py")
X = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(X)

UTC = dt.timezone.utc
MARKET = "cpc-btc-updown-5m-2026-11-11-1200z"
OTHER_5M = "cpc-btc-updown-5m-2026-11-11-1205z"
M15 = "cpc-btc-updown-15m-2026-11-11-1200z"
T0 = dt.datetime(2026, 11, 11, 12, 0, 30, tzinfo=UTC)
NOT_AFTER = "2026-11-11T12:05:00Z"

ENV = (C.CONTROLLER_ENV, X.LIVE_ENV, X.MARKET_ENV, X.NOT_AFTER_ENV,
       X.HALT_ENV, X.SHADOW_ONLY_ENV, X.ACCOUNT_CAP_ENV)
ARMED = {C.CONTROLLER_ENV: "ON", X.LIVE_ENV: X.ARM_TOKEN,
         X.MARKET_ENV: MARKET, X.NOT_AFTER_ENV: NOT_AFTER,
         X.SHADOW_ONLY_ENV: "0", X.ACCOUNT_CAP_ENV: "10"}

#: the first order the real controller emits on book 0.60/0.62 with p_up
#: 0.75: two ticks under the bid, $0.10 clip floored to the 0.01 grid
FIRST = ("UP", D("0.58"), D("0.17"))
FIRST_COST = D("0.0986")


# ------------------------------------------------------------------ doubles

class Venue:
    """The five calls the adapter may make, scripted. Anything else it
    reaches for raises, so an unexpected transport call fails the test."""

    def __init__(self):
        self.armed = True
        self.sent, self.cancelled = [], []
        self.by_oid, self.cum = {}, {}
        self.retired, self.full = set(), set()
        self.lag = collections.Counter()
        self.read_error = collections.Counter()
        self.reads = collections.Counter()
        self.submit_error = None      # raise this BEFORE the order exists
        self.ack = None               # answer submit with this instead
        self.before_submit = None
        self.fill_on_cancel = set()
        self.account_error = None
        self.extra_open, self.extra_positions = [], []

    def __getattr__(self, name):
        raise AssertionError("the adapter reached for transport.%s" % name)

    def submit(self, intent):
        if self.before_submit is not None:
            self.before_submit(intent)
        if self.submit_error is not None:
            raise self.submit_error
        coid = intent["client_order_id"]
        if any(s["client_order_id"] == coid for s in self.sent):
            raise RuntimeError("DUPLICATE_CLIENT_ORDER_ID")
        self.sent.append(dict(intent))
        oid = "v%d" % len(self.sent)
        self.by_oid[oid], self.cum[oid] = dict(intent), "0"
        if self.ack is not None:
            return self.ack
        return {"order_id": oid, "filled_qty": "0",
                "fill_price": str(intent["price"])}

    def cancel(self, order_id, market_slug=None):
        self.cancelled.append((order_id, market_slug))
        if order_id in self.fill_on_cancel:
            self.fill(order_id)           # the fill beat the cancel
        else:
            self.retired.add(order_id)
        return {"cancelled": order_id}

    def fill(self, oid, qty=None):
        want = D(str(self.by_oid[oid]["qty"]))
        got = want if qty is None else D(str(qty))
        assert got <= want
        self.cum[oid] = str(got)
        if got == want:
            self.full.add(oid)

    def order(self, oid):
        self.reads[oid] += 1
        if self.read_error[oid] > 0:
            self.read_error[oid] -= 1
            raise ConnectionError("venue read failed")
        qty, cum = D(str(self.by_oid[oid]["qty"])), D(self.cum[oid])
        if oid in self.full:
            state, leaves = "ORDER_STATE_FILLED", D(0)
        elif oid in self.retired and self.lag[oid] <= 0:
            state, leaves = "ORDER_STATE_CANCELED", D(0)
        else:
            if oid in self.retired:
                self.lag[oid] -= 1
            state = ("ORDER_STATE_PARTIALLY_FILLED" if cum > 0
                     else "ORDER_STATE_NEW")
            leaves = qty - cum
        return {"id": oid, "state": state, "quantity": str(qty),
                "cumQuantity": str(cum), "leavesQuantity": str(leaves)}

    def open_orders(self):
        if self.account_error is not None:
            raise self.account_error
        return [{"id": oid, "clientOrderId": i["client_order_id"],
                 "marketSlug": i["market"], "qty": str(i["qty"]),
                 "remainingQuantity": str(D(str(i["qty"])) - D(self.cum[oid]))}
                for oid, i in self.by_oid.items()
                if oid not in self.full and oid not in self.retired
                ] + list(self.extra_open)

    def positions(self):
        if self.account_error is not None:
            raise self.account_error
        rows = []
        for market in sorted({i["market"] for i in self.by_oid.values()}):
            up = sum(D(self.cum[o]) for o, i in self.by_oid.items()
                     if i["market"] == market and i["side"] == "UP")
            dn = sum(D(self.cum[o]) for o, i in self.by_oid.items()
                     if i["market"] == market and i["side"] == "DOWN")
            if up or dn:
                rows.append({"marketSlug": market, "qtyBought": str(up),
                             "qtySold": str(dn),
                             "netPositionDecimal": str(up - dn)})
        return rows + list(self.extra_positions)

    def cancelled_ids(self):
        return [oid for oid, _slug in self.cancelled]


class Rig:
    def __init__(self, tmp_path, venue="default", **kw):
        self.venue = Venue() if venue == "default" else venue
        self.path = str(tmp_path / "btc5m" / "ledger.json")
        self.now = T0
        self.kw = kw
        self.ex = self.build()

    def build(self):
        return X.Btc5mExecutor(self.venue, self.path,
                               now_fn=lambda: self.now, **self.kw)

    def restart(self):
        self.ex = self.build()
        return self.ex

    def tick(self, seconds=1.0):
        self.now += dt.timedelta(seconds=seconds)
        return self

    def step(self, bid="0.60", ask="0.62", p_up="0.75", t=30.0, market=MARKET):
        return self.ex.step(market=market,
                            book={"bid": bid, "ask": ask, "age_s": 0.1},
                            t_in_window_s=t, p_up=p_up, p_source="TEST")

    def sent(self):
        return [(s["side"], D(s["price"]), D(s["qty"])) for s in self.venue.sent]

    def disk(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def armed(monkeypatch):
    for k, v in ARMED.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def place(side="UP", price="0.58", qty="0.17", post_only=True, intent="BUY"):
    return {"op": "PLACE", "side": side, "price": D(price), "qty": D(qty),
            "post_only": post_only, "intent": intent, "reason": "SCRIPTED"}


def scripted(monkeypatch, *orders):
    """Hand the adapter exactly these orders in place of a real decision."""
    def decide(**_kw):
        return {"action": "SCRIPTED", "why": "SCRIPTED",
                "orders": [dict(o) for o in orders], "evaluation": {},
                "exposure": None, "favorite": None, "p_up": None,
                "p_source": None}
    monkeypatch.setattr(C, "decide", decide)


def opened(rig):
    """One real decision: the first maker order is resting as v1."""
    rep = rig.step()
    assert rig.sent() == [FIRST], rep
    return rep


def reasons(rep):
    return [r["reason"] for r in rep["refused"]]


# ======================================================================
# 0. OFF BY DEFAULT
# ======================================================================
class TestOffUnlessTheOwnerArmedIt:

    def test_controller_off_does_nothing_at_all(self, rig):
        rep = rig.step()
        assert rep["action"] == C.DISABLED
        assert rep["placed"] == rep["cancelled"] == rep["would_submit"] == []
        assert rig.venue.sent == [] and rig.venue.cancelled == []

    def test_disarmed_sends_nothing_and_says_what_it_would_send(
            self, rig, monkeypatch):
        monkeypatch.setenv(C.CONTROLLER_ENV, "ON")
        rep = rig.step()
        assert rep["action"] == C.OPEN_FAVORITE
        assert rig.venue.sent == [] and rep["placed"] == []
        assert rep["arming_refusal"] == "NOT_ARMED"
        (w,) = rep["would_submit"]
        assert (w["side"], D(w["price"]), D(w["qty"])) == FIRST
        assert w["market"] == MARKET and w["blocked_by"] == "NOT_ARMED"
        assert w["client_order_id"].startswith("b5m-")
        assert w["post_only"] is True and w["mode"] == "MAKER"
        # nothing was created, so nothing is in the ledger
        assert rig.ex.orders() == {}

    @pytest.mark.parametrize("env,market,want", [
        ({X.LIVE_ENV: "armed_by_owner"}, MARKET, "NOT_ARMED"),
        ({X.LIVE_ENV: "1"}, MARKET, "NOT_ARMED"),
        ({X.LIVE_ENV: "ARMED_BY_OWNER "}, MARKET, "NOT_ARMED"),
        ({X.MARKET_ENV: None}, MARKET, "MARKET_NOT_CONFIGURED"),
        ({X.MARKET_ENV: OTHER_5M}, MARKET, "MARKET_MISMATCH"),
        ({X.MARKET_ENV: MARKET + " "}, MARKET, "MARKET_MISMATCH"),
        ({X.MARKET_ENV: M15}, M15, "NOT_A_BTC5M_MARKET"),
        ({X.NOT_AFTER_ENV: "2026-11-11T12:00:29Z"}, MARKET, "NOT_AFTER_PASSED"),
        ({X.NOT_AFTER_ENV: None}, MARKET, "NOT_AFTER_MISSING"),
        ({X.NOT_AFTER_ENV: "  "}, MARKET, "NOT_AFTER_MISSING"),
        ({X.NOT_AFTER_ENV: "soon"}, MARKET, "NOT_AFTER_UNREADABLE"),
        # no offset: not a UTC time anyone can be held to
        ({X.NOT_AFTER_ENV: "2026-11-11T12:05:00"}, MARKET, "NOT_AFTER_UNREADABLE"),
        ({X.HALT_ENV: "1"}, MARKET, "L5_LIVE_HALT"),
        ({X.SHADOW_ONLY_ENV: "1"}, MARKET, "L5_SHADOW_ONLY"),
    ])
    def test_each_arming_refusal_sends_nothing(self, rig, armed, monkeypatch,
                                               env, market, want):
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k)
            else:
                monkeypatch.setenv(k, v)
        assert rig.ex.arming_refusal(market, T0) == want
        rep = rig.step(market=market)
        assert rig.venue.sent == [] and rep["placed"] == []
        assert rep["arming_refusal"] == want
        assert [w["blocked_by"] for w in rep["would_submit"]] == [want]
        assert rig.ex.orders() == {}

    @pytest.mark.parametrize("value,blocked", [
        ("1", True), ("true", True), ("0", True), ("", False), ("  ", False)])
    def test_live_halt_is_read_as_the_15m_service_reads_it(
            self, rig, armed, monkeypatch, value, blocked):
        """Any non-empty value halts there, so it halts here."""
        monkeypatch.setenv(X.HALT_ENV, value)
        assert (rig.ex.arming_refusal(MARKET, T0) == "L5_LIVE_HALT") is blocked

    @pytest.mark.parametrize("value,blocked", [
        ("0", False), ("1", True), ("", True), ("no", True), (None, True)])
    def test_shadow_only_is_released_only_by_zero(self, rig, armed,
                                                  monkeypatch, value, blocked):
        if value is None:
            monkeypatch.delenv(X.SHADOW_ONLY_ENV)
        else:
            monkeypatch.setenv(X.SHADOW_ONLY_ENV, value)
        assert (rig.ex.arming_refusal(MARKET, T0) == "L5_SHADOW_ONLY") is blocked

    def test_not_after_holds_to_the_second(self, rig, armed):
        at = dt.datetime(2026, 11, 11, 12, 5, 0, tzinfo=UTC)
        assert rig.ex.arming_refusal(MARKET, at) is None
        late = at + dt.timedelta(seconds=1)
        assert rig.ex.arming_refusal(MARKET, late) == "NOT_AFTER_PASSED"
        assert rig.ex.arming_refusal(MARKET, late.timestamp()) == "NOT_AFTER_PASSED"
        assert rig.ex.arming_refusal(MARKET, "now") == "NOW_UNREADABLE"

    def test_a_transport_that_is_not_armed_sends_nothing(self, rig, armed):
        rig.venue.armed = False
        rep = rig.step()
        assert rig.venue.sent == []
        assert rep["arming_refusal"] == "TRANSPORT_NOT_ARMED"
        rig.venue.armed = "yes"                   # truthy is not True
        assert rig.ex.arming_refusal(MARKET, T0) == "TRANSPORT_NOT_ARMED"

    def test_no_transport_sends_nothing(self, tmp_path, armed):
        r = Rig(tmp_path, venue=None)
        rep = r.step()
        assert rep["arming_refusal"] == "TRANSPORT_MISSING"
        assert [w["blocked_by"] for w in rep["would_submit"]] == [
            "TRANSPORT_MISSING"]

    @pytest.mark.parametrize("cap", [None, "", "lots", "-1", "nan"])
    def test_account_cap_unset_or_unreadable_refuses(self, rig, armed,
                                                     monkeypatch, cap):
        if cap is None:
            monkeypatch.delenv(X.ACCOUNT_CAP_ENV)
        else:
            monkeypatch.setenv(X.ACCOUNT_CAP_ENV, cap)
        rep = rig.step()
        assert rig.venue.sent == [] and rep["placed"] == []
        assert reasons(rep) == ["ACCOUNT_CAP_UNSET_OR_UNREADABLE"]
        assert rig.ex.orders() == {}


# ======================================================================
# 1. ARMED: ONE POST-ONLY MAKER ORDER, WRITTEN DOWN FIRST
# ======================================================================
class TestArmedPlacement:

    def test_positive_ev_places_one_post_only_maker_on_the_5m_market(
            self, rig, armed):
        rep = opened(rig)
        assert rep["action"] == C.OPEN_FAVORITE and rep["refused"] == []
        assert rep["would_submit"] == [] and rep["arming_refusal"] is None
        (intent,) = rig.venue.sent
        assert intent["client_order_id"].startswith(X.COID_PREFIX)
        assert not intent["client_order_id"].startswith("l5f-")
        assert intent["market"] == MARKET
        assert intent["post_only"] is True and intent["mode"] == "MAKER"
        assert intent["action"] == "BUY"
        (p,) = rep["placed"]
        assert p["venue_order_id"] == "v1"
        row = rig.ex.orders()[intent["client_order_id"]]
        assert row["state"] == X.LIVE and row["venue_order_id"] == "v1"
        assert (row["side"], D(row["price"]), D(row["qty"])) == FIRST
        assert rig.ex.owns(intent["client_order_id"])
        assert rep["exposure"]["btc5m"]["total_exposure_usd"] == FIRST_COST

    def test_book_mid_alone_places_nothing(self, rig, armed):
        """No independent probability: the controller holds, so do we."""
        rep = rig.ex.step(market=MARKET, t_in_window_s=30.0,
                          book={"bid": "0.60", "ask": "0.62", "age_s": 0.1})
        assert rep["action"] == C.HOLD_OR_SKIP and rig.venue.sent == []

    def test_the_ledger_row_is_on_disk_before_submit_is_called(self, rig, armed):
        seen = {}

        def look(intent):
            row = rig.disk()["orders"][intent["client_order_id"]]
            seen.update(row)

        rig.venue.before_submit = look
        opened(rig)
        assert seen["state"] == X.NEW and seen["venue_order_id"] is None
        assert (seen["side"], D(seen["price"]), D(seen["qty"])) == FIRST

    def test_a_submit_that_raises_leaves_an_uncertain_row_and_halts(
            self, rig, armed):
        rig.venue.submit_error = TimeoutError("no answer")
        rep = rig.step()
        assert rep["placed"] == [] and reasons(rep) == ["SUBMIT_UNCERTAIN"]
        (coid, row), = rig.disk()["orders"].items()
        assert row["state"] == X.UNCERTAIN and row["venue_order_id"] is None
        assert rep["halted_reason"].startswith("ORDER_UNCERTAIN:%s" % coid)
        assert "SUBMIT_RAISED:TimeoutError" in rep["halted_reason"]
        # NO RETRY, even though the venue would now accept it
        rig.venue.submit_error = None
        for _ in range(3):
            rep = rig.tick().step()
            assert rep["placed"] == []
            assert rep["arming_refusal"].startswith("LEDGER_HALTED:")
        assert rig.venue.sent == []
        # and its notional still counts: it may be resting
        assert rig.ex.account_exposure()["own_usd"] == FIRST_COST
        # a restart does not forget
        assert rig.restart().halted_reason.startswith("ORDER_UNCERTAIN")
        assert rig.tick().step()["placed"] == [] and rig.venue.sent == []

    def test_an_ack_without_an_order_id_is_uncertain_until_found(self, rig, armed):
        rig.venue.ack = {"status": "accepted"}
        rep = rig.step()
        assert reasons(rep) == ["SUBMIT_UNCERTAIN"] and rep["halted_reason"]
        assert len(rig.venue.sent) == 1
        # the venue lists it under OUR client order id: a read finds it
        rig.venue.ack = None
        rep = rig.tick().step()
        (row,) = rig.ex.orders().values()
        assert row["state"] == X.LIVE and row["venue_order_id"] == "v1"
        assert rep["halted_reason"] is None and len(rig.venue.sent) == 1

    def test_a_failed_ledger_write_sends_nothing(self, rig, armed, monkeypatch):
        def broken(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(X.os, "replace", broken)
        rep = rig.step()
        assert rig.venue.sent == [] and reasons(rep) == ["LEDGER_WRITE_FAILED"]
        assert rep["halted_reason"].startswith("LEDGER_WRITE_FAILED")
        assert rig.ex.orders() == {}
        rep = rig.tick().step()                      # still failing: still off
        assert rig.venue.sent == []
        assert rep["arming_refusal"].startswith("LEDGER_HALTED:LEDGER_WRITE")
        # once a write succeeds memory and disk agree again, and it resumes
        monkeypatch.undo()
        for k, v in ARMED.items():
            monkeypatch.setenv(k, v)
        rep = rig.tick().step()
        assert rep["halted_reason"] is None and rig.sent() == [FIRST]
        assert rig.disk()["halted_reason"] is None

    def test_a_failed_write_after_a_fill_keeps_the_fill_and_stops_new_orders(
            self, rig, armed, monkeypatch):
        opened(rig)
        rig.venue.fill("v1", "0.05")

        def broken(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(X.os, "replace", broken)
        rep = rig.tick().step()
        assert rep["inventory"]["UP"]["qty"] == D("0.05")    # booked in memory
        assert rep["halted_reason"].startswith("LEDGER_WRITE_FAILED")
        assert rep["placed"] == [] and len(rig.venue.sent) == 1
        # end of window still withdraws our order by its exact id
        rig.ex.end_of_window(MARKET)
        assert rig.venue.cancelled == [("v1", MARKET)]

    def test_the_same_intent_is_never_submitted_twice(self, rig, armed,
                                                      monkeypatch):
        scripted(monkeypatch, place())
        first = rig.step()
        again = rig.tick().step()
        assert len(first["placed"]) == 1 and again["placed"] == []
        assert reasons(again) == ["DUPLICATE_INTENT"]
        assert len(rig.venue.sent) == 1
        # once that order has ENDED the same intent is a new order
        rig.venue.fill("v1")
        third = rig.tick().step()
        assert len(third["placed"]) == 1 and len(rig.venue.sent) == 2
        ids = [s["client_order_id"] for s in rig.venue.sent]
        assert len(set(ids)) == 2

    def test_coid_is_deterministic_and_namespaced(self, rig):
        a = rig.ex.coid(MARKET, 0, "UP", D("0.58"), D("0.17"))
        assert a == rig.ex.coid(MARKET, 0, "UP", "0.580", "0.17")
        assert re.fullmatch(r"b5m-[0-9a-f]{24}", a)
        others = {rig.ex.coid(MARKET, 1, "UP", "0.58", "0.17"),
                  rig.ex.coid(MARKET, 0, "DOWN", "0.58", "0.17"),
                  rig.ex.coid(MARKET, 0, "UP", "0.57", "0.17"),
                  rig.ex.coid(MARKET, 0, "UP", "0.58", "0.18"),
                  rig.ex.coid(OTHER_5M, 0, "UP", "0.58", "0.17")}
        assert a not in others and len(others) == 5

    def test_owns_needs_the_prefix_and_the_ledger(self, rig, armed):
        opened(rig)
        (coid,) = rig.ex.orders()
        assert rig.ex.owns(coid) is True
        assert rig.ex.owns("b5m-" + "0" * 24) is False     # ours by name only
        assert rig.ex.owns("l5f-" + coid[4:]) is False
        assert rig.ex.owns(None) is False and rig.ex.owns("v1") is False


# ======================================================================
# 2. FILLS
# ======================================================================
class TestFills:

    def test_a_partial_fill_is_booked_at_exact_cost_then_the_rest(self, rig, armed):
        opened(rig)
        rig.venue.fill("v1", "0.05")
        got = rig.ex.poll()
        assert [(f["qty"], f["price"], f["cost"]) for f in got["fills"]] == [
            (D("0.05"), D("0.58"), D("0.0290"))]
        inv = rig.ex.inventory(MARKET)
        assert inv["UP"] == {"qty": D("0.05"), "cost": D("0.029")}
        assert inv["DOWN"] == {"qty": D(0), "cost": D(0)}
        (row,) = rig.ex.orders().values()
        assert row["state"] == X.LIVE and D(row["filled_qty"]) == D("0.05")
        # read again: the cumulative is the same, so nothing is booked twice
        assert rig.ex.poll()["fills"] == []
        rig.venue.fill("v1")
        got = rig.ex.poll()
        assert [(f["qty"], f["cost"]) for f in got["fills"]] == [
            (D("0.12"), D("0.0696"))]
        assert rig.ex.inventory(MARKET)["UP"] == {"qty": D("0.17"),
                                                 "cost": FIRST_COST}
        (row,) = rig.ex.orders().values()
        assert row["state"] == X.FILLED and rig.ex.live_orders() == {}

    def test_a_full_fill(self, rig, armed):
        opened(rig)
        rig.venue.fill("v1")
        # 0.58 + 0.43 is over the pair ceiling and entries are closed: hold
        rep = rig.tick().step(bid="0.55", ask="0.57", t=280.0)
        assert rep["action"] == C.HOLD_OR_SKIP and rep["placed"] == []
        assert [(f["side"], f["qty"], f["cost"]) for f in rep["fills"]] == [
            ("UP", D("0.17"), FIRST_COST)]
        assert rep["inventory"]["UP"] == {"qty": D("0.17"), "cost": FIRST_COST}
        exp = rep["exposure"]["btc5m"]
        assert exp["filled_cost_usd"] == FIRST_COST
        assert exp["unmatched_side"] == "UP" and exp["unmatched_qty"] == D("0.17")

    def test_a_hedge_follows_a_fill_when_the_pair_is_within_target(
            self, rig, armed):
        opened(rig)
        rig.venue.fill("v1")
        rep = rig.tick().step(bid="0.63", ask="0.65")
        assert rep["action"] == C.HEDGE_UNDERDOG
        assert rig.sent() == [FIRST, ("DOWN", D("0.35"), D("0.17"))]
        assert rig.venue.sent[1]["post_only"] is True
        # 0.58 + 0.35: inside the 0.98 target
        rig.venue.fill("v2")
        rep = rig.tick().step(bid="0.63", ask="0.65")
        exp = rep["exposure"]["btc5m"]
        assert exp["matched_qty"] == D("0.17")
        assert exp["matched_cost_per_pair"] == D("0.93")
        assert exp["unmatched_qty"] == 0

    def test_no_hedge_when_the_pair_would_cost_more_than_target(self, rig, armed):
        opened(rig)
        rig.venue.fill("v1")
        rep = rig.tick().step(bid="0.55", ask="0.57")   # 0.58 + 0.43 = 1.01
        assert all(s[0] == "UP" for s in rig.sent()), rep

    def test_a_read_error_halts_until_a_clean_read(self, rig, armed):
        opened(rig)
        rig.venue.read_error["v1"] = 1
        rep = rig.tick().step(bid="0.61", ask="0.63")
        (coid, row), = rig.ex.orders().items()
        assert row["state"] == X.UNCERTAIN
        assert rep["halted_reason"] == (
            "ORDER_UNCERTAIN:%s:ORDER_READ_FAILED:ConnectionError" % coid)
        assert rep["placed"] == [] and len(rig.venue.sent) == 1
        assert rep["arming_refusal"].startswith("LEDGER_HALTED:")
        assert rig.disk()["halted_reason"] == rep["halted_reason"]
        rep = rig.tick().step()
        assert rep["halted_reason"] is None and rep["arming_refusal"] is None
        assert rig.ex.orders()[coid]["state"] == X.LIVE

    @pytest.mark.parametrize("row", [
        {"state": "ORDER_STATE_NEW"},                              # no cum
        {"state": "ORDER_STATE_NEW", "cumQuantity": "0.50"},       # > qty
        {"state": "ORDER_STATE_NEW", "cumQuantity": "0",
         "leavesQuantity": "0"},                                   # 1300z shape
        {"state": "ORDER_STATE_NEW", "cumQuantity": "x"},
        "not a row"])
    def test_an_order_row_that_does_not_add_up_is_uncertain(self, rig, armed,
                                                            monkeypatch, row):
        opened(rig)
        monkeypatch.setattr(rig.venue, "order", lambda oid: row)
        got = rig.ex.poll()
        assert got["fills"] == [] and len(got["uncertain"]) == 1
        assert rig.ex.halted_reason.startswith("ORDER_UNCERTAIN")
        assert rig.ex.inventory(MARKET)["UP"]["qty"] == 0


# ======================================================================
# 3. CANCELS: EXACT ID, PROVED BY A RE-READ, NO REPLACEMENT BEFORE THAT
# ======================================================================
class TestCancelAndReplace:

    def flip(self, rig):
        return rig.tick().step(bid="0.38", ask="0.40", p_up="0.30")

    def test_a_favorite_flip_cancels_by_exact_id_and_waits_for_terminal(
            self, rig, armed):
        opened(rig)
        rig.venue.lag["v1"] = 4          # the venue is slow to say CANCELED
        rep = self.flip(rig)
        assert rep["action"] == C.OPEN_FAVORITE
        assert rig.venue.cancelled == [("v1", MARKET)]
        (c,) = rep["cancelled"]
        assert c["venue_order_id"] == "v1" and c["terminal"] is False
        assert reasons(rep) == ["CANCEL_PENDING"]
        assert rig.sent() == [FIRST]                       # no replacement yet
        rep = self.flip(rig)
        assert reasons(rep) == ["CANCEL_PENDING"] and rig.sent() == [FIRST]
        rep = self.flip(rig)
        assert rep["refused"] == []
        assert rig.sent() == [FIRST, ("DOWN", D("0.58"), D("0.17"))]
        # one cancel, by that id, and never repeated while it was pending
        assert rig.venue.cancelled == [("v1", MARKET)]
        states = sorted(r["state"] for r in rig.ex.orders().values())
        assert states == [X.CANCELLED, X.LIVE]
        assert rig.ex.inventory(MARKET)["UP"]["qty"] == 0

    def test_a_prompt_cancel_is_replaced_in_the_same_step(self, rig, armed):
        opened(rig)
        rep = self.flip(rig)
        assert rep["cancelled"][0]["terminal"] is True
        assert rig.sent() == [FIRST, ("DOWN", D("0.58"), D("0.17"))]

    def test_a_fill_that_beats_the_cancel_is_booked_and_nothing_is_doubled(
            self, rig, armed):
        opened(rig)
        rig.venue.fill_on_cancel.add("v1")
        rep = self.flip(rig)
        assert rig.venue.cancelled == [("v1", MARKET)]
        assert [(f["side"], f["qty"], f["cost"]) for f in rep["fills"]] == [
            ("UP", D("0.17"), FIRST_COST)]
        # the decision was made flat; it is not acted on now that we are not
        assert reasons(rep) == ["DECISION_STALE_FILL_DURING_CANCEL"]
        assert rig.sent() == [FIRST]
        assert rep["inventory"]["UP"] == {"qty": D("0.17"), "cost": FIRST_COST}
        assert rep["inventory"]["DOWN"]["qty"] == 0
        (row,) = rig.ex.orders().values()
        assert row["state"] == X.FILLED
        assert rig.ex.poll()["fills"] == []                # booked once

    def test_a_cancel_that_raises_is_sent_again_and_settled_by_the_read(
            self, rig, armed, monkeypatch):
        opened(rig)
        calls = []

        def boom(order_id, market_slug=None):
            calls.append(order_id)
            raise ConnectionError("cancel failed")
        monkeypatch.setattr(rig.venue, "cancel", boom)
        rep = self.flip(rig)
        assert calls == ["v1"] and reasons(rep) == ["CANCEL_PENDING"]
        assert rep["cancelled"][0]["cancel_error"] == "ConnectionError"
        self.flip(rig)
        assert calls == ["v1", "v1"] and rig.sent() == [FIRST]

    def test_a_cancel_for_an_id_we_do_not_own_is_refused(self, rig, armed,
                                                         monkeypatch):
        opened(rig)
        scripted(monkeypatch, {"op": "CANCEL", "id": "l5f-" + "a" * 24},
                 {"op": "CANCEL", "id": "v1"},
                 {"op": "CANCEL", "id": "b5m-" + "a" * 24})
        rep = rig.tick().step()
        assert reasons(rep) == ["NOT_OWNED"] * 3
        assert rig.venue.cancelled == [] and rep["cancelled"] == []


# ======================================================================
# 4. LIMITS
# ======================================================================
class TestLimits:

    def test_the_5m_exposure_cap_refuses(self, rig, armed, monkeypatch):
        scripted(monkeypatch, place(qty="9"))             # 0.58 * 9 = 5.22
        rep = rig.step()
        assert reasons(rep) == ["BTC5M_EXPOSURE_CAP"] and rig.venue.sent == []
        scripted(monkeypatch, place(qty="8.62"))          # 4.9996
        assert len(rig.step()["placed"]) == 1
        scripted(monkeypatch, place(side="DOWN", price="0.35", qty="0.01"))
        assert reasons(rig.step()) == ["BTC5M_EXPOSURE_CAP"]   # 5.0031

    def test_the_account_cap_counts_a_foreign_position_at_a_dollar(
            self, rig, armed, monkeypatch):
        rig.venue.extra_positions = [{
            "marketSlug": M15, "qtyBought": "3", "qtySold": "0",
            "netPositionDecimal": "3"}]
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "3.05")     # 3.00 + 0.0986
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_EXPOSURE_CAP"] and rig.venue.sent == []
        acct = rep["exposure"]["account"]
        assert acct["other_usd"] == D("3.00") and acct["own_usd"] == 0
        assert acct["total_usd"] == D("3.00") and acct["unknown"] is False
        assert "$1.00 per contract" in acct["other_basis"]
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "3.0986")   # exactly at the cap
        assert len(rig.step()["placed"]) == 1

    def test_foreign_orders_and_both_legs_of_a_foreign_pair_are_charged(
            self, rig, armed):
        rig.venue.extra_open = [{"id": "f1", "clientOrderId": "l5f-" + "c" * 24,
                                 "marketSlug": M15, "qty": "0.6",
                                 "remainingQuantity": "0.4"}]
        rig.venue.extra_positions = [{
            "marketSlug": M15, "qtyBought": "0.6", "qtySold": "0.6",
            "netPositionDecimal": "0"}]
        acct = rig.ex.account_exposure()
        assert acct["other_open_order_contracts"] == D("0.4")
        assert acct["other_position_contracts"] == D("1.2")
        assert acct["other_usd"] == D("1.60") and acct["unknown"] is False

    def test_our_own_orders_and_fills_are_counted_once_at_exact_cost(
            self, rig, armed):
        opened(rig)
        acct = rig.ex.account_exposure()
        assert acct["own_usd"] == FIRST_COST and acct["other_usd"] == 0
        rig.venue.fill("v1", "0.05")
        rig.ex.poll()
        acct = rig.ex.account_exposure()
        assert acct["own_filled_cost_usd"] == D("0.029")
        assert acct["own_resting_usd"] == D("0.0696")
        assert acct["total_usd"] == FIRST_COST and acct["other_usd"] == 0

    @pytest.mark.parametrize("fault", ["raise", "open_row", "open_qty",
                                       "pos_row", "pos_qty", "pos_market",
                                       "not_list"])
    def test_an_unknown_account_refuses(self, rig, armed, fault):
        v = rig.venue
        if fault == "raise":
            v.account_error = ConnectionError("down")
        elif fault == "open_row":
            v.extra_open = ["?"]
        elif fault == "open_qty":
            v.extra_open = [{"id": "f1", "marketSlug": M15}]
        elif fault == "pos_row":
            v.extra_positions = [None]
        elif fault == "pos_qty":
            v.extra_positions = [{"marketSlug": M15}]
        elif fault == "pos_market":
            v.extra_positions = [{"qtyBought": "1", "qtySold": "0"}]
        else:
            v.open_orders = lambda: {"orders": []}
        assert rig.ex.account_exposure()["unknown"] is True
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_STATE_UNKNOWN"]
        assert rig.venue.sent == [] and rig.ex.orders() == {}

    def test_max_live_orders(self, tmp_path, armed, monkeypatch):
        r = Rig(tmp_path, max_live_orders=1)
        scripted(monkeypatch, place())
        assert len(r.step()["placed"]) == 1
        scripted(monkeypatch, place(side="DOWN", price="0.35"))
        rep = r.tick().step()
        assert reasons(rep) == ["MAX_LIVE_ORDERS"] and len(r.venue.sent) == 1
        r.venue.fill("v1")                    # the slot frees when it ends
        assert len(r.tick().step()["placed"]) == 1

    def test_a_taker_order_is_refused_even_when_the_controller_allows_it(
            self, tmp_path, armed):
        r = Rig(tmp_path, params=C.Params(taker_allowed=True))
        opened(r)
        r.venue.fill("v1")
        rep = r.tick().step(bid="0.30", ask="0.32", p_up="0.20")
        assert rep["action"] == C.EXIT_OR_FLIP
        (ref,) = rep["refused"]
        assert ref["reason"] == "TAKER_NEVER_SENT"
        assert ref["order"]["post_only"] is False
        assert ref["order"]["intent"] == "SELL"
        assert r.sent() == [FIRST] and rep["would_submit"] == []

    @pytest.mark.parametrize("order,want", [
        (place(post_only=False), "TAKER_NEVER_SENT"),
        (place(post_only=None), "TAKER_NEVER_SENT"),
        (place(post_only="True"), "TAKER_NEVER_SENT"),
        (place(intent="SELL"), "ONLY_MAKER_BUY_SENT"),
        (place(side="YES"), "SIDE_UNREADABLE"),
        (place(price="0.585"), "PRICE_OFF_GRID"),
        (place(price="0.00"), "PRICE_OFF_GRID"),
        (place(price="1.00"), "PRICE_OFF_GRID"),
        (place(qty="0.009"), "QTY_BELOW_MIN"),
        (place(qty="0"), "QTY_BELOW_MIN"),
        (place(qty="0.175"), "QTY_OFF_GRID"),
    ])
    def test_an_order_off_the_grid_or_not_a_maker_buy_is_never_sent(
            self, rig, armed, monkeypatch, order, want):
        scripted(monkeypatch, order)
        rep = rig.step()
        assert reasons(rep) == [want]
        assert rig.venue.sent == [] and rep["would_submit"] == []


# ======================================================================
# 5. RESTART
# ======================================================================
class TestRestart:

    def test_restart_restores_inventory_and_live_orders_without_duplicating(
            self, rig, armed):
        opened(rig)
        before = rig.ex.orders()
        again = rig.restart()
        assert again.orders() == before and again.halted_reason is None
        assert list(again.live_orders()) == list(before)
        # the same decision on the same book: the resting order is kept
        rep = rig.tick().step()
        assert rep["action"] == C.HOLD_OR_SKIP
        assert rep["placed"] == [] and rig.sent() == [FIRST]
        # a partial fill survives a restart too
        rig.venue.fill("v1", "0.05")
        rig.ex.poll()
        before = (rig.ex.orders(), rig.ex.inventory(MARKET))
        again = rig.restart()
        assert (again.orders(), again.inventory(MARKET)) == before
        assert again.inventory(MARKET)["UP"] == {"qty": D("0.05"),
                                                "cost": D("0.029")}
        assert again.poll()["fills"] == []          # not booked a second time
        # and the order still fills into the restored inventory, once
        rig.venue.fill("v1")
        rig.ex.poll()
        assert rig.ex.inventory(MARKET)["UP"] == {"qty": D("0.17"),
                                                 "cost": FIRST_COST}

    def test_a_row_left_new_by_a_crash_is_uncertain_at_restart(self, rig, armed):
        opened(rig)
        doc = rig.disk()
        (coid,) = doc["orders"]
        doc["orders"][coid].update(state=X.NEW, venue_order_id=None)
        with open(rig.path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        again = rig.restart()
        assert again.orders()[coid]["state"] == X.UNCERTAIN
        assert again.halted_reason.startswith("ORDER_UNCERTAIN:%s" % coid)
        assert again.arming_refusal(MARKET, T0).startswith("LEDGER_HALTED:")
        assert rig.disk()["orders"][coid]["state"] == X.UNCERTAIN

    @pytest.mark.parametrize("text", [
        "", "{not json", "[]", '{"version": 2, "orders": {}, "inventory": {}}',
        '{"version": 1, "orders": [], "inventory": {}}',
        '{"version": 1, "orders": {"l5f-x": {}}, "inventory": {}}',
        # inventory that the order rows do not account for
        json.dumps({"version": 1, "orders": {}, "settled": {},
                    "inventory": {MARKET: {"UP": {"qty": "1", "cost": "0.5"}}}}),
    ])
    def test_an_unreadable_ledger_fails_closed_and_is_not_overwritten(
            self, tmp_path, armed, text):
        path = tmp_path / "btc5m" / "ledger.json"
        path.parent.mkdir()
        path.write_text(text)
        r = Rig(tmp_path)
        assert r.ex.halted_reason.startswith("LEDGER_UNREADABLE")
        assert r.ex.arming_refusal(MARKET, T0).startswith("LEDGER_HALTED:")
        rep = r.step()
        assert rep["action"] == "HALTED" and rep["placed"] == []
        assert rep["would_submit"] == [] and r.venue.sent == []
        assert r.ex.end_of_window(MARKET)["cancelled"] == []
        assert r.venue.cancelled == []
        assert path.read_text() == text


# ======================================================================
# 6. END OF WINDOW AND SETTLEMENT
# ======================================================================
class TestEndOfWindowAndSettle:

    def two_of_ours_among_strangers(self, rig):
        """v1 a 15m order, v2 a hand-placed order in OUR market, then ours:
        the entry (partly filled) and its hedge."""
        v = rig.venue
        v.submit({"client_order_id": "l5f-" + "1" * 24, "market": M15,
                  "side": "UP", "price": "0.50", "qty": "0.6"})
        v.submit({"client_order_id": "manual-1", "market": MARKET,
                  "side": "DOWN", "price": "0.30", "qty": "1"})
        rig.step()
        v.fill("v3", "0.05")
        rig.tick().step()                       # hedges the 0.05
        assert rig.sent()[2:] == [FIRST, ("DOWN", D("0.38"), D("0.05"))]
        return rig

    def test_end_of_window_cancels_only_ids_this_ledger_owns(self, rig, armed):
        self.two_of_ours_among_strangers(rig)
        rep = rig.ex.end_of_window(MARKET)
        assert sorted(rig.venue.cancelled) == [("v3", MARKET), ("v4", MARKET)]
        assert sorted(c["venue_order_id"] for c in rep["cancelled"]) == [
            "v3", "v4"]
        assert rep["still_open"] == rep["unresolved"] == rep["filled"] == []
        assert rig.ex.live_orders() == {}
        assert rep["inventory"]["UP"] == {"qty": D("0.05"), "cost": D("0.029")}
        # the strangers are still resting
        assert [o["id"] for o in rig.venue.open_orders()] == ["v1", "v2"]
        # and a second call has nothing left to cancel
        rig.ex.end_of_window(MARKET)
        assert len(rig.venue.cancelled) == 2

    def test_end_of_window_books_a_fill_that_beat_its_cancel(self, rig, armed):
        opened(rig)
        rig.venue.fill_on_cancel.add("v1")
        rep = rig.ex.end_of_window(MARKET)
        assert [f["venue_order_id"] for f in rep["filled"]] == ["v1"]
        assert rep["cancelled"] == [] and rep["fills"][0]["cost"] == FIRST_COST
        assert rep["inventory"]["UP"]["qty"] == D("0.17")

    def test_end_of_window_reports_an_order_the_venue_still_shows(self, rig, armed):
        opened(rig)
        rig.venue.lag["v1"] = 1
        rep = rig.ex.end_of_window(MARKET)
        assert [s["venue_order_id"] for s in rep["still_open"]] == ["v1"]
        assert len(rig.ex.live_orders()) == 1
        rep = rig.ex.end_of_window(MARKET)                 # the re-read lands
        assert [c["venue_order_id"] for c in rep["cancelled"]] == ["v1"]
        assert rig.venue.cancelled == [("v1", MARKET)]

    def test_end_of_window_leaves_other_markets_alone(self, rig, armed):
        opened(rig)
        rep = rig.ex.end_of_window(OTHER_5M)
        assert rep["cancelled"] == [] and rig.venue.cancelled == []

    def test_settle_is_matched_pair_plus_residual_at_exact_cost(self, rig, armed):
        opened(rig)
        rig.venue.fill("v1")
        rig.tick().step(bid="0.63", ask="0.65")           # hedge 0.17 @ 0.35
        rig.venue.fill("v2", "0.10")
        rig.ex.poll()
        early = rig.ex.settle(MARKET, True)
        assert early["provisional"] is True and len(early["unresolved_orders"]) == 1
        assert MARKET not in rig.disk()["settled"]
        rig.ex.end_of_window(MARKET)
        won = rig.ex.settle(MARKET, True)
        assert won["provisional"] is False
        assert won["matched_qty"] == D("0.10") and won["pair_cost"] == D("0.93")
        assert (won["residual_side"], won["residual_qty"]) == ("UP", D("0.07"))
        assert won["cost_total"] == D("0.1336")           # 0.0986 + 0.035
        assert won["redemption_total"] == D("0.17")
        assert won["settlement_pnl"] == D("0.0364")
        lost = rig.ex.settle(MARKET, False)
        assert lost["redemption_total"] == D("0.10")
        assert lost["settlement_pnl"] == D("-0.0336")
        assert "realized only when the venue credits" in won["label"]
        # a settled market stops counting as our exposure
        assert rig.ex.account_exposure()["own_usd"] == 0
        assert MARKET in rig.restart()._ledger["settled"]

    def test_settle_of_a_flat_market_is_zero(self, rig):
        got = rig.ex.settle(MARKET, True)
        assert got["matched_qty"] == got["cost_total"] == got["settlement_pnl"] == 0
        assert got["pair_cost"] is None and got["residual_side"] is None
        with pytest.raises(ValueError):
            rig.ex.settle(MARKET, "yes")


# ======================================================================
# 7. WHAT THE MODULE IS ALLOWED TO BE
# ======================================================================
class TestIsolationOfTheModule:

    def test_the_source_names_no_credentialed_module_and_no_http(self):
        src = inspect.getsource(X)
        for banned in ("l5_live_execution", "l5_pmus_live_runner",
                       "l5_railway", "requests", "urllib"):
            assert banned not in src, banned

    def test_it_imports_only_stdlib_and_the_controller(self):
        src = inspect.getsource(X)
        mods = set(re.findall(r"^(?:from|import)\s+([\w.]+)", src, re.M))
        assert mods == {"__future__", "datetime", "hashlib", "json", "os",
                        "decimal", "typing", "src"}
        assert re.findall(r"^from src import (.+)$", src, re.M) == [
            "l5_btc5m_controller as C"]

    def test_it_calls_only_the_five_transport_methods(self):
        src = inspect.getsource(X)
        direct = set(re.findall(r"\btransport\.(\w+)", src))
        # `t = self.transport` is the one alias, and it only reads
        assert src.count("= self.transport\n") == 1
        alias = set(re.findall(r"\bt\.(\w+)\(", src))
        assert alias == {"open_orders", "positions"}
        assert direct | alias == {"submit", "cancel", "order", "open_orders",
                                  "positions"}
        assert "cancel_all" not in src and "setattr(" not in src
        assert src.count("getattr(self.transport") == 1      # reads `armed`

    def test_the_transport_is_not_mutated(self, rig, armed):
        before = set(vars(rig.venue))
        opened(rig)
        rig.venue.fill("v1", "0.05")
        rig.tick().step()
        rig.ex.end_of_window(MARKET)
        rig.ex.settle(MARKET, True)
        assert set(vars(rig.venue)) == before and rig.venue.armed is True
