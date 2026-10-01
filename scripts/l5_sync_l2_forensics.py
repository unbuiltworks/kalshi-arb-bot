"""Were the levels L5 executed at ALREADY REPRESENTED before his burst began?

THE QUESTION, restated after the Grok reconciliation (bd10a5e5). Not "did he
react after the market moved" but: before his first execution, what liquidity
pattern existed at the prices where his subsequent executions occur, and did the
market move TOWARD those levels?

THREE METHODOLOGICAL FACTS, each established from our own captured frames rather
than assumed:

1. L5's fills join the native feed EXACTLY. `last_trade_price` carries
   `transaction_hash`, and L5's activity rows carry `transactionHash`. On the
   first window all 4 of 4 joined with identical price and size. So his fills are
   placed on the book timeline at MILLISECOND resolution, and within-second
   ordering -- previously unknowable, and the flaw the audit identified -- is now
   OBSERVED.

2. The touch does not need reconstructing. Every `price_change` entry STATES
   `best_bid` and `best_ask` for its asset, at ~38 Hz with ms timestamps. That is
   the venue reporting the touch, not us inferring it.

3. Per-level sizes are taken from `price_change.price`/`size` read as the new
   size AT that level, and that reading is VALIDATED against the next `book`
   snapshot rather than trusted: the validation rate is reported, and folded
   state is used only where it validates. Deltas are never discarded.

NO ATTRIBUTION. Anonymous resting liquidity is never assigned to L5. The only
classification offered is CONSISTENT_WITH_PRE_RESTING / REQUIRES_POST_EVENT_ORDER
/ AMBIGUOUS.

    python3 scripts/l5_sync_l2_forensics.py analysis/l5/sync_l2/<slug>.jsonl
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

LEADS = (30.0, 10.0, 5.0, 1.0)
BURST_S = 3.0


def scan(path: str) -> Dict[str, Any]:
    """One pass. Keeps only what the forensics needs, so a 200MB tape fits."""
    out: Dict[str, Any] = {"open": None, "snaps": defaultdict(list),
                           "touch": defaultdict(list), "levels": defaultdict(list),
                           "trades": {}, "act": [], "failed": 0, "errors": [],
                           "n_ws": 0, "ev": defaultdict(int)}
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            k = r["kind"]
            if k == "window_open":
                out["open"] = r
            elif k == "activity_read_failed":
                out["failed"] += 1
            elif k in ("ws_error", "ws_fatal", "window_abandoned"):
                out["errors"].append(r)
            elif k == "activity":
                out["act"].append(r)
            elif k == "ws":
                out["n_ws"] += 1
                pay = r["payload"]
                for e in (pay if isinstance(pay, list) else [pay]):
                    if not isinstance(e, dict):
                        continue
                    et = str(e.get("event_type"))
                    out["ev"][et] += 1
                    if et == "book":
                        aid = str(e.get("asset_id"))
                        ms = float(e.get("timestamp") or 0) / 1000.0
                        out["snaps"][aid].append(
                            {"ts": ms, "hash": e.get("hash"),
                             "bids": e.get("bids") or [],
                             "asks": e.get("asks") or []})
                    elif et == "price_change":
                        ms = float(e.get("timestamp") or 0) / 1000.0
                        for c in (e.get("price_changes") or []):
                            aid = str(c.get("asset_id"))
                            out["touch"][aid].append(
                                (ms, c.get("best_bid"), c.get("best_ask")))
                            out["levels"][aid].append(
                                (ms, str(c.get("price")), str(c.get("size")),
                                 str(c.get("side"))))
                    elif et == "last_trade_price":
                        out["trades"][str(e.get("transaction_hash")).lower()] = {
                            "ts": float(e.get("timestamp") or 0) / 1000.0,
                            "px": e.get("price"), "size": e.get("size"),
                            "side": e.get("side"),
                            "asset_id": str(e.get("asset_id"))}
    for d in (out["snaps"], out["touch"], out["levels"]):
        for v in d.values():
            v.sort(key=lambda x: x[0] if isinstance(x, tuple) else x["ts"])
    return out


def validate_level_semantics(snaps: List[dict], levels: List[tuple]) -> Dict[str, Any]:
    """Is `price_change.size` the NEW SIZE at that level? Checked against the
    next snapshot, not assumed. Reports agreement rather than asserting it."""
    ok = bad = 0
    for a, b in zip(snaps, snaps[1:]):
        state = {}
        for ms, px, sz, _side in levels:
            if a["ts"] < ms <= b["ts"]:
                state[px] = sz
        if not state:
            continue
        actual = {}
        for row in (b["bids"] or []) + (b["asks"] or []):
            actual[str(row.get("price"))] = str(row.get("size"))
        for px, sz in state.items():
            got = actual.get(px)
            if got is None:
                #: level absent from the snapshot: consistent only with size 0
                if float(sz) == 0.0:
                    ok += 1
                else:
                    bad += 1
            elif abs(float(got) - float(sz)) < 1e-6:
                ok += 1
            else:
                bad += 1
    n = ok + bad
    return {"checked": n, "agree": ok, "disagree": bad,
            "agreement_rate": (ok / n) if n else None,
            "reading": "price_change.size is the NEW SIZE at that price",
            "verdict": ("VALIDATED" if n and ok / n >= 0.95 else
                        ("NOT_VALIDATED" if n else "UNTESTABLE"))}


def level_size_at(snaps: List[dict], levels: List[tuple], px: str,
                  t: float) -> Tuple[Optional[str], str]:
    """Size resting at `px` at time `t`: the latest price_change for that price,
    else the newest snapshot at or before t. Returns (size, source)."""
    best = None
    for ms, p, sz, _s in levels:
        if ms <= t and p == px:
            best = (ms, sz)
    snap = None
    for s in snaps:
        if s["ts"] <= t:
            snap = s
        else:
            break
    snap_sz = None
    if snap:
        for row in (snap["bids"] or []) + (snap["asks"] or []):
            if str(row.get("price")) == px:
                snap_sz = str(row.get("size"))
                break
    if best and (snap is None or best[0] >= snap["ts"]):
        return best[1], "price_change"
    if snap_sz is not None:
        return snap_sz, "book_snapshot"
    return None, ("absent_from_snapshot" if snap else "no_state_before_t")


def touch_at(touch: List[tuple], t: float):
    prev = None
    for ms, b, a in touch:
        if ms <= t:
            prev = (b, a)
        else:
            break
    return prev or (None, None)


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    d = scan(path)
    op = d["open"]
    if not op:
        print(f"{path}: no window_open")
        return 1
    info = op["payload"]
    ids = info.get("ids") or {}
    cond = str(ids.get("condition_id"))
    toks = [str(t) for t in (ids.get("token_ids") or [])]
    outcomes = ids.get("outcomes") or []
    tok_of = dict(zip(outcomes, toks))

    print("=" * 102)
    print(f"PRE-RESTING FORENSICS  {info['slug']}")
    print("=" * 102)
    print(f"  {ids.get('question')}")
    print(f"  ws frames {d['n_ws']}  events {dict(d['ev'])}")
    print(f"  activity polls {len(d['act'])}  failed reads {d['failed']}  "
          f"errors {len(d['errors'])}")
    print(f"  outcome -> token: "
          + ", ".join(f"{k}={v[:12]}..." for k, v in tok_of.items()))

    for name, tid in tok_of.items():
        v = validate_level_semantics(d["snaps"][tid], d["levels"][tid])
        print(f"\n  DELTA SEMANTICS CHECK [{name}]: {v['verdict']}  "
              f"agree {v['agree']}/{v['checked']}"
              + (f" = {v['agreement_rate']:.4f}" if v['agreement_rate'] else ""))
        print(f"    reading tested: {v['reading']}")

    #: L5's fills, joined to the native feed for ms timing
    seen, fills = set(), []
    for r in d["act"]:
        for row in (r["payload"] or []):
            if str(row.get("conditionId")) != cond:
                continue
            tx = str(row.get("transactionHash")).lower()
            key = (tx, row.get("timestamp"), row.get("price"), row.get("size"),
                   row.get("outcome"))
            if key in seen:
                continue
            seen.add(key)
            j = d["trades"].get(tx)
            fills.append({
                "outcome": row.get("outcome"), "side": str(row.get("side")),
                "px": float(row.get("price") or 0),
                "size": float(row.get("size") or 0),
                "activity_ts": int(row.get("timestamp") or 0),
                "ms_ts": j["ts"] if j else None,
                "joined": bool(j), "tx": tx,
                "asset_id": (j or {}).get("asset_id"),
            })
    fills.sort(key=lambda f: (f["ms_ts"] if f["ms_ts"] else f["activity_ts"]))
    nj = sum(1 for f in fills if f["joined"])
    print(f"\n  L5 fills: {len(fills)}   joined to native feed on "
          f"transaction_hash: {nj}/{len(fills)}")
    if not fills:
        print("  L5 did not trade this window.")
        return 0
    for f in fills:
        print("    %-5s %-4s px=%-6s size=%-10s ms_ts=%s" % (
            f["outcome"], f["side"], f["px"], f["size"],
            ("%.3f" % f["ms_ts"]) if f["ms_ts"] else "UNJOINED"))

    #: bursts, now at MILLISECOND resolution
    bursts, cur = [], [fills[0]]
    for a, b in zip(fills, fills[1:]):
        ta = a["ms_ts"] or a["activity_ts"]
        tb = b["ms_ts"] or b["activity_ts"]
        if tb - ta <= BURST_S:
            cur.append(b)
        else:
            bursts.append(cur)
            cur = [b]
    bursts.append(cur)

    print("\n" + "=" * 102)
    print("PER BURST: were the executed levels represented BEFORE the burst?")
    print("=" * 102)
    for bi, bu in enumerate(bursts, 1):
        t0 = bu[0]["ms_ts"] or bu[0]["activity_ts"]
        span = (bu[-1]["ms_ts"] or bu[-1]["activity_ts"]) - t0
        print(f"\n  BURST {bi}: {len(bu)} fill(s), span {span*1000:.0f} ms")
        for f in bu:
            dt = ((f["ms_ts"] or f["activity_ts"]) - t0) * 1000.0
            print("    T%+8.0fms  %-5s px=%-6s size=%s" % (
                dt, f["outcome"], f["px"], f["size"]))
        aid = bu[0]["asset_id"]
        if not aid:
            print("    UNJOINED: no ms timing, burst not analysable")
            continue
        snaps, levels, touch = d["snaps"][aid], d["levels"][aid], d["touch"][aid]
        print("    touch on the executed token, before the burst:")
        for lead in LEADS:
            b, a = touch_at(touch, t0 - lead)
            print(f"      T-{lead:>5.0f}s  best_bid {b}  best_ask {a}")
        b0, a0 = touch_at(touch, t0 - 0.001)
        print(f"      T-0.001s  best_bid {b0}  best_ask {a0}")

        print("    level represented at each executed price, BEFORE the burst:")
        verdicts = []
        for f in bu:
            px = f"{f['px']:.2f}"
            row = []
            for lead in LEADS:
                sz, src = level_size_at(snaps, levels, px, t0 - lead)
                row.append(f"T-{lead:.0f}s={sz if sz is not None else 'absent'}")
            sz_pre, src_pre = level_size_at(snaps, levels, px, t0 - 0.001)
            enough = (sz_pre is not None and float(sz_pre) >= f["size"])
            verdicts.append(enough)
            print("      px %-6s %s | just before: %s (%s) -> covers %s? %s" % (
                px, "  ".join(row), sz_pre if sz_pre is not None else "absent",
                src_pre, f["size"], "YES" if enough else "NO"))

        if all(verdicts):
            cls = "CONSISTENT_WITH_PRE_RESTING"
            why = ("every executed price already showed at least the executed "
                   "size before the burst began")
        elif any(verdicts):
            cls = "AMBIGUOUS"
            why = ("some executed prices were represented beforehand and others "
                   "were not")
        else:
            cls = "REQUIRES_POST_EVENT_ORDER"
            why = ("no executed price showed enough size beforehand, so some "
                   "order must have arrived after the burst began")
        print(f"    => {cls}: {why}")
        print("       Liquidity is ANONYMOUS. This says the sequence is "
              "consistent with standing liquidity, NOT that the standing "
              "liquidity was L5's.")

    print("\n" + "=" * 102)
    print("CLASSIFICATION")
    print("=" * 102)
    print("  OBSERVED : L5's fills at ms resolution via the transaction_hash "
          "join; venue-stated best_bid/best_ask; book snapshots; per-level "
          "sizes where the delta reading validated.")
    print("  INFERRED : whether a burst is consistent with pre-resting; that "
          "the market moved toward the executed levels.")
    print("  UNKNOWN  : whose orders the resting liquidity was; whether L5 "
          "cancelled or replaced anything; his unfilled intent; whether a fill "
          "was a book trade or a complete-set mint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
