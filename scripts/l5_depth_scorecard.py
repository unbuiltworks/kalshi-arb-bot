#!/usr/bin/env python3
"""Durable, settlement-correct scorecard for the forward depth comparison.

READ-ONLY with respect to the venue, the recorder and the strategy. It parses
PMUS_SHADOW_SETTLED / PMUS_SHADOW_POLICY log records, appends the comparable
ones to a durable JSONL ledger, and prints the scorecard. It places no order,
changes no limit and contacts no venue.

A window enters the ledger ONLY when every compared arm is settlement-resolved
(`priced: true`) in the same settled record. Everything else is counted in its
own bucket and named, never silently dropped -- in particular every window
settled before the section 48 fix carries no variants block at all and is
therefore reported as PRE_FIX_NO_VARIANTS rather than treated as missing.

Usage:
  l5_depth_scorecard.py --ledger data/l5/forward/depth_scorecard.jsonl \
      [--ingest logs1.json logs2.json ...] [--report]
"""
from __future__ import annotations
import argparse, json, os, statistics as st, sys
from decimal import Decimal as D

ARMS = ("B25", "B25D2", "B25D3")
CONTROL = "B25"
TARGET = D(5)


def _records(paths):
    """Yield (timestamp, kind, payload) for every shadow record in the files."""
    for p in paths:
        try:
            blob = json.load(open(p))
        except Exception as exc:                                   # noqa: BLE001
            print("  ingest: cannot read %s (%s)" % (p, exc), file=sys.stderr)
            continue
        for e in blob.get("deploy") or []:
            msg = str(e.get("message") or "")
            # a log line can carry two records concatenated by the transport
            for marker in ("PMUS_SHADOW_SETTLED", "PMUS_SHADOW_POLICY"):
                i = msg.find(marker + " {")
                if i < 0:
                    continue
                frag = msg[i + len(marker) + 1:]
                dec = json.JSONDecoder()
                try:
                    obj, _n = dec.raw_decode(frag)
                except Exception:                                  # noqa: BLE001
                    continue
                yield e.get("timestamp"), marker, obj


def ingest(paths, ledger_path):
    """Append newly comparable windows. Idempotent on market slug."""
    seen, rows = set(), []
    if os.path.exists(ledger_path):
        for line in open(ledger_path):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            seen.add(r["market"]); rows.append(r)
    added, buckets = 0, {"PRE_FIX_NO_VARIANTS": [], "ARM_UNPRICED": [],
                         "NOT_RECONCILED": [], "DUPLICATE": []}
    for ts, kind, obj in _records(paths):
        if kind != "PMUS_SHADOW_SETTLED":
            continue
        for w in obj.get("markets") or []:
            mk = w.get("market")
            if not mk:
                continue
            if not w.get("reconciled"):
                buckets["NOT_RECONCILED"].append(mk); continue
            v = w.get("variants") or {}
            if not v:
                buckets["PRE_FIX_NO_VARIANTS"].append(mk); continue
            if not all((v.get(a) or {}).get("priced") for a in ARMS):
                buckets["ARM_UNPRICED"].append(mk); continue
            if mk in seen:
                buckets["DUPLICATE"].append(mk); continue
            rec = {"market": mk, "settled_at": ts, "basis": w.get("basis"),
                   "attempts": w.get("attempts"),
                   "arms": {a: {k: (v[a] or {}).get(k) for k in (
                       "net_usd", "matched_pair_net_usd",
                       "residual_settlement_net_usd", "matched_qty",
                       "residual_qty", "split_basis")} for a in ARMS}}
            with open(ledger_path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            seen.add(mk); rows.append(rec); added += 1
    return added, rows, buckets


def _f(x):
    return 0.0 if x is None else float(x)


def report(rows, buckets=None):
    n = len(rows)
    print("=== L5 FORWARD DEPTH SCORECARD (settlement-resolved only) ===")
    print("  fully settled comparable windows: %d" % n)
    if buckets:
        for k, v in buckets.items():
            if v:
                print("  %-22s %d  %s" % (k, len(v),
                      "" if len(v) > 4 else [m[-5:] for m in v]))
    if not n:
        print("\n  No comparable windows yet. No arm comparison is computed.")
        return
    cols = {a: [_f(r["arms"][a]["net_usd"]) for r in rows] for a in ARMS}
    mat = {a: [_f(r["arms"][a]["matched_pair_net_usd"]) for r in rows]
           for a in ARMS}
    res = {a: [_f(r["arms"][a]["residual_settlement_net_usd"]) for r in rows]
           for a in ARMS}
    print("\n%-8s %9s %9s %9s %9s %8s %9s" % (
        "arm", "matched$", "resid$", "TOTAL$", "mean/win", ">=$5", "worst win"))
    for a in ARMS:
        print("%-8s %9.2f %9.2f %9.2f %9.3f %7d%% %9.2f" % (
            a, sum(mat[a]), sum(res[a]), sum(cols[a]), st.mean(cols[a]),
            round(100 * sum(1 for x in cols[a] if x >= float(TARGET)) / n),
            min(cols[a])))
    # worst cumulative drawdown of the running total, per arm
    print("\n  worst drawdown of the running total:")
    for a in ARMS:
        run = peak = dd = 0.0
        for x in cols[a]:
            run += x; peak = max(peak, run); dd = min(dd, run - peak)
        print("    %-7s $%+0.2f" % (a, dd))
    print("\n  paired difference vs %s, SPLIT into execution vs direction:" % CONTROL)
    print("    %-8s %12s %12s %12s %s" % (
        "arm", "total", "from matched", "from residual", "t(total)"))
    for a in ARMS:
        if a == CONTROL:
            continue
        dt = [y - x for x, y in zip(cols[CONTROL], cols[a])]
        dm = [y - x for x, y in zip(mat[CONTROL], mat[a])]
        dr = [y - x for x, y in zip(res[CONTROL], res[a])]
        t = ("%+0.2f" % (st.mean(dt) / (st.stdev(dt) / len(dt) ** 0.5))
             if len(dt) > 1 and st.stdev(dt) else "n/a")
        print("    %-8s %12.3f %12.3f %12.3f %s" % (
            a, st.mean(dt), st.mean(dm), st.mean(dr), t))
    print("\n  MATCHED net is execution economics; RESIDUAL net is the")
    print("  directional settlement. Only the matched column is evidence about")
    print("  depth; the residual column is the coin the window happened to")
    print("  flip. MODELLED fills throughout -- no figure here is realized.")
    for mark in (50, 100):
        if n < mark:
            print("  %d/%d windows toward the %d-window assessment" % (n, mark, mark))
            break


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--ingest", nargs="*", default=[])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    added, rows, buckets = ingest(a.ingest, a.ledger) if a.ingest else (
        0, [json.loads(l) for l in open(a.ledger)] if os.path.exists(a.ledger)
        else [], None)
    if a.ingest:
        print("  ingested %d new comparable window(s)\n" % added)
    if a.report or not a.ingest:
        report(rows, buckets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
