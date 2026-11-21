"""ONE canonical view of what actually executed, for every consumer.

WHY THIS EXISTS. CONTINUOUS-8 proved the feedback loop and exposed the last
seam while doing it. The coherence gate could explain an order's
disappearance from `fills u trades u terminal_seen`, while `CP.recover` built
inventory from `fills` ALONE. At C8B read 13 -- 2.64 seconds after a real
maker fill -- `/v1/mytrades` already carried the execution and
`order_history(filled)` did not, so the gate said `coherent: True`,
CORRECTLY, while the inventory it handed forward still read flat. A
replacement then went out against that flat inventory.

Nothing was corrupted: no decision ran on it, because the next `decide` used
the later read. But `coherent: True` alongside an inventory that omits the
very execution that made it coherent is a contradiction the system should not
be able to express, and the post-cancel refresh exists precisely to stop a
replacement being sized against stale zero inventory.

So both consumers now read THE SAME view, built here. `coherent` and the
inventory cannot disagree about whether an execution happened, because they
are no longer separate answers to that question.

THE TWO SOURCES ARE DIFFERENT SHAPES, AND THAT IS THE WHOLE DIFFICULTY.

  order_history(filled)  ONE ROW PER ORDER, carrying `filledQuantity` --
                         an aggregate. Has the venue's own `outcome`, so it
                         states the leg. Lags.
  /v1/mytrades           ONE ROW PER EXECUTION, carrying `tid`, `amount`,
                         `fee_amount` and `aggressor`. Faster. Carries NO
                         outcome field, so it does NOT state the leg.

Summing them double-counts: three partial trades plus the order row that
aggregates them is 2x the real quantity. So executions are grouped by ORDER
and the finer-grained source wins, with the aggregate used to CHECK it.

WHAT THIS REFUSES TO DO:

  * infer a leg from a client-order-id naming convention. Ours happen to
    contain "-yes-" and "-no-", and reading them would work right up until
    the day it silently did not. The leg comes from the venue's own row or
    from our own placement record, and an execution whose leg cannot be
    established is a CONTRADICTION, never a guess.
  * resolve a disagreement. Two sources that state different quantities for
    one order are reported and fail closed.
  * treat a terminal row as a fill. Cancelled and expired rows cannot enter
    here at all.
  * turn an unreadable source into an empty one.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: statuses that may contribute an execution. Anything else -- cancelled,
#: expired, closed, open -- is not a fill and cannot become one here.
FILL_STATUSES = ("filled", "partially_filled", "partiallyfilled")

#: statuses that must NEVER contribute, checked explicitly rather than left
#: to the absence of a match: a terminal row becoming a fill is the single
#: worst failure this module could have.
TERMINAL_STATUSES = ("cancelled", "canceled", "expired", "closed", "rejected")


def _dec(v: Any) -> Optional[D]:
    try:
        d = D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d


def _oid(v: Any) -> Optional[str]:
    """A venue order id as a STRING, whatever type the venue used.

    The listing returns `orderId` as an INT and the ack as a STRING for the
    same order -- the defect that cost CONTINUOUS-5 a window. Both normalize
    to the same key here.
    """
    if v in (None, "", 0) or isinstance(v, bool):
        return None
    if isinstance(v, float):
        return None
    s = str(v).strip()
    return s or None


def _leg_from_outcome(outcome: Any) -> Optional[str]:
    o = str(outcome or "").strip().lower()
    if o == "yes":
        return "YES"
    if o == "no":
        return "NO"
    return None


def _order_key(*ids: Any) -> Optional[str]:
    for i in ids:
        s = _oid(i)
        if s:
            return s
    return None


def canonical_executions(orders_seen: Any, trades: Any, *,
                         leg_of: Optional[Mapping[str, str]] = None,
                         symbol: Optional[str] = None,
                         fills: Any = None) -> Dict[str, Any]:
    """The executions the venue can actually evidence, each counted once.

    `orders_seen` is the filled-history row list (None = UNREADABLE).
    `trades` is the /v1/mytrades row list (None = UNREADABLE).
    `leg_of` maps an order id OR client order id to "YES"/"NO" from our own
    placement record, and is what lets a trades-only execution be sided
    before the slower source catches up.

    Readability: `orders_seen` unreadable makes the whole view unreadable,
    because that is the source a restart is rebuilt from and an unread venue
    is never an empty one. `trades` unreadable leaves the view usable and
    DEGRADED, recorded as such -- it can only make this view lag, never
    invent.
    """
    view: Dict[str, Any] = {
        "sources_readable": {"orders_seen": orders_seen is not None,
                             "trades": trades is not None},
        "symbol": symbol,
        "labels": ("ONE execution view. The coherence gate and inventory "
                   "recovery both read it, so `coherent` can never coexist "
                   "with an inventory that omits the execution that made it "
                   "coherent. Nothing here is REALIZED: an execution is a "
                   "cost basis, not a profit."),
    }
    if orders_seen is None and fills is not None:
        # LEGACY SHAPE, KEPT EXPLICIT. Some callers hand over the normalized
        # {leg, qty, price} list rather than the venue rows it was derived
        # from. Those carry no identifiers, so they cannot be deduplicated
        # against trades -- the quantity therefore comes from ONE source and
        # trade rows contribute identifiers only, never quantity. Recorded as
        # degraded, because a view that cannot cross-check is weaker than one
        # that can.
        exe = []
        for i, f in enumerate(fills or []):
            if not isinstance(f, Mapping):
                continue
            leg = str(f.get("leg") or "").upper()
            qty, price = _dec(f.get("qty")), _dec(f.get("price"))
            if leg not in ("YES", "NO") or qty is None or price is None:
                continue
            exe.append({"key": "fill:%d" % i, "leg": leg, "qty": str(qty),
                        "price": str(price), "fee": None,
                        "fee_currency": None, "maker": None, "utc_ms": None,
                        "order_id": None, "client_order_id": None,
                        "tid": None, "sources": ["fills"]})
        view.update({
            "readable": True, "executions": exe, "by_order": {},
            "contradictions": [], "n_executions": len(exe),
            "degraded": True,
            "extra_identifiers": sorted(_ids_of_rows(trades)),
            "why": ("built from the normalized fill list: it carries no "
                    "order identifiers, so nothing can be cross-checked "
                    "against the trade record and quantity comes from one "
                    "source only")})
        return view

    if orders_seen is None:
        view.update({"readable": False, "executions": [], "by_order": {},
                     "contradictions": [],
                     "why": ("filled order history is UNREADABLE; a restart "
                             "or a refresh never assumes flat")})
        return view

    # ---------------------------------------------------------------- trades
    # grouped by the ORDER they belong to, because that is the unit the
    # aggregate source also describes.
    per_order_trades: Dict[str, List[Dict[str, Any]]] = {}
    seen_tids: set = set()
    contradictions: List[Dict[str, Any]] = []
    for t in (trades or []):
        if not isinstance(t, Mapping):
            continue
        if symbol and t.get("symbol") not in (None, "", symbol):
            continue
        key = _order_key(t.get("order_id"), t.get("orderId"),
                         t.get("client_order_id"), t.get("clientOrderId"))
        if key is None:
            contradictions.append({
                "kind": "TRADE_WITHOUT_ORDER_IDENTITY",
                "why": ("a trade row carries no order id and no client order "
                        "id, so it cannot be attributed to an order or "
                        "deduplicated against the aggregate source"),
                "row": {k: t.get(k) for k in ("tid", "price", "amount")}})
            continue
        tid = _oid(t.get("tid"))
        # THE SAME EXECUTION TWICE IN ONE READ is dropped, not added. tid is
        # the venue's own execution identity and it is stable across reads.
        if tid is not None:
            if tid in seen_tids:
                continue
            seen_tids.add(tid)
        qty = _dec(t.get("amount") if t.get("amount") is not None
                   else t.get("qty"))
        price = _dec(t.get("price"))
        if qty is None or price is None or qty <= 0:
            contradictions.append({
                "kind": "TRADE_UNPRICEABLE", "order": key, "tid": tid,
                "why": "a trade row has no usable amount/price"})
            continue
        per_order_trades.setdefault(key, []).append({
            "tid": tid, "qty": qty, "price": price,
            "fee": _dec(t.get("fee_amount")),
            "fee_currency": t.get("fee_currency"),
            # aggressor False == we were the MAKER. Kept because maker/taker
            # is evidence about HOW the fill happened, and the program never
            # assumes it.
            "maker": (None if t.get("aggressor") is None
                      else not bool(t.get("aggressor"))),
            "utc_ms": t.get("timestampms") or t.get("timestamp_ms"),
            "client_order_id": _oid(t.get("client_order_id")
                                    or t.get("clientOrderId")),
            "order_id": _oid(t.get("order_id") or t.get("orderId")),
        })

    # ------------------------------------------------------------ aggregates
    per_order_rows: Dict[str, Dict[str, Any]] = {}
    legs: Dict[str, str] = {}
    for o in (orders_seen or []):
        if not isinstance(o, Mapping):
            continue
        status = str(o.get("status") or "").strip().lower()
        if status in TERMINAL_STATUSES:
            # A TERMINAL ROW CAN NEVER BECOME A FILL. Refused explicitly.
            continue
        if status and status not in FILL_STATUSES:
            continue
        key = _order_key(o.get("orderId"), o.get("globalOrderId"),
                         o.get("order_id"), o.get("clientOrderId"),
                         o.get("client_order_id"))
        if key is None:
            continue
        filled = _dec(o.get("filledQuantity") if o.get("filledQuantity")
                      is not None else o.get("filled_quantity"))
        if filled is None or filled <= 0:
            continue
        leg = _leg_from_outcome(o.get("outcome"))
        for alias in (key, _oid(o.get("clientOrderId")),
                      _oid(o.get("client_order_id")),
                      _oid(o.get("orderId")), _oid(o.get("globalOrderId"))):
            if alias and leg:
                legs[alias] = leg
        per_order_rows[key] = {"qty": filled, "price": _dec(o.get("price")),
                               "leg": leg, "status": status,
                               "utc": o.get("updatedAt") or o.get("createdAt"),
                               "client_order_id": _oid(o.get("clientOrderId")
                                                       or o.get("client_order_id"))}

    # our own placement record is consulted only where the venue row has not
    # arrived. It is authoritative about what WE asked for, which is what a
    # leg is.
    for k, v in (leg_of or {}).items():
        s = _oid(k)
        lv = str(v or "").upper()
        if s and lv in ("YES", "NO"):
            legs.setdefault(s, lv)

    # ------------------------------------------------------------- reconcile
    executions: List[Dict[str, Any]] = []
    by_order: Dict[str, Dict[str, Any]] = {}
    for key in sorted(set(per_order_trades) | set(per_order_rows)):
        ts = per_order_trades.get(key) or []
        row = per_order_rows.get(key)
        coid = None
        for t in ts:
            coid = coid or t.get("client_order_id")
        if row:
            coid = coid or row.get("client_order_id")
        leg = legs.get(key) or (legs.get(coid) if coid else None)
        if row and row.get("leg"):
            leg = row["leg"]

        if ts:
            total = sum((t["qty"] for t in ts), D("0"))
            # CROSS-CHECK, NOT A SUM. When the aggregate has caught up it must
            # AGREE with the executions; if it does not, the two sources
            # disagree about what happened and that fails closed.
            if row is not None and row["qty"] != total:
                contradictions.append({
                    "kind": "QUANTITY_DISAGREEMENT", "order": key,
                    "trades_total": str(total),
                    "order_history_filled": str(row["qty"]),
                    "why": ("the venue's trade rows and its own filled-order "
                            "row state different quantities for one order; "
                            "neither is preferred and nothing is summed")})
                continue
            if leg is None:
                contradictions.append({
                    "kind": "LEG_UNRESOLVED", "order": key,
                    "client_order_id": coid,
                    "why": ("an execution exists but no source states which "
                            "outcome it bought; a leg is never inferred from "
                            "a client-order-id naming convention")})
                continue
            for t in ts:
                executions.append({
                    "key": ("tid:%s" % t["tid"]) if t["tid"]
                    else "order:%s" % key,
                    "leg": leg, "qty": str(t["qty"]), "price": str(t["price"]),
                    "fee": (None if t["fee"] is None else str(t["fee"])),
                    "fee_currency": t.get("fee_currency"),
                    "maker": t["maker"], "utc_ms": t["utc_ms"],
                    "order_id": t.get("order_id") or key,
                    "client_order_id": coid, "tid": t["tid"],
                    "sources": (["trades", "order_history"] if row
                                else ["trades"]),
                })
            by_order[key] = {"qty": str(total), "n_executions": len(ts),
                             "leg": leg,
                             "sources": (["trades", "order_history"] if row
                                         else ["trades"])}
        elif row is not None:
            if leg is None:
                contradictions.append({
                    "kind": "LEG_UNRESOLVED", "order": key,
                    "client_order_id": coid,
                    "why": "a filled order row states no usable outcome"})
                continue
            executions.append({
                "key": "order:%s" % key, "leg": leg, "qty": str(row["qty"]),
                "price": (None if row["price"] is None else str(row["price"])),
                "fee": None, "fee_currency": None, "maker": None,
                "utc_ms": None, "order_id": key, "client_order_id": coid,
                "tid": None, "sources": ["order_history"],
            })
            by_order[key] = {"qty": str(row["qty"]), "n_executions": 1,
                             "leg": leg, "sources": ["order_history"]}

    view.update({
        "readable": not contradictions,
        "executions": executions,
        "by_order": by_order,
        "contradictions": contradictions,
        "n_executions": len(executions),
        "degraded": trades is None,
        "why": (None if not contradictions else
                "the execution sources contradict each other; the state is "
                "not established and nothing is decided on it"),
    })
    return view


def _ids_of_rows(rows: Any) -> set:
    """Every identifier a set of venue rows carries, whatever key it used."""
    out: set = set()
    for r in (rows or []):
        if not isinstance(r, Mapping):
            continue
        for k in ("order_id", "orderId", "globalOrderId", "client_order_id",
                  "clientOrderId", "tid"):
            v = _oid(r.get(k))
            if v:
                out.add(v)
    return out


def identifiers(view: Mapping[str, Any]) -> set:
    """Every identifier the canonical view can evidence an execution for.

    This is what the coherence gate matches a disappeared order against, so
    it must carry the same aliases the venue uses in different places.
    """
    out: set = set()
    for k, v in (view.get("by_order") or {}).items():
        out.add(str(k))
    for e in (view.get("executions") or []):
        for k in ("order_id", "client_order_id", "tid"):
            if e.get(k):
                out.add(str(e[k]))
    out |= {str(x) for x in (view.get("extra_identifiers") or [])}
    return out


def as_fills(view: Mapping[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """The executions in the shape inventory recovery consumes.

    None when the view is unreadable -- UNREADABLE is not empty, and a
    refresh that cannot establish what executed does not get to call the
    state flat.
    """
    if not view.get("readable"):
        return None
    return [{"leg": e["leg"], "qty": e["qty"], "price": e["price"]}
            for e in (view.get("executions") or [])
            if e.get("price") is not None]
