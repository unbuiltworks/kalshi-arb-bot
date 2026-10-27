"""The ninety seconds the engine spent re-reading a fortnight of settlements.

MEASURED 2026-10-27 on the running executor (Railway deployment 4fc4e25b,
service `l5-pmus-live`). Three facts, from the production record:

  * `markets_with_exposure` had grown to **86** BTC15m markets, the oldest
    `cpc-btc-updown-15m-2026-10-12-0030z`. None can retire -- they hold
    unmatched inventory, so `retire_settled_market` refuses -- so they stay
    in the book and the sweep reads every one of them, forever.
  * the network-flow trace for the 2130z window sits at exactly 1.5
    flows/second from 21:31:26 to 21:32:18: 52 unbroken seconds of the
    shared limiter's `rps=1.5` pacing, http_429 at zero throughout.
  * the tick that began at window+48.8s did not reach preflight until
    window+142.8s -- and preflight then PASSED on **attempt 1** in 1.574s,
    against a two-sided websocket book 0.083s old.

So preflight was never the blocker. `sweep_settlements` runs before
`maybe_preflight` and before the decision path, and it spent the first
ninety seconds of the window asleep in the limiter asking the venue to
re-confirm settlements from the twelfth of October. Worse, the pass takes
~55s where SETTLEMENT_POLL_S is 10s, so every market is due again the
instant it ends: a treadmill, not a backlog.

The cap defers reads; it never drops them. These tests pin that -- that the
default is byte-for-byte today's unbounded walk, that a capped sweep still
eventually reads everything, and that the market we are actually waiting on
is never queued behind the ones that will never retire.
"""
from __future__ import annotations

import collections
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

CUR = "cpc-btc-updown-15m-2026-10-27-2130z"


def slug(day, hhmm):
    return "cpc-btc-updown-15m-2026-10-%02d-%sz" % (day, hhmm)


class Venue:
    """Counts settlement reads and never settles anything.

    Never settling is the measured state: these markets are terminal at the
    venue but refuse retirement locally, which is why they accumulate.
    """

    def __init__(self):
        self.market_slug = CUR
        self.armed = True
        self.reads = []

    def settlement(self, s):
        self.reads.append(s)
        return {"market": s, "state": "MARKET_STATE_OPEN",
                "settlement_px": None, "terminal": False,
                "not_terminal_reason": "STATE_NOT_TERMINAL"}


def runner(tmp, venue, markets):
    r = R.Runner.__new__(R.Runner)
    r.root = tmp
    r.store = LX.InventoryStore(os.path.join(tmp, "inv.json"))
    # exposure the only way the sweep can see it: a position per market
    r.store.state.setdefault("positions", {})
    for m in markets:
        r.store.state["positions"]["%s|UP" % m] = str(D("1"))
    r.transport = venue
    r.now_t = 1000.0
    r.last_settlement_poll = {}
    r.stats = collections.Counter()
    r.win = collections.Counter()
    return r


@pytest.fixture()
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture(autouse=True)
def no_cap_env(monkeypatch):
    monkeypatch.delenv(R.SETTLEMENT_SWEEP_CAP_ENV, raising=False)


# ----------------------------------------------------- the default is frozen

def test_the_default_is_unbounded(tmp):
    v = Venue()
    ms = [slug(12, "0030"), slug(14, "2230"), slug(27, "1500"), CUR]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert v.reads == [slug(12, "0030"), slug(14, "2230"), slug(27, "1500")]
    assert r.stats["settlement_sweep_deferred"] == 0


def test_the_default_keeps_the_order_it_has_always_read_in(tmp):
    """`markets_with_exposure` is sorted, so the walk is slug order."""
    v = Venue()
    ms = [slug(27, "1500"), slug(12, "0030"), slug(14, "2230")]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert v.reads == sorted(ms)


def test_the_current_market_is_never_swept(tmp):
    v = Venue()
    r = runner(tmp, v, [CUR])
    r.sweep_settlements()
    assert v.reads == []


@pytest.mark.parametrize("raw", ["", "  ", "0", "-4", "four", "4.5", "None"])
def test_a_typo_means_unbounded_not_some_default_cap(tmp, monkeypatch, raw):
    """A cap a typo can introduce would silently throttle settlement
    detection, which moves the realized total the -$5 halt is measured on."""
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, raw)
    assert R.settlement_sweep_cap() is None
    v = Venue()
    ms = [slug(12, "%02d30" % h) for h in range(8)]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert len(v.reads) == 8


# -------------------------------------------------------------- the cap bites

def test_the_cap_bounds_one_sweep_and_counts_what_it_deferred(
        tmp, monkeypatch):
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")
    v = Venue()
    ms = [slug(12, "%02d30" % h) for h in range(20)]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert len(v.reads) == 4
    assert r.stats["settlement_sweep_read"] == 4
    assert r.stats["settlement_sweep_deferred"] == 16
    assert r.win["settlement_sweep_deferred"] == 16


def test_a_deferred_market_stays_due_rather_than_being_marked_read(
        tmp, monkeypatch):
    """The whole safety property: `last_settlement_poll` may only move for
    a market the venue was actually asked about."""
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")
    v = Venue()
    ms = [slug(12, "%02d30" % h) for h in range(20)]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert sorted(r.last_settlement_poll) == sorted(v.reads)
    assert len(r.last_settlement_poll) == 4


def test_nothing_is_dropped_every_market_is_read_within_a_few_passes(
        tmp, monkeypatch):
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")
    v = Venue()
    ms = [slug(12, "%02d%02d" % (h, m))
          for h in range(5) for m in (0, 15, 30, 45)]       # 20 markets
    r = runner(tmp, v, ms)
    for _ in range(5):
        r.sweep_settlements()
        r.now_t += R.SETTLEMENT_POLL_S + 1.0
    assert sorted(set(v.reads)) == sorted(ms)
    # exactly once each: oldest-first cannot re-read before the rest are done
    assert collections.Counter(v.reads) == collections.Counter(ms)


def test_the_poll_interval_still_throttles_each_market(tmp, monkeypatch):
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")
    v = Venue()
    ms = [slug(12, "%02d30" % h) for h in range(4)]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert len(v.reads) == 4
    r.now_t += R.SETTLEMENT_POLL_S - 0.5          # not due yet
    r.sweep_settlements()
    assert len(v.reads) == 4
    r.now_t += 1.0                                # now due
    r.sweep_settlements()
    assert len(v.reads) == 8


# ------------------------------------- the settlement we are waiting FOR

def test_a_market_never_read_is_read_before_a_fortnight_of_backlog(
        tmp, monkeypatch):
    """The window that just closed must not queue behind the markets that
    will never retire. Its `last_settlement_poll` is 0.0, so oldest-first
    puts it at the front -- which is the point of ordering by read time
    rather than by slug.
    """
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "2")
    v = Venue()
    backlog = [slug(12, "%02d30" % h) for h in range(20)]
    just_closed = slug(27, "2115")
    r = runner(tmp, v, backlog + [just_closed])
    # the backlog has all been read once already; the new window has not
    for m in backlog:
        r.last_settlement_poll[m] = r.now_t - 100.0
    r.now_t += 1.0
    r.sweep_settlements()
    assert just_closed in v.reads
    assert len(v.reads) == 2


def test_under_the_default_that_same_market_is_read_twentieth(tmp):
    """Unbounded order is slug order, so 2115z is behind all of October."""
    v = Venue()
    backlog = [slug(12, "%02d30" % h) for h in range(20)]
    just_closed = slug(27, "2115")
    r = runner(tmp, v, backlog + [just_closed])
    r.sweep_settlements()
    assert v.reads.index(just_closed) == 20


# ------------------------------------------------- the measured shape itself

def test_eighty_six_markets_cost_one_sweep_of_four(tmp, monkeypatch):
    """The production count on 2026-10-27, and what the cap does to it.

    86 markets at the shared limiter's 1.5 rps is 56.7 seconds of
    synchronous sleep ahead of preflight and ahead of the decision path.
    Four is 2.7 seconds. The test asserts the read count, which is what the
    pacing is a linear function of -- it does not assert a latency.
    """
    ms = [slug(d, "%02d%02d" % (h, m))
          for d in (12, 14, 15, 20, 24, 25, 26, 27)
          for h in range(3) for m in (0, 15, 30, 45)][:86]
    assert len(ms) == 86
    v = Venue()
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert len(v.reads) == 86                     # today

    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")
    v2 = Venue()
    r2 = runner(tmp, v2, ms)
    r2.sweep_settlements()
    assert len(v2.reads) == 4
    assert r2.stats["settlement_sweep_deferred"] == 82
    assert len(v.reads) / len(v2.reads) == 21.5


# --------------------------------------------- it cannot change the verdicts

def test_a_read_error_still_leaves_the_market_unretired(tmp, monkeypatch):
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")

    class Boom(Venue):
        def settlement(self, s):
            self.reads.append(s)
            raise RuntimeError("venue unreachable")

    v = Boom()
    ms = [slug(12, "%02d30" % h) for h in range(10)]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert r.stats["settlement_read_error"] == 4
    assert r.store.state.get("retired_markets") in (None, {}, [])


def test_a_capped_sweep_still_retires_what_the_venue_has_settled(
        tmp, monkeypatch):
    monkeypatch.setenv(R.SETTLEMENT_SWEEP_CAP_ENV, "4")

    class Settling(Venue):
        def settlement(self, s):
            self.reads.append(s)
            return {"market": s, "state": "MARKET_STATE_EXPIRED",
                    "settlement_px": D(1), "terminal": True,
                    "not_terminal_reason": None,
                    "resolution": {"source": "VENUE_SETTLEMENT_PX",
                                   "market": s, "market_named": s,
                                   "state": "MARKET_STATE_EXPIRED",
                                   "yes_value": "1"}}

    v = Settling()
    ms = [slug(12, "%02d30" % h) for h in range(10)]
    r = runner(tmp, v, ms)
    r.sweep_settlements()
    assert len(v.reads) == 4
    # it REACHED retirement for each one it read: either retired or
    # refused by name, never silently skipped
    touched = (len(r.store.state.get("retired_markets") or {})
               + sum(n for k, n in r.stats.items()
                     if k.startswith("retire_refused")))
    assert touched == 4
