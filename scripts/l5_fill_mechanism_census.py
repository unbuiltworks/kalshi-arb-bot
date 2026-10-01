"""Which MECHANISM produced each L5 fill? Per fill, across synchronized windows.

WHY THIS REPLACES THE THREE-WAY PRE-RESTING TAXONOMY. That taxonomy
(CONSISTENT_WITH_PRE_RESTING / REQUIRES_POST_EVENT_ORDER / AMBIGUOUS) presumed
every fill CONSUMES BOOK LIQUIDITY. A complete-set mint consumes none: two buyers
of opposite outcomes are matched by minting $1, needing no order on either side of
L5's own book. So "no bid depth at his price" does not imply "an order arrived
after the event", and on window 1 that mis-specification turned every burst into a
false REQUIRES_POST_EVENT_ORDER.

Each fill is placed on the native timeline at MILLISECOND resolution by joining
`last_trade_price.transaction_hash` to the activity row's `transactionHash`, then
classified on two independent axes:

  POSITION vs the contemporaneous touch of the token he bought
      AT_OR_ABOVE_ASK | INSIDE_SPREAD | AT_OR_BELOW_BID
  MINT FEASIBILITY    his price + the COMPLEMENT's best bid >= 1

`pair_sum` is his fill price plus the complement's best bid -- the joint cost of
minting a dollar. It is NOT his own Up cost plus his own Down cost, and it says
nothing about what he paid for a pair.

Touches are read at a 250 ms MARGIN before the fill, because an update caused by
the fill can carry a timestamp at or before the trade's.

NO ATTRIBUTION. Liquidity is anonymous throughout.

    python3 scripts/l5_fill_mechanism_census.py analysis/l5/sync_l2/*.jsonl
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List

MARGIN_S = 0.25


def read(path: str) -> Dict[str, Any]:
    cond, toks, outc = None, [], []
    touch: Dict[str, List] = {}
    acts, trades = [], {}
    with open(path) as fh:
        for line in fh:
            if cond is None and '"window_open"' in line:
                ids = (json.loads(line)["payload"].get("ids") or {})
                cond = str(ids.get("condition_id"))
                toks = [str(t) for t in (ids.get("token_ids") or [])]
                outc = ids.get("outcomes") or []
                continue
            if '"kind": "activity"' in line:
                if cond and cond not in line:
                    continue
                r = json.loads(line)
                for row in (r["payload"] or []):
                    if str(row.get("conditionId")) == cond:
                        acts.append(row)
            elif '"kind": "ws"' in line:
                if "price_change" not in line and "last_trade_price" not in line:
                    continue
                r = json.loads(line)
                pay = r["payload"]
                for e in (pay if isinstance(pay, list) else [pay]):
                    if not isinstance(e, dict):
                        continue
                    if e.get("event_type") == "price_change":
                        ms = float(e.get("timestamp") or 0) / 1000.0
                        for c in (e.get("price_changes") or []):
                            touch.setdefault(str(c.get("asset_id")), []).append(
                                (ms, c.get("best_bid"), c.get("best_ask")))
                    elif e.get("event_type") == "last_trade_price":
                        trades[str(e.get("transaction_hash")).lower()] = {
                            "ts": float(e.get("timestamp") or 0) / 1000.0,
                            "asset": str(e.get("asset_id"))}
    for v in touch.values():
        v.sort(key=lambda x: x[0])
    return {"cond": cond, "toks": toks, "outcomes": outc, "touch": touch,
            "acts": acts, "trades": trades}


def touch_at(touch, aid, t):
    out = (None, None)
    for ms, b, a in touch.get(aid, []):
        if ms <= t:
            out = (b, a)
        else:
            break
    return out


def classify(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    toks = d["toks"]
    if len(toks) != 2:
        return []
    other = {toks[0]: toks[1], toks[1]: toks[0]}
    seen, rows = set(), []
    for row in d["acts"]:
        key = (row.get("transactionHash"), row.get("timestamp"),
               str(row.get("price")), row.get("outcome"))
        if key in seen:
            continue
        seen.add(key)
        j = d["trades"].get(str(row.get("transactionHash")).lower())
        if not j:
            rows.append({"joined": False, "outcome": row.get("outcome"),
                         "px": float(row.get("price") or 0),
                         "size": float(row.get("size") or 0)})
            continue
        t = j["ts"] - MARGIN_S
        ob, oa = touch_at(d["touch"], j["asset"], t)
        cb, _ca = touch_at(d["touch"], other[j["asset"]], t)
        px = float(row.get("price") or 0)
        ps = (px + float(cb)) if cb else None
        if oa and px >= float(oa):
            pos = "AT_OR_ABOVE_ASK"
        elif ob and px <= float(ob):
            pos = "AT_OR_BELOW_BID"
        elif ob or oa:
            pos = "INSIDE_SPREAD"
        else:
            pos = "NO_TOUCH_KNOWN"
        rows.append({"joined": True, "ts": j["ts"],
                     "outcome": row.get("outcome"), "px": px,
                     "size": float(row.get("size") or 0),
                     "own_bid": ob, "own_ask": oa, "compl_bid": cb,
                     "pair_sum": ps, "position": pos,
                     "mint_feasible": (ps is not None and ps >= 1.0)})
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def main(argv) -> int:
    paths = [p for p in argv[1:] if os.path.exists(p)]
    if not paths:
        print(__doc__)
        return 2
    pos_c, mint_c, both = Counter(), Counter(), Counter()
    n_fills = n_joined = 0
    print("=" * 104)
    print("L5 FILL MECHANISM CENSUS from synchronized native L2")
    print("=" * 104)
    for p in sorted(paths):
        d = read(p)
        rows = classify(d)
        if not rows:
            continue
        print(f"\n  {os.path.basename(p)}   fills {len(rows)}")
        print("    %-5s %-8s %-11s %8s %8s %10s %9s  %s" % (
            "out", "px", "size", "own_bid", "own_ask", "compl_bid",
            "pair_sum", "mechanism"))
        for r in rows:
            n_fills += 1
            if not r["joined"]:
                print("    %-5s %-8.4f %-11s  UNJOINED to the native feed"
                      % (r["outcome"], r["px"], r["size"]))
                continue
            n_joined += 1
            pos_c[r["position"]] += 1
            mint_c["mint_feasible" if r["mint_feasible"]
                   else "not_mint_feasible"] += 1
            both[(r["position"], r["mint_feasible"])] += 1
            print("    %-5s %-8.4f %-11s %8s %8s %10s %9s  %s + %s" % (
                r["outcome"], r["px"], r["size"], r["own_bid"], r["own_ask"],
                r["compl_bid"],
                ("%.4f" % r["pair_sum"]) if r["pair_sum"] is not None else "n/a",
                r["position"],
                "MINT_FEASIBLE" if r["mint_feasible"] else "not_mint_feasible"))

    print("\n" + "=" * 104)
    print(f"TOTALS   fills {n_fills}   joined to the native feed {n_joined}")
    print("=" * 104)
    for k, v in pos_c.most_common():
        print("  %-18s %4d  %5.1f%%" % (k, v, 100.0 * v / max(1, n_joined)))
    print()
    for k, v in mint_c.most_common():
        print("  %-18s %4d  %5.1f%%" % (k, v, 100.0 * v / max(1, n_joined)))
    print("\n  joint distribution (position, mint_feasible):")
    for (pos, mf), v in both.most_common():
        print("    %-18s mint=%-5s %4d" % (pos, mf, v))
    print("\n  OBSERVED : ms-resolution fill timing via transaction_hash; "
          "venue-stated touches; position and mint feasibility.")
    print("  INFERRED : that a mint-feasible at/above-ask fill WAS a mint.")
    print("  UNKNOWN  : whose liquidity; whether L5 cancels or replaces; "
          "mint versus book for any individual fill; his unfilled intent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
