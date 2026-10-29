#!/usr/bin/env python3
"""Would a larger clip earn more matched profit, or just strand more?

THE DECISION. Matched surplus is LINEAR in contracts at a fixed pair cost:
$0.04697 per matched contract over C8's 21.68. So if fill probability were
independent of clip size, doubling the clip would double matched surplus --
and double stranded losses too, leaving the RATIO unchanged and the variance
higher. The question is therefore not "does size scale profit" (it does,
mechanically) but:

  1. does fill probability FALL with size, and
  2. does the cash envelope permit it at all.

Both are answered from the venue-confirmed record: every transmitted order
in C6/C7/C8 with its quantity and its terminal state.

LIMITS ARE NOT TOUCHED. This reads tapes. It changes no limit and proposes
no change by itself.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]


def orders():
    """(qty, filled, campaign) for every transmitted order."""
    out = []
    for camp in ("campaign6", "campaign7"):
        base = ROOT / "analysis" / "l5" / camp
        subs = {}
        for line in (base / "tape_submits.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                subs[r["coid"]] = r
        term = {}
        for line in (base / "tape_terminals.jsonl").read_text().splitlines():
            if line.strip():
                t = json.loads(line)
                term[t["coid"]] = t
        for coid, s in subs.items():
            t = term.get(coid)
            if t is None:
                continue
            out.append((D(str(s["qty"])),
                        "FILLED" in str(t.get("state") or ""), camp))
    for r in json.loads((ROOT / "analysis/l5/campaign8/c8_joined.json"
                         ).read_text()):
        out.append((D(str(r["qty"])), r["state"] == "FILLED", "campaign8"))
    return out


def main() -> int:
    o = orders()
    qs = sorted(q for q, _, _ in o)
    n = len(qs)
    # terciles on quantity -- quartiles on 80-odd orders put 20 in a bucket
    # and the fill rate's standard error is then 0.11, wider than any effect
    # worth acting on.
    lo, hi = qs[n // 3], qs[2 * n // 3]
    buckets = {"small": [], "mid": [], "large": []}
    for q, f, _ in o:
        k = "small" if q <= lo else ("mid" if q <= hi else "large")
        buckets[k].append(f)

    rows = {}
    for k, v in buckets.items():
        rows[k] = {"n": len(v),
                   "fills": sum(1 for x in v if x),
                   "fill_rate": round(sum(1 for x in v if x) / len(v), 4)}

    # the cash constraint, from the worst simultaneous committed cash C8
    # actually reached. This is the gate at $12, not the ungated high-water
    # mark of $22.2436.
    c8_worst = D("9.0833")
    envelope = D("12")
    headroom = envelope / c8_worst

    print(json.dumps({
        "orders_measured": n,
        "quantity_terciles": {"low_break": str(lo), "high_break": str(hi),
                              "min": str(qs[0]), "max": str(qs[-1])},
        "fill_rate_by_clip_size": rows,
        "cash_constraint": {
            "c8_worst_simultaneous_committed_usd": str(c8_worst),
            "approved_envelope_usd": str(envelope),
            "max_clip_multiple_inside_envelope": str(round(headroom, 3)),
            "note": ("the $12 gate is max_cash_deployed_usd, the measured "
                     "simultaneous commitment -- not the $22.2436 worst-case "
                     "high-water mark, which is budget accounting and not a "
                     "gate"),
        },
        "matched_surplus_per_contract_usd": "0.04697",
        "labels": ("fill_rate is over venue-confirmed terminals; a CANCELED "
                   "order is a non-fill, which is the conservative reading "
                   "because some cancels were pulled before the market "
                   "reached them"),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
