"""WHY DID BOTH BIDS RISE? Directional repricing, or the spread compressing.

THE OBSERVATION THIS EXISTS TO EXPLAIN. On GEMI-BTC05M2609201535-UP both legs
of every blocked candidate marked FAVOURABLY at 60s -- NO at +46 ticks and YES
at +17.5. That cannot come from a directional move: BTC going up should help a
YES maker and hurt a NO maker, roughly equal and opposite, which is exactly the
signature seen in the earlier 61-120s cells.

Both bids rising at once means something else. On a two-outcome complementary
book `yes_bid + no_bid` is what a passive pair COSTS, and `1 - (yes_bid +
no_bid)` is the total spread across both outcomes. So both bids rising IS the
total spread compressing.

If wide Gemini books systematically compress after the moment we would have
posted into them, that is not a side effect -- it is the edge. A passive maker
resting on both bids of a wide book is long exactly that compression. This
module measures whether it happens, rather than inferring it from two favourable
markouts.

THE FOUR CLASSES, and the arithmetic that separates them:

    COMPRESSION   both bids move UP (or the bid sum rises materially) --
                  the two sides converge toward the mid and a resting pair
                  gains on both legs
    EXPANSION     both bids move DOWN / the bid sum falls -- the book widens
                  away from a resting pair
    DIRECTIONAL   the bids move in OPPOSITE directions with the sum roughly
                  unchanged -- a repricing, which helps one leg and hurts the
                  other
    MIXED         anything else, including sums that move while the legs
                  disagree

The threshold for "materially" is one tick on the bid sum, so a book that did
not move is not classified as a tiny compression.

UNKNOWN IS A CLASS. A read missing either outcome's bid yields UNKNOWN, never a
default. Nothing here gates anything.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

SCHEMA = "gemini_econ_book_move/v1"

COMPRESSION = "SPREAD_COMPRESSION"
EXPANSION = "SPREAD_EXPANSION"
DIRECTIONAL = "DIRECTIONAL_REPRICING"
MIXED = "MIXED"
UNKNOWN = "UNKNOWN"
CLASSES = (COMPRESSION, EXPANSION, DIRECTIONAL, MIXED, UNKNOWN)

LABELS = (
    "yes_bid + no_bid is what a PASSIVE PAIR COSTS; 1 - that sum is the total "
    "spread across both outcomes. A rising bid sum IS spread compression.",
    "Both legs marking favourably at once cannot be a directional move. This "
    "module measures which it was instead of inferring it.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def snapshot(book: Mapping[str, Any]) -> Dict[str, Any]:
    """Both outcomes' bids, asks and spreads, or Nones where unreadable."""
    y = (book or {}).get("yes") or {}
    n = (book or {}).get("no") or {}
    yb, ya = _dec(y.get("bid")), _dec(y.get("ask"))
    nb, na = _dec(n.get("bid")), _dec(n.get("ask"))
    return {
        "yes_bid": None if yb is None else str(yb),
        "yes_ask": None if ya is None else str(ya),
        "no_bid": None if nb is None else str(nb),
        "no_ask": None if na is None else str(na),
        "yes_spread": (None if (yb is None or ya is None) else str(ya - yb)),
        "no_spread": (None if (nb is None or na is None) else str(na - nb)),
        "bid_sum": (None if (yb is None or nb is None) else str(yb + nb)),
        "ask_sum": (None if (ya is None or na is None) else str(ya + na)),
    }


def classify(t0: Mapping[str, Any], t1: Mapping[str, Any], *,
             tick: Any = "0.01") -> Dict[str, Any]:
    """How the book moved between two snapshots."""
    tk = _dec(tick) or D("0.01")
    y0, y1 = _dec(t0.get("yes_bid")), _dec(t1.get("yes_bid"))
    n0, n1 = _dec(t0.get("no_bid")), _dec(t1.get("no_bid"))
    out: Dict[str, Any] = {"schema": SCHEMA, "t0": dict(t0), "t1": dict(t1),
                           "labels": list(LABELS)}
    if None in (y0, y1, n0, n1) or tk <= 0:
        out.update(klass=UNKNOWN,
                   why="a bid was unreadable at one end; NOT defaulted")
        return out

    dy = (y1 - y0) / tk
    dn = (n1 - n0) / tk
    dsum = dy + dn
    out.update(d_yes_bid_ticks=float(dy), d_no_bid_ticks=float(dn),
               d_bid_sum_ticks=float(dsum),
               bid_sum_t0=str(y0 + n0), bid_sum_t1=str(y1 + n1))

    material = abs(dsum) >= 1.0
    if dy > 0 and dn > 0 and material:
        k = COMPRESSION
    elif dy < 0 and dn < 0 and material:
        k = EXPANSION
    elif (dy > 0) != (dn > 0) and not material:
        k = DIRECTIONAL
    elif dy == 0 and dn == 0:
        k = MIXED
    else:
        k = MIXED
    out["klass"] = k
    out["why"] = ("dYES=%.1f dNO=%.1f ticks, bid-sum %+.1f ticks"
                  % (dy, dn, dsum))
    return out


@dataclass
class BookMoveCensus:
    """How wide books move after the moment we would have posted into them."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    max_rows: int = 4000

    def record(self, *, horizon_s: int, wide: bool, klass: str,
               d_bid_sum_ticks: Optional[float],
               bid_sum_t0: Optional[str]) -> None:
        self.rows.append({"horizon_s": int(horizon_s), "wide": bool(wide),
                          "klass": klass,
                          "d_bid_sum_ticks": d_bid_sum_ticks,
                          "bid_sum_t0": bid_sum_t0})
        if len(self.rows) > self.max_rows:
            self.rows = self.rows[-(self.max_rows // 2):]

    def report(self) -> Dict[str, Any]:
        if not self.rows:
            return {"schema": SCHEMA, "n": 0, "labels": list(LABELS)}
        out: Dict[str, Any] = {"schema": SCHEMA, "n": len(self.rows),
                               "labels": list(LABELS), "by_horizon": {}}
        for h in sorted({r["horizon_s"] for r in self.rows}):
            rs = [r for r in self.rows if r["horizon_s"] == h]
            wide = [r for r in rs if r["wide"]]
            c = Counter(r["klass"] for r in rs)
            cw = Counter(r["klass"] for r in wide)
            ds = [r["d_bid_sum_ticks"] for r in rs
                  if r["d_bid_sum_ticks"] is not None]
            dw = [r["d_bid_sum_ticks"] for r in wide
                  if r["d_bid_sum_ticks"] is not None]
            out["by_horizon"][str(h)] = {
                "n": len(rs),
                "classes": dict(c),
                "compression_share": (c[COMPRESSION] / len(rs)) if rs else None,
                "median_d_bid_sum_ticks": (statistics.median(ds)
                                           if ds else None),
                "wide_n": len(wide),
                "wide_classes": dict(cw),
                # THE QUESTION: do WIDE books compress after we would post?
                "wide_compression_share": ((cw[COMPRESSION] / len(wide))
                                           if wide else None),
                "wide_median_d_bid_sum_ticks": (statistics.median(dw)
                                                if dw else None),
            }
        return out
