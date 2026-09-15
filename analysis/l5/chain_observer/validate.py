"""The promotion test for L5_CHAIN_OBSERVER: does it agree, not just arrive first.

WHY THIS IS THE GATE. The chain carried the same fill a median 19.6 s earlier
than /activity and won 10 of 10 observations, and observation lag is the second
item on the measured blocker list -- median intent age at decision is 17.6 s of
a 30 s validity, so 59 % of an intent's life is already gone when we can first
price it. A ~4 s route instead of a ~15 s one would roughly triple the usable
life of every intent.

None of which matters if the two sources disagree about WHAT happened. A faster
feed that misses fills is worse than a slow one, because a missed fill is
indistinguishable from L5 not trading, and the engine would read it as a
decision rather than as an absence.

THE ASYMMETRY IS THE WHOLE TEST:

    chain_only   EXPECTED and benign -- the chain sees it before publication
    feed_only    DANGEROUS -- a fill the chain missed. Must be empty.

A feed row inside the publication lag is not yet evidence of a miss, so the
comparison runs over a SETTLED window: it ends far enough in the past that the
venue has certainly published everything in it. The default margin is 300 s
against a measured p95 publication lag of ~30 s -- a 10x cushion, chosen so a
single slow publish cannot masquerade as a chain miss.

READ ONLY, AND NOT A PROMOTION. This records evidence; `observer.PROMOTED`
stays False and is flipped by a human.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from src.l5_chain_observer import observer as O

# a window that ENDS this long ago, so the venue has certainly published it
SETTLE_MARGIN_S = 300.0
WINDOW_S = 900.0                 # how much settled history to compare per pass
POLL_S = 120.0
OUT = "validations.jsonl"
POLYGON_BLOCK_S = 2.1            # nominal; every bound is verified against the
                                 # chain's own timestamps rather than trusted


def block_ts(n: int) -> Optional[float]:
    b = O.rpc("eth_getBlockByNumber", [hex(n), False])
    if not isinstance(b, dict) or not b.get("timestamp"):
        return None
    return float(int(b["timestamp"], 16))


def block_at(target_ts: float, hi_block: int, hi_ts: float, *,
             side: str = "floor") -> int:
    """A block bracketing `target_ts`: `floor` gives ts <= target, `ceil` >=.

    THE SIDE MATTERS AND GETTING IT WRONG MANUFACTURES A PROMOTION BLOCKER.
    The first version accepted any block within one block-time AFTER the
    target and used it for both bounds. On a live pass that put lo_block at
    13:51:43 for a window opening at 13:51:41, and a real L5 fill in the block
    at 13:51:42 fell outside the queried range while its timestamp sat inside
    the time window. It was reported as `feed_only` -- "the chain MISSED a
    fill" -- when the chain had simply never been asked about that block.

    So the block range must strictly CONTAIN the time window. Any excess is
    harmless: it can only produce `chain_only` rows, which are expected.

    The nominal block time is a starting guess only; every candidate is
    verified against the chain's own timestamp, because a drifting estimate
    silently shifts the window.
    """
    n = max(1, int(hi_block - (hi_ts - target_ts) / POLYGON_BLOCK_S))
    best: Optional[int] = None
    for _ in range(60):
        ts = block_ts(n)
        if ts is None:
            break
        if side == "floor":
            if ts <= target_ts:
                best = n
                if target_ts - ts <= POLYGON_BLOCK_S:
                    break
                n += max(1, int((target_ts - ts) / POLYGON_BLOCK_S))
            else:
                n -= max(1, int((ts - target_ts) / POLYGON_BLOCK_S))
        else:
            if ts >= target_ts:
                best = n
                if ts - target_ts <= POLYGON_BLOCK_S:
                    break
                n -= max(1, int((ts - target_ts) / POLYGON_BLOCK_S))
            else:
                n += max(1, int((target_ts - ts) / POLYGON_BLOCK_S))
        n = max(1, min(n, hi_block))
    if best is None:
        best = n
    # one more block of slack on each side, so a reorg or an estimate landing
    # exactly on a boundary still leaves the window strictly contained
    return max(1, best - 1) if side == "floor" else min(hi_block, best + 1)


def one_pass(*, settle_margin_s: float = SETTLE_MARGIN_S,
             window_s: float = WINDOW_S) -> Dict[str, Any]:
    now = time.time()
    hi_ts = now - settle_margin_s
    lo_ts = hi_ts - window_s
    try:
        head = int(O.rpc("eth_blockNumber", []), 16)
        head_ts = block_ts(head)
    except O.RpcError as e:
        return {"ok": False, "err": f"head unreadable: {e}", "t": now}
    if head_ts is None:
        return {"ok": False, "err": "head block carried no timestamp", "t": now}

    # the block range must CONTAIN the time window on both sides
    hi_b = block_at(hi_ts, head, head_ts, side="ceil")
    lo_b = block_at(lo_ts, head, head_ts, side="floor")
    ok, chain = O.chain_fills_checked(lo_b, hi_b)
    if not ok:
        # THE QUESTION COULD NOT BE PUT. This is never reported as "the chain
        # saw nothing": that conflation is exactly what made an earlier probe
        # claim 0 chain fills against 480 feed fills and call it a success.
        return {"ok": False, "err": "chain could not be queried on any RPC",
                "t": now, "blocks": [lo_b, hi_b]}
    feed = O.activity_fills()
    if feed is None:
        return {"ok": False, "err": "activity feed unreadable", "t": now}

    cv = O.cross_validate(chain, feed, window=(lo_ts, hi_ts))
    leads = _leads(chain, feed)
    return {
        "ok": True, "t": now, "schema": "l5_chain_validation/v1",
        "window_utc": [lo_ts, hi_ts], "settle_margin_s": settle_margin_s,
        "blocks": [lo_b, hi_b],
        "n_chain": len(chain), "n_feed_total": len(feed),
        **{k: v for k, v in cv.items() if k != "labels"},
        "lead_not_measured_here": leads,
        "labels": cv["labels"],
    }


def _leads(chain: Dict[str, dict], feed: Dict[str, dict]) -> Dict[str, Any]:
    """NOT a lead measurement, and this function exists to say so.

    The first version of this differenced the feed's `timestamp` against the
    chain block timestamp and reported a median lead of 0.0s over 40 shared
    fills. That is not "no lead" -- it is the SAME CLOCK TWICE. Polymarket's
    activity feed publishes the on-chain event time, not the moment it
    published the row, so the difference is identically zero by construction
    and says nothing about which source we could have READ first.

    The observational lead is a different measurement entirely: poll both
    surfaces and record when each FIRST carried a given transaction. That is
    what analysis/l5/latency/chainwatch.py does, and it measured the chain
    ~19.6s ahead over 10 of 10 joined observations. A settled-window
    reconciliation like this one runs every couple of minutes and structurally
    cannot see first-arrival, so it does not try.
    """
    shared = sum(1 for t in chain if t in feed)
    return {
        "measured": False,
        "shared_fills": shared,
        "why": ("the activity feed's timestamp IS the chain block timestamp, "
                "so differencing them is zero by construction and is not a "
                "lead. Observational lead belongs to the first-seen probe "
                "(analysis/l5/latency/chainwatch.py), which measured ~19.6s."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-chain-validate")
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    print(f"L5_CHAIN_VALIDATE boot: settled-window cross-validation, "
          f"margin={SETTLE_MARGIN_S}s window={WINDOW_S}s. READ ONLY; "
          f"observer.PROMOTED={O.PROMOTED}", flush=True)
    deadline = time.time() + a.hours * 3600 if a.hours else None
    while True:
        rec = one_pass()
        with open(os.path.join(a.out_dir, OUT), "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print("L5_CHAIN_VALIDATE " + json.dumps(
            {k: rec.get(k) for k in
             ("ok", "err", "n_chain", "n_union", "agree", "agreement_rate",
              "feed_only", "promotion_blocker")},
            default=str), flush=True)
        if a.once or (deadline is not None and time.time() >= deadline):
            break
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
