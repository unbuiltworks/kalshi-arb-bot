"""The live probe decides against the AUTHENTICATED book, or not at all.

The first version of the one-shot read the book over REST. That was the
wrong source and the evidence says so plainly: `PmusTransport.book()` hits
`pmus_auth.GATEWAY`, and `ReadOnlyTransport.request` attaches
`signed_headers` only when `base == API` -- so those reads left the process
unauthenticated. The gates were all validated against the authenticated
websocket stream, and a decision taken against a different, weaker view is
not the thing that was validated.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import src.l5_live_book_feed as F
import src.pmus_auth as Q1


def feed(clock=None, market="cpc-btc-updown-15m-2026-09-23-1615z"):
    t = {"now": 1000.0}
    f = F.AuthenticatedBookFeed(market, clock=clock or (lambda: t["now"]))
    f._t = t                                   # test handle on the clock
    return f


def book_msg(slug, bid, ask, bid_qty="100", ask_qty="100"):
    return {"market_data": [{"market_slug": slug,
                             "best_bid": bid, "best_ask": ask,
                             "bids": [{"price": bid, "quantity": bid_qty}],
                             "asks": [{"price": ask, "quantity": ask_qty}]}]}


# ------------------------------------------- the REST book was not authenticated

def test_the_rest_book_path_is_unsigned():
    """The evidence for replacing it, asserted rather than asserted-to."""
    src = inspect.getsource(Q1.ReadOnlyTransport.request)
    assert "signed_headers" in src
    # signing is conditional on the API host...
    assert "if base == API" in src or "base == API" in src
    from src import l5_pmus_transport as TP
    book = inspect.getsource(TP.PmusTransport.book)
    # ...and the book is read from the GATEWAY, so it is not signed
    assert "Q1.GATEWAY" in book


def test_the_probe_no_longer_reads_the_book_over_rest():
    tree = ast.parse(pathlib.Path("scripts/l5_pmus_live_oneshot.py").read_text())
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "book"):
            pytest.fail("the probe still reads the book over REST")


# ------------------------------------------------------- it reuses the recorder

def test_the_feed_reuses_the_recorders_own_code():
    """No second feed implementation: auth, frames, guard and fold are its."""
    src = pathlib.Path("src/l5_live_book_feed.py").read_text()
    for name in ("auth_headers", "subscribe_frames", "guard_outbound",
                 "apply_events", "WS_URL"):
        assert "R.%s" % name in src, name


def test_every_outbound_frame_passes_the_recorders_guard():
    src = inspect.getsource(F.AuthenticatedBookFeed._session)
    assert "guard_outbound" in src
    assert src.count("ws.send") == 1, "one send path, and it is guarded"


def test_the_feed_holds_no_order_path():
    tree = ast.parse(pathlib.Path("src/l5_live_book_feed.py").read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Str if hasattr(ast, "Str") else ast.Constant):
            v = getattr(n, "s", getattr(n, "value", None))
            if isinstance(v, str):
                assert "/v1/orders" not in v


# --------------------------------------------------------------- book history

def test_a_folded_frame_becomes_a_book_state(monkeypatch):
    f = feed()
    f.ingest(book_msg(f.market, "0.40", "0.60"))
    last = f.latest()
    assert last["bid"] == "0.40" and last["ask"] == "0.60"
    assert last["status"] == "OK"
    assert last["t"] == 1000.0


def test_history_keeps_each_change_with_its_own_time():
    f = feed()
    f.ingest(book_msg(f.market, "0.40", "0.60"))
    f._t["now"] = 1001.0
    f.ingest(book_msg(f.market, "0.41", "0.61"))
    rows = f.books()
    assert [r["t"] for r in rows] == [1000.0, 1001.0]
    assert [r["bid"] for r in rows] == ["0.40", "0.41"]


def test_an_unchanged_book_does_not_manufacture_a_new_state():
    # otherwise a quiet market would look perpetually fresh
    f = feed()
    f.ingest(book_msg(f.market, "0.40", "0.60"))
    f._t["now"] = 1005.0
    f.ingest(book_msg(f.market, "0.40", "0.60"))
    assert len(f.books()) == 1
    assert f.age_s() == 5.0


def test_a_one_sided_book_says_so():
    f = feed()
    f.ingest(book_msg(f.market, "0.40", None))
    assert f.latest()["status"] == "ONE_SIDED"


# ------------------------------------------------------------------ freshness

def test_freshness_is_bounded_at_the_frozen_rules_own_limit():
    from src import l5_preview_gate as PG
    from src import l5_faithful_translation as FT
    assert F.MAX_AGE_S == PG.BOOK_MAX_AGE_S
    assert F.MAX_AGE_S <= FT.STALE_MAX_S


def test_a_stale_book_is_not_fresh():
    f = feed()
    f.ingest(book_msg(f.market, "0.40", "0.60"))
    assert f.fresh() is True
    f._t["now"] = 1000.0 + F.MAX_AGE_S + 0.01
    assert f.fresh() is False


def test_no_book_at_all_is_not_fresh():
    f = feed()
    assert f.latest() is None
    assert f.age_s() is None
    assert f.fresh() is False


def test_wait_ready_gives_up_rather_than_pretending():
    f = feed()
    t = f._t
    assert f.wait_ready(timeout_s=1.0,
                        sleep=lambda s: t.__setitem__("now", t["now"] + s)) \
        is False


def test_wait_ready_returns_once_a_book_arrives():
    f = feed()
    t = f._t

    def tick(s):
        t["now"] += s
        if t["now"] >= 1000.5:
            f.ingest(book_msg(f.market, "0.40", "0.60"))

    assert f.wait_ready(timeout_s=5.0, sleep=tick) is True


def test_health_names_the_source():
    f = feed()
    f.ingest(book_msg(f.market, "0.40", "0.60"))
    h = f.health()
    assert h["source"] == "AUTHENTICATED_WS"
    assert h["book_states"] == 1 and h["fresh"] is True


# ------------------------------------------------------- the recorder is intact

def test_the_recorder_image_does_not_gain_the_feed_or_the_probe():
    df = pathlib.Path("deploy/pmus_ws_recorder/Dockerfile").read_text()
    copies = [l for l in df.splitlines()
              if l.strip().upper().startswith("COPY ")]
    for banned in ("l5_live_book_feed", "l5_pmus_live_oneshot",
                   "l5_live_authorization"):
        assert not [l for l in copies if banned in l], banned


# ---------------------------------------------- the real _run -> _session path
#
# Probe 01 on cpc-btc-updown-15m-2026-09-24-1900z never armed. The feed
# reported:
#
#   "errors": ["NameError: name 'asyncio' is not defined"]
#
# `_run` imported asyncio inside its own body and `_session` used
# asyncio.wait_for and asyncio.TimeoutError, which that import did not reach.
# Both methods were marked `pragma: no cover` and every feed test above used
# a double that bypassed them, so 20 passing tests could not see it.
#
# These tests drive the actual code path -- _run, asyncio.run, _session, the
# websocket loop -- with a fake transport, far enough to execute the lines
# that referenced the missing name.

import asyncio as _asyncio
import json as _json
import sys
import types


class _FakeWS:
    """A websocket that serves canned frames, then stops the session."""

    def __init__(self, frames, feed):
        self._frames = list(frames)
        self._feed = feed
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if self._frames:
            return _json.dumps(self._frames.pop(0))
        # nothing left: end the loop rather than hanging the test
        self._feed._stop.set()
        return _json.dumps({})


class _FakeConnect:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *a):
        return False


def _install_fake_websockets(monkeypatch, ws):
    mod = types.ModuleType("websockets")
    mod.connect = lambda url, **kw: _FakeConnect(ws)
    monkeypatch.setitem(sys.modules, "websockets", mod)
    return mod


def _credentials(monkeypatch):
    from src import pmus_ws_evidence_recorder as R
    monkeypatch.setenv(R.KEY_ENV, "test-key-id")
    monkeypatch.setenv(R.SECRET_ENV, "dGVzdC1zZWNyZXQtbm90LWEtcmVhbC1rZXktMDAwMA==")
    # the signature itself is proven elsewhere; this test is about the
    # session loop, and the crypto backend is unavailable in this sandbox.
    monkeypatch.setattr(R, "auth_headers",
                        lambda k, s, path=None, now_ms=None: {"X-PM-Access-Key": k})


def test_the_real_session_loop_runs_and_folds_a_book(monkeypatch):
    """_run -> asyncio.run(_session) -> the recv loop, for real.

    This is the test that would have caught probe 01: with asyncio missing
    from _session's scope, the wait_for line raises NameError and no book
    ever arrives.
    """
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-23-1615z")
    ws = _FakeWS([book_msg(f.market, "0.40", "0.60")], f)
    _install_fake_websockets(monkeypatch, ws)
    _credentials(monkeypatch)

    f._run()                      # the real thing, synchronously

    assert f.errors == [], f.errors
    assert f.latest() is not None, "no book arrived through the real loop"
    assert f.latest()["bid"] == "0.40"
    assert f.latest()["ask"] == "0.60"
    assert ws.sent, "the session subscribed"


def test_the_session_subscribes_through_the_recorders_guard(monkeypatch):
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-23-1615z")
    ws = _FakeWS([book_msg(f.market, "0.40", "0.60")], f)
    _install_fake_websockets(monkeypatch, ws)
    _credentials(monkeypatch)

    f._run()

    frames = [_json.loads(s) for s in ws.sent]
    assert frames, "at least one subscribe frame"
    for fr in frames:
        assert set(fr) == {"subscribe"}, fr
        assert fr["subscribe"]["market_slugs"] == [f.market]


def test_asyncio_is_reachable_from_the_session_itself():
    """The defect stated directly: _session's own globals must carry it.

    A function-local import in _run satisfies _run and nothing else. This
    asserts the name resolves where it is actually used.
    """
    import inspect
    src = inspect.getsource(F.AuthenticatedBookFeed._session)
    assert "asyncio." in src, "the session uses asyncio"
    assert "asyncio" in F.AuthenticatedBookFeed._session.__globals__, (
        "asyncio must be importable from _session's module scope, not from "
        "some other function's body")


def test_a_timeout_does_not_end_the_session(monkeypatch):
    """asyncio.TimeoutError is caught and the loop continues.

    The other line that referenced the missing name, so it gets its own test.
    """
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-23-1615z")
    state = {"first": True}

    class TimingOutWS(_FakeWS):
        async def recv(self):
            if state["first"]:
                state["first"] = False
                raise _asyncio.TimeoutError()
            return await super().recv()

    ws = TimingOutWS([book_msg(f.market, "0.41", "0.61")], f)
    _install_fake_websockets(monkeypatch, ws)
    _credentials(monkeypatch)

    f._run()

    assert f.errors == [], f.errors
    assert f.latest()["bid"] == "0.41", "the loop survived the timeout"


def test_an_unparseable_frame_is_skipped_not_fatal(monkeypatch):
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-23-1615z")

    class JunkWS(_FakeWS):
        async def recv(self):
            if self._frames:
                self._frames.pop(0)          # consume it, or the loop never ends
                return "{not json"
            self._feed._stop.set()
            return _json.dumps({})

    ws = JunkWS([{"junk": True}], f)
    _install_fake_websockets(monkeypatch, ws)
    _credentials(monkeypatch)

    f._run()
    assert f.errors == [], f.errors


def test_a_connect_failure_is_recorded_not_raised(monkeypatch):
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-23-1615z")
    mod = types.ModuleType("websockets")

    def boom(url, **kw):
        raise OSError("connection refused")

    mod.connect = boom
    monkeypatch.setitem(sys.modules, "websockets", mod)
    _credentials(monkeypatch)

    f._run()                      # must not raise out of the thread body

    assert f.errors and "OSError" in f.errors[0]
    assert f.connected is False
    assert f.latest() is None
