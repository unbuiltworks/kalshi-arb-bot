#!/usr/bin/env python3
"""Run the continuous shadow policy evaluation over emitted recorder evidence.

Reads `PMUS_LADDER_ROWS` / `PMUS_TRADE_ROWS` lines (the rows the read-only
recorder already prints), evaluates every eligible window, and APPENDS one
record per window to a JSONL store, keyed by slug so a re-run is idempotent
and never double-counts.

  python3 scripts/l5_shadow_policy_run.py <evidence.json|-> [--store PATH]

Evidence may be a Railway log dump (`{"deploy": [{"message": ...}]}`) or raw
lines. SHADOW ONLY: this submits nothing, reads no credential and touches no
ledger. Every fill in the output is MODELLED and labelled as such.
"""
import argparse, collections, json, os, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_shadow_policy_eval as E          # noqa: E402

DEFAULT_STORE = "data/l5/shadow/policy_eval.jsonl"
#: the limits the probe is ALREADY authorized for. Not widened here.
TICK = D("0.01"); MIN_QTY = D("0.01"); DEPTH_TICKS = 1
CASH_ENVELOPE = D("2.00"); WORST_CASE_CEILING = D("2.00")


def harvest(text):
    lad = collections.defaultdict(list)
    trd = collections.defaultdict(list)
    msgs = []
    try:
        doc = json.loads(text)
        msgs = [e.get("message", "") for e in (doc.get("deploy") or [])]
    except Exception:
        msgs = text.splitlines()
    for m in msgs:
        tag, _, rest = m.partition(" ")
        if tag not in ("PMUS_LADDER_ROWS", "PMUS_TRADE_ROWS"):
            continue
        try:
            j = json.loads(rest)
        except Exception:
            continue
        tgt = lad if tag == "PMUS_LADDER_ROWS" else trd
        for slug, rows in (j.get("markets") or {}).items():
            tgt[slug].extend(rows)

    def ded(rows, key):
        seen, out = set(), []
        for r in rows:
            k = tuple(str(r.get(x)) for x in key)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out
    return ({k: ded(v, ("t",)) for k, v in lad.items()},
            {k: ded(v, ("t", "px", "qty", "trade_time")) for k, v in trd.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence", nargs="+")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--settlement", default=None,
                    help="JSON {slug: yes_value} from VENUE_SETTLEMENT_PX only")
    a = ap.parse_args()
    lad = collections.defaultdict(list); trd = collections.defaultdict(list)
    for src in a.evidence:
        text = sys.stdin.read() if src == "-" else open(src).read()
        l2, t2 = harvest(text)
        for k, v in l2.items():
            lad[k].extend(v)
        for k, v in t2.items():
            trd[k].extend(v)

    def ded2(rows, key):
        seen, out = set(), []
        for r in rows:
            k = tuple(str(r.get(x)) for x in key)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out
    lad = {k: ded2(v, ("t",)) for k, v in lad.items()}
    trd = {k: ded2(v, ("t", "px", "qty", "trade_time")) for k, v in trd.items()}
    settle = json.load(open(a.settlement)) if a.settlement else {}

    have = {}
    if os.path.exists(a.store):
        for line in open(a.store):
            try:
                r = json.loads(line)
            except Exception:
                continue
            have[r.get("market")] = r
    new = []
    for slug in sorted(lad):
        if slug in have and have[slug].get("ok"):
            continue                       # already scored; idempotent re-run
        rec = E.evaluate_window(
            slug, lad[slug], trd.get(slug, []),
            tick=TICK, min_qty=MIN_QTY, depth_ticks=DEPTH_TICKS,
            cash_envelope=CASH_ENVELOPE,
            max_worst_case_loss=WORST_CASE_CEILING,
            yes_value=settle.get(slug))
        have[slug] = rec
        new.append(rec)
    os.makedirs(os.path.dirname(a.store) or ".", exist_ok=True)
    with open(a.store, "w") as fh:
        for slug in sorted(have):
            fh.write(json.dumps(have[slug], default=str) + "\n")
    print("scored %d new window(s); store holds %d" % (len(new), len(have)))
    for r in new:
        print("  %-46s %s" % (r.get("market"),
                              r.get("outcome") or r.get("reason")))
    print(json.dumps(E.summarize(list(have.values())), indent=2, default=str))


if __name__ == "__main__":
    main()
