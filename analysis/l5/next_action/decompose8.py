#!/usr/bin/env python3
"""Eight-component economic decomposition of every settled L5 window, with the
provenance of each component stated. Read-only. DISCOVERY."""
from __future__ import annotations
import csv, gzip, collections, statistics as st, sys, datetime as dt
from decimal import Decimal as D
sys.path.insert(0, ".")
from src.pmus_fees import taker_fee, maker_rebate
WHEN = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)

R = list(csv.DictReader(gzip.open("analysis/l5/l5_canonical_sequence.csv.gz", "rt")))
def isup(r): return r["outcome"].lower().startswith("up") or r["side"].lower() in ("up","yes")
W = collections.defaultdict(list)
for r in R: W[r["window_id"]].append(r)

tot = collections.Counter()
agg = collections.defaultdict(float)
per_window = []
for wid, rows in W.items():
    up = dn = upsp = dnsp = 0.0
    fees = reb = 0.0
    nmaker = ntaker = nunk = 0
    for r in rows:
        q = float(r["quantity"]); p = float(r["price"])
        if isup(r): up += q; upsp += q*p
        else:        dn += q; dnsp += q*p
        pr = D(str(round(p,2))); qd = D(str(q))
        if r["maker_taker"] == "maker": reb += float(maker_rebate(pr,qd,when=WHEN)); nmaker += 1
        elif r["maker_taker"] == "taker": fees += float(taker_fee(pr,qd,when=WHEN)); ntaker += 1
        else: nunk += 1                      # excluded from both, the registered convention
    sup = rows[0]["settlement_outcome"].lower().startswith("up")
    matched = min(up, dn)
    up_avg = upsp/up if up else 0.0
    dn_avg = dnsp/dn if dn else 0.0
    pair_cost = up_avg + dn_avg if matched > 0 else None
    # (1) matched-pair economics, pre fee/rebate: each matched pair redeems exactly $1
    m_pnl = matched * (1.0 - pair_cost) if matched > 0 else 0.0
    # (2) directional residual P&L
    if up > dn:
        res_q = up - dn; res_up = True;  res_cost = res_q * up_avg
    elif dn > up:
        res_q = dn - up; res_up = False; res_cost = res_q * dn_avg
    else:
        res_q = 0.0; res_up = True; res_cost = 0.0
    res_payout = res_q * (1.0 if (sup == res_up) else 0.0)
    r_pnl = res_payout - res_cost
    # (7) settlement/redemption cashflow actually received
    redeem = matched * 1.0 + res_payout
    gross_check = sum((1.0 if (sup == isup(r)) else 0.0) - float(r["price"]) for r in rows for _ in [0]) # placeholder
    gross = sum(((1.0 if (sup == isup(r)) else 0.0) - float(r["price"])) * float(r["quantity"]) for r in rows)
    agg["1_matched_pre_fees"] += m_pnl
    agg["2_directional_residual"] += r_pnl
    agg["4_maker_rebate_MODELED"] += reb
    agg["6_taker_fees_MODELED"] += fees
    agg["7_redemption_cashflow"] += redeem
    agg["gross_identity_check"] += gross
    tot["windows"] += 1
    tot["maker"] += nmaker; tot["taker"] += ntaker; tot["unknown"] += nunk
    per_window.append({"w": wid, "m": m_pnl, "r": r_pnl, "fees": fees, "reb": reb,
                       "res_q": res_q, "pair_cost": pair_cost, "matched": matched,
                       "gross": gross, "dur": int(rows[0]["duration_minutes"]),
                       "asset": rows[0]["asset"]})

print("=" * 80)
print("EIGHT-COMPONENT DECOMPOSITION -- 2,216 settled windows  (DISCOVERY)")
print("=" * 80)
print(f"{'component':42s} {'total':>14}  provenance")
print("-" * 80)
rowsout = [
 ("1  matched-pair economics (pre fee/rebate)", agg["1_matched_pre_fees"],
  "ACTUAL L5 fills on Polymarket"),
 ("2  directional residual P&L",               agg["2_directional_residual"],
  "ACTUAL L5 fills on Polymarket"),
 ("3  maker spread/basis economics",           None,
  "NOT IDENTIFIABLE -- no book/mid in this dataset"),
 ("4  maker rebates",                          agg["4_maker_rebate_MODELED"],
  "MODELED PMUS counterfactual -- NOT cash-confirmed"),
 ("5  liquidity rewards",                      None,
  "UNVERIFIED -- none credited to us; counted at zero"),
 ("6  taker fees",                            -agg["6_taker_fees_MODELED"],
  "MODELED PMUS counterfactual -- L5 never paid these"),
 ("7  settlement/redemption cashflow",          agg["7_redemption_cashflow"],
  "ACTUAL -- gross redemption, not a P&L line"),
]
for name, v, prov in rowsout:
    s = "NOT MEASURABLE" if v is None else f"${v:+,.2f}"
    print(f"{name:42s} {s:>14}  {prov}")
print("-" * 80)
g = agg["1_matched_pre_fees"] + agg["2_directional_residual"]
print(f"{'(1)+(2) gross directional+pair, pre-fee':42s} {f'${g:+,.2f}':>14}  ACTUAL behaviour, ACTUAL settlements")
gic = f"${agg['gross_identity_check']:+,.2f}"
print(f"{'identity check: sum (won-price)*qty':42s} {gic:>14}  "
      f"{'IDENTITY HOLDS' if abs(g-agg['gross_identity_check'])<1.0 else 'MISMATCH'}")
net_pre = g - agg["6_taker_fees_MODELED"]
net_post = net_pre + agg["4_maker_rebate_MODELED"]
print(f"{'8  net pre-rebate (modeled PMUS fees)':42s} {f'${net_pre:+,.2f}':>14}  MODELED TRANSPLANT")
print(f"{'8  net incl modeled maker rebate':42s} {f'${net_post:+,.2f}':>14}  MODELED TRANSPLANT")
print(f"\nfills: maker {tot['maker']:,} / taker {tot['taker']:,} / unknown {tot['unknown']:,} "
      f"(unknown excluded from fees AND rebates -- the registered convention)")
print(f"maker share of classified fills: {tot['maker']/(tot['maker']+tot['taker']):.1%}")

print("\n--- where the money is, by component, as a share of |total| ---")
for k, v in (("matched pair (1)", agg["1_matched_pre_fees"]),
             ("directional residual (2)", agg["2_directional_residual"]),
             ("modeled maker rebate (4)", agg["4_maker_rebate_MODELED"]),
             ("modeled taker fees (6)", -agg["6_taker_fees_MODELED"])):
    print(f"   {k:28s} ${v:+10,.2f}")
print("\n--- per-window distribution of (1)+(2), pre-fee ---")
pw = sorted(x["gross"] for x in per_window)
print(f"   n={len(pw):,}  mean ${st.mean(pw):+.3f}  p5 ${pw[int(len(pw)*.05)]:+.2f}  "
      f"p25 ${pw[int(len(pw)*.25)]:+.2f}  median ${pw[len(pw)//2]:+.2f}  "
      f"p75 ${pw[int(len(pw)*.75)]:+.2f}  p95 ${pw[int(len(pw)*.95)]:+.2f}")
print(f"   windows with (1)+(2) > 0: {sum(1 for x in pw if x>0)/len(pw):.1%}")
res = sorted(x["r"] for x in per_window)
print(f"\n--- per-window DIRECTIONAL RESIDUAL P&L (2) only ---")
print(f"   mean ${st.mean(res):+.3f}  median ${res[len(res)//2]:+.2f}  "
       f"p5 ${res[int(len(res)*.05)]:+.2f}  p95 ${res[int(len(res)*.95)]:+.2f}  "
       f"share>0 {sum(1 for x in res if x>0)/len(res):.1%}")
mm = sorted(x["m"] for x in per_window)
print(f"--- per-window MATCHED-PAIR P&L (1) only ---")
print(f"   mean ${st.mean(mm):+.3f}  median ${mm[len(mm)//2]:+.2f}  "
      f"p5 ${mm[int(len(mm)*.05)]:+.2f}  p95 ${mm[int(len(mm)*.95)]:+.2f}  "
      f"share>0 {sum(1 for x in mm if x>0)/len(mm):.1%}")
pc = sorted(x["pair_cost"] for x in per_window if x["pair_cost"])
print(f"\n--- effective matched pair cost, per window (n={len(pc):,}) ---")
print(f"   p10 {pc[int(len(pc)*.1)]:.4f}  p25 {pc[int(len(pc)*.25)]:.4f}  median {pc[len(pc)//2]:.4f}  "
      f"p75 {pc[int(len(pc)*.75)]:.4f}  p90 {pc[int(len(pc)*.9)]:.4f}")
print(f"   windows with pair cost < 1.00 (locked pair profit): "
      f"{sum(1 for x in pc if x<1.0)/len(pc):.1%}")
