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
