"""Reconstruct the preserved Oct-24 BTC15m live campaign from its own records.

Source: analysis/l5/residual_exit/preserved/*.ndjson -- the preserved live
tape for 2026-10-24 0045z..0230z. Settled P&L is L5_LIVE_SETTLED, which is
the venue's own line.
"""
from __future__ import annotations
import collections, glob, json
from decimal import Decimal as D

recs = []
for p in sorted(glob.glob("analysis/l5/residual_exit/preserved/*.ndjson")):
    for ln in open(p):
        ln = ln.strip()
        if not ln: continue
        try: d = json.loads(ln)
        except Exception: continue
        b = d.get("body")
        if isinstance(b, str):
            try: d["body"] = json.loads(b)
            except Exception: d["body"] = {}
        recs.append(d)

print("preserved records: %d\n" % len(recs))

print("=" * 86)
print("ACTUAL SETTLED P&L, from L5_LIVE_SETTLED (the venue's own lines)")
print("=" * 86)
print("%-9s %7s %7s %9s %9s %9s %11s %11s" % (
    "window", "up_qty", "dn_qty", "matched", "mat_pnl", "resid", "resid_pnl",
    "REALIZED"))
tot = D(0); tm = D(0); trp = D(0)
for d in recs:
    if d.get("label") != "L5_LIVE_SETTLED": continue
    b = d["body"]
    def g(k): return D(str(b.get(k) or 0))
    net = g("settlement_pnl")
    tot += net; tm += g("matched_pnl"); trp += g("residual_pnl")
    print("%-9s %7s %7s %9s %+9.4f %9s %+11.4f %+11.4f" % (
        str(b.get("market",""))[-5:], b.get("up_qty"), b.get("dn_qty"),
        b.get("matched_qty"), g("matched_pnl"), b.get("residual_qty"),
        g("residual_pnl"), net))
print("-" * 86)
print("%-9s %7s %7s %9s %+9.4f %9s %+11.4f %+11.4f" % (
    "TOTAL", "", "", "", tm, "", trp, tot))

print("\n" + "=" * 86)
print("ORDER LIFECYCLE")
print("=" * 86)
lc = collections.Counter()
for d in recs:
    if d.get("label") != "L5_LIVE_LIFECYCLE": continue
    b = d["body"]
    lc[str(b.get("event") or b.get("state") or b.get("kind") or "?")] += 1
for k, v in lc.most_common(12):
    print("   %-44s %4d" % (k, v))
ps = sum(1 for d in recs if d.get("label") == "L5_FILL_CALIB_PRESUBMIT")
tm_ = sum(1 for d in recs if d.get("label") == "L5_FILL_CALIB_TERMINAL")
print("   %-44s %4d" % "pre-submit stamps (orders sent)".ljust(44).split("|")[0:1] + (ps,) if False else "")
print("   pre-submit stamps (orders sent)              %4d" % ps)
print("   terminal stamps                              %4d" % tm_)

print("\n" + "=" * 86)
print("REFUSAL CENSUS -- why the engine did not place")
print("=" * 86)
ref = collections.Counter()
for d in recs:
    if d.get("label") != "L5_LIVE_DECISION": continue
    k = str(d["body"].get("key") or "")
    if ":" not in k: continue
    # collapse the numeric tail of CASH_COMMITMENT so the class is countable
    if "CASH_COMMITMENT" in k:
        k = k.split("CASH_COMMITMENT")[0] + "CASH_COMMITMENT:<amount>over<cap>"
    ref[k] += 1
for k, v in ref.most_common(16):
    print("   %-66s %4d" % (k[:66], v))
