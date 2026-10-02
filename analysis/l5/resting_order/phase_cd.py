#!/usr/bin/env python3
"""Phase C/D: can an L5 maker fill be anticipated from causally-prior state?

Design note that matters: snapshots are taken on a FIXED TIME GRID, not at fill
times. Sampling only at fills would condition on the event we are trying to
predict and would make "a fill happens" trivially true. The grid gives genuine
negatives.

Targets come from the canonical corpus (L5's own fills, with maker labels).
Features come from the PUBLIC market-wide taker-indexed trade tape plus L5's own
PRIOR fills. Leakage gate in one place: strictly `t < tau`.
Settlement never enters a feature.
"""
from __future__ import annotations
import csv, gzip, json, math, collections, statistics as st, random

L5 = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68".lower()
STEP = 5
HORIZONS = [60, 30, 15, 10, 5, 2]
WARMUP = 30          # need some prior state before the first snapshot

man = json.load(open("data/l5_market_tape/manifest.json"))
R = list(csv.DictReader(gzip.open("analysis/l5/l5_canonical_sequence.csv.gz", "rt")))
byw = collections.defaultdict(list)
for r in R:
    byw[r["window_id"]].append(r)


def isup(r): return r["outcome"].lower().startswith("up")


def build():
    rows = []
    for m in man["markets"]:
        wid = m["window_id"]
        tape = [json.loads(l) for l in gzip.open(f"data/l5_market_tape/{wid}.jsonl.gz", "rt")]
        for t in tape:
            t["t"] = int(t["timestamp"]); t["p"] = float(t["price"])
            t["q"] = float(t["size"]); t["buy"] = (t.get("side") or "").upper() == "BUY"
            t["up"] = (t.get("outcome") or "").lower().startswith("up")
        tape.sort(key=lambda x: x["t"])
        fills = sorted(byw[wid], key=lambda r: int(r["trade_seq"]))
        for fr in fills:
            fr["t"] = int(float(fr["timestamp"])); fr["p"] = float(fr["price"])
            fr["q"] = float(fr["quantity"]); fr["up"] = isup(fr)
        mk = [f for f in fills if f["maker_taker"] == "maker"]
        if not mk:
            continue
        t0 = min(f["t"] for f in fills); t1 = max(f["t"] for f in fills)
        ti = tj = 0
        for tau in range(t0 + WARMUP, t1 + 1, STEP):
            pri = [x for x in tape if x["t"] < tau]
            l5p = [f for f in fills if f["t"] < tau]
            if len(pri) < 10 or not l5p:
                continue
            # ---- features (H1 + H3 only; no H2 band control; no settlement)
            up = sum(f["q"] for f in l5p if f["up"]); dn = sum(f["q"] for f in l5p if not f["up"])
            ups = sum(f["q"]*f["p"] for f in l5p if f["up"]); dns = sum(f["q"]*f["p"] for f in l5p if not f["up"])
            upb = ups/up if up else None; dnb = dns/dn if dn else None
            w60 = [x for x in pri if x["t"] >= tau-60]
            lastp = pri[-1]["p"]
            prev = l5p[-1]
            run = 0
            for f in reversed(l5p):
                if f["up"] == prev["up"]: run += 1
                else: break
            resid = up - dn
            heldup = resid > 0
            heldb = upb if heldup else dnb
            # complement price proxy for the held side, from the tape
            comp = (1.0 - lastp) if heldup else lastp
            paircost = (heldb + comp) if heldb is not None else None
            f_ = {
                "residual": resid, "abs_resid": abs(resid), "matched": min(up, dn),
                "imbalance": abs(resid)/(up+dn) if (up+dn) else 0.0,
                "held_basis": heldb, "comp_px_proxy": comp, "pair_cost_now": paircost,
                "last_px": lastp, "prev_l5_px": prev["p"],
                "prev_l5_up": 1.0 if prev["up"] else 0.0,
                "px_vs_held_basis": (lastp - heldb) if heldb is not None else None,
                "run": run, "dt_since_l5": tau - prev["t"],
                "t_in_window": tau - t0,
                "flow_n_60": len(w60),
                "flow_buyfrac_60": (sum(1 for x in w60 if x["buy"])/len(w60)) if w60 else None,
                "flow_upfrac_60": (sum(1 for x in w60 if x["up"])/len(w60)) if w60 else None,
                "wallets_60": len({x["proxyWallet"] for x in w60}),
                "px_std_60": st.pstdev([x["p"] for x in w60]) if len(w60) > 2 else None,
                "px_chg_60": ([x["p"] for x in w60][-1]-[x["p"] for x in w60][0]) if len(w60) > 1 else None,
            }
            for H in HORIZONS:
                nxt = [f for f in mk if tau < f["t"] <= tau + H]
                rows.append({"market": m["slug"][-10:], "tau": tau, "H": H,
                             "y_any": 1 if nxt else 0,
                             "y_up": (1 if nxt[0]["up"] else 0) if nxt else None,
                             "y_px": nxt[0]["p"] if nxt else None,
                             "y_q": nxt[0]["q"] if nxt else None,
                             "y_lead": (nxt[0]["t"]-tau) if nxt else None,
                             **f_})
    return rows


rows = build()
json.dump(rows, gzip.open("analysis/l5/resting_order/phase_cd_rows.json.gz", "wt"))
print(f"snapshots: {len(rows):,} over {len({r['market'] for r in rows})} markets, "
      f"{len({(r['market'],r['tau']) for r in rows}):,} distinct grid points\n")

print("="*86)
print("PHASE C -- BEHAVIORAL FIDELITY: anticipating the NEXT L5 MAKER FILL")
print("="*86)
print(f"{'H':>4} {'n':>7} {'P(fill in H)':>13} {'side: persist':>14} {'side: flow':>11} "
      f"{'|px err| vs last':>17} {'|px err| vs prevL5':>19} {'lead p50':>9}")
for H in HORIZONS:
    g = [r for r in rows if r["H"] == H]
    pos = [r for r in g if r["y_any"] == 1]
    if not pos: continue
    # side baselines, both causally available
    persist = st.mean(1.0 if r["y_up"] == r["prev_l5_up"] else 0.0 for r in pos)
    flow = st.mean(1.0 if (r["y_up"] == (1 if (r["flow_upfrac_60"] or .5) > .5 else 0)) else 0.0
                   for r in pos if r["flow_upfrac_60"] is not None)
    e_last = st.median(abs(r["y_px"] - r["last_px"]) for r in pos)
    e_prev = st.median(abs(r["y_px"] - r["prev_l5_px"]) for r in pos)
    lead = st.median(r["y_lead"] for r in pos)
    print(f"{H:4d} {len(g):7,d} {len(pos)/len(g):13.3f} {persist:14.3f} {flow:11.3f} "
          f"{e_last:17.4f} {e_prev:19.4f} {lead:9.1f}s")
print("\n  side baselines are PERSISTENCE (same side as L5's previous fill) and FLOW")
print("  (majority Up-side share of market takes in the last 60s). Both use only prior data.")

print("\n" + "="*86)
print("PRICE ANTICIPATION: how close is the best causally-available price guess?")
print("="*86)
g = [r for r in rows if r["H"] == 30 and r["y_any"] == 1]
for nm, fn in (("last market trade px", lambda r: r["last_px"]),
               ("L5's previous fill px", lambda r: r["prev_l5_px"]),
               ("L5's held-side basis", lambda r: r["held_basis"])):
    e = [abs(r["y_px"] - fn(r)) for r in g if fn(r) is not None]
    e.sort()
    print(f"  {nm:24s} n={len(e):5,d}  |err| p50={e[len(e)//2]:.4f} "
          f"p90={e[int(len(e)*.9)]:.4f}  within 1 tick={sum(1 for x in e if x<=0.0101)/len(e):6.1%} "
          f" within 2 ticks={sum(1 for x in e if x<=0.0201)/len(e):6.1%}")
print("\n  'within 1 tick' is the operative number: it is the share of cases where resting at")
print("  that guess would have been L5's own resting price to the cent.")
