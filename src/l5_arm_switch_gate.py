"""The blocked-arm switch gate: may this process be restarted into the other
completion mode right now? READ-ONLY. It decides nothing about money.

WHY A PREDICATE AND NOT A CHECKLIST. `L5_TS_COMPLETION_AT_TOUCH` is read
once, via `os.getenv`, at `l5_live_execution.py:7303`, and no in-process
mutation of it exists anywhere in the tree. A Railway variable change
therefore takes effect only on restart, so comparing a control arm against
an at-touch arm requires a RESTART BETWEEN THEM and cannot be interleaved
per window. That restart is the whole risk: anything still live or
unresolved when the process goes down crosses a configuration boundary, and
on the other side the engine is running a different completion policy than
the one that created it.

A prose checklist is not evidence that nothing crossed. This is the same
question asked as code, so it can be evaluated, logged with its reasons, and
regression-tested.

WHAT "RETIRED" MEANS HERE, and it is strict. `retired_markets` is written by
exactly one function, `retire_settled_market`, which refuses on every one of:
ALREADY_RETIRED, LIVE_ORDERS_IN_MARKET, UNBOOKED_TERMINAL_ORDERS, an
incoherent book, a settlement price that is not exactly 0 or exactly 1 (a
mark is not cash), and a resolution that is absent, from another market, or
disagreeing with the value booked. So a market in `retired_markets` is a
CONFIRMED venue settlement by construction, and it persists, so a restart
cannot resurrect it.

`markets_with_exposure()` is the broad net: it unions `committed`,
`positions`, `leg_cost`, `live_orders` and `pending_terminal`, then subtracts
`retired_markets`. Empty means nothing anywhere in the book is unresolved --
which subsumes residual inventory, adopted venue positions and parked
orders. The named-window check is kept BESIDE it rather than folded into it,
because an empty exposure list is consistent with "the window was never
traded", and the owner asked for confirmed settlement of the windows that
WERE.

FAIL CLOSED. An unreadable store, an unreadable key or an exception is a
blocker, never a pass. There is one path to ok.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: the row-books that must be empty. Each is written by a different path and
#: each holds an order whose fate is NOT YET SETTLED, so any one of them
#: non-empty means unresolved execution state would cross the restart.
#:
#:   live_orders       -- resting and still fillable
#:   pending_terminal  -- left the open list and still owes this market a fill
#:   quarantine        -- an order whose fate was deliberately set aside
ORDER_BOOKS = ("live_orders", "pending_terminal", "quarantine")

#: `submitted` IS NOT ONE OF THEM, and the first production emission is why.
#: It came back with 22,848 entries while live_orders, pending_terminal,
#: quarantine and markets_with_exposure were ALL empty -- so the gate
#: reported SUBMITTED_NOT_EMPTY and could never have passed again.
#:
#: `submitted` is an append-only IDEMPOTENCY ledger, not a set of open
#: orders. `record_submit` writes it for every client order id ever sent,
#: nothing anywhere pops or deletes from it, it is the backing store for
#: `already_submitted(coid)` duplicate suppression, and it lives on a volume
#: that outlives the process. Its size is the account's lifetime submission
#: count.
#:
#: Nothing is lost by excluding it. `record_submit` writes `submitted` and
#: `live_orders` in the SAME call before a single save, so a coid cannot
#: exist in one without the other; an order that leaves `live_orders`
#: unresolved lands in `pending_terminal`; and a set we cannot enumerate at
#: all is caught by `orders_uncertain`. It is still REPORTED as evidence,
#: because an operator may want the number -- it just cannot block.
EVIDENCE_ONLY_BOOKS = ("submitted",)


def _state(store: Any) -> Optional[Mapping[str, Any]]:
    st = getattr(store, "state", None)
    return st if isinstance(st, Mapping) else None


def switch_readiness(store: Any, *,
                     windows: Sequence[str] = (),
                     halt_asserted: Optional[bool] = None,
                     ) -> Dict[str, Any]:
    """May the process be restarted into the other arm? `ok` or named blockers.

    `windows` are the markets the completed arm worked; each must be
    CONFIRMED settled and retired. `halt_asserted` is passed in rather than
    read here, because the latch is owned by `HaltGate` over an
    owner-controlled object and this module must not acquire a second,
    divergent reading of it. None means UNESTABLISHED, which blocks.
    """
    blockers: List[str] = []
    evidence: Dict[str, Any] = {}

    # 1. the latch. Not read here on purpose; an unestablished answer blocks.
    if halt_asserted is not True:
        blockers.append("HALT_NOT_ASSERTED" if halt_asserted is False
                        else "HALT_STATE_UNESTABLISHED")
    evidence["halt_asserted"] = halt_asserted

    st = _state(store)
    if st is None:
        blockers.append("STORE_UNREADABLE")
        return {"ok": False, "blockers": sorted(set(blockers)),
                "evidence": evidence,
                "labels": _LABELS}

    # 2. the order books, each for its own reason.
    for book in ORDER_BOOKS:
        try:
            rows = st.get(book) or {}
            n = len(rows) if isinstance(rows, Mapping) else 0
            keys = sorted(str(k) for k in rows)[:8] if isinstance(
                rows, Mapping) else []
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            blockers.append("ORDER_BOOK_UNREADABLE:%s" % book)
            continue
        evidence[book] = {"count": n, "keys": keys}
        if n:
            blockers.append("%s_NOT_EMPTY" % book.upper())

    # 2b. reported, never blocking. See EVIDENCE_ONLY_BOOKS.
    for book in EVIDENCE_ONLY_BOOKS:
        try:
            rows = st.get(book) or {}
            evidence[book] = {
                "count": len(rows) if isinstance(rows, Mapping) else 0,
                "blocking": False,
                "note": ("append-only idempotency ledger of every client "
                         "order id ever submitted; never pruned, so its size "
                         "is a lifetime count and not open-order state")}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            evidence[book] = {"count": None, "blocking": False,
                              "error": type(exc).__name__}

    # 3. the executor's own uncertainty flag. A restart while the order set
    #    is uncertain is the one case where we cannot even enumerate what
    #    would cross.
    try:
        why = store.orders_uncertain() if hasattr(
            store, "orders_uncertain") else (st.get("orders_uncertain") or None)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        why = "UNREADABLE:%s" % type(exc).__name__
    evidence["orders_uncertain"] = why
    if why:
        blockers.append("ORDERS_UNCERTAIN")

    # 4. nothing unresolved anywhere in the book.
    try:
        exposed = list(store.markets_with_exposure())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        blockers.append("EXPOSURE_UNREADABLE")
        exposed = None
    evidence["markets_with_exposure"] = exposed
    if exposed:
        blockers.append("EXPOSURE_OUTSTANDING")

    # 5. every window the finished arm worked is CONFIRMED settled and
    #    retired. Kept separate from (4) because an empty exposure list is
    #    also what a window that never traded looks like.
    retired = st.get("retired_markets")
    retired = retired if isinstance(retired, Mapping) else {}
    per_window: Dict[str, Any] = {}
    for m in windows:
        rec = retired.get(str(m))
        if not isinstance(rec, Mapping):
            per_window[str(m)] = {"retired": False,
                                  "why": "NOT_IN_RETIRED_MARKETS"}
            blockers.append("WINDOW_NOT_RETIRED")
            continue
        # the record must carry the settlement, not merely the key: a
        # retirement with no settlement figure is not a confirmed one.
        has = ("settlement_pnl" in rec) or ("yes_value" in rec)
        per_window[str(m)] = {"retired": True, "t": rec.get("t"),
                              "settlement_recorded": bool(has)}
        if not has:
            blockers.append("WINDOW_RETIRED_WITHOUT_SETTLEMENT_RECORD")
    if windows:
        evidence["windows"] = per_window

    return {"ok": not blockers, "blockers": sorted(set(blockers)),
            "evidence": evidence, "labels": _LABELS}


_LABELS = (
    "READ-ONLY. ok True means no order and no unresolved execution state "
    "would cross a restart, and every named window of the finished arm is a "
    "CONFIRMED venue settlement -- retired_markets is written only by "
    "retire_settled_market, which refuses on a live order, an unbooked "
    "terminal order, an incoherent book, a settlement price that is not "
    "exactly 0 or 1, and a resolution that is absent, foreign or "
    "disagreeing. An unestablished answer is a blocker, never a pass."
)
