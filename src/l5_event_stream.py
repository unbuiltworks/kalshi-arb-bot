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

DE-DUPLICATION, corrected after an integrity audit. The earlier version
de-duplicated individual TRADES on (t, px, qty, trade_time) and book states on
`t`. That is wrong: a venue can legitimately print twice for the same size at
the same price in the same instant, and collapsing those destroys real volume --
the error runs in the dangerous direction, because volume is what fills us.

What is actually duplicated is whole LOG LINES: the harvester may capture the
same Railway line in overlapping rounds. So de-duplication now happens at the
log-line level only ((timestamp, message)), and every row inside a document is
kept. Source order is preserved for equal timestamps by carrying
(file index, line index, position in array) and sorting stably on it.

UPSTREAM CAVEAT, which this module cannot repair and must not hide: the recorder
itself de-duplicates before emitting, via `_trade_row_key` =
(slug, t, px, qty, trade_time, dir) in `emit_new_trades`. Two genuinely distinct
prints identical in all six fields are therefore dropped at the source and never
reach any consumer. Nothing downstream can recover them. Their frequency is
unknown; the effect is to UNDERSTATE traded volume, so every fill estimate built
on this stream is conservative on that axis.
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


def _lines(paths) -> Iterator[Tuple[int, int, dict]]:
    """(file_index, line_index, envelope), whole-line de-duplicated.

    The same Railway log line can be captured by two overlapping harvest
    rounds. That -- and only that -- is a duplicate.
    """
    seen_lines: set = set()
    for fi, p in enumerate(sorted(paths)):
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8", errors="replace") as fh:
            for li, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    env = json.loads(line)
                except ValueError:
                    continue
                key = (env.get("timestamp"), env.get("message"))
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                yield fi, li, env


def load(pattern: str) -> Dict[str, Dict[str, list]]:
    """{slug: {"trades": [...], "book": [...]}} in receipt-time order."""
    out: Dict[str, Dict[str, list]] = {}
    for fi, li, env in _lines(glob.glob(pattern)):
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
                for ai, r in enumerate(rows or []):
                    # EVERY row is kept. Two identical prints in one document
                    # are two prints; only whole repeated log lines are
                    # duplicates, and those were dropped in `_lines`.
                    rr = dict(r)
                    rr["_src"] = (fi, li, ai)
                    bucket[key].append(rr)
            break
    for slug, b in out.items():
        # STABLE on receipt time, ties broken by source order -- never by
        # value, so two equal-timestamp events keep the order the venue sent.
        b["trades"].sort(key=lambda r: (float(r.get("t") or 0), r["_src"]))
        b["book"].sort(key=lambda r: (float(r.get("t") or 0), r["_src"]))
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
    ev.sort(key=lambda x: (x[0], 0 if x[1] == "book" else 1,
                           x[2].get("_src") or (0, 0, 0)))
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
