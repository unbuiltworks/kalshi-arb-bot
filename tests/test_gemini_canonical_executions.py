"""ONE execution view, and the C8B timing that demanded it.

CONTINUOUS-8 proved the feedback loop and exposed the last seam doing it: the
coherence gate explained a disappearance from `fills u trades u
terminal_seen` while `CP.recover` built inventory from `fills` alone. At read
13 -- 2.64s after a real maker fill -- trades had the execution and filled
history did not, so the refresh reported `coherent: True` beside an inventory
that still read flat.

These tests pin the replay of that exact timing, the deduplication that makes
one view safe to build from two differently-shaped sources, and the refusals.
"""
import asyncio

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import executions as EX
from src.gemini_execution import ws_continuous as WC


# the REAL C8B rows, as the venue returned them
YES_OID = "145828836256673007"
YES_COID = "l5cp-owner-2026-yes-1713dabf"

TRADE = {"price": "0.31", "amount": "0.01", "timestamp": 1789781788,
         "timestampms": 1789781788553, "type": "Buy", "aggressor": False,
         "fee_currency": "USD", "fee_amount": "0", "tid": 1893456011790529,
         "order_id": YES_OID, "exchange": "gemini", "is_auction_fill": False,
         "is_clearing_fill": False, "symbol": "GEMI-BTC05M2609190140-UP",
         "client_order_id": YES_COID}

ORDER_ROW = {"orderId": 145828836256673007, "globalOrderId": YES_OID,
             "clientOrderId": YES_COID, "symbol": "GEMI-BTC05M2609190140-UP",
             "side": "buy", "outcome": "yes", "orderType": "limit",
             "timeInForce": "maker-or-cancel", "price": "0.31",
             "quantity": "0.01", "filledQuantity": "0.01",
             "remainingQuantity": "0", "status": "filled"}

LEGS = {YES_COID: "YES", YES_OID: "YES"}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ============================================ 2. the C8B replay, step by step

def test_the_C8B_timing_read_13_carries_the_real_inventory():
    """Steps 1-5 of the replay: the order disappears, trades sees it before
    filled history, and the canonical view already holds exactly ONE
    execution that inventory is built from."""
    # 3. trades sees the execution; filled history is still empty
    view = EX.canonical_executions([], [TRADE], leg_of=LEGS)
    assert view["readable"] is True
    # 4. EXACTLY ONE YES 0.01 @ 0.31
    assert view["n_executions"] == 1
    e = view["executions"][0]
    assert (e["leg"], e["qty"], e["price"]) == ("YES", "0.01", "0.31")
    assert e["sources"] == ["trades"]
    # provenance preserved
    assert e["tid"] == "1893456011790529" and e["maker"] is True
    assert e["fee"] == "0" and e["utc_ms"] == 1789781788553
    assert e["order_id"] == YES_OID and e["client_order_id"] == YES_COID

    # 5. inventory becomes the real one IMMEDIATELY
    inv, why = CP.recover("AUTH", "SYM", EX.as_fills(view))
    assert why is None
    rep = inv.report()
    assert rep["yes_qty"] == "0.01"
    assert rep["directional_residual"] == "0.01"
    assert rep["total_spend_usd"] == "0.0031"
    assert rep["n_fills"] == 1


def test_when_filled_history_catches_up_the_state_is_UNCHANGED():
    """Step 7, and the reason grouping is by ORDER rather than by row.

    The aggregate row describes the same execution the trade rows do. Summing
    both is 0.02 where the venue holds 0.01 -- a phantom position, in the
    direction that hurts.
    """
    early = EX.canonical_executions([], [TRADE], leg_of=LEGS)
    late = EX.canonical_executions([ORDER_ROW], [TRADE], leg_of=LEGS)

    assert late["readable"] is True and late["n_executions"] == 1
    assert late["executions"][0]["sources"] == ["trades", "order_history"]

    a = CP.recover("AUTH", "SYM", EX.as_fills(early))[0].report()
    b = CP.recover("AUTH", "SYM", EX.as_fills(late))[0].report()
    for k in ("yes_qty", "no_qty", "directional_residual", "n_fills",
              "total_spend_usd", "avg_yes_cost"):
        assert a[k] == b[k], k
    assert b["yes_qty"] == "0.01" and b["n_fills"] == 1


def test_rebuilding_the_same_state_repeatedly_is_idempotent():
    """A refresh runs every cycle. Rebuilding must not accumulate."""
    reps = []
    for _ in range(5):
        v = EX.canonical_executions([ORDER_ROW], [TRADE], leg_of=LEGS)
        reps.append(CP.recover("AUTH", "SYM", EX.as_fills(v))[0].report())
    assert all(r == reps[0] for r in reps)
    assert reps[0]["yes_qty"] == "0.01" and reps[0]["n_fills"] == 1


# ================================================== 3. deduplication proofs

def test_the_same_execution_in_both_sources_is_counted_once():
    view = EX.canonical_executions([ORDER_ROW], [TRADE], leg_of=LEGS)
    assert view["n_executions"] == 1
    assert view["by_order"][YES_OID]["qty"] == "0.01"
    assert view["by_order"][YES_OID]["n_executions"] == 1


def test_the_same_trade_twice_in_one_read_is_counted_once():
    """tid is the venue's own execution identity and is stable across reads."""
    view = EX.canonical_executions([], [dict(TRADE), dict(TRADE)],
                                   leg_of=LEGS)
    assert view["readable"] is True and view["n_executions"] == 1


def test_partial_fills_are_kept_SEPARATE_and_still_reconcile():
    """Three executions of one order: the fine-grained source wins and the
    aggregate CHECKS it rather than adding to it."""
    ts = [dict(TRADE, tid=1, amount="0.01"),
          dict(TRADE, tid=2, amount="0.02"),
          dict(TRADE, tid=3, amount="0.03")]
    agg = dict(ORDER_ROW, filledQuantity="0.06", quantity="0.06")
    view = EX.canonical_executions([agg], ts, leg_of=LEGS)
    assert view["readable"] is True
    assert view["n_executions"] == 3
    assert view["by_order"][YES_OID]["qty"] == "0.06"
    inv = CP.recover("AUTH", "SYM", EX.as_fills(view))[0].report()
    assert inv["yes_qty"] == "0.06" and inv["n_fills"] == 3


def test_ids_of_different_types_are_the_same_order():
    """The listing returns orderId as an INT and the ack as a STRING -- the
    defect that cost CONTINUOUS-5 a window."""
    t = dict(TRADE, order_id=145828836256673007)          # int
    view = EX.canonical_executions([ORDER_ROW], [t], leg_of=LEGS)
    assert view["readable"] is True and view["n_executions"] == 1


# ================================================== contradictions fail closed

def test_sources_that_disagree_about_quantity_FAIL_CLOSED():
    agg = dict(ORDER_ROW, filledQuantity="0.05")
    view = EX.canonical_executions([agg], [TRADE], leg_of=LEGS)
    assert view["readable"] is False
    c = view["contradictions"][0]
    assert c["kind"] == "QUANTITY_DISAGREEMENT"
    assert c["trades_total"] == "0.01" and c["order_history_filled"] == "0.05"
    # and NOTHING is handed on
    assert EX.as_fills(view) is None


def test_an_execution_with_no_establishable_leg_FAILS_CLOSED():
    view = EX.canonical_executions([], [TRADE], leg_of=None)
    assert view["readable"] is False
    assert view["contradictions"][0]["kind"] == "LEG_UNRESOLVED"
    assert EX.as_fills(view) is None


def test_a_leg_is_never_inferred_from_the_client_order_id():
    """`l5cp-owner-2026-yes-1713dabf` contains "yes". That must not be what
    sides an execution: it is a naming convention, not evidence."""
    view = EX.canonical_executions([], [TRADE], leg_of=None)
    assert view["readable"] is False
    blob = str(view)
    assert "LEG_UNRESOLVED" in blob


def test_a_trade_with_no_order_identity_is_a_contradiction():
    t = {k: v for k, v in TRADE.items()
         if k not in ("order_id", "client_order_id")}
    view = EX.canonical_executions([ORDER_ROW], [t], leg_of=LEGS)
    assert view["readable"] is False
    assert view["contradictions"][0]["kind"] == "TRADE_WITHOUT_ORDER_IDENTITY"


# ============================================ terminal is never an execution

@pytest.mark.parametrize("status", ["cancelled", "canceled", "expired",
                                    "closed", "rejected"])
def test_a_terminal_row_can_NEVER_become_a_fill(status):
    """The single worst failure this module could have, refused explicitly
    rather than left to the absence of a match."""
    row = dict(ORDER_ROW, status=status, filledQuantity="0.01")
    view = EX.canonical_executions([row], [], leg_of=LEGS)
    assert view["readable"] is True
    assert view["n_executions"] == 0
    assert CP.recover("AUTH", "SYM", EX.as_fills(view))[0].report()["n_fills"] == 0


def test_a_filled_row_with_zero_quantity_is_not_an_execution():
    row = dict(ORDER_ROW, filledQuantity="0")
    view = EX.canonical_executions([row], [], leg_of=LEGS)
    assert view["n_executions"] == 0


# ================================================ unreadable stays unreadable

def test_unreadable_filled_history_is_unreadable_not_empty():
    view = EX.canonical_executions(None, [TRADE], leg_of=LEGS)
    assert view["readable"] is False
    assert "UNREADABLE" in view["why"]
    assert EX.as_fills(view) is None
    assert view["sources_readable"] == {"orders_seen": False, "trades": True}


def test_unreadable_trades_is_DEGRADED_not_fatal():
    """It can only make this view lag, never invent -- so the window keeps
    going and the degradation is recorded."""
    view = EX.canonical_executions([ORDER_ROW], None, leg_of=LEGS)
    assert view["readable"] is True and view["degraded"] is True
    assert view["n_executions"] == 1
    assert view["executions"][0]["sources"] == ["order_history"]
    assert view["sources_readable"] == {"orders_seen": True, "trades": False}


# ====================================== 6. the driver: one view, both consumers

def _driver(tmp_path):
    d = WC.Driver("AUTH-CANON", "GEMI-BTC05M2609190140-UP",
                  ledger_path=str(tmp_path / "led.jsonl"), max_actions=12)
    d.state = WC.BOUND
    d.inv = CP.Inventory()
    d.guard = CP.WindowGuard("GEMI-BTC05M2609190140-UP", auth_id="AUTH-CANON")
    d.budget_consumed = 0
    return d


def test_the_gate_and_the_inventory_cannot_disagree(tmp_path, monkeypatch):
    """Step 6: the post-cancel decision after that point receives the REAL
    inventory, never flat.

    This is the C8B read-13 shape end to end, through the driver."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = _driver(tmp_path)
    d.working.add(YES_COID, leg="YES", price="0.31", qty="0.01", now=1000.0,
                  venue_order_id=YES_OID)
    d._remember_leg(YES_COID, "YES", YES_OID)

    async def refresh():
        # EXACTLY read 13: gone from active orders, filled history empty,
        # trades already carrying it
        return {"fills": [], "open_orders": [], "orders_seen": [],
                "terminal_seen": [], "trades": [TRADE]}

    out = _run(d._refresh(refresh))
    assert out is not None
    assert out["coherent"] is True and out["reconcile_attempts"] == 1
    # the refresh that said coherent HANDS ON the execution that made it so
    assert out["n_fills"] == 1 and out["yes_qty"] == "0.01"
    assert d.inv.report()["total_spend_usd"] == "0.0031"

    # and the controller therefore sees it
    turn = d.decide(200.0, book={"readable": True, "identity_consistent": True,
                                 "yes": {"bid": "0.12", "ask": "0.31"},
                                 "no": {"bid": "0.69", "ask": "0.88"}})
    labels = [a["action"] for a in (turn.get("turn") or {}).get("actions") or []]
    assert CP.OPEN not in labels, labels


# ============================== the crossed book the first soak actually found

@pytest.mark.parametrize("book,why", [
    # THE REAL ROW, GEMI-BTC05M2609190305-UP cycles 20-21, 2026-09-19:
    # a YES bid thirty cents ABOVE its own ask, with the complement identity
    # holding EXACTLY -- which is why the existing check could not catch it.
    ({"readable": True, "identity_consistent": True,
      "yes": {"bid": "0.95", "ask": "0.65"},
      "no": {"bid": "0.35", "ask": "0.05"}}, "crossed"),
    # touching is not a book either: a zero-width market is not one we can
    # join passively on both sides
    ({"readable": True, "identity_consistent": True,
      "yes": {"bid": "0.50", "ask": "0.50"},
      "no": {"bid": "0.50", "ask": "0.50"}}, "equal"),
    # a missing side is not a price
    ({"readable": True, "identity_consistent": True,
      "yes": {"bid": "0.50", "ask": None},
      "no": {"bid": "0.50", "ask": "0.50"}}, "missing"),
])
def test_a_crossed_or_degenerate_book_is_not_quoted_into(book, why):
    assert CP.crossed(book) is True
    st = CP.state_from_venue("SYM", 200.0, CP.Inventory(), now=1.0, book=book)
    assert st["inventory_known"] is False
    assert st["state_id"] == "unreadable"
    assert st["outcomes"] == {}


def test_the_identity_check_alone_would_have_passed_that_book():
    """The point of the new gate, pinned: crossing BOTH sides preserves the
    complement identity by construction, so `identity_consistent` is True on
    exactly the book that must be refused."""
    b = {"readable": True, "identity_consistent": True,
         "yes": {"bid": "0.95", "ask": "0.65"},
         "no": {"bid": "0.35", "ask": "0.05"}}
    # the identity really does hold: no_bid == 1 - yes_ask, no_ask == 1 - yes_bid
    from decimal import Decimal as _D
    assert _D(b["no"]["bid"]) == _D("1") - _D(b["yes"]["ask"])
    assert _D(b["no"]["ask"]) == _D("1") - _D(b["yes"]["bid"])
    assert CP.crossed(b) is True


def test_an_ordinary_book_is_still_quoted_into():
    """The gate must not refuse the 84 of 86 soak cycles that were fine."""
    b = {"readable": True, "identity_consistent": True,
         "yes": {"bid": "0.31", "ask": "0.58"},
         "no": {"bid": "0.42", "ask": "0.69"}}
    assert CP.crossed(b) is False
    st = CP.state_from_venue("SYM", 265.0, CP.Inventory(), now=1.0, book=b)
    assert st["inventory_known"] is True


def test_the_gate_says_nothing_about_what_a_PAIR_may_cost():
    """It is a READABILITY gate, not the pair-cost ceiling the owner ruled
    against. A wide, expensive, perfectly readable book still passes."""
    b = {"readable": True, "identity_consistent": True,
         "yes": {"bid": "0.60", "ask": "0.95"},
         "no": {"bid": "0.05", "ask": "0.40"}}          # passive pair 0.65
    assert CP.crossed(b) is False
    b2 = {"readable": True, "identity_consistent": True,
          "yes": {"bid": "0.90", "ask": "0.95"},
          "no": {"bid": "0.05", "ask": "0.10"}}         # passive pair 0.95
    assert CP.crossed(b2) is False
