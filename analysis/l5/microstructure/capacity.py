"""Section 11 of the overnight brief: capacity, DESCRIPTIVE ONLY.

Capacity is expressed in latent clips, shares and observable executable
depth. It is explicitly NOT obtained by scaling historical dollars: L5's
dollar P&L is not ours, his queue position is not ours, and adding our own
size changes the book we would be measuring. Nothing here is a projection of
what we could earn.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List

from analysis.l5.microstructure.stats import qs

CLIP80 = 80.0


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def run(ev: List[dict], tag: str) -> Dict[str, Any]:
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)

    shares, notional, fills = [], [], []
    for g in byw.values():
        shares.append(sum(r["quantity"] for r in g))
        notional.append(sum(r["quantity"] * r["price"] for r in g))
        fills.append(len(g))

    depth_mine = [r["size_at_fill_price_before"] for r in ev
                  if r.get("size_at_fill_price_before") is not None]
    depth_at_or_above = [r["size_at_or_above_fill_price_before"] for r in ev
                         if r.get("size_at_or_above_fill_price_before")
                         is not None]
    total_bid = [r["total_bid_depth_own"] for r in ev
                 if r.get("total_bid_depth_own") is not None]

    return {
        "n_windows": len(byw),
        "LABEL": "DESCRIPTIVE CAPACITY -- not a projection, not scaled dollars",
        "per_window_shares_acquired": qs(shares),
        "per_window_notional_usdc_at_fill_price": qs(notional),
        "per_window_fills": qs(fills),
        "per_window_equivalent_80_share_clips": qs(
            [s / CLIP80 for s in shares]),
        "observable_depth_at_L5s_own_price_before_the_fill": qs(depth_mine),
        "observable_depth_at_or_above_L5s_price": qs(depth_at_or_above),
        "total_observable_bid_depth_own_token": qs(total_bid),
        "WHY_THIS_IS_NOT_OUR_CAPACITY": (
            "Three reasons, each sufficient. (1) Queue position is "
            "unobservable: the depth above is anonymous and we cannot tell "
            "how much of it is ahead of a hypothetical order of ours. "
            "(2) Our own size is not neutral -- adding it changes the book "
            "being measured, so a depth figure read off L5's tape is not a "
            "fill guarantee at that size. (3) Cancelled and never-filled "
            "orders are invisible, so L5's own exposed size is a lower "
            "bound and the clip count below is a floor, not a ceiling."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--events5", required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)
    out = {"protocol": "PREREGISTRATION_OVERNIGHT.md",
           "S11_BTC15m": run(jl(a.events15), "15m"),
           "S11_BTC5m": run(jl(a.events5), "5m")}
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "CAPACITY.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written CAPACITY.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
