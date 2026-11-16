"""Rollover must not wait for settlement.

OBSERVED 2026-11-16: `one_window` was synchronous through `settle`,
which polls for up to 1800s. Four lanes entered 1520z, closed clean, and
sat in that poll for the rest of a 23-minute run -- 4 of 16 windows.
Windows are 5 minutes apart, so any settlement slower than the gap
silently skipped windows.

These tests pin the decoupling AND the thing it must not break: a
window that has not settled keeps its cost reserved.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN
from src import l5_shared_reservation as R

NOW = dt.datetime(2026, 11, 16, 18, 0, tzinfo=dt.timezone.utc)


def oid(c):
    return X.COID_PREFIX + (c * 24)[:24]


def row(coid, market, side, price, qty, filled_qty, filled_cost,
        state=X.FILLED):
    return coid, {"client_order_id": coid, "market": market, "side": side,
                  "price": price, "qty": qty, "venue_order_id": "v" + coid[-4:],
                  "state": state, "filled_qty": filled_qty,
                  "filled_cost": filled_cost, "created_at": None,
                  "reason": "STRADDLE_LEG", "slot": 0, "klass": "OPEN",
                  "claim_sid": None, "strategy": "STRADDLE"}


class Venue:
    """Settlement is unavailable until `ready` is set."""

    def __init__(self):
        self.ready = {}
        self.asks = []

    def settlement(self, market):
        self.asks.append(market)
        if market in self.ready:
            return {"terminal": True, "settlement_px": self.ready[market]}
        return {"terminal": False, "settlement_px": None}


@pytest.fixture
def ex(tmp_path, monkeypatch):
    monkeypatch.setenv(R.PATH_ENV, str(tmp_path / "resv.json"))
    return X.Btc5mExecutor(transport=None,
                           ledger_path=str(tmp_path / "ledger.json"))


def hold(market, usd):
    return R.reserve(lane="BTC5M", key="hold-" + market[-6:], usd=usd,
                     now=NOW)


class TestPendingIsDerivedFromTheLedger:

    def test_a_filled_unsettled_market_is_pending(self, ex):
        m = "cpc-btc-updown-5m-2026-11-16-1800z"
        ex._ledger["orders"] = dict([row(oid("a"), m, X.C.UP, "0.08", "2",
                                         "2", "0.16")])
        assert ex.pending_settlement() == [m]

    def test_a_settled_market_is_not_pending(self, ex):
        m = "cpc-btc-updown-5m-2026-11-16-1800z"
        ex._ledger["orders"] = dict([row(oid("a"), m, X.C.UP, "0.08", "2",
                                         "2", "0.16")])
        ex._ledger["settled"][m] = {"up_won": True}
        assert ex.pending_settlement() == []

    def test_an_unfilled_market_is_not_pending(self, ex):
        m = "cpc-btc-updown-5m-2026-11-16-1800z"
        ex._ledger["orders"] = dict([row(oid("a"), m, X.C.UP, "0.08", "2",
                                         "0", "0", state=X.CANCELLED)])
        assert ex.pending_settlement() == []

    def test_several_windows_queue_up_in_order(self, ex):
        rows = []
        for i, t in enumerate(("1800z", "1805z", "1810z", "1815z")):
            m = "cpc-btc-updown-5m-2026-11-16-" + t
            rows.append(row(oid(chr(ord("a") + i)), m, X.C.UP, "0.08", "2",
                            "2", "0.16"))
        ex._ledger["orders"] = dict(rows)
        got = ex.pending_settlement()
        assert len(got) == 4 and got == sorted(got)

    def test_it_survives_a_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv(R.PATH_ENV, str(tmp_path / "r.json"))
        p = str(tmp_path / "ledger.json")
        m = "cpc-btc-updown-5m-2026-11-16-1800z"
        a = X.Btc5mExecutor(transport=None, ledger_path=p)
        a._ledger["orders"] = dict([row(oid("a"), m, X.C.UP, "0.08", "2",
                                        "2", "0.16")])
        a._ledger["inventory"] = {m: {X.C.UP: {"qty": "2", "cost": "0.16"},
                                      X.C.DOWN: {"qty": "0", "cost": "0"}}}
        assert a._save_or_halt() is True
        b = X.Btc5mExecutor(transport=None, ledger_path=p)
        assert b.pending_settlement() == [m]


class TestThirtyMinutesOfUnavailableSettlement:
    """Requirement: settlement unavailable for 30 minutes while the
    executor advances through consecutive five-minute windows."""

    MARKETS = ["cpc-btc-updown-5m-2026-11-16-%s" % t
               for t in ("1800z", "1805z", "1810z", "1815z", "1820z",
                         "1825z", "1830z")]

    def _fill(self, ex, markets):
        rows = []
        for i, m in enumerate(markets):
            rows.append(row(oid(chr(ord("a") + i)), m, X.C.UP, "0.08", "2",
                            "2", "0.16"))
        ex._ledger["orders"] = dict(rows)

    def test_windows_accumulate_as_pending_and_none_is_lost(self, ex):
        v = Venue()
        for n in range(1, len(self.MARKETS) + 1):
            self._fill(ex, self.MARKETS[:n])
            got = RUN.reconcile_pending(ex, v)
            assert got["settled"] == []
            assert len(got["pending"]) == n
        #: seven consecutive five-minute windows -- 30 minutes -- and
        #: every one is still accounted for
        assert len(ex.pending_settlement()) == 7

    def test_each_unsettled_window_keeps_its_cost_reserved(self, ex):
        self._fill(ex, self.MARKETS)
        for m in self.MARKETS:
            assert hold(m, "0.16")["ok"] is True
        before = R.aggregate()["usd"]
        assert before == D("1.12")              # 7 x 0.16
        v = Venue()
        RUN.reconcile_pending(ex, v)
        #: nothing settled, so nothing is released
        live = [k for k in ex.pending_settlement()]
        assert len(live) == 7
        assert R.aggregate()["usd"] == before

    def test_the_venue_is_asked_once_per_market_per_pass(self, ex):
        self._fill(ex, self.MARKETS)
        v = Venue()
        RUN.reconcile_pending(ex, v)
        assert sorted(v.asks) == sorted(self.MARKETS)
        assert len(v.asks) == len(set(v.asks))

    def test_when_settlement_finally_arrives_it_is_taken(self, ex):
        self._fill(ex, self.MARKETS)
        v = Venue()
        RUN.reconcile_pending(ex, v)            # 30 minutes of nothing
        v.ready[self.MARKETS[0]] = "1"
        v.ready[self.MARKETS[1]] = "0"
        got = RUN.reconcile_pending(ex, v)
        assert len(got["settled"]) == 2
        assert len(got["pending"]) == 5
        done = {s["market"] for s in got["settled"]}
        assert done == {self.MARKETS[0], self.MARKETS[1]}

    def test_a_settled_window_gives_its_room_back_and_only_then(self, ex):
        self._fill(ex, self.MARKETS[:2])
        R.reserve(lane="BTC5M", key=oid("a"), usd="0.16", now=NOW)
        R.reserve(lane="BTC5M", key=oid("b"), usd="0.16", now=NOW)
        v = Venue()
        RUN.reconcile_pending(ex, v)
        ex.sync_reservation()
        assert R.aggregate()["usd"] == D("0.32")     # neither settled
        v.ready[self.MARKETS[0]] = "1"
        RUN.reconcile_pending(ex, v)
        ex.sync_reservation()
        assert R.aggregate()["usd"] == D("0.16")     # one did


class TestItNeverDoubleCountsOrGuesses:

    M = "cpc-btc-updown-5m-2026-11-16-1800z"

    def _one(self, ex):
        ex._ledger["orders"] = dict([row(oid("a"), self.M, X.C.UP, "0.08",
                                         "2", "2", "0.16")])

    def test_a_duplicate_settlement_event_writes_once(self, ex):
        self._one(ex)
        v = Venue(); v.ready[self.M] = "1"
        first = RUN.reconcile_pending(ex, v)
        assert first["settled"][0]["already"] is False
        #: the same event again -- the market is no longer pending at all
        again = RUN.reconcile_pending(ex, v)
        assert again["settled"] == [] and again["pending"] == []

    def test_a_contradictory_settlement_cannot_reverse_it(self, ex):
        self._one(ex)
        v = Venue(); v.ready[self.M] = "1"
        RUN.reconcile_pending(ex, v)
        #: force a re-attempt with the opposite outcome
        ex._ledger["orders"][oid("a")]["market"] = self.M
        v.ready[self.M] = "0"
        del ex._ledger["settled"][self.M]["up_won"]
        ex._ledger["settled"][self.M]["up_won"] = True
        got = RUN.reconcile_pending(ex, v)
        assert got["settled"] == []              # not pending any more

    @pytest.mark.parametrize("px", ["0.5", "0.99", "", None, "nope"])
    def test_a_non_binary_price_is_not_a_settlement(self, ex, px):
        self._one(ex)
        v = Venue(); v.ready[self.M] = px
        got = RUN.reconcile_pending(ex, v)
        assert got["settled"] == []
        assert got["unreadable"] and self.M in str(got["unreadable"])
        assert ex.pending_settlement() == [self.M]

    def test_a_venue_timeout_leaves_it_pending(self, ex):
        self._one(ex)

        class Boom:
            def settlement(self, market):
                raise TimeoutError("venue timeout")
        got = RUN.reconcile_pending(ex, Boom())
        assert got["settled"] == []
        assert "VENUE_ERROR:TimeoutError" in str(got["pending"])
        assert ex.pending_settlement() == [self.M]

    def test_an_open_order_keeps_the_window_unsettled(self, ex):
        """A partial fill with the rest still working is not over."""
        ex._ledger["orders"] = dict([row(oid("a"), self.M, X.C.UP, "0.08",
                                         "5", "2", "0.16", state=X.NEW)])
        v = Venue(); v.ready[self.M] = "1"
        got = RUN.reconcile_pending(ex, v)
        assert got["settled"] == []
        assert "ORDERS_UNRESOLVED" in str(got["pending"])

    def test_an_unreadable_pending_set_settles_nothing(self):
        class Bad:
            def pending_settlement(self):
                raise RuntimeError("ledger unreadable")
        got = RUN.reconcile_pending(Bad(), Venue())
        assert got["settled"] == [] and got.get("error") == "RuntimeError"
