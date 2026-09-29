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


#: cancel outcomes that leave the side FREE TO QUOTE. `ALREADY_TERMINAL` is
#: here because of R4: the venue refuses to cancel an order it has finished
#: with, `_ensure_side_clear` demanded the literal string CONFIRMED_CANCELLED,
#: and so a FILLED order and a REJECTED order each returned CANCEL_FAILED and
#: blocked their side for the REST OF THE WINDOW -- from tick 3 onward, 200ms
#: after submission. An order the venue is done with needs no cancellation.
CLEARED_STATES = ("CONFIRMED_CANCELLED", "ALREADY_TERMINAL",
                  "NOTHING_RESTING")


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

    def mark_terminal(self, oid: str, filled: D,
                      classification: str = None) -> dict:
        row = self.orders.get(str(oid))
        if row is None:
            return {}
        filled = D(str(filled))
        if filled > row["qty"]:
            filled = row["qty"]
        row["filled"] = filled
        row["state"] = TERMINAL
        # The venue's own classification, kept so the order census can be
        # taken from the registry rather than recounted from a second source
        # that could disagree with it. Absent one, it is derived from the
        # quantity, which is the only thing we can honestly say.
        if classification is not None:
            row["classification"] = str(classification)
        elif "classification" not in row:
            row["classification"] = ("FILLED" if filled > 0
                                     else "CANCELLED_NO_FILL")
        return row

    def census(self) -> Dict[str, int]:
        """Terminal classification counts for THIS session's orders.

        One source: the registry that recorded every submission. A census
        recounted from journal rows or from the venue could disagree with the
        orders we actually sent, which is the whole defect this removes.
        """
        out = {"submitted": len(self.orders), "filled": 0, "rejected": 0,
               "no_fill_terminal": 0, "nonterminal": 0, "unknown": 0}
        for r in self.orders.values():
            if r["state"] == UNKNOWN:
                out["unknown"] += 1
            if r["state"] != TERMINAL:
                out["nonterminal"] += 1
                continue
            cls = str(r.get("classification") or "")
            if cls in ("FILLED", "PARTIALLY_FILLED"):
                out["filled"] += 1
            elif cls == "REJECTED_NO_FILL":
                out["rejected"] += 1
            else:
                out["no_fill_terminal"] += 1
        return out

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
        #: every venue REJECTION this session saw, so a failed complement is
        #: visible in the record instead of recoverable only by an
        #: after-the-fact /v1/order read (R4).
        self.rejections: List[dict] = []
        #: whether the venue can be asked what an order did. False is a
        #: wiring defect; see build().
        self.lifecycle_seam = True
        #: order ids whose authoritative state was read on the current pass.
        self._resolved_this_pass: set = set()
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
        #: consecutive cancel-transport failures per order id, so a cancel
        #: that keeps failing is bounded and escalated rather than silently
        #: stranding its side for the rest of the window (R5 CSPERKXEYYB5).
        self._cancel_failures: Dict[str, int] = {}

    # ------------------------------------------------------------ the tick

    def on_book_state(self, row: Mapping[str, Any],
                      ladder: Optional[Mapping[str, Any]] = None
                      ) -> Dict[str, Any]:
        """One authenticated book state -> zero or more real orders."""
        if self.feed is not None and not self.feed.fresh():
            return {"acted": False, "refused": "BOOK_NOT_FRESH",
                    "age_s": (None if self.feed.age_s() is None
                              else round(self.feed.age_s(), 3))}
        # AUTHORITATIVE ORDER STATE FIRST, every tick, for every order this
        # session has not yet seen a terminal answer for. This is the R4 fix:
        # the previous sweep asked `tp.fills()` behind a `hasattr` guard that
        # PmusTransport never satisfies, so nothing was ever booked, the 0045z
        # window filled 10 UP at 0.26 and the engine ran 215 further ticks
        # believing it was flat, and its complement's ORDER_STATE_REJECTED was
        # never noticed.
        #
        # THE ENGINE DOES NOT DECIDE UNTIL THIS HAS BEEN INCORPORATED. An
        # order we could not read is UNKNOWN and HALTS the window rather than
        # being treated as unfilled.
        resolved = self.resolve_open_orders()
        if resolved["unknown"]:
            return {"acted": False, "refused": "ORDER_STATE_UNKNOWN",
                    "halted": True, "detail": resolved["unknown"],
                    "fills_booked": resolved["fills_booked"],
                    "note": ("an order whose authoritative state we could not "
                             "read may not be traded around")}
        booked = resolved["fills_booked"] + self._book_authenticated_fills()

        try:
            intents = self.eng.on_book(row, ladder,
                                       execute_taker=self._execute_taker)
        except E.StaleBook as exc:
            return {"acted": False, "refused": "STALE_BOOK",
                    "detail": str(exc), "fills_booked": booked}

        out: Dict[str, Any] = {"acted": True, "intents": len(intents),
                               "submitted": [], "cancelled": [],
                               "lock": None, "fills_booked": booked,
                               "terminated": resolved["terminated"],
                               "rejected": resolved["rejected"],
                               "residual": str(self.eng.bk.residual())
                               if hasattr(self.eng.bk, "residual") else None}
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

    def resolve_open_orders(self) -> Dict[str, Any]:
        """Read back every nonterminal order and INCORPORATE what it did.

        One responsibility, one classifier (`pmus_order_lifecycle`, which is
        the proven `pmus_q1_executor` set), applied to every order this
        session submitted:

          FILLED / PARTIAL   book the fill into the engine at the venue's own
                             average price in OWN terms, with the venue's own
                             commission, then mark the registry terminal for
                             the filled quantity so only the UNFILLED part of
                             the reservation is released.
          REJECTED_NO_FILL   TERMINAL IMMEDIATELY, zero filled, the whole
                             reservation released. R4's complement was
                             rejected 94ms after submission and the sweep
                             still recorded CANCEL_FAILED and left it
                             nonterminal, which is why $7.30 stayed reserved
                             and the side never cleared.
          CANCELLED_NO_FILL  same, for an order the venue finished with.
          WORKING            incorporated as "still resting, zero filled".
                             Not terminal, and not a reason to stop: a maker
                             quote is supposed to rest.
          UNKNOWN            the caller HALTS. Never treated as unfilled.

        A fill is booked ONCE: the registry's recorded filled quantity is the
        high-water mark and only the increment is handed to the engine.
        """
        out: Dict[str, Any] = {"fills_booked": [], "terminated": [],
                               "rejected": [], "working": [], "unknown": []}
        self._resolved_this_pass = set()
        if self.venue is None or not hasattr(self.venue, "order_status"):
            return out
        from src import pmus_order_lifecycle as LC
        for row in list(self.registry.nonterminal()):
            oid = str(row.get("oid") or "")
            if not oid:
                # never ACKed with an id: there is nothing to ask by, and the
                # registry already charges it in full.
                continue
            v = self.venue.order_status(oid, requested_qty=row["qty"],
                                        side=row["side"])
            self._resolved_this_pass.add(str(oid))
            cls = v.get("classification")
            if cls == LC.UNKNOWN:
                self.registry.mark_unknown(oid, str(v.get("why") or "UNKNOWN"))
                out["unknown"].append({"oid": oid, "why": v.get("why")})
                continue
            if cls in (LC.FILLED, LC.PARTIAL):
                filled = D(str(v["filled_qty"]))
                already = D(str(row.get("filled") or 0))
                new = filled - already
                if new > 0:
                    px = D(str(v["price_own"]))
                    fee = v.get("commission")
                    self.eng.on_fill(row["side"], new, px * new,
                                     maker=bool(row.get("kind") == E.REST),
                                     fee=(None if fee is None
                                          else D(str(fee))))
                    booked = {"oid": oid, "side": row["side"],
                              "qty": str(new), "price_own": str(px),
                              "cost": str(px * new),
                              "commission": (None if fee is None
                                             else str(fee)),
                              "source": "GET /v1/order/%s" % oid}
                    out["fills_booked"].append(booked)
                    self.booked_fills.append(booked)
                    if self.journal is not None:
                        try:
                            self.journal.write("authenticated_fills",
                                               {"fills": [booked]})
                        except BaseException:                  # noqa: BLE001
                            pass
                if v.get("terminal"):
                    # BOTH: the registry stops treating it as live, and the
                    # ledger row releases only the unfilled remainder.
                    # `release_terminal` alone left it nonterminal forever.
                    self.registry.mark_terminal(oid, filled, cls)
                    self.release_terminal(oid, filled)
                    out["terminated"].append({"oid": oid, "class": cls,
                                              "filled": str(filled)})
                else:
                    row["filled"] = filled
                continue
            if cls in (LC.REJECTED_NO_FILL, LC.CANCELLED_NO_FILL):
                self.registry.mark_terminal(oid, D(0), cls)
                self.release_terminal(oid, D(0))
                cur = self.live_orders.get(row["side"]) or {}
                if str(cur.get("oid") or "") == oid:
                    self.live_orders[row["side"]] = None
                rec = {"oid": oid, "side": row["side"], "class": cls,
                       "state": v.get("state"), "filled": "0",
                       "reservation_released": str(row.get("reserved")),
                       "note": ("terminal at the venue; only the UNFILLED "
                                "reservation is released and the residual "
                                "this leg failed to complete remains")}
                out["terminated"].append(rec)
                if cls == LC.REJECTED_NO_FILL:
                    out["rejected"].append(rec)
                    self.rejections.append(rec)
                continue
            out["working"].append({"oid": oid, "state": v.get("state")})
        return out

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

    #: How many consecutive cancel-transport failures on ONE order id are
    #: retried before the side is escalated. R5's CSPERKXEYYB5 took an
    #: HTTPError on every cancel from 11:16:54 to the 11:30 close and its side
    #: was never re-quoted again -- ~13 minutes, 3 UP fills against 5 DOWN,
    #: and a 20-share directional residual that was an execution artifact
    #: rather than a decision. The bound does NOT unblock replacement: while
    #: the order may still be live, submitting a replacement would stack two
    #: live quotes on one side and both could fill. It bounds how long we go
    #: on believing a cancel will work, and it separates the two cases the
    #: old code merged.
    CANCEL_RETRY_BUDGET = 3

    def _cancel_failure_verdict(self, side: str, oid: str,
                                res: Mapping[str, Any]) -> Dict[str, Any]:
        """Why is this side occupied, and is our ownership actually uncertain?

        R5 treated every non-cleared cancel identically. Two cases are not the
        same thing:

          KNOWN_LIVE  -- the venue answers authoritatively that the order is
                         WORKING. Ownership is NOT uncertain: we know exactly
                         what is resting and for how much. The side is
                         legitimately occupied by our own working quote.
          UNCERTAIN   -- the venue will not or cannot say. Ownership IS
                         uncertain and this must stay fail-closed.

        Replacement is refused in both cases. The distinction is what lets the
        first one be retried and escalated instead of silently stranded, and
        it is recorded so the window's evidence says which one happened.
        """
        from src import pmus_order_lifecycle as LC
        n = self._cancel_failures.get(oid, 0) + 1
        self._cancel_failures[oid] = n
        row = self.registry.orders.get(str(oid)) or {}
        state, why = "UNCERTAIN", None
        try:
            v = self.venue.order_status(oid, requested_qty=row.get("qty"),
                                        side=row.get("side") or side)
        except BaseException as exc:                           # noqa: BLE001
            v, why = {}, "%s: %s" % (type(exc).__name__, str(exc)[:120])
        cls = str(v.get("classification") or "")
        if v.get("terminal"):
            # Established terminal while we were failing to cancel it. Book it
            # and unblock IMMEDIATELY -- this is the whole point of asking.
            filled = D(str(v.get("filled_qty") or 0))
            self.registry.mark_terminal(oid, filled, cls or None)
            self.release_terminal(oid, filled)
            self._cancel_failures.pop(oid, None)
            return {"clear": True, "side": side, "oid": oid,
                    "reason": "TERMINAL_ESTABLISHED_DURING_CANCEL_RETRY",
                    "venue_state": v.get("state"),
                    "classification": cls, "filled_qty": str(filled)}
        if cls == LC.WORKING:
            state = "KNOWN_LIVE"
        elif cls == LC.UNKNOWN:
            self.registry.mark_unknown(oid, str(v.get("why") or why
                                               or "CANCEL_STATUS_UNREADABLE"))
        return {"clear": False, "side": side, "oid": oid,
                "reason": res.get("state"), "ownership": state,
                "venue_state": v.get("state") or None,
                "classification": cls or None,
                "cancel_failures": n,
                "retry_budget": self.CANCEL_RETRY_BUDGET,
                "escalated": n > self.CANCEL_RETRY_BUDGET,
                "why": why,
                "note": ("replacement stays refused while this order may be "
                         "live; KNOWN_LIVE means the venue confirms what is "
                         "resting, UNCERTAIN means it will not say and the "
                         "full reservation keeps being charged")}

    def _ensure_side_clear(self, side: str) -> Dict[str, Any]:
        """No replacement while anything of ours on this side is nonterminal."""
        pending = self.registry.live_for(side)
        if not pending:
            return {"clear": True, "side": side}
        for row in list(pending):
            res = self.cancel_side(side, oid=row["oid"])
            if res.get("state") not in CLEARED_STATES:
                verdict = self._cancel_failure_verdict(side, row["oid"], res)
                if not verdict.get("clear"):
                    return verdict
        still = self.registry.live_for(side)
        if still:
            # The previous quote was cancelled and confirmed above, so anything
            # still here is an order we could not establish the state of.
            #
            # NOT a terminality-before-submission rule: the replacement path is
            # cancel-then-replace, which is what the frozen policy does when
            # the touch moves, so this does not change LOCK5's behaviour. It
            # refuses only when an order's authoritative state is unknown --
            # measured on the 25-window replay, relaxing it changes nothing,
            # because a missing order comes from the engine's quote outliving
            # its FILLED order, not from this gate.
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
        # AUTHORITATIVE STATE FIRST. R4 attempted to cancel an order the venue
        # had REJECTED 94ms earlier and another it had FILLED, got
        # CANCEL_FAILED for both, and never quoted again. Asking first means
        # the failure path is not even entered for an order that is done.
        done = self._terminal_after_failed_cancel(side, oid)
        if done is not None:
            return done
        try:
            if oid and hasattr(self.venue, "cancel_order"):
                self.venue.cancel_order(oid)
            else:
                self.venue.cancel_resting(side)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # A CANCEL THAT FAILS IS NOT AN UNKNOWN ORDER. The venue refuses
            # to cancel an order it has already finished with, so ASK WHAT IT
            # DID before concluding anything. R4 ended with a FILLED order and
            # a REJECTED order both recorded CANCEL_FAILED and left
            # nonterminal: $9.90 stayed reserved, neither side ever cleared,
            # and `uncertainty_cleared` was false over two orders the venue
            # had been finished with for thirteen minutes.
            done = self._terminal_after_failed_cancel(side, oid)
            if done is not None:
                return done
            self.registry.mark_unknown(oid, "CANCEL_FAILED")
            return {"side": side, "oid": oid, "state": "CANCEL_FAILED",
                    "error": "%s" % type(exc).__name__}
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
        self.registry.mark_terminal(
            oid, filled, "FILLED" if filled > 0 else "CANCELLED_NO_FILL")
        self.release_terminal(oid, filled)
        cur = self.live_orders.get(side) or {}
        if str(cur.get("oid") or "") == oid:
            self.live_orders[side] = None
        if hasattr(self.venue, "resting_ids"):
            self.venue.resting_ids[side] = None
        return {"side": side, "oid": oid, "state": "CONFIRMED_CANCELLED",
                "filled_qty": str(filled)}

    def _terminal_after_failed_cancel(self, side: str, oid: str):
        """Was the order already terminal? Then the cancel had nothing to do.

        Returns a verdict dict when the venue says the order is finished, and
        None when it does not or cannot say -- in which case the caller keeps
        its UNKNOWN, which keeps the full reservation.
        """
        if not oid or self.venue is None \
                or not hasattr(self.venue, "order_status"):
            return None
        from src import pmus_order_lifecycle as LC
        row = self.registry.orders.get(str(oid)) or {}
        v = self.venue.order_status(oid, requested_qty=row.get("qty"),
                                   side=row.get("side") or side)
        if not v.get("terminal"):
            return None
        filled = D(str(v.get("filled_qty") or 0))
        if filled > 0 and v.get("price_own") is not None:
            already = D(str(row.get("filled") or 0))
            new = filled - already
            if new > 0:
                px = D(str(v["price_own"]))
                fee = v.get("commission")
                self.eng.on_fill(row.get("side") or side, new, px * new,
                                 maker=bool(row.get("kind") == E.REST),
                                 fee=(None if fee is None else D(str(fee))))
                self.booked_fills.append(
                    {"oid": oid, "side": row.get("side") or side,
                     "qty": str(new), "price_own": str(px),
                     "cost": str(px * new),
                     "source": "GET /v1/order/%s (after failed cancel)" % oid})
        self.registry.mark_terminal(oid, filled)
        self.release_terminal(oid, filled)
        cur = self.live_orders.get(side) or {}
        if str(cur.get("oid") or "") == oid:
            self.live_orders[side] = None
        if hasattr(self.venue, "resting_ids"):
            self.venue.resting_ids[side] = None
        rec = {"side": side, "oid": oid, "state": "ALREADY_TERMINAL",
               "classification": v.get("classification"),
               "venue_state": v.get("state"), "filled_qty": str(filled)}
        if v.get("classification") == LC.REJECTED_NO_FILL:
            self.rejections.append(dict(rec, note="rejected by the venue"))
        return rec

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
        # THE CERTIFIED SUBMIT, not a second POST implementation.
        # `transport.request("POST", ORDER_PATH, body)` was this line, and
        # PmusTransport has no public `request`: the 2345z window armed,
        # decided a 10-contract intent and died with AttributeError inside
        # GuardedTransport's forwarding. `submit_body` previews the body,
        # validates the venue's echo field by field and posts THE SAME body,
        # and the guard admits against the mandate in front of it.
        ack = self.tp.submit_body(body)
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
        # THE ID MUST REACH THE CONSUMER. `completion_fill` is keyed on
        # `intent["venue_order_id"]` and NOTHING ever set it, so it returned
        # None for every completion, `_remainder_uncertain` fired and the
        # engine's pass was abandoned -- every taker completion failed closed,
        # in production and in replay. Third seam of the same kind as
        # `tp.fills()` and `tp.request()`: a consumer reading a key no
        # producer writes.
        intent["venue_order_id"] = oid

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
        # ALREADY BOOKED INLINE. `NativeEngine.on_completion_fill` applies this
        # fill the moment we return, so the resolver must not apply it again:
        # recording the confirmed quantity on the registry row makes the next
        # pass's increment zero. Without this, a taker completion is booked
        # TWICE -- once inline and once when /v1/order reports the same order
        # FILLED.
        #
        # DELIBERATELY NARROW: the row is NOT marked terminal and NOTHING is
        # released here. Doing that changed reservation accounting and dropped
        # measured exposure in tests/test_native_high_churn_lifecycle.py, and
        # releasing a reservation is a separate decision from not
        # double-counting a fill. The resolver still terminalises this order
        # when the venue says it is terminal.
        row = self.registry.orders.get(str(oid))
        if row is not None:
            row["filled"] = max(D(str(row.get("filled") or 0)), n)
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
    # THE SEAM. `unbooked_fills` asked `tp.fills()` behind a hasattr guard
    # PmusTransport never satisfied, so production booked no fills while every
    # test's double did. The PRODUCTION venue is required to expose
    # `order_status`; a double that does not is recorded rather than refused,
    # and the structural test
    # tests/test_r4_lifecycle_consolidation.py::
    #   test_the_production_venue_exposes_the_lifecycle_seam
    # is what stops production regressing to a silent skip.
    lifecycle_seam = venue is None or hasattr(venue, "order_status")
    eng = E.NativeEngine(clip=clip, lock_gate=OV.lock_gate_for(venue,
                                                               journal=journal))
    mp = NativeMoneyPath(market=market, engine=eng, transport=transport,
                         feed=feed, venue=venue, journal=journal)
    mp.lifecycle_seam = lifecycle_seam
    if journal is not None and not lifecycle_seam:
        try:
            journal.write("lifecycle_seam_absent", {
                "venue": type(venue).__name__,
                "note": ("no order_status: no order this session submits can "
                         "be resolved authoritatively")})
        except BaseException:                                  # noqa: BLE001
            pass
    return mp
