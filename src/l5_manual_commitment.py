"""The venue reads behind the bot-only / total-account drawdown split.

Owner decision 2026-11-25; the arithmetic and the reasons are in
`l5_portfolio_drawdown`. This module is the only part that talks to the
venue, and it only ever GETs one order by id (`transport.order`).

WHAT IT PRODUCES. A `measurement` the runner stores on its tick:

    {"t": epoch, "orders": [{order_id_tail, market, side, filled, cost_usd,
                             state}],
     "rejected": [{order_id_tail, why}]}

`l5_portfolio_drawdown.manual_commitment` turns that into dollars against
the positions the account still holds, with no venue request, so the
published block can be built on the registry's thread.

THE OWNER ATTESTS IDS, THE VENUE STATES EVERYTHING ELSE. An id counts only
when the venue's own row says `manualOrderIndicator` is the MANUAL value,
the order is a BUY, and its market, side, filled quantity and average
price all read cleanly. Anything else is listed under `rejected` with a
reason and contributes nothing -- a smaller manual amount means a LARGER
bot figure, which is the safe direction.

PACE. A row is re-read at most every REREAD_S while it is not terminal and
never again once it is terminal and accounted; at most READS_PER_STEP
reads happen on one tick. A FULL ORDER ID IS NEVER LOGGED OR PUBLISHED from
here: the last four characters only.
"""
from __future__ import annotations

import re
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_live_execution as LX
from src import l5_portfolio_drawdown as PDD

MANUAL_INDICATOR = "MANUAL_ORDER_INDICATOR_MANUAL"
#: the only intents that are a purchase; a sell is listed, never counted
BUY_INTENTS = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
REREAD_S = 60.0
READS_PER_STEP = 3
#: what may be put in the order route. Anything else is never requested.
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def parse_row(order_id: str, row: Any) -> Dict[str, Any]:
    """One venue order row -> {"order": {...}, "terminal"} or {"why"}.

    PURE. Uses the same helpers the ledger books fills with: the cumulative
    quantity and terminal test of `order_progress`, the WIRE average price
    converted to the outcome's own terms, and the venue's collected
    commission (absent counts 0, which only lowers the manual amount).
    """
    if not isinstance(row, Mapping) or not row:
        return {"why": "ORDER_NOT_FOUND"}
    rid = row.get("id")
    if rid is not None and str(rid) != str(order_id):
        return {"why": "ORDER_ID_MISMATCH"}
    if str(row.get("manualOrderIndicator") or "") != MANUAL_INDICATOR:
        # the venue does not say a person placed it: terminal, and final
        return {"why": "NOT_MANUAL_PER_VENUE", "final": True}
    intent = str(row.get("intent") or "").strip().upper()
    action = str(row.get("action") or "").strip().upper()
    if "SELL" in intent or "SELL" in action:
        return {"why": "MANUAL_SELL_NOT_SUPPORTED", "final": True}
    if intent not in BUY_INTENTS:
        return {"why": "INTENT_UNREADABLE"}
    try:
        prog = LX.order_progress(row)
        side = LX.venue_row_side(row, doing="measuring a manual order")
        market = LX.venue_row_market(row, doing="measuring a manual order")
    except Exception as exc:                                   # noqa: BLE001
        return {"why": "ROW_UNREADABLE:%s" % type(exc).__name__}
    cum = prog["cum_qty"]
    cost = D(0)
    if cum > 0:
        wire = LX.venue_order_avg_price(row)
        if wire is None:
            return {"why": "AVG_PRICE_UNREADABLE"}
        fee = prog.get("commission")
        cost = cum * LX.own_outcome_price(side, wire, basis=LX.WIRE) \
            + (fee if fee is not None else D(0))
    elif cum < 0:
        return {"why": "ROW_UNREADABLE:NEGATIVE_FILL"}
    return {"order": {"order_id_tail": PDD.order_tail(order_id),
                      "market": market, "side": side, "filled": str(cum),
                      "cost_usd": str(max(D(0), cost)),
                      "state": prog.get("state")},
            "terminal": bool(prog.get("terminal"))}


class Reader:
    """Reads the attested orders at a bounded pace and keeps what it read."""

    def __init__(self) -> None:
        #: order id -> {"t": last read, "got": parse_row(...), "final": bool}
        self.cache: Dict[str, Dict[str, Any]] = {}

    def step(self, transport: Any, now: float,
             ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """One tick. Returns the measurement to store; never raises on a
        failed read -- that order is rejected until it reads again."""
        want = PDD.attested_order_ids() if ids is None else list(ids)
        for gone in [k for k in self.cache if k not in want]:
            self.cache.pop(gone, None)
        orders: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        reads = 0
        for n, oid in enumerate(want):
            tail = PDD.order_tail(oid)
            if n >= PDD.MANUAL_MAX_ORDERS:
                rejected.append({"order_id_tail": tail,
                                 "why": "TOO_MANY_ATTESTED_ORDERS"})
                continue
            if not ORDER_ID_RE.match(oid):
                rejected.append({"order_id_tail": tail,
                                 "why": "ORDER_ID_MALFORMED"})
                continue
            ent = self.cache.get(oid)
            due = ent is None or (not ent.get("final")
                                  and now - ent["t"] >= REREAD_S)
            if due and reads < READS_PER_STEP:
                reads += 1
                try:
                    got = parse_row(oid, transport.order(oid))
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    got = {"why": "ORDER_UNREADABLE:%s" % type(exc).__name__}
                ent = {"t": now, "got": got,
                       "final": bool(got.get("final")
                                     or (got.get("order")
                                         and got.get("terminal")))}
                self.cache[oid] = ent
            if ent is None:
                rejected.append({"order_id_tail": tail,
                                 "why": "NOT_READ_YET"})
            elif ent["got"].get("order"):
                orders.append(dict(ent["got"]["order"]))
            else:
                rejected.append({"order_id_tail": tail,
                                 "why": ent["got"].get("why")})
        return {"t": float(now), "orders": orders, "rejected": rejected,
                "reads": reads}
