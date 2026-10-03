"""Venue boundary adapters: raw producer output -> explicit internal types.

WHY THIS MODULE EXISTS. Three live defects in three attempts, all the same
failure class -- a consumer guessing the shape its producer returns:

  1. the place ACK: `open` was read as NOT_FILLED, so four fills went
     unobserved and the exposure caps were evaluated against phantom-flat
     inventory (CI15M-1, $2.86 against a $2.70 cap);
  2. the active-orders body: `_orders_of` returns [] for any shape it does
     not recognise, and the consumer never checked `ok`, so a FAILED read
     was read as "zero outstanding orders" and four orders rested at once;
  3. `metadata.positions()`: the consumer looked for `positions`/`result`/
     `data`; the producer emits `rows`. A SUCCESSFUL read was discarded,
     UNKNOWN_POSITION halted the cohort, and CI15M-2 died before its first
     order.

Each of those was a guessed key, not a venue problem. So every venue
response the CI15M path consumes is normalised HERE, once, at the boundary,
into an explicit type -- and each adapter's contract is derived from the
PRODUCER'S OWN IMPLEMENTATION, with a test that reads the producer source so
the two cannot drift apart silently again.

THE DISTINCTION THESE TYPES EXIST TO PRESERVE, and the one every defect
above destroyed:

    "the venue says nothing is there"   is a FACT.
    "we could not find out"             is UNKNOWN.

They are different, they must never normalise to the same value, and
UNKNOWN must fail closed. `ok=True, rows=[]` is an empty account; anything
else is unknown.

SCOPE. The CI15M execution path only. This is not a rewrite of the Gemini
layer: the producers are untouched and every other caller keeps its current
behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------- statuses
FILLED = "FILLED"
CANCELLED = "CANCELLED"
RESTING = "RESTING"
UNRESOLVED = "UNRESOLVED"

#: venue order statuses that are TERMINAL. Anything else -- including an
#: ABSENT status -- is not, and `open` is emphatically not.
TERMINAL_FILLED_STATUSES = ("filled",)
TERMINAL_DEAD_STATUSES = ("cancelled", "canceled", "expired", "rejected",
                          "closed")
NONTERMINAL_STATUSES = ("open", "accepted", "pending", "partially_filled",
                        "partially filled")


def _dec(v: Any) -> Optional[D]:
    """A Decimal, or None. None means UNREADABLE, never zero."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ------------------------------------------------------------- positions
@dataclass(frozen=True)
class PositionRead:
    """`metadata.positions()` normalised.

    PRODUCER CONTRACT (src/gemini_execution/metadata.py::positions):
        {"path": str, "rows": list|None, "status": int|None, "ok": bool,
         "total": int|None, "why": str|None}
    and its docstring states `rows` is None whenever the read did not
    succeed -- an exception, a non-200, an unparseable body, or a payload
    whose `positions` key is not a list.
    """
    ok: bool
    rows: Optional[List[Mapping[str, Any]]]
    status: Optional[int] = None
    why: Optional[str] = None

    @property
    def readable(self) -> bool:
        return self.ok and self.rows is not None

    @property
    def is_empty_account(self) -> bool:
        """A FACT: the venue was read and holds no row. Not the same as
        unknown, and the only case a caller may treat as flat."""
        return self.readable and len(self.rows or []) == 0

    @classmethod
    def of(cls, got: Any) -> "PositionRead":
        if not isinstance(got, Mapping):
            return cls(False, None, None,
                       "positions read returned %s, not a mapping"
                       % type(got).__name__)
        if got.get("ok") is not True:
            return cls(False, None, got.get("status"),
                       got.get("why") or "the positions read did not succeed")
        rows = got.get("rows")
        if not isinstance(rows, list):
            return cls(False, None, got.get("status"),
                       "ok was True but `rows` is %s, not a list -- the "
                       "producer contract was not met"
                       % type(rows).__name__)
        return cls(True, [r for r in rows if isinstance(r, Mapping)],
                   got.get("status"), None)


# ------------------------------------------------------------ order state
@dataclass(frozen=True)
class OrderState:
    """One order's state, from an ACK or from order history.

    `state` is exactly one of FILLED / CANCELLED / RESTING / UNRESOLVED.
    RESTING is NOT terminal and NOT a non-fill: it is an order whose fate is
    still open, and reading it as NOT_FILLED is defect #1 above.
    """
    state: str
    status: Optional[str]
    filled: Optional[D]
    price: Optional[D]
    outcome: Optional[str]
    order_id: Optional[str]
    why: Optional[str] = None
    raw_keys: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def terminal(self) -> bool:
        return self.state in (FILLED, CANCELLED)

    @classmethod
    def of(cls, body: Any) -> "OrderState":
        """PRODUCER CONTRACT: a venue order object. Observed keys on this
        account (CI15M-1 order 145828836710181412): orderId, globalOrderId,
        clientOrderId, symbol, side, outcome, orderType, timeInForce, price,
        quantity, filledQuantity, remainingQuantity, status, createdAt,
        updatedAt, cancelledAt.
        """
        if not isinstance(body, Mapping):
            return cls(UNRESOLVED, None, None, None, None, None,
                       "order object is %s, not a mapping"
                       % type(body).__name__)
        keys = tuple(sorted(str(k) for k in body))
        st = str(body.get("status") or "").strip().lower()
        raw_filled = (body.get("filledQuantity")
                      if body.get("filledQuantity") is not None
                      else body.get("executedQuantity"))
        filled = _dec(raw_filled if raw_filled is not None else "0")
        oid = body.get("orderId") or body.get("id")
        oid = None if oid is None else str(oid)
        price = _dec(body.get("avgExecutionPrice")
                     or body.get("executedPrice") or body.get("price"))
        outcome = body.get("outcome")
        outcome = None if outcome is None else str(outcome).lower()
        if filled is None:
            return cls(UNRESOLVED, st or None, None, price, outcome, oid,
                       "filled quantity %r is unreadable" % (raw_filled,),
                       keys)
        if st in TERMINAL_FILLED_STATUSES or (st in TERMINAL_DEAD_STATUSES
                                              and filled > 0):
            return cls(FILLED, st, filled, price, outcome, oid, None, keys)
        if st in TERMINAL_DEAD_STATUSES:
            return cls(CANCELLED, st, filled, price, outcome, oid, None, keys)
        if st in NONTERMINAL_STATUSES:
            return cls(RESTING, st, filled, price, outcome, oid,
                       "the order is RESTING -- `%s` is not NOT_FILLED, and "
                       "its terminal state must be polled" % st, keys)
        return cls(UNRESOLVED, st or "(absent)", filled, price, outcome, oid,
                   "unrecognised or absent order status %r -- fail closed"
                   % st, keys)


# --------------------------------------------------------------- the ACK
@dataclass(frozen=True)
class PlaceAck:
    """`transport.place()` normalised.

    PRODUCER CONTRACT (src/gemini_execution/transport.py::_post):
        {"status": int|None, "body": any|None, "ok": bool,
         "classification": "ACCEPTED"|"REJECTED"|"AMBIGUOUS"|...,
         "unparseable": str|None}
    `place` additionally nests the venue's order object at body, or at
    body["order"] when the venue wraps it.
    """
    ok: bool
    classification: Optional[str]
    status: Optional[int]
    order: OrderState
    why: Optional[str] = None

    @property
    def ambiguous(self) -> bool:
        return self.classification == "AMBIGUOUS"

    @classmethod
    def of(cls, res: Any) -> "PlaceAck":
        if not isinstance(res, Mapping):
            return cls(False, None, None,
                       OrderState.of(None),
                       "place returned %s, not a mapping" % type(res).__name__)
        cl = res.get("classification")
        body = res.get("body")
        inner = (body.get("order") if isinstance(body, Mapping)
                 and isinstance(body.get("order"), Mapping) else body)
        if res.get("ok") is not True:
            return cls(False, cl, res.get("status"), OrderState.of(inner),
                       "the place did not succeed (classification %r)" % cl)
        if not isinstance(inner, Mapping):
            return cls(False, cl, res.get("status"), OrderState.of(None),
                       "the ack carried no readable order object")
        return cls(True, cl, res.get("status"), OrderState.of(inner), None)


# ------------------------------------------------------- active orders
@dataclass(frozen=True)
class ActiveOrders:
    """`transport.active_orders()` normalised.

    PRODUCER CONTRACT: the `_post` envelope above, plus `_orders_of`, which
    finds the list at body itself or under "orders" / "results" / "data" --
    AND RETURNS [] FOR ANY OTHER SHAPE. That silent [] is defect #2, so this
    adapter requires the shape to be RECOGNISED before it believes a count.
    """
    readable: bool
    orders: Tuple[OrderState, ...]
    why: Optional[str] = None

    @property
    def count(self) -> Optional[int]:
        return None if not self.readable else len(self.orders)

    @property
    def any_unresolved(self) -> bool:
        """Any order not in a terminal state blocks the next decision."""
        return any(not o.terminal for o in self.orders)

    @classmethod
    def of(cls, res: Any) -> "ActiveOrders":
        if not isinstance(res, Mapping):
            return cls(False, (), "active orders returned %s"
                                  % type(res).__name__)
        if res.get("ok") is not True:
            return cls(False, (),
                       "the active-orders read did not succeed (status %r, "
                       "classification %r); an unreadable order book is "
                       "never an empty one"
                       % (res.get("status"), res.get("classification")))
        body = res.get("body")
        rows: Optional[Sequence[Any]] = None
        if isinstance(body, list):
            rows = body
        elif isinstance(body, Mapping):
            for k in ("orders", "results", "data"):
                if isinstance(body.get(k), list):
                    rows = body[k]
                    break
        if rows is None:
            return cls(False, (),
                       "the active-orders shape was not recognised (body "
                       "%s, keys %s); an empty list we could not find is "
                       "not an empty order book"
                       % (type(body).__name__,
                          sorted(body)[:8] if isinstance(body, Mapping)
                          else None))
        return cls(True, tuple(OrderState.of(r) for r in rows
                               if isinstance(r, Mapping)), None)


# ------------------------------------------------------ executable book
@dataclass(frozen=True)
class ExecutableBook:
    """`metadata.contract_depth()` normalised: the EXECUTABLE ladder.

    PRODUCER CONTRACT (metadata.py::contract_depth):
        {"readable": bool, "bids": {"levels", "best", "best_qty",
         "qty_shown"}, "asks": {...}, "venue_ts", "snapshot_age_s", "why"}
    For a -UP contract these are the YES bid and ask sides.

    This is the ONLY price source for an order. The contract object's
    `prices.buy.yes` agreed with this ladder on 23.1% of 372 measured
    instants and is carried separately, as telemetry, by the caller.
    """
    readable: bool
    bid: Optional[D]
    ask: Optional[D]
    bid_size: Optional[D]
    ask_size: Optional[D]
    snapshot_age_s: Optional[float] = None
    why: Optional[str] = None
    crossed: bool = False

    def acquisition(self, side: str) -> Optional[D]:
        """What the ladder charges to BUY `side`. DOWN is synthesized."""
        if not self.readable:
            return None
        if side == "UP":
            return self.ask
        if side == "DOWN":
            return None if self.bid is None else D("1") - self.bid
        raise ValueError("side must be UP or DOWN, got %r" % (side,))

    @classmethod
    def of(cls, dep: Any) -> "ExecutableBook":
        if not isinstance(dep, Mapping):
            return cls(False, None, None, None, None,
                       why="contract_depth returned %s" % type(dep).__name__)
        if dep.get("readable") is not True:
            return cls(False, None, None, None, None,
                       why=dep.get("why") or "the ladder was not readable")
        bids = dep.get("bids") if isinstance(dep.get("bids"), Mapping) else {}
        asks = dep.get("asks") if isinstance(dep.get("asks"), Mapping) else {}
        bid, ask = _dec(bids.get("best")), _dec(asks.get("best"))
        if bid is None or ask is None:
            return cls(False, None, None, None, None,
                       why="the ladder is one-sided (bid %r, ask %r); "
                           "nothing is derived in its place"
                           % (bids.get("best"), asks.get("best")))
        if not (bid < ask):
            return cls(False, bid, ask, None, None,
                       why="the ladder is crossed or locked: bid %s >= ask "
                           "%s" % (bid, ask), crossed=True)
        return cls(True, bid, ask, _dec(bids.get("best_qty")),
                   _dec(asks.get("best_qty")), dep.get("snapshot_age_s"))


# ------------------------------------------------------- order history
@dataclass(frozen=True)
class OrderHistory:
    """`metadata.order_history()` normalised.

    PRODUCER CONTRACT (metadata.py::order_history):
        {"readable": bool, "verdict": "UNREADABLE"|"ORDERS_FOUND"|
         "NO_ORDERS_ON_RECORD", "total_rows": int, "per_status":
         [{"status_filter": str, "orders": [..] (ONLY when include_rows),
           "sample": {...}, "row_keys": [...]}],
         "unreadable_statuses": [...]}

    NOTE THE TRAP, which the producer documents itself: `orders` is present
    only when `include_rows=True`. A caller that forgets it gets rows=[] on
    every call -- "readable and empty" -- which is the substitution that
    makes a restart assume flat when it is not.
    """
    readable: bool
    verdict: Optional[str]
    total_rows: int
    orders: Tuple[OrderState, ...]
    why: Optional[str] = None

    def find(self, order_id: Any) -> Optional[OrderState]:
        for o in self.orders:
            if o.order_id is not None and str(o.order_id) == str(order_id):
                return o
        return None

    @classmethod
    def of(cls, hist: Any) -> "OrderHistory":
        if not isinstance(hist, Mapping):
            return cls(False, None, 0, (),
                       "order_history returned %s" % type(hist).__name__)
        if hist.get("readable") is not True:
            return cls(False, hist.get("verdict"),
                       int(hist.get("total_rows") or 0), (),
                       "order history was not readable (verdict %r, "
                       "unreadable statuses %r); UNREADABLE is not an empty "
                       "history" % (hist.get("verdict"),
                                    hist.get("unreadable_statuses")))
        per = hist.get("per_status")
        if not isinstance(per, list):
            return cls(False, hist.get("verdict"), 0, (),
                       "per_status is %s, not a list" % type(per).__name__)
        rows: List[Mapping[str, Any]] = []
        saw_rows_key = False
        for entry in per:
            if not isinstance(entry, Mapping):
                continue
            got = entry.get("orders")
            if isinstance(got, list):
                saw_rows_key = True
                rows.extend(r for r in got if isinstance(r, Mapping))
        total = int(hist.get("total_rows") or 0)
        if total > 0 and not saw_rows_key:
            return cls(False, hist.get("verdict"), total, (),
                       "the history reports %d rows but carries none: "
                       "order_history was called without include_rows=True, "
                       "and an absent row list is not an empty one" % total)
        return cls(True, hist.get("verdict"), total,
                   tuple(OrderState.of(r) for r in rows))


# ------------------------------------------------------------- discovery
@dataclass(frozen=True)
class DiscoveredMarket:
    symbol: str
    ticker: str
    expiry_ts: Optional[float]
    strike_value: Optional[str]
    strike_available_at: Optional[float]

    @property
    def strike_published(self) -> bool:
        return self.strike_value not in (None, "")


def markets_of(events: Any) -> List[DiscoveredMarket]:
    """The public events listing normalised.

    PRODUCER CONTRACT (r2_exec_runner._active_events_public): a list of
    event objects, each {"ticker": str, "contracts": [{"strike": {"value",
    "availableAt"}, "expiryDate": iso}]}.
    """
    from datetime import datetime
    out: List[DiscoveredMarket] = []
    if not isinstance(events, list):
        return out
    for e in events:
        if not isinstance(e, Mapping):
            continue
        tk = str(e.get("ticker") or "")
        if not tk:
            continue
        cs = e.get("contracts")
        c = cs[0] if isinstance(cs, list) and cs and isinstance(cs[0], Mapping) \
            else {}
        st = c.get("strike") if isinstance(c.get("strike"), Mapping) else {}
        exp = c.get("expiryDate")
        ets = None
        if exp:
            try:
                ets = datetime.fromisoformat(
                    str(exp).replace("Z", "+00:00")).timestamp()
            except ValueError:
                ets = None
        av = st.get("availableAt")
        ats: Optional[float] = None
        if av not in (None, ""):
            if isinstance(av, (int, float)):
                ats = float(av)
            else:
                try:
                    ats = datetime.fromisoformat(
                        str(av).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ats = None
        out.append(DiscoveredMarket("GEMI-%s-UP" % tk, tk, ets,
                                    st.get("value"), ats))
    return out
