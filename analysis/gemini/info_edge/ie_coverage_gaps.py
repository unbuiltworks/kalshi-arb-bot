"""Collection-continuity monitor. Makes lost coverage VISIBLE and counted.

The risk this exists for: a runner dies, collection silently stops, and the
loss is only noticed hours later. This quantifies gaps instead of trusting
that a process stayed up.

Reports:
  * per-tape span and the gap between consecutive tapes
  * Coinbase stream continuity (it runs continuously, so any gap in it is a
    collection outage, not a market feature)
  * how many BTC5m windows fall inside each outage, i.e. windows LOST
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

GAP_ALERT_S = 90.0        # a gap longer than this costs window coverage


def u(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def main(outdir="data/gemini_infoedge"):
    tapes = []
    cb = []
    for f in sorted(Path(outdir).glob("ie_*.jsonl")):
        lo = hi = None
        n = 0
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("t_ns")
            if not t:
                continue
            t /= 1e9
            lo = t if lo is None else min(lo, t)
            hi = t if hi is None else max(hi, t)
            n += 1
            if r.get("kind") == "cb_match":
                cb.append(t)
        if lo:
            tapes.append((f.name, lo, hi, n))
    cb.sort()

    print("=" * 78)
    print("COLLECTION CONTINUITY")
    print("=" * 78)
    print(f"{'tape':<34s} {'from':>9s} {'to':>9s} {'min':>7s} {'rows':>8s}")
    prev_hi = None
    outages = []
    for name, lo, hi, n in tapes:
        if prev_hi is not None and lo - prev_hi > 5:
            outages.append((prev_hi, lo))
        prev_hi = hi
        print(f"{name:<34s} {u(lo):>9s} {u(hi):>9s} "
              f"{(hi-lo)/60:7.1f} {n:8d}")

    print()
    print("--- Coinbase stream gaps (it runs continuously; a gap = outage) ---")
    gaps = [(a, b) for a, b in zip(cb, cb[1:]) if b - a > GAP_ALERT_S]
    total_lost = sum(b - a for a, b in gaps)
    if gaps:
        for a, b in gaps[-10:]:
            print(f"  {u(a)} -> {u(b)}   lost {b-a:7.1f} s   "
                  f"(~{(b-a)/300:.1f} windows)")
        print(f"  outages > {int(GAP_ALERT_S)} s : {len(gaps)}")
        print(f"  total lost time    : {total_lost/60:.1f} min "
              f"(~{total_lost/300:.1f} BTC5m windows)")
    else:
        print(f"  none over {int(GAP_ALERT_S)} s -- collection continuous")

    if cb:
        span = cb[-1] - cb[0]
        duty = (span - total_lost) / span * 100 if span else 0
        print()
        print(f"wall span      : {span/3600:.2f} h")
        print(f"DUTY CYCLE     : {duty:.1f}%  (share of wall time collecting)")
        print(f"windows elapsed: ~{span/300:.0f}   usable-rate implies "
              f"~{span/300*duty/100:.0f} collectable")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(*(sys.argv[1:2] or ["data/gemini_infoedge"])))
