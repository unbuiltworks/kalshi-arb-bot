"""The ownership boundary: an Intent is a REQUEST, not an order.

R5 PROVED THE DEFECT. `pmus_l5_native_engine.on_book` used to write
`self.resting[side]` immediately after appending a REST Intent, before the
money path had established that anything existed. Of the 416 ticks after
R5's last fill, **271 (65.1%)** emitted NO INTENT because `cur["px"] == px`
matched a quote that had been blocked before submission and never placed.

`resting` is now owned by the execution layer and adopted through
`sync_resting(live_orders)`. `live_orders` is populated only by a submit that
returned a venue order id and cleared when the venue says that order is
terminal.
"""
from decimal import Decimal as D

import pytest

from src import pmus_l5_native_engine as E
from src import pmus_native_money_path as MP

CLIP = D(10)


def engine():
    return E.NativeEngine(clip=CLIP, prospective_cap=False)


def book(bid, ask, t=1000.0):
    return {"t": t, "bid": D(str(bid)), "ask": D(str(ask)),
            "bid_qty": D(500), "ask_qty": D(500),
            "state": "MARKET_STATE_OPEN", "status": "OK"}


def rest_sides(intents):
    return sorted(str(i["side"]) for i in intents if i.kind == E.REST)


# ---- THE R5 CASE ------------------------------------------------------

def test_a_blocked_intent_is_re_emitted_on_the_next_identical_tick():
    """THE EXACT R5 FAILURE, reproduced.

    1. the engine emits an UP intent;
    2. the money path blocks it, so NOTHING is submitted and `live_orders`
       stays empty;
    3. the next identical book tick MUST emit the UP intent again, because
       no live UP order exists.

    Before the fix, step 3 emitted nothing: the engine had recorded its own
    request as resting.
    """
    eng = engine()
    live = {"up": None, "down": None}          # the money path submitted NOTHING

    eng.sync_resting(live)
    first = eng.on_book(book("0.45", "0.46"))
    assert "up" in rest_sides(first), "engine never asked for UP"

    # money path BLOCKED it -> live_orders unchanged
    eng.sync_resting(live)
    second = eng.on_book(book("0.45", "0.46"))
    assert "up" in rest_sides(second), (
        "PHANTOM ORDER: the engine suppressed UP against a quote it never "
        "placed -- this is the R5 defect")


def test_once_execution_owns_resting_the_engine_stops_recording_intents():
    """Ownership TRANSFERS on the first sync, and is scoped deliberately.

    The frozen replay harness drives this engine with no money path and no
    `live_orders` to adopt, so before any sync the engine keeps its own
    model -- otherwise frozen parity would change, and frozen parity is the
    reference rather than something to renegotiate. From the first
    `sync_resting` call on, the caller owns it.
    """
    eng = engine()
    assert eng._resting_owned_by_execution is False
    eng.on_book(book("0.45", "0.46"))
    assert eng.resting["up"] is not None, (
        "with NO execution layer the engine must keep its own model")

    owned = engine()
    owned.sync_resting({"up": None, "down": None})
    assert owned._resting_owned_by_execution is True
    owned.on_book(book("0.45", "0.46"))
    assert owned.resting["up"] is None, "on_book wrote resting state"
    assert owned.resting["down"] is None


def test_the_optimistic_write_is_guarded_not_merely_present():
    import inspect
    src = inspect.getsource(E.NativeEngine.on_book)
    i = src.index('self.resting[side] = {"px": px, "qty": self.clip}')
    assert "if not self._resting_owned_by_execution:" in src[max(0, i - 600):i]


# ---- duplicate protection is NOT weakened -----------------------------

def test_an_accepted_WORKING_order_suppresses_a_duplicate_intent():
    eng = engine()
    eng.sync_resting({"up": {"px": "0.45", "qty": "10"}, "down": None})
    out = eng.on_book(book("0.45", "0.46"))
    assert "up" not in rest_sides(out), (
        "a genuinely live UP order at this exact price must not be duplicated")
    assert "down" in rest_sides(out), "the other side must still be quoted"


def test_a_live_order_at_a_DIFFERENT_price_is_cancelled_then_requoted():
    eng = engine()
    eng.sync_resting({"up": {"px": "0.40", "qty": "10"}, "down": None})
    out = eng.on_book(book("0.45", "0.46"))
    assert any(i.kind == E.CANCEL and i["side"] == "up" for i in out)
    assert "up" in rest_sides(out)


def test_a_terminal_order_clears_resting_state():
    eng = engine()
    eng.sync_resting({"up": {"px": "0.45", "qty": "10"}, "down": None})
    assert eng.resting["up"] is not None
    # the venue said terminal, so the money path cleared live_orders
    eng.sync_resting({"up": None, "down": None})
    assert eng.resting["up"] is None
    out = eng.on_book(book("0.45", "0.46"))
    assert "up" in rest_sides(out), "a terminal order must not suppress a re-quote"


# ---- UNKNOWN / UNCERTAIN ownership stays fail-closed ------------------

def test_an_unknown_order_never_reaches_the_engine_at_all():
    """UNKNOWN halts the tick BEFORE the engine is consulted, so it can
    never be mistaken for an absent order."""
    import inspect
    src = inspect.getsource(MP.NativeMoneyPath.on_book_state)
    assert src.index('"refused": "ORDER_STATE_UNKNOWN"') < src.index("sync_resting")
    assert src.index("sync_resting") < src.index("self.eng.on_book(")


def test_uncertain_ownership_still_blocks_replacement():
    """The money path's own gate is unchanged and is the authority."""
    from src import pmus_order_lifecycle as LC
    mp = MP.NativeMoneyPath.__new__(MP.NativeMoneyPath)
    mp.venue = type("V", (), {"order_status": lambda s, oid, requested_qty=None,
                              side=None: {"classification": LC.UNKNOWN,
                                          "terminal": False, "why": "404"}})()
    mp.journal = None
    mp.timing = {}
    mp._cancel_failures = {}
    mp._resolved_this_pass = set()
    mp._terminal_verdicts = {}
    mp._status_reads = 0
    mp._status_cache_hits = 0
    mp.registry = MP.SessionOrderRegistry()
    mp.registry.record("A", side="up", price=D("0.46"), qty=D(10), kind=E.REST)
    mp.release_terminal = lambda *a, **k: None
    v = mp._cancel_failure_verdict("up", "A", {"state": "CANCEL_FAILED"})
    assert v["clear"] is False
    assert v["ownership"] == "UNCERTAIN"
    assert mp.registry.open_reserved == D("4.60")   # money still charged


def test_sync_ignores_a_missing_or_empty_mapping():
    eng = engine()
    eng.sync_resting({"up": {"px": "0.45", "qty": "10"}, "down": None})
    eng.sync_resting({})
    assert eng.resting == {"up": None, "down": None}


# ---- the production wiring exists ------------------------------------

def test_the_money_path_syncs_from_live_orders_before_deciding():
    import inspect
    src = inspect.getsource(MP.NativeMoneyPath.on_book_state)
    assert "self.eng.sync_resting(self.live_orders)" in src


def test_a_filled_order_is_removed_from_live_orders():
    """A filled order left in live_orders is the same phantom in a different
    costume: the engine would suppress a re-quote at a price nothing sits
    at any more."""
    import inspect
    src = inspect.getsource(MP.NativeMoneyPath.resolve_open_orders)
    seg = src.split('if v.get("terminal"):')[1][:900]
    assert "_cur = self.live_orders.get(row[\"side\"])" in seg
    assert "self.live_orders[row[\"side\"]] = None" in seg


def test_frozen_parameters_untouched():
    assert E.THETA == D("0.96")
    assert E.VARIANT == "B"
    assert float(E.HOLD_S) == 60.0
    assert E.RESIDUAL_CAP_K == D(2)
    assert E.CLIP == D(50)
