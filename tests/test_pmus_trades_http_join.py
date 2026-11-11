"""The join that carries the authenticated trade tape to the evidence ladder.

THE GAP THIS CLOSES. The ladder is PLACED -> RESTABLE -> BOOK_REACHED ->
TRADE_FLOW -> EVIDENCED_FILL, and every stage was already implemented. What
was missing was a NETWORK source carrying the tape: AUTHENTICATED needs the
recorder's state in-process, and STATUS_HTTP carries the authenticated book
with trade AGGREGATES only. So a deployed run stopped at BOOK_REACHED for
want of a join, not for want of a ladder.

These tests hold the join to the one rule that makes the whole ladder worth
anything: UNREADABLE IS NOT EMPTY. An unread tape must stop the ladder, never
clear it, because [] asserts that nobody traded and manufactures a confident
no-fill on exactly the busy markets where it is most wrong.
"""
import ast
import json
import time

import pytest

from src import pmus_l5_surface_runner as RN
from src import pmus_l5_surface_shadow as SS
from src.l5_surface import pmus_prints as PP


# --------------------------------------------------------------------------
# fetch_trades: every unreadable shape is None, never {}
# --------------------------------------------------------------------------

def _patch_fetch(monkeypatch, body):
    monkeypatch.setattr(RN, "fetch_status", lambda url, timeout=10.0: body)


@pytest.mark.parametrize("body,why", [
    (None, "endpoint disabled (404) / unreachable / timeout"),
    ("", "empty body"),
    ("not json at all", "unparseable"),
    ('{"t":"x"}', "no markets key"),
    ('{"markets":[]}', "markets is not a mapping"),
])
def test_an_unreadable_trades_document_is_none_never_empty(monkeypatch, body,
                                                           why):
    _patch_fetch(monkeypatch, body)
    got = RN.fetch_trades("http://x/trades")
    assert got is None, (
        "%s returned %r. {} would tell EVERY market that nobody traded and "
        "clear the whole slate at once." % (why, got))


def test_a_readable_document_carries_rows_and_the_ring_flag(monkeypatch):
    doc = {"markets": {
        "a": {"trades": [{"t": 1.0, "px": "0.4", "qty": "3", "dir": "BUY"}],
              "ring_full": False, "bad_trade_rows": 0},
        "b": {"trades": [], "ring_full": True, "bad_trade_rows": 2},
    }}
    _patch_fetch(monkeypatch, json.dumps(doc) + "\n")
    got = RN.fetch_trades("http://x/trades")
    assert set(got) == {"a", "b"}
    assert len(got["a"]["trades"]) == 1
    assert got["a"]["ring_full"] is False
    assert got["b"]["trades"] == []          # subscribed and genuinely silent
    assert got["b"]["ring_full"] is True


def test_the_newest_ndjson_line_wins(monkeypatch):
    old = {"markets": {"a": {"trades": [], "ring_full": False}}}
    new = {"markets": {"a": {"trades": [{"t": 9.0, "px": "0.5", "qty": "1"}],
                             "ring_full": False}}}
    _patch_fetch(monkeypatch, json.dumps(old) + "\n" + json.dumps(new) + "\n")
    assert len(RN.fetch_trades("http://x/trades")["a"]["trades"]) == 1


# --------------------------------------------------------------------------
# join_trades: absence stays unreadable, presence is honoured
# --------------------------------------------------------------------------

def _served():
    return {"m1": {"bid": "0.40", "ask": "0.44", "bid_qty": "25",
                   "ask_qty": "30", "trades": None},
            "m2": {"bid": "0.30", "ask": "0.34", "bid_qty": "10",
                   "ask_qty": "10", "trades": None}}


def test_a_failed_tape_read_leaves_every_market_unreadable():
    out = RN.join_trades(_served(), None)
    assert [m["trades"] for m in out.values()] == [None, None], (
        "a failed /trades read must leave trades=None everywhere; turning it "
        "into [] would assert the whole slate was silent")


def test_a_market_absent_from_the_tape_stays_unreadable():
    out = RN.join_trades(_served(), {"m1": {"trades": [], "ring_full": False}})
    assert out["m1"]["trades"] == []      # present and genuinely silent
    assert out["m2"]["trades"] is None    # absent -> our view is incomplete


def test_a_non_list_tape_stays_unreadable():
    out = RN.join_trades(_served(), {"m1": {"trades": "oops"}})
    assert out["m1"]["trades"] is None


def test_the_join_carries_both_truncation_flags():
    doc = {"m1": {"trades": [{"t": 1.0, "px": "0.4", "qty": "1"}],
                  "ring_full": True}}
    out = RN.join_trades(_served(), doc)
    assert out["m1"]["ring_full"] is True
    assert out["m1"]["served_cap"] == RN.SERVED_TAIL_CAP == 200


# --------------------------------------------------------------------------
# coverage: counting rows cannot see a capped transport
# --------------------------------------------------------------------------

def _rows(n):
    return [{"t": float(i), "px": "0.4", "qty": "1"} for i in range(n)]


def test_a_wrapped_ring_is_unvouched_even_though_the_tail_is_short():
    # THE HOLE: the recorder serves 200 of a 500 ring, so len() can never
    # reach RING_CAPACITY over HTTP and the length test alone says "complete".
    cov = PP.coverage(_rows(12), subscribed_at=0.0, placed_at=1.0,
                      observed_until=99.0, ring_full=True)
    assert cov["complete"] is None
    assert "ring FULL" in cov["why"]


def test_a_tail_at_the_served_cap_is_unvouched():
    cov = PP.coverage(_rows(200), subscribed_at=0.0, placed_at=1.0,
                      observed_until=99.0, ring_full=False, served_cap=200)
    assert cov["complete"] is None
    assert "capped tail" in cov["why"]


def test_a_short_unwrapped_tail_is_vouched():
    cov = PP.coverage(_rows(12), subscribed_at=0.0, placed_at=1.0,
                      observed_until=99.0, ring_full=False, served_cap=200)
    assert cov["complete"] is True


def test_omitting_the_flags_preserves_the_in_process_behaviour():
    assert PP.coverage(_rows(12), subscribed_at=0.0, placed_at=1.0,
                       observed_until=99.0)["complete"] is True
    assert PP.coverage(_rows(500), subscribed_at=0.0, placed_at=1.0,
                       observed_until=99.0)["complete"] is None


# --------------------------------------------------------------------------
# the ladder itself: does the join actually break through BOOK_REACHED?
# --------------------------------------------------------------------------

def _walk(tape, *, bid_qty="25"):
    """Place at the touch, then re-observe with the tape attached."""
    surf = SS.MarketSurface("demo", SS.spec())
    book = {"bid": "0.40", "ask": "0.44", "bid_qty": bid_qty, "ask_qty": "30"}
    t0 = time.time()
    SS.step(surf, dict(book, trades=None), t0, tte_s=3600.0)   # PLACED
    served = RN.join_trades({"demo": dict(book, trades=None)},
                            {"demo": {"trades": tape, "ring_full": False}}
                            if tape is not None else None)
    SS.step(surf, served["demo"], t0 + 1.0, tte_s=3600.0)
    return surf


def test_without_the_join_the_ladder_stops_at_book_reached():
    surf = _walk(None)
    assert surf.ladder["BUY_YES|BOOK_REACHED"] == 1
    assert surf.ladder.get("BUY_YES|TRADE_FLOW", 0) == 0
    assert surf.tape_unreadable >= 1


def test_with_the_join_a_cleared_queue_reaches_evidenced_fill():
    now = time.time()
    # 30 traded THROUGH 0.40 after placement, against a 25 queue
    tape = [{"t": now + 0.5, "px": "0.40", "qty": "30", "dir": "SELL"}]
    surf = _walk(tape, bid_qty="25")
    assert surf.ladder["BUY_YES|TRADE_FLOW"] == 1, (
        "the join did not reach TRADE_FLOW: %r" % dict(surf.ladder))
    assert surf.ladder.get("BUY_YES|EVIDENCED_FILL", 0) == 1, (
        "flow reached but the fill did not: %r" % dict(surf.ladder))


def test_flow_that_does_not_clear_the_queue_is_not_a_fill():
    now = time.time()
    tape = [{"t": now + 0.5, "px": "0.40", "qty": "3", "dir": "SELL"}]
    surf = _walk(tape, bid_qty="25")
    assert surf.ladder["BUY_YES|TRADE_FLOW"] == 1
    assert surf.ladder.get("BUY_YES|EVIDENCED_FILL", 0) == 0, (
        "3 traded against a queue of 25 is not a fill; reading it as one is "
        "exactly the promotion the ladder exists to refuse")


def test_a_genuinely_empty_tape_is_a_no_fill_not_an_unknown():
    surf = _walk([])
    assert surf.tape_readable >= 1
    assert surf.ladder.get("BUY_YES|TRADE_FLOW", 0) == 0


# --------------------------------------------------------------------------
# isolation: this runner still cannot trade
# --------------------------------------------------------------------------

def test_the_runner_has_no_order_transport():
    src = open(RN.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    names = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    banned = {"post", "place_order", "order_place", "submit", "cancel",
              "sign", "send"}
    assert not (names & banned), names & banned
    for bad in ("PMUS_API_SECRET_KEY", "PMUS_API_KEY_ID", "makerOrCancel",
                "order.place", "private_key"):
        assert bad not in src, bad


def test_status_http_still_carries_no_tape(monkeypatch):
    doc = {"top_markets": [{"slug": "m1", "bid": "0.4", "ask": "0.44",
                            "bid_qty": "1", "ask_qty": "1"}]}
    got = RN.markets_from_status(json.dumps(doc))
    assert got["m1"]["trades"] is None, (
        "STATUS_HTTP must keep asserting it has no tape; only TRADES_HTTP "
        "adds one")


# --------------------------------------------------------------------------
# following the live slate without discarding what we already know
# --------------------------------------------------------------------------

def test_resync_keeps_surfaces_for_markets_that_are_still_there():
    spec = SS.spec()
    surfaces = {"a": SS.MarketSurface("a", spec), "b": SS.MarketSurface("b", spec)}
    keep = surfaces["a"]
    keep.observations = 42                      # history we must not lose
    d = RN.resync(surfaces, {"a": {}, "c": {}}, cap=40, spec=spec)
    assert surfaces["a"] is keep and surfaces["a"].observations == 42, (
        "rebuilding a market that never went away would discard the "
        "queue-ahead-at-placement that EVIDENCED_FILL is derived from")
    assert "b" not in surfaces                  # genuinely gone
    assert "c" in surfaces                      # genuinely new
    assert d == {"added": 1, "retired": 1, "watching": 2}


def test_resync_respects_the_cap():
    spec = SS.spec()
    surfaces = {}
    RN.resync(surfaces, {str(i): {} for i in range(100)}, cap=5, spec=spec)
    assert len(surfaces) == 5
