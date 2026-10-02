#!/usr/bin/env python3
"""Convert saved Railway log dumps from the btc-ref collector into local raw rows.

Same transport and the same discipline as scripts/pmus_railway_log_to_tape.py: the
service echoes BTC_REF_ROWS lines, each stating `emitted` and `pending`.

CORRECTION, found on the first real harvest: summing `pending` does NOT measure
loss. `pending` is the remainder of the SAME batch still to go out in the next
line, so a multi-chunk emission reports a positive pending that is then fully
delivered -- the first harvest summed 320 that way with nothing missing. The
truncation test is SEQUENCE CONTINUITY: `seq` increments once per emitted line,
so a hole in the seq range is a lost line. Both are reported below, with the
pending sum labelled for what it is. Dedupe is by (kind, ev, t_ns, tid/bid, px/ask); nothing is
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
    seqs = []
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
            if o.get("seq") is not None:
                seqs.append(int(o["seq"]))
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
    gaps = []
    if seqs:
        have = set(seqs)
        gaps = [s for s in range(min(seqs), max(seqs) + 1) if s not in have]
    print(json.dumps({"new_rows": len(rows), "sessions": sessions,
                      "seq_range": [min(seqs), max(seqs)] if seqs else None,
                      "seq_lines": len(seqs), "seq_missing": len(gaps),
                      "lost_lines": gaps[:20],
                      "pending_sum_NOT_loss": pending, "out": OUT}))


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(glob.glob("/tmp/btcref_logs/*.json")))
