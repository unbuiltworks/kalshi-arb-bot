#!/usr/bin/env python3
"""Exhaustive mine of every captured Gemini artifact for fill-level evidence.

The live API cannot be probed from here: GEMINI_API_KEY / GEMINI_API_SECRET
are absent and every private path returns 401 MissingSecurityHeaders. So this
is a census of what was ALREADY captured, not a new read.

Targets, in the order the P0 question needs them:
  transfers        union across all snapshots (each capture held only the
                   unpaginated 9-10 row default page, but snapshots were taken
                   at different times, so the union may exceed any one page)
  payout rows      /v1/prediction-markets/maker-rebate/payouts
  fills / trades   anything with a price, quantity and side on our account
  positions        /v1/prediction-markets/positions
  balances         /v1/balances
Nothing is inferred. A field that was never captured is reported absent.
"""
from __future__ import annotations

import json, os, glob, sys, collections
from decimal import Decimal as D

ROOTS = ["analysis/gemini", "analysis/l5", "data"]
FILL_KEYS = ({"price", "amount"}, {"price", "quantity"}, {"px", "qty"},
             {"price", "qty"}, {"fill_price", "fill_qty"})


def walk(o, f, out):
    if isinstance(o, dict):
        ks = set(o.keys())
        if o.get("purpose") or (o.get("type") in ("AdminCredit",)) or "eid" in ks:
            if {"amount", "currency"} <= ks:
                out["transfers"].setdefault(
                    str(o.get("eid") or o.get("advanceEid") or json.dumps(o, sort_keys=True)), (o, f))
        if {"total_rebate_usd", "total_volume_usd"} <= ks:
            out["payouts"].setdefault(str(o.get("id")), (o, f))
        for req in FILL_KEYS:
            if req <= ks and ("order_id" in ks or "tid" in ks or "trade_id" in ks
                              or "maker_or_taker" in ks or "fee_amount" in ks):
                out["fills"].setdefault(json.dumps(o, sort_keys=True)[:200], (o, f))
        if "settlementValue" in ks or "resolutionSide" in ks:
            out["resolutions"].setdefault(
                str(o.get("symbol") or json.dumps(o, sort_keys=True)[:80]), (o, f))
        for v in o.values():
            walk(v, f, out)
    elif isinstance(o, list):
        for v in o:
            walk(v, f, out)


def main():
    out = {k: {} for k in ("transfers", "payouts", "fills", "resolutions")}
    nfiles = 0
    for root in ROOTS:
        for f in glob.glob(f"{root}/**/*", recursive=True):
            if not f.endswith((".json", ".jsonl")):
                continue
            if "gemini" not in f.lower() and "/gemini" not in f.lower():
                # still scan l5 tapes, they may carry gemini rows
                if not f.endswith(".jsonl"):
                    continue
            try:
                if f.endswith(".jsonl"):
                    for line in open(f):
                        if line.strip():
                            walk(json.loads(line), f, out)
                else:
                    walk(json.load(open(f)), f, out)
                nfiles += 1
            except Exception:
                continue

    print(f"files scanned: {nfiles}\n")
    # ---- transfers
    tr = [o for o, _ in out["transfers"].values()]
    reb = [o for o in tr if str(o.get("purpose", "")).lower().find("rebate") >= 0]
    print(f"TRANSFERS  distinct: {len(tr)}   of which maker-rebate: {len(reb)}")
    bypurp = collections.Counter(str(o.get("purpose")) for o in tr)
    for k, v in bypurp.most_common(10):
        vals, bad = [], 0
        for o in tr:
            if str(o.get("purpose")) != k:
                continue
            try:
                vals.append(D(str(o["amount"])))
            except Exception:
                bad += 1          # a schema-sample row, not a real amount
        tot = sum(vals)
        if bad:
            k = k + f"  [{bad} non-numeric row(s) excluded]"
        print(f"   {k[:44]:44s} n={v:>3d} total=${tot}")
    # ---- payouts
    po = [o for o, _ in out["payouts"].values()]
    paid = [o for o in po if str(o.get("status")) == "PAID"]
    print(f"\nPAYOUT ROWS  distinct: {len(po)}  PAID: {len(paid)}")
    print(f"   credited rebate  ${sum(D(str(o['total_rebate_usd'])) for o in paid)}")
    print(f"   maker notional   ${sum(D(str(o['total_volume_usd'])) for o in paid)}")
    print(f"   maker fills      {sum(int(o['total_fill_count']) for o in paid)}")
    pk = set()
    for o in po:
        pk |= set(o.keys())
    print(f"   fields present on payout rows: {sorted(pk)}")
    print("   -> per-FILL breakdown present?  "
          f"{'YES' if any('fill' in k and k != 'total_fill_count' for k in pk) else 'NO'}")
    # ---- fills
    print(f"\nFILL/TRADE RECORDS with price+qty+id: {len(out['fills'])}")
    for o, f in list(out["fills"].values())[:5]:
        print(f"   {f.split('/')[-1][:40]:40s} {json.dumps(o)[:140]}")
    # ---- resolutions
    print(f"\nRESOLUTION RECORDS: {len(out['resolutions'])}")
    for s, (o, f) in list(out["resolutions"].items())[:5]:
        print(f"   {str(s)[:44]:44s} side={o.get('resolutionSide')} "
              f"settle={o.get('settlementValue')}")
    json.dump({k: {kk: vv[0] for kk, vv in v.items()} for k, v in out.items()},
              open("analysis/gemini/evidence_census.json", "w"), indent=1, default=str)
    print("\n-> analysis/gemini/evidence_census.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
