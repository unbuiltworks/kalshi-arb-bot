#!/usr/bin/env python3
"""Coverage matrix of every existing Gemini BTC5m/BTC15m dataset.

Answers one question per dataset: can it extend the prospective two-sided
sample UNDER THE EXACT SAME conservative fill rules? The rules are not
negotiable to make old data usable, so a dataset qualifies only if it has
ALL of:

  LADDER   book depth beyond the touch, as a ladder or as deltas we can
           rebuild (BBO alone cannot establish queue-ahead)
  TRADES   executions with price, size and aggressor side (the only thing
           allowed to consume queue)
  SYNC     a timestamp on both, from the same clock or with a measurable
           offset, so a trade can bind to the book state strictly before it

OWN FILLS and SETTLEMENT are recorded where present but are not required:
the shadow simulates its own fills and the policy closes before settlement.
"""
from __future__ import annotations
import json, gzip, os, sys, glob, collections

CANDIDATES = [
    "analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz",
    "analysis/gemini/continuous_v1/continuous_v1.jsonl",
    "analysis/gemini/btc5m_v2/continuous_v1.jsonl",
    "analysis/gemini/btc15m_v1/continuous_v1.jsonl",
    "analysis/gemini/p7_bulk_tape.json",
    "analysis/gemini/tape_replay.json",
    "analysis/gemini/window_volume.json",
    "analysis/gemini/p9d_events/btc_arms_events.jsonl",
    "analysis/gemini/cohort_live_status.json.jsonl",
]
CANDIDATES += sorted(glob.glob("analysis/gemini/p10_capture/*.jsonl"))
CANDIDATES += sorted(glob.glob("analysis/gemini/cohort_run_*.json"))[:2]

LADDER_HINT = ("bids", "asks", "changes", "book", "levels", "depth", "ladder")
TRADE_HINT = ("trades", "tid", "trade_id")
OWNFILL_KEYS = {"client_order_id", "order_id", "fee_amount"}


def rows(path, cap=60000):
    if path.endswith(".gz"):
        op = gzip.open(path, "rt")
    else:
        op = open(path, errors="ignore")
    with op as fh:
        if path.endswith((".jsonl", ".jsonl.gz")) or path.endswith(".json.jsonl"):
            for i, line in enumerate(fh):
                if i >= cap:
                    return
                if line.strip():
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        else:
            try:
                d = json.load(fh)
            except Exception:
                return
            yield d


def probe(path):
    n = 0
    keys = collections.Counter()
    ts = []
    has_ladder = has_trades = has_ownfill = has_settle = False
    depth_seen = 0
    syms = set()

    def walk(o, d=0):
        nonlocal has_ladder, has_trades, has_ownfill, has_settle, depth_seen
        if d > 6:
            return
        if isinstance(o, dict):
            ks = set(o.keys())
            for k in ks:
                keys[k] += 1
            if OWNFILL_KEYS <= ks:
                has_ownfill = True
            if "resolutionSide" in ks or "settlementValue" in ks:
                has_settle = True
            for k in ("bids", "asks", "changes"):
                v = o.get(k)
                if isinstance(v, list) and v:
                    has_ladder = True
                    depth_seen = max(depth_seen, len(v))
            if isinstance(o.get("trades"), list) and o["trades"]:
                has_trades = True
            if {"tid", "price"} <= ks and ("amount" in ks or "qty" in ks
                                           or "quantity" in ks):
                has_trades = True
            s = o.get("symbol") or o.get("market")
            if isinstance(s, str) and s.startswith("GEMI-"):
                syms.add(s)
            for k in ("t", "ts", "_ts", "utc", "timestamp", "timestampms",
                      "utc_ms", "t_ns", "event_ns"):
                if k in o and o[k]:
                    ts.append(str(o[k])[:24])
            for v in o.values():
                walk(v, d + 1)
        elif isinstance(o, list):
            for v in o[:400]:
                walk(v, d + 1)

    for r in rows(path):
        n += 1
        walk(r)
    return {"path": path, "rows": n, "symbols": len(syms),
            "ladder": has_ladder, "max_level_count": depth_seen,
            "trades": has_trades, "own_fills": has_ownfill,
            "settlement": has_settle,
            "ts_min": min(ts) if ts else None, "ts_max": max(ts) if ts else None,
            "top_keys": [k for k, _ in keys.most_common(10)]}


def main():
    out = []
    for p in CANDIDATES:
        if not os.path.exists(p):
            continue
        try:
            out.append(probe(p))
        except Exception as e:
            out.append({"path": p, "error": str(e)[:120]})
    print(f"{'dataset':52s} {'rows':>7s} {'sym':>4s} {'ladder':>7s} {'lvls':>5s} "
          f"{'trades':>7s} {'ownfill':>8s} {'settle':>7s}")
    for r in out:
        if "error" in r:
            print(f"{r['path'][-52:]:52s}  ERROR {r['error'][:40]}")
            continue
        print(f"{r['path'][-52:]:52s} {r['rows']:>7d} {r['symbols']:>4d} "
              f"{str(r['ladder']):>7s} {r['max_level_count']:>5d} "
              f"{str(r['trades']):>7s} {str(r['own_fills']):>8s} "
              f"{str(r['settlement']):>7s}")
    json.dump(out, open("analysis/gemini/DATASET_AUDIT.json", "w"), indent=2)
    print("\n-> analysis/gemini/DATASET_AUDIT.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
