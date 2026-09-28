"""Compose NativeEngine onto the ALREADY-CERTIFIED execution stack.

Nothing here re-implements a control that exists. The engine decides; these
pieces, unchanged, decide whether a decision may reach the wire:

    PairedLockTransport   paired shadow/live record, LOCK5 gate, ACK-is-not-FILL
    MANDATE.guard         the mandate, the sole monetary authority
    InventoryStore        authenticated inventory, canonical UP/DOWN
    AuthenticatedBookFeed freshness and reconnect; a stale book decides nothing

The engine's intents are translated into order bodies by `src.l5_order_body`,
the same builder the certified path already uses. The L5-action translator is
not reachable from here and `assert_no_translate_fallback` states that
structurally.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_order_body as OB
from src import pmus_l5_native_engine as E
from src import pmus_native_live_overlay as OV

ORDER_PATH = "/v1/orders"
SIDE_WIRE = {"up": "OUTCOME_SIDE_YES", "down": "OUTCOME_SIDE_NO"}


def wire_price(side: str, own_px: D) -> D:
    """Own-outcome price -> the venue's YES-terms wire price."""
    own_px = D(str(own_px))
    return own_px if side == "up" else (D(1) - own_px)


def body_for(intent: "E.Intent", market: str) -> Dict[str, Any]:
    """One intent -> one order body, through the certified builder."""
    side = str(intent["side"])
    if intent.kind == E.REST:
        return OB.build(slug=market, side=SIDE_WIRE[side],
                        price=wire_price(side, intent["price"]),
                        qty=D(str(intent["qty"])), post_only=True)
    if intent.kind in (E.COMPLETE, E.SECURE):
        qty = D(str(intent["qty"]))
        # the per-level walked cost is what the frozen rule priced; the order
        # is marketable and carries the level-average as its limit.
        px = D(str(intent["cost"])) / qty if qty > 0 else D(0)
        return OB.build(slug=market, side=SIDE_WIRE[side],
                        price=wire_price(side, px), qty=qty, post_only=False)
    raise ValueError("intent %r builds no order" % (intent.kind,))


class NativeMoneyPath:
    """Engine -> intents -> certified transport. No other route to the wire."""

    def __init__(self, *, market: str, engine: "E.NativeEngine", transport,
                 feed=None, venue=None, journal=None) -> None:
        self.market = market
        self.eng = engine
        #: the PairedLockTransport wrapping MANDATE.guard(...). Every POST goes
        #: through it, so LOCK5 and the mandate bound every order.
        self.tp = transport
        self.feed = feed
        self.venue = venue
        self.journal = journal
        self.submitted: List[dict] = []
        self.lock_transactions: List[dict] = []

    # ------------------------------------------------------------ the tick

    def on_book_state(self, row: Mapping[str, Any],
                      ladder: Optional[Mapping[str, Any]] = None
                      ) -> Dict[str, Any]:
        """One authenticated book state -> zero or more real orders."""
        if self.feed is not None and not self.feed.fresh():
            return {"acted": False, "refused": "BOOK_NOT_FRESH",
                    "age_s": (None if self.feed.age_s() is None
                              else round(self.feed.age_s(), 3))}
        try:
            intents = self.eng.on_book(row, ladder,
                                       execute_taker=self._execute_taker)
        except E.StaleBook as exc:
            return {"acted": False, "refused": "STALE_BOOK",
                    "detail": str(exc)}

        out: Dict[str, Any] = {"acted": True, "intents": len(intents),
                               "submitted": [], "cancelled": [],
                               "lock": None}
        for it in intents:
            if it.kind == E.CANCEL:
                if self.venue is not None:
                    self.venue.cancel_resting(it["side"])
                out["cancelled"].append(it["side"])
            elif it.kind == E.REST:
                out["submitted"].append(self._post(it))
            elif it.kind == E.LOCK_CANDIDATE:
                txn = OV.LockTransaction(self.eng, self.venue,
                                         journal=self.journal)
                verdict = txn.run(it)
                self.lock_transactions.append(verdict)
                out["lock"] = verdict["verdict"]
            # COMPLETE / SECURE already went through _execute_taker
        return out

    # ------------------------------------------------------------- the wire

    def _post(self, intent: "E.Intent") -> Dict[str, Any]:
        body = body_for(intent, self.market)
        ack = self.tp.request("POST", ORDER_PATH, body)
        rec = {"kind": intent.kind, "side": intent["side"], "body": body,
               "ack": ack}
        self.submitted.append(rec)
        return rec

    def _execute_taker(self, intent: "E.Intent"):
        """A completion leg. Submitted, then resolved from AUTHENTICATED fill
        state -- an ACK is not a fill, so nothing is booked from the ACK."""
        self._post(intent)
        if self.venue is None:
            return None
        got = self.venue.completion_fill(intent)
        if got is None:
            return None            # unconfirmed: the engine stops the pass
        n, cost, fee = got
        return D(str(n)), D(str(cost)), D(str(fee))


def build(*, market: str, transport, feed=None, venue=None, journal=None,
          clip: D = E.CLIP) -> "NativeMoneyPath":
    """The only constructor. The lock gate is always installed live."""
    E.assert_no_translate_fallback()
    eng = E.NativeEngine(clip=clip, lock_gate=OV.lock_gate_for(venue,
                                                               journal=journal))
    return NativeMoneyPath(market=market, engine=eng, transport=transport,
                           feed=feed, venue=venue, journal=journal)
