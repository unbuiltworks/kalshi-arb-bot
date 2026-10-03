"""Full-depth L2 books for Gemini prediction contracts, read-only.

`GET /v1/book/{instrumentSymbol}` returns the COMPLETE ladder by default:

    {"bids":[{"price":"0.95","amount":"526.0","timestamp":"..."}, ...],
     "asks":[{"price":"0.98","amount":"250.0", ...}]}

NEVER REQUEST A TRUNCATED BOOK. The Gemini lane on this repository measured that
asking for `?limit_bids=1&limit_asks=1` hides a median 182 shares behind the best
price (p95 720), and that a thin touch sitting in front of 500 shares one tick
back was being reported as "the venue cannot supply size". This module always
takes the whole ladder, so capacity is measured rather than inferred from the
touch.
"""
from __future__ import annotations

import time
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_arb_discovery.http import get_json

BOOK_PATH = "/v1/book/%s"

Level = Tuple[D, D]  # (price, amount)


def _levels(rows: Any, *, descending: bool) -> List[Level]:
    out: List[Level] = []
    for r in rows or []:
        try:
            p, a = D(str(r.get("price"))), D(str(r.get("amount")))
        except (InvalidOperation, TypeError, AttributeError):
            continue
        if a > 0:
            out.append((p, a))
    out.sort(key=lambda pa: pa[0], reverse=descending)
    return out


def fetch_book(symbol: str, timeout: float = 20.0) -> Dict[str, Any]:
    """{readable, symbol, bids, asks, recv} -- bids best-first, asks best-first."""
    r = get_json(BOOK_PATH % symbol, timeout=timeout)
    if not r.get("readable"):
        return {"readable": False, "symbol": symbol, "err": r.get("err"),
                "status": r.get("status"), "recv": time.time()}
    j = r["json"]
    return {"readable": True, "symbol": symbol,
            "bids": _levels(j.get("bids"), descending=True),
            "asks": _levels(j.get("asks"), descending=False),
            "recv": r.get("recv", time.time()), "rtt_s": r.get("rtt_s")}


def best_bid(book: Dict[str, Any]) -> Optional[D]:
    b = book.get("bids") or []
    return b[0][0] if b else None


def best_ask(book: Dict[str, Any]) -> Optional[D]:
    a = book.get("asks") or []
    return a[0][0] if a else None


def depth_total(book: Dict[str, Any], side: str) -> D:
    return sum((a for _, a in (book.get(side) or [])), D("0"))


def two_sided(book: Dict[str, Any]) -> bool:
    return bool(book.get("bids")) and bool(book.get("asks"))


def spread(book: Dict[str, Any]) -> Optional[D]:
    bb, ba = best_bid(book), best_ask(book)
    return None if bb is None or ba is None else ba - bb
