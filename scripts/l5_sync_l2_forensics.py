"""LADDER or REACTIVE? The forensic reading of one synchronized window.

PRIMARY DISCRIMINATOR. When consecutive L5 fills walk .31 -> .32 -> .33:
  REACTIVE  the book moved through those levels BEFORE each fill, so each fill
            follows a move it did not cause.
  LADDER    the levels were already resting and one move swept them, so the
            fills cluster INSIDE a single book transition rather than after
            separate ones.

Every conclusion is labelled OBSERVED / INFERRED / UNKNOWN. Fill-only inference
is never dressed as observation, and the absence of order-level events is stated
rather than worked around.

    python3 scripts/l5_sync_l2_forensics.py analysis/l5/sync_l2/<slug>.jsonl
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

SIDE_OF = {"Up": "up", "Down": "down", "UP": "up", "DOWN": "down"}


def load(path: str) -> Dict[str, Any]:
    recs = [json.loads(l) for l in open(path)]
    out: Dict[str, Any] = {"open": None, "close": None, "ws": [], "act": [],
                           "rest": [], "errors": [], "failed_reads": 0}
    for r in recs:
        k = r["kind"]
        if k == "window_open":
            out["open"] = r
        elif k == "window_close":
            out["close"] = r
        elif k == "ws":
            out["ws"].append(r)
        elif k == "activity":
            out["act"].append(r)
        elif k == "rest_book":
            out["rest"].append(r)
        elif k in ("ws_error", "ws_fatal", "window_abandoned"):
            out["errors"].append(r)
        elif k == "activity_read_failed":
            out["failed_reads"] += 1
    return out


def best(levels: Any, side: str) -> Optional[Tuple[float, float]]:
    """Top of one side of a book payload: (price, size)."""
    if not isinstance(levels, list) or not levels:
        return None
    try:
        rows = [(float(x["price"]), float(x["size"])) for x in levels
                if x.get("price") is not None]
    except (TypeError, ValueError, KeyError):
        return None
    if not rows:
        return None
    return max(rows, key=lambda r: r[0]) if side == "bid" \
        else min(rows, key=lambda r: r[0])


def book_series(ws: List[dict], token: str) -> List[Dict[str, Any]]:
    """Reconstruct a touch series for ONE token from `book` snapshots only.

    price_change deltas are recorded and counted but NOT folded: folding a delta
    stream without the venue's own sequencing rules would invent a book, and the
    `book` snapshots alone are already ~1/s here. Stated rather than silently
    approximated.
    """
    out = []
    for r in ws:
        for e in (r["payload"] if isinstance(r["payload"], list)
                  else [r["payload"]]):
            if not isinstance(e, dict) or e.get("event_type") != "book":
                continue
            if str(e.get("asset_id")) != str(token):
                continue
            b, a = best(e.get("bids"), "bid"), best(e.get("asks"), "ask")
            out.append({"recv": r["recv_epoch"],
                        "venue_ts": e.get("timestamp"),
                        "bid": b and b[0], "bid_sz": b and b[1],
                        "ask": a and a[0], "ask_sz": a and a[1],
                        "bids": e.get("bids"), "asks": e.get("asks")})
    out.sort(key=lambda x: x["recv"])
    return out


def l5_fills(act: List[dict], condition_id: str) -> List[Dict[str, Any]]:
    seen, fills = set(), []
    for r in act:
        for row in (r["payload"] or []):
            if str(row.get("conditionId")) != str(condition_id):
                continue
            key = (str(row.get("transactionHash")), str(row.get("timestamp")),
                   str(row.get("price")), str(row.get("size")),
                   str(row.get("outcome")), str(row.get("side")))
            if key in seen:
                continue
            seen.add(key)
            fills.append({
                "venue_ts": int(row.get("timestamp") or 0),
                "first_seen_recv": r["recv_epoch"],
                "outcome": row.get("outcome"),
                "side": str(row.get("side") or ""),
                "px": float(row.get("price") or 0),
                "size": float(row.get("size") or 0),
                "usdc": float(row.get("usdcSize") or 0),
                "tx": row.get("transactionHash"),
            })
    fills.sort(key=lambda f: (f["venue_ts"], f["px"]))
    return fills


def at_or_before(series: List[Dict[str, Any]], t: float):
    prev = None
    for s in series:
        if s["recv"] <= t:
            prev = s
        else:
            break
    return prev


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    d = load(path)
    op = d["open"]
    if not op:
        print(f"{path}: no window_open record")
        return 1
    info = op["payload"]
    ids = info.get("ids") or {}
    slug = info["slug"]
    cond = ids.get("condition_id")
    toks = ids.get("token_ids") or []
    outcomes = ids.get("outcomes") or []

    ev = Counter()
    for r in d["ws"]:
        for e in (r["payload"] if isinstance(r["payload"], list)
                  else [r["payload"]]):
            if isinstance(e, dict):
                ev[str(e.get("event_type"))] += 1

    print("=" * 100)
    print(f"SYNCHRONIZED FORENSICS  {slug}")
    print("=" * 100)
    print(f"  question      {ids.get('question')}")
    print(f"  outcomes      {outcomes}  tokens {len(toks)}")
    print(f"  ws frames     {len(d['ws'])}   event types {dict(ev)}")
    print(f"  activity polls{len(d['act'])}   failed reads {d['failed_reads']}")
    print(f"  rest snapshots{len(d['rest'])}   errors {len(d['errors'])}")
    if d["errors"]:
        for e in d["errors"][:3]:
            print(f"    ERROR {e['kind']}: {str(e['payload'])[:140]}")

    fills = l5_fills(d["act"], cond)
    print(f"\n  L5 fills in THIS market: {len(fills)}")
    if not fills:
        print("  OBSERVED: L5 did not trade this window. Not an active window; "
              "the discriminator needs one where he does.")
        return 0

    #: the UP token's series carries the whole market in up-terms
    up_tok = str(toks[0])
    series = book_series(d["ws"], up_tok)
    print(f"  book snapshots for the up token: {len(series)}")

    #: LATENCY, measured, kept distinct from L5's event time
    lat = sorted(f["first_seen_recv"] - f["venue_ts"] for f in fills)
    if lat:
        print(f"\n  OBSERVED collection lag (our first sight MINUS venue event "
              f"time): min {lat[0]:.1f}s  median {lat[len(lat)//2]:.1f}s  "
              f"max {lat[-1]:.1f}s")
        print("  This is OUR delay. It is NOT L5's reaction time and is never "
              "used as his decision time.")

    print("\n" + "=" * 100)
    print("FILL-BY-FILL against the contemporaneous book")
    print("=" * 100)
    print("  %-9s%-6s%8s%8s%11s%11s%9s" % (
        "venue_ts", "side", "px", "size", "bid@fill", "ask@fill", "vs touch"))
    rows = []
    for f in fills:
        side = SIDE_OF.get(str(f["outcome"]), "?")
        #: own-terms touch for this side, from the up-token book
        s = at_or_before(series, f["venue_ts"])
        own_bid = own_ask = None
        if s and s["bid"] is not None and s["ask"] is not None:
            own_bid = s["bid"] if side == "up" else 1.0 - s["ask"]
            own_ask = s["ask"] if side == "up" else 1.0 - s["bid"]
        rel = ("UNKNOWN" if own_ask is None else
               ("at/through ask" if f["px"] >= own_ask else
                ("at bid" if own_bid is not None and f["px"] >= own_bid
                 else "below bid")))
        print("  %-9d%-6s%8.3f%8.1f%11s%11s%9s" % (
            f["venue_ts"], side, f["px"], f["size"],
            "n/a" if own_bid is None else "%.3f" % own_bid,
            "n/a" if own_ask is None else "%.3f" % own_ask, rel))
        rows.append({**f, "side": side, "own_bid": own_bid,
                     "own_ask": own_ask, "rel": rel})

    print("\n" + "=" * 100)
    print("THE DISCRIMINATOR: same-side price walks")
    print("=" * 100)
    walks, cur = [], [rows[0]]
    for a, b in zip(rows, rows[1:]):
        if b["side"] == a["side"] and b["venue_ts"] - a["venue_ts"] <= 3 \
                and b["px"] != a["px"]:
            cur.append(b)
        else:
            if len(cur) >= 2:
                walks.append(cur)
            cur = [b]
    if len(cur) >= 2:
        walks.append(cur)
    if not walks:
        print("  No multi-level same-side walk within 3s in this window. "
              "The discriminator needs one; UNKNOWN for this window.")
    for wk in walks:
        t0, t1 = wk[0]["venue_ts"], wk[-1]["venue_ts"]
        pxs = [w["px"] for w in wk]
        moved = [s for s in series if t0 - 2 <= s["recv"] <= t1 + 2]
        touches = [(round(s["recv"] - t0, 2), s["bid"], s["ask"])
                   for s in moved]
        print(f"\n  WALK {wk[0]['side']} {pxs}  over {t1 - t0}s, "
              f"sizes {[w['size'] for w in wk]}")
        print(f"    book snapshots spanning it: {len(moved)}")
        for t, b, a in touches[:14]:
            print(f"      t{t:+6.2f}s  bid {b}  ask {a}")
        distinct = {(b, a) for _, b, a in touches}
        print(f"    distinct touch states across the walk: {len(distinct)}")
        if len(moved) == 0:
            print("    UNKNOWN: no book snapshot covers this walk.")
        elif len(distinct) <= 1:
            print("    FITS LADDER (INFERRED): the touch did not change across "
                  "the walk, so the levels were not reached sequentially by a "
                  "moving book.")
        else:
            print("    FITS REACTIVE (INFERRED): the touch changed "
                  f"{len(distinct)} times across the walk, consistent with "
                  "levels being reached sequentially.")
        print("    Either reading is INFERRED, not OBSERVED: order placement "
              "and cancellation are not in this feed.")

    print("\n" + "=" * 100)
    print("CLASSIFICATION OF WHAT THIS WINDOW SUPPORTS")
    print("=" * 100)
    print("  OBSERVED : L5's fills (venue timestamp, side, price, size), the "
          "native book snapshots and trade prints, our collection lag.")
    print("  INFERRED : whether a walk fits ladder or reactive, L5's position "
          "relative to the touch, any adverse selection.")
    print("  UNKNOWN  : L5's order placements and cancellations; whether a fill "
          "was maker or taker from his side; his unfilled intent; his reaction "
          "latency; whether a small fill is a partial of a larger resting "
          "order. None of these are in any public feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
