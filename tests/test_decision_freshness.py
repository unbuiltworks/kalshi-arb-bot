"""Decision freshness: the pass-scoped terminal cache and the instrumentation.

Safety properties that must hold unchanged are asserted directly, because the
whole point of a latency change is that it must buy speed and nothing else.
"""
from decimal import Decimal as D

import pytest

from src import pmus_native_money_path as MP
from src import pmus_l5_native_engine as E
from src import pmus_order_lifecycle as LC

WORKING = {"classification": LC.WORKING, "state": "ORDER_STATE_WORKING",
           "terminal": False, "filled_qty": "0"}
FILLED = {"classification": LC.FILLED, "state": "ORDER_STATE_FILLED",
          "terminal": True, "filled_qty": "10", "price_own": "0.46",
          "commission": "-0.03"}
UNREADABLE = {"classification": LC.UNKNOWN, "terminal": False,
              "why": "HTTPError: HTTP Error 404: Not Found"}


class CountingVenue:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def order_status(self, oid, requested_qty=None, side=None):
        self.calls.append(oid)
        return dict(self.answer)


def path(venue):
    mp = MP.NativeMoneyPath.__new__(MP.NativeMoneyPath)
    mp.venue = venue
    mp.journal = None
    mp.timing = {}
    mp._cancel_failures = {}
    # observation hooks, as production initialises them: both None
    mp.ws_observer = None
    mp.decision_shadow = None
    mp._resolved_this_pass = set()
    mp._terminal_verdicts = {}
    mp._status_reads = 0
    mp._status_cache_hits = 0
    mp._last_sweep_t = None
    mp._last_sweep_trades = 0
    mp._sweeps_done = 0
    mp._sweeps_skipped = 0
    mp.feed = None
    mp.registry = MP.SessionOrderRegistry()
    mp.release_terminal = lambda *a, **k: None
    return mp


# ---- optimisation 2: never re-read an already-resolved TERMINAL order ----

def test_a_terminal_verdict_is_read_once_per_pass():
    v = CountingVenue(FILLED)
    mp = path(v)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    for _ in range(5):
        mp._status_once("A", requested_qty=D(10), side="up")
    assert v.calls == ["A"], "terminal order was re-read"
    assert mp._status_reads == 1
    assert mp._status_cache_hits == 4


def test_a_WORKING_verdict_is_NEVER_cached():
    """SAFETY, not an optimisation gap.

    A cancel attempted later in the same tick may have been applied at the
    venue even when its transport call failed. Serving a cached WORKING
    across that would make us believe an order is live when it is not.
    """
    v = CountingVenue(WORKING)
    mp = path(v)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    for _ in range(4):
        mp._status_once("A", requested_qty=D(10), side="up")
    assert v.calls == ["A"] * 4
    assert mp._status_cache_hits == 0


def test_an_UNKNOWN_verdict_is_never_cached_either():
    v = CountingVenue(UNREADABLE)
    mp = path(v)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._status_once("A")
    mp._status_once("A")
    assert v.calls == ["A", "A"], "an unreadable order must stay re-readable"


def test_begin_pass_clears_the_cache():
    """The cache is pass-scoped. A new tick asks the venue again."""
    v = CountingVenue(FILLED)
    mp = path(v)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._status_once("A")
    mp.begin_pass()
    mp._status_once("A")
    assert v.calls == ["A", "A"]
    assert mp._terminal_verdicts == {} or "A" in mp._terminal_verdicts


# ---- optimisation 1: fan-out is order-independent -----------------------

def test_fanout_reads_every_order_exactly_once():
    v = CountingVenue(WORKING)
    mp = path(v)
    for oid in "ABCD":
        mp.registry.record(oid, side="up", price=D("0.46"), qty=D(10),
                           kind=E.REST)
    rows = list(mp.registry.nonterminal())
    mp.STATUS_FANOUT = 4
    got = mp._read_statuses(rows)
    assert sorted(got) == list("ABCD")
    assert sorted(v.calls) == list("ABCD")


def test_fanout_and_serial_return_the_same_verdicts():
    rows_for = lambda mp: list(mp.registry.nonterminal())
    a, b = path(CountingVenue(WORKING)), path(CountingVenue(WORKING))
    for mp in (a, b):
        for oid in "ABC":
            mp.registry.record(oid, side="up", price=D("0.46"), qty=D(10),
                               kind=E.REST)
    a.STATUS_FANOUT = 1
    b.STATUS_FANOUT = 3
    assert a._read_statuses(rows_for(a)) == b._read_statuses(rows_for(b))


def test_fanout_defaults_to_one_because_the_bucket_is_shared():
    """Measured: fan-out changes book age by 0 ms.

    The transport's limiter is ONE token bucket at rps=1.5 that sleeps inside
    before(); concurrency does not create tokens. The default must therefore
    not imply a benefit that does not exist.
    """
    assert MP.NativeMoneyPath.STATUS_FANOUT == 1


# ---- the instrumentation ------------------------------------------------

def test_timing_report_names_every_requested_segment():
    mp = path(CountingVenue(WORKING))
    mp.timing = {}
    rep = mp.timing_report()
    for k in ("status_reconciliation_ms", "lock5_ms", "mandate_ms",
              "preview_ms", "post_ms", "decision_ms",
              "book_age_ms_at_decision", "book_age_ms_at_post",
              "status_reads", "status_cache_hits"):
        assert k in rep, k


def test_book_age_is_measured_from_the_books_own_timestamp():
    import time
    mp = path(CountingVenue(WORKING))
    now = time.time()
    mp.timing = {"book_received_ms": (now - 2.0) * 1000.0,
                 "_lock5_wall_ms": now * 1000.0,
                 "_post_wall_ms": (now + 0.08) * 1000.0}
    rep = mp.timing_report()
    assert 1900 <= rep["book_age_ms_at_decision"] <= 2100
    assert 1980 <= rep["book_age_ms_at_post"] <= 2180


def test_missing_timestamps_report_none_rather_than_a_wrong_number():
    mp = path(CountingVenue(WORKING))
    mp.timing = {}
    rep = mp.timing_report()
    assert rep["book_age_ms_at_decision"] is None
    assert rep["lock5_ms"] is None


# ---- frozen LOCK5 is untouched -----------------------------------------

def test_frozen_parameters_are_unchanged():
    assert E.THETA == D("0.96")
    assert E.VARIANT == "B"
    assert float(E.HOLD_S) == 60.0
    assert E.RESIDUAL_CAP_K == D(2)
    eng = E.NativeEngine(clip=D(10))
    assert eng.theta == D("0.96") and eng.variant == "B"
    assert eng.residual_cap_k == D(2)


# ---- the status-sweep gate: skip only REDUNDANT reads -------------------

class FakeFeed:
    def __init__(self, count=0, last_t=None):
        self._wm = {"count": count, "last_t": last_t}

    def trade_watermark(self):
        return dict(self._wm)

    def bump(self, n=1):
        self._wm["count"] += n


def gated(venue, feed=None):
    mp = path(venue)
    mp.feed = feed
    return mp


def test_no_nonterminal_orders_needs_no_sweep():
    mp = gated(CountingVenue(WORKING), FakeFeed())
    assert mp._status_sweep_required()["why"] == "NO_NONTERMINAL_ORDERS"


def test_first_sweep_is_always_required():
    mp = gated(CountingVenue(WORKING), FakeFeed())
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    g = mp._status_sweep_required()
    assert g["required"] is True and g["why"] == "FIRST_SWEEP"


def test_no_trade_since_last_sweep_skips_the_read():
    """A fill of our order IS a trade. No trade, no fill."""
    import time as _t
    f = FakeFeed(count=7)
    mp = gated(CountingVenue(WORKING), f)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._last_sweep_t = _t.time()
    mp._last_sweep_trades = 7
    g = mp._status_sweep_required()
    assert g["required"] is False
    assert g["why"] == "NO_TRADE_SINCE_LAST_SWEEP"


def test_a_trade_forces_the_read():
    import time as _t
    f = FakeFeed(count=7)
    mp = gated(CountingVenue(WORKING), f)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._last_sweep_t = _t.time()
    mp._last_sweep_trades = 7
    f.bump(2)
    g = mp._status_sweep_required()
    assert g["required"] is True and g["why"] == "TAPE_MOVED"
    assert g["trades_since"] == 2


def test_staleness_forces_the_read_even_on_a_silent_tape():
    """A reject or expiry prints no trade, so silence cannot last forever."""
    import time as _t
    f = FakeFeed(count=7)
    mp = gated(CountingVenue(WORKING), f)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._last_sweep_trades = 7
    mp._last_sweep_t = _t.time() - (MP.NativeMoneyPath.STATUS_MAX_STALENESS_S + 1)
    g = mp._status_sweep_required()
    assert g["required"] is True and g["why"] == "STALENESS_BOUND"


def test_an_unknown_order_always_forces_the_read():
    import time as _t
    f = FakeFeed(count=7)
    mp = gated(CountingVenue(WORKING), f)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp.registry.mark_unknown("A", "404")
    mp._last_sweep_t = _t.time()
    mp._last_sweep_trades = 7
    g = mp._status_sweep_required()
    assert g["required"] is True and g["why"] == "ORDER_STATE_UNKNOWN"


def test_no_tape_means_always_read_fail_closed():
    mp = gated(CountingVenue(WORKING), feed=None)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._last_sweep_t = 1.0
    g = mp._status_sweep_required()
    assert g["required"] is True and g["why"] == "NO_TRADE_TAPE"


def test_a_feed_that_raises_fails_closed_to_reading():
    class Bad:
        def trade_watermark(self):
            raise RuntimeError("boom")
    mp = gated(CountingVenue(WORKING), Bad())
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._last_sweep_t = 1.0
    assert mp._status_sweep_required()["why"] == "NO_TRADE_TAPE"


def test_skipping_never_releases_a_reservation_or_books_a_fill():
    """The one way a skip can be wrong is CONSERVATIVE: the order stays live
    with its money charged, and its side stays blocked."""
    import time as _t
    f = FakeFeed(count=7)
    mp = gated(CountingVenue(WORKING), f)
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp._last_sweep_t = _t.time()
    mp._last_sweep_trades = 7
    before = mp.registry.open_reserved
    out = mp.resolve_open_orders()
    assert out["fills_booked"] == [] and out["terminated"] == []
    assert mp.registry.open_reserved == before == D("4.60")
    assert mp.registry.orders["A"]["state"] == MP.LIVE
    assert mp._sweeps_skipped == 1
    assert out["sweep"]["why"] == "NO_TRADE_SINCE_LAST_SWEEP"


def test_the_staleness_bound_is_well_inside_the_variant_b_hold():
    """It must not be able to change when frozen re-quotes."""
    assert MP.NativeMoneyPath.STATUS_MAX_STALENESS_S < float(E.HOLD_S)
    assert MP.NativeMoneyPath.STATUS_MAX_STALENESS_S <= 5.0
