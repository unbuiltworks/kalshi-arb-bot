"""P0: TERMINAL-STATE OBSERVATION, regression-tested against CI15M-4.

THE EVENT THESE TESTS EXIST FOR. On 2026-10-03 order 145828836725658335
was acked `open` at 21:34:17.262Z and FILLED q1 @ 0.68 at 21:34:42.776Z --
25.514s later, inside a ~90s/12-poll budget. The engine nevertheless halted
at 21:35:49.794Z claiming SEMANTIC_CONTRADICTION, recorded the window as
NO_FILL and the confirmed realized principal loss as "0", against a real
$0.68 principal that settled worthless (resolutionSide "no", settlement
84689.7985 vs strike 84695.55).

THE CAUSE WAS A CONSUMER IGNORING A DOCUMENTED PRODUCER CONTRACT, for the
fourth time in this program. `poll_terminal` asked order_history for
("filled", "cancelled", "open"). The producer's own docstring says:

    "open" IS NOT A VALID STATUS HERE. The venue answers 400 InvalidStatus,
    "Use /orders/active for open orders" -- measured 2026-09-18.

The `open` bucket 400'd. The producer's top-level `readable` is `not
unreadable` ACROSS ALL BUCKETS, so the whole read reported UNREADABLE and
`OrderHistory.of` discarded the `filled` bucket that had returned 200
CARRYING THE ROW -- twelve times.

So these tests pin the DISTINCTIONS, not the timeout. Nothing here may
conclude a non-fill from an absence, and an absence that cannot be resolved
HALTs as UNKNOWN.

NO STRATEGY PARAMETER IS EXERCISED OR CHANGED HERE: not .96, .05, .90, q1,
NATIVE_UP, Treatment T, the inventory caps, or the policy fingerprint.
"""
from __future__ import annotations

import asyncio
import importlib
from decimal import Decimal as D

import pytest

from src.gemini_execution import ci15m_adapters as AD
from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import metadata
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY
from src.gemini_execution.continuous_probe import COMPLETE, EXTEND, HOLD, OPEN

CID = "CI15M-TERMOBS"
TICKER = "BTC15M2610032145"
SYMBOL = "GEMI-%s-UP" % TICKER
OID = "145828836725658335"

# The CI15M-4 order, as the venue's own history row reported it.
FILLED_ROW = {"orderId": int(OID), "hashOrderId": "Klj272AexooO",
              "symbol": SYMBOL, "side": "buy", "outcome": "yes",
              "status": "filled", "orderType": "limit",
              "timeInForce": "good-til-cancel", "quantity": "1",
              "filledQuantity": "1", "remainingQuantity": "0",
              "price": "0.68", "avgExecutionPrice": "0.68",
              "createdAt": "2026-10-03T21:34:17.262Z",
              "updatedAt": "2026-10-03T21:34:42.776Z"}
# The authenticated trade row. The fee is whatever the venue says; it is
# never modelled, and absent is UNKNOWN rather than zero.
TRADE = {"tid": 9911001, "order_id": OID, "symbol": SYMBOL, "price": "0.68",
         "amount": "1", "fee_amount": "0.02", "aggressor": True,
         "timestampms": 1791063282776}


async def _nosleep(_s):
    return None


def _bucket(status, rows, *, http=200, unreadable=False):
    """One per_status entry in the PRODUCER'S shape."""
    if unreadable:
        return {"status_filter": status, "http": http, "rows": None,
                "unreadable": True, "detail": {"reason": "InvalidStatus"}}
    return {"status_filter": status, "http": http, "rows": len(rows),
            "page_is_full": False, "orders": list(rows)}


def _hist(buckets):
    """The producer's envelope. `readable` is its ACROSS-ALL-BUCKETS flag,
    so it is False whenever any bucket failed -- that is the trap."""
    bad = [b["status_filter"] for b in buckets if b.get("unreadable")]
    total = sum(b.get("rows") or 0 for b in buckets)
    return {"readable": not bad,
            "verdict": ("UNREADABLE" if bad
                        else ("ORDERS_FOUND" if total
                              else "NO_ORDERS_ON_RECORD")),
            "path": metadata.ORDER_HISTORY_PATH, "symbol": SYMBOL,
            "total_rows": total, "unreadable_statuses": bad,
            "per_status": buckets}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [0],
                                        "count": 1, "why": None})
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    BUD.open_window(CID, TICKER, root=root)
    return root


def _durable_open(root, price="0.68"):
    return BUD.consume_slot(CID, TICKER, {
        "action": "OPEN", "side": "UP", "outcome": "yes", "price": price,
        "quantity": "1", "unmatched_shares_after": "1",
        "unmatched_principal_after": price, BUD.RECONCILED_KEY: "0"},
        root=root, remote=None)


def _tape(monkeypatch, sequence, *, active=None, trades=(TRADE,)):
    """Feed order-history envelopes in order, last one repeating.

    `active` is what /orders/active reports: an int count, or None for an
    unreadable read.
    """
    seen = {"n": 0}

    async def hist(**kw):
        assert kw.get("include_rows") is True, (
            "a caller reconstructing inventory MUST ask for rows; an absent "
            "row list is not an empty one")
        assert "open" not in tuple(kw.get("statuses") or ()), (
            "`open` is not a valid order-history status -- the venue answers "
            "400 InvalidStatus and says to use /orders/active")
        i = min(seen["n"], len(sequence) - 1)
        seen["n"] += 1
        return sequence[i]

    async def act(symbol):
        if active is None:
            return {"ok": False, "status": None, "classification": None}
        return {"ok": True, "status": 200, "classification": "ACCEPTED",
                "body": {"orders": [] if not active else [
                    dict(FILLED_ROW, status="open", filledQuantity="0",
                         remainingQuantity="1")]}}

    async def my(sym):
        return {"rows": list(trades), "status": 200}

    async def pos():
        return {"ok": True, "rows": [], "status": 200, "total": 0}

    from src.gemini_execution import transport
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(transport, "active_orders", act)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", pos)
    return seen


# =====================================================================
# THE EXACT CI15M-4 SEQUENCE: OPEN @ .68 -> open -> open -> FILLED @ .68
# =====================================================================
CI15M4_POISONED_OPEN_BUCKET = _hist([
    _bucket("filled", []), _bucket("cancelled", []),
    _bucket("open", [], unreadable=True, http=400)])
CI15M4_RESTING = _hist([_bucket("filled", []), _bucket("cancelled", [])])
CI15M4_FILLED = _hist([_bucket("filled", [FILLED_ROW]),
                       _bucket("cancelled", [])])


def _poll(root, sequence, *, active=1, attempts=12, monkeypatch=None,
          trades=(TRADE,)):
    _tape(monkeypatch, sequence, active=active, trades=trades)
    return asyncio.run(P.poll_terminal(CID, SYMBOL, OID, attempts=attempts,
                                       pause=0, root=root,
                                       sleeper=_nosleep))


def test_1_the_ci15m4_sequence_produces_no_semantic_contradiction(env,
                                                                  monkeypatch):
    """(1) no SEMANTIC_CONTRADICTION, and (2) the fill is seen in budget."""
    root = env
    _durable_open(root)
    term = _poll(root, [CI15M4_RESTING, CI15M4_RESTING, CI15M4_FILLED],
                 monkeypatch=monkeypatch)
    assert term["resolved"] is True
    assert term["state"] == P.TERMINAL_FILLED
    assert term["filled"] == D("1")
    assert D(term["row"]["price"]) == D("0.68")
    # (2) recognised BEFORE the budget is spent
    assert term["attempts"] == 3 < 12
    st = BUD.state(CID, root=root)
    assert st["halted"] is None, "a fill in budget must not halt"


def test_1b_a_poisoned_open_bucket_can_no_longer_discard_a_filled_row(
        env, monkeypatch):
    """THE EXACT FAILING READ. Even if some caller still asks for a bucket
    the venue rejects, a 400 on it may not discard a 200 that carries the
    row. This is the regression that cost the order."""
    root = env
    _durable_open(root)
    poisoned_but_filled = _hist([
        _bucket("filled", [FILLED_ROW]), _bucket("cancelled", []),
        _bucket("open", [], unreadable=True, http=400)])
    # the PRODUCER still reports the whole read as unreadable ...
    assert poisoned_but_filled["readable"] is False
    assert poisoned_but_filled["verdict"] == "UNREADABLE"
    # ... and the OLD type still mirrors that, which is why it lost the row
    assert AD.OrderHistory.of(poisoned_but_filled).readable is False
    assert AD.OrderHistory.of(poisoned_but_filled).find(OID) is None
    # ... while the per-bucket view keeps the evidence it DID get
    hr = AD.HistoryRead.of(poisoned_but_filled)
    assert hr.any_readable is True and hr.fully_readable is False
    assert hr.unreadable_statuses == ("open",)
    assert hr.find(OID) is not None and hr.find(OID).terminal
    term = _poll(root, [poisoned_but_filled], monkeypatch=monkeypatch)
    assert term["resolved"] and term["state"] == P.TERMINAL_FILLED
    assert term["unreadable_statuses"] == ["open"]


def test_3_and_4_inventory_becomes_up_q1_at_068_and_lifecycle_is_filled(
        env, monkeypatch):
    """(3) inventory UP q1 / cost $0.68, (4) OPEN lifecycle -> FILLED,
    (7) the risk ledger carries the $0.68 principal exposure."""
    root = env
    _durable_open(root)
    _tape(monkeypatch, [CI15M4_FILLED], active=0)
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=root))
    assert rec["ok"], rec
    # (3) rebuilt from the AUTHENTICATED fill record
    inv = rec["inventory"]
    assert rec["signed"] == D("1")
    assert inv.q_up == D("1")
    assert inv.c_up == D("0.68")
    assert inv.avg_up == D("0.68")
    assert inv.q_down == D("0")
    # (4) the durable OPEN lifecycle
    BUD.record_status(CID, TICKER, slot=0, status="FILLED",
                      detail={"price": "0.68", "quantity": "1"}, root=root)
    assert BUD.open_state(CID, TICKER, root=root) == BUD.OPEN_FILLED
    # (7) principal exposure is on the risk ledger, not merely in memory
    st = BUD.state(CID, root=root)
    assert D(st["max_unmatched_principal"]) == D("2.70")
    principal = inv.residual * inv.avg_up
    assert inv.residual == D("1") and principal == D("0.68")
    assert principal <= D(st["max_unmatched_principal"])
    # and the AUTHORIZED durable intent carries the same price and size
    intents = P.authorized_intents(CID, root=root)
    assert intents and intents[0]["price"] == "0.68"
    assert intents[0]["quantity"] == "1" and intents[0]["action"] == "OPEN"


def test_5_no_duplicate_open_after_the_fill(env, monkeypatch):
    """(5) the OPEN lifecycle blocks a second first leg."""
    root = env
    _durable_open(root)
    BUD.record_status(CID, TICKER, slot=0, status="FILLED",
                      detail={"price": "0.68", "quantity": "1"}, root=root)
    assert BUD.open_state(CID, TICKER, root=root) == BUD.OPEN_FILLED
    assert BUD.open_state(CID, TICKER, root=root) != BUD.OPEN_NOT_ATTEMPTED
    # one slot spent, and the window is not re-openable as a fresh OPEN
    assert BUD.consumed(CID, root=root) == 1


def test_6_next_frozen_decision_is_taken_from_q1_up_inventory(env,
                                                              monkeypatch):
    """(6) the next frozen-policy decision is evaluated from UP q1 @ .68.

    The policy is READ, never altered. Its fingerprint is asserted unchanged.
    """
    root = env
    _durable_open(root)
    _tape(monkeypatch, [CI15M4_FILLED], active=0)
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=root))
    assert rec["ok"], rec
    inv = rec["inventory"]
    assert inv.q_up == D("1") and inv.avg_up == D("0.68")
    # The complement must be reachable at the FROZEN .96 pair ceiling.
    req = POLICY["required_complement_price"](inv, QUANTITY)
    assert req == D("0.28"), "0.96 - 0.68, the frozen ceiling"
    # a DOWN ask at/below .28 completes; above it the policy must not.
    dec = POLICY["decide_once"](inv, bid=D("0.57"), ask=D("0.68"),
                                bid_size=D("350"), ask_size=D("350"),
                                tte=620.0, qty=QUANTITY, n_extends=0,
                                actions=1)
    assert dec["action"] in (OPEN, EXTEND, COMPLETE, HOLD)
    assert dec["action"] != OPEN, (
        "inventory is no longer flat: a second first leg is not a decision "
        "the engine may take here")


def test_8_a_losing_settlement_cannot_remain_recorded_as_no_fill(env,
                                                                 monkeypatch):
    """(8) the very substitution that happened. Once the authenticated
    record shows a fill, the window may not stay NO_FILL and the principal
    may not stay zero."""
    root = env
    _durable_open(root)
    # the live process's WRONG record, reproduced exactly
    BUD.close_window(CID, TICKER, "NO_FILL", root=root)
    assert BUD.windows(CID, root=root)[TICKER] == "NO_FILL"
    # the authenticated record disagrees
    _tape(monkeypatch, [CI15M4_FILLED], active=0)
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=root))
    assert rec["ok"] and rec["signed"] == D("1"), (
        "the venue says a share was bought")
    # the corrected outcome must be representable and must win
    BUD.close_window(CID, TICKER, "FILLED",
                     detail={"corrected_from": "NO_FILL",
                             "source": "authenticated fill record"},
                     root=root)
    assert BUD.windows(CID, root=root)[TICKER] == "FILLED"
    assert BUD.windows(CID, root=root)[TICKER] != "NO_FILL"


def test_9_restart_after_the_fill_reconstructs_the_same_inventory(env,
                                                                 monkeypatch):
    """(9) a fresh process rebuilds UP q1 @ .68 from the venue record."""
    root = env
    _durable_open(root)
    _tape(monkeypatch, [CI15M4_FILLED], active=0)
    first = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=root))
    assert first["ok"], first

    importlib.reload(BUD)
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [0],
                                        "count": 1, "why": None})
    _tape(monkeypatch, [CI15M4_FILLED], active=0)
    again = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=root))
    assert again["ok"], again
    assert again["reconstruction"] == first["reconstruction"]
    assert (again["reconstruction"]["up_qty"],
            again["reconstruction"]["up_cost"]) == ("1", "0.68")


# =====================================================================
# THE OTHER TERMINAL SHAPES. Each decided on its OWN evidence.
# =====================================================================
def test_open_then_cancelled_is_a_terminal_no_fill(env, monkeypatch):
    root = env
    _durable_open(root)
    dead = dict(FILLED_ROW, status="cancelled", filledQuantity="0",
                remainingQuantity="1")
    term = _poll(root, [_hist([_bucket("filled", []),
                               _bucket("cancelled", [dead])])],
                 active=0, monkeypatch=monkeypatch)
    assert term["resolved"] and term["state"] == P.TERMINAL_CANCELLED
    assert term["filled"] == D("0")
    assert term["partial"] is False


def test_open_then_expired_is_terminal_and_labelled_expired(env, monkeypatch):
    root = env
    _durable_open(root)
    gone = dict(FILLED_ROW, status="expired", filledQuantity="0",
                remainingQuantity="1")
    # `expired` is not in the routine buckets, so this exercises the
    # LAST-CHANCE lookup rather than the hot path.
    term = _poll(root, [CI15M4_RESTING,
                        _hist([_bucket("filled", []),
                               _bucket("cancelled", []),
                               _bucket("expired", [gone])])],
                 active=0, attempts=1, monkeypatch=monkeypatch)
    assert term["resolved"] and term["state"] == P.TERMINAL_EXPIRED
    assert term["filled"] == D("0") and term.get("from") == "last_chance"


def test_open_then_partial_then_cancelled_keeps_the_partial_quantity(
        env, monkeypatch):
    """A partial that later died is a FILL of the partial size. Recording it
    as a non-fill is exactly the error under repair, one share smaller."""
    root = env
    _durable_open(root, price="0.68")
    partial = dict(FILLED_ROW, status="cancelled", quantity="2",
                   filledQuantity="1", remainingQuantity="1")
    term = _poll(root, [_hist([_bucket("filled", []),
                               _bucket("cancelled", [partial])])],
                 active=0, monkeypatch=monkeypatch)
    assert term["resolved"] is True
    assert term["state"] == P.TERMINAL_FILLED, (
        "a dead order with filledQuantity > 0 is a FILL, not a non-fill")
    assert term["filled"] == D("1")


def test_open_then_api_failure_halts_as_unknown(env, monkeypatch):
    """A real read failure is UNKNOWN. It is never a non-fill."""
    root = env
    _durable_open(root)

    async def boom(**kw):
        raise RuntimeError("upstream connect error")

    async def act(symbol):
        return {"ok": True, "status": 200, "classification": "ACCEPTED",
                "body": {"orders": []}}
    from src.gemini_execution import transport
    monkeypatch.setattr(metadata, "order_history", boom)
    monkeypatch.setattr(transport, "active_orders", act)
    term = asyncio.run(P.poll_terminal(CID, SYMBOL, OID, attempts=2, pause=0,
                                       root=root, sleeper=_nosleep))
    assert term["resolved"] is False
    assert term["halt"] == "UNKNOWN_POSITION"
    assert term["unreadable_polls"] == 2
    st = BUD.state(CID, root=root)
    assert "UNKNOWN_POSITION" in str(st["halted"])
    assert "SEMANTIC_CONTRADICTION" not in str(st["halted"])
    assert BUD.windows(CID, root=root).get(TICKER) != "NO_FILL"


def test_disappearance_with_an_unreadable_terminal_lookup_halts_as_unknown(
        env, monkeypatch):
    """THE CASE THE OWNER NAMED LAST, and the one the old code got wrong:
    the order is gone from the open bucket and the terminal lookup cannot
    be read. That must HALT as UNKNOWN. It must NOT claim NO_FILL."""
    root = env
    _durable_open(root)
    all_buckets_dead = _hist([_bucket("filled", [], unreadable=True, http=503),
                              _bucket("cancelled", [], unreadable=True,
                                      http=503)])
    hr = AD.HistoryRead.of(all_buckets_dead)
    assert hr.any_readable is False, "no bucket readable is UNKNOWN"
    term = _poll(root, [all_buckets_dead], active=0, attempts=3,
                 monkeypatch=monkeypatch)
    assert term["resolved"] is False
    assert term["halt"] == "UNKNOWN_POSITION"
    assert term["unreadable_polls"] == 3
    st = BUD.state(CID, root=root)
    assert "UNKNOWN_POSITION" in str(st["halted"])
    # and the absence is NOT recorded as a non-fill anywhere
    assert BUD.windows(CID, root=root).get(TICKER) != "NO_FILL"
    assert "NOT_FILLED" not in str(st["halted"])


def test_disappearance_from_the_active_book_alone_is_not_evidence(env,
                                                                  monkeypatch):
    """Readable terminal buckets, order absent, active book empty: the
    engine keeps resolving and then fails closed as UNKNOWN -- it never
    concludes a non-fill from the disappearance itself."""
    root = env
    _durable_open(root)
    term = _poll(root, [CI15M4_RESTING], active=0, attempts=2,
                 monkeypatch=monkeypatch)
    assert term["resolved"] is False
    assert term["disappeared_from_active"] is True
    assert term["halt"] == "UNKNOWN_POSITION"
    assert term["unreadable_polls"] == 0, (
        "the surfaces were READABLE; the state was merely unresolved")


def test_resting_on_the_active_book_is_readable_nonterminal(env, monkeypatch):
    """RESTING is established on /orders/active -- the surface that carries
    it -- and is neither terminal nor a non-fill."""
    root = env
    _durable_open(root)
    term = _poll(root, [CI15M4_RESTING], active=1, attempts=2,
                 monkeypatch=monkeypatch)
    assert term["resolved"] is False
    assert term["saw_resting"] is True
    assert term["disappeared_from_active"] is False
    assert term["halt"] == "UNKNOWN_POSITION"


# =====================================================================
# THE CONTRACT ITSELF
# =====================================================================
def test_the_poller_never_asks_history_for_the_open_bucket():
    import inspect
    src = inspect.getsource(P.poll_terminal) + inspect.getsource(
        P._terminal_lookup)
    assert "TERMINAL_HISTORY_STATUSES" in src
    assert P.TERMINAL_HISTORY_STATUSES == ("filled", "cancelled")
    assert "open" not in P.TERMINAL_HISTORY_STATUSES
    assert "open" not in P.LAST_CHANCE_STATUSES


def test_the_producer_still_forbids_the_open_bucket():
    """If the venue ever accepts `open` here, this test should fail and be
    reconsidered deliberately rather than silently relied upon."""
    doc = metadata.order_history.__doc__ or ""
    assert "NOT A VALID STATUS" in doc
    assert "/orders/active" in doc


def test_the_frozen_policy_fingerprint_is_untouched_by_this_repair():
    from src.gemini_execution.ci15m_policy import fingerprint
    assert fingerprint() == (
        "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329")


# ---------------------------------------------------------------------------
# THE POST-FILL AUTHORITY. Found by the four-window shadow proof, and it
# would have stopped the first persistent live cohort on its FIRST FILL.
#
# `reconcile_after_fill` was the last call site still gating on
# `positions()`. That surface has NEVER carried a BTC intraday contract --
# established read-only on the CI15M-3 fill -- so it returns a SUCCESSFUL
# EMPTY read, and comparing that against a correct ledger of 1 produced
# INVENTORY_DISAGREEMENT every time: venue_representation FLAT_NO_ROWS,
# venue_signed 0, our_signed 1, halt.
#
# The authority is the AUTHENTICATED FILL RECORD. positions() stays read
# and reported, as telemetry.
# ---------------------------------------------------------------------------
def test_an_empty_positions_read_does_not_halt_a_reconciled_fill(env,
                                                                 monkeypatch):
    root = env
    _durable_open(root)
    _tape(monkeypatch, [CI15M4_FILLED], active=0)
    inv = P.new_inventory()
    inv.add(POLICY["make_fill"](0.0, "UP", "taker", D("0.68"), D("1"),
                                "live OPEN"))
    inv.assert_identities()
    rec = asyncio.run(P.reconcile_after_fill(CID, SYMBOL, inv, root=root))
    assert rec["halted"] is False, rec
    assert rec["verdict"] == "RECONCILED"
    assert rec["signed"] == D("1")
    assert "authenticated fill record" in rec["authority"]
    # positions WAS read, and is reported as telemetry rather than as a gate
    assert rec["positions_telemetry"]["read"] is True
    assert rec["positions_telemetry"]["n_rows"] == 0
    assert "TELEMETRY ONLY" in rec["positions_telemetry"]["labels"]
    assert BUD.halted(CID, root=root) is None


def test_the_post_fill_gate_no_longer_consults_the_position_surface():
    import inspect
    src = inspect.getsource(P.reconcile_after_fill)
    assert "reconciled_inventory" in src
    assert "reconcile_or_halt" not in src, (
        "the position-gated path must not decide the post-fill outcome")


def test_an_unreadable_fill_record_still_halts_after_a_fill(env,
                                                            monkeypatch):
    """The authority moved; fail-closed did not."""
    root = env
    _durable_open(root)
    dead = _hist([_bucket("filled", [], unreadable=True, http=503)])
    _tape(monkeypatch, [dead], active=0)
    inv = P.new_inventory()
    inv.add(POLICY["make_fill"](0.0, "UP", "taker", D("0.68"), D("1"),
                                "live OPEN"))
    rec = asyncio.run(P.reconcile_after_fill(CID, SYMBOL, inv, root=root))
    assert rec["halted"] is True
    assert BUD.halted(CID, root=root) is not None
