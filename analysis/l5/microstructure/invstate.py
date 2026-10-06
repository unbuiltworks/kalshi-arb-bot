"""E2 — is L5's FUTURE INVENTORY STATE more predictable than his next fill?

PREREGISTRATION_OVERNIGHT.md ss1 E2. At each causal second T predict L5's
state at T+10/+30/+60: UP acquired, DOWN acquired, matched, residual and
signed imbalance. Scored in SHARES by MAE and median absolute error against
persistence/no-change and a frozen-rate baseline, with leave-one-window-out.

WHY THIS ENDPOINT EXISTS. If several L5 orders rest at once, the counterparty
chooses WHICH fills next, so the next individual fill can be unpredictable
while the inventory he is working toward is systematic. Next-fill prediction
and this are different questions and both are reported.

ALL FILLS COUNT TOWARD INVENTORY, including the legs of transaction-level
$1.00 par pairs: they are real acquisitions. They are flagged so their
contribution can be separated, but they are not excluded from the state.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

HORIZONS = (10, 30, 60)
MIN_N = 100


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def inv_series(fills: List[dict], start: int, end: int
               ) -> Dict[int, Dict[str, float]]:
    """Cumulative acquired state at every second of the window."""
    by_s: Dict[int, List[dict]] = defaultdict(list)
    for f in fills:
        by_s[int(f["t"])].append(f)
    up = dn = 0.0
    out: Dict[int, Dict[str, float]] = {}
    for t in range(start, end + 1):
        for f in by_s.get(t, []):
            if f["outcome"] == "up":
                up += f["quantity"]
            else:
                dn += f["quantity"]
        out[t] = {"up": up, "down": dn, "matched": min(up, dn),
                  "residual": abs(up - dn), "imbalance": up - dn,
                  "total": up + dn}
    return out


def build_states(slug: str, fills: List[dict], start: int, end: int,
                 window_s: int) -> List[Dict[str, Any]]:
    inv = inv_series(fills, start, end)
    if not fills:
        return []
    t0 = min(int(f["t"]) for f in fills)
    rows: List[Dict[str, Any]] = []
    for t in range(t0, end - max(HORIZONS) + 1):
        cur = inv[t]
        # realized acquisition over the preceding 60s -- causal
        prev = inv.get(t - 60, {"total": 0.0, "up": 0.0, "down": 0.0})
        r = {
            "market_slug": slug, "t": t,
            "secs_after_open": t - start,
            "tte_s": end - t,
            "tte_frac": round((end - t) / window_s, 4),
            "up": cur["up"], "down": cur["down"],
            "matched": cur["matched"], "residual": cur["residual"],
            "imbalance": cur["imbalance"], "total": cur["total"],
            "rate_60s_total": round(cur["total"] - prev["total"], 4),
            "rate_60s_up": round(cur["up"] - prev["up"], 4),
            "rate_60s_down": round(cur["down"] - prev["down"], 4),
        }
        for h in HORIZONS:
            nx = inv.get(t + h)
            if nx is None:
                r["y%d_up" % h] = None
                continue
            r["y%d_up" % h] = round(nx["up"] - cur["up"], 4)
            r["y%d_down" % h] = round(nx["down"] - cur["down"], 4)
            r["y%d_total" % h] = round(nx["total"] - cur["total"], 4)
            r["y%d_matched" % h] = round(nx["matched"] - cur["matched"], 4)
            r["y%d_residual" % h] = round(nx["residual"] - cur["residual"], 4)
            r["y%d_imbalance" % h] = round(nx["imbalance"] - cur["imbalance"],
                                           4)
        rows.append(r)
    return rows


def median(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def fit(train: List[dict], h: int) -> Dict[str, Any]:
    """Training-fold constants only -- never touched by the held-out window."""
    tot = [r["y%d_total" % h] for r in train if r.get("y%d_total" % h)
           is not None]
    by_tte: Dict[int, List[float]] = defaultdict(list)
    for r in train:
        v = r.get("y%d_total" % h)
        if v is None:
            continue
        by_tte[int(r["tte_frac"] * 5)].append(v)
    # share of the horizon's acquisition that lands on UP
    sh = []
    for r in train:
        a, b = r.get("y%d_up" % h), r.get("y%d_total" % h)
        if a is not None and b:
            sh.append(a / b)
    return {"rate_med": median(tot) or 0.0,
            "by_tte": {k: median(v) or 0.0 for k, v in by_tte.items()},
            "up_share": median(sh) if sh else 0.5}


def predict(r: Dict[str, Any], h: int, c: Dict[str, Any], model: str
            ) -> float:
    if model == "B1_persistence":
        return 0.0
    if model == "B2_frozen_rate":
        return c["rate_med"]
    if model == "M1_own_recent_rate":
        return r["rate_60s_total"] * (h / 60.0)
    if model == "M2_tte_conditional_rate":
        return c["by_tte"].get(int(r["tte_frac"] * 5), c["rate_med"])
    return 0.0


MODELS = ("B1_persistence", "B2_frozen_rate", "M1_own_recent_rate",
          "M2_tte_conditional_rate")


def evaluate(by_w: Dict[str, List[dict]]) -> Dict[str, Any]:
    wins = sorted(by_w)
    out: Dict[str, Any] = {"horizons": {}}
    for h in HORIZONS:
        preds: List[Dict[str, Any]] = []
        for held in wins:
            tr = [r for w in wins if w != held for r in by_w[w]
                  if r.get("y%d_total" % h) is not None]
            if len(tr) < 50:
                continue
            c = fit(tr, h)
            for r in by_w[held]:
                y = r.get("y%d_total" % h)
                if y is None:
                    continue
                row = {"market_slug": held, "y": y, "t": r["t"]}
                for m in MODELS:
                    row[m] = predict(r, h, c, m)
                preds.append(row)
        if len(preds) < MIN_N:
            out["horizons"][str(h)] = {"n": len(preds),
                                       "UNDERPOWERED": True}
            continue
        res: Dict[str, Any] = {"n": len(preds), "UNDERPOWERED": False,
                               "models": {}}
        for m in MODELS:
            ae = [abs(p[m] - p["y"]) for p in preds]
            res["models"][m] = {"MAE": round(sum(ae) / len(ae), 3),
                                "median_AE": round(median(ae), 3)}
        base = min(res["models"][b]["MAE"] for b in
                   ("B1_persistence", "B2_frozen_rate"))
        best_m = min(("M1_own_recent_rate", "M2_tte_conditional_rate"),
                     key=lambda m: res["models"][m]["MAE"])
        res["best_baseline_MAE"] = round(base, 3)
        res["best_model"] = best_m
        res["best_model_MAE"] = res["models"][best_m]["MAE"]
        res["improvement_vs_best_baseline"] = round(
            1 - res["models"][best_m]["MAE"] / base, 4)
        res["MEETS_20PCT_BAR"] = res["improvement_vs_best_baseline"] >= 0.20
        # per-window reproducibility
        byw: Dict[str, List[dict]] = defaultdict(list)
        for p in preds:
            byw[p["market_slug"]].append(p)
        wins_better = 0
        per: Dict[str, Any] = {}
        for w, ps in sorted(byw.items()):
            bm = sum(abs(p[best_m] - p["y"]) for p in ps) / len(ps)
            bb = min(sum(abs(p[b] - p["y"]) for p in ps) / len(ps)
                     for b in ("B1_persistence", "B2_frozen_rate"))
            per[w] = {"n": len(ps), "model_MAE": round(bm, 3),
                      "baseline_MAE": round(bb, 3),
                      "improvement": round(1 - bm / bb, 4) if bb else None}
            if bb and bm < bb:
                wins_better += 1
        res["windows_where_model_beats_baseline"] = wins_better
        res["windows_total"] = len(byw)
        res["MEETS_8_OF_12_BAR"] = wins_better >= 8
        res["per_window"] = per
        out["horizons"][str(h)] = res
    hs = [v for v in out["horizons"].values() if not v.get("UNDERPOWERED")]
    meets = sum(1 for v in hs if v.get("MEETS_20PCT_BAR")
                and v.get("MEETS_8_OF_12_BAR"))
    out["E2_POSITIVE"] = meets >= 2
    out["horizons_meeting_both_bars"] = meets
    out["bar"] = ("MAE beats the best baseline by >=20% at two of three "
                  "horizons, n>=100 each, improvement reproducing in >=8 of "
                  "12 windows")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)
    ev = jl(a.events)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    states: Dict[str, List[dict]] = {}
    for slug, fl in sorted(byw.items()):
        start = int(slug.rsplit("-", 1)[1])
        s = build_states(slug, fl, start, start + a.window_s, a.window_s)
        if s:
            states[slug] = s
    rep = {"endpoint": "E2 future inventory state",
           "protocol": "PREREGISTRATION_OVERNIGHT.md",
           "contract_length": a.contract_length,
           "n_windows": len(states),
           "n_states": sum(len(v) for v in states.values()),
           "par_pair_legs_included_in_inventory": True,
           **evaluate(states)}
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "INVSTATE_%s.json"
                           % a.contract_length), "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: (v if k != "horizons" else
                          {hk: {kk: vv for kk, vv in hv.items()
                                if kk != "per_window"}
                           for hk, hv in v.items()})
                      for k, v in rep.items()}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
