"""Durable resting-order ledger: exposure that a restart cannot forget.

THE DEFECT THIS CLOSES. `MutationLedger` makes the ATTEMPT durable, so a
restart knows how many mutations were spent. It does not record what is still
RESTING, for how much, or how much of it has filled. So a process that placed,
partially filled, re-rested, then died could come back believing it had no
exposure -- because process memory was the only place the working set lived.
Counting mutations bounds how often you may act; it does not bound what you
already owe.

THE RULE THAT MAKES IT SAFE. A placement is written and fsynced BEFORE the
frame is transmitted, exactly as the mutation budget is spent before the send.
Recording on success loses precisely the orders that matter -- the ones whose
outcome we never learned. So the crash window between "venue accepted" and
"ledger recorded" is closed from the other side: the ledger records the INTENT
first, and an intent whose result never arrived is UNCERTAIN, which counts as
live exposure until a human or the venue resolves it.

UNCERTAIN IS NEVER ABSENT. Ambiguous placement, ambiguous cancel, transport
error, torn ledger line -- every one becomes an order carrying its full
notional. There is no path in this module where not knowing produces zero.

WHAT IT DOES NOT DO. It authorizes nothing. It holds no credential, imports no
transport and has no order verb. It is a record and a set of refusals.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------- the envelope
# Owner-stated envelope for this repair. These are CEILINGS, never grants: the
# executor's own tighter limits (CAMPAIGN_RISK_CAP $1.00, RESIDUAL_CAP 1) keep
# applying, and the binding constraint is whichever refuses first. Nothing here
# relaxes an existing limit.
MAX_COMMITTED_USD = D("2.50")
MAX_DIRECTIONAL_RESIDUAL_USD = D("2.00")
MAX_WORKING_ORDERS = 2
EXPLICIT_QTY = D("1")          # q=1 explicit-only; any other size is refused

# Row kinds. Append-only; state is the fold of the log.
PLACE_INTENT = "PLACE_INTENT"
PLACE_RESULT = "PLACE_RESULT"
FILL = "FILL"
CANCEL_INTENT = "CANCEL_INTENT"
CANCEL_RESULT = "CANCEL_RESULT"

# Lifecycle states.
INTENT = "INTENT"                    # written, not yet known to be accepted
WORKING = "WORKING"                  # venue accepted, resting
PARTIAL = "PARTIALLY_FILLED"
FILLED = "FILLED"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"                # venue refused: no exposure
UNCERTAIN = "UNCERTAIN"              # outcome never learned: FULL exposure

# States that still owe money or could still fill.
LIVE_STATES = (INTENT, WORKING, PARTIAL, UNCERTAIN)
BLOCKING_STATES = (INTENT, UNCERTAIN)


class LedgerUnreadable(Exception):
    """The ledger exists but cannot be trusted. Never downgraded to empty."""


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


class RestingOrderLedger:
    """Append-only, fsynced lifecycle record for every order we tried to rest."""

    def __init__(self, path: str, *, session_id: str = "") -> None:
        self.path = str(path)
        self.session_id = str(session_id)
        self.rows: List[Dict[str, Any]] = []
        self.torn = 0
        self.readable = True
        self._load()

    # ------------------------------------------------------------- durability
    def _append(self, row: Mapping[str, Any]) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self.rows.append(dict(row))

    def _load(self) -> None:
        """Replay the log. A torn line is exposure of unknown identity.

        It cannot be skipped: the line that failed to finish writing is exactly
        the one describing an order we may still owe. It becomes a synthetic
        UNCERTAIN order, which blocks arming until a human resolves it.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        self.torn += 1
                        self.rows.append({
                            "kind": PLACE_INTENT,
                            "client_order_id": "TORN-%d" % self.torn,
                            "torn": True, "qty": str(EXPLICIT_QTY),
                            "price": None, "leg": None,
                            "notional": str(MAX_COMMITTED_USD),
                            "why": "unparseable ledger row: exposure of unknown size"})
                        continue
                    if isinstance(r, dict):
                        self.rows.append(r)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # An unreadable ledger is NOT an empty one.
            self.readable = False
            self._err = str(exc)

    # ------------------------------------------------------------- recording
    def record_place_intent(self, *, leg: str, price: Any, qty: Any,
                            client_order_id: Optional[str] = None,
                            **extra: Any) -> str:
        """Durably claim exposure BEFORE the order is transmitted.

        Returns the clientOrderId the caller must send. The row is on disk and
        fsynced before this returns, so a crash on the very next instruction
        still shows the exposure.
        """
        p, q = _dec(price), _dec(qty)
        if p is None or q is None:
            raise ValueError("price and qty must be numeric to claim exposure")
        coid = client_order_id or ("q1-%s" % uuid.uuid4().hex[:16])
        self._append({"kind": PLACE_INTENT, "t": time.time(),
                      "session_id": self.session_id, "client_order_id": coid,
                      "leg": str(leg), "price": str(p), "qty": str(q),
                      "original_qty": str(q), "notional": str(p * q), **extra})
        return coid

    def record_place_result(self, coid: str, outcome: str, *,
                            venue_order_id: Any = None, **extra: Any) -> None:
        """ACCEPTED / REJECTED / UNCERTAIN. Anything unrecognised is UNCERTAIN."""
        if outcome not in (WORKING, REJECTED, UNCERTAIN):
            outcome, extra = UNCERTAIN, dict(extra, why="unrecognised outcome %r" % (outcome,))
        self._append({"kind": PLACE_RESULT, "t": time.time(),
                      "client_order_id": str(coid), "outcome": outcome,
                      "venue_order_id": (None if venue_order_id is None
                                         else str(venue_order_id)), **extra})

    def record_fill(self, coid: str, qty: Any, price: Any = None, **extra: Any) -> None:
        q = _dec(qty)
        if q is None or q <= 0:
            raise ValueError("a fill must carry a positive quantity")
        self._append({"kind": FILL, "t": time.time(), "client_order_id": str(coid),
                      "qty": str(q), "price": (None if price is None else str(_dec(price))),
                      **extra})

    def record_cancel_intent(self, coid: str, **extra: Any) -> None:
        self._append({"kind": CANCEL_INTENT, "t": time.time(),
                      "client_order_id": str(coid), **extra})

    def record_cancel_result(self, coid: str, outcome: str, **extra: Any) -> None:
        """CANCELLED only on CONFIRMED cancel. Everything else is UNCERTAIN.

        A cancel that timed out, errored, or returned something we do not
        recognise leaves the order possibly still resting, so it must not be
        allowed to release exposure.
        """
        if outcome != CANCELLED:
            outcome, extra = UNCERTAIN, dict(extra, why="cancel not confirmed: %r" % (outcome,))
        self._append({"kind": CANCEL_RESULT, "t": time.time(),
                      "client_order_id": str(coid), "outcome": outcome, **extra})

    # ------------------------------------------------------------- the fold
    def orders(self) -> Dict[str, Dict[str, Any]]:
        """Fold the log into per-order lifecycle state."""
        if not self.readable:
            raise LedgerUnreadable(
                "resting-order ledger at %s could not be read (%s); an "
                "unreadable ledger is never treated as an empty one"
                % (self.path, getattr(self, "_err", "unknown")))
        o: Dict[str, Dict[str, Any]] = {}
        for r in self.rows:
            coid = str(r.get("client_order_id") or "")
            if not coid:
                continue
            k = r.get("kind")
            if k == PLACE_INTENT:
                q = _dec(r.get("qty")) or D("0")
                o[coid] = {"client_order_id": coid, "leg": r.get("leg"),
                           "price": _dec(r.get("price")),
                           "original_qty": q, "remaining_qty": q,
                           "filled_qty": D("0"), "state": INTENT,
                           "venue_order_id": None, "torn": bool(r.get("torn")),
                           "notional": _dec(r.get("notional")) or D("0")}
            elif coid in o:
                e = o[coid]
                if k == PLACE_RESULT:
                    out = r.get("outcome")
                    if out == REJECTED:
                        e["state"], e["remaining_qty"] = REJECTED, D("0")
                    elif out == UNCERTAIN:
                        e["state"] = UNCERTAIN
                    else:
                        # a fill may already have landed before the ack
                        e["state"] = PARTIAL if e["filled_qty"] > 0 else WORKING
                    if r.get("venue_order_id"):
                        e["venue_order_id"] = str(r["venue_order_id"])
                elif k == FILL:
                    q = _dec(r.get("qty")) or D("0")
                    e["filled_qty"] += q
                    e["remaining_qty"] = max(D("0"), e["original_qty"] - e["filled_qty"])
                    if e["state"] != UNCERTAIN:
                        e["state"] = FILLED if e["remaining_qty"] <= 0 else PARTIAL
                elif k == CANCEL_INTENT:
                    e["cancel_requested"] = True
                elif k == CANCEL_RESULT:
                    if r.get("outcome") == CANCELLED:
                        # a confirmed cancel releases ONLY the unfilled part
                        e["remaining_qty"] = D("0")
                        e["state"] = FILLED if e["filled_qty"] > 0 else CANCELLED
                    else:
                        e["state"] = UNCERTAIN
        for e in o.values():
            px = e["price"] if e["price"] is not None else MAX_COMMITTED_USD
            e["committed_usd"] = (px * e["remaining_qty"]
                                  if e["state"] in LIVE_STATES else D("0"))
            # an order of unknown price charges the whole envelope
            if e["state"] in LIVE_STATES and e["price"] is None:
                e["committed_usd"] = MAX_COMMITTED_USD
        return o

    # ------------------------------------------------------------- exposure
    def live(self) -> List[Dict[str, Any]]:
        return [e for e in self.orders().values() if e["state"] in LIVE_STATES]

    def uncertain(self) -> List[Dict[str, Any]]:
        return [e for e in self.orders().values() if e["state"] in BLOCKING_STATES]

    def working_orders(self) -> int:
        return len(self.live())

    def committed_usd(self) -> D:
        return sum((e["committed_usd"] for e in self.live()), D("0"))

    def filled_by_leg(self) -> Dict[str, D]:
        out: Dict[str, D] = {}
        for e in self.orders().values():
            if e["filled_qty"] > 0:
                out[str(e["leg"])] = out.get(str(e["leg"]), D("0")) + e["filled_qty"]
        return out

    def directional_residual_usd(self, legs: Tuple[str, str] = ("YES", "NO")) -> D:
        """Filled imbalance between the two legs, valued at the envelope ceiling.

        Valued conservatively on purpose: the residual we care about is what it
        could cost to be wrong, and a cheaper valuation would let a bigger
        imbalance pass the same cap.
        """
        f = self.filled_by_leg()
        a, b = f.get(legs[0], D("0")), f.get(legs[1], D("0"))
        return abs(a - b) * MAX_DIRECTIONAL_RESIDUAL_USD / max(EXPLICIT_QTY, D("1"))

    def projected_residual_usd(self, leg: str, qty: Any,
                               legs: Tuple[str, str] = ("YES", "NO")) -> D:
        """Residual if every live order AND this proposed one filled.

        Projection is the point: a cap checked only against settled fills
        admits an order that, together with what is already resting, breaks it.
        """
        f = dict(self.filled_by_leg())
        for e in self.live():
            f[str(e["leg"])] = f.get(str(e["leg"]), D("0")) + e["remaining_qty"]
        q = _dec(qty) or D("0")
        f[str(leg)] = f.get(str(leg), D("0")) + q
        a, b = f.get(legs[0], D("0")), f.get(legs[1], D("0"))
        return abs(a - b) * MAX_DIRECTIONAL_RESIDUAL_USD / max(EXPLICIT_QTY, D("1"))


# ------------------------------------------------------------------ preflight
UNREADABLE = "UNREADABLE"


def venue_open_ids(venue_orders: Optional[Sequence[Mapping[str, Any]]]
                   ) -> Optional[set]:
    """Venue order ids, or None when the venue did not answer.

    None and [] are different facts and only one of them is safe: "the venue
    has no open orders" releases exposure, "the venue did not answer" must not.
    """
    if venue_orders is None:
        return None
    out = set()
    for o in venue_orders:
        for f in ("orderId", "order_id", "id", "clientOrderId", "client_order_id"):
            if o.get(f) not in (None, "", False):
                out.add(str(o[f]))
                break
    return out


def preflight(ledger: RestingOrderLedger,
              venue_orders: Optional[Sequence[Mapping[str, Any]]]
              ) -> Dict[str, Any]:
    """May this process arm? Fail closed on every ambiguity.

    Refuses when EITHER source shows exposure, when the two DISAGREE, when any
    order is UNCERTAIN, or when either source is UNREADABLE. Only a positive,
    agreeing, empty answer from both sides may arm.
    """
    reasons: List[str] = []
    try:
        live = ledger.live()
        unc = ledger.uncertain()
        readable = True
    except LedgerUnreadable as exc:
        return {"may_arm": False, "verdict": UNREADABLE,
                "reasons": [str(exc)], "ledger_readable": False,
                "ledger_live": None, "venue_open": None}
    vids = venue_open_ids(venue_orders)
    if vids is None:
        reasons.append("venue open orders UNREADABLE: a restart never assumes "
                       "the venue is flat")
    if unc:
        reasons.append("%d order(s) UNCERTAIN: %s"
                       % (len(unc), ", ".join(sorted(e["client_order_id"] for e in unc))))
    if live:
        reasons.append("durable ledger shows %d live order(s) committing $%s"
                       % (len(live), ledger.committed_usd()))
    if vids:
        reasons.append("venue reports %d open order(s): %s"
                       % (len(vids), ", ".join(sorted(vids))))
    if vids is not None:
        known = {e["venue_order_id"] for e in live if e["venue_order_id"]}
        known |= {e["client_order_id"] for e in live}
        only_venue = vids - known
        only_ledger = {e["venue_order_id"] or e["client_order_id"] for e in live} - vids
        if only_venue or only_ledger:
            reasons.append(
                "DISAGREEMENT: venue-only %s ledger-only %s"
                % (sorted(only_venue) or "none", sorted(only_ledger) or "none"))
    verdict = ("CLEAN" if not reasons
               else (UNREADABLE if vids is None else "EXPOSURE_OR_DISAGREEMENT"))
    return {"may_arm": not reasons, "verdict": verdict, "reasons": reasons,
            "ledger_readable": readable,
            "ledger_live": [e["client_order_id"] for e in live],
            "ledger_committed_usd": str(ledger.committed_usd()),
            "venue_open": (None if vids is None else sorted(vids)),
            "uncertain": [e["client_order_id"] for e in unc]}


def admit_place(ledger: RestingOrderLedger, *, leg: str, price: Any, qty: Any,
                venue_orders: Optional[Sequence[Mapping[str, Any]]] = None,
                max_working: int = MAX_WORKING_ORDERS,
                max_committed: D = MAX_COMMITTED_USD,
                max_residual: D = MAX_DIRECTIONAL_RESIDUAL_USD) -> Dict[str, Any]:
    """May we transmit one more PLACE? Budgets read DURABLE state, not memory.

    Called before transmission. The caller must then write the intent (which
    fsyncs) and only then send, so the exposure is on disk before the frame is.
    """
    p, q = _dec(price), _dec(qty)
    if q is None or q != EXPLICIT_QTY:
        return {"admit": False, "reason":
                "q=1 explicit-only: qty %r is not the authorized size" % (qty,)}
    if p is None or p <= 0:
        return {"admit": False, "reason": "price %r is not usable" % (price,)}
    try:
        live = ledger.live()
        committed = ledger.committed_usd()
    except LedgerUnreadable as exc:
        return {"admit": False, "reason": str(exc)}
    if ledger.uncertain():
        return {"admit": False, "reason":
                "refusing to place while %d order(s) are UNCERTAIN"
                % len(ledger.uncertain())}
    if venue_orders is not None:
        pf = preflight(ledger, venue_orders)
        if not pf["may_arm"]:
            return {"admit": False, "reason": "preflight refused: %s"
                    % "; ".join(pf["reasons"]), "preflight": pf}
    working_after = len(live) + 1
    committed_after = committed + p * q
    residual_after = ledger.projected_residual_usd(leg, q)
    if working_after > max_working:
        return {"admit": False, "reason":
                "max_working_orders %d would be exceeded (%d live + 1)"
                % (max_working, len(live))}
    if committed_after > max_committed:
        return {"admit": False, "reason":
                "max_committed_usd $%s would be exceeded ($%s live + $%s)"
                % (max_committed, committed, p * q)}
    if residual_after > max_residual:
        return {"admit": False, "reason":
                "projected directional residual $%s would exceed $%s"
                % (residual_after, max_residual)}
    return {"admit": True, "reason": "within envelope",
            "working_after": working_after,
            "committed_after_usd": str(committed_after),
            "projected_residual_usd": str(residual_after),
            "computed_from": "DURABLE_LEDGER_NOT_PROCESS_MEMORY"}
