"""EARLY_15M_ADAPTATION -- Model B research, offline, production untouched.

96.7% of BTC15m notional lands at TTE > 300, outside the interval the frozen
policy quotes in. The question is NOT whether that interval has more volume;
it plainly does. The question is whether the flow there supports the actual
sequence:

    first fill -> opposite flow -> COMPLETE -> par-constrained match

More volume that never turns around is exactly the failure BTC5m already
demonstrated, so volume alone is not an answer.

WHAT THIS IS NOT. It does not modify `quote_between_tte_s`, does not touch
the running lanes, and is never imported by them. It answers the question by
replaying the venue's own expired tapes with the quoting window WIDENED IN
THE REPLAY ONLY, and every output carries the EARLY_15M_ADAPTATION label so
no reader can mistake it for the production policy's behaviour.

THE LIMIT, stated because it bounds every number here: Gemini publishes no
book history, so an expired contract's BOOK cannot be recovered. The replay
therefore prices from the TAPE -- the last print before each decision -- and
that is an approximation of a book we cannot see. Queue position is unknown
and is treated as zero, which is OPTIMISTIC. These are upper bounds.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "EARLY_15M_ADAPTATION"

#: the interval this research looks at -- OUTSIDE the frozen quoting window
EARLY_LO_S = 300.0
EARLY_HI_S = 900.0

PAR = D("1")
TICK = D("0.01")

CAVEAT = (
    "EARLY_15M_ADAPTATION. Model B research, offline. The frozen policy's "
    "quote_between_tte_s is UNCHANGED and the running lanes are untouched. "
    "Gemini serves no book history, so this prices from the TAPE rather than "
    "a book, and treats queue position as zero. Every figure is an UPPER "
    "BOUND on what the early interval could support, not an estimate of what "
    "it would deliver.")

#: THE PAIR BASIS HERE IS SET, NOT DISCOVERED. The complement is quoted at
#: `1 - held - tick`, which makes the basis 0.99 BY CONSTRUCTION on every
#: match. So "par-constrained" is not a measurement of what the pair cost --
#: it is the question "did external flow reach a price that WOULD have made
#: the pair fee-safe". Reporting the constructed 0.99 as though it were an
#: observed basis would be reporting a tautology as a finding.
BASIS_IS_CONSTRUCTED = (
    "the complement is quoted at 1 - held - tick, so the pair basis is 0.99 "
    "BY CONSTRUCTION on every match. The finding is that external flow "
    "REACHED that price, not that the pair happened to cost 0.99.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _leg_reached(leg: str, price: D, t: Mapping[str, Any]) -> bool:
    """The tape is priced in YES and records the aggressor."""
    tp = _dec(t.get("price"))
    if tp is None:
        return False
    want = "sell" if leg == "YES" else "buy"
    if str(t.get("type")) != want:
        return False
    return (tp <= price) if leg == "YES" else (tp >= (D("1") - price))


def replay_early(symbol: str, prints: Sequence[Mapping[str, Any]], *,
                 expiry_ms: Any, qty: Any = "0.01") -> Dict[str, Any]:
    """One expired 15m contract, replayed across TTE 900 -> 300.

    The mechanics are L5's own: OPEN on a flat book, then once residual
    exists work the COMPLETE on the opposite leg and suppress EXTEND -- the
    GEMINI_RECYCLE shape. What differs is ONLY the interval.
    """
    exp = _dec(expiry_ms)
    q = _dec(qty) or D("0.01")
    if exp is None:
        return {"computable": False, "why": "no expiry", "label": LABEL}
    rows = []
    for t in prints or []:
        ms, px, amt = (_dec(t.get("utc_ms")), _dec(t.get("price")),
                       _dec(t.get("amount")))
        if None in (ms, px, amt):
            continue
        tte = float((exp - ms) / D("1000"))
        if not (EARLY_LO_S <= tte <= EARLY_HI_S):
            continue
        rows.append((ms, px, amt, str(t.get("type")), tte))
    if len(rows) < 2:
        return {"computable": False, "symbol": symbol, "label": LABEL,
                "why": "fewer than two prints in the early interval",
                "prints": len(rows)}
    rows.sort(key=lambda r: r[0])

    held_leg = None
    held_px = None
    comp_px = None
    first_fill = None
    complete = None
    opposite_seen = 0
    extends_suppressed = 0
    for ms, px, amt, typ, tte in rows:
        if held_leg is None:
            # OPEN: quote the side the last print implies is cheap, one tick
            # behind it, and take the next print that reaches it
            held_leg = "YES" if typ == "sell" else "NO"
            held_px = (px - TICK) if held_leg == "YES" else (D("1") - px
                                                             - TICK)
            if held_px <= 0 or held_px >= 1:
                held_leg = None
                continue
            first_fill = {"leg": held_leg, "price": str(held_px),
                          "tte_s": round(tte, 1), "utc_ms": str(ms)}
            comp_leg = "NO" if held_leg == "YES" else "YES"
            comp_px = D("1") - held_px - TICK
            continue
        comp_leg = "NO" if held_leg == "YES" else "YES"
        if _leg_reached(comp_leg, comp_px, {"price": str(px), "type": typ}):
            opposite_seen += 1
            if complete is None:
                complete = {"leg": comp_leg, "price": str(comp_px),
                            "tte_s": round(tte, 1), "utc_ms": str(ms),
                            "seconds_after_first_fill": round(
                                float((ms - _dec(first_fill["utc_ms"]))
                                      / D("1000")), 1)}
        elif _leg_reached(held_leg, held_px, {"price": str(px), "type": typ}):
            extends_suppressed += 1

    basis = ((held_px + comp_px) if complete is not None else None)
    return {
        "computable": True, "symbol": symbol, "label": LABEL,
        "prints_in_early_interval": len(rows),
        "first_fill": first_fill,
        "opposite_flow_after_fill": opposite_seen,
        "complete": complete,
        "extends_suppressed": extends_suppressed,
        "matched": bool(complete is not None),
        "constructed_pair_basis": (str(basis) if basis is not None else None),
        "flow_reached_a_fee_safe_complement": bool(complete is not None),
        "basis_is_constructed": BASIS_IS_CONSTRUCTED,
        "caveat": CAVEAT,
    }


def census(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if r.get("computable")]
    matched = [r for r in ok if r.get("matched")]
    t2c = sorted(r["complete"]["seconds_after_first_fill"] for r in matched
                 if r.get("complete"))
    bases = sorted({str(r.get("constructed_pair_basis")) for r in matched})
    return {
        "label": LABEL,
        "contracts_replayed": len(rows),
        "computable": len(ok),
        "with_a_first_fill": sum(1 for r in ok if r.get("first_fill")),
        "with_opposite_flow_after_fill": sum(
            1 for r in ok if r.get("opposite_flow_after_fill")),
        "flow_reached_a_fee_safe_complement": len(matched),
        "reach_rate": (round(len(matched) / len(ok), 4) if ok else None),
        "median_seconds_to_complete": (t2c[len(t2c) // 2] if t2c else None),
        "constructed_basis_values": sorted(bases),
        "basis_is_constructed": BASIS_IS_CONSTRUCTED,
        "extends_suppressed": sum(int(r.get("extends_suppressed") or 0)
                                  for r in ok),
        "caveat": CAVEAT,
    }
