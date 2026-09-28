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
from src import pmus_live_mandate as MANDATE
from src import pmus_native_live_overlay as OV

ORDER_PATH = "/v1/orders"
SIDE_WIRE = {"up": "OUTCOME_SIDE_YES", "down": "OUTCOME_SIDE_NO"}

LIVE, TERMINAL, UNKNOWN = "LIVE", "TERMINAL", "UNKNOWN"


class OrderStateUnknown(RuntimeError):
    """We cannot say what one of our own orders is doing. Fail closed."""


class SessionOrderRegistry:
    """EVERY order this session submitted, by venue id, with its own money.

    One tracked quote per side was not enough: an order replaced without a
    confirmed cancel left the registry and became an orphan that could still
    fill. So every ACK is recorded here and nothing leaves until the venue says
    it is terminal.

    THE THREE NUMBERS ARE SEPARATE AND NEVER CONFLATED:

      filled_commitment   cash for quantity that actually filled. PERMANENT.
      open_reserved       cash reserved for orders still live or still unknown.
                          Released only on a venue-confirmed terminal status,
                          and only for the part that did not fill.
      cumulative_submitted  every dollar ever proposed. Diagnostic only -- it
                          is NOT exposure, and it may exceed the mandate
                          precisely because reservations get released.

    Live economic exposure is `filled_commitment + open_reserved`. Cancelling
    can only lower `open_reserved`; it can never raise headroom above what the
    mandate already allows, because a fill's commitment never leaves.
    """

    def __init__(self) -> None:
        self.orders: Dict[str, dict] = {}
        self.cumulative_submitted = D(0)

    def record(self, oid: str, *, side: str, price: D, qty: D,
               kind: str) -> dict:
        row = {"oid": str(oid), "side": str(side), "price": D(str(price)),
               "qty": D(str(qty)), "kind": str(kind), "state": LIVE,
               "filled": D(0), "reserved": D(str(price)) * D(str(qty))}
        self.orders[str(oid)] = row
        self.cumulative_submitted += row["reserved"]
        return row

    def mark_terminal(self, oid: str, filled: D) -> dict:
        row = self.orders.get(str(oid))
        if row is None:
            return {}
        filled = D(str(filled))
        if filled > row["qty"]:
            filled = row["qty"]
        row["filled"] = filled
        row["state"] = TERMINAL
        return row

    def mark_unknown(self, oid: str, why: str) -> None:
        row = self.orders.get(str(oid))
        if row is not None:
            row["state"] = UNKNOWN
            row["unknown_why"] = str(why)

    def nonterminal(self) -> List[dict]:
        return [r for r in self.orders.values() if r["state"] != TERMINAL]

    def live_for(self, side: str) -> List[dict]:
        return [r for r in self.nonterminal()
                if r["side"] == side and r["kind"] == E.REST]

    @property
    def filled_commitment(self) -> D:
        return sum((r["price"] * r["filled"] for r in self.orders.values()),
                   D(0))

    @property
    def open_reserved(self) -> D:
        """Unfilled cash still reserved. An UNKNOWN order is charged in FULL --
        an outcome we never learned can never quietly free budget."""
        tot = D(0)
        for r in self.orders.values():
            if r["state"] == TERMINAL:
                continue
            tot += r["reserved"] - (r["price"] * r["filled"])
        return tot

    @property
    def exposure(self) -> D:
        return self.filled_commitment + self.open_reserved

    def as_dict(self) -> Dict[str, Any]:
        return {"filled_commitment": str(self.filled_commitment),
                "open_reserved": str(self.open_reserved),
                "exposure": str(self.exposure),
                "cumulative_submitted": str(self.cumulative_submitted),
                "orders": len(self.orders),
                "nonterminal": len(self.nonterminal())}


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
        #: OUR live acquisition orders by side, with the venue id. Without this
        #: `cancel_resting` had no id to cancel and silently cancelled nothing,
        #: so re-quotes stacked at the venue instead of replacing.
        self.live_orders: Dict[str, Optional[dict]] = {"up": None, "down": None}
        #: every order this session submitted, and the three separate numbers
        self.registry = SessionOrderRegistry()
        self.terminals: List[dict] = []
        self.booked_fills: List[dict] = []

    # ------------------------------------------------------------ the tick

    def on_book_state(self, row: Mapping[str, Any],
                      ladder: Optional[Mapping[str, Any]] = None
                      ) -> Dict[str, Any]:
        """One authenticated book state -> zero or more real orders."""
        if self.feed is not None and not self.feed.fresh():
            return {"acted": False, "refused": "BOOK_NOT_FRESH",
                    "age_s": (None if self.feed.age_s() is None
                              else round(self.feed.age_s(), 3))}
        # AUTHENTICATED FILLS FIRST, every tick. Previously fills were booked
        # only inside a LOCK transaction, so a maker fill during ordinary
        # operation never reached the book: inventory stayed at zero, no
        # completion or lock could ever trigger, and the engine re-quoted
        # forever. Inventory must be current BEFORE the engine decides.
        booked = self._book_authenticated_fills()

        try:
            intents = self.eng.on_book(row, ladder,
                                       execute_taker=self._execute_taker)
        except E.StaleBook as exc:
            return {"acted": False, "refused": "STALE_BOOK",
                    "detail": str(exc), "fills_booked": booked}

        out: Dict[str, Any] = {"acted": True, "intents": len(intents),
                               "submitted": [], "cancelled": [],
                               "lock": None, "fills_booked": booked}
        for it in intents:
            if it.kind == E.CANCEL:
                out["cancelled"].append(self.cancel_side(it["side"]))
            elif it.kind == E.REST:
                # REPLACEMENT IS ORDERED, NOT CONCURRENT:
                #   cancel old -> venue confirms TERMINAL -> reconcile fill
                #   -> submit replacement.
                # Submitting first would stack two live quotes on one side, and
                # both could fill.
                gate = self._ensure_side_clear(it["side"])
                if not gate["clear"]:
                    out.setdefault("replacement_blocked", []).append(gate)
                    continue
                out["submitted"].append(self._post(it))
            elif it.kind == E.LOCK_CANDIDATE:
                txn = OV.LockTransaction(self.eng, self.venue,
                                         journal=self.journal,
                                         cancel_all=self.cancel_nonterminal)
                verdict = txn.run(it)
                self.lock_transactions.append(verdict)
                out["lock"] = verdict["verdict"]
            # COMPLETE / SECURE already went through _execute_taker
        return out

    # ------------------------------------------------------------- the wire

    def _book_authenticated_fills(self) -> List[dict]:
        """Apply every fill the venue reports and our book has not applied.

        Returns the rows applied. An unreadable answer is NOT "no fills": it
        returns None from the venue and we book nothing, leaving inventory as
        it was, which keeps the engine conservative rather than optimistic.
        """
        if self.venue is None:
            return []
        try:
            fills = self.venue.unbooked_fills()
        except BaseException:                                  # noqa: BLE001
            return []
        if not fills:
            return []
        applied = []
        for f in fills:
            self.eng.on_fill(str(f["side"]), D(str(f["qty"])),
                             D(str(f["cost"])),
                             maker=bool(f.get("maker", True)),
                             fee=(None if f.get("fee") is None
                                  else D(str(f["fee"]))))
            applied.append({"side": str(f["side"]), "qty": str(f["qty"]),
                            "cost": str(f["cost"])})
        self.booked_fills.extend(applied)
        if self.journal is not None:
            try:
                self.journal.write("authenticated_fills", {"fills": applied})
            except BaseException:                              # noqa: BLE001
                pass
        return applied

    def _order_id(self, ack: Any) -> str:
        if not isinstance(ack, Mapping):
            return ""
        return str(ack.get("orderId") or ack.get("id")
                   or (ack.get("order") or {}).get("id") or "")

    def _state_dir(self):
        """The mandate's ledger dir, found on the guard we submit through."""
        t = self.tp
        for _ in range(6):
            d = getattr(t, "_state_dir", None)
            if d is not None:
                return d
            t = getattr(t, "_inner", None)
            if t is None:
                break
        return None

    def release_terminal(self, oid: str, filled_qty) -> None:
        """Write the terminal row that releases an order's UNFILLED remainder.

        `admit()` charges every submission in full "until a terminal row proves
        a smaller fill". Nothing in this lane wrote that row, so a cancelled
        quote kept consuming the envelope forever and a re-quoting engine
        exhausted the mandate on quotes rather than on inventory. A FILLED
        amount is never released -- only the part that did not fill.
        """
        d = self._state_dir()
        if d is None or not oid:
            return
        row = {"kind": "order_terminal", "order_id": str(oid),
               "filled_qty": str(filled_qty),
               "labels": ("releases only the UNFILLED remainder of this "
                          "order's own reservation; a fill is permanent")}
        MANDATE._append(d, row)
        self.terminals.append(row)
        if self.journal is not None:
            try:
                self.journal.write("order_terminal", row)
            except BaseException:                              # noqa: BLE001
                pass

    def _ensure_side_clear(self, side: str) -> Dict[str, Any]:
        """No replacement while anything of ours on this side is nonterminal."""
        pending = self.registry.live_for(side)
        if not pending:
            return {"clear": True, "side": side}
        for row in list(pending):
            res = self.cancel_side(side, oid=row["oid"])
            if res.get("state") != "CONFIRMED_CANCELLED":
                return {"clear": False, "side": side, "oid": row["oid"],
                        "reason": res.get("state")}
        still = self.registry.live_for(side)
        if still:
            return {"clear": False, "side": side,
                    "reason": "STILL_NONTERMINAL",
                    "oids": [r["oid"] for r in still]}
        return {"clear": True, "side": side}

    def cancel_side(self, side: str, *, oid: Optional[str] = None) -> Dict[str, Any]:
        """Cancel our resting quote, CONFIRM it is gone, then release budget.

        A cancel request is not a cancellation. Nothing is released and nothing
        is forgotten until an authenticated read agrees the order is no longer
        live; if it cannot be established, the order stays tracked and stays
        charged, which is the conservative direction.
        """
        if oid is None:
            st = self.live_orders.get(side)
            if not st:
                return {"side": side, "state": "NOTHING_RESTING"}
            oid = str(st.get("oid") or "")
        oid = str(oid or "")
        if self.venue is None:
            return {"side": side, "state": "NO_VENUE"}
        try:
            if oid and hasattr(self.venue, "cancel_order"):
                self.venue.cancel_order(oid)
            else:
                self.venue.cancel_resting(side)
        except BaseException:                                  # noqa: BLE001
            self.registry.mark_unknown(oid, "CANCEL_FAILED")
            return {"side": side, "oid": oid, "state": "CANCEL_FAILED"}
        # book anything that filled in the cancel race BEFORE concluding
        self._book_authenticated_fills()
        try:
            still = self.venue.open_orders()
        except BaseException:                                  # noqa: BLE001
            self.registry.mark_unknown(oid, "CONFIRM_UNREADABLE")
            return {"side": side, "oid": oid, "state": "CONFIRM_UNREADABLE"}
        if still is None:
            self.registry.mark_unknown(oid, "CONFIRM_NO_ANSWER")
            return {"side": side, "oid": oid, "state": "CONFIRM_NO_ANSWER"}
        ids = {str((r or {}).get("orderId") or (r or {}).get("id") or "")
               for r in still}
        if oid and oid in ids:
            return {"side": side, "oid": oid, "state": "STILL_LIVE"}
        # TERMINAL at the venue. Its filled quantity must be READ, not assumed:
        # an unreadable answer is UNKNOWN and keeps its full reservation.
        try:
            got = (self.venue.filled_qty(oid)
                   if hasattr(self.venue, "filled_qty") else None)
        except BaseException:                                  # noqa: BLE001
            got = None
        if got is None:
            self.registry.mark_unknown(oid, "FILLED_QTY_UNREADABLE")
            return {"side": side, "oid": oid, "state": "FILL_QTY_UNKNOWN"}
        filled = D(str(got))
        self.registry.mark_terminal(oid, filled)
        self.release_terminal(oid, filled)
        cur = self.live_orders.get(side) or {}
        if str(cur.get("oid") or "") == oid:
            self.live_orders[side] = None
        if hasattr(self.venue, "resting_ids"):
            self.venue.resting_ids[side] = None
        return {"side": side, "oid": oid, "state": "CONFIRMED_CANCELLED",
                "filled_qty": str(filled)}

    def cancel_nonterminal(self) -> List[dict]:
        """Cancel EVERY nonterminal session order, by venue id.

        Used by the LOCK transaction and by close-out. A latch whose cancel
        step only touched the per-side handle left replaced-but-unconfirmed
        orders live, which is precisely the exposure a latch exists to end.
        """
        out = []
        for row in list(self.registry.nonterminal()):
            out.append(self.cancel_side(row["side"], oid=row["oid"]))
        # AND the engine's own per-side handle. An ACK that carried no id has no
        # registry key, so the handle is the only trace that something may be
        # resting; cancelling both covers either being the live one. Belt and
        # braces is correct here -- the cost of a redundant cancel is nothing,
        # the cost of a missed one is an order filling unmanaged.
        for side in ("up", "down"):
            if not self.eng.resting.get(side):
                continue
            if any(r["side"] == side for r in self.registry.nonterminal()):
                continue
            try:
                if self.venue is not None:
                    self.venue.cancel_resting(side)
                out.append({"side": side, "state": "CANCEL_REQUESTED_BY_HANDLE"})
            except BaseException:                              # noqa: BLE001
                out.append({"side": side, "state": "CANCEL_FAILED"})
        return out

    def sweep_all(self, *, rounds: int = 3) -> Dict[str, Any]:
        """End of window: cancel EVERYTHING the venue still lists.

        ORPHAN SWEEP, not a tracked-order sweep. We track one quote per side,
        so any order that was replaced without a confirmed cancel, or that
        partially filled and stayed live, is no longer in `live_orders` -- and
        a sweep that only cancels what it remembers leaves those resting past
        the window, where they can fill on a market nothing is watching. The
        venue's own open-orders list is the authority, and it is re-read after
        each round because cancelling can reveal a fill.
        """
        out = {"cancelled": [], "fills_booked": self._book_authenticated_fills(),
               "orphans_cancelled": []}
        # EVERY NONTERMINAL SESSION ORDER, from the registry -- not just the one
        # we happen to be tracking per side.
        for row in list(self.registry.nonterminal()):
            out["cancelled"].append(self.cancel_side(row["side"],
                                                     oid=row["oid"]))
        for _ in range(max(1, rounds)):
            try:
                rows = self.venue.open_orders() if self.venue else None
            except BaseException:                              # noqa: BLE001
                rows = None
            if not rows:
                break
            for r in rows:
                oid = str((r or {}).get("orderId") or (r or {}).get("id") or "")
                if not oid:
                    continue
                try:
                    self.venue.cancel_order(oid)
                except BaseException:                          # noqa: BLE001
                    continue
                # a cancel can reveal a fill; book it before releasing anything
                self._book_authenticated_fills()
                filled = D(0)
                try:
                    got = (self.venue.filled_qty(oid)
                           if hasattr(self.venue, "filled_qty") else None)
                    if got is not None:
                        filled = D(str(got))
                except BaseException:                          # noqa: BLE001
                    filled = D(0)
                self.release_terminal(oid, filled)
                out["orphans_cancelled"].append({"oid": oid,
                                                 "filled_qty": str(filled)})
        try:
            still = self.venue.open_orders() if self.venue else None
        except BaseException:                                  # noqa: BLE001
            still = None
        out["open_orders_after"] = (None if still is None else len(still))
        out["nonterminal_after"] = len(self.registry.nonterminal())
        out["exposure"] = self.registry.as_dict()
        out["clean"] = (still is not None and len(still) == 0
                        and not self.registry.nonterminal())
        if self.journal is not None:
            try:
                self.journal.write("native_sweep", out)
            except BaseException:                              # noqa: BLE001
                pass
        return out

    def _post(self, intent: "E.Intent") -> Dict[str, Any]:
        body = body_for(intent, self.market)
        ack = self.tp.request("POST", ORDER_PATH, body)
        oid = self._order_id(ack)
        rec = {"kind": intent.kind, "side": intent["side"], "body": body,
               "ack": ack, "oid": oid}
        self.submitted.append(rec)
        self.registry.record(oid, side=str(intent["side"]),
                             price=D(str(intent.get("price")
                                         or intent.get("limit_price") or 0)),
                             qty=D(str(intent["qty"])), kind=intent.kind)
        if intent.kind == E.REST:
            # TRACK THE ID. Without it `cancel_resting` had nothing to cancel.
            self.live_orders[str(intent["side"])] = {
                "oid": oid, "px": str(intent["price"]),
                "qty": str(intent["qty"])}
            if self.venue is not None and hasattr(self.venue, "resting_ids"):
                self.venue.resting_ids[str(intent["side"])] = oid
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
