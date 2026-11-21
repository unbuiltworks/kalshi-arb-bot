"""Real block timestamps for every block carrying an L5 fill.

WHY NOT INTERPOLATE. The first offset table timed each fill by linear
interpolation across its window's block range. That put 27-36% of L5's MAKER
buys at or above the prevailing ask -- impossible for a resting bid -- and the
rate FELL as the lead shortened (35.6% at T-30, 27.2% at T-1), which is the
signature of a mistimed snapshot rather than a mis-joined token: the nearer the
snapshot, the less the book has moved since, so the fewer impossible offsets.

A block timestamp is a fact on the chain and costs one call. 1695 blocks is
cheap enough that interpolation is not worth defending, so this fetches them
all and the residual against the old estimate is reported rather than assumed
small.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict, List

from src.l5_chain_observer import observer as O

ARCHIVE = "https://polygon.drpc.org"
PACE_S = 0.1


def fetch(blocks: List[int], url: str = ARCHIVE) -> Dict[int, int]:
    """block -> unix timestamp. A block that cannot be read is OMITTED, never
    defaulted: a missing timestamp must stay missing so the offset row it feeds
    becomes UNKNOWN rather than silently wrong."""
    out: Dict[int, int] = {}
    for n, b in enumerate(sorted(blocks), 1):
        try:
            r = O.rpc("eth_getBlockByNumber", [hex(b), False], url)
            if r and r.get("timestamp"):
                out[b] = int(r["timestamp"], 16)
        except Exception:                                     # noqa: BLE001
            pass
        if n % 200 == 0:
            print(f"  {n}/{len(blocks)}", flush=True)
        time.sleep(PACE_S)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--out", default="analysis/l5/historical_l2/block_times.json")
    a = ap.parse_args(argv)

    bf = json.load(open(a.backfill))
    blocks = sorted({f["block"] for w in bf["rows"] for f in w["fills"]
                     if f.get("block") is not None})
    print(f"{len(blocks)} distinct blocks", flush=True)
    ts = fetch(blocks)
    print(f"got {len(ts)}/{len(blocks)}")

    # the residual against the interpolation the first table used
    res = []
    for w in bf["rows"]:
        lo_b, hi_b = w["blocks"]
        lo_t, hi_t = w["window_utc"]
        if hi_b == lo_b:
            continue
        for f in w["fills"]:
            b = f.get("block")
            if b is None or b not in ts:
                continue
            est = lo_t + (b - lo_b) * (hi_t - lo_t) / (hi_b - lo_b)
            res.append(ts[b] - est)
    res.sort()
    def q(p):
        return round(res[int(p * (len(res) - 1))], 1) if res else None
    rep = {"n_blocks": len(ts), "n_requested": len(blocks),
           "block_times": {str(k): v for k, v in ts.items()},
           "interpolation_residual_s": {
               "n": len(res), "min": q(0.0), "p05": q(0.05), "p50": q(0.50),
               "p95": q(0.95), "max": q(1.0),
               "mean": (round(sum(res) / len(res), 2) if res else None),
               "what": ("real block timestamp MINUS the linear interpolation "
                        "the first offset table used; positive means the fill "
                        "actually happened LATER than that table assumed")}}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep["interpolation_residual_s"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
