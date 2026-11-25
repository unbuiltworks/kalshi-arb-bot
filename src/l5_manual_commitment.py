"""The venue reads behind the bot-only / total-account drawdown split.

Owner decision 2026-11-25; the arithmetic and the reasons are in
`l5_portfolio_drawdown`. This module is the only part that talks to the
venue, and it only ever GETs one order by id (`transport.order`).

WHAT IT PRODUCES. A `measurement` the runner stores on its tick:

    {"t": epoch,
     "orders": [{order_id_tail, market, side, action BUY|SELL, filled,
                 cost_usd | proceeds_usd, state, terminal}],
     "unclassified": [{order_id_tail, market, filled, why, state}],
     "rejected": [{order_id_tail, why}]}

`l5_portfolio_drawdown.manual_commitment` turns that into signed dollars
per market against the positions the account still holds and the ledger's
retired rows, with no venue request, so the published block can be built
on the registry's thread.

THE OWNER ATTESTS IDS, THE VENUE STATES EVERYTHING ELSE. An id counts only
when the venue's own row says `manualOrderIndicator` is the MANUAL value.
A row that is not manual, not found or unreadable is `rejected` and
contributes nothing. A manual row whose direction, market or price cannot
be read with certainty is `unclassified`: nothing of it is credited and
its market is counted as a possible full win. Every doubt makes the BOT
figure LARGER, which is the safe direction.

PACE. A row is re-read at most every REREAD_S while it is not terminal and
never again once it is terminal and accounted; at most READS_PER_STEP
reads happen on one tick. The cache is in memory only: after a restart
every attested id is read again by id, which the venue answers for a
terminal order (`PmusTransport.order`), and the settlement side comes from
the ledger's persisted `retired_markets`, so nothing new is stored on disk.
A FULL ORDER ID IS NEVER LOGGED OR PUBLISHED from
here: the last four characters only.
"""
from __future__ import annotations

import re
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_live_execution as LX
from src import l5_portfolio_drawdown as PDD

MANUAL_INDICATOR = "MANUAL_ORDER_INDICATOR_MANUAL"
#: the only intents that are a purchase
BUY_INTENTS = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
REREAD_S = 60.0
READS_PER_STEP = 3
#: what may be put in the order route. Anything else is never requested.
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


#: A CLOSING SELL, AS FAR AS IT IS KNOWN. This venue has only ever been
#: OBSERVED returning the two BUY intents (`LX.VENUE_SIDE_WORDS`,
#: analysis/l5/completion_repair/EXIT_OPTIONS_VENUE_CHECK.md); no sell row
#: has been recorded by this repository. The spellings below are the
#: venue's documented vocabulary, not an observation, so a sell is accepted
#: only when EVERY statement on the row agrees with one of these two lines
#: and anything else is UNCLASSIFIED -- never guessed into a direction:
#:
#:   intent SELL_LONG  -> closes UP   (outcomeSide YES, side ORDER_SIDE_SELL)
#:   intent SELL_SHORT -> closes DOWN (outcomeSide NO,  side ORDER_SIDE_BUY)
#:
#: (`side` is about the YES token: selling YES sells it, selling NO buys
#: it back.) A hand "sell" the venue records as a BUY of the other outcome
#: is an ordinary BUY row here; the pair it forms is netted at $1 in
#: `l5_portfolio_drawdown.manual_commitment`.
SELL_INTENTS = {"ORDER_INTENT_SELL_LONG": ("UP", "OUTCOME_SIDE_YES",
                                           "ORDER_SIDE_SELL"),
                "ORDER_INTENT_SELL_SHORT": ("DOWN", "OUTCOME_SIDE_NO",
                                            "ORDER_SIDE_BUY")}
BUY, SELL = "BUY", "SELL"


def _direction(row: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """{"action": BUY|SELL, "side": UP|DOWN}, or None when not CERTAIN."""
    intent = str(row.get("intent") or "").strip().upper()
    action = str(row.get("action") or "").strip().upper()
    # the row's `action`, when present, must end in the same verb. (Matched
    # by suffix: this module only READS rows; nothing in src/ builds a sell
    # order, and `test_l5_venue_exit_probe` pins that no sell-action token
    # exists in the source.)
    if intent in BUY_INTENTS and (not action or action.endswith("_BUY")):
        try:
            return {"action": BUY, "side": LX.venue_row_side(
                row, doing="measuring a manual order")}
        except Exception:                                      # noqa: BLE001
            return None
    if intent in SELL_INTENTS and (not action or action.endswith("_SELL")):
        side, outcome, token = SELL_INTENTS[intent]
        for key, want in (("outcomeSide", outcome), ("side", token)):
            got = row.get(key)
            if got is not None and str(got).strip().upper() != want:
                return None
        return {"action": SELL, "side": side}
    return None


def parse_row(order_id: str, row: Any) -> Dict[str, Any]:
    """One venue order row -> {"order": {...}, "terminal"}, or
    {"unclassified": {...}, "terminal"}, or {"why"}.

    PURE. Uses the same helpers the ledger books fills with: the cumulative
    quantity and terminal test of `order_progress`, the WIRE average price
    converted to the outcome's own terms (`own_outcome_price`), and the
    venue's collected commission (absent counts 0).

    A BUY's cash is its cost, filled x own price + commission. A closing
    SELL's cash is its proceeds, filled x own price - commission. A row
    the venue marks manual whose direction or price cannot be read with
    certainty is UNCLASSIFIED: it is not counted, and its market is treated
    as unmeasured with the row's whole quantity as a possible gain.
    """
    if not isinstance(row, Mapping) or not row:
        return {"why": "ORDER_NOT_FOUND"}
    rid = row.get("id")
    if rid is not None and str(rid) != str(order_id):
        return {"why": "ORDER_ID_MISMATCH"}
    if str(row.get("manualOrderIndicator") or "") != MANUAL_INDICATOR:
        # the venue does not say a person placed it: terminal, and final
        return {"why": "NOT_MANUAL_PER_VENUE", "final": True}
    try:
        prog = LX.order_progress(row)
    except Exception as exc:                                   # noqa: BLE001
        return {"why": "ROW_UNREADABLE:%s" % type(exc).__name__}
    cum = prog["cum_qty"]
    if cum < 0:
        return {"why": "ROW_UNREADABLE:NEGATIVE_FILL"}
    tail = PDD.order_tail(order_id)
    terminal = bool(prog.get("terminal"))
    try:
        market = LX.venue_row_market(row, doing="measuring a manual order")
    except Exception:                                          # noqa: BLE001
        market = None
    way = _direction(row)
    why = ("MARKET_UNREADABLE" if market is None
           else "DIRECTION_UNCLASSIFIED" if way is None else None)
    cash = D(0)
    if why is None and cum > 0:
        wire = LX.venue_order_avg_price(row)
        if wire is None:
            why = "AVG_PRICE_UNREADABLE"
        else:
            fee = prog.get("commission")
            fee = fee if fee is not None else D(0)
            gross = cum * LX.own_outcome_price(way["side"], wire,
                                               basis=LX.WIRE)
            cash = max(D(0), gross + fee if way["action"] == BUY
                       else gross - fee)
    if why is not None:
        return {"unclassified": {"order_id_tail": tail, "market": market,
                                 "filled": str(cum), "why": why,
                                 "state": prog.get("state")},
                "terminal": terminal}
    order = {"order_id_tail": tail, "market": market, "side": way["side"],
             "action": way["action"], "filled": str(cum),
             "state": prog.get("state"),
             # a settled result is measured only from a reading taken
             # after the order could no longer fill
             "terminal": terminal}
    order["cost_usd" if way["action"] == BUY else "proceeds_usd"] = str(cash)
    return {"order": order, "terminal": terminal}


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
        unclassified: List[Dict[str, Any]] = []
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
                                     or ((got.get("order")
                                          or got.get("unclassified"))
                                         and got.get("terminal")))}
                self.cache[oid] = ent
            if ent is None:
                rejected.append({"order_id_tail": tail,
                                 "why": "NOT_READ_YET"})
            elif ent["got"].get("order"):
                orders.append(dict(ent["got"]["order"]))
            elif ent["got"].get("unclassified"):
                unclassified.append(dict(ent["got"]["unclassified"]))
            else:
                rejected.append({"order_id_tail": tail,
                                 "why": ent["got"].get("why")})
        return {"t": float(now), "orders": orders, "rejected": rejected,
                "unclassified": unclassified, "reads": reads}
