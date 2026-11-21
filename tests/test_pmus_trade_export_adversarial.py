"""Adversarial tests for the recorder trade export and its consumer.

Each names a specific way the pipeline could LIE, and pins it shut. These are
the five the owner named, plus the credential and order-capability proofs that
make the endpoint safe to turn on at all.
"""
import ast
import inspect
import json
import os

import pytest

from src import pmus_l5_surface_runner as RUN
from src import pmus_l5_surface_shadow as SS
from src import pmus_ws_evidence_recorder as REC
from src.l5_surface import pmus_prints as PP


@pytest.fixture(autouse=True)
def _off():
    prev = os.environ.pop(REC.TRADES_ENV, None)
    yield
    if prev is None:
        os.environ.pop(REC.TRADES_ENV, None)
    else:
        os.environ[REC.TRADES_ENV] = prev


def _mkt(trades, bq="20", aq="570"):
    return {"bid": "0.43", "ask": "0.48", "bid_qty": bq, "ask_qty": aq,
            "trades": trades}


# =========================================================== 1. unreadable != empty

def test_unreadable_tape_never_becomes_an_observed_absence():
    surf = SS.MarketSurface("s", SS.spec())
    for i in range(4):
        SS.step(surf, _mkt(None), 100.0 + i, tte_s=3600.0)
    r = SS.report({"s": surf})
    assert r["verdict"] == "TRADE_FLOW_NOT_OBSERVABLE"
    assert r["tape"]["flow_observable"] is False


def test_readable_and_empty_IS_an_observed_absence():
    surf = SS.MarketSurface("s", SS.spec())
    for i in range(4):
        SS.step(surf, _mkt([]), 100.0 + i, tte_s=3600.0)
    r = SS.report({"s": surf})
    assert r["verdict"] == "NO_FILLS_NO_FLOW"
    assert r["tape"]["flow_observable"] is True


def test_the_two_are_not_interchangeable_at_any_layer():
    """Adapter, flow measurement and consumer must all keep the distinction."""
    assert PP.to_prints(None) is None and PP.to_prints([]) == []
    assert PP.trade_flow_for("0.4", None, side="BUY",
                             placed_at=0)["readable"] is False
    assert PP.trade_flow_for("0.4", [], side="BUY",
                             placed_at=0)["readable"] is True
    assert RUN.fetch_status("http://127.0.0.1:9/x", timeout=0.2) is None


def test_an_unreachable_status_scores_no_market_rather_than_zero():
    doc_missing = RUN.markets_from_status("")
    assert doc_missing == {}          # nothing to score, not a market at zero


# ================================================ 2. wrapped ring != complete tape

def test_a_full_ring_forfeits_coverage():
    c = PP.coverage([{"t": 1.0}] * PP.RING_CAPACITY, subscribed_at=1.0,
                    placed_at=8.0, observed_until=30.0)
    assert c["complete"] is None and "ring is full" in c["why"]


def test_the_export_declares_a_wrapped_ring_with_the_data():
    """A consumer cannot know the tape is partial unless it travels with it."""
    p = REC.trades_payload({"s": {"trades": [{"t": 1.0, "px": "0.4",
                                              "qty": "1"}] * 500}}, 1.0)
    assert p["markets"]["s"]["ring_full"] is True


def test_a_ring_one_short_of_full_still_vouches():
    c = PP.coverage([{"t": 1.0}] * (PP.RING_CAPACITY - 1), subscribed_at=1.0,
                    placed_at=8.0, observed_until=30.0)
    assert c["complete"] is True


def test_a_wrapped_ring_cannot_produce_an_evidenced_no_fill():
    """The dangerous outcome: a busy market reporting 'nobody traded'."""
    from src.l5_shadow import maker as MK
    ev = MK.evidence_for_quote(quote_px="0.47", quote_qty="1", ahead="0",
                               placed_at=5.0, prints=[], observed_until=60.0,
                               coverage_complete=None)
    assert ev["evidenced_fill"] is None         # UNKNOWN, not a no-fill


# ============================================== 3. late subscription != coverage

def test_subscribing_after_the_quote_forfeits_coverage():
    c = PP.coverage([{"t": 9.0}], subscribed_at=12.0, placed_at=8.0,
                    observed_until=30.0)
    assert c["complete"] is None and "AFTER the quote" in c["why"]


def test_an_unknown_subscription_start_is_not_assumed_early():
    c = PP.coverage([{"t": 9.0}], subscribed_at=None, placed_at=8.0,
                    observed_until=30.0)
    assert c["complete"] is None


def test_every_coverage_refusal_is_none_never_false():
    """'We did not watch' must never render as 'we watched and saw nothing'."""
    cases = [(None, 1.0), ([{"t": 1.0}], None), ([{"t": 1.0}], 99.0),
             ([{"t": 1.0}] * PP.RING_CAPACITY, 1.0)]
    for trades, sub in cases:
        c = PP.coverage(trades, subscribed_at=sub, placed_at=8.0,
                        observed_until=30.0)
        assert c["complete"] is None and c["why"]


# ==================================================== 4. fill implies trade_flow

def test_a_fill_always_carries_its_flow():
    surf = SS.MarketSurface("s", SS.spec())
    SS.step(surf, _mkt([]), 100.0, tte_s=3600.0)
    SS.step(surf, _mkt([{"t": 110.0, "px": "0.43", "qty": "25"}]), 120.0,
            tte_s=3600.0)
    v = SS.report({"s": surf})["per_side"]["BUY_YES"]
    assert v["EVIDENCED_FILL"] == 1 and v["TRADE_FLOW"] >= v["EVIDENCED_FILL"]


def test_no_fill_can_exist_without_flow_on_either_side():
    surf = SS.MarketSurface("s", SS.spec())
    SS.step(surf, _mkt([]), 100.0, tte_s=3600.0)
    SS.step(surf, _mkt([{"t": 110.0, "px": "0.43", "qty": "25"},
                        {"t": 111.0, "px": "0.48", "qty": "999"}]), 120.0,
            tte_s=3600.0)
    for side, v in SS.report({"s": surf})["per_side"].items():
        assert v["TRADE_FLOW"] >= v["EVIDENCED_FILL"], side


def test_an_unknown_queue_cannot_manufacture_a_fill():
    surf = SS.MarketSurface("s", SS.spec())
    SS.step(surf, _mkt([], bq=None), 100.0, tte_s=3600.0)
    rows = SS.step(surf, _mkt([{"t": 110.0, "px": "0.43", "qty": "99999"}],
                              bq=None), 120.0, tte_s=3600.0)
    assert rows == []
    assert SS.report({"s": surf})["per_side"]["BUY_YES"]["EVIDENCED_FILL"] == 0


# ============================================= 5. monotonic quote-level funnel

def test_the_funnel_is_monotone_under_a_long_random_walk():
    import random
    rnd = random.Random(20260917)
    surf = SS.MarketSurface("s", SS.spec())
    t = 100.0
    for _ in range(300):
        bid = rnd.choice(["0.40", "0.43", "0.47", "0.50"])
        ask = rnd.choice(["0.52", "0.55", "0.60"])
        trades = ([{"t": t - 1, "px": rnd.choice(["0.39", "0.43", "0.61"]),
                    "qty": str(rnd.randint(1, 400))}] if rnd.random() < 0.4
                  else [])
        SS.step(surf, {"bid": bid, "ask": ask, "bid_qty": "20",
                       "ask_qty": "30", "trades": trades}, t, tte_s=3600.0)
        t += 1.0
    for side, v in SS.report({"s": surf})["per_side"].items():
        assert (v["PLACED"] >= v["RESTABLE"] >= v["BOOK_REACHED"]
                >= v["TRADE_FLOW"] >= v["EVIDENCED_FILL"]), (side, v)


def test_a_quote_counts_once_per_stage_however_long_it_rests():
    surf = SS.MarketSurface("s", SS.spec())
    for i in range(50):
        SS.step(surf, _mkt([]), 100.0 + i, tte_s=3600.0)
    v = SS.report({"s": surf})["per_side"]["BUY_YES"]
    assert v["PLACED"] == 1 and v["RESTABLE"] == 1


# ======================================== the endpoint stays safe to enable

def test_the_endpoint_is_off_by_default():
    assert REC.trades_http_enabled() is False
    REC.publish_live_trades({"s": {"trades": [{"t": 1, "px": "1", "qty": "1"}]}},
                            1.0)
    assert REC.live_trades_bytes() is None


def test_a_disabled_endpoint_serves_404_not_an_empty_tape():
    """An empty tape would read as 'nobody traded'; 404 cannot be mistaken."""
    src = inspect.getsource(REC.serve_status_doc)
    i = src.index("TRADES_HTTP_PATH")
    assert "404" in src[i:i + 400]


def test_the_payload_carries_no_credential_material():
    p = REC.trades_payload({"s": {"trades": [{"t": 1, "px": "0.4", "qty": "1",
                                              "dir": "buy"}]}}, 1.0)
    flat = json.dumps(p).lower()
    for banned in ("key", "secret", "signature", "apikey", "token",
                   "authorization", "payload"):
        assert banned not in flat


def test_the_payload_is_bounded():
    p = REC.trades_payload({"s": {"trades": [{"t": i, "px": "0.4", "qty": "1"}
                                             for i in range(5000)]}}, 1.0)
    assert len(p["markets"]["s"]["trades"]) == REC.TRADES_PER_MARKET


def test_the_export_adds_no_order_endpoint_or_import():
    """The recorder must not gain order capability by gaining an export."""
    src = "\n".join(inspect.getsource(f) for f in
                    (REC.trades_payload, REC.publish_live_trades,
                     REC.live_trades_bytes, REC.trades_http_enabled))
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for banned in ("place_limit_buy", "new_order", "cancel_order", "submit",
                   "OrderArgs", "post"):
        assert banned not in names


def test_it_is_served_by_the_same_process_and_port_as_status():
    src = inspect.getsource(REC)
    assert src.count("HTTPServer(") == 1
    assert src.count("def serve_status_doc") == 1


def test_the_trade_tail_comes_from_the_same_snapshot_as_the_status():
    """A consumer must never see a book and trades from different moments."""
    src = inspect.getsource(REC)
    i = src.index("write_status_doc(root, st)")
    assert "publish_live_trades(snap" in src[i:i + 400]


def test_it_is_a_GET_only_handler():
    src = inspect.getsource(REC.serve_status_doc)
    assert "def do_GET" in src
    for verb in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert verb not in src
