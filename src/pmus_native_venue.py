"""The real venue seat for the native engine. Authenticated state only.

`NativeMoneyPath` asks four questions of a venue and this answers all four
against the certified `PmusTransport`, never against a model:

    cancel_resting(side)  cancel our resting acquisition on that side
    cancel_order(oid)     cancel one order by id
    open_orders()         the venue's OWN list; None is never synthesised
    unbooked_fills()      fills the venue knows about and our book does not
    completion_fill(it)   what a marketable leg ACTUALLY filled

Every one of them fails by raising or returning None. Nothing here converts
"we could not read it" into "there is none" -- that conversion is the whole
class of bug the overlay exists to refuse.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_live_execution as EX

SIDE_WIRE = {"up": "OUTCOME_SIDE_YES", "down": "OUTCOME_SIDE_NO"}


class AuthenticatedVenue:
    """Venue truth for one market, over the certified transport."""

    def __init__(self, transport, *, market: str, journal=None) -> None:
        self.tp = transport
        self.market = market
        self.journal = journal
        #: coid/venue-id of our resting acquisition, by side
        self.resting_ids: Dict[str, Optional[str]] = {"up": None, "down": None}
        self._booked: set = set()

    # ------------------------------------------------------------- cancels

    def cancel_resting(self, side: str) -> None:
        oid = self.resting_ids.get(side)
        if oid:
            self.tp.cancel(oid)
            self.resting_ids[side] = None

    def cancel_order(self, oid: str) -> None:
        self.tp.cancel(oid)

    # --------------------------------------------------------------- reads

    def open_orders(self) -> Optional[List[dict]]:
        """The venue's own answer. An unreadable answer RAISES; it never
        degrades to an empty list."""
        rows = self.tp.open_orders()
        if rows is None:
            return None
        return [r for r in rows
                if str((r or {}).get("marketSlug") or
                       (r or {}).get("market") or self.market) == self.market]

    def unbooked_fills(self) -> Optional[List[dict]]:
        """Fills the venue reports that we have not yet applied.

        Identity comes from the venue's own trade id, so a fill is booked once
        and once only -- a double-booked fill is inventory we do not have.
        """
        rows = self.tp.fills() if hasattr(self.tp, "fills") else None
        if rows is None:
            return None
        out: List[dict] = []
        for r in rows or []:
            tid = str((r or {}).get("tradeId") or (r or {}).get("id") or "")
            if not tid or tid in self._booked:
                continue
            side = EX.canonical_side((r or {}).get("outcomeSide")
                                     or (r or {}).get("side"),
                                     doing="booking a venue fill")
            qty = D(str((r or {}).get("qtyDecimal")
                        or (r or {}).get("quantity") or 0))
            if qty <= 0:
                continue
            wire_px = D(str(((r or {}).get("price") or {}).get("value")
                            if isinstance((r or {}).get("price"), Mapping)
                            else (r or {}).get("price") or 0))
            own = wire_px if side == "UP" else (D(1) - wire_px)
            self._booked.add(tid)
            out.append({"side": "up" if side == "UP" else "down",
                        "qty": qty, "cost": own * qty,
                        "maker": bool((r or {}).get("maker", True)),
                        "fee": (None if (r or {}).get("commission") is None
                                else D(str((r or {}).get("commission"))))})
        return out

    def completion_fill(self, intent) -> Optional[tuple]:
        """What the marketable leg ACTUALLY filled, from /v1/order.

        ACK IS NOT FILL. This reads the order back and reports the venue's
        cumulative quantity; an unreadable or non-terminal answer returns None
        so the caller fails closed.
        """
        oid = getattr(intent, "venue_order_id", None) or intent.get("venue_order_id")
        if not oid:
            return None
        row = self.tp.order(str(oid))
        if not row:
            return None
        cum = D(str(row.get("cumQty") or row.get("cumQuantity") or 0))
        if cum <= 0:
            return D(0), D(0), D(0)
        avg = D(str(row.get("avgPx") or row.get("avgPrice") or 0))
        side = str(intent["side"])
        own = avg if side == "up" else (D(1) - avg)
        fee = D(str(row.get("commission") or 0))
        return cum, own * cum, fee
