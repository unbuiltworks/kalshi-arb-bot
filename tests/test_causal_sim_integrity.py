"""Integrity tests for the causal event-stream simulator.

These pin three properties an audit asked for explicitly. Each corresponds to a
way the simulator could quietly invent or destroy evidence.
"""
from __future__ import annotations

import gzip
import json
import os
import tempfile
from decimal import Decimal as D

from src import l5_event_stream as ES
from src.l5_causal_sim import CausalSim


def _book(bid, ask, bq, aq, status="OK"):
    return {"bid": bid, "ask": ask, "bid_qty": bq, "ask_qty": aq,
            "status": status}


# ------------------------------------------------- 3. same-print exhaustion

def test_one_print_both_clears_the_queue_and_fills_us():
    """THE RULE: print_qty consumes queue_ahead first, and the REMAINDER of
    that same print fills us. No separate later print is required."""
    s = CausalSim(clip=D(10))
    s.on_book(_book("0.40", "0.41", "100", "100"), 1.0)
    assert s.resting["up"].ahead == D(100)
    s.on_trade({"px": "0.40", "qty": "107"}, 2.0)      # 100 queue + 7 to us
    assert len(s.fills) == 1
    assert D(s.fills[0]["qty"]) == D(7), s.fills
    # and the REMAINDER of our own order is then cancelled, because those 7
    # contracts made UP the long side: buying more of it is an EXTEND, which is
    # off by default. That is the intended continuous-inventory behaviour --
    # a partial fill immediately re-prices the whole book, it does not leave a
    # stale order deepening the imbalance.
    assert s.resting["up"] is None
    assert s.bk.q["up"] == D(7)
    assert s.complement_cap("down") is not None, "DOWN must now be a complement"


def test_a_print_smaller_than_the_queue_fills_nothing():
    s = CausalSim(clip=D(10))
    s.on_book(_book("0.40", "0.41", "500", "500"), 1.0)
    s.on_trade({"px": "0.40", "qty": "495"}, 2.0)
    assert s.fills == []
    assert s.resting["up"].ahead == D(5)


def test_the_next_print_consumes_the_rest_and_fills():
    s = CausalSim(clip=D(10))
    s.on_book(_book("0.40", "0.41", "500", "500"), 1.0)
    s.on_trade({"px": "0.40", "qty": "495"}, 2.0)
    s.on_trade({"px": "0.40", "qty": "20"}, 3.0)       # 5 queue + 15 avail
    assert len(s.fills) == 1
    assert D(s.fills[0]["qty"]) == D(10)               # capped by our clip


def test_a_print_away_from_our_price_never_reaches_us():
    s = CausalSim(clip=D(10))
    s.on_book(_book("0.40", "0.41", "0", "0"), 1.0)
    s.on_trade({"px": "0.45", "qty": "10000"}, 2.0)    # through the ask side
    ups = [f for f in s.fills if f["side"] == "up"]
    assert ups == []


def test_touch_alone_and_depth_alone_never_fill():
    s = CausalSim(clip=D(10))
    for i in range(50):
        s.on_book(_book("0.40", "0.41", "5000", "5000"), float(i))
    assert s.fills == [], "a quoted, deep book with no prints filled us"


def test_a_requote_goes_to_the_back_of_the_new_queue():
    s = CausalSim(clip=D(10))
    s.on_book(_book("0.40", "0.41", "100", "100"), 1.0)
    s.on_trade({"px": "0.40", "qty": "60"}, 2.0)
    assert s.resting["up"].ahead == D(40)
    s.on_book(_book("0.50", "0.51", "900", "900"), 3.0)   # touch moved
    assert s.resting["up"].px == D("0.50")
    assert s.resting["up"].ahead == D(900), "kept a stale queue position"


# --------------------------------------------------------- 2. de-duplication

def _write(tmp, name, docs):
    path = os.path.join(tmp, name)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for ts, msg in docs:
            fh.write(json.dumps({"timestamp": ts, "message": msg}) + "\n")
    return path


def _trade_doc(slug, rows):
    return "PMUS_TRADE_ROWS " + json.dumps({"markets": {slug: rows}})


SLUG = "cpc-btc-updown-15m-2026-09-30-1330z"


def test_two_identical_prints_in_one_document_stay_two():
    """MUTATION TEST the audit asked for. A venue may print twice for the same
    size at the same price in the same instant. Collapsing them destroys real
    volume, and volume is what fills us."""
    row = {"t": 100.0, "px": "0.40", "qty": "25", "trade_time": "X",
           "bid_at_trade": "0.40", "ask_at_trade": "0.41", "dir": "sell"}
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "a.jsonl.gz", [("T1", _trade_doc(SLUG, [row, dict(row)]))])
        data = ES.load(os.path.join(tmp, "*.jsonl.gz"))
    assert len(data[SLUG]["trades"]) == 2, data[SLUG]["trades"]


def test_two_book_events_sharing_a_timestamp_stay_two():
    doc = "PMUS_BOOK_ROWS " + json.dumps({"markets": {SLUG: [
        {"t": 100.0, "bid": "0.40", "ask": "0.41", "bid_qty": "10",
         "ask_qty": "10", "status": "OK"},
        {"t": 100.0, "bid": "0.41", "ask": "0.42", "bid_qty": "20",
         "ask_qty": "20", "status": "OK"}]}})
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "a.jsonl.gz", [("T1", doc)])
        data = ES.load(os.path.join(tmp, "*.jsonl.gz"))
    b = data[SLUG]["book"]
    assert len(b) == 2
    assert [x["bid"] for x in b] == ["0.40", "0.41"], "source order lost"


def test_a_repeated_log_line_is_de_duplicated():
    """The real duplicate: the same Railway line captured in two rounds."""
    row = {"t": 100.0, "px": "0.40", "qty": "25", "trade_time": "X",
           "dir": "sell"}
    msg = _trade_doc(SLUG, [row])
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "a.jsonl.gz", [("T1", msg)])
        _write(tmp, "b.jsonl.gz", [("T1", msg)])       # same line, round 2
        data = ES.load(os.path.join(tmp, "*.jsonl.gz"))
    assert len(data[SLUG]["trades"]) == 1


def test_equal_timestamps_keep_source_order_across_files():
    mk = lambda q: {"t": 100.0, "px": "0.40", "qty": q, "trade_time": "X",
                    "dir": "sell"}                      # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "a.jsonl.gz", [("T1", _trade_doc(SLUG, [mk("1"), mk("2")]))])
        _write(tmp, "b.jsonl.gz", [("T2", _trade_doc(SLUG, [mk("3")]))])
        data = ES.load(os.path.join(tmp, "*.jsonl.gz"))
    assert [t["qty"] for t in data[SLUG]["trades"]] == ["1", "2", "3"]


def test_merged_stream_puts_book_before_trade_on_a_tie():
    data = {"book": [{"t": 5.0, "bid": "0.4", "_src": (0, 0, 0)}],
            "trades": [{"t": 5.0, "px": "0.4", "_src": (0, 0, 0)}]}
    ev = ES.merged(data)
    assert [k for _t, k, _r in ev] == ["book", "trade"]
