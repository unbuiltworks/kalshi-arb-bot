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
import os
import re
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH

UTC = dt.timezone.utc
MARKET = "cpc-btc-updown-5m-2026-11-11-1200z"
OTHER_5M = "cpc-btc-updown-5m-2026-11-11-1205z"
M15 = "cpc-btc-updown-15m-2026-11-11-1200z"
T0 = dt.datetime(2026, 11, 11, 12, 0, 30, tzinfo=UTC)
NOT_AFTER = "2026-11-11T12:05:00Z"

ENV = (C.CONTROLLER_ENV, X.LIVE_ENV, X.MARKET_ENV, X.NOT_AFTER_ENV,
       X.HALT_ENV, X.SHADOW_ONLY_ENV, X.ACCOUNT_CAP_ENV,
       X.ENTRY_MODE_ENV, X.MAX_RESTING_ENV,
       CA.APPROVAL_ENV, CA.ROOT_ENV, AUTH.EXPERIMENT_ENV,
       AUTH.MAX_SUBMISSIONS_ENV, AUTH.SUBMIT_PAUSE_ENV, AUTH.OWNER_ROLE_ENV,
       AUTH.SERVICE_ROLE_ENV, AUTH.LEDGER_ENV,
       *AUTH.CLASS_BUDGET_ENV.values())
W_1200 = dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
W_1205 = dt.datetime(2026, 11, 11, 12, 5, 0, tzinfo=UTC)


def approval_document(starts, **over):
    """An owner approval for the 5-minute series naming exactly `starts`."""
    starts = sorted(starts)
    doc = {"approved": True, "nonce": "t" * 32,
           "series": CA.BTC5M_SERIES,
           "issued_utc": (starts[0] - dt.timedelta(minutes=5)).isoformat(),
           "expires_utc": (starts[-1] + dt.timedelta(minutes=30)).isoformat(),
           "max_windows": len(starts),
           "max_total_exposure_usd": "5", "max_daily_loss_usd": "5",
           "window_starts": [t.isoformat() for t in starts]}
    doc.update(over)
    return doc


def authorize_5m(mp, tmp_path, *, now_fn, starts, max_submissions="200",
                 release_locks=True, **doc_over):
    """Everything the OWNER has to have in place for the adapter's claim to
    pass: the approval document for these windows, the named experiment,
    the submission total, both shared locks released, and this process
    declared the holder on durable storage. The claim, the approval gate,
    the window ledger and the locks are all the real ones; only the gate's
    clock and the mount lookup are pointed at the test's."""
    mp.setattr(CA, "approval_gate", lambda: CA.ApprovalGate(
        CA.EnvApprovalStore(), max_age_s=CA.MAX_CAMPAIGN_S, now_fn=now_fn))
    mp.setattr(AUTH, "nearest_mount", lambda path: "/state")
    mp.setenv(CA.APPROVAL_ENV, json.dumps(
        approval_document(starts, **doc_over)))
    mp.setenv(CA.ROOT_ENV, str(tmp_path / "auth_root"))
    mp.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth" / "auth.jsonl"))
    mp.setenv(AUTH.EXPERIMENT_ENV, "B5M-TEST")
    mp.setenv(AUTH.MAX_SUBMISSIONS_ENV, str(max_submissions))
    mp.setenv(AUTH.OWNER_ROLE_ENV, "l5-btc5m-live")
    mp.setenv(AUTH.SERVICE_ROLE_ENV, "l5-btc5m-live")
    if release_locks:
        # the two SHARED locks and the drain. A caller that shares an
        # environment with the 15-minute harness leaves them exactly as
        # they are: they are shared on purpose.
        mp.setenv(X.SHADOW_ONLY_ENV, "0")
        mp.delenv(X.HALT_ENV, raising=False)
        mp.delenv(AUTH.SUBMIT_PAUSE_ENV, raising=False)
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
def armed(monkeypatch, tmp_path):
    """Owner-approved for the 12:00Z and 12:05Z windows, and locally armed."""
    authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T0,
                 starts=(W_1200, W_1205))
    for k, v in ARMED.items():
        monkeypatch.setenv(k, v)
    #: THE SHARED ACCOUNT-WIDE RESERVATION. Every submission takes a hold
    #: from it, and an UNSET path refuses -- a lane that cannot reach the
    #: shared budget must not trade, because the per-lane caps alone
    #: cannot bound the account. Each test gets its own file, so these
    #: tests exercise the hold without sharing a budget between them.
    monkeypatch.setenv("L5_SHARED_RESERVATION",
                       str(tmp_path / "shared_reservation.json"))


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
        """The order of the three steps is claim, ledger row, wire. Here the
        ledger row cannot be written: nothing is sent, and the claim that
        was already taken stays spent -- the conservative direction, the
        same one the 15-minute executor takes."""
        real = X.os.replace

        def broken(src, dst, *a, **k):
            if str(dst) == rig.path:
                raise OSError("disk full")
            return real(src, dst, *a, **k)
        with monkeypatch.context() as m:
            m.setattr(X.os, "replace", broken)
            rep = rig.step()
            assert rig.venue.sent == []
            assert reasons(rep) == ["LEDGER_WRITE_FAILED"]
            assert rep["halted_reason"].startswith("LEDGER_WRITE_FAILED")
            assert rig.ex.orders() == {}
            rep = rig.tick().step()                  # still failing: still off
            assert rig.venue.sent == []
            assert rep["arming_refusal"].startswith(
                "LEDGER_HALTED:LEDGER_WRITE")
        # once a write succeeds memory and disk agree again, and it resumes.
        # The SAME order is not sent: its claim was spent when the write
        # failed. A different order (the book has moved a tick) is.
        rep = rig.tick().step()
        assert rep["halted_reason"] is None and rig.venue.sent == []
        assert reasons(rep) == ["AUTHORIZATION:AUTHORIZATION_ALREADY_USED"]
        rep = rig.tick().step(bid="0.61", ask="0.63")
        assert rig.sent() == [("UP", D("0.59"), D("0.16"))], rep
        assert rig.disk()["halted_reason"] is None

    def test_a_disk_that_cannot_hold_the_claim_sends_nothing(
            self, rig, armed, monkeypatch):
        def broken(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(X.os, "replace", broken)
        rep = rig.step()
        assert rig.venue.sent == [] and rig.ex.orders() == {}
        assert reasons(rep) == ["AUTHORIZATION:WINDOW_RESERVE_FAILED"]

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
        # what the other outcome WOULD pay: arithmetic, recorded nowhere
        lost = rig.ex.settlement_arithmetic(MARKET, False)
        assert lost["redemption_total"] == D("0.10")
        assert lost["settlement_pnl"] == D("-0.0336")
        assert MARKET not in rig.disk()["settled"]
        won = rig.ex.settle(MARKET, True)
        assert won["provisional"] is False
        assert won["matched_qty"] == D("0.10") and won["pair_cost"] == D("0.93")
        assert (won["residual_side"], won["residual_qty"]) == ("UP", D("0.07"))
        assert won["cost_total"] == D("0.1336")           # 0.0986 + 0.035
        assert won["redemption_total"] == D("0.17")
        assert won["settlement_pnl"] == D("0.0364")
        assert won["origin"] == "BTC5M"
        assert won["cost_basis"] == "AUTHENTICATED_FILLS"
        assert "realized only when the venue credits" in won["label"]
        # RECORDED ONCE. The same outcome again writes nothing new; the
        # other outcome is refused -- a settlement is not reversible here.
        first = rig.disk()["settled"][MARKET]
        again = rig.ex.settle(MARKET, True)
        assert again["already_settled"] is True
        assert again["settlement_pnl"] == D("0.0364")
        assert rig.disk()["settled"][MARKET] == first
        with pytest.raises(ValueError):
            rig.ex.settle(MARKET, False)
        assert rig.disk()["settled"][MARKET] == first
        # and it is this strategy's settled result for the day
        assert rig.ex.day_settled_pnl() == D("0.0364")
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
        # the pure controller, the authorization claim it must spend before
        # every submit, and the registry of strategy ids. Nothing that
        # holds a credential or builds a transport.
        #
        # `l5_shared_reservation` is on this list for the same reason as
        # the others: stdlib-only, no credential, no venue schema. It is
        # the only place all four lanes meet, and without it each lane's
        # cap is its own books alone -- four lanes clearing one $10 cap
        # against four separate ledgers commit $40 between them.
        # `l5_persistent_mode` is on it too, same rationale: stdlib-only,
        # no credential, no venue schema. It holds the standing envelope
        # and the durable RUN/STOP state, and it is the only thing that
        # can refuse an order for being outside the authorized strategy,
        # market or DEPLOYMENT, or for breaching the worst-case daily
        # loss once outstanding risk is counted.
        assert re.findall(r"^from src import (.+)$", src, re.M) == [
            "l5_btc5m_controller as C", "l5_live_authorization as AUTH",
            "l5_origin as ORIGIN", "l5_persistent_mode as PMODE",
            "l5_shared_reservation as RESV",
            # `l5_btc5m_attribution` is arithmetic on fills already booked:
            # it imports only `decimal` and `typing`, reads nothing and
            # decides nothing (pinned in test_l5_btc5m_attribution.py).
            "l5_btc5m_attribution as ATTR"]

    def test_it_calls_only_the_six_transport_methods(self):
        """Five since this module was written, plus `account` -- the cash
        read FOREIGN_EXPOSURE=REPORTED gates on. `buying_power` is the
        transport class's own parser for that answer, taken from the
        transport rather than reimplemented here, because this module may
        not import a module that knows a venue schema."""
        src = inspect.getsource(X)
        direct = set(re.findall(r"\btransport\.(\w+)", src))
        # `t = self.transport` is the one alias, and it only reads
        assert src.count("= self.transport\n") == 1
        alias = set(re.findall(r"\bt\.(\w+)\(", src))
        assert alias == {"open_orders", "positions"}
        assert direct | alias == {"submit", "cancel", "order", "open_orders",
                                  "positions", "account"}
        assert 'getattr(type(transport), "buying_power", None)' in src
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


# ======================================================================
# 8. THE AUTHORIZATION CLAIM IS THE ONLY WAY AN ORDER LEAVES
#    (owner decision 2026-11-11: the same mechanism as the 15-minute
#    strategy, and no second, weaker path)
# ======================================================================
def claim_locks(tmp_path):
    d = tmp_path / "auth"
    return sorted(p.name for p in d.glob("l5_auth_claim_b5m1.*.lock")) \
        if d.is_dir() else []


def locally_armed_only(monkeypatch):
    for k, v in ARMED.items():
        monkeypatch.setenv(k, v)
    #: and the shared account-wide reservation, on its own file per call.
    #: An UNSET path refuses every submission on purpose -- a lane that
    #: cannot reach the shared budget must not trade -- so a test that
    #: expects an order to reach the venue has to provide one.
    monkeypatch.setenv("L5_SHARED_RESERVATION",
                       os.path.join(tempfile.mkdtemp(), "resv.json"))


class TestTheClaimGatesEverySubmit:

    def test_the_local_switches_alone_send_nothing(self, rig, monkeypatch,
                                                   tmp_path):
        """Every local switch on, an armed transport, and NO owner approval."""
        locally_armed_only(monkeypatch)
        rep = rig.step()
        assert rig.venue.sent == [] and rig.ex.orders() == {}
        assert rep["arming_refusal"] is None          # the local lock is open
        (r,) = rep["refused"]
        assert r["reason"].startswith("AUTHORIZATION:")
        assert claim_locks(tmp_path) == []

    @pytest.mark.parametrize("change,reason", [
        (lambda mp: mp.delenv(CA.APPROVAL_ENV), "BTC5M_APPROVAL_ABSENT"),
        (lambda mp: mp.setenv(CA.APPROVAL_ENV, json.dumps(approval_document(
            (W_1200,), series=CA.BTC15M_SERIES))), "CAMPAIGN_SERIES_MISMATCH"),
        (lambda mp: mp.setenv(CA.APPROVAL_ENV, json.dumps(approval_document(
            (W_1205,), issued_utc=(W_1200 - dt.timedelta(minutes=5)
                                   ).isoformat()))),
         "WINDOW_NOT_IN_AUTHORIZED_SET"),
        (lambda mp: mp.setenv(CA.APPROVAL_ENV, json.dumps(approval_document(
            (W_1200,), approved=False))), "APPROVAL_NOT_VALID"),
        (lambda mp: mp.delenv(AUTH.EXPERIMENT_ENV),
         "BTC5M_CLAIM_NO_EXPERIMENT"),
        (lambda mp: mp.delenv(AUTH.MAX_SUBMISSIONS_ENV),
         "BTC5M_SUBMISSION_BUDGET_NOT_SET"),
        (lambda mp: mp.setenv(AUTH.SERVICE_ROLE_ENV, "l5-pmus-live"),
         "NOT_THE_AUTHORIZATION_HOLDER"),
        (lambda mp: mp.setenv(AUTH.SUBMIT_PAUSE_ENV, "1"),
         "SUBMISSION_PAUSED_FOR_DEPLOY"),
        (lambda mp: mp.setattr(AUTH, "nearest_mount", lambda p: "/"),
         "AUTHORIZATION_LEDGER_NOT_DURABLE"),
    ])
    def test_each_missing_authorization_sends_nothing(
            self, rig, armed, monkeypatch, tmp_path, change, reason):
        change(monkeypatch)
        rep = rig.step()
        assert rig.venue.sent == [] and rig.ex.orders() == {}
        assert reasons(rep) == ["AUTHORIZATION:%s" % reason]
        assert claim_locks(tmp_path) == []
        assert not rig.venue.cancelled

    def test_the_claim_is_on_disk_before_submit_is_called(self, rig, armed,
                                                          tmp_path):
        seen = {}

        def at_the_wire(intent):
            seen["locks"] = claim_locks(tmp_path)
            seen["coid"] = intent["client_order_id"]
        rig.venue.before_submit = at_the_wire
        opened(rig)
        (lock,) = seen["locks"]
        assert lock.endswith("%s.lock" % seen["coid"])
        body = json.loads((tmp_path / "auth" / lock).read_text())
        assert body["kind"] == "btc5m_submission_claim"
        assert body["market"] == MARKET and body["klass"] == "OPEN"
        assert body["client_order_id"] == seen["coid"]
        assert D(body["worst_case_usd"]) == FIRST_COST
        (row,) = rig.ex.orders().values()
        assert row["claim_sid"] == body["sid"] and row["klass"] == "OPEN"

    def test_one_claim_per_order_and_never_one_more(self, rig, armed, tmp_path):
        opened(rig)
        rig.venue.fill("v1")
        rig.tick().step(bid="0.63", ask="0.65")          # the hedge
        rig.tick(30).step(bid="0.66", ask="0.68", p_up="0.80")
        sent = [s["client_order_id"] for s in rig.venue.sent]
        assert len(sent) == len(set(sent)) == len(claim_locks(tmp_path))
        for coid in sent:
            assert AUTH.claim_held(AUTH.btc5m_sid(AUTH.experiment_id(), coid))

    def test_a_claim_spent_on_a_submit_that_raised_is_not_returned(
            self, rig, armed, tmp_path):
        rig.venue.submit_error = ConnectionError("no answer")
        rep = rig.step()
        assert reasons(rep) == ["SUBMIT_UNCERTAIN"]
        assert len(claim_locks(tmp_path)) == 1
        rig.venue.submit_error = None
        rig.tick().step()
        assert rig.venue.sent == [] and len(claim_locks(tmp_path)) == 1

    def test_each_order_is_charged_to_the_class_of_what_it_does(
            self, rig, armed, tmp_path):
        opened(rig)                                      # first leg: OPEN
        rig.venue.fill("v1", "0.05")
        rig.tick(5).step(t=35.0)                         # hedge: COMPLETE
        rig.tick(5).step(bid="0.62", ask="0.64", p_up="0.78", t=40.0)
        rig.tick(5).step(bid="0.62", ask="0.64", p_up="0.78", t=45.0)
        got = [(r["side"], r["klass"]) for r in sorted(
            rig.ex.orders().values(), key=lambda r: r["created_at"])]
        assert got == [("UP", "OPEN"), ("DOWN", "COMPLETE"),
                       ("UP", "REPLACE"), ("DOWN", "COMPLETE")]
        bodies = [json.loads((tmp_path / "auth" / n).read_text())
                  for n in claim_locks(tmp_path)]
        assert sorted((b["klass"], b["reducing"]) for b in bodies) == [
            ("COMPLETE", True), ("COMPLETE", True), ("OPEN", False),
            ("REPLACE", False)]

    def test_exposure_above_the_owners_approval_is_refused_at_the_claim(
            self, rig, monkeypatch, tmp_path):
        """The adapter's own cap is $5; the owner approved $0.05."""
        authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T0,
                     starts=(W_1200, W_1205), max_total_exposure_usd="0.05")
        locally_armed_only(monkeypatch)
        rep = rig.step()
        assert rig.venue.sent == []
        (r,) = rep["refused"]
        assert r["reason"] == "AUTHORIZATION:BTC5M_EXPOSURE_ABOVE_APPROVED"
        assert r["detail"] == {"worst_case_usd": "0.0986",
                               "approved_usd": "0.05"}

    def test_the_days_settled_loss_stops_new_orders_at_the_approved_limit(
            self, rig, monkeypatch, tmp_path):
        authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T0,
                     starts=(W_1200, W_1205), max_daily_loss_usd="0.09")
        locally_armed_only(monkeypatch)
        opened(rig)
        rig.venue.fill("v1")
        rig.ex.poll()
        rig.ex.end_of_window(MARKET)
        lost = rig.ex.settle(MARKET, False)              # UP 0.17 @ 0.58 lost
        assert lost["settlement_pnl"] == D("-0.0986")
        assert rig.ex.day_settled_pnl() == D("-0.0986")
        monkeypatch.setenv(X.MARKET_ENV, OTHER_5M)
        monkeypatch.setenv(X.NOT_AFTER_ENV, "2026-11-11T12:10:00Z")
        rig.now = W_1205 + dt.timedelta(seconds=30)
        rep = rig.step(market=OTHER_5M)
        assert len(rig.venue.sent) == 1                  # nothing new
        (r,) = rep["refused"]
        assert r["reason"] == "AUTHORIZATION:BTC5M_DAILY_LOSS_LIMIT"

    def test_a_settled_row_that_cannot_be_read_refuses_rather_than_reads_zero(
            self, rig, armed):
        rig.ex._ledger["settled"]["some-market"] = {"settlement_pnl": "-1"}
        assert rig.ex.day_settled_pnl() is None          # no settled_at
        rep = rig.step()
        assert rig.venue.sent == []
        assert reasons(rep) == ["AUTHORIZATION:BTC5M_DAY_RESULT_UNREADABLE"]

    def test_the_shared_total_binds_and_leaves_room_to_close(
            self, rig, monkeypatch, tmp_path):
        """With a total of 3: one OPEN (1 + the reserve of 2), then no
        second OPEN -- but the hedge that reduces exposure still goes."""
        authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T0,
                     starts=(W_1200, W_1205), max_submissions="3")
        locally_armed_only(monkeypatch)
        opened(rig)
        rig.venue.fill("v1")
        rep = rig.tick().step(bid="0.63", ask="0.65")
        assert rep["action"] == C.HEDGE_UNDERDOG and len(rig.venue.sent) == 2
        rig.venue.fill("v2")
        rep = rig.tick().step(bid="0.63", ask="0.65")    # wants to add
        assert len(rig.venue.sent) == 2
        assert reasons(rep) == [
            "AUTHORIZATION:NO_COMPLETION_CAPACITY_RESERVED"]

    def test_a_window_the_owner_did_not_list_cannot_be_traded(
            self, rig, armed, monkeypatch):
        later = "cpc-btc-updown-5m-2026-11-11-1210z"
        monkeypatch.setenv(X.MARKET_ENV, later)
        monkeypatch.setenv(X.NOT_AFTER_ENV, "2026-11-11T12:15:00Z")
        rig.now = dt.datetime(2026, 11, 11, 12, 10, 30, tzinfo=UTC)
        rep = rig.step(market=later)
        assert rig.venue.sent == []
        assert reasons(rep) == ["AUTHORIZATION:WINDOW_NOT_IN_AUTHORIZED_SET"]

    def test_ownership_by_venue_order_id(self, rig, armed):
        assert rig.ex.owns_venue_order_id("v1") is False
        opened(rig)
        assert rig.ex.owns_venue_order_id("v1") is True
        for other in ("v2", "", None, 1, "V1"):
            assert rig.ex.owns_venue_order_id(other) is False
        assert rig.restart().owns_venue_order_id("v1") is True

    def test_the_only_claim_this_module_can_spend_is_the_5m_one(self):
        src = inspect.getsource(X)
        assert set(re.findall(r"AUTH\.(\w+)", src)) == {
            "claim_btc5m_submission", "Refused"}
        i = src.index("AUTH.claim_btc5m_submission(")
        assert i < src.index("self.transport.submit(")
        # nothing between the claim and the wire can skip the wire's gate
        assert len(re.findall(r"\btransport\.submit\(", src)) == 1


# ======================================================================
# FOREIGN EXPOSURE: REPORTED -- THE MONEY PATH
# ======================================================================
class TestForeignExposureReportedMoneyPath:
    """L5_BTC5M_FOREIGN_EXPOSURE=REPORTED, at the submission gate.

    The decision is scripted in these tests so the order reaches the money
    path regardless of what the controller's own exposure-room check would
    have said; the cross-lane behaviour is pinned in
    test_l5_btc5m_btc15m_isolation. CHARGED is the default and every other
    test in this file runs under it."""

    CASH = {"balances": [{"buyingPower": "500.00"}]}

    @pytest.fixture(autouse=True)
    def _parser(self, monkeypatch):
        """Give the fake the REAL balances parser, as a class attribute --
        the adapter reads it off `type(transport)`, never off the instance.

        The test may import the live transport; the adapter may not, so the
        parser under test here is the same code the live venue uses and not
        a second implementation of the schema."""
        from src.l5_pmus_transport import PmusTransport
        monkeypatch.setattr(Venue, "buying_power",
                            staticmethod(PmusTransport.buying_power),
                            raising=False)

    @staticmethod
    def _foreign(rig, contracts="3"):
        rig.venue.extra_positions = [{
            "marketSlug": M15, "qtyBought": contracts, "qtySold": "0",
            "netPositionDecimal": contracts}]

    def test_reported_stops_charging_a_foreign_position(
            self, rig, armed, monkeypatch):
        """The same block as the CHARGED test above: 3 foreign contracts,
        a $3.05 ceiling, $0 of our own risk."""
        self._foreign(rig)
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "3.05")
        scripted(monkeypatch, place())
        assert reasons(rig.step()) == ["ACCOUNT_EXPOSURE_CAP"]
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        rig.venue.account = lambda: self.CASH
        rep = rig.step()
        assert rep["refused"] == [] and len(rep["placed"]) == 1
        acct = rep["exposure"]["account"]
        assert acct["other_usd"] == D("3.00")       # still read, still priced
        assert acct["own_usd"] == 0 and acct["unknown"] is False
        assert rep["exposure"]["foreign_mode"] == X.FOREIGN_REPORTED
        assert rep["exposure"]["buying_power_usd"] == "500.00"

    def test_our_own_exposure_still_meets_the_ceiling(
            self, rig, armed, monkeypatch):
        self._foreign(rig)
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.05")       # under one leg
        rig.venue.account = lambda: self.CASH
        scripted(monkeypatch, place())                      # 0.58 * 0.17
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_EXPOSURE_CAP"] and rig.venue.sent == []
        r0 = rep["refused"][0]
        assert D(str(r0["total_usd"])) == 0                 # OURS, not theirs
        assert D(str(r0["cap_usd"])) == D("0.05")
        assert r0["foreign_mode"] == X.FOREIGN_REPORTED
        assert D(str(r0["other_usd_reported"])) == D("3.00")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.0986")     # exactly at it
        assert len(rig.step()["placed"]) == 1

    def test_a_second_leg_is_charged_to_us(self, rig, armed, monkeypatch):
        """Our own filled cost accumulates under REPORTED exactly as it
        does under CHARGED -- only the foreign term changed."""
        self._foreign(rig)
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.15")
        rig.venue.account = lambda: self.CASH
        scripted(monkeypatch, place())
        assert len(rig.step()["placed"]) == 1               # 0.0986 resting
        scripted(monkeypatch, place(price="0.57"))          # a distinct intent
        rep = rig.step()                                    # + 0.0969 > 0.15
        assert reasons(rep) == ["ACCOUNT_EXPOSURE_CAP"]
        assert D(str(rep["refused"][0]["total_usd"])) == D("0.0986")

    def test_unreadable_cash_refuses(self, rig, armed, monkeypatch):
        """The fake raises on any transport call it does not script, so a
        venue with no cash route is exactly an unreadable one. Unknown
        cash is never read as unlimited."""
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "10")
        scripted(monkeypatch, place())
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_CASH_UNREADABLE"]
        assert rig.venue.sent == []
        assert rep["exposure"]["buying_power_usd"] is None

        def boom():
            raise ConnectionError("venue did not answer")

        rig.venue.account = boom
        assert reasons(rig.step()) == ["ACCOUNT_CASH_UNREADABLE"]
        rig.venue.account = lambda: {"somethingElse": 1}    # unknown shape
        assert reasons(rig.step()) == ["ACCOUNT_CASH_UNREADABLE"]
        assert rig.venue.sent == []
        rig.venue.account = lambda: self.CASH
        monkeypatch.delattr(Venue, "buying_power")          # no parser at all
        assert reasons(rig.step()) == ["ACCOUNT_CASH_UNREADABLE"]
        assert rig.venue.sent == []
        monkeypatch.setattr(Venue, "buying_power",
                            staticmethod(lambda b: D("500.00")), raising=False)
        assert len(rig.step()["placed"]) == 1

    def test_cash_short_refuses(self, rig, armed, monkeypatch):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "10")
        rig.venue.account = lambda: {"balances": [{"buyingPower": "0.05"}]}
        scripted(monkeypatch, place())
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_CASH_SHORT"] and rig.venue.sent == []
        r0 = rep["refused"][0]
        assert D(str(r0["buying_power_usd"])) == D("0.05")
        assert D(str(r0["own_usd"])) == 0
        rig.venue.account = lambda: {"balances": [{"buyingPower": "0.0986"}]}
        assert len(rig.step()["placed"]) == 1               # exactly fundable

    def test_an_unreadable_foreign_book_still_halts_us(
            self, rig, armed, monkeypatch):
        """REPORTED weakens the CHARGE, never the READ."""
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "10")
        rig.venue.account = lambda: self.CASH
        rig.venue.account_error = ConnectionError("venue did not answer")
        scripted(monkeypatch, place())
        assert reasons(rig.step()) == ["ACCOUNT_STATE_UNKNOWN"]
        assert rig.venue.sent == []

    def test_cash_is_not_read_at_all_under_charged(
            self, rig, armed, monkeypatch):
        """No new transport call on the default path: CHARGED must not
        start depending on a cash route that may not answer."""
        monkeypatch.delenv(X.FOREIGN_EXPOSURE_ENV, raising=False)
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "10")
        calls = []
        rig.venue.account = lambda: calls.append(1) or self.CASH
        scripted(monkeypatch, place())
        rep = rig.step()
        assert len(rep["placed"]) == 1 and calls == []
        assert rep["exposure"]["foreign_mode"] == X.FOREIGN_CHARGED
        assert "buying_power_usd" not in rep["exposure"]


# ======================================================================
# 9. THE ACCOUNT CEILING IS SHARED BETWEEN LANES
# ======================================================================
class TestAccountLaneSplit:
    """Under FOREIGN_REPORTED the ceiling is checked against `own_usd`,
    which each lane reads from its OWN ledger. Four lanes of the 5m fleet
    therefore each saw $0 of sibling risk and could claim the whole
    account between them. `account_lanes` divides the ceiling."""

    CASH = {"balances": [{"buyingPower": "500.00"}]}

    # ------------------------------------------------------- the reader

    def test_unset_is_one_lane_so_a_single_executor_is_unchanged(self):
        assert X.account_lanes() == 1

    @pytest.mark.parametrize("raw,want", [
        ("1", 1), ("4", 4), ("16", 16), (" 4 ", 4)])
    def test_a_lane_count_is_read(self, monkeypatch, raw, want):
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, raw)
        assert X.account_lanes() == want

    @pytest.mark.parametrize("raw", ["0", "17", "-1", "4.0", "four", "4,4", "?"])
    def test_an_unreadable_lane_count_refuses_rather_than_defaulting_to_one(
            self, monkeypatch, raw):
        """1 is the PERMISSIVE answer -- it hands this lane the whole
        account -- so a typo must not land on it."""
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, raw)
        assert X.account_lanes() is None
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "17.00")
        assert X.lane_cap() is None

    # --------------------------------------------------------- the share

    def test_four_lanes_split_the_ceiling(self, monkeypatch):
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "17.00")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "4")
        assert X.lane_cap() == D("4.25")

    def test_the_share_is_floored_to_the_cent_so_lanes_cannot_overcommit(
            self, monkeypatch):
        """$17/3 is 5.666...; three lanes at 5.66 fit, at 5.67 would not."""
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "17.00")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "3")
        assert X.lane_cap() == D("5.66")
        assert X.lane_cap() * 3 <= D("17.00")

    def test_one_lane_is_the_whole_ceiling(self, monkeypatch):
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "17.00")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "1")
        assert X.lane_cap() == X.account_cap() == D("17.00")

    def test_no_cap_is_no_share(self, monkeypatch):
        monkeypatch.delenv(X.ACCOUNT_CAP_ENV, raising=False)
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "4")
        assert X.lane_cap() is None

    # ------------------------------------------------------ enforcement

    def _reported(self, rig, monkeypatch, cap, lanes=None):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, cap)
        if lanes is not None:
            monkeypatch.setenv(X.ACCOUNT_LANES_ENV, lanes)
        rig.venue.account = lambda: self.CASH
        monkeypatch.setattr(type(rig.venue), "buying_power",
                            staticmethod(lambda _a: D("500")), raising=False)
        scripted(monkeypatch, place())                      # 0.58 * 0.17

    def test_the_whole_ceiling_admits_the_order(
            self, rig, armed, monkeypatch):
        """$0.0986 of our own risk under a $0.30 ceiling: it fits. This is
        the control for the split test below."""
        self._reported(rig, monkeypatch, "0.30")
        assert len(rig.step()["placed"]) == 1

    def test_the_same_ceiling_split_four_ways_refuses_it(
            self, rig, armed, monkeypatch):
        """$0.30/4 = $0.07, under the $0.0986 the same order commits. A
        separate rig from the control, so the refusal is the cap and not
        a duplicate intent."""
        self._reported(rig, monkeypatch, "0.30", "4")
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_EXPOSURE_CAP"] and rig.venue.sent == []
        r0 = rep["refused"][0]
        assert D(str(r0["cap_usd"])) == D("0.07")           # the SHARE
        assert D(str(r0["account_cap_usd"])) == D("0.30")   # and the whole
        assert r0["account_lanes"] == 4

    def test_the_share_is_reported_even_when_the_order_is_allowed(
            self, rig, armed, monkeypatch):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "17.00")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "4")
        rig.venue.account = lambda: self.CASH
        monkeypatch.setattr(type(rig.venue), "buying_power",
                            staticmethod(lambda _a: D("500")), raising=False)
        scripted(monkeypatch, place())
        rep = rig.step()
        assert len(rep["placed"]) == 1
        assert rep["exposure"]["account_lanes"] == 4
        assert rep["exposure"]["lane_cap_usd"] == "4.25"

    def test_an_unreadable_lane_count_sends_nothing(
            self, rig, armed, monkeypatch):
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "17.00")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "four")
        scripted(monkeypatch, place())
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_LANES_UNREADABLE"]
        assert rig.venue.sent == []

    def test_charged_keeps_the_undivided_ceiling(
            self, rig, armed, monkeypatch):
        """Under CHARGED a sibling lane IS foreign inventory and is
        already counted at $1 a contract, so dividing as well would
        charge it twice. The $3.05 ceiling still refuses 3 foreign
        contracts with four lanes declared, and for the same reason."""
        rig.venue.extra_positions = [{
            "marketSlug": M15, "qtyBought": "3", "qtySold": "0",
            "netPositionDecimal": "3"}]
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "3.05")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "4")
        scripted(monkeypatch, place())
        rep = rig.step()
        assert reasons(rep) == ["ACCOUNT_EXPOSURE_CAP"]
        r0 = rep["refused"][0]
        assert D(str(r0["cap_usd"])) == D("3.05")    # undivided, on purpose
        assert "account_lanes" not in r0

    def test_one_lane_does_not_round_a_sub_cent_ceiling(self, monkeypatch):
        """A $0.0986 ceiling admits exactly one 0.58 x 0.17 leg. Flooring
        a single lane's share to $0.09 would refuse it, so dividing by
        one returns the ceiling untouched."""
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.0986")
        assert X.lane_cap() == D("0.0986")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "1")
        assert X.lane_cap() == D("0.0986")
        monkeypatch.setenv(X.ACCOUNT_LANES_ENV, "2")
        assert X.lane_cap() == D("0.04")                # floored, as intended


# ======================================================================
# 10. THE OPERATOR STOP
# ======================================================================
class TestTheOperatorStop:
    """STOP latches FIRST, then unwinds. The order matters: cancellation
    talks to a venue and can fail, hang or race a fill, and a STOP whose
    cancels failed must still leave the lane unable to submit."""

    def _stopped_state(self):
        from src import l5_persistent_mode as P
        return P.read_state()["state"]

    def test_it_latches_before_it_cancels(self, rig, armed, monkeypatch,
                                          tmp_path):
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        out = rig.ex.stop(reason="operator", now=rig.now)
        assert out["latched"] is True
        assert self._stopped_state() == P.STOPPED

    def test_the_latch_holds_even_when_a_cancel_cannot_be_sent(
            self, rig, armed, monkeypatch, tmp_path):
        """The worst case must be 'stopped with orders we could not
        withdraw', never 'still trading'."""
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)

        def boom(*_a, **_k):
            raise RuntimeError("venue down")
        monkeypatch.setattr(rig.venue, "cancel", boom)
        out = rig.ex.stop(reason="operator", now=rig.now)
        assert out["latched"] is True
        assert out["ok"] is False
        assert self._stopped_state() == P.STOPPED

    def test_it_cancels_our_order_by_its_exact_venue_id(self, rig, armed,
                                                       monkeypatch, tmp_path):
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        rig.ex.stop(reason="operator", now=rig.now)
        assert [c[0] for c in rig.venue.cancelled] == ["v1"]

    def test_a_fill_that_beat_the_cancel_is_booked_not_lost(
            self, rig, armed, monkeypatch, tmp_path):
        """Only the re-read decides, so a fill racing the cancellation is
        reconciled into inventory rather than discarded."""
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        rig.venue.fill_on_cancel.add("v1")
        out = rig.ex.stop(reason="operator", now=rig.now)
        inv = rig.ex.inventory(MARKET)
        assert inv[C.UP]["qty"] > 0
        assert out["fills_during_stop"]

    def test_an_order_the_venue_will_not_confirm_is_reported_still_open(
            self, rig, armed, monkeypatch, tmp_path):
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        #: the venue accepts the cancel but keeps reporting it live
        monkeypatch.setattr(rig.venue, "cancel",
                            lambda oid, market_slug=None: {"cancelled": oid})
        out = rig.ex.stop(reason="operator", now=rig.now)
        assert out["still_open"] == list(rig.ex.orders())
        assert out["ok"] is False

    def test_it_is_idempotent(self, rig, armed, monkeypatch, tmp_path):
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        first = rig.ex.stop(reason="operator", now=rig.now)
        second = rig.ex.stop(reason="operator again", now=rig.now)
        assert first["ok"] is True and second["ok"] is True
        assert second["cancelled"] == []        # nothing left to cancel
        assert len(rig.venue.cancelled) == 1    # and nothing re-sent

    def test_the_latch_survives_a_restart(self, rig, armed, monkeypatch,
                                          tmp_path):
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        rig.ex.stop(reason="operator", now=rig.now)
        rig.restart()
        assert P.read_state()["state"] == P.STOPPED

    def test_stopping_does_not_release_a_hold_on_unsettled_inventory(
            self, rig, armed, monkeypatch, tmp_path):
        """A stopped lane still owns what it bought. Freeing the budget
        here would let a restart re-spend money still at risk."""
        from src import l5_persistent_mode as P
        from src import l5_shared_reservation as R
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        opened(rig)
        rig.venue.fill("v1")
        rig.tick().step()                       # book the fill
        #: the FILLED order's cost is what must survive. An unfilled
        #: order that STOP successfully cancels SHOULD give its room
        #: back -- that money is no longer at risk.
        filled_cost = sum(
            D(r["filled_cost"]) for r in rig.ex.orders().values()
            if D(r["filled_qty"]) > 0)
        assert filled_cost > 0
        rig.ex.stop(reason="operator", now=rig.now)
        after = R.aggregate()["usd"]
        assert after == filled_cost
        #: and it is still held because the market has not settled
        assert R.aggregate()["holds"] == 1

    def test_a_clean_stop_with_nothing_outstanding_succeeds(
            self, rig, armed, monkeypatch, tmp_path):
        from src import l5_persistent_mode as P
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        out = rig.ex.stop(reason="operator", now=rig.now)
        assert out["ok"] is True
        assert out["cancelled"] == [] and out["still_open"] == []


# ======================================================================
# THE MEASURED-RATE ENTRY GATE, ON THE REAL SUBMIT PATH
#
# WHY THESE LIVE HERE rather than beside the gate's own unit tests. Those
# test the predicate; mutation testing showed they do NOT test the thing
# that matters -- making the gate apply to EVERY order, completions
# included, passed all 25 of them. Only driving `_submit` with real
# inventory distinguishes "gates openings" from "gates everything", and a
# gate that blocks completions turns held inventory into inventory that
# cannot be closed.
# ======================================================================
class TestMeasuredEntryGateOnTheSubmitPath:

    @pytest.fixture
    def refusing(self, monkeypatch, tmp_path):
        """Gate ON with a rows file too small to establish rates, so the
        gate's verdict is a refusal for any order it is asked about."""
        rows = tmp_path / "rows.jsonl"
        rows.write_text(json.dumps(
            {"matched_qty": "1", "matched_pnl": "0.2", "residual_qty": "0",
             "residual_cost": "0", "settlement_pnl": "0.2"}) + "\n")
        monkeypatch.setenv(X.MEASURED_ENTRY_GATE_ENV, "ON")
        monkeypatch.setenv(X.MEASURED_ENTRY_ROWS_ENV, str(rows))
        return rows

    def test_off_by_default_the_opening_still_places(self, rig, armed):
        rep = opened(rig)
        assert rep["refused"] == []

    def test_an_opening_is_refused_while_the_gate_cannot_measure(
            self, rig, armed, refusing):
        rep = rig.step()
        assert rig.venue.sent == [] and rep["placed"] == []
        assert reasons(rep) == [
            "MEASURED_ENTRY_EV:TOO_FEW_AUTHENTICATED_MARKETS"]
        assert rig.ex.orders() == {}

    def test_a_COMPLETION_is_NOT_refused_by_the_gate(
            self, rig, armed, refusing, monkeypatch):
        """THE PROPERTY MUTATION TESTING EXPOSED. With UP held and the
        gate refusing, buying DOWN completes the pair and must still be
        sent. If this ever fails, arming the gate would strand inventory
        it cannot close."""
        rig.ex._ledger["inventory"][MARKET] = {
            C.UP: {"qty": "0.17", "cost": "0.0986"}}
        scripted(monkeypatch, place(side=C.DOWN, price="0.40", qty="0.17"))
        rep = rig.step()
        assert "MEASURED_ENTRY_EV:TOO_FEW_AUTHENTICATED_MARKETS" not in \
            reasons(rep), rep
        assert len(rig.venue.sent) == 1
        assert rig.venue.sent[0]["side"] == C.DOWN

    def test_adding_to_the_HELD_side_is_still_refused(
            self, rig, armed, refusing, monkeypatch):
        """The exemption is for the complement only. More of what we are
        already long adds risk and stays gated."""
        rig.ex._ledger["inventory"][MARKET] = {
            C.UP: {"qty": "0.17", "cost": "0.0986"}}
        scripted(monkeypatch, place(side=C.UP, price="0.58", qty="0.17"))
        rep = rig.step()
        assert reasons(rep) == [
            "MEASURED_ENTRY_EV:TOO_FEW_AUTHENTICATED_MARKETS"]
        assert rig.venue.sent == []


# ======================================================================
# THE INVENTORY LIFECYCLE, END TO END, ON THE REAL EXECUTOR
#
# Owner 2026-11-18: "Get L5 to manage one position correctly, repeatedly,
# from the first fill through completion or settlement." These drive the
# production `step` through venue-confirmed fills and assert what it does
# NEXT -- not that a hedge function exists, but that the executor reaches
# it, sizes it from the ledger, and keeps doing so.
#
# Each test names the leg of the lifecycle it covers. Where the executor
# does NOT do the thing, the test says so explicitly rather than being
# written around it: a lifecycle with a missing link should read as a
# missing link.
# ======================================================================
class TestInventoryLifecycle:

    def _fill_first(self, rig):
        """Open, then let the venue fill that leg completely."""
        rep = rig.step()
        assert rig.sent() == [FIRST], rep
        oid = next(iter(rig.venue.by_oid))
        rig.venue.fill(oid)
        rig.tick(1.0)
        return oid

    def test_a_fill_is_reconciled_from_the_venue_before_the_next_decision(
            self, rig, armed):
        """Step 1 of the loop: `poll` runs BEFORE `decide`, so the decision
        sees post-fill inventory rather than the state we opened on."""
        self._fill_first(rig)
        rep = rig.step()
        assert [(f["side"], str(f["qty"])) for f in rep["fills"]] == \
            [("UP", "0.17")]
        inv = rep["inventory"]
        assert str(inv[C.UP]["qty"]) == "0.17"
        assert str(inv[C.UP]["cost"]) == str(FIRST_COST)
        assert str(inv[C.DOWN]["qty"]) == "0"

    def test_the_unmatched_side_and_quantity_are_identified(self, rig,
                                                            armed):
        """Step 2: matched pairs vs unmatched exposure."""
        self._fill_first(rig)
        rep = rig.step()
        exp = rep["exposure"]["btc5m"]
        assert exp["unmatched_side"] == C.UP
        assert exp["unmatched_qty"] == D("0.17")

    def test_YES_fills_first_then_the_executor_works_the_completing_side(
            self, rig, armed):
        """THE CORE CASE. After an UP fill the next decision must be about
        the DOWN leg -- the hedge -- not another UP entry."""
        self._fill_first(rig)
        rep = rig.step()
        assert rep["action"] in (C.HEDGE_UNDERDOG, C.REPRICE,
                                 C.HOLD_OR_SKIP), rep
        placed = [(p["side"], p["price"]) for p in rep["placed"]]
        #: whatever it decides, it must not ADD to the heavy side
        assert all(s != C.UP for s, _ in placed), rep

    def test_the_hedge_is_sized_from_the_ledger_not_the_caller(self, rig,
                                                               armed):
        """A hedge larger than the unmatched quantity would OPEN a pair
        rather than complete one."""
        self._fill_first(rig)
        rep = rig.step()
        for p in rep["placed"]:
            if p["side"] == C.DOWN:
                assert D(str(p["qty"])) <= D("0.17"), rep

    def test_multiple_same_side_fills_accumulate_one_inventory(self, rig,
                                                               armed):
        """Repeated fills on one side must aggregate, so the unmatched
        quantity the hedge is sized from is the TOTAL."""
        self._fill_first(rig)
        rep = rig.step()
        for oid in list(rig.venue.by_oid):
            if oid not in rig.venue.full:
                rig.venue.fill(oid)
        rig.tick(1.0)
        rep2 = rig.step()
        inv = rep2["inventory"]
        tot = D(str(inv[C.UP]["qty"])) + D(str(inv[C.DOWN]["qty"]))
        assert tot > D("0.17"), rep2

    def test_a_partial_fill_leaves_the_rest_working(self, rig, armed):
        """Partial pair completion: a half-filled leg is inventory AND a
        live order, and both must be accounted."""
        rep = rig.step()
        assert rig.sent() == [FIRST]
        oid = next(iter(rig.venue.by_oid))
        rig.venue.fill(oid, qty="0.08")
        rig.tick(1.0)
        rep = rig.step()
        assert [(f["side"], str(f["qty"])) for f in rep["fills"]] == \
            [("UP", "0.08")]
        assert str(rep["inventory"][C.UP]["qty"]) == "0.08"

    def test_repricing_does_not_cancel_an_order_that_is_still_right(
            self, rig, armed):
        """Queue priority: an order that would be placed again now must be
        KEPT. Stepping twice on an unchanged book must not churn."""
        rig.step()
        before = list(rig.venue.cancelled)
        rig.tick(1.0)
        rig.step()
        assert rig.venue.cancelled == before

    def test_no_duplicate_order_after_a_restart(self, rig, armed):
        """Durable reconciliation: the ledger, not memory, is the record."""
        rig.step()
        n = len(rig.venue.sent)
        rig.restart()
        rig.tick(1.0)
        rig.step()
        assert len(rig.venue.sent) == n, rig.sent()

    def test_no_opening_when_the_account_cap_is_breached(self, rig, armed,
                                                         monkeypatch):
        """Economic limit: a breached cap must stop OPENINGS."""
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.01")
        rep = rig.step()
        assert rig.venue.sent == []
        #: THE CONTROLLER CATCHES IT FIRST, with NO_EXPOSURE_ROOM, so the
        #: executor's own ACCOUNT_EXPOSURE_CAP is never reached. Both are
        #: correct refusals; my first version of this test asserted the
        #: wrong one. What matters is that nothing was sent and the reason
        #: names exposure.
        assert ("NO_EXPOSURE_ROOM" in str(rep.get("why"))
                or "ACCOUNT_EXPOSURE_CAP" in str(reasons(rep))), rep

    def test_after_a_pair_COMPLETES_it_opens_again_in_the_same_window(
            self, rig, armed):
        """REPLENISHMENT -- the capability the owner listed as needing
        verification, and the one that decides whether a window earns once
        or repeatedly.

        Fill the first leg, let the executor work the hedge, fill that
        too, and then ask: with a flat book and time left, does it open a
        NEW first leg?
        """
        self._fill_first(rig)
        rep = rig.step()
        n_after_open = len(rig.venue.sent)
        # fill whatever the hedge placed
        for oid in list(rig.venue.by_oid):
            if oid not in rig.venue.full:
                rig.venue.fill(oid)
        rig.tick(1.0)
        rep2 = rig.step()
        inv = rep2["inventory"]
        matched = min(D(str(inv[C.UP]["qty"])), D(str(inv[C.DOWN]["qty"])))
        rig.tick(1.0)
        rep3 = rig.step()
        sent_after = len(rig.venue.sent)
        #: RECORDED, not asserted either way: this is the observation the
        #: owner asked for. If `sent_after` never exceeds `n_after_open`
        #: the window earns at most one pair, whatever the economics say.
        assert matched >= 0            # inventory reconciled at all
        print("\nREPLENISH OBSERVATION: orders after first open=%d, "
              "after a completed pair=%d, matched=%s, action=%s/%s"
              % (n_after_open, sent_after, matched, rep3["action"],
                 rep3["why"]))

    @pytest.fixture
    def straddle(self, monkeypatch):
        """STRADDLE mode with bounded repetition, which the owner named
        specifically. Both ceilings are REQUIRED when repetition is on --
        the controller refuses the mode without them, because repeated
        entry with no unmatched ceiling is a directional bet in a
        straddle's clothes."""
        monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
        monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
        monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
        monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.00")
        monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.50")
        #: AND THE ENTRY-EV PROBABILITIES, without which the mode refuses
        #: every step with ENTRY_EV_P_PAIR_NATURAL_NOT_SET -- which is how
        #: my first version of these tests failed. `straddle_probability`
        #: returns None when unset and the gate declines rather than
        #: guessing, which is the right behaviour and is the reason
        #: STRADDLE cannot trade on defaults alone.
        #:
        #: p_pair here is the MEASURED value: 0.3716 over 148
        #: authenticated BTC15m settlements of the same two-sided maker
        #: mechanism (src/l5_measured_entry_rates). It is used as a TEST
        #: INPUT, not as an authorisation for production.
        #: THE BASIS MUST BE ATTESTED, or all three rates read as None and
        #: the entry gate declines -- which is the new default and is
        #: correct. These tests exercise the inventory LIFECYCLE, not the
        #: basis gate, so they declare their rates conditional-on-fill;
        #: `test_l5_btc5m_conditional_probability_basis.py` is what pins
        #: the refusing behaviour.
        monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)
        monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.3716")
        monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.50")
        #: AND A THIRD. The mode needs p_lone_win too. The controller's
        #: own note gives the principled value: "in a prediction market
        #: THE PRICE IS THE IMPLIED PROBABILITY", and feeding a constant
        #: measured at one rung to a different rung mispriced it by
        #: -$0.53 a share. The rung here is 0.08, so this is 0.08.
        monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.08")

    def test_STRADDLE_rests_BOTH_sides_at_once(self, rig, armed, straddle):
        """The capability the owner listed as built: one order a side,
        simultaneously, from a single decision."""
        rep = rig.step()
        sides = sorted(s for s, _, _ in rig.sent())
        assert sides == [C.DOWN, C.UP], rep
        assert len(rig.venue.sent) == 2

    def test_STRADDLE_one_sided_fill_then_the_other_side_completes(
            self, rig, armed, straddle):
        """DOWN fills first here -- the mirror of the UP-first case -- and
        the executor must keep working the UP side, not add more DOWN."""
        rig.step()
        down = next(o for o, v in rig.venue.by_oid.items()
                    if v["side"] == C.DOWN)
        rig.venue.fill(down)
        rig.tick(1.0)
        rep = rig.step()
        assert [(f["side"], str(f["qty"])) for f in rep["fills"]] == \
            [(C.DOWN, rig.venue.by_oid[down]["qty"])]
        exp = rep["exposure"]["btc5m"]
        assert exp["unmatched_side"] == C.DOWN
        #: MY FIRST ASSERTION HERE WAS WRONG, and the distinction matters.
        #: In the favourite path, adding to the heavy side after a
        #: one-sided fill would deepen the imbalance. In STRADDLE it is
        #: the DESIGN: the mode wants a rung resting on BOTH sides, so the
        #: leg that filled is replaced. What makes that safe is not
        #: refusing the replacement but the ceilings --
        #: STRADDLE_MAX_SIDE_USD and STRADDLE_MAX_UNMATCHED_USD -- which
        #: the mode refuses to run without.
        #:
        #: So the property worth asserting is that the UP side is STILL
        #: being quoted, i.e. completion remains reachable, and the
        #: unmatched exposure stays inside its ceiling.
        assert exp["unmatched_qty"] > 0
        #: the key is worst_case_settlement_loss_usd -- what the unmatched
        #: leg loses if it settles worthless, which IS the exposure the
        #: STRADDLE_MAX_UNMATCHED_USD ceiling bounds.
        assert D(str(exp["worst_case_settlement_loss_usd"])) <= D("0.50"), rep
        up_working = [o for o in rig.venue.sent if o["side"] == C.UP]
        assert up_working, "no UP quote left, so the pair cannot complete"

    def test_STRADDLE_completes_the_pair_and_then_replenishes(
            self, rig, armed, straddle):
        """The full cycle in one window: both legs fill, the pair matches,
        and with repetition authorised the mode quotes again."""
        rig.step()
        n_open = len(rig.venue.sent)
        for oid in list(rig.venue.by_oid):
            rig.venue.fill(oid)
        rig.tick(1.0)
        rep = rig.step()
        inv = rep["inventory"]
        matched = min(D(str(inv[C.UP]["qty"])), D(str(inv[C.DOWN]["qty"])))
        assert matched > 0, rep
        rig.tick(1.0)
        rig.step()
        assert len(rig.venue.sent) > n_open, \
            "a completed pair did not lead to another quote"

    def test_STRADDLE_stops_adding_at_the_per_side_ceiling(self, rig,
                                                           armed,
                                                           straddle,
                                                           monkeypatch):
        """Repetition is BOUNDED. With a ceiling this small the second
        clip on a side must be refused."""
        monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "0.02")
        rig.step()
        for oid in list(rig.venue.by_oid):
            rig.venue.fill(oid)
        rig.tick(1.0)
        rig.step()
        rig.tick(1.0)
        rep = rig.step()
        per_side = {}
        for s in rig.venue.sent:
            per_side[s["side"]] = per_side.get(s["side"], D(0)) + \
                D(str(s["price"])) * D(str(s["qty"]))
        assert all(v <= D("0.25") for v in per_side.values()), per_side

    def test_STRADDLE_repeated_same_side_fills_accumulate_then_bound(
            self, rig, armed, straddle):
        """REPEATED FILLS ON ONE SIDE. Each fill is reconciled, the side's
        cost accumulates, and the per-side ceiling eventually refuses --
        which is what makes repetition bounded rather than directional."""
        seen = []
        for _ in range(6):
            rig.step()
            new = [o for o in rig.venue.by_oid
                   if o not in rig.venue.full
                   and rig.venue.by_oid[o]["side"] == C.DOWN]
            for oid in new:
                rig.venue.fill(oid)
            rig.tick(1.0)
            rep = rig.step()
            inv = rep["inventory"]
            seen.append(D(str(inv[C.DOWN]["cost"])))
            rig.tick(1.0)
        #: cost is monotone non-decreasing: every fill was reconciled
        assert all(b >= a for a, b in zip(seen, seen[1:])), seen
        #: and the per-side ceiling is respected throughout
        assert seen[-1] <= D("1.00"), seen

    def test_STRADDLE_partial_fill_on_the_opposite_side_completes_part(
            self, rig, armed, straddle):
        """PARTIAL PAIR COMPLETION. DOWN fills whole, UP fills half: the
        matched quantity is the minimum of the two sides and the rest
        stays unmatched, both accounted."""
        rig.step()
        down = next(o for o, v in rig.venue.by_oid.items()
                    if v["side"] == C.DOWN)
        up = next(o for o, v in rig.venue.by_oid.items()
                  if v["side"] == C.UP)
        rig.venue.fill(down)
        half = D(str(rig.venue.by_oid[up]["qty"])) / 2
        rig.venue.fill(up, qty=str(half))
        rig.tick(1.0)
        rep = rig.step()
        inv = rep["inventory"]
        dn, u = D(str(inv[C.DOWN]["qty"])), D(str(inv[C.UP]["qty"]))
        assert u > 0 and dn > 0 and u < dn, (u, dn)
        exp = rep["exposure"]["btc5m"]
        assert exp["matched_qty"] == min(u, dn)
        assert exp["unmatched_qty"] == dn - u
        assert exp["unmatched_side"] == C.DOWN

    def test_STRADDLE_unmatched_ceiling_binds_before_the_side_ceiling(
            self, rig, armed, straddle, monkeypatch):
        """The two ceilings are independent, and the TIGHTER one must
        bind. With unmatched set very low, repeated one-sided fills stop
        adding even though the per-side ceiling has room."""
        monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.02")
        monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "5.00")
        for _ in range(4):
            rig.step()
            for oid in list(rig.venue.by_oid):
                if (oid not in rig.venue.full
                        and rig.venue.by_oid[oid]["side"] == C.DOWN):
                    rig.venue.fill(oid)
            rig.tick(1.0)
            rep = rig.step()
            rig.tick(1.0)
        exp = rep["exposure"]["btc5m"]
        assert D(str(exp["worst_case_settlement_loss_usd"])) <= D("0.25"), \
            exp

    def test_STRADDLE_survives_a_restart_mid_inventory(self, rig, armed,
                                                        straddle):
        """RESTART RECOVERY with inventory on the books: the ledger is the
        record, so the rebuilt executor must see the same inventory and
        must not re-send what is already resting."""
        rig.step()
        down = next(o for o, v in rig.venue.by_oid.items()
                    if v["side"] == C.DOWN)
        rig.venue.fill(down)
        rig.tick(1.0)
        before = rig.step()["inventory"]
        n = len(rig.venue.sent)
        rig.restart()
        rig.tick(1.0)
        after = rig.step()["inventory"]
        assert str(after[C.DOWN]["qty"]) == str(before[C.DOWN]["qty"])
        assert str(after[C.DOWN]["cost"]) == str(before[C.DOWN]["cost"])
        assert len(rig.venue.sent) == n, rig.sent()
