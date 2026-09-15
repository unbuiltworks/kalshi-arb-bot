"""The promotion test for L5_CHAIN_OBSERVER: does it agree, not just arrive first.

WHY THIS IS THE GATE. Observation lag is the second item on the measured
blocker list: median intent age at decision is 17.6 s of a 30 s validity, so
59% of an intent's life is gone before we can price anything.

Measured over n=73 joined fills, the chain is readable 0.37 s after its block
(p95 1.65 s) while /activity publishes 15.90 s after it (p95 30.08 s) -- a
median lead of 15.16 s, winning 73 of 73. (An earlier note said 19.6 s from a
10-observation sample; the larger series says 15.16 s.) On the chain route that
17.6 s of spent budget would be roughly 2.4 s.

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

from src.l5_chain_observer import classify as CL
from src.l5_chain_observer import decode as DE
from src.l5_chain_observer import observer as O

# a window that ENDS this long ago, so the venue has certainly published it
SETTLE_MARGIN_S = 300.0
WINDOW_S = 900.0                 # how much settled history to compare per pass
POLL_S = 120.0
# the feed's oldest row may itself be truncated mid-second, so the compared
# window stops short of it rather than butting against it
FEED_EDGE_MARGIN_S = 5.0
OUT = "validations.jsonl"
# THE MEASUREMENT EPOCH. Every row this module writes is stamped with it, and
# the gate counts only rows at or above its qualifying epoch. Without this the
# >=500 sample silently mixes in measurements taken by superseded code -- which
# it did: rows carrying "240 @ 0.35" from before legs were grouped by role sat
# in the same file as the corrected ones and failed G-C4 on their own.
#
#   1  hash-join only, no attribution
#   2  block-boundary fix (commit 47cb38e0): the block range contains the
#      time window on both sides
#   3  grouped attribution by (event, L5 role, token); every feed fill in a
#      transaction retained; decoding restricted to verified event
#      signatures; chain_only classified; window clamped to the feed's reach
#
# BUMP THIS whenever a change would make older rows non-comparable. It is
# cheaper to re-accumulate a sample than to argue about a contaminated one.
EPOCH = 3
ATTR = "attribution.jsonl"
CLASSIFIED = "chain_only_classified.jsonl"
# Gate G-C5: a chain fill counts as FINAL only this far behind the head. Chosen
# as a conservative multiple of Polygon's practical reorg depth, fixed in the
# preregistered gate and not tuned against observations.
CONFIRMATIONS = 64
# attribution costs one receipt fetch per transaction, so it is bounded per
# pass; over many passes it accumulates toward the gate's 500.
ATTR_PER_PASS = 25
# classification costs at most one receipt fetch per unexplained chain_only
# transaction, and most are settled from the feed alone at zero RPC cost.
CLASSIFY_PER_PASS = 25
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
    """A pass that could not complete is a FAILED PASS, not a dead process.

    The first version wrapped only the head query. `block_at` also calls the
    RPC, and a transient `TimeoutError` there escaped and killed the process --
    observed in production 15:28:42Z, returncode 1. The supervisor restarted it
    in two seconds and recorded the gap, which is the supervisor working, but a
    collector that dies on every network blip churns coverage holes into the
    record for no reason. Every RPC path is now inside the guard.
    """
    try:
        return _one_pass(settle_margin_s=settle_margin_s, window_s=window_s)
    except Exception as exc:                                  # noqa: BLE001
        return failed_pass(f"{type(exc).__name__}: {exc}")


_CODE = []


def code_version() -> Optional[str]:
    """The commit this row was measured by, for provenance (gate G-C8).

    Best effort and resolved once: the deployed image may carry no git
    metadata, and an unknown version is reported as None rather than guessed.
    The gate enforces EPOCH, which is always present; this only says which
    commit that epoch was running.
    """
    if not _CODE:
        v = None
        try:
            import subprocess
            v = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                               cwd=os.path.dirname(os.path.abspath(__file__)),
                               capture_output=True, text=True,
                               timeout=5).stdout.strip() or None
        except Exception:                                     # noqa: BLE001
            v = None
        _CODE.append(v)
    return _CODE[0]


def failed_pass(err: str, **extra: Any) -> Dict[str, Any]:
    """The ONE shape a pass that did not run takes.

    Every failure path returns this, so a reader never meets one failed pass
    carrying the caveat and another without it. It deliberately contains no
    agreement figures at all: an unrun pass reports nothing about the chain,
    and a zero here would be indistinguishable from a clean comparison.
    """
    return {"ok": False, "t": time.time(), "epoch": EPOCH,
            "code": code_version(), "err": err[:300], **extra,
            "labels": ("this pass did not run. It is NOT evidence about the "
                       "chain, and nothing may be concluded from it")}


def _one_pass(*, settle_margin_s: float = SETTLE_MARGIN_S,
              window_s: float = WINDOW_S) -> Dict[str, Any]:
    now = time.time()
    hi_ts = now - settle_margin_s
    lo_ts = hi_ts - window_s

    # THE FEED IS READ FIRST, because how far back it reaches decides which
    # window can honestly be compared at all.
    feed = O.activity_fills()
    if feed is None:
        return failed_pass("activity feed unreadable")
    reach = min((float(f["ts"]) for f in feed.values()
                 if f.get("ts") is not None), default=None)
    if reach is None:
        return failed_pass("activity feed carried no timestamps")
    # Measured 2026-09-15: the endpoint returns 500 rows whatever `limit` asks
    # for, so its reach is set by L5's own trade rate -- 3433 s when sampled,
    # but far shorter in a burst. A window extending past it would count every
    # chain fill in the uncovered stretch as `chain_only` purely because the
    # feed was never able to mention it. The window is therefore CLAMPED to
    # what the feed covers rather than assumed to fit inside it.
    floor_ts = reach + FEED_EDGE_MARGIN_S
    clamped = floor_ts > lo_ts
    lo_ts = max(lo_ts, floor_ts)
    if lo_ts >= hi_ts:
        return failed_pass(
            "the feed's reach does not cover any settled window",
            feed_reach_s=round(now - reach, 1), n_feed_total=len(feed))

    try:
        head = int(O.rpc("eth_blockNumber", []), 16)
        head_ts = block_ts(head)
    except O.RpcError as e:
        return failed_pass(f"head unreadable: {e}")
    if head_ts is None:
        return failed_pass("head block carried no timestamp")

    # the block range must CONTAIN the time window on both sides
    hi_b = block_at(hi_ts, head, head_ts, side="ceil")
    lo_b = block_at(lo_ts, head, head_ts, side="floor")
    ok, chain = O.chain_fills_checked(lo_b, hi_b)
    if not ok:
        # THE QUESTION COULD NOT BE PUT. This is never reported as "the chain
        # saw nothing": that conflation is exactly what made an earlier probe
        # claim 0 chain fills against 480 feed fills and call it a success.
        return failed_pass("chain could not be queried on any RPC",
                           blocks=[lo_b, hi_b])

    cv = O.cross_validate(chain, feed, window=(lo_ts, hi_ts))
    attr = _attribute(chain, feed, head=head, out_dir=_ATTR_DIR[0])
    conly = _classify_chain_only(cv.get("chain_only") or [], chain, feed,
                                 window=(lo_ts, hi_ts), out_dir=_ATTR_DIR[0])
    leads = _leads(chain, feed)
    return {
        "ok": True, "t": now, "schema": "l5_chain_validation/v1",
        "epoch": EPOCH, "code": code_version(),
        "window_utc": [lo_ts, hi_ts], "settle_margin_s": settle_margin_s,
        "blocks": [lo_b, hi_b],
        "n_chain": len(chain), "n_feed_total": len(feed),
        "feed_reach_s": round(now - reach, 1),
        "window_clamped_to_feed_reach": clamped,
        **{k: v for k, v in cv.items() if k != "labels"},
        "lead_not_measured_here": leads,
        "attribution": attr,
        "chain_only_classified": conly,
        "confirmations_required": CONFIRMATIONS,
        "labels": cv["labels"],
    }



def _classify_chain_only(txs: List[str], chain: Dict[str, dict],
                         feed: Dict[str, dict], *,
                         window: Tuple[float, float],
                         out_dir: Optional[str]) -> Dict[str, Any]:
    """Gate G-C3: every chain_only transaction gets a NAMED cause.

    `feed` is passed unwindowed on purpose. The question is whether the venue
    published this transaction at all; whether it published it inside the
    interval we happened to compare is a different question, and conflating
    them is what makes a boundary artifact look like the chain inventing fills.
    """
    if not txs:
        return {"n": 0, "by_category": {}, "classified": 0, "unresolved": 0,
                "outside_permitted_categories": 0, "all_classified": True,
                "note": "no chain_only transactions in this window"}
    oldest = min((float(f["ts"]) for f in feed.values()
                  if f.get("ts") is not None), default=None)
    rows = []
    for tx in txs[:CLASSIFY_PER_PASS]:
        blk = chain.get(tx, {}).get("block")
        rows.append(CL.classify_one(
            tx, feed=feed, window=window,
            receipt_fn=lambda t: O.rpc("eth_getTransactionReceipt", [t]),
            l5_topic=O.PAD, exchanges=O.EXCHANGES,
            feed_horizon_ts=oldest,
            block_ts=(block_ts(blk) if blk is not None else None)))
    if out_dir and rows:
        with open(os.path.join(out_dir, CLASSIFIED), "a") as fh:
            for r in rows:
                fh.write(json.dumps({**r, "t": time.time(),
                                     "epoch": EPOCH, "code": code_version(),
                                     "window_utc": list(window)},
                                    default=str) + "\n")
    out = CL.summarise(rows)
    out["n_chain_only"] = len(txs)
    out["examples"] = [{"tx": r["tx"], "category": r["category"]}
                       for r in rows[:5]]
    return out


_ATTR_DIR = [None]          # set by main(); keeps one_pass's signature stable


def _attribute(chain: Dict[str, dict], feed: Dict[str, dict], *, head: int,
               out_dir: Optional[str]) -> Dict[str, Any]:
    """Gate G-C4: do the two sources agree about WHAT happened, not just THAT.

    Only FINAL transactions are attributed (gate G-C5): a fill fewer than
    CONFIRMATIONS blocks behind the head can still be reorged away, and
    counting it as evidence is the "faster but wrong" failure in miniature.

    Every comparison is at the TRANSACTION AGGREGATE, because a transaction may
    carry several fills -- comparing one leg against one feed row produced two
    phantom one-cent disagreements before this was understood.
    """
    shared = [t for t in chain if t in feed]
    final = [t for t in shared
             if chain[t].get("block") is not None
             and head - int(chain[t]["block"]) >= CONFIRMATIONS]
    rows, agree_px, agree_sz, mismatch = [], 0, 0, []
    for tx in final[:ATTR_PER_PASS]:
        try:
            rc = O.rpc("eth_getTransactionReceipt", [tx])
        except O.RpcError:
            continue                    # unreadable is UNKNOWN, not a mismatch
        gs = DE.groups(DE.decode_receipt(rc, l5_topic=O.PAD,
                                         exchanges=O.EXCHANGES))
        f = feed[tx]
        if not gs or f.get("agg_size") is None:
            continue
        # a transaction carries several groups -- L5 as taker and as maker, on
        # different tokens, and a second event restating the same fill. The
        # feed row refers to ONE of them, so the match is against the group
        # rather than against a sum across all of them.
        m = DE.best_match(gs, price=f["agg_price"], size=f["agg_size"])
        px_ok = sz_ok = m is not None
        agree_px += px_ok
        agree_sz += sz_ok
        agg = m or gs[0]
        row = {"tx": tx, "block": chain[tx].get("block"),
               "confirmations": head - int(chain[tx]["block"]),
               "chain_price": agg["price"], "feed_price": f["agg_price"],
               "chain_size": agg["size"], "feed_size": f["agg_size"],
               "price_agrees": px_ok, "size_agrees": sz_ok,
               "matched_group": (m["group_key"] if m else None),
               "n_groups": len(gs),
               "all_groups": [{"price": g["price"], "size": g["size"],
                               **g["group_key"]} for g in gs],
               "n_legs": agg["n_legs"], "n_feed_fills": f.get("n_fills"),
               "token_ids": agg["token_ids"], "blocks": agg["blocks"],
               "log_indexes": agg["log_indexes"],
               "contract": chain[tx].get("contract"),
               "l5_topic_pos": chain[tx].get("topic_pos"),
               "slug": f.get("slug"), "t": time.time()}
        rows.append(row)
        if not (px_ok and sz_ok):
            mismatch.append(row)
    if out_dir and rows:
        with open(os.path.join(out_dir, ATTR), "a") as fh:
            for r in rows:
                fh.write(json.dumps({**r, "epoch": EPOCH,
                                     "code": code_version()},
                                    default=str) + "\n")
    n = len(rows)
    return {
        "n_shared": len(shared), "n_final": len(final), "n_attributed": n,
        "price_agree": agree_px, "size_agree": agree_sz,
        "price_agree_rate": (round(agree_px / n, 4) if n else None),
        "size_agree_rate": (round(agree_sz / n, 4) if n else None),
        "mismatches": mismatch[:5],
        "labels": ("aggregate-vs-aggregate over a whole transaction, because a "
                   "transaction may carry several fills. Only transactions at "
                   f"least {CONFIRMATIONS} blocks deep are attributed."),
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
    15.16s ahead over 73 of 73 joined fills. A settled-window
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
                "(analysis/l5/latency/chainwatch.py), which measures 15.16s "
                "median over n=73."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-chain-validate")
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    _ATTR_DIR[0] = a.out_dir
    print(f"L5_CHAIN_VALIDATE boot: settled-window cross-validation, "
          f"margin={SETTLE_MARGIN_S}s window={WINDOW_S}s. READ ONLY; "
          f"observer.PROMOTED={O.PROMOTED}", flush=True)
    deadline = time.time() + a.hours * 3600 if a.hours else None
    consecutive_errors = 0
    while True:
        rec = one_pass()
        consecutive_errors = 0 if rec.get("ok") else consecutive_errors + 1
        rec["consecutive_errors"] = consecutive_errors
        with open(os.path.join(a.out_dir, OUT), "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print("L5_CHAIN_VALIDATE " + json.dumps(
            {k: rec.get(k) for k in
             ("ok", "epoch", "err", "consecutive_errors", "n_chain",
              "n_union", "agree", "agreement_rate", "feed_only",
              "promotion_blocker", "feed_reach_s",
              "window_clamped_to_feed_reach", "attribution",
              "chain_only_classified")},
            default=str), flush=True)
        if a.once or (deadline is not None and time.time() >= deadline):
            break
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
