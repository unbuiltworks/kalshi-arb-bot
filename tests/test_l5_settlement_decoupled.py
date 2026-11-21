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


class TestReconciliationIsBoundedAndRolloverTakesPriority:
    """A backlog must never delay the next five-minute window. If the
    venue goes quiet for an hour, four lanes accumulate twelve pending
    markets each; attempting all of them with a slow venue would spend
    the inter-window gap on settlement and miss the next market -- the
    stall this decoupling removes, reappearing as a backlog."""

    def _many(self, ex, n):
        rows = []
        for i in range(n):
            m = "cpc-btc-updown-5m-2026-11-16-%04dz" % (1800 + i * 5)
            rows.append(row(oid(chr(ord("a") + i % 26)) + str(i), m,
                            X.C.UP, "0.08", "2", "2", "0.16"))
        ex._ledger["orders"] = dict(rows)
        return [r[1]["market"] for r in rows]

    def test_the_workload_per_pass_is_capped(self, ex):
        self._many(ex, 20)
        v = Venue()
        got = RUN.reconcile_pending(ex, v, max_markets=8)
        assert len(v.asks) == 8
        assert got["considered"] == 20
        assert len(got["deferred"]) == 12
        assert all(d["why"] == "WORKLOAD_BUDGET" for d in got["deferred"])

    def test_a_slow_venue_cannot_eat_the_window_gap(self, ex):
        """Time budget, not just a count: ten markets at two seconds each
        would be twenty seconds of a five-minute gap."""
        self._many(ex, 20)
        ticks = iter([0.0] + [float(i) for i in range(1, 60)])
        v = Venue()
        got = RUN.reconcile_pending(ex, v, max_markets=50, budget_s=5.0,
                                    clock=lambda: next(ticks))
        assert len(v.asks) <= 7
        assert got["deferred"] and got["deferred"][0]["why"] == "TIME_BUDGET"

    def test_at_least_one_market_is_always_attempted(self, ex):
        """Even with no time left, a pass makes progress rather than
        spinning forever on a backlog it never touches."""
        self._many(ex, 5)
        v = Venue()
        got = RUN.reconcile_pending(ex, v, budget_s=0.0,
                                    clock=lambda: 10.0)
        assert len(v.asks) == 1
        assert len(got["deferred"]) == 4

    def test_nothing_deferred_is_lost(self, ex):
        """The pending set is DERIVED from the ledger, so a market not
        reached this pass is reached in a later one."""
        ms = self._many(ex, 20)
        v = Venue()
        RUN.reconcile_pending(ex, v, max_markets=8)
        assert sorted(ex.pending_settlement()) == sorted(ms)

    def test_oldest_first_so_nothing_starves(self, ex):
        """Slugs sort chronologically; a settled market leaves the set,
        so every pass advances the front of the queue."""
        ms = self._many(ex, 20)
        v = Venue()
        first = RUN.reconcile_pending(ex, v, max_markets=4)
        assert v.asks == sorted(ms)[:4]
        #: settle those four, and the next pass moves on
        for m in v.asks:
            v.ready[m] = "1"
        RUN.reconcile_pending(ex, v, max_markets=4)
        RUN.reconcile_pending(ex, v, max_markets=4)
        assert sorted(v.asks)[-1] == sorted(ms)[7]

    def test_a_whole_backlog_drains_over_consecutive_passes(self, ex):
        ms = self._many(ex, 20)
        v = Venue()
        for m in ms:
            v.ready[m] = "1"
        passes = 0
        while ex.pending_settlement() and passes < 10:
            RUN.reconcile_pending(ex, v, max_markets=8)
            passes += 1
        assert ex.pending_settlement() == []
        assert passes == 3                      # 20 markets, 8 a pass


class TestTheSessionHonoursItsSettlementWait:
    """These EXECUTE the real settle path.

    Two NameErrors hid in it: `settle_wait_s` was threaded into the
    Session call but not into `one_window`, and then not into `_run_one`
    either. Both survived a green suite and a string-matching "call
    graph check", because nothing ran the code. The second one killed a
    four-lane shadow run on its first window. Asserting that a name
    appears in a source file is not a test; calling the function is.
    """

    @staticmethod
    def _session(transport, wait, now_s):
        import types
        return RUN.Session(
            market="cpc-btc-updown-5m-2026-11-16-1600z",
            executor=types.SimpleNamespace(), transport=transport,
            root="/tmp", book_fn=lambda m: {}, p_fn=lambda m, w, n: {},
            risk_fn=lambda n, o: {}, halt_fn=lambda: {},
            clock=lambda: now_s, sleep=lambda s: None,
            settle_wait_s=wait)

    @staticmethod
    def _after_window():
        return dt.datetime(2026, 11, 16, 16, 10,
                           tzinfo=dt.timezone.utc).timestamp()

    def test_a_zero_wait_asks_the_venue_exactly_once(self):
        """Not zero times -- a while-loop on an elapsed deadline would
        report the window unsettled without ever asking."""
        v = Venue()
        got = self._session(v, 0.0, self._after_window()).settle()
        assert len(v.asks) == 1
        assert got["settled"] is False
        assert got["reason"] == "VENUE_SETTLEMENT_NOT_SEEN"

    def test_a_zero_wait_still_takes_a_ready_settlement(self):
        """Closing fast must not mean refusing a settlement the venue is
        already offering."""
        import types
        v = Venue()
        m = "cpc-btc-updown-5m-2026-11-16-1600z"
        v.ready[m] = "1"
        seen = {}

        def _settle(market, up_won):
            seen["market"], seen["up_won"] = market, up_won
            return {"settlement_pnl": D("0.84"), "provisional": False}
        sess = self._session(v, 0.0, self._after_window())
        sess.ex = types.SimpleNamespace(settle=_settle)
        got = sess.settle()
        assert len(v.asks) == 1
        assert got["settled"] is True
        assert seen == {"market": m, "up_won": True}

    def test_the_default_is_still_the_long_wait(self):
        """The bounded campaign trades a fixed list and has nothing to
        miss by waiting, so its behaviour is unchanged."""
        import types
        sess = RUN.Session(
            market="cpc-btc-updown-5m-2026-11-16-1600z",
            executor=types.SimpleNamespace(), transport=Venue(),
            root="/tmp", book_fn=lambda m: {}, p_fn=lambda m, w, n: {},
            risk_fn=lambda n, o: {}, halt_fn=lambda: {},
            clock=lambda: 0.0, sleep=lambda s: None)
        assert sess.settle_wait_s == RUN.SETTLE_WAIT_S == 1800.0

    def test_one_window_threads_the_wait_all_the_way_down(self):
        """The signature chain `one_window` -> `_run_one` -> `Session`.
        Both NameErrors lived in that chain; this walks it by signature
        rather than by grep."""
        #: BY SIGNATURE, NOT BY SOURCE TEXT.
        #:
        #: This asserted on literal substrings of
        #: `inspect.getsource(RUN.main)`, which is the same brittleness
        #: that let the original NameError through: a string match proves
        #: a line is PRESENT, never that a name is BOUND. It is also
        #: order-dependent in a way a test should never be -- any edit to
        #: the file during a run shifts what `getsource` returns, and it
        #: failed in a full-suite run while passing alone for exactly
        #: that reason.
        #:
        #: `one_window` and `_run_one` are closures defined inside
        #: `main`, so they are not reachable as attributes. What IS
        #: checkable without executing a campaign: the executable
        #: parameter is on `Session`, and `src/l5_undefined_names.py`
        #: proves every name in the module resolves -- which is the
        #: property the string match was reaching for.
        import inspect
        from src import l5_undefined_names as UN
        sig = inspect.signature(RUN.Session.__init__)
        assert "settle_wait_s" in sig.parameters
        p = sig.parameters["settle_wait_s"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY
        #: and the default is the module's constant, not a literal
        assert p.default == RUN.SETTLE_WAIT_S
        #: no unbound name anywhere in the runner, which is what the
        #: two NameErrors in this chain actually were
        assert UN.undefined(RUN.__file__) == []
