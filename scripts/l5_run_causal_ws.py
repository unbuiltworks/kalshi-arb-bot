"""Causal simulator over the AUTHENTICATED WS slice: real prints, real ladder.

Fill evidence class: OBSERVED. Every fill is earned against an individual venue
print with its own `id`, not against an inferred volume lump.

This slice is a PARTIAL window, so it measures FILL MECHANICS -- how often a
basis-derived maker order is reached, and what inventory results -- and it does
NOT produce settled window economics. Nothing here is a net/window figure.
"""
from __future__ import annotations

import gzip
import json
import statistics
import sys
from decimal import Decimal as D
from typing import Dict, List, Optional

from src.l5_causal_sim import CausalSim

SLICE = "data/pmus_ws_stream/raw_l2/pmus_ws_20260930_1330z.jsonl.gz"
CLIPS = (10, 20, 30, 50)


def _d(v) -> Optional[D]:
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def _ep(t) -> Optional[float]:
    import datetime as dt
    if isinstance(t, (int, float)):
        return float(t)
    try:
        return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
    except Exception:                                        # noqa: BLE001
        return None


def events(slug: str):
    rows = [json.loads(l) for l in gzip.open(SLICE, "rt", encoding="utf-8")]
    out = []
    seen_ids = set()
    for r in rows:
        m = r.get("msg")
        if not isinstance(m, dict):
            continue
        t = _ep(r.get("t"))
        if t is None:
            continue
        md = m.get("marketData")
        if isinstance(md, dict) and (md.get("marketSlug") == slug):
            bids, offers = {}, {}
            for l in (md.get("bids") or []):
                px, q = _d(l.get("px")), _d(l.get("qty"))
                if px is not None and q is not None and q > 0:
                    bids[px] = q
            for l in (md.get("offers") or md.get("asks") or []):
                px, q = _d(l.get("px")), _d(l.get("qty"))
                if px is not None and q is not None and q > 0:
                    offers[px] = q
            bid = max(bids) if bids else None
            ask = min(offers) if offers else None
            out.append((t, "book", {
                "bid": str(bid) if bid is not None else None,
                "ask": str(ask) if ask is not None else None,
                "bid_qty": str(bids[bid]) if bid is not None else None,
                "ask_qty": str(offers[ask]) if ask is not None else None,
                "status": "OK" if (bid is not None and ask is not None)
                          else "ONE_SIDED",
                "_ladder": {"bids": bids, "offers": offers}}))
            continue
        tr = m.get("trade")
        if isinstance(tr, dict) and tr.get("marketSlug") == slug:
            # DE-DUPLICATE ON THE VENUE ID, which is the only key that can tell
            # two identical-looking prints apart.
            tid = tr.get("id")
            if tid and tid in seen_ids:
                continue
            if tid:
                seen_ids.add(tid)
            px, q = _d(tr.get("price")), _d(tr.get("quantity"))
            if px is None or q is None or q <= 0:
                continue
            out.append((t, "trade", {"px": str(px), "qty": str(q)}))
    out.sort(key=lambda e: (e[0], 0 if e[1] == "book" else 1))
    return out


ARMS = {
    "CONTROL reprice": dict(persistent_maker=True),
    "HOLD_THE_LEVEL": dict(persistent_maker=True, hold_the_level=True),
    "HOLD + take": dict(persistent_maker=True, hold_the_level=True,
                        reactive_take=True),
    "HOLD + take + extend": dict(persistent_maker=True, hold_the_level=True,
                                 reactive_take=True, basis_extend=True),
}

if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 \
        else "cpc-btc-updown-15m-2026-09-30-1330z"
    ev = events(slug)
    nb = sum(1 for e in ev if e[1] == "book")
    nt = len(ev) - nb
    span = ev[-1][0] - ev[0][0]
    print("%s: %d events (%d book, %d PRINTS) over %.1f min"
          % (slug[-5:], len(ev), nb, nt, span / 60), flush=True)
    print()
    hdr = "%-22s %4s %6s %6s %7s %8s %9s %9s %8s %7s %7s"
    print(hdr % ("arm", "clip", "fills", "part", "pairs", "resid",
                 "q-ahead0", "q-eaten", "q-lost", "cancels", "held_s"))
    for arm, kw in ARMS.items():
        for clip in CLIPS:
            sim = CausalSim(clip=D(clip), **kw)
            r = sim.run(ev, winner=None)
            print(hdr % (arm, clip, r["n_fills"], r["partial_fills"],
                         "%.2f" % (float(r["matched_qty"]) / clip),
                         str(r["resid_qty"]),
                         "%.0f" % float(r["median_ahead_at_placement"]),
                         "%.0f" % float(r["queue_consumed"]),
                         "%.0f" % float(r["queue_forfeited"]),
                         r["cancels"] + r["requotes"],
                         "%.0f" % r["held_s"]), flush=True)
