"""SHADOW-vs-LIVE paired logging for the first bounded BTC15m live window.

THE PRINCIPAL EXPERIMENT
    Did an order the q50 shadow model counted as FILLED actually fill live, at
    comparable economics?

Checkpoint 25 advanced on a number -- mean q50 PROFIT_LOCK_5 SHADOW TRADING NET
+6.1167/window -- computed under the frozen `queue_aware` maker-fill treatment.
That treatment is a MODEL. Every dollar of the +6.1167 rests on it. This module
exists to measure it against the venue, one intended action at a time, and it is
built so that the measurement cannot flatter itself.

THE RULE THAT SHAPES THE WHOLE MODULE

    NO SHADOW FILL MAY BE PROMOTED TO A LIVE FILL.

A row's live side starts UNKNOWN and reaches FILLED only through
`venue_fill()`, which requires a venue-sourced authority (VENUE_FILL or
RECONCILIATION). There is no argument, flag or code path by which
`shadow.filled` can set `live.filled`. The honest outcome of the experiment may
well be that the shadow model counted fills the venue never gave us; a logger
that defaulted the live side to the shadow side would report a clean parity that
never happened. `NONFILL` is a RESULT, recorded as deliberately as a fill.

WHAT EACH ROW CARRIES, in the order the owner asked for it:

    frozen shadow action -> live submission -> ACK -> actual fill/nonfill
    -> price -> quantity -> inventory -> floor -> LOCK state
    -> completion/cancel -> venue reconciliation -> settlement
    -> authoritative cash P&L

A row is INCOMPLETE until its live side is resolved, and `unresolved()` names
every such row so a window cannot be written up as if it were.

ACCOUNTING LABELS are the repository's standard ones and stay separate:
the shadow side is SHADOW, the live side is SUBMITTED / ACK / FILLED /
SETTLED / REALIZED. `authoritative_cash_pnl` is populated only from settlement.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

#: the only sources that may move a row's live side to FILLED. Same whitelist as
#: pmus_lock5_translation.apply_authoritative_fill -- an ACK is a receipt.
FILL_AUTHORITIES = ("VENUE_FILL", "RECONCILIATION")

#: live-side lifecycle. UNKNOWN is where every row starts; it is not a synonym
#: for NONFILL, and reporting must not collapse the two.
LIVE_STATES = ("UNKNOWN", "SUBMITTED", "ACKED", "FILLED", "NONFILL",
               "CANCELLED", "REFUSED")

_RESOLVED = ("FILLED", "NONFILL", "CANCELLED", "REFUSED")


class PromotionRefused(RuntimeError):
    """An attempt to derive the live side from the shadow side, or from an ACK."""


def _s(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def _d(v: Any) -> Optional[D]:
    return None if v is None else D(str(v))


class PairedRow:
    """One intended action: what the frozen model did, and what the venue did."""

    def __init__(self, *, seq: int, market: str, window: str, action: str,
                 side: str, shadow_price: Any, shadow_qty: Any,
                 shadow_filled: bool, shadow_treatment: str,
                 shadow_floor: Any, shadow_locked: bool) -> None:
        self.seq = int(seq)
        self.market = str(market)
        self.window = str(window)
        # ---- frozen shadow action (never mutated after construction)
        self.action = str(action).upper()
        self.side = str(side)
        self.shadow_price = _d(shadow_price)
        self.shadow_qty = _d(shadow_qty)
        self.shadow_filled = bool(shadow_filled)
        self.shadow_treatment = str(shadow_treatment)
        self.shadow_floor = _d(shadow_floor)
        self.shadow_locked = bool(shadow_locked)
        # ---- live side
        self.live_state = "UNKNOWN"
        self.submission: Optional[Dict[str, Any]] = None
        self.ack: Optional[Dict[str, Any]] = None
        self.live_price: Optional[D] = None
        self.live_qty: Optional[D] = None
        self.fill_source: Optional[str] = None
        self.nonfill_reason: Optional[str] = None
        self.inventory: Optional[Dict[str, str]] = None
        self.live_floor: Optional[D] = None
        self.live_locked: Optional[bool] = None
        self.completion: Optional[Dict[str, Any]] = None
        self.cancel: Optional[Dict[str, Any]] = None
        self.reconciliation: Optional[Dict[str, Any]] = None
        self.settlement: Optional[Dict[str, Any]] = None
        self.authoritative_cash_pnl: Optional[D] = None
        #: set by PairedLog.intend. Every live transition persists immediately:
        #: a crash between an ACK and its fill is exactly the case the paired
        #: record is needed for, so it must already be on disk.
        self._log: Optional["PairedLog"] = None

    def _persist(self) -> None:
        if self._log is not None:
            self._log.flush()

    # ------------------------------------------------------- live transitions

    def submitted(self, body: Mapping[str, Any]) -> None:
        self.submission = dict(body)
        self.live_state = "SUBMITTED"
        self._persist()

    def acked(self, ack: Mapping[str, Any]) -> None:
        """An ACK is a receipt. It NEVER sets FILLED, whatever it claims.

        Probe 04 filled two milliseconds after an ACK that said `filled_qty 0`.
        The inverse is just as possible, so an ACK carrying a non-zero
        `filled_qty` is recorded verbatim and still does not resolve the row.
        """
        self.ack = dict(ack)
        self.live_state = "ACKED"
        self._persist()

    def venue_fill(self, *, price: Any, qty: Any, source: str,
                   inventory: Optional[Mapping[str, Any]] = None,
                   floor: Any = None, locked: Optional[bool] = None) -> None:
        if source not in FILL_AUTHORITIES:
            raise PromotionRefused(
                "a live fill requires %s, got %r" % (" or ".join(FILL_AUTHORITIES), source))
        q = _d(qty)
        if q is None or q <= 0:
            raise PromotionRefused("a live fill needs a positive quantity, got %r" % (qty,))
        self.live_price, self.live_qty = _d(price), q
        self.fill_source = str(source)
        self.live_state = "FILLED"
        if inventory is not None:
            self.inventory = {k: str(v) for k, v in dict(inventory).items()}
        if floor is not None:
            self.live_floor = _d(floor)
        if locked is not None:
            self.live_locked = bool(locked)
        self._persist()

    def nonfill(self, reason: str) -> None:
        """A RESULT, not an absence. This is the row the experiment is about."""
        self.nonfill_reason = str(reason)
        self.live_state = "NONFILL"
        self._persist()

    def cancelled(self, **detail: Any) -> None:
        self.cancel = {k: _s(v) for k, v in detail.items()}
        self.live_state = "CANCELLED"
        self._persist()

    def refused(self, verdict: Mapping[str, Any]) -> None:
        self.submission = self.submission or {}
        self.cancel = {"mandate_refusal": json.dumps(dict(verdict), default=str)}
        self.live_state = "REFUSED"
        self._persist()

    def completed(self, **detail: Any) -> None:
        self.completion = {k: _s(v) for k, v in detail.items()}
        self._persist()

    def reconciled(self, **detail: Any) -> None:
        self.reconciliation = {k: _s(v) for k, v in detail.items()}
        self._persist()

    def settled(self, *, authoritative_cash_pnl: Any, **detail: Any) -> None:
        self.settlement = {k: _s(v) for k, v in detail.items()}
        self.authoritative_cash_pnl = _d(authoritative_cash_pnl)
        self._persist()

    # ------------------------------------------------------------- the verdict

    @property
    def resolved(self) -> bool:
        return self.live_state in _RESOLVED

    @property
    def live_filled(self) -> bool:
        """Derived ONLY from the live state. Never from `shadow_filled`."""
        return self.live_state == "FILLED"

    def parity(self) -> Dict[str, Any]:
        """Shadow-vs-live comparison for THIS action. None while unresolved."""
        if not self.resolved:
            return {"verdict": "UNRESOLVED", "live_state": self.live_state}
        if self.shadow_filled and self.live_filled:
            verdict = "BOTH_FILLED"
        elif self.shadow_filled:
            verdict = "SHADOW_ONLY"          # the model over-counted
        elif self.live_filled:
            verdict = "LIVE_ONLY"            # the model under-counted
        else:
            verdict = "NEITHER_FILLED"
        px_delta = qty_delta = None
        if verdict == "BOTH_FILLED":
            if self.shadow_price is not None and self.live_price is not None:
                px_delta = self.live_price - self.shadow_price
            if self.shadow_qty is not None and self.live_qty is not None:
                qty_delta = self.live_qty - self.shadow_qty
        return {"verdict": verdict,
                "live_state": self.live_state,
                "shadow_filled": self.shadow_filled,
                "live_filled": self.live_filled,
                "price_delta": _s(px_delta),
                "qty_delta": _s(qty_delta),
                "nonfill_reason": self.nonfill_reason,
                "fill_source": self.fill_source}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq, "market": self.market, "window": self.window,
            "shadow": {"action": self.action, "side": self.side,
                       "price": _s(self.shadow_price), "qty": _s(self.shadow_qty),
                       "filled": self.shadow_filled,
                       "fill_treatment": self.shadow_treatment,
                       "floor": _s(self.shadow_floor),
                       "locked": self.shadow_locked,
                       "label": "SHADOW"},
            "live": {"state": self.live_state, "submission": self.submission,
                     "ack": self.ack, "price": _s(self.live_price),
                     "qty": _s(self.live_qty), "fill_source": self.fill_source,
                     "nonfill_reason": self.nonfill_reason,
                     "inventory": self.inventory, "floor": _s(self.live_floor),
                     "locked": self.live_locked, "completion": self.completion,
                     "cancel": self.cancel, "reconciliation": self.reconciliation,
                     "settlement": self.settlement,
                     "authoritative_cash_pnl": _s(self.authoritative_cash_pnl)},
            "parity": self.parity(),
        }


class PairedLog:
    """Every intended action in one window, in order, append-only."""

    def __init__(self, *, market: str, window: str, path: Optional[str] = None,
                 shadow_treatment: str = "queue_aware") -> None:
        self.market, self.window = str(market), str(window)
        self.shadow_treatment = str(shadow_treatment)
        self.path = path
        self.rows: List[PairedRow] = []

    def intend(self, *, action: str, side: str, shadow_price: Any,
               shadow_qty: Any, shadow_filled: bool, shadow_floor: Any = None,
               shadow_locked: bool = False) -> PairedRow:
        row = PairedRow(seq=len(self.rows) + 1, market=self.market,
                        window=self.window, action=action, side=side,
                        shadow_price=shadow_price, shadow_qty=shadow_qty,
                        shadow_filled=shadow_filled,
                        shadow_treatment=self.shadow_treatment,
                        shadow_floor=shadow_floor, shadow_locked=shadow_locked)
        row._log = self
        self.rows.append(row)
        self.flush()
        return row

    def unresolved(self) -> List[int]:
        """Rows whose live side never resolved. A window with any of these is
        NOT a completed measurement and must not be reported as one."""
        return [r.seq for r in self.rows if not r.resolved]

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for r in self.rows:
            counts[r.parity()["verdict"]] = counts.get(r.parity()["verdict"], 0) + 1
        shadow_n = sum(1 for r in self.rows if r.shadow_filled)
        both = counts.get("BOTH_FILLED", 0)
        realized = [r.authoritative_cash_pnl for r in self.rows
                    if r.authoritative_cash_pnl is not None]
        return {
            "market": self.market, "window": self.window,
            "shadow_fill_treatment": self.shadow_treatment,
            "intended_actions": len(self.rows),
            "verdicts": counts,
            "unresolved": self.unresolved(),
            "complete": not self.unresolved(),
            # the principal experiment, as a single number. WITHHELD rather than
            # defaulted to 1.0 when the shadow model counted no fills at all.
            "shadow_fills_confirmed_live": both,
            "shadow_fills_counted": shadow_n,
            "shadow_fill_confirmation_rate": (
                None if shadow_n == 0 else str(D(both) / D(shadow_n))),
            # filled throughput, per the ladder: authenticated/reconciled fills,
            # NOT submitted quantity.
            "reconciled_filled_qty": str(sum(
                (r.live_qty for r in self.rows
                 if r.live_filled and r.live_qty is not None), D(0))),
            "submitted_qty": str(sum(
                (r.shadow_qty for r in self.rows
                 if r.submission is not None and r.shadow_qty is not None), D(0))),
            "authoritative_cash_pnl": (
                None if not realized else str(sum(realized, D(0)))),
            "label": "SETTLED/REALIZED only where authoritative_cash_pnl is set",
        }

    def flush(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump({"summary": self.summary(),
                       "rows": [r.to_dict() for r in self.rows]}, fh, indent=2)
        os.replace(tmp, self.path)
