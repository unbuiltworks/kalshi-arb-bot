"""HISTORICAL TAPE BACKFILL across a contract family's expired windows.

Runs beside the live collector, never instead of it.

WHAT CAN AND CANNOT BE BACKFILLED, said plainly. `/v1/trades/{symbol}` serves
EXPIRED contracts, so the external tape is recoverable for any past window.
The BOOK is not: Gemini publishes the current book and no history of it. The
full sequential replay needs book snapshots at each cycle, so it can only run
forward from the moment a collector starts recording.

What the tape alone supports is exactly the recycling-relevant comparison:
prints, notional, two-sidedness, aggressor balance and ALTERNATION -- how often
the aggressor direction changes, which is what an inventory oscillator needs
and what a volume ranking cannot see.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def tickers_back(cohort, *, windows: int,
                 now: Optional[float] = None) -> List[str]:
    """The family's expired tickers, most recent first, on its own grid."""
    from src.gemini_execution import metadata as MD
    t = time.time() if now is None else now
    cur = MD.current_ticker(t, prefix=cohort.prefix, minutes=cohort.minutes)
    out = []
    for i in range(1, windows + 1):
        tk = MD.sibling_ticker(cur, cohort.minutes * i)
        if tk:
            out.append(tk)
    return out


def backfill(cohort, *, windows: int = 48, pause_s: float = 0.12,
             now: Optional[float] = None) -> Dict[str, Any]:
    from src.gemini_execution import slate as SL
    from src.gemini_execution import window_volume as WV
    rows: List[Dict[str, Any]] = []
    for tk in tickers_back(cohort, windows=windows, now=now):
        sym = cohort.symbol_for(tk)
        t = WV.fetch_tape(sym)
        rows.append({
            "symbol": sym, "ticker": tk, "cohort": cohort.name,
            "tape": (SL.tape_diagnostics(t.get("prints") or [])
                     if t.get("readable")
                     else {"readable": False, "why": t.get("why")})})
        time.sleep(pause_s)
    readable = [r for r in rows if (r["tape"] or {}).get("readable")]
    traded = [r for r in readable if (r["tape"] or {}).get("prints")]
    return {
        "cohort": cohort.name,
        "windows_requested": windows,
        "windows_readable": len(readable),
        "windows_with_any_print": len(traded),
        "traded_share": (round(len(traded) / len(readable), 4)
                         if readable else None),
        "pooled": SL.by_series([{**r, "series": cohort.name}
                                for r in readable]).get(cohort.name),
        "rows": rows,
        "book_caveat": ("the external tape is recoverable for expired "
                        "contracts; the BOOK is not. A full sequential replay "
                        "needs book snapshots per cycle and can only run "
                        "forward from when a collector starts recording."),
        "labels": "PUBLIC READS ONLY. Nothing here places or sizes anything.",
    }
