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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GATEWAY = "https://gateway.polymarket.us"
LIVE_EVENTS = ("/v1/events?live=true&active=true&closed=false"
               "&categories=sports&limit=500")
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
        cap: int = 60) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / ("pmus_book_%s.jsonl" % stamp)
    end = time.time() + minutes * 60.0
    slugs, refreshed, rows, cycles = live_slugs(cap), time.time(), 0, 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "t": datetime.now(timezone.utc).isoformat(), "kind": "tape_start",
            "gateway": GATEWAY, "interval_s": interval_s, "cap": cap,
            "slugs": len(slugs), "minutes": minutes,
            "provenance": "public PMUS gateway, read-only, no credentials",
        }) + "\n")
        while time.time() < end:
            if time.time() - refreshed > 300:            # re-slate every 5 min
                fresh = live_slugs(cap)
                if fresh:
                    slugs = fresh
                refreshed = time.time()
            for s in slugs:
                row = snapshot(s)
                if row:
                    fh.write(json.dumps(row) + "\n")
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
    a = ap.parse_args()
    return run(Path(a.out_dir), a.minutes, a.interval, a.cap)


if __name__ == "__main__":
    raise SystemExit(main())
