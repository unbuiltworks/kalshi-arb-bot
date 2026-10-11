"""Backfill L5's BTC15m fills for the Zenodo L2 window, from the chain.

WHY A BACKFILL IS NEEDED AT ALL. Our canonical history covers 2026-03-19..04-21
and 2026-08-28..31. The Zenodo capture is 2026-08-21..24, so on our file the
overlap is zero -- but that is a gap in OUR COLLECTION, not in L5's activity.
Sampled directly, the chain shows him trading on 2026-08-21 (157 L5 logs across
four 105-second probes), 08-22 (86) and 08-24 (150), against a positive control
of 141 on a day we do have. So the dataset does overlap him; we simply never
collected those days.

SCOPE IT BY THE DATASET, NOT BY THE DAY. The parquet names 33 BTC15m windows
and their 66 token ids, so only those block ranges are scanned -- about 430
blocks each rather than four whole days. The token ids are the join key and
nothing is matched by market name.

The archive RPC caps eth_getLogs at ~50 blocks, so each window is walked in
chunks. A chunk that fails is recorded as a HOLE and never counted as an
absence of fills.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.l5_chain_observer import decode as DE
from src.l5_chain_observer import observer as O

ARCHIVE = "https://polygon.drpc.org"
LOG_SPAN = 50            # the node's eth_getLogs limit
PACE_S = 0.12


def block_ts(b: int, url: str = ARCHIVE) -> Optional[int]:
    r = O.rpc("eth_getBlockByNumber", [hex(b), False], url)
    return int(r["timestamp"], 16) if r else None


class Clock:
    """Two anchors and a linear fit, refined locally. A binary search per bound
    would cost ~25 calls each; this costs two calls total plus a nudge."""

    def __init__(self, lo_b: int, hi_b: int) -> None:
        self.lo_b, self.lo_t = lo_b, block_ts(lo_b)
        self.hi_b, self.hi_t = hi_b, block_ts(hi_b)
        self.rate = (self.hi_t - self.lo_t) / (self.hi_b - self.lo_b)

    def block_at(self, ts: int, *, refine: int = 2) -> int:
        b = int(self.lo_b + (ts - self.lo_t) / self.rate)
        for _ in range(refine):
            t = block_ts(b)
            if t is None:
                break
            off = int((ts - t) / self.rate)
            if abs(off) <= 1:
                break
            b += off
            time.sleep(PACE_S)
        return b


def l5_logs(lo: int, hi: int, url: str = ARCHIVE) -> Tuple[List[dict], int]:
    """(logs, holes). A failed chunk is a HOLE, never an empty answer."""
    out, holes = [], 0
    b = lo
    while b <= hi:
        top = min(b + LOG_SPAN, hi)
        got = False
        for pos in (2, 3):
            topics: List[Any] = [None] * (pos + 1)
            topics[pos] = O.PAD
            try:
                logs = O.rpc("eth_getLogs",
                             [{"fromBlock": hex(b), "toBlock": hex(top),
                               "address": list(O.EXCHANGES),
                               "topics": topics}], url)
                out.extend(logs or [])
                got = True
            except O.RpcError:
                pass
            time.sleep(PACE_S)
        if not got:
            holes += 1
        b = top + 1
    return out, holes


def fills_for_window(slug: str, start_ts: int, end_ts: int, clock: Clock,
                     *, tokens: Sequence[str]) -> Dict[str, Any]:
    lo = clock.block_at(start_ts)
    hi = clock.block_at(end_ts)
    logs, holes = l5_logs(lo, hi)
    seen, fills = set(), []
    want = set(str(t) for t in tokens)
    for lg in logs:
        leg = DE.decode_log(lg, l5_topic=O.PAD)
        if not leg or leg["token_id"] not in want:
            continue
        k = (leg["tx"], leg["log_index"])
        if k in seen:
            continue
        seen.add(k)
        fills.append(leg)
    return {"market_slug": slug, "blocks": [lo, hi],
            "window_utc": [start_ts, end_ts],
            "logs_seen": len(logs), "chunk_holes": holes,
            "fills": fills, "n_fills": len(fills),
            "coverage": ("COMPLETE" if holes == 0 else f"{holes} HOLE(S)")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--out", default="analysis/l5/historical_l2/l5_backfill.json")
    ap.add_argument("--max-windows", type=int, default=0)
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    t = pq.read_table(a.parquet, columns=["coin", "market_slug", "token_id",
                                          "outcome", "market_end_at"])
    d = t.to_pydict()
    wins: Dict[str, Dict[str, Any]] = {}
    for i, c in enumerate(d["coin"]):
        if c != "btc":
            continue
        s = d["market_slug"][i]
        w = wins.setdefault(s, {"tokens": {}, "end": d["market_end_at"][i]})
        w["tokens"][str(d["token_id"][i])] = d["outcome"][i]
    slugs = sorted(wins)
    if a.max_windows:
        slugs = slugs[:a.max_windows]
    print(f"{len(slugs)} BTC15m windows from the parquet", flush=True)

    start0 = int(slugs[0].rsplit("-", 1)[1])
    end0 = int(slugs[-1].rsplit("-", 1)[1]) + 900
    clock = Clock(*_anchors(start0, end0))
    print(f"clock: {clock.rate:.4f}s/block", flush=True)

    out = []
    for n, s in enumerate(slugs, 1):
        st = int(s.rsplit("-", 1)[1])
        r = fills_for_window(s, st, st + 900, clock,
                             tokens=list(wins[s]["tokens"]))
        r["tokens"] = wins[s]["tokens"]
        out.append(r)
        print(f"  [{n}/{len(slugs)}] {s}  fills={r['n_fills']:3d}  "
              f"{r['coverage']}", flush=True)
    tot = sum(r["n_fills"] for r in out)
    holes = sum(r["chunk_holes"] for r in out)
    rep = {"windows": len(out), "total_l5_fills": tot, "chunk_holes": holes,
           "rows": out,
           "labels": ("chain-derived, joined to the dataset by TOKEN ID. A "
                      "failed chunk is a HOLE and is never counted as an "
                      "absence of fills.")}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(f"\nTOTAL L5 fills in the overlap: {tot} across {len(out)} windows "
          f"({holes} chunk holes)")
    return 0


def _anchors(start_ts: int, end_ts: int) -> Tuple[int, int]:
    """Two blocks bracketing the capture, found by coarse binary search once."""
    def find(ts):
        lo, hi = 90_000_000, 93_100_000
        while lo < hi:
            mid = (lo + hi) // 2
            t = block_ts(mid)
            if t is None:
                return lo
            lo, hi = (mid + 1, hi) if t < ts else (lo, mid)
            time.sleep(PACE_S)
        return lo
    return find(start_ts), find(end_ts)


if __name__ == "__main__":
    raise SystemExit(main())
