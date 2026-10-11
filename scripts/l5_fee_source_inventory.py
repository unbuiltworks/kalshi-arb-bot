#!/usr/bin/env python3
"""Inventory every historical Polymarket fee/cost source on disk (READ-ONLY).

Emits analysis/l5/POLYMARKET_FEE_SOURCES.json. Companion to the .md.
Distinguishes CHARGED (debited amount) from TERM (schedule) from UNAVAILABLE.
No network calls, no venue writes, no estimation of missing fees.
"""
import json, gzip, glob, os, statistics as st
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(ROOT, *p)

def qs(xs):
    xs = sorted(xs)
    n = len(xs)
    q = lambda f: xs[min(n - 1, int(f * n))]
    return dict(n=n, min=xs[0], p10=q(.10), p25=q(.25), median=q(.50),
                mean=sum(xs) / n, p75=q(.75), p90=q(.90), max=xs[-1])

out = {"generated_utc": None, "scope": "another trader's public activity; nothing here is realized P&L for our account",
       "sources": [], "confirmed_fee_distribution": {}, "representativeness": {}, "schedule": {}}
import datetime
out["generated_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

# ---------- 1. CHARGED: entryFeesUsdc on positions snapshots ----------
pos_files = ["positions_all.jsonl", "positions_closed.jsonl", "positions_redeemable.jsonl"]
pos_rows = {}
for fn in pos_files:
    rows, fetched = [], set()
    with open(R("data/l5/raw", fn)) as f:
        for line in f:
            d = json.loads(line)
            fetched.add(d.get("fetched_at"))
            for r in d.get("payload") or []:
                if isinstance(r, dict):
                    rows.append(r)
    pos_rows[fn] = (rows, sorted(fetched))

ALL = pos_rows["positions_all.jsonl"][0]
feefield = [r for r in ALL if r.get("entryFeesUsdc") is not None]
nonzero = [r for r in feefield if float(r["entryFeesUsdc"]) > 0]

# per-leg and per-contract
per_leg = [float(r["entryFeesUsdc"]) for r in feefield]
per_leg_nz = [float(r["entryFeesUsdc"]) for r in nonzero]
per_ct, rate_vs_notional, implied_rate_formula = [], [], []
for r in nonzero:
    sz = float(r.get("size") or 0)
    gross = float(r.get("grossInitialValue") or 0)
    fee = float(r["entryFeesUsdc"])
    tb = float(r.get("totalBought") or 0)
    base = sz if sz > 0 else tb
    if base > 0:
        per_ct.append(fee / base)
    if gross > 0:
        rate_vs_notional.append(fee / gross)
    p = float(r.get("avgPrice") or 0)
    if base > 0 and 0 < p < 1:
        implied_rate_formula.append((fee / base) / (p * (1 - p)))

tot_fee = sum(per_leg)
tot_gross = sum(float(r.get("grossInitialValue") or 0) for r in feefield)
dates = sorted({r.get("endDate") for r in feefield if r.get("endDate")})

out["confirmed_fee_distribution"] = {
    "field": "entryFeesUsdc", "category": "CHARGED",
    "file": "data/l5/raw/positions_all.jsonl",
    "snapshot_fetched_at": pos_rows["positions_all.jsonl"][1],
    "legs_with_field": len(feefield), "legs_fee_gt_zero": len(nonzero),
    "legs_fee_exactly_zero": len(feefield) - len(nonzero),
    "endDate_range": [dates[0], dates[-1]] if dates else None,
    "fee_per_leg_usdc_all": qs(per_leg),
    "fee_per_leg_usdc_nonzero": qs(per_leg_nz) if per_leg_nz else None,
    "fee_per_contract_usdc_nonzero": qs(per_ct) if per_ct else None,
    "fee_over_grossInitialValue_nonzero": qs(rate_vs_notional) if rate_vs_notional else None,
    "total_fee_usdc": tot_fee, "total_grossInitialValue_usdc": tot_gross,
    "aggregate_effective_bps_vs_gross": (tot_fee / tot_gross * 1e4) if tot_gross else None,
    "implied_rate_if_fee_eq_rate_x_p_1mp_x_size": qs(implied_rate_formula) if implied_rate_formula else None,
}

# ---------- 2. TERM: market-level fee schedule ----------
sched = Counter(); mk_terms = Counter(); mkts = 0
by_feetype_slugpfx = defaultdict(Counter)
clob_rewards = []
for line in open(R("data/l5/raw/markets.jsonl")):
    d = json.loads(line)
    for r in d.get("payload") or []:
        if not isinstance(r, dict): continue
        mkts += 1
        sched[json.dumps(r.get("feeSchedule"), sort_keys=True)] += 1
        for k in ("feeType", "feesEnabled", "makerBaseFee", "takerBaseFee",
                  "makerRebatesFeeShareBps", "holdingRewardsEnabled",
                  "rewardsMinSize", "rewardsMaxSpread"):
            mk_terms[(k, json.dumps(r.get(k)))] += 1
        slug = (r.get("slug") or "")
        pfx = "-".join(slug.split("-")[:3])
        by_feetype_slugpfx[r.get("feeType")][pfx] += 1
        if r.get("clobRewards"):
            clob_rewards.extend(r["clobRewards"])

out["schedule"] = {
    "file": "data/l5/raw/markets.jsonl", "category": "TERM",
    "market_records": mkts,
    "feeSchedule_values": {k: v for k, v in sched.most_common()},
    "other_term_fields": {f"{k}={v}": n for (k, v), n in sorted(mk_terms.items())},
    "feeType_by_slug_prefix": {k: dict(v.most_common(8)) for k, v in by_feetype_slugpfx.items()},
    "clobRewards_records": len(clob_rewards),
    "clobRewards_sample": clob_rewards[:4],
}

# ---------- 3. CHARGED (reconstructable): activity wedge ----------
# fee = usdcSize - price*size on BUY. Count coverage only; the validated
# reconstruction itself lives in analysis/l5/fees/reconstruct.py.
wedge_n = wedge_bearing = 0
wedge_fees = 0.0
wedge_gross = 0.0
ts_min = ts_max = None
wedge_per_ct = []
import itertools
def _lines():
    for l in open(R("data/l5/raw/activity.jsonl")): yield l
    for l in gzip.open(R("data/l5/raw/activity_gap.jsonl.gz"), "rt"): yield l
for line in _lines():
    d = json.loads(line)
    for r in d.get("payload") or []:
        if not isinstance(r, dict) or r.get("type") != "TRADE": continue
        u, p, s = r.get("usdcSize"), r.get("price"), r.get("size")
        if u is None or p is None or not s: continue
        wedge_n += 1
        t = r.get("timestamp")
        if t: ts_min = t if ts_min is None else min(ts_min, t); ts_max = t if ts_max is None else max(ts_max, t)
        w = (u - p * s) if r.get("side") == "BUY" else (p * s - u)
        if abs(w / s) > 1e-9:
            wedge_bearing += 1
            _k = r.get("transactionHash")
            wedge_fees += w
            wedge_gross += p * s
            wedge_per_ct.append(w / s)
fmt = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat() if t else None
out["sources"].append({
    "source": "data/l5/raw/activity.jsonl + activity_gap.jsonl.gz wedge (usdcSize - price*size)",
    "category": "CHARGED (reconstructed, model-validated)",
    "fills": wedge_n, "fee_bearing_fills": wedge_bearing,
    "fee_bearing_fraction": wedge_bearing / wedge_n if wedge_n else None,
    "total_wedge_usdc": wedge_fees,
    "fee_bearing_gross_usdc": wedge_gross,
    "fee_bearing_effective_bps": (wedge_fees / wedge_gross * 1e4) if wedge_gross else None,
    "fee_per_contract_fee_bearing": qs(wedge_per_ct) if wedge_per_ct else None,
    "date_range_utc": [fmt(ts_min), fmt(ts_max)],
})

# ---------- 4. TERM: sports_trades maker/taker bps ----------
sp = [json.loads(l) for l in gzip.open(R("data/transfer_backtest/sports_trades.jsonl.gz"), "rt")]
spc = Counter((e.get("maker_fee_bps"), e.get("taker_fee_bps")) for e in sp)
sp_has_usdc = sum(1 for e in sp for t in e.get("trades") or [] if "usdcSize" in t)
sp_trades = sum(len(e.get("trades") or []) for e in sp)
out["sources"].append({
    "source": "data/transfer_backtest/sports_trades.jsonl.gz",
    "category": "TERM (placeholder/default — see .md)",
    "events": len(sp), "trades": sp_trades,
    "maker_taker_bps_values": {f"{k}": v for k, v in spc.items()},
    "trades_with_usdcSize": sp_has_usdc,
    "feeSchedule_present": any("feeSchedule" in e for e in sp),
})

# ---------- 5. UNAVAILABLE: gas / on-chain cost ----------
act_tx = 0
with open(R("data/l5/raw/trades.jsonl")) as f:
    for line in f:
        d = json.loads(line)
        for r in d.get("payload") or []:
            if isinstance(r, dict) and r.get("transactionHash"): act_tx += 1
out["sources"].append({
    "source": "on-chain gas / transaction cost",
    "category": "UNAVAILABLE",
    "note": "transactionHash present on fills (trades.jsonl: %d rows) but no gasUsed/gasPrice/effectiveGasPrice field exists anywhere in the corpus, and no RPC/receipt capture is on disk. Not estimated." % act_tx,
})

# ---------- 6. Representativeness of the fee-bearing positions subset ----------
def prof(rows, label):
    szs = [float(r.get("size") or 0) for r in rows]
    pxs = [float(r.get("avgPrice") or 0) for r in rows if r.get("avgPrice")]
    gv = [float(r.get("grossInitialValue") or 0) for r in rows]
    sl = Counter("-".join((r.get("eventSlug") or "").split("-")[:3]) for r in rows)
    dd = sorted({r.get("endDate") for r in rows if r.get("endDate")})
    return {"label": label, "n": len(rows),
            "size": qs(szs) if szs else None, "avgPrice": qs(pxs) if pxs else None,
            "grossInitialValue": qs(gv) if gv else None,
            "cohort_slug_prefix": dict(sl.most_common(8)),
            "endDate_range": [dd[0], dd[-1]] if dd else None,
            "redeemable_true": sum(1 for r in rows if r.get("redeemable")),
            "realizedPnl_nonzero": sum(1 for r in rows if float(r.get("realizedPnl") or 0) != 0)}

out["representativeness"] = {
    "population_note": "positions_all.jsonl is a single snapshot of OPEN positions; every row in it carries entryFeesUsdc. The 1,537 figure is the snapshot size, not a 1,537-of-N subset of the 761k-fill tape.",
    "positions_all_rows": len(ALL),
    "rows_with_entryFeesUsdc": len(feefield),
    "coverage_within_file": len(feefield) / len(ALL) if ALL else None,
    "activity_tape_fills_incl_gap": wedge_n,
    "coverage_of_tape": len(feefield) / wedge_n if wedge_n else None,
    "fee_gt_zero": prof(nonzero, "entryFeesUsdc > 0"),
    "fee_eq_zero": prof([r for r in feefield if float(r["entryFeesUsdc"]) == 0], "entryFeesUsdc == 0"),
    "file_row_counts": {fn: len(v[0]) for fn, v in pos_rows.items()},
    "snapshot_times": {fn: v[1] for fn, v in pos_rows.items()},
}

p = R("analysis/l5/POLYMARKET_FEE_SOURCES.json")
with open(p, "w") as f:
    json.dump(out, f, indent=2, default=str)
print("wrote", p)
print(json.dumps({k: out[k] for k in ("confirmed_fee_distribution", "representativeness")}, indent=2, default=str)[:6000])
print(json.dumps(out["sources"], indent=2, default=str)[:3000])
print(json.dumps(out["schedule"], indent=2, default=str)[:2500])
