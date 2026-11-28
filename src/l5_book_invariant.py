"""The NO_BOOK invariant: a market cannot be both untradeable and trading.

    NO_BOOK + contemporaneous authenticated trades > 0  ==>  OBSERVATION DEFECT

`NO_BOOK` was being read as venue illiquidity. That reading is only available
when nothing was trading. If the authenticated tape shows prints in the SAME
market at the SAME time our observer saw no book, then the venue was quoting
and trading and we failed to look at it -- which is a fact about our data
path, not about the venue.

WHAT THIS IS NOT. It never fabricates a BBO from trades, never backfills a
price and never reclassifies an action into something tradeable. It only
labels a NO_BOOK as CONTRADICTED so the affected rows can be re-observed or
excluded honestly. A contradicted row is not evidence of liquidity; it is
evidence that we do not know what the book was.
"""
from __future__ import annotations

import collections
import datetime as dt
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: a print this many seconds either side of our observation counts as
#: contemporaneous. The observer's own PMUS snapshot cache is 2.0s, so a
#: window wider than a few seconds would start blaming the venue for our
#: latency rather than for our blindness.
CONTEMPORANEOUS_S = 30.0

FLAG = "NO_BOOK_CONTRADICTED_BY_TRADES"


def _epoch(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def trade_times_by_market(docs: Iterable[Mapping[str, Any]]
                          ) -> Dict[str, List[float]]:
    """Authenticated print times per market, sorted."""
    out: Dict[str, List[float]] = collections.defaultdict(list)
    for d in docs:
        for slug, rows in (d.get("markets") or {}).items():
            for r in rows or ():
                t = _epoch(r.get("trade_time")) or _epoch(r.get("t"))
                if t is not None:
                    out[slug].append(t)
    for k in out:
        out[k].sort()
    return dict(out)


def _any_near(times: Sequence[float], t: float, window: float) -> int:
    lo, hi = t - window, t + window
    return sum(1 for x in times if lo <= x <= hi)


def check(rows: Sequence[Mapping[str, Any]],
          trades_by_market: Mapping[str, Sequence[float]],
          *, window_s: float = CONTEMPORANEOUS_S) -> Dict[str, Any]:
    """Flag every NO_BOOK row that the authenticated tape contradicts."""
    contradicted: List[Dict[str, Any]] = []
    no_book = 0
    unverifiable = 0
    by_window = collections.Counter()

    for r in rows:
        if (r.get("copyability") or {}).get("klass") != "NO_BOOK":
            continue
        no_book += 1
        slug = (r.get("pmus") or {}).get("slug")
        t = _epoch(r.get("receipt_utc"))
        times = trades_by_market.get(slug or "")
        if t is None or not times:
            unverifiable += 1          # no tape here: we cannot say either way
            continue
        n = _any_near(times, t, window_s)
        if n > 0:
            w = str(r.get("window_start_utc") or "")[11:16]
            by_window[w] += 1
            contradicted.append({
                "action_key": r.get("action_key"), "window": w,
                "market": slug, "receipt_utc": r.get("receipt_utc"),
                "authenticated_prints_within_%ds" % int(window_s): n,
                "tte_s_at_receipt": r.get("tte_s_at_receipt"),
                "verdict": FLAG,
            })

    return {
        "label": "NO_BOOK_INVARIANT_V1",
        "no_book_rows": no_book,
        "contradicted": len(contradicted),
        "unverifiable_no_tape": unverifiable,
        "contradicted_rate_of_verifiable": (
            round(100.0 * len(contradicted) / (no_book - unverifiable), 1)
            if (no_book - unverifiable) > 0 else None),
        "by_window": dict(by_window),
        "detail": contradicted,
        "verdict": ("OBSERVATION_DEFECT" if contradicted else "CONSISTENT"),
        "meaning": (
            "a contradicted row means the venue was trading while we saw no "
            "book: our observation failed. It does NOT supply a price, and "
            "no BBO is ever fabricated from a trade."),
    }
