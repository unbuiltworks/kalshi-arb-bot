"""Read-only PMUS book tape collector, for ADAPTED execution-sweep input.

OWNER 2026-09-17. The ADAPTED sweep needs real book state: what the touch was,
how fast it moved, whether a proposed maker price would have crossed, and
whether a complement stayed priceable. The V1/V1.1 shadows already record that
-- ~296k rows -- but onto a Railway volume this container cannot reach, and the
export routes all cost either a redeploy or a credential. PMUS's book endpoints
are PUBLIC, so this collects the same state directly and perturbs nothing.

WHAT THIS IS NOT. It records the BOOK, never a fill. Nothing here may be called
FILLED. The sweep labels outcomes EXECUTABLE or RESTABLE from this tape, and
calibrates actual fill probability only against real prints and our own live
fills. A tape that let us assume fills would be worse than no tape, because it
would make every candidate look good.

READ-ONLY AND UNAUTHENTICATED BY CONSTRUCTION: public gateway endpoints only,
no account state, no credentials, no orders. It cannot touch the live executor.
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

GATEWAY = "https://gateway.polymarket.us"
LIVE_EVENTS = ("/v1/events?live=true&active=true&closed=false"
               "&categories=sports&limit=500")

#: THE ONE SPORTS ASSUMPTION IN THIS FILE, AND ITS ESCAPE HATCH.
#:
#: `LIVE_EVENTS` pins `categories=sports`, so the tape could only ever see the
#: sports slate. PMUS launched recurring BTC up/down series on 2026-09-22
#: (`btc-updown-15m`, `btc-updown-1h`) with the same book endpoints, and those
#: markets are invisible to sports discovery. They are also SHORT: a 15-minute
#: market's whole life is shorter than this collector's 300 s re-slate, so a
#: series universe additionally has to roll on its own clock.
#:
#: Nothing else in the file is sports-specific -- `snapshot()` is already
#: venue-generic -- so the admission is a second slug source, not a second
#: collector.
SERIES_RESLATE_S = 45.0

#: how far ahead of its start a window is worth polling
LEAD_S = 60.0
UA = "pmus-book-tape (read-only research collector)"


def _get(path: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(GATEWAY + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=ssl.create_default_context(),
                                timeout=timeout) as r:
        return json.loads(r.read())


def live_slugs(cap: int = 60) -> list:
    """Sports markets the venue currently serves as live. The sweep is about
    the regime we actually trade, so the universe is the live slate rather
    than a hand-picked list."""
    out = []
    try:
        ev = _get(LIVE_EVENTS)
    except Exception:                                    # noqa: BLE001
        return out
    for e in (ev.get("events") or []):
        for m in (e.get("markets") or []):
            s = m.get("slug")
            if s:
                out.append(str(s))
        if len(out) >= cap:
            break
    return out[:cap]


def series_slugs(series_slug: str, *, get=None, now=None) -> list:
    """Every market of a recurring series that is open or about to open.

    A 15-minute window is created well in advance and expires on a hard clock,
    so "the one trading right now" is a property of the wall clock, not of a
    `live=true` flag the venue only sets for sports. Markets that have already
    closed are excluded; the current window and the next two are kept, so a
    rollover is never missed between re-slates.
    """
    get = _get if get is None else get
    now = datetime.now(timezone.utc) if now is None else now
    try:
        sid = None
        off = 0
        while off < 2000 and sid is None:
            d = get("/v1/series?limit=100&offset=%d" % off) or {}
            rows = d.get("series") or []
            if not rows:
                break
            for r in rows:
                if str(r.get("slug")) == series_slug:
                    sid = str(r.get("id"))
                    break
            off += len(rows)
        if sid is None:
            return []
        evs, off = [], 0
        while off < 4000:
            d = get("/v1/events?series_id=%s&limit=100&offset=%d" % (sid, off)) or {}
            rows = d.get("events") or []
            if not rows:
                break
            evs += rows
            off += len(rows)
            if len(rows) < 100:
                break
    except Exception:                                    # noqa: BLE001
        return []
    live = []
    for e in evs:
        if e.get("closed"):
            continue
        try:
            start = datetime.fromisoformat(
                str(e.get("startTime")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        live.append((start, e))
    live.sort(key=lambda r: r[0])
    # ONLY WHAT IS ACTUALLY TRADING. The venue pre-creates windows hours
    # ahead, and their book/bbo endpoints 404 until the window opens. Polling
    # them spends two thirds of the call budget on 404s and fills the tape
    # with errors that are not errors, so the universe is bounded to the
    # window in progress plus the one about to start.
    out = []
    for start, e in live:
        if start > now + timedelta(seconds=LEAD_S):
            break
        for m in (e.get("markets") or []):
            if m.get("slug"):
                out.append(str(m["slug"]))
    return out


def book_snapshot(slug: str) -> Optional[dict]:
    """The full ladder, for depth. Separate from `snapshot` because the BBO
    endpoint answers the liveness question on its own and costs one call; a
    depth question is worth a second call only when it is actually asked."""
    try:
        bk = (_get("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
    except Exception as exc:                             # noqa: BLE001
        return {"t": datetime.now(timezone.utc).isoformat(), "slug": slug,
                "kind": "book_error", "error": "%s:%s" % (
                    type(exc).__name__, str(exc)[:80])}

    def side(rows):
        out = []
        for r in rows or []:
            try:
                out.append([str(r["px"]["value"]), str(r["qty"])])
            except (KeyError, TypeError):
                continue
        return out

    return {
        "t": datetime.now(timezone.utc).isoformat(),
        "slug": slug, "kind": "book",
        "bids": side(bk.get("bids")),
        "offers": side(bk.get("offers")),
        "state": bk.get("state"),
        "transact_time": bk.get("transactTime"),
        "labels": "BOOK STATE ONLY -- never a fill, never realized",
    }


def snapshot(slug: str) -> Optional[dict]:
    """One book observation. `offers: []` is recorded as an empty side, not as
    a missing one -- a one-sided book is exactly the condition that stranded
    the 2026-09-17 residual, so it has to survive into the tape."""
    try:
        bbo = (_get("/v1/markets/%s/bbo" % slug) or {}).get("marketData") or {}
    except Exception as exc:                             # noqa: BLE001
        return {"t": datetime.now(timezone.utc).isoformat(), "slug": slug,
                "kind": "error", "error": "%s:%s" % (type(exc).__name__,
                                                     str(exc)[:80])}
    bid = (bbo.get("bestBid") or {}).get("value") if bbo.get("bestBid") else None
    ask = (bbo.get("bestAsk") or {}).get("value") if bbo.get("bestAsk") else None
    return {
        "t": datetime.now(timezone.utc).isoformat(),
        "slug": slug, "kind": "bbo",
        "bid": bid, "ask": ask,
        "bid_depth": bbo.get("bidDepth"), "ask_depth": bbo.get("askDepth"),
        "bid_shares": bbo.get("bidShares"), "ask_shares": bbo.get("askShares"),
        "last": (bbo.get("lastTradePx") or {}).get("value")
        if bbo.get("lastTradePx") else None,
        "shares_traded": bbo.get("sharesTraded"),
        "state": bbo.get("state"),
        "one_sided": (bid is None) != (ask is None),
        "two_sided": bool(bid) and bool(ask),
        "labels": "BOOK STATE ONLY -- never a fill, never realized",
    }


def run(out_dir: Path, minutes: float, interval_s: float = 5.0,
        cap: int = 60, series: Optional[str] = None,
        with_book: bool = False) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = ("pmus_%s_" % series.replace("-", "")) if series else "pmus_book_"
    path = out_dir / ("%s%s.jsonl" % (tag, stamp))
    end = time.time() + minutes * 60.0

    def universe():
        return series_slugs(series) if series else live_slugs(cap)

    reslate = SERIES_RESLATE_S if series else 300.0
    slugs, refreshed, rows, cycles = universe(), time.time(), 0, 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "t": datetime.now(timezone.utc).isoformat(), "kind": "tape_start",
            "gateway": GATEWAY, "interval_s": interval_s, "cap": cap,
            "series": series, "with_book": with_book,
            "reslate_s": reslate,
            "slugs": len(slugs), "slug_list": list(slugs),
            "minutes": minutes,
            "provenance": "public PMUS gateway, read-only, no credentials",
        }) + "\n")
        while time.time() < end:
            if time.time() - refreshed > reslate:
                fresh = universe()
                if fresh:
                    if series and set(fresh) != set(slugs):
                        fh.write(json.dumps({
                            "t": datetime.now(timezone.utc).isoformat(),
                            "kind": "reslate", "series": series,
                            "was": list(slugs), "now": list(fresh)}) + "\n")
                    slugs = fresh
                refreshed = time.time()
            for s in slugs:
                row = snapshot(s)
                if row:
                    fh.write(json.dumps(row) + "\n")
                    rows += 1
                if with_book:
                    brow = book_snapshot(s)
                    if brow:
                        fh.write(json.dumps(brow) + "\n")
                        rows += 1
            fh.flush()
            cycles += 1
            print("PMUS_BOOK_TAPE %s" % json.dumps(
                {"cycle": cycles, "slugs": len(slugs), "rows": rows,
                 "file": path.name}), flush=True)
            time.sleep(interval_s)
    print("PMUS_BOOK_TAPE_DONE %s" % json.dumps(
        {"rows": rows, "cycles": cycles, "file": str(path)}), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--cap", type=int, default=60)
    ap.add_argument("--out-dir", default="data/pmus_book_tape")
    ap.add_argument("--series", default=None,
                    help="recurring series slug (e.g. btc-updown-15m). "
                         "Default None keeps the sports live slate.")
    ap.add_argument("--with-book", action="store_true",
                    help="also record the full ladder, for depth")
    a = ap.parse_args()
    return run(Path(a.out_dir), a.minutes, a.interval, a.cap,
               series=a.series, with_book=a.with_book)


if __name__ == "__main__":
    raise SystemExit(main())
