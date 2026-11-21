"""A failed public book read is a step with no book, never the end of the
process.

Live window 2026-11-20 14:20Z: the public market-record GET hit its 3 s read
timeout at 14:24:27Z, the TimeoutError rose out of `trade()` and `main`, and
the process exited 33 s before the window closed -- skipping `close()` and
leaving ARMED on disk.
"""
import socket
import urllib.error
from decimal import Decimal as D

import pytest

from src import l5_btc5m_runner as RUN
from tests.test_l5_btc5m_runner import M, Rig

GOOD = {"bid": D("0.60"), "ask": D("0.62"), "age_s": 0.1}
FAILURES = [TimeoutError("The read operation timed out"),
            socket.timeout("timed out"),
            urllib.error.URLError("unreachable"),
            ConnectionResetError("reset"),
            ValueError("not json")]


def failing_between(lo, hi, exc):
    def book(t):
        if lo <= t < hi:
            raise exc
        return dict(GOOD)
    return book


# ------------------------------------------------------------ the wrapper

@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_a_failed_read_is_a_book_with_no_quote(exc):
    def boom(_m):
        raise exc
    b = RUN.read_book(boom, M)
    assert b["bid"] is None and b["ask"] is None and b["age_s"] is None
    assert b["reason"] == "BOOK_READ_FAILED:%s" % type(exc).__name__


def test_a_non_book_answer_is_not_trusted():
    b = RUN.read_book(lambda m: None, M)
    assert b["bid"] is None and b["reason"] == "BOOK_READ_FAILED:NOT_A_BOOK"


def test_a_good_read_passes_through_unchanged():
    assert RUN.read_book(lambda m: dict(GOOD, min_qty="0.01"), M) == dict(
        GOOD, min_qty="0.01")


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_a_stop_request_is_never_swallowed(exc):
    def stop(_m):
        raise exc()
    with pytest.raises(exc):
        RUN.read_book(stop, M)


# ------------------------------------------------- the session, end to end

@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_a_timeout_mid_window_does_not_end_the_session(tmp_path, monkeypatch,
                                                       exc):
    rig = Rig(tmp_path, monkeypatch, book=failing_between(60, 90, exc),
              venue_kw={"fee": "0", "settle_px": "1"})
    rep = rig.session.run()                       # must not raise
    steps = rig.kinds("L5_BTC5M_STEP")
    failed = [s for s in steps if s.get("book_reason")]
    assert failed, "the failing interval produced no step"
    for s in failed:
        assert s["book_reason"] == "BOOK_READ_FAILED:%s" % type(exc).__name__
        assert s["why"] == "BOOK_UNUSABLE"
        assert s["placed"] == []                  # nothing priced blind
        assert s["bid"] is None and s["ask"] is None
    # the window ran to its normal end and closed cleanly
    assert rep["close"]["clean"] is True
    assert rig.ex.live_orders() == {}
    assert rig.venue.armed is False
    assert rig.venue.disarmed_with == "BTC5M_SESSION_CLOSED"


def test_quotes_resting_when_the_book_goes_dark_are_pulled(tmp_path,
                                                           monkeypatch):
    rig = Rig(tmp_path, monkeypatch,
              book=failing_between(60, 90, TimeoutError()),
              venue_kw={"fee": "0", "settle_px": "1"})
    seen = {}
    real = rig.session.book_fn

    def spy(market):
        try:
            return real(market)
        except TimeoutError:
            # what was resting when the read failed
            seen.setdefault("live", dict(rig.ex.live_orders()))
            raise
    rig.session.book_fn = spy
    rig.session.run()
    assert seen["live"], "the scenario needs an order resting at the failure"
    first_failed = [s for s in rig.kinds("L5_BTC5M_STEP")
                    if s.get("book_reason")][0]
    later = [s for s in rig.kinds("L5_BTC5M_STEP")
             if s["utc"] >= first_failed["utc"] and s.get("book_reason")]
    pulled = {c for s in later for c in s["cancelled"]}
    resting = {r["venue_order_id"] for r in seen["live"].values()}
    assert resting <= pulled | {oid for oid, _m in rig.venue.cancelled}


def test_trading_resumes_when_the_book_returns(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch,
              book=failing_between(60, 90, TimeoutError()),
              venue_kw={"fee": "0", "settle_px": "1"})
    rig.session.run()
    steps = rig.kinds("L5_BTC5M_STEP")
    last_failed = max(s["utc"] for s in steps if s.get("book_reason"))
    after = [s for s in steps if s["utc"] > last_failed]
    assert after and all(s.get("book_reason") is None for s in after)
    assert any(s["placed"] for s in after)


def test_a_book_that_never_reads_sends_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch,
              book=failing_between(-1e9, 1e9, TimeoutError()),
              venue_kw={"fee": "0", "settle_px": "1"})
    rep = rig.session.run()
    assert rig.venue.sent == []
    assert rep["close"]["clean"] is True
    assert all(s["why"] == "BOOK_UNUSABLE"
               for s in rig.kinds("L5_BTC5M_STEP"))


def test_the_trade_loop_reads_the_book_only_through_the_guard():
    import inspect
    src = inspect.getsource(RUN.Session.trade)
    assert "read_book(self.book_fn, self.market)" in src
    assert "self.book_fn(" not in src
