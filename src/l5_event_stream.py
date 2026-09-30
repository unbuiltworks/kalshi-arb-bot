"""Parse the authenticated WS recorder's event stream into causal event time.

INPUT. Verbatim Railway log lines from `pmus-ws-evidence-recorder`, each
`{"timestamp","message"}`, whose message carries either

  PMUS_TRADE_ROWS {"markets": {slug: [{t, px, qty, trade_time,
                                       bid_at_trade, ask_at_trade, dir}, ...]}}
  PMUS_BOOK_ROWS  {"markets": {slug: [{t, bid, ask, bid_qty, ask_qty,
                                       ask_book_missing, status}, ...]}}

`t` is OUR receipt epoch for that state; `trade_time` is the venue's own
timestamp for the print. Both are kept: ordering uses receipt time, because that
is when the information became available to us, and venue time is preserved so a
reviewer can check the two never disagree in a way that matters.

WHY RECEIPT TIME ORDERS THE STREAM. A causal simulator may use only what it
knew at T. A venue timestamp can precede its own arrival, so ordering by venue
time would let a decision consume a print before we could possibly have seen it.
Receipt time is the conservative choice and it is the one used.

DE-DUPLICATION. The emitter re-sends its recent buffer, so the same row appears
in several documents. Trades de-duplicate on (t, px, qty, trade_time); book
states on t. Duplicates are dropped, never summed -- double-counting volume
would manufacture fills.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, Iterator, List, Optional, Tuple

TRADE_MARK = "PMUS_TRADE_ROWS "
BOOK_MARK = "PMUS_BOOK_ROWS "
BTC15 = "cpc-btc-updown-15m-"


def _d(v) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def _lines(paths) -> Iterator[dict]:
    for p in sorted(paths):
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def load(pattern: str) -> Dict[str, Dict[str, list]]:
    """{slug: {"trades": [...], "book": [...]}} in receipt-time order."""
    out: Dict[str, Dict[str, list]] = {}
    seen_t: Dict[str, set] = {}
    seen_b: Dict[str, set] = {}
    for env in _lines(glob.glob(pattern)):
        msg = env.get("message") or ""
        for mark, key in ((TRADE_MARK, "trades"), (BOOK_MARK, "book")):
            i = msg.find(mark)
            if i < 0:
                continue
            try:
                doc = json.loads(msg[i + len(mark):])
            except ValueError:
                continue
            for slug, rows in (doc.get("markets") or {}).items():
                if not slug.startswith(BTC15):
                    continue
                bucket = out.setdefault(slug, {"trades": [], "book": []})
                for r in rows or []:
                    if key == "trades":
                        k = (r.get("t"), r.get("px"), r.get("qty"),
                             r.get("trade_time"))
                        s = seen_t.setdefault(slug, set())
                    else:
                        k = r.get("t")
                        s = seen_b.setdefault(slug, set())
                    if k in s:
                        continue
                    s.add(k)
                    bucket[key].append(r)
            break
    for slug, b in out.items():
        b["trades"].sort(key=lambda r: (float(r.get("t") or 0),))
        b["book"].sort(key=lambda r: (float(r.get("t") or 0),))
    return out


def merged(slug_data: Dict[str, list]) -> List[Tuple[float, str, dict]]:
    """One causal stream: (receipt_epoch, kind, row), book before trade on ties.

    A book state and a print carrying the same receipt time are ordered book
    first, because the print's own `bid_at_trade`/`ask_at_trade` show the book
    as it stood when the trade happened -- so the state is information we held
    at that instant, not after it.
    """
    ev: List[Tuple[float, str, dict]] = []
    for r in slug_data.get("book") or []:
        t = r.get("t")
        if t is not None:
            ev.append((float(t), "book", r))
    for r in slug_data.get("trades") or []:
        t = r.get("t")
        if t is not None:
            ev.append((float(t), "trade", r))
    ev.sort(key=lambda x: (x[0], 0 if x[1] == "book" else 1))
    return ev


def window_of(slug: str) -> Optional[Tuple[float, float]]:
    """(start_epoch, end_epoch) from the slug's own 15-minute stamp."""
    import datetime as dt
    try:
        tail = slug.rsplit("-", 2)
        day, hhmm = tail[-2], tail[-1]
        if not hhmm.endswith("z"):
            return None
        hh, mm = int(hhmm[0:2]), int(hhmm[2:4])
        y, mo, d = slug.split("-")[-4], slug.split("-")[-3], day
        start = dt.datetime(int(y), int(mo), int(d), hh, mm,
                            tzinfo=dt.timezone.utc)
    except Exception:                                        # noqa: BLE001
        return None
    s = start.timestamp()
    return (s, s + 900.0)


def coverage(slug: str, data: Dict[str, list]) -> dict:
    """How much of the window we actually hold, measured not assumed."""
    w = window_of(slug)
    ev = merged(data)
    if not ev:
        return {"slug": slug, "events": 0}
    first, last = ev[0][0], ev[-1][0]
    gaps = [b[0] - a[0] for a, b in zip(ev, ev[1:]) if b[0] > a[0]]
    gaps.sort()
    return {
        "slug": slug, "events": len(ev),
        "trades": len(data.get("trades") or []),
        "book_states": len(data.get("book") or []),
        "first": first, "last": last, "span_s": last - first,
        "window": w,
        "covers_from_start": (w is not None and first <= w[0] + 5),
        "covers_to_end": (w is not None and last >= w[1] - 5),
        "median_gap_s": (gaps[len(gaps) // 2] if gaps else None),
        "p95_gap_s": (gaps[int(0.95 * len(gaps))] if gaps else None),
        "max_gap_s": (gaps[-1] if gaps else None),
    }
