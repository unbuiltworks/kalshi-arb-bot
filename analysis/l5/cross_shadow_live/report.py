"""Cohort state for the PROSPECTIVE <=0.97 complement-cross shadow.

FOUR LEDGERS, NEVER ADDED TOGETHER:
  A  MAKER OUTCOME      what the real post-only COMPLETE actually did
  B  CROSS SHADOW       THEORETICAL counterfactual, computed ONLY for rows
                        that pass the frozen depth/freshness/size/fee criteria
  C  INCENTIVES         venue-credited rewards/rebates, reported separately
  D  REALIZED P&L       venue-confirmed settlement only

THE STATISTICAL UNIT IS AN INDEPENDENT BOOK READING, not an order attempt.
Repeated COMPLETE attempts against one cached book reading are ONE observation.
Every summary is clustered by market, because these observations are not
independent across a market and pooling them would overstate n.

NO HYPOTHETICAL FILL IS EVER ASSIGNED to an UNKNOWN or STALE observation.
"""
from __future__ import annotations
import json, os, statistics as st
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D

SCR = os.path.dirname(os.path.abspath(__file__))
def jl(name):
    p = os.path.join(SCR, name)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
ROWS, LED = jl("cohort.jsonl"), jl("ledger.jsonl")

def dec(v):
    try:
        return D(str(v))
    except Exception:
        return None
def ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

SUPPORTED = "SUPPORTED_COUNTERFACTUAL_CROSS"
markets = sorted({r["slug"] for r in ROWS})
books = {(r["slug"], r["obs_status_utc"]) for r in ROWS if r.get("obs_status_utc")}
bym = defaultdict(list)
for r in ROWS:
    bym[r["slug"]].append(r)

print("=" * 76)
print("PROSPECTIVE <=0.97 CROSS SHADOW -- COHORT STATE       (SHADOW ONLY)")
print("=" * 76)
span = [ts(r["obs_probe_receipt_utc"]) for r in ROWS if ts(r["obs_probe_receipt_utc"])]
print(f"serving commit(s) : {sorted({r['commit'] for r in ROWS})}")
if span:
    print(f"window            : {min(span):%m-%d %H:%M:%S}Z -> {max(span):%m-%d %H:%M:%S}Z")
print(f"INDEPENDENT BOOK READINGS : {len(books)}   <- the statistical unit")
print(f"order attempts            : {len(ROWS)}   (NOT the unit; retries share a book)")
print(f"markets                   : {len(markets)}")

print("\n" + "-" * 76)
print("PER-MARKET CLUSTER  (pooling these would overstate n)")
print("-" * 76)
print(f"{'market':44s} {'books':>5s} {'ord':>4s} {'priced':>6s} {'<=.97':>5s} {'SUPP':>4s}")
for m in markets:
    sub = bym[m]
    b = len({r["obs_status_utc"] for r in sub if r.get("obs_status_utc")})
    priced = [r for r in sub if r.get("econ_pair_cost")]
    le97 = [r for r in priced if dec(r["econ_pair_cost"]) <= D("0.97")]
    sup = [r for r in sub if r.get("verdict") == SUPPORTED]
    print(f"{m:44s} {b:5d} {len(sub):4d} {len(priced):6d} {len(le97):5d} {len(sup):4d}")

print("\n" + "-" * 76)
print("INVARIANTS (every row, every market)")
print("-" * 76)
print("  would_submit false :", all(r.get("would_submit") is False for r in ROWS))
print("  shadow_only true   :", all(r.get("shadow_only") is True for r in ROWS))
print("  arm                :", sorted({r.get("prospective_pair_cost_max") for r in ROWS}))
print("  live pair ceiling  :", sorted({r.get("live_pair_ceiling_unchanged") for r in ROWS}))

print("\n" + "-" * 76)
print("EVIDENCE QUALITY, per market (what the instrumentation exists to fix)")
print("-" * 76)
for m in markets:
    sub = bym[m]
    c = Counter(r["obs_executable_depth_status"] or "NO_OBSERVATION" for r in sub)
    ages = [r["quote_age_s"] for r in sub if isinstance(r.get("quote_age_s"), (int, float))]
    fresh = [a for a in ages if a <= 5.0]
    print(f"  {m}")
    print("      depth: " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    if ages:
        print(f"      age_at_probe p50 {st.median(ages):.2f}s   "
              f"contemporaneous(<=5s) {len(fresh)}/{len(ages)} = {len(fresh)/len(ages):.1%}")

print("\n" + "-" * 76)
print("FIRST STOP (pooled, for shape only -- clusters above are authoritative)")
print("-" * 76)
def stop_class(r):
    fs = r.get("first_stop") or "?"
    for pre, lab in (("quote_age_", "quote_age_over_5.0s"),
                     ("pair_", "pair_over_prospective_0.97"),
                     ("depth_", "depth_below_intended"),
                     ("executable_depth:", "depth_unknown_or_missing")):
        if fs.startswith(pre):
            return lab
    return fs.split(":")[0]
for k, v in Counter(stop_class(r) for r in ROWS).most_common():
    print(f"  {k:44s} {v:4d}  {v/len(ROWS):6.1%}")
priced = [r for r in ROWS if r.get("econ_pair_cost")]
print(f"\n  REACHED THE PRICE TEST {len(priced):4d}  {len(priced)/max(len(ROWS),1):6.1%}"
      "   (pre-registered corpus: 0 of 260)")
if priced:
    pcs = sorted(dec(r["econ_pair_cost"]) for r in priced)
    print(f"  pair cost  min {pcs[0]}  p50 {pcs[len(pcs)//2]}  max {pcs[-1]}")
    print(f"  pair <= 1.00 : {sum(1 for p in pcs if p <= D('1.00'))} / {len(pcs)}")
    print(f"  pair <= 0.97 : {sum(1 for p in pcs if p <= D('0.97'))} / {len(pcs)}  <- THE ARM")

print("\n" + "=" * 76)
print("LEDGER A -- MAKER OUTCOME (real orders; never mixed with the shadow)")
print("=" * 76)
for m in markets:
    sub = bym[m]
    filled = [r for r in sub if dec(r.get("filled_qty")) and dec(r["filled_qty"]) > 0]
    print(f"  {m}: COMPLETE attempts {len(sub)}, FILLED {len(filled)}")
    for k, v in Counter(str(r.get("end_reason"))[:46] for r in sub).most_common(4):
        print(f"      {k:48s} {v}")

print("\n" + "=" * 76)
print("LEDGER B -- CROSS-SHADOW COUNTERFACTUAL  (THEORETICAL, NEVER REALIZED)")
print("=" * 76)
sup = [r for r in ROWS if r.get("verdict") == SUPPORTED]
print("  Computed ONLY for rows meeting ALL frozen criteria: relation COMPLETE,")
print("  authenticated quote <=5s AT THE PROBE, depth OBSERVED and >= intended")
print("  qty, pair <= 0.97, and net positive after confirmed taker fee AND")
print("  forgone maker rebate. UNKNOWN / STALE rows get NO hypothetical fill.")
excluded = Counter()
for r in ROWS:
    if r.get("verdict") != SUPPORTED:
        st_ = r.get("obs_executable_depth_status") or "NO_OBSERVATION"
        excluded[st_ if st_ in ("STALE", "NO_OBSERVATION") else stop_class(r)] += 1
print(f"\n  SUPPORTED opportunities : {len(sup)}")
print(f"  excluded from any counterfactual : {len(ROWS) - len(sup)}")
for k, v in excluded.most_common():
    print(f"      {k:44s} {v}")
if sup:
    print("\n  per market:")
    for m in sorted({r['slug'] for r in sup}):
        s = [r for r in sup if r["slug"] == m]
        net = sum(float(r["econ_net_locked_pair_pnl"]) for r in s)
        print(f"      {m}: n={len(s)}  THEORETICAL net ${net:+.4f}")
        for r in s:
            print(f"        {r['stage']} pair={r['econ_pair_cost']} "
                  f"gross={r['econ_gross_locked_pair_pnl']} "
                  f"fee={r['econ_confirmed_taker_fee']} "
                  f"forgone_rebate={r['econ_maker_rebate_forgone']} "
                  f"NET={r['econ_net_locked_pair_pnl']} "
                  f"would_submit={r['would_submit']}")
    print("\n  ^ THEORETICAL. Not realized, not booked, not comparable to Ledger D.")
else:
    print("\n  No opportunity has met the frozen criteria. Nothing to counterfactual.")

print("\n" + "=" * 76)
print("LEDGER C -- INCENTIVES (venue-credited only)")
print("=" * 76)
inc = [r for r in LED if "incentive" in r["_kind"] or "reward" in r["_kind"]]
print(f"  rows: {len(inc)}" + ("" if inc else "   (none emitted in this window)"))
if inc:
    r = inc[-1]
    print(f"  latest audit: {r['_ts']}")
    print("\n  MAKER REBATE  (negative venue commission on our own fills;")
    print("                 netted into each fill, never a separate payment)")
    print(f"    credited to us            $ {r.get('maker_rebate_credited_usd')}")
    print(f"    charged to us             $ {r.get('maker_rebate_charged_usd')}")
    print(f"    net                       $ {r.get('maker_rebate_net_usd')}"
          f"   over {r.get('fills_with_a_credit')} fills")
    print(f"    counterparty side         $ {r.get('counterparty_side_usd_not_ours')}"
          "   <- the OTHER trader's fee, NEVER our income")
    print("\n  LIQUIDITY REWARDS  (/v1/incentives/earnings)")
    print(f"    PAID (credited)           $ {r.get('rewards_paid_usd')}")
    print(f"    accrued, NOT credited     $ {r.get('rewards_accrued_not_credited_usd')}"
          "   <- NOT income until credited")
    print(f"    all statuses              $ {r.get('rewards_total_all_statuses_usd')}")
    print(f"    by status                   {r.get('rewards_by_status')}")
    try:
        credited = (float(r.get("maker_rebate_net_usd") or 0)
                    + float(r.get("rewards_paid_usd") or 0))
        print(f"\n  TOTAL VENUE-CREDITED        $ {credited:.4f}")
    except (TypeError, ValueError):
        pass
    print(f"  (formula estimate, NOT a credit, excluded: "
          f"$ {r.get('estimated_not_a_credit')})")
    print("  Incentives are additive economics and are NEVER netted into"
          "\n  Ledger D trading P&L.")

print("\n" + "=" * 76)
print("LEDGER D -- REALIZED P&L (venue-confirmed settlement only)")
print("=" * 76)
setl = [r for r in LED if r["_kind"] == "settlement"]
by_slug = {}
for r in sorted(setl, key=lambda x: x["_ts"]):
    by_slug[r.get("slug")] = r
if not by_slug:
    print("  no settlement rows yet")
for slug, r in sorted(by_slug.items()):
    tag = "  <- IN COHORT" if slug in markets else ""
    print(f"  {slug}{tag}")
    print(f"      status={r.get('status')} settled={r.get('settled')} "
          f"realized_usd={r.get('realized_usd')} "
          f"confirmed={r.get('realized_confirmed')} source={r.get('realized_source')}")
    if r.get("resolution") is not None:
        print(f"      resolution={r.get('resolution')} "
              f"venue_qty={r.get('venue_qty_total')} ledger_qty={r.get('ledger_qty_total')}")
unsettled = [m for m in markets if by_slug.get(m, {}).get("realized_usd") in (None, "null")]
print(f"\n  cohort markets awaiting venue-confirmed settlement: {len(unsettled)}/{len(markets)}")
for m in unsettled:
    print(f"      {m}")

print("\n" + "=" * 76)
print("STOP CONDITIONS")
print("=" * 76)
enough = len(markets) >= 4 and len(books) >= 60
print(f"  (A) first SUPPORTED <=0.97 opportunity : {'MET' if sup else 'NOT YET'}")
print(f"  (B) >=60 independent readings, >=4 markets : "
      f"{len(books)} readings / {len(markets)} markets -> {'MET' if enough else 'NOT YET'}")
print("\n  At either stop: REPORT THE EVIDENCE. No strategy change is committed")
print("  or deployed on the strength of it.")
