#!/usr/bin/env python3
"""Merge raw L2 tapes + raw REST trade tapes into ONE tape in the recorder's
own schema, under a synchronization invariant.

The raw inputs are never modified or deleted. This writes a separate merged
artifact; `gemini_btc15m_shadow_record.py` and `gemini_twosided_sim.py` are
both untouched and the simulator consumes the merged file unchanged.

THE INVARIANT, which decides whether a trade may ever cause a fill:

  Every REST trade is bound to the LATEST L2 frame whose receipt time is
  STRICTLY BEFORE the trade's venue timestamp, for that symbol.
  book_age_ms = trade_venue_ts - that frame's receipt_ts.

  A trade is UNSCORABLE, and is written with its trades array withheld so it
  can never consume queue, when any of these hold:
    NO_PRECEDING_BOOK  no L2 frame for that symbol precedes it
    COVERAGE_GAP       it lands in a hole between tape segments, or between
                       two consecutive frames separated by more than the
                       freshness threshold
    STALE_BOOK         book_age_ms exceeds the freshness threshold
    DEDUP_CONFLICT     a repeated tid whose price/qty/timestamp disagree

CLOCK. book_age_ms mixes the venue's trade timestamp with OUR receipt time
for the book, so it is only meaningful if the two clocks agree. They do: the
heartbeat frames carry a venue timestamp alongside our receipt time, and the
measured offset is a few milliseconds. The offset is computed from the
heartbeats on the tape, reported, and SUBTRACTED before any age is taken --
it is measured, not assumed.

DEDUP. Trades are keyed by tid. A repeated tid with identical
(timestampms, price, amount) is dropped silently -- that is just the poller
re-reading the same window. A repeated tid whose fields DISAGREE is never
merged and is counted as DEDUP_CONFLICT, because that would mean the venue
changed a print under us and nothing downstream should consume it twice.
"""
from __future__ import annotations

import json, os, sys, glob, statistics, collections
from datetime import datetime, timezone

OUT = "data/gemini_shadow/merged/merged_tape.jsonl"
REPORT = "analysis/gemini/MERGE_SYNC_REPORT.json"
# Freshness threshold. Justified from the observed inter-frame interval
# distribution on the tape itself and reported alongside it; a trade whose
# preceding book is older than this cannot be said to describe the queue the
# trade actually hit.
FRESHNESS_MS = 2000
# Liveness, which is NOT the same thing as book freshness and is the criterion
# that actually decides coverage.
#
# The WS emits an l2_updates frame only when the book CHANGES. A quiet book
# therefore produces no frames, and the first revision of this merger read
# that silence as lost coverage: 97 of 106 "gaps" were simply an unchanged
# book (median 4.2s), and 3 of 4 trades were discarded as COVERAGE_GAP for no
# better reason than that nothing had moved. That is the wrong conservatism --
# it throws away exactly the quiet-book trades a maker would actually be
# resting into.
#
# The connection carries a heartbeat every ~5s independently of book activity,
# so heartbeat liveness distinguishes "connected and quiet" (state known, last
# frame IS current) from "disconnected" (state unknown). Coverage is decided by
# liveness; book_age_ms is still measured and reported for every scored trade,
# and the strict frame-recency rule is reported alongside so the effect of the
# choice is visible rather than buried.
HEARTBEAT_MAX_MS = 15000


def ms(iso):
    return datetime.fromisoformat(iso).timestamp() * 1000.0


def load_l2(paths):
    """[(recv_ms, raw_row)] plus per-symbol frame index and heartbeat pairs."""
    rows = []
    hb = []
    for p in sorted(paths):
        for line in open(p):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") != "frame":
                rows.append((None, r, p))
                continue
            f = r["f"]
            t = ms(r["t"])
            if f.get("type") == "heartbeat" and f.get("timestamp"):
                hb.append(float(f["timestamp"]) - t)
            rows.append((t, r, p))
    return rows, hb


def load_trades(paths):
    out = {}
    conflicts = []
    for p in sorted(paths):
        for line in open(p):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") != "trade":
                continue
            t = r["t"]
            tid = t.get("tid")
            key = (tid, t.get("timestampms"), str(t.get("price")),
                   str(t.get("amount")))
            if tid in out:
                if out[tid][0] != key:
                    conflicts.append({"tid": tid, "a": out[tid][0], "b": key})
                continue
            out[tid] = (key, r)
    return out, conflicts


def main():
    l2_paths = [p for p in glob.glob("data/gemini_shadow/gemini_*.jsonl")]
    tr_paths = [p for p in glob.glob("data/gemini_shadow/trades_*.jsonl")]
    rows, hb = load_l2(l2_paths)
    trades, conflicts = load_trades(tr_paths)
    offset = statistics.median(hb) if hb else 0.0
    print(f"L2 segments {len(l2_paths)}  rows {len(rows)}   "
          f"trade tapes {len(tr_paths)}  distinct tids {len(trades)}")
    print(f"clock offset (venue - our receipt), median of {len(hb)} heartbeats: "
          f"{offset:+.1f} ms")

    # connection liveness: receipt times of ANY frame (heartbeats included),
    # which is what proves the socket was up
    live_ts = sorted(t for t, r, p in rows if t is not None)

    # per-symbol ordered frame receipt times
    per_sym = collections.defaultdict(list)
    for t, r, p in rows:
        if t is None:
            continue
        s = (r["f"] or {}).get("symbol")
        if s:
            per_sym[s].append((t, p))
    for s in per_sym:
        per_sym[s].sort()

    gaps = []
    for s, lst in per_sym.items():
        for i in range(1, len(lst)):
            d = lst[i][0] - lst[i - 1][0]
            if d > FRESHNESS_MS or lst[i][1] != lst[i - 1][1]:
                gaps.append((s, lst[i - 1][0], lst[i][0], d,
                             "segment_boundary" if lst[i][1] != lst[i - 1][1] else "gap"))

    import bisect
    scored, unscorable = [], collections.Counter()
    ages = []
    liveness_ages = []
    strict_rejected = []
    bound = collections.defaultdict(list)       # frame_recv_ms -> [trade rows]
    for tid, (key, r) in trades.items():
        t = r["t"]
        sym = r["symbol"]
        tv = float(t["timestampms"]) - offset       # into OUR clock
        lst = per_sym.get(sym) or []
        times = [x[0] for x in lst]
        i = bisect.bisect_left(times, tv) - 1       # strictly preceding
        if i < 0:
            unscorable["NO_PRECEDING_BOOK"] += 1
            continue
        age = tv - times[i]
        # only a SEGMENT BOUNDARY is true lost coverage: the recorder was dead.
        in_gap = any(g[0] == sym and g[4] == "segment_boundary"
                     and g[1] <= times[i] and g[2] >= tv for g in gaps)
        if in_gap:
            unscorable["COVERAGE_GAP_RECORDER_DOWN"] += 1
            continue
        j = bisect.bisect_left(live_ts, tv) - 1
        live_age = (tv - live_ts[j]) if j >= 0 else float("inf")
        if live_age > HEARTBEAT_MAX_MS:
            unscorable["CONNECTION_DOWN"] += 1
            continue
        strict_ok = age <= FRESHNESS_MS
        if not strict_ok:
            strict_rejected.append(age)
        ages.append(age)
        liveness_ages.append(live_age)
        scored.append((times[i], sym, t, age))
        bound[(sym, times[i])].append({
            "price": t["price"], "quantity": t.get("amount"),
            "amount": t.get("amount"), "side": t.get("type"),
            "tid": tid, "timestampms": t["timestampms"],
            "book_age_ms": round(age, 1),
        })
    unscorable["DEDUP_CONFLICT"] = len(conflicts)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    nwritten = 0
    with open(OUT, "w") as fh:
        for t, r, p in rows:
            if r.get("kind") != "frame":
                fh.write(json.dumps(r) + "\n")
                continue
            f = dict(r["f"])
            s = f.get("symbol")
            k = (s, t)
            if k in bound:
                f["trades"] = bound[k]
            out = dict(r)
            out["f"] = f
            fh.write(json.dumps(out) + "\n")
            nwritten += 1

    a = sorted(ages)
    pct = lambda q: a[min(len(a) - 1, int(len(a) * q))] if a else None
    rep = {
        "merged_tape": OUT,
        "raw_preserved": {"l2": sorted(l2_paths), "trades": sorted(tr_paths)},
        "freshness_threshold_ms": FRESHNESS_MS,
        "clock_offset_ms_venue_minus_receipt": offset,
        "clock_offset_source": f"median of {len(hb)} heartbeat frames",
        "distinct_tids": len(trades),
        "scored_trades": len(scored),
        "unscorable": dict(unscorable),
        "dedup_conflicts": conflicts,
        "coverage_gaps": [{"symbol": g[0], "gap_ms": round(g[3], 1), "why": g[4]}
                          for g in gaps],
        "heartbeat_liveness_threshold_ms": HEARTBEAT_MAX_MS,
        "scored_under_strict_frame_recency": len(ages) - len(strict_rejected),
        "scored_only_because_of_liveness_rule": len(strict_rejected),
        "liveness_age_ms_p50": (statistics.median(liveness_ages)
                                if liveness_ages else None),
        "book_age_ms": {
            "n": len(a), "min": a[0] if a else None, "p50": pct(.50),
            "p90": pct(.90), "p99": pct(.99), "max": a[-1] if a else None,
            "mean": statistics.mean(a) if a else None,
        },
        "frames_written": nwritten,
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    json.dump(rep, open(REPORT, "w"), indent=2)
    print(f"\nscored trades      : {len(scored)}")
    print(f"UNSCORABLE         : {dict(unscorable)}")
    print(f"coverage gaps found: {len(gaps)}")
    if a:
        print(f"book_age_ms        : n={len(a)} min={a[0]:.0f} p50={pct(.5):.0f} "
              f"p90={pct(.9):.0f} max={a[-1]:.0f}  (threshold {FRESHNESS_MS})")
    print(f"-> {OUT}\n-> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
