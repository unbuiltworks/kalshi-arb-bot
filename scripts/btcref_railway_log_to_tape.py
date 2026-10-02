#!/usr/bin/env python3
"""Convert saved Railway log dumps from the btc-ref collector into local raw rows.

Same transport and the same discipline as scripts/pmus_railway_log_to_tape.py: the
service echoes BTC_REF_ROWS lines, each stating `emitted` and `pending`, and this
reader SUMS the pending counts so a truncated emission is reported rather than
silently absorbed. Dedupe is by (kind, ev, t_ns, tid/bid, px/ask); nothing is
interpolated or backfilled. Session rows are kept, because a restart must stay
visible in the data.

The volume copy under /state/btc-ref remains the durable store; this path exists
only because the volume is not readable from here.
"""
from __future__ import annotations
import glob, json, os, sys

OUT = "data/btc_ref/btcref_harvested.jsonl"


def main(paths):
    seen, rows, pending, sessions = set(), [], 0, 0
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                r = json.loads(line)
            except Exception:
                continue
            seen.add((r.get("kind"), r.get("ev"), r.get("t_ns"),
                      r.get("tid") or r.get("bid"), r.get("px") or r.get("ask")))
    for p in paths:
        d = json.load(open(p))
        for r in d.get("deploy") or []:
            m = r.get("message", "")
            if "BTC_REF_SESSION" in m:
                try:
                    rows.append(json.loads(m.split("BTC_REF_SESSION", 1)[1].strip()))
                    sessions += 1
                except Exception:
                    pass
                continue
            if "BTC_REF_ROWS" not in m:
                continue
            try:
                o = json.loads(m.split("BTC_REF_ROWS", 1)[1].strip())
            except Exception:
                continue
            pending += int(o.get("pending") or 0)
            for x in o.get("rows") or []:
                k = (x.get("kind"), x.get("ev"), x.get("t_ns"),
                     x.get("tid") or x.get("bid"), x.get("px") or x.get("ask"))
                if k in seen:
                    continue
                seen.add(k); rows.append(x)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a") as fh:
        for x in rows:
            fh.write(json.dumps(x, separators=(",", ":")) + "\n")
    print(json.dumps({"new_rows": len(rows), "sessions": sessions,
                      "pending_reported_by_service": pending, "out": OUT}))


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(glob.glob("/tmp/btcref_logs/*.json")))
