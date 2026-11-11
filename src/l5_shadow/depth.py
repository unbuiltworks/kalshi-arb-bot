"""Multi-level depth: what quantity is ACTUALLY executable, and at what average.

WHY THE TOUCH IS NOT ENOUGH. A single Gemini BTC15m observation showed depth 1
at the touch against a wanted 10, and the router correctly declined. But "the
touch is thin" and "the venue cannot supply 10" are different claims: the
second needs the whole ladder, because a book can be 1 deep at the best price
and 40 deep one tick behind it.

L5 itself appears to accumulate through repeated small fills rather than
demanding a single large one, so the question that actually matters is not
"can we get 10 at once" but "what can we get, at what average price, at each
size we might ask for". That is what this module answers.

EVERY NUMBER HERE IS STILL A BOOK READING, NOT A FILL. `executable_qty` is an
upper bound: it is the size resting at prices we would accept, and queue
position is unobservable from a public book. Nothing here may be counted as a
fill.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Iterable, Optional, Sequence

# the sizes we ask the book about, rather than a single wanted quantity. The
# point is the SHAPE of what a venue can supply, not one pass/fail.
QTY_LADDER = (D("1"), D("2"), D("5"), D("10"), D("20"))


def _levels(raw: Iterable[Any], price_key: str = "price",
            size_key: str = "amount") -> list:
    """[(price, size)] sorted best-first for a BUY: cheapest ask first."""
    out = []
    for lv in raw or []:
        try:
            if isinstance(lv, dict):
                p, s = D(str(lv.get(price_key))), D(str(lv.get(size_key)))
            else:
                p, s = D(str(lv[0])), D(str(lv[1]))
        except Exception:                                     # noqa: BLE001
            continue
        if p > 0 and s > 0:
            out.append((p, s))
    out.sort(key=lambda x: x[0])
    return out


def vwap_for_qty(levels: Sequence, qty: Any) -> dict:
    """Walk the ladder to fill `qty`. Partial fills are reported, not rounded up.

    Returns filled/vwap/levels_used/exhausted. When the book cannot supply the
    full quantity, `filled` is what it COULD supply and `vwap` is the average
    over that part -- never an average over a quantity that does not exist.
    """
    want, got, cost, used = D(str(qty)), D("0"), D("0"), 0
    for px, sz in levels:
        if got >= want:
            break
        take = min(sz, want - got)
        got += take
        cost += take * px
        used += 1
    return {
        "requested": str(want),
        "filled": str(got),
        "vwap": (str((cost / got).quantize(D("0.000001"))) if got > 0 else None),
        "notional": str(cost),
        "levels_used": used,
        "complete": got >= want,
        "exhausted_book": got < want,
    }


def executable_ladder(levels: Sequence, *, max_price: Any,
                      qty_ladder: Sequence = QTY_LADDER) -> dict:
    """For each candidate size: what the book supplies at or under the ceiling.

    Levels priced above `max_price` are excluded BEFORE the walk, because a
    VWAP that averages an acceptable price with an unacceptable one can land
    under the ceiling while containing fills we would never have accepted.
    """
    cap = D(str(max_price))
    eligible = [(p, s) for p, s in levels if p <= cap]
    inside = sum((s for _, s in eligible), D("0"))
    rows = {}
    for q in qty_ladder:
        r = vwap_for_qty(eligible, q)
        r["within_ceiling"] = (r["vwap"] is not None
                               and D(r["vwap"]) <= cap)
        rows[str(q)] = r
    return {
        "max_price": str(cap),
        "immediately_executable_qty": str(inside),
        "eligible_levels": len(eligible),
        "total_levels": len(levels),
        "best_eligible_price": (str(eligible[0][0]) if eligible else None),
        "by_qty": rows,
        "labels": ("book reading, NOT a fill. immediately_executable_qty is the "
                   "size resting at acceptable prices; queue position is "
                   "unobservable from a public book, so this is an UPPER "
                   "BOUND."),
    }


class BookParseError(ValueError):
    """Levels were present and NONE of them parsed. Never treat this as empty.

    Polymarket sizes levels as `size` and Gemini as `amount`. Reading a
    Polymarket book with Gemini's key silently drops every level and the result
    is indistinguishable from a venue quoting nothing -- which is precisely how
    a live 112-level book got reported as "venue quotes no Up side at all".
    An unparseable book is UNKNOWN, and unknown must fail closed.
    """


def parse_book(raw: Any, side: str = "asks", *, price_key: Optional[str] = None,
               size_key: Optional[str] = None) -> list:
    """Gemini/CLOB book -> best-first ladder for BUYING that side.

    Key names are taken from the book itself when it carries them, so the
    caller cannot pair a Polymarket book with Gemini's key by accident.
    """
    if not isinstance(raw, dict):
        return []
    pk = price_key or raw.get("price_key") or "price"
    sk = size_key or raw.get("size_key") or "amount"
    rows = raw.get(side) or []
    lv = _levels(rows, pk, sk)
    if rows and not lv:
        raise BookParseError(
            f"{len(rows)} {side} level(s) present but none parsed with "
            f"keys ({pk!r}, {sk!r}); sample={rows[0]!r}. This is an UNKNOWN "
            f"book, not an empty one.")
    return lv if side == "asks" else sorted(lv, key=lambda x: -x[0])


def no_ladder_from_yes(yes_bids: Sequence) -> list:
    """Buying NO means selling YES: a NO ask at 1-p sits where a YES bid at p is.

    Deriving this wrong silently prices the wrong side of every Down intent,
    which is the same inversion the single-level evaluator had to get right.
    """
    out = [(D("1") - p, s) for p, s in yes_bids if D("0") < p < D("1")]
    out.sort(key=lambda x: x[0])
    return out
