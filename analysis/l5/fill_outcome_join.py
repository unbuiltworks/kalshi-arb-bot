"""Join emitted L5_FILL_BOOK_EVIDENCE rows to L5_WINDOW_ACCOUNTING settlement.

WHY OFFLINE. Both record types are ALREADY emitted by the deployed engine:
`L5_FILL_BOOK_EVIDENCE` carries each fill's side, own price, quantity, the
executable book before and after, `order_resting`, depth ticks and
`venue_event_t`; `L5_WINDOW_ACCOUNTING` (phase CONFIRMED) carries the venue's
own settlement lines. Nothing joined them, which is the only reason the
"did this fill land on the winner" question was unanswerable.

Doing the join here rather than in the runner means the measurement needs NO
change to the money path and NO deployment. That matters: the programme is
supposed to measure the execution model we already run, and an instrumentation
change would measure a different one.

THE ONE FIELD WE CANNOT GET. `venue_event_t` is null on every fill we hold, so
fill LATENCY is unmeasurable. That is the venue's fill record omitting a
timestamp, not a gap in our code -- the runner already passes
`venue_event_t=ev.get("venue_event_t")` straight through. Latency is therefore
reported as unavailable rather than estimated.

NEVER A FILL. Only rows the venue confirmed enter here. A resting order, a
modelled fill (which carries `venue_event_t=None` AND an explicit modelled
basis) and a replay fill all produce nothing.
"""
import json
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import sys
sys.path.insert(0, ".")
from src.l5_fill_outcome import fill_outcomes          # noqa: E402
from analysis.l5.adverse_selection_stats import analyse  # noqa: E402

MODELLED_BASES = ("MODEL", "MODELLED", "SHADOW", "REPLAY")


def _is_real_fill(rec: Mapping[str, Any]) -> bool:
    """A modelled row is labelled as such; exclude it on the label, not on a
    guess about its timestamp."""
    basis = str(rec.get("basis") or "").upper()
    if any(m in basis for m in MODELLED_BASES):
        return False
    return bool(rec.get("coid"))


def settled_side_of(acct: Mapping[str, Any]) -> Optional[str]:
    """The resolved outcome, from the venue's own lines.

    Priority: the YES settlement value (1 => UP, 0 => DOWN), then the residual
    side together with the SIGN of the venue's residual P&L (a residual on the
    losing side settles negative). Returns None when neither is readable --
    never a guess, because an unresolved window must contribute to no
    denominator.
    """
    for key in ("yes_value", "settlement_px"):
        v = acct.get(key)
        if v is None:
            continue
        try:
            d = D(str(v))
        except Exception:                                  # noqa: BLE001
            continue
        if d == 1:
            return "UP"
        if d == 0:
            return "DOWN"
    s = acct.get("settled") if isinstance(acct.get("settled"), Mapping) else {}
    side = str(s.get("residual_side") or "").strip().upper() or None
    pnl = s.get("residual_pnl_usd")
    qty = s.get("residual_qty")
    if side in ("UP", "DOWN") and pnl is not None and qty is not None:
        try:
            if D(str(qty)) > 0:
                lost = D(str(pnl)) < 0
                return ({"UP": "DOWN", "DOWN": "UP"}[side] if lost else side)
        except Exception:                                  # noqa: BLE001
            return None
    return None


def join(fill_rows: Iterable[Mapping[str, Any]],
         accounting_rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One L5_FILL_OUTCOME dict per CONFIRMED window."""
    by_slug: Dict[str, List[Mapping[str, Any]]] = {}
    for r in fill_rows:
        if not _is_real_fill(r):
            continue
        by_slug.setdefault(str(r.get("slug")), []).append(r)

    out: List[Dict[str, Any]] = []
    seen = set()
    for a in accounting_rows:
        if str(a.get("phase") or "").upper() != "CONFIRMED":
            continue
        slug = str(a.get("market") or a.get("slug"))
        if slug in seen:              # CONFIRMED is emitted once per window
            continue
        seen.add(slug)
        fills = []
        for r in by_slug.get(slug, ()):
            fills.append({
                "coid": r.get("coid"),
                "side": r.get("side") or r.get("side_raw"),
                "qty": r.get("fill_qty"),
                "own_px": r.get("fill_own_px"),
                "t": r.get("processing_t"),
                "order_resting": r.get("order_resting"),
                "queue_ahead": r.get("queue_ahead"),
                "level_depth": r.get("level_depth"),
                # null on every fill we hold; see the module docstring
                "latency_s": r.get("latency_s"),
            })
        ex = a.get("execution") if isinstance(a.get("execution"), Mapping) else {}
        out.append(fill_outcomes(
            slug=slug, fills=fills,
            settled_side=settled_side_of(a),
            actual_fees_usd=ex.get("actual_fees_usd"),
            resolution_source=(a.get("settled") or {}).get("resolution_source")
            if isinstance(a.get("settled"), Mapping) else None))
    return out


def report(fill_rows, accounting_rows) -> Dict[str, Any]:
    rows = join(fill_rows, accounting_rows)
    stats = analyse(rows)
    latency = [r for w in rows for r in w["fills"]
               if r.get("latency_s") not in (None, "None")]
    return {
        "windows_confirmed": len(rows),
        "windows_with_fills": sum(1 for r in rows if r["n_fills"]),
        "fills": sum(r["n_fills"] for r in rows),
        "latency_observations": len(latency),
        "latency_note": ("venue_event_t is null on every fill we hold, so "
                         "fill latency is unmeasurable from venue records; "
                         "it is not estimated here"),
        "adverse_selection": stats,
        "windows": rows,
    }
