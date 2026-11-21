"""The hypothetical fill ledger: the fill mechanism a shadow lane lacks."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import hypothetical as HY
from src.gemini_execution import tape_replay as TR


def _p(tid, ms, price, amount, type_):
    return {"tid": tid, "utc_ms": ms, "price": price, "amount": amount,
            "type": type_}


def _depth(levels):
    return {"readable": True, "bids": {"levels": levels}}


# =================================================== it is not an order path

def test_IT_NEVER_REACHES_A_WRITE_PATH():
    """The whole safety property, asserted on the module's own source."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(HY))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "place" not in called
    assert "cancel" not in called
    assert "submit" not in called


def test_every_fill_is_labelled_hypothetical_and_an_upper_bound():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    inv = CP.Inventory()
    got = b.consume([_p(1, 2000, "0.28", "5", "sell")], inv=inv, book={},
                    depth=None, tte_s=200)
    assert len(got) == 1
    assert "HYPOTHETICAL" in got[0]["labels"]
    assert "upper bound" in got[0]["labels"].lower()
    flat = " ".join(HY.__doc__.lower().split())
    assert "not a cost basis" in flat
    assert "no order is submitted" in flat


# ============================================== the tape semantics are kept

def test_a_YES_rung_is_reached_only_by_a_SELLER():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    inv = CP.Inventory()
    # a BUY at 0.28 does not reach a resting YES bid, however cheap
    assert b.consume([_p(1, 2000, "0.28", "5", "buy")], inv=inv, book={},
                     depth=None, tte_s=200) == []
    assert inv.residual() == 0


def test_a_NO_rung_is_reached_by_a_BUYER_at_one_minus_price():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "NO", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    inv = CP.Inventory()
    # NO at 0.30 is reached by a YES buy at >= 0.70
    assert b.consume([_p(1, 2000, "0.69", "5", "buy")], inv=inv, book={},
                     depth=None, tte_s=200) == []
    assert b.consume([_p(2, 3000, "0.71", "5", "buy")], inv=inv, book={},
                     depth=None, tte_s=200) != []


# ======================================================= the queue eats first

def test_THE_DISPLAYED_QUEUE_EATS_FIRST():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}],
               now_ms=1000, depth=_depth([{"price": "0.30", "qty": "10"}]))
    inv = CP.Inventory()
    # 4 contracts do not clear 10 ahead of us
    assert b.consume([_p(1, 2000, "0.30", "4", "sell")], inv=inv, book={},
                     depth=None, tte_s=200) == []
    # 7 more does: 10 eaten, then us
    assert b.consume([_p(2, 3000, "0.30", "7", "sell")], inv=inv, book={},
                     depth=None, tte_s=200) != []


def test_a_price_change_loses_queue_position():
    d = _depth([{"price": "0.30", "qty": "10"}, {"price": "0.29", "qty": "4"}])
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=d)
    b.consume([_p(1, 1500, "0.30", "6", "sell")], inv=CP.Inventory(), book={},
              depth=None, tte_s=200)
    assert b.live["YES"].ahead_left == D("4")
    # requote one tick lower: a NEW place at the back of THAT level
    b.retarget([{"leg": "YES", "price": "0.29", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=2000, depth=d)
    assert b.live["YES"].ahead_left == D("14")
    assert b.requoted == 1


def test_the_same_price_keeps_the_queue_but_takes_the_new_label():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    d = _depth([{"price": "0.30", "qty": "10"}])
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=d)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.COMPLETE}], now_ms=2000, depth=d)
    assert b.live["YES"].kind == CP.COMPLETE
    assert b.live["YES"].ahead_left == D("10")
    assert b.requoted == 0


# ========================================================= no double filling

def test_A_REFETCHED_TAPE_CANNOT_FILL_THE_SAME_RUNG_TWICE():
    """The live loop re-reads a rolling tape every cycle; most of it is old."""
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.05",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    inv = CP.Inventory()
    tape = [_p(1, 2000, "0.28", "0.01", "sell")]
    assert len(b.consume(tape, inv=inv, book={}, depth=None, tte_s=200)) == 1
    assert b.consume(list(tape), inv=inv, book={}, depth=None,
                     tte_s=200) == []
    assert inv.residual() == D("0.01")


def test_an_idless_print_is_admitted_only_if_strictly_newer():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.05",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    inv = CP.Inventory()
    old = {"utc_ms": 2000, "price": "0.28", "qty": "0.01", "side": "sell"}
    assert len(b.consume([old], inv=inv, book={}, depth=None,
                         tte_s=200)) == 1
    assert b.consume([dict(old)], inv=inv, book={}, depth=None,
                     tte_s=200) == []


def test_public_flow_rows_are_translated_not_assumed():
    """public_flow speaks qty/side; the replay engine speaks amount/type."""
    rows = HY.normalize([{"tid": 7, "utc_ms": 5, "price": "0.4",
                          "qty": "3", "side": "SELL"}])
    assert rows[0]["amount"] == D("3") and rows[0]["type"] == "sell"


def test_prints_are_walked_oldest_first():
    rows = HY.normalize([{"tid": 2, "utc_ms": 20, "price": "0.4", "qty": "1",
                          "side": "sell"},
                         {"tid": 1, "utc_ms": 10, "price": "0.4", "qty": "1",
                          "side": "sell"}])
    assert [r["tid"] for r in rows] == [1, 2]


# ============================================ the sequence it exists to see

def test_a_first_fill_is_marked_as_one_and_only_once():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.02",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    inv = CP.Inventory()
    f1 = b.consume([_p(1, 2000, "0.28", "0.01", "sell")], inv=inv, book={},
                   depth=None, tte_s=200)
    assert f1[0]["was_first_fill"] is True
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.02",
                 "action": CP.EXTEND}], now_ms=2500, depth=None)
    f2 = b.consume([_p(2, 3000, "0.28", "0.01", "sell")], inv=inv, book={},
                   depth=None, tte_s=200)
    assert f2[0]["was_first_fill"] is False
    assert b.report()["first_fills"] == 1


def test_the_full_recycle_moves_inventory_to_matched():
    """OPEN fill -> inventory change -> COMPLETE fill -> matched inventory."""
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    inv = CP.Inventory()
    b.retarget([{"leg": "YES", "price": "0.40", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    b.consume([_p(1, 2000, "0.39", "1", "sell")], inv=inv, book={},
              depth=None, tte_s=200)
    assert inv.residual() == D("0.01") and inv.matched() == 0
    b.retarget([{"leg": "NO", "price": "0.55", "qty": "0.01",
                 "action": CP.COMPLETE}], now_ms=3000, depth=None)
    b.consume([_p(2, 4000, "0.46", "1", "buy")], inv=inv, book={},
              depth=None, tte_s=180)
    assert inv.matched() == D("0.01")
    assert inv.residual() == 0
    r = b.report()
    assert r["first_fills"] == 1 and r["complete_fills"] == 1


def test_the_report_counts_by_action_label():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    r = b.report()
    for k in ("rungs_placed", "requotes", "fills", "first_fills",
              "open_fills", "extend_fills", "complete_fills",
              "fills_resting_on_an_unknown_queue"):
        assert k in r
    assert "HYPOTHETICAL" in r["labels"]


def test_A_REQUOTE_COUNTS_AS_A_RUNG_THAT_RESTED():
    """Fill conversion divided by `placed` alone would flatter a lane that
    requotes every cycle -- exactly the lane this is."""
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    for i, px in enumerate(("0.30", "0.31", "0.32")):
        b.retarget([{"leg": "YES", "price": px, "qty": "0.01",
                     "action": CP.OPEN}], now_ms=1000 + i, depth=None)
    r = b.report()
    assert r["rungs_placed"] == 1 and r["requotes"] == 2
    assert r["rungs_rested"] == 3


def test_THE_MID_FILL_RETARGET_SAYS_IT_SKIPPED_THE_GUARD():
    """Cycle-boundary rungs come from WindowGuard; the post-fill one does not.

    Recorded on the row rather than left implicit, so the evidence cannot be
    read as though every rung passed the guard.
    """
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    got = b.consume([_p(1, 2000, "0.28", "5", "sell")], inv=CP.Inventory(),
                    book={}, depth=None, tte_s=200)
    assert got[0]["retargeted_by"] == "tape_replay.desired (no WindowGuard)"
    flat = " ".join(HY.__doc__.split())
    assert "without the guard" in flat


def test_an_unreadable_book_takes_the_rungs_down_rather_than_guessing():
    b = HY.HypotheticalBook(symbol="S", policy=TR.POLICY_A)
    b.retarget([{"leg": "YES", "price": "0.30", "qty": "0.01",
                 "action": CP.OPEN}], now_ms=1000, depth=None)
    b.consume([_p(1, 2000, "0.28", "5", "sell")], inv=CP.Inventory(),
              book={"readable": False, "why": "nope"}, depth=None, tte_s=200)
    assert b.live == {}
