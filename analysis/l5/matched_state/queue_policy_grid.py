#!/usr/bin/env python3
"""BTC15m policy grid on the EXISTING queue-aware print replay
(`analysis/l5/print_replay.py`), split in-sample / out-of-sample by date.

REPLAY, hypothetical fills (queue-aware against L5's own prints: strict
price priority fills, a tie needs a print >= 5x our clip). Never realized.

Accounting kept separate, per window:
  trading net   = matched-pair P&L + settled residual P&L (no fees: maker)
  rebate        = maker rebate at THIS ACCOUNT'S VERIFIED TIER (Tier 1:
                  none, docs.polymarket.us/fees fetched 2026-11-12);
  rebate_ref    = the retired flat 0.0125 coefficient, REFERENCE ONLY.

The pair-cost ceiling stays at the deployed 0.96 (`L5_TS_MAX_PAIR_COST`);
the residual cap at the deployed k; nothing here loosens either.

    python3 analysis/l5/matched_state/queue_policy_grid.py [--out OUT.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics as st
import sys
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
spec = importlib.util.spec_from_file_location("pr", os.path.join(ROOT, "analysis/l5/print_replay.py"))
PR = importlib.util.module_from_spec(spec)
os.chdir(ROOT)
spec.loader.exec_module(PR)

BASE = dict(k=1, clip=1, band_lo=D("0.05"), band_hi=D("0.95"), comp_cap=D("0.96"),
            max_subs=40)
POLICIES = {
    # today's deployed shape: paired opening, simultaneous, no replenish, depth 2
    "deployed_d2": dict(depth=2, simultaneous=True, replenish=False, paired=True),
    "d1": dict(depth=1, simultaneous=True, replenish=False, paired=True),
    "d0": dict(depth=0, simultaneous=True, replenish=False, paired=True),
    "d2_replenish": dict(depth=2, simultaneous=True, replenish=True, paired=True),
    "d0_replenish": dict(depth=0, simultaneous=True, replenish=True, paired=True),
    "d1_replenish": dict(depth=1, simultaneous=True, replenish=True, paired=True),
    "d2_sequential": dict(depth=2, simultaneous=False, replenish=False, paired=False),
}


def run(windows, pol):
    rows = [PR.sim(w, **BASE, **pol) for w in windows]
    n = len(rows)
    tr = [float(r["net"]) for r in rows]
    reb_ref = [float(r["rebate"]) for r in rows]
    mean = st.mean(tr) if n else 0.0
    sd = st.pstdev(tr) if n > 1 else 0.0
    return {"windows": n,
            "trading_net_per_window": round(mean, 4),
            "trading_net_se": round(sd / (n ** 0.5), 4) if n else None,
            "matched_pnl_per_window": round(st.mean(float(r["m_pnl"]) for r in rows), 4),
            "residual_pnl_per_window": round(st.mean(float(r["r_pnl"]) for r in rows), 4),
            "fills_per_window": round(st.mean(r["fills"] for r in rows), 2),
            "contracts_per_window": round(st.mean(float(r["contracts"]) for r in rows), 2),
            "windows_with_pair": sum(1 for r in rows if r["matched"] > 0),
            "residual_qty_per_window": round(st.mean(float(r["r_qty"]) for r in rows), 3),
            "rebate_tier1_per_window": 0.0,
            "rebate_ref_0p0125_per_window": round(st.mean(reb_ref), 4)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    ws = PR.load()
    days = sorted({w["day"] for w in ws})
    cut = days[len(days) // 2]
    IS = [w for w in ws if w["day"] < cut]
    OOS = [w for w in ws if w["day"] >= cut]
    out = {"label": "REPLAY, queue-aware vs L5 prints; trading net excludes rebates",
           "windows": len(ws), "days": [days[0], days[-1]], "oos_from": cut,
           "base": {k: str(v) for k, v in BASE.items()}, "results": {}}
    for name, pol in POLICIES.items():
        out["results"][name] = {"IS": run(IS, pol), "OOS": run(OOS, pol)}
        r = out["results"][name]
        print("%-15s IS %+.4f (se %.4f, pairs %d)  OOS %+.4f (se %.4f, pairs %d)  fills/w %.1f/%.1f"
              % (name, r["IS"]["trading_net_per_window"], r["IS"]["trading_net_se"], r["IS"]["windows_with_pair"],
                 r["OOS"]["trading_net_per_window"], r["OOS"]["trading_net_se"], r["OOS"]["windows_with_pair"],
                 r["IS"]["fills_per_window"], r["OOS"]["fills_per_window"]))
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
