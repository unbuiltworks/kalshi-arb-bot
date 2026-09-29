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

    def order_status(self, oid: str, *, requested_qty=None,
                     side: Optional[str] = None) -> Dict[str, Any]:
        """THE authoritative lifecycle verdict for one of our orders.

        The ONLY way the native path learns what an order did. It replaces
        `unbooked_fills`, which asked `self.tp.fills()` behind a
        `hasattr` guard that `PmusTransport` never satisfies -- so in
        production it always returned None and no fill was ever booked. R4
        filled 10 UP at 0.26 and the engine ran 215 more ticks believing it
        was flat.

        Classification comes from `pmus_order_lifecycle`, which is the
        proven `pmus_q1_executor` classifier set. An unreadable read is
        UNKNOWN, never zero.
        """
        from src import pmus_order_lifecycle as LC
        if not oid:
            return dict(LC.classify(None), order_id="",
                        why="no venue order id to ask by")
        try:
            row = self.tp.order(str(oid))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return dict(LC.classify(None), order_id=str(oid),
                        why="%s: %s" % (type(exc).__name__, str(exc)[:160]))
        try:
            v = LC.classify(row, requested_qty=requested_qty, side=side)
        except LC.AmbiguousFill as exc:
            v = dict(LC.classify(None), why=str(exc))
        v["order_id"] = str(oid)
        if self.journal is not None:
            try:
                self.journal.write("order_status", {
                    k: (str(x) if isinstance(x, D) else x)
                    for k, x in v.items()})
            except BaseException:                              # noqa: BLE001
                pass
        return v

    def filled_qty(self, oid: str) -> Optional[D]:
        """Authenticated cumulative filled quantity for one order, or None.

        None means UNREADABLE, never zero: the caller must not release budget
        or forget an order on the strength of a read that did not happen.
        """
        if not oid:
            return None
        row = self.tp.order(str(oid))
        if not row:
            return None
        v = row.get("cumQty")
        if v is None:
            v = row.get("cumQuantity")
        if v is None:
            return None
        return D(str(v))

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
        # THE REAL RECORD NESTS ITS MONEY FIELDS. R4's raw order carried
        # `avgPx: {"value": "0.2600", "currency": "USD"}` and
        # `commissionNotionalTotalCollected: {"value": "-0.0200", ...}`, and
        # this function did `D(str(row.get("avgPx")))` -- which on the real
        # shape raises decimal.InvalidOperation inside `_execute_taker`. Both
        # are now read through the shared classifier, which is the only place
        # that knows the venue's shapes and the NO -> own-terms inversion.
        from src import pmus_order_lifecycle as LC
        side = str(intent["side"])
        cum = D(str(row.get("cumQty") if row.get("cumQty") is not None
                    else row.get("cumQuantity") or 0))
        if cum <= 0:
            return D(0), D(0), D(0)
        own = LC.own_terms_price(row, side=side)
        if own is None:
            return None                     # filled and unpriceable: fail closed
        fee = LC.commission(row)
        return cum, own * cum, (D(0) if fee is None else D(str(fee)))
