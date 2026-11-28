"""Record which side a window settled on. READ-ONLY; places no order.

WHY THIS EXISTS, and it is the narrowest possible gap. The paired-entry
economics need two conditional settlement probabilities --
P(UP settles | UP filled alone) and P(UP settles | DOWN filled alone) --
because the leg that fills alone is the adversely selected one, and that
single parameter decided the 2026-11-09 15:30Z loss. The entry turns
positive-to-negative at a skew of 0.0871, so the estimate has to be good to
about +/-0.09, which needs on the order of 400 observations.

We have 6. Not because the markets are rare -- there are 96 BTC15m windows
a day -- but because the recorded tapes STOP EARLY: of ten windows in the
replay sources, four end between 31% and 59% of the way through, so the
terminal book never shows which side won. The missing field is one line per
window.

WHAT IT COSTS: nothing. No order, no venue write, no capital, no new
service. It observes windows the executor already watches.

WHAT IT DELIBERATELY DOES NOT DO. It does not infer a settlement from a
mid-window price, it does not interpolate, and it does not fill a gap with
a guess. A window whose terminal book is early or ambiguous is recorded as
UNDETERMINED with the reason, because an unobserved settlement must stay
unobserved rather than become a weak data point that then calibrates a
money decision.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

#: a 15-minute window.
WINDOW_S = 900.0

#: a terminal book is informative only this far into the window. Earlier
#: than this the price still has time to cross, so it states a probability
#: and not an outcome.
LATE_S = 700.0

#: and only this close to a boundary. 0.03 carries an implied ~3% chance
#: for the other side, which is REPORTED on every record rather than
#: rounded away, so a later calibration can weight or exclude it.
EXTREME = D("0.03")

UP, DOWN = "UP", "DOWN"
UNDETERMINED = "UNDETERMINED"


def _dec(v: Any) -> Optional[D]:
    try:
        d = D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def observe(*, market: str, window_start_utc: Any, observed_utc: Any,
            bid: Any, ask: Any, frames_seen: Any = None,
            late_s: float = LATE_S, extreme: D = EXTREME) -> Dict[str, Any]:
    """One settlement observation. Never raises; refuses instead.

    `bid`/`ask` are the LAST book this process saw for the window, and
    `observed_utc` is when it saw it -- not when the window ends. The
    distinction is the whole point: a book seen at 59% of the window says
    nothing about the settlement.
    """
    rec: Dict[str, Any] = {
        "label": "L5_WINDOW_SETTLEMENT_OBSERVED",
        "market": str(market or "") or None,
        "window_start_utc": None, "observed_utc": None,
        "elapsed_s": None, "coverage": None,
        "last_bid": None, "last_ask": None,
        "settled_side": UNDETERMINED, "reason": None,
        "implied_other_side": None,
        "frames_seen": (None if frames_seen is None else int(frames_seen)),
        "late_threshold_s": float(late_s), "extreme_threshold": str(extreme),
        "labels": ("READ-ONLY observation of which side a window settled "
                   "on. UNDETERMINED is a refusal, not a zero: an "
                   "unobserved settlement stays unobserved rather than "
                   "becoming a weak point that calibrates a money "
                   "decision. implied_other_side is the residual error the "
                   "terminal-book proxy carries and is never discarded."),
    }
    if not rec["market"]:
        rec["reason"] = "NO_MARKET"
        return rec
    try:
        ws = dt.datetime.fromisoformat(str(window_start_utc).replace(
            "Z", "+00:00"))
        ob = dt.datetime.fromisoformat(str(observed_utc).replace(
            "Z", "+00:00"))
    except BaseException:                                      # noqa: BLE001
        rec["reason"] = "UNREADABLE_TIMES"
        return rec
    if ws.tzinfo is None or ob.tzinfo is None:
        rec["reason"] = "NAIVE_TIME"
        return rec
    rec["window_start_utc"] = ws.isoformat()
    rec["observed_utc"] = ob.isoformat()
    elapsed = (ob - ws).total_seconds()
    rec["elapsed_s"] = round(elapsed, 1)
    rec["coverage"] = round(elapsed / WINDOW_S, 4)
    if elapsed < 0:
        rec["reason"] = "OBSERVED_BEFORE_WINDOW"
        return rec

    b, a = _dec(bid), _dec(ask)
    if b is None or a is None:
        rec["reason"] = "UNREADABLE_BOOK"
        return rec
    rec["last_bid"], rec["last_ask"] = str(b), str(a)
    if not (D(0) <= b <= D(1) and D(0) <= a <= D(1)) or a < b:
        rec["reason"] = "BOOK_NOT_SANE"
        return rec

    if elapsed < float(late_s):
        # THE COMMON CASE, and the reason this module exists.
        rec["reason"] = "TAPE_ENDS_EARLY"
        return rec

    if b >= (D(1) - extreme):
        rec["settled_side"] = UP
        rec["implied_other_side"] = str(D(1) - b)
        return rec
    if a <= extreme:
        rec["settled_side"] = DOWN
        rec["implied_other_side"] = str(a)
        return rec
    rec["reason"] = "TERMINAL_BOOK_NOT_EXTREME"
    return rec


def summarise(records) -> Dict[str, Any]:
    """Counts only. It computes NO probability.

    Deliberate: turning six observations into a conditional settlement
    probability is the error this module was built to avoid. The
    sufficiency bound is reported so a caller can see how far off it is.
    """
    rows = [r for r in records if isinstance(r, Mapping)]
    det = [r for r in rows if r.get("settled_side") in (UP, DOWN)]
    by_reason: Dict[str, int] = {}
    for r in rows:
        if r.get("settled_side") not in (UP, DOWN):
            by_reason[str(r.get("reason"))] = (
                by_reason.get(str(r.get("reason")), 0) + 1)
    return {
        "observed": len(rows),
        "determined": len(det),
        "up": sum(1 for r in det if r["settled_side"] == UP),
        "down": sum(1 for r in det if r["settled_side"] == DOWN),
        "undetermined_by_reason": by_reason,
        "sufficient_for_entry_calibration": len(det) >= 400,
        "labels": ("determined counts windows whose settled side was "
                   "OBSERVED. No probability is computed here: the entry "
                   "decision turns on a skew of 0.0871, so an estimate "
                   "needs ~400 observations to resolve to +/-0.09, and "
                   "reporting a rate off six would be the error this "
                   "module exists to prevent."),
    }
