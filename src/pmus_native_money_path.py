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


def body_for(intent: "E.Intent", market: str) -> Dict[str, Any]:
    """One intent -> one order body, through the certified builder.

    THE PRICE IS PASSED IN THE OUTCOME'S OWN TERMS AND IS NOT PRE-CONVERTED.
    `OB.build` calls `venue_price`, which performs the NO inversion itself;
    an earlier version of this function inverted first, so every DOWN order
    was inverted TWICE and went out at the wrong price. The builder's contract
    is the authority and there is no second serializer here.

    A taker leg carries the WORST ADMITTED LEVEL price, never the VWAP, and
    goes IOC so it cannot leave a resting remainder behind.
    """
    side = str(intent["side"])
    if intent.kind == E.REST:
        return OB.build(slug=market, side=SIDE_WIRE[side],
                        price=D(str(intent["price"])),
                        qty=D(str(intent["qty"])), post_only=True)
    if intent.kind in (E.COMPLETE, E.SECURE):
        qty = D(str(intent["qty"]))
        limit = intent.get("limit_price")
        if limit is None:
            raise ValueError("%s intent carries no executable limit price"
                             % intent.kind)
        return OB.build(slug=market, side=SIDE_WIRE[side],
                        price=D(str(limit)), qty=qty, post_only=False,
                        tif=OB.TIF_IOC)
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
        self.remainders: List[dict] = []

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
        """A completion leg, resolved from AUTHENTICATED fill state.

        AN ACK IS NEVER A FILL. The order goes out IOC so the venue itself
        cancels any unfilled remainder, but IOC is not a guarantee we may
        assume: the remainder is CHECKED, and while an unaccounted completion
        remainder is still live nothing downstream may proceed. Returning None
        stops the engine's pass, which is the fail-closed answer.
        """
        rec = self._post(intent)
        if self.venue is None:
            return None
        oid = str((rec.get("ack") or {}).get("orderId")
                  or (rec.get("ack") or {}).get("id") or "")

        got = self.venue.completion_fill(intent)
        if got is None:
            self._remainder_uncertain(intent, oid, "FILL_STATE_UNKNOWN")
            return None
        n, cost, fee = (D(str(got[0])), D(str(got[1])), D(str(got[2])))

        want = D(str(intent["qty"]))
        if n < want:
            # PARTIAL. The remainder must be provably gone before any dependent
            # decision. IOC should have cancelled it; we verify rather than
            # trust, and an unverifiable remainder fails closed.
            if not self._remainder_resolved(intent, oid, want - n):
                return None
        if n <= 0:
            return None
        return n, cost, fee

    def _remainder_resolved(self, intent, oid: str, remainder: D) -> bool:
        """Cancel, CONFIRM, reconcile race fills. Only then may we continue."""
        cancel = getattr(self.venue, "cancel_order", None)
        if cancel is not None and oid:
            try:
                cancel(oid)
            except BaseException:                              # noqa: BLE001
                self._remainder_uncertain(intent, oid, "CANCEL_FAILED")
                return False
        try:
            still = self.venue.open_orders()
        except BaseException:                                  # noqa: BLE001
            self._remainder_uncertain(intent, oid, "OPEN_ORDERS_UNREADABLE")
            return False
        if still is None:
            self._remainder_uncertain(intent, oid, "OPEN_ORDERS_NO_ANSWER")
            return False
        if still:
            self._remainder_uncertain(intent, oid, "REMAINDER_STILL_LIVE")
            return False
        self._note_remainder({"resolved": True, "remainder": str(remainder),
                              "order": oid})
        return True

    def _remainder_uncertain(self, intent, oid: str, why: str) -> None:
        self._note_remainder({"resolved": False, "why": why, "order": oid,
                              "intent": intent.kind})

    def _note_remainder(self, row: Dict[str, Any]) -> None:
        self.remainders.append(row)
        if self.journal is not None:
            try:
                self.journal.write("completion_remainder", row)
            except BaseException:                              # noqa: BLE001
                pass


CLIP_ENV = "L5_NATIVE_CLIP"


def configured_clip(env=None) -> D:
    """The capacity control, read once. Fails closed on anything unusable."""
    import os
    src = os.environ if env is None else env
    return E.parse_clip(src.get(CLIP_ENV))


def build(*, market: str, transport, feed=None, venue=None, journal=None,
          clip: Optional[D] = None) -> "NativeMoneyPath":
    """The only constructor. The lock gate is always installed live.

    `clip` unset reads L5_NATIVE_CLIP, which unset is the frozen 50.
    """
    clip = configured_clip() if clip is None else E.parse_clip(clip)
    E.assert_no_translate_fallback()
    eng = E.NativeEngine(clip=clip, lock_gate=OV.lock_gate_for(venue,
                                                               journal=journal))
    return NativeMoneyPath(market=market, engine=eng, transport=transport,
                           feed=feed, venue=venue, journal=journal)
