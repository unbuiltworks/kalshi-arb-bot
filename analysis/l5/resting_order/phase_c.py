#!/usr/bin/env python3
"""Phase C/D: can L5's maker fill be anticipated from causally-prior state?

Per PREREGISTRATION.md. Leakage rules enforced in one place (`prior`): a feature
may use only records with timestamp STRICTLY LESS THAN the horizon; ties at the
horizon second are excluded because intra-second order is unknown.
Settlement never enters a feature.
"""
from __future__ import annotations
import csv, glob, gzip, json, math, collections, statistics as st, os

L5 = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68".lower()
HOR = [60, 30, 15, 10, 5, 2]
TAPE = "data/l5_market_tape"
TICK = 0.01
SPAN = (1787875677, 1788217879)   # takerOnly coverage span

# ---- taker fingerprints -> confidence classes
import sys
sys.path.insert(0, ".")
from src.polymarket_l5.normalize import normalize_trade_item, fingerprint
taker_fps = set()
for l in open("data/l5/raw/trades_takeronly.jsonl"):
    for it in (json.loads(l).get("payload") or []):
        r = normalize_trade_item(it, "trades_takeronly")
        if r is not None:
            taker_fps.add(fingerprint(r))


def load_market(path):
    rows = [json.loads(l) for l in gzip.open(path, "rt")]
    for r in rows:
        r["t"] = int(r["timestamp"]); r["p"] = float(r["price"])
        r["q"] = float(r["size"]); r["w"] = r["proxyWallet"].lower()
        r["up"] = (r.get("outcome") or "").lower().startswith("up")
        r["buy"] = (r.get("side") or "").upper() == "BUY"
    rows.sort(key=lambda r: r["t"])
    return rows


def prior(rows, horizon_t):
    """LEAKAGE GATE: strictly-before only; horizon-second ties excluded."""
    return [r for r in rows if r["t"] < horizon_t]


def classify(fill_row):
    fp = fingerprint({"transaction_hash": fill_row.get("transactionHash", ""),
                      "timestamp": fill_row["t"], "condition_id": fill_row.get("conditionId", ""),
                      "token_id": fill_row.get("asset", ""),
                      "side": fill_row.get("side", ""), "price": fill_row["p"],
                      "shares": fill_row["q"]})
    if fp in taker_fps:
        return "HIGH_CONFIDENCE_TAKER"
    if SPAN[0] <= fill_row["t"] <= SPAN[1]:
        return "INFERRED_MAKER"
    return "UNKNOWN"


def features(pri, l5_pri, wstart, h_t):
    """All H1/H3 inputs. No H2 inventory-band control. No settlement."""
    f = {}
    up = sum(r["q"] for r in l5_pri if r["up"])
    dn = sum(r["q"] for r in l5_pri if not r["up"])
    upsp = sum(r["q"] * r["p"] for r in l5_pri if r["up"])
    dnsp = sum(r["q"] * r["p"] for r in l5_pri if not r["up"])
    f["up_qty"], f["dn_qty"] = up, dn
    f["matched"] = min(up, dn)
    f["residual"] = up - dn
    f["up_basis"] = upsp / up if up else None
    f["dn_basis"] = dnsp / dn if dn else None
    # touch PROXY from the market tape -- declared proxy, not a bid/ask
    lb = [r for r in pri if r["buy"]]
    ls = [r for r in pri if not r["buy"]]
    f["last_buy_px"] = lb[-1]["p"] if lb else None
    f["last_sell_px"] = ls[-1]["p"] if ls else None
    f["last_px"] = pri[-1]["p"] if pri else None
    # complement / pair cost if completed now, at the proxy
    f["pair_cost_now"] = None
    if f["last_px"] is not None:
        if up > dn and f["up_basis"] is not None:
            f["pair_cost_now"] = f["up_basis"] + (1.0 - f["last_px"])
        elif dn > up and f["dn_basis"] is not None:
            f["pair_cost_now"] = f["dn_basis"] + f["last_px"]
    w60 = [r for r in pri if r["t"] >= h_t - 60]
    f["flow_n_60"] = len(w60)
    f["flow_buyfrac_60"] = (sum(1 for r in w60 if r["buy"]) / len(w60)) if w60 else None
    f["flow_upfrac_60"] = (sum(1 for r in w60 if r["up"]) / len(w60)) if w60 else None
    f["wallets_60"] = len({r["w"] for r in w60})
    px = [r["p"] for r in w60]
    f["px_std_60"] = st.pstdev(px) if len(px) > 2 else None
    f["px_chg_60"] = (px[-1] - px[0]) if len(px) > 1 else None
    f["t_in_window"] = h_t - wstart
    if l5_pri:
        f["dt_since_l5"] = h_t - l5_pri[-1]["t"]
        f["l5_prev_up"] = 1.0 if l5_pri[-1]["up"] else 0.0
        f["l5_prev_px"] = l5_pri[-1]["p"]
        run = 0
        for r in reversed(l5_pri):
            if r["up"] == l5_pri[-1]["up"]: run += 1
            else: break
        f["l5_run"] = run
    else:
        f["dt_since_l5"] = None; f["l5_prev_up"] = None
        f["l5_prev_px"] = None; f["l5_run"] = 0
    return f


def main():
    paths = sorted(glob.glob(f"{TAPE}/*.jsonl.gz"))
    print(f"markets harvested: {len(paths)}")
    rows_out = []
    cls_counts = collections.Counter()
    for p in paths:
        rows = load_market(p)
        if not rows: continue
        wstart = min(r["t"] for r in rows)
        l5rows = [r for r in rows if r["w"] == L5]
        for fr in l5rows:
            k = classify(fr)
            cls_counts[k] += 1
            if k != "INFERRED_MAKER":      # only candidate maker fills are targets
                continue
            for h in HOR:
                h_t = fr["t"] - h
                pri = prior(rows, h_t)
                l5_pri = [r for r in pri if r["w"] == L5]
                if not pri:
                    continue
                f = features(pri, l5_pri, wstart, h_t)
                rows_out.append({"market": os.path.basename(p)[:12], "horizon": h,
                                 "target_t": fr["t"], "target_up": fr["up"],
                                 "target_px": fr["p"], "target_q": fr["q"],
                                 "n_prior": len(pri), "n_l5_prior": len(l5_pri), **f})
    print(f"L5 fill label classes in sample: {dict(cls_counts)}")
    json.dump(rows_out, gzip.open("analysis/l5/resting_order/phase_c_rows.json.gz", "wt"))
    print(f"snapshot rows: {len(rows_out):,} over "
          f"{len({(r['market'],r['target_t']) for r in rows_out}):,} target fills")
    return rows_out


if __name__ == "__main__":
    main()
