"""Idempotent, order-keyed fill ingestion and venue-vs-ledger
reconciliation (OWNER #167 comment 5584132560 item 2, risk-critical).

WHAT WENT WRONG, exactly, on aec-setkameua-vervas-kohboh-2026-09-08.

The venue executed four trades on that market: 0.20 at 05:57:09.234,
4.34 at 05:57:10.971, 4.54 at 05:57:12.137 (all NO 0.44 / YES 0.56) and
4.74 at 05:57:56.611. Our ledger recorded 0.20 and 4.54 and never saw the
4.34, so it read `directional_residual 0.00` on a book that was 4.34 units
long at resolution -- `resolution.beforeNet` was -4.3400 -- and the market
was valued at +0.3318 when the venue's own realized was -1.5778.

The missing 4.34 belonged to order CC1E3Y7TPQ45 (`spring_open_2`, NO 0.44,
qty 4.54), submitted 05:56:59.596 and rested 5.676 s. When the rest
deadline passed, `_await_order` called `cancel_exact`, IGNORED its return
value, read the order's status exactly once, saw nothing filled, wrote
`end_reason: deadline` and never looked at that order again. The cancel
was never confirmed. The order stayed live on the venue and filled 4.34
units 5.7 seconds later, by which time nothing in the process was
watching it.

So the defect is not arithmetic. It is that we treated "we asked for a
cancel" as "the order is over", and a single status read as a final
answer. Two orders can also be live at once while a cancel is
unconfirmed, which is how one intent (three successive NO 0.44 x 4.54
submissions) produced 9.08 units of fills.

WHAT THIS MODULE GUARANTEES.

  * The venue's CUMULATIVE filled quantity per order is the only
    authority. We ingest deltas against what we have already booked, so
    re-reading an order any number of times -- on reconnect, on replay, on
    duplicate delivery -- can only ever add what we had not yet seen, and
    can never double count.
  * A cumulative quantity that goes BACKWARDS is not silently accepted.
    It is a venue-evidence contradiction, and it fails closed.
  * An order is only closed when the venue says it is terminal. An
    unconfirmed cancel leaves the order OPEN in our own book, to be
    re-read until the venue gives a terminal state.
  * Raw provenance is preserved: every ingest keeps the order id, the
    cumulative quantity the venue reported and the observation time.

Nothing here decides strategy, prices anything, or sends an order. It is
an accounting layer with one job: if the venue says we own X, our risk
engine must know X.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional

# A cancel we asked for is not a cancel the venue performed. After an
# unconfirmed cancel we wait this long and re-read, so a fill that landed
# inside the race is seen rather than lost.
CANCEL_SETTLE_S = 1.0

TERMINAL_TOKENS = ("CANCEL", "EXPIRED", "REJECT", "FILLED")


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                   # noqa: BLE001
        return None


def cum_filled(order: Mapping[str, Any]) -> Optional[D]:
    """The venue's cumulative filled quantity for one order, under any of
    the field names it has served. Absent stays absent -- an unreadable
    order is not an order with zero fills."""
    for key in ("cumQuantity", "filledQuantity", "cumQty"):
        v = _d(order.get(key))
        if v is not None:
            return v
    return None


def order_state(order: Mapping[str, Any]) -> str:
    return str(order.get("state") or order.get("status") or "").upper()


def is_terminal(order: Mapping[str, Any]) -> bool:
    """Terminal means the VENUE says no further fill can arrive. A
    partially filled order that is still open is NOT terminal."""
    state = order_state(order)
    if not state:
        return False
    if "PARTIAL" in state:
        return False
    return any(tok in state for tok in TERMINAL_TOKENS)


class FillLedgerError(Exception):
    """Venue evidence contradicts what we have already booked."""


class FillLedger:
    """Per-order cumulative booking. One instance per market session."""

    def __init__(self) -> None:
        self._booked: Dict[str, D] = {}
        self._open: Dict[str, dict] = {}
        self.provenance: List[dict] = []
        self.contradictions: List[dict] = []

    # ---------------------------------------------------------------- ingest
    def ingest(self, order_id: str, order: Mapping[str, Any],
               *, observed_at: Optional[float] = None) -> D:
        """Book whatever this observation adds, and return ONLY the delta.

        Called twice with the same observation the second call returns 0.
        Called with a larger cumulative quantity it returns the increase.
        Called with a smaller one it records a contradiction and returns 0
        without un-booking anything -- inventory we have already acted on
        is never silently reduced."""
        cum = cum_filled(order)
        if cum is None:
            return D("0")
        prior = self._booked.get(order_id, D("0"))
        if cum < prior:
            self.contradictions.append({
                "order_id": order_id, "booked": str(prior),
                "venue_cum": str(cum),
                "why": ("the venue reported a smaller cumulative quantity "
                        "than we have already booked"),
                "observed_at": observed_at if observed_at is not None else time.time(),
            })
            return D("0")
        delta = cum - prior
        if delta > 0:
            self._booked[order_id] = cum
            self.provenance.append({
                "order_id": order_id, "venue_cum": str(cum),
                "delta": str(delta), "state": order_state(order),
                "observed_at": observed_at if observed_at is not None else time.time(),
            })
        return delta

    def booked(self, order_id: str) -> D:
        return self._booked.get(order_id, D("0"))

    def total_booked(self) -> D:
        return sum(self._booked.values(), D("0"))

    # ------------------------------------------------------- open-order set
    def track(self, order_id: str, *, meta: Optional[Mapping[str, Any]] = None
              ) -> None:
        """An order we submitted. It stays open until the VENUE says it is
        terminal -- not when we ask for a cancel, and not when we stop
        waiting for it."""
        self._open.setdefault(order_id, dict(meta or {}))

    def close_if_terminal(self, order_id: str, order: Mapping[str, Any]) -> bool:
        if is_terminal(order):
            self._open.pop(order_id, None)
            return True
        return False

    def open_orders(self) -> List[str]:
        return sorted(self._open)

    def close_on_venue_rejection(self, order_id: str,
                                 order: Optional[Mapping[str, Any]]) -> bool:
        """A venue REJECTION is the venue's own terminal verdict, and it is
        the one case where an order can be closed without a successful
        re-read of a live record.

        LIVE 2026-09-08 12:14Z: `_await_order` returned early on
        RejectedNoFill without closing the order, so two post-only
        rejections stayed tracked as open, their later re-read failed, and
        `may_assert_flat` was pinned false on the market. Rejections are
        common -- 3 of 30 lifecycles -- so a guard that can never clear
        stops being a signal.

        This does NOT weaken the guard. The order is closed only when the
        venue's own record says REJECTED with a cumulative quantity of
        zero. A record we cannot read, or one that shows any fill at all,
        leaves the order OPEN exactly as before."""
        if order is None:
            return False
        state = order_state(order)
        if "REJECT" not in state:
            return False
        cum = cum_filled(order)
        if cum is None or cum != self.booked(order_id):
            # the venue calls it rejected but reports a quantity we have
            # not booked, or none at all: that is a contradiction, not a
            # clean terminal state
            return False
        self._open.pop(order_id, None)
        return True

    # --------------------------------------------------------- reconcile
    def reconcile(self, read_order: Callable[[str], Mapping[str, Any]],
                  *, order_ids: Optional[List[str]] = None) -> dict:
        """Re-read orders and book anything they have gained since we last
        looked. Safe to run at any time and any number of times.

        `read_order` may raise for an order the venue will not serve; that
        order stays OPEN and is reported as unreadable. Unreadable is never
        treated as finished, because a finished order is exactly what we
        would need proof of."""
        ids = list(order_ids if order_ids is not None else self.open_orders())
        added: Dict[str, str] = {}
        unreadable: List[str] = []
        closed: List[str] = []
        # OWNER 2026-09-17 P0: keep WHY each read failed. `unreadable` used to
        # be a bare list of ids, so a 2026-09-17 forensics question -- were
        # these timeouts, rate limits, or the venue refusing rejected orders?
        # -- could not be answered from the record at all, and had to be
        # inferred from how long the loop took. The exception type and its
        # bounded message are kept per id; the classification is unchanged.
        why: Dict[str, str] = {}
        for oid in ids:
            try:
                order = read_order(oid)
            except Exception as exc:                    # noqa: BLE001
                unreadable.append(oid)
                code = getattr(exc, "code", None)
                why[oid] = "%s:%s:%s" % (type(exc).__name__,
                                         code if code is not None else "-",
                                         str(exc)[:160])
                continue
            delta = self.ingest(oid, order)
            if delta > 0:
                added[oid] = str(delta)
            if self.close_if_terminal(oid, order):
                closed.append(oid)
        return {
            "checked": len(ids), "recovered_fills": added,
            "recovered_qty": str(sum((D(v) for v in added.values()), D("0"))),
            "closed": closed, "unreadable": unreadable,
            # OWNER 2026-09-17 P0: the actual failure per unreadable id,
            # "<ExceptionType>:<http code or ->:<bounded message>", so a
            # future read failure is diagnosable from the record instead of
            # inferred from loop timing.
            "unreadable_why": why,
            "still_open": self.open_orders(),
            "contradictions": len(self.contradictions),
        }


# --------------------------------------------------------------------------
# venue quantity against ledger quantity, on the live path
# --------------------------------------------------------------------------

def venue_vs_ledger(venue_qty: Any, ledger_qty: Any,
                    *, tolerance: str = "0.0001") -> dict:
    """Compare the venue's own traded quantity with ours.

    `agrees` is None when either side is missing. Unknown is NOT agreement:
    a caller that needs to assert flatness or a locked value must fail
    closed on None exactly as it does on False."""
    v, l = _d(venue_qty), _d(ledger_qty)
    tol = D(tolerance)
    if v is None or l is None:
        return {"venue_qty": None if v is None else str(v),
                "ledger_qty": None if l is None else str(l),
                "delta": None, "agrees": None,
                "why": "no venue quantity to reconcile against"}
    delta = v - l
    return {"venue_qty": str(v), "ledger_qty": str(l), "delta": str(delta),
            "agrees": bool(abs(delta) <= tol),
            "why": ("the venue's traded quantity matches ours"
                    if abs(delta) <= tol else
                    "the venue traded a quantity our ledger does not hold")}


def may_assert_flat(recon: Mapping[str, Any]) -> bool:
    """Whether any calculation that ASSUMES flatness, a zero residual or a
    locked value is allowed to proceed. Fails closed on disagreement and
    on missing evidence alike."""
    return recon.get("agrees") is True
