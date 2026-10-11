"""The proposed minimum recorder-side export, and the consumer that reads it.

NOT DEPLOYED. `PMUS_WS_TRADES_HTTP` is off by default and the recorder tracks
a different branch, so merging this changes nothing until an owner turns it on.
These tests pin the properties that make it safe to turn on.
"""
import ast
import inspect
import json
import os

import pytest

from src import pmus_l5_surface_runner as RUN
from src import pmus_ws_evidence_recorder as REC


@pytest.fixture(autouse=True)
def _off():
    prev = os.environ.pop(REC.TRADES_ENV, None)
    yield
    if prev is None:
        os.environ.pop(REC.TRADES_ENV, None)
    else:
        os.environ[REC.TRADES_ENV] = prev


def _state(n=3, ring=1):
    return {"aec-x": {"trades": [{"t": float(i), "px": "0.47", "qty": "10",
                                  "dir": "buy"} for i in range(n * ring)],
                      "bad_trade_rows": 0}}


# ------------------------------------------------------- off by default

def test_it_is_off_unless_explicitly_enabled():
    assert REC.trades_http_enabled() is False
    REC.publish_live_trades(_state(), 1.0)
    assert REC.live_trades_bytes() is None


def test_enabling_it_publishes():
    os.environ[REC.TRADES_ENV] = "1"
    REC.publish_live_trades(_state(), 1.0)
    blob = REC.live_trades_bytes()
    assert blob and json.loads(blob.decode())["markets"]["aec-x"]["trades"]


# ------------------------------------------------ what travels with the data

def test_a_wrapped_ring_is_declared_with_the_data():
    """A consumer cannot otherwise know older trades may have been dropped,
    and a wrapped ring is exactly where silence stops meaning no trades."""
    p = REC.trades_payload(_state(n=500), 1.0)
    assert p["markets"]["aec-x"]["ring_full"] is True
    p2 = REC.trades_payload(_state(n=3), 1.0)
    assert p2["markets"]["aec-x"]["ring_full"] is False


def test_the_tail_is_bounded_and_newest_last():
    p = REC.trades_payload(_state(n=500), 1.0)
    tr = p["markets"]["aec-x"]["trades"]
    assert len(tr) == REC.TRADES_PER_MARKET
    assert tr[-1]["t"] > tr[0]["t"]


def test_it_carries_price_and_size_which_is_the_whole_point():
    """trades_1h is a COUNT and cannot say a trade happened at OUR price."""
    tr = REC.trades_payload(_state(), 1.0)["markets"]["aec-x"]["trades"][0]
    assert set(tr) == {"t", "px", "qty", "dir"}


def test_a_missing_market_is_absent_not_empty():
    p = REC.trades_payload({}, 1.0)
    assert p["markets"] == {}


# --------------------------------------------------------- it stays safe

def test_it_adds_no_order_capability():
    src = inspect.getsource(REC.trades_payload) + \
        inspect.getsource(REC.publish_live_trades) + \
        inspect.getsource(REC.live_trades_bytes)
    tree = ast.parse("\n".join(l for l in src.splitlines()))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & {"request", "post", "place_limit_buy", "send"})


def test_publishing_never_raises_on_a_malformed_state():
    os.environ[REC.TRADES_ENV] = "1"
    REC.publish_live_trades({"aec-x": None}, 1.0)          # must not raise
    REC.publish_live_trades(None, 1.0)                     # must not raise


def test_it_reuses_the_existing_server_and_port():
    """No new service, no new port, no new thread."""
    src = inspect.getsource(REC)
    assert src.count("HTTPServer(") == 1
    assert REC.TRADES_HTTP_PATH not in REC.STATUS_HTTP_PATHS


# ------------------------------------------------------- the consumer side

def test_the_status_source_marks_trades_unreadable_not_empty():
    """/status carries the authenticated BOOK and only aggregate trades, so a
    consumer of it must stop at BOOK_REACHED and say so."""
    doc = json.dumps({"top_markets": [
        {"slug": "aec-x", "bid": "0.43", "ask": "0.48", "bid_qty": "20",
         "ask_qty": "570", "trades_1h": 17, "two_sided_trades": True}]})
    m = RUN.markets_from_status(doc + "\n")
    assert m["aec-x"]["trades"] is None
    assert m["aec-x"]["bid_qty"] == "20"


def test_the_newest_status_line_wins():
    a = json.dumps({"top_markets": [{"slug": "s", "bid": "0.10", "ask": "0.2"}]})
    b = json.dumps({"top_markets": [{"slug": "s", "bid": "0.40", "ask": "0.5"}]})
    assert RUN.markets_from_status(a + "\n" + b + "\n")["s"]["bid"] == "0.40"


def test_an_unparseable_status_yields_no_markets_not_a_crash():
    assert RUN.markets_from_status("<html>nope</html>") == {}
    assert RUN.markets_from_status("") == {}


def test_rows_are_collected_from_every_list_the_status_carries():
    doc = json.dumps({"top_markets": [{"slug": "a", "bid": "0.1", "ask": "0.2"}],
                      "eligible_markets": [{"slug": "b", "bid": "0.3", "ask": "0.4"}],
                      "spring_watch": [{"slug": "c", "bid": "0.5", "ask": "0.6"}]})
    assert set(RUN.markets_from_status(doc)) == {"a", "b", "c"}


def test_an_unreachable_status_is_none_not_empty():
    """None means unreachable; {} would mean 'served, and no markets'."""
    assert RUN.fetch_status("http://127.0.0.1:9/nope", timeout=0.2) is None


def test_the_surface_over_status_http_stops_at_book_reached():
    """End to end: authenticated book in, funnel out, flow not observable."""
    from src import pmus_l5_surface_shadow as SS
    doc = json.dumps({"top_markets": [
        {"slug": "aec-x", "bid": "0.43", "ask": "0.48", "bid_qty": "20",
         "ask_qty": "570"}]})
    mkt = RUN.markets_from_status(doc)["aec-x"]
    surf = SS.MarketSurface("aec-x", SS.spec())
    SS.step(surf, mkt, 100.0, tte_s=3600.0)
    rep = SS.report({"aec-x": surf})
    assert rep["per_side"]["BUY_YES"]["BOOK_REACHED"] == 1
    assert rep["per_side"]["BUY_NO"]["BOOK_REACHED"] == 1
    assert rep["verdict"] == "TRADE_FLOW_NOT_OBSERVABLE"
    assert rep["tape"]["flow_observable"] is False
