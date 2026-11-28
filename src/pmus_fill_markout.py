"""Post-fill price movement ("markout") of the sports bot's own fills.

Owner 2026-11-22 (issue #251): "Measure signed price movement following
fills at available horizons, including 5 seconds, 30 seconds, 2 minutes,
and 5 minutes where data supports them ... Clearly distinguish markout,
modeled expected value, and settled realized profit. Do not invent missing
order-book snapshots or interpolate through material data gaps."

PURE. No I/O, no credentials, no orders. The engine feeds it the best bid
and ask it reads anyway; this keeps a short rolling history per market and,
for each fill, looks up what the side was worth at each horizon.

  markout(h) = value of the bought side at fill time + h  -  fill price
               (YES is worth the mid; NO is worth 1 - mid)

A MARKOUT IS A MARK, NOT A RESULT. It says where the book was shortly after
we were filled -- the adverse selection a resting bid suffers -- and nothing
about what the contract finally paid.

NO INTERPOLATION. The value at a horizon is the first book sample AT OR
AFTER it, and only if that sample is no later than `tolerance(h)` past the
horizon. Otherwise the horizon is recorded as missed, with the reason. A
restart empties the history: horizons still pending are missed, never
guessed.
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, List, Mapping, Optional, Tuple

HORIZONS_S: Tuple[int, ...] = (5, 30, 120, 300)
#: history kept per market: the longest horizon plus its tolerance, plus the
#: time a fill can go unnoticed before the engine reads it (30 s seen live)
KEEP_S = 720.0
MAX_SAMPLES = 4000
LABEL = ("markout: where the book was after the fill. NOT realized, NOT "
         "an expected value; settlement decides the result")


def tolerance(h: float) -> float:
    """How late the first sample after a horizon may be and still count."""
    return max(float(h), 30.0)


def side_value(side: str, bid: float, ask: float) -> float:
    mid = (float(bid) + float(ask)) / 2.0
    return mid if str(side).upper() == "YES" else 1.0 - mid


class Mids:
    """Rolling (t, bid, ask) per market, oldest first."""

    def __init__(self, keep_s: float = KEEP_S) -> None:
        self.keep_s = float(keep_s)
        self._t: Dict[str, List[float]] = {}
        self._q: Dict[str, List[Tuple[float, float]]] = {}

    def add(self, slug: str, t: float, bid: Any, ask: Any) -> bool:
        """Record one two-sided quote. A crossed, one-sided or unreadable
        book is not a quote and is not recorded."""
        try:
            b, a = float(bid), float(ask)
        except (TypeError, ValueError):
            return False
        if not (0.0 < b < a < 1.0):
            return False
        ts, qs = self._t.setdefault(slug, []), self._q.setdefault(slug, [])
        t = float(t)
        if ts and t < ts[-1]:
            return False                       # never reorder history
        ts.append(t)
        qs.append((b, a))
        cut = bisect.bisect_left(ts, t - self.keep_s)
        cut = max(cut, len(ts) - MAX_SAMPLES)
        if cut > 0:
            del ts[:cut]
            del qs[:cut]
        return True

    def at_or_after(self, slug: str, t: float
                    ) -> Optional[Tuple[float, float, float]]:
        ts = self._t.get(slug) or []
        i = bisect.bisect_left(ts, float(t))
        if i >= len(ts):
            return None
        b, a = self._q[slug][i]
        return ts[i], b, a

    def at_or_before(self, slug: str, t: float
                     ) -> Optional[Tuple[float, float, float]]:
        ts = self._t.get(slug) or []
        i = bisect.bisect_right(ts, float(t)) - 1
        if i < 0:
            return None
        b, a = self._q[slug][i]
        return ts[i], b, a

    def drop(self, slug: str) -> None:
        self._t.pop(slug, None)
        self._q.pop(slug, None)


def new_record(*, slug: str, side: str, px: Any, qty: Any, order_id: str,
               t_fill: float, t_seen: float, t_source: str, mids: Mids,
               fair_yes: Any = None, unpaired_before: Any = None,
               role: Optional[str] = None) -> Dict[str, Any]:
    """One fill, with the book as it stood just before it."""
    rec: Dict[str, Any] = {
        "slug": slug, "side": str(side).upper(), "px": str(px),
        "qty": str(qty), "order_id": str(order_id),
        "t": float(t_fill), "t_seen": float(t_seen),
        "t_source": t_source, "role": role,
        "unpaired_before": (None if unpaired_before is None
                            else str(unpaired_before)),
        "h": {}}
    pre = mids.at_or_before(slug, t_fill)
    if pre is not None and float(t_fill) - pre[0] <= tolerance(5):
        rec["book_before"] = {
            "age_s": round(float(t_fill) - pre[0], 2),
            "bid": pre[1], "ask": pre[2],
            # > 0: we bought below what the side was marked at
            "entry_vs_mid": round(side_value(side, pre[1], pre[2])
                                  - float(px), 6)}
    else:
        rec["book_before"] = None
    if fair_yes is not None:
        try:
            fy = float(fair_yes)
            rec["fair_before"] = round(
                (fy if rec["side"] == "YES" else 1.0 - fy) - float(px), 6)
        except (TypeError, ValueError):
            pass
    return rec


def resolve(rec: Dict[str, Any], mids: Mids, now: float,
            horizons: Tuple[int, ...] = HORIZONS_S) -> List[int]:
    """Fill in every horizon that can be decided now. Returns the horizons
    newly decided (valued or missed)."""
    newly: List[int] = []
    px = float(rec["px"])
    for h in horizons:
        k = str(h)
        if k in rec["h"]:
            continue
        target = float(rec["t"]) + h
        tol = tolerance(h)
        s = mids.at_or_after(rec["slug"], target)
        if s is not None and s[0] - target <= tol:
            rec["h"][k] = {
                "markout": round(side_value(rec["side"], s[1], s[2]) - px, 6),
                "late_s": round(s[0] - target, 2),
                "bid": s[1], "ask": s[2]}
            newly.append(h)
        elif now - target > tol:
            rec["h"][k] = {"missed": True, "why": (
                "no_book_sample_within_%gs_of_the_horizon" % tol)}
            newly.append(h)
    return newly


def complete(rec: Mapping[str, Any],
             horizons: Tuple[int, ...] = HORIZONS_S) -> bool:
    return all(str(h) in (rec.get("h") or {}) for h in horizons)


def summary(recs: Any, horizons: Tuple[int, ...] = HORIZONS_S
            ) -> Dict[str, Any]:
    """Quantity-weighted mean markout per horizon over decided records,
    with how many fills had no usable sample. Says nothing about P&L."""
    out: Dict[str, Any] = {"fills": 0, "label": LABEL, "h": {}}
    rows = [r for r in (recs or ()) if isinstance(r, Mapping)]
    out["fills"] = len(rows)
    for h in horizons:
        k = str(h)
        w = v = 0.0
        n = missed = pending = 0
        for r in rows:
            cell = (r.get("h") or {}).get(k)
            if cell is None:
                pending += 1
            elif cell.get("missed"):
                missed += 1
            else:
                q = float(r.get("qty") or 0)
                w += q
                v += q * float(cell["markout"])
                n += 1
        out["h"][k] = {"n": n, "missed": missed, "pending": pending,
                       "mean_markout": (round(v / w, 6) if w > 0 else None)}
    return out
