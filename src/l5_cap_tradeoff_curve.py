"""OWNER 5569709706 #6 -- the residual-cap tradeoff curve:
    cap -> L5 action fidelity -> completed-pair dollars -> incentive dollars
        -> residual variance / loss -> worst-case exposure.

ONE RULE, the R4 rule (l5_r4_counterfactual): an order that would raise
unmatched exposure above the cap is refused; an order that reduces it is
always allowed. Nothing else changes.

TWO REPLAYS.
  A. L5's canonical windows. The cap is expressed in ACTION UNITS -- one
     unit = the window's own median BUY notional -- because that is what
     R4's cap is (USD 2.50 cap over a USD 2.50 action = 1 unit). Each
     window is replayed under k = 1, 1.5, 2, 3, 4 and uncapped, settled at
     the window's ACTUAL outcome.
  B. Our own fills: R3L (the venue's fill rows in l5_r4_counterfactual)
     and R4 (analysis/l5/r4_orders.jsonl, filled orders), cap = k x USD
     2.50, settled at the venue's actual resolutions.

PER CAP: fidelity (share of L5's actions kept; relation-mix L1 against the
uncapped mix), matched shares and derived pair dollars, incentive dollars,
residual P&L with its loss distribution, peak unmatched exposure (the
worst case), and the owner's objective as a ratio: (pair dollars +
incentive dollars) per dollar of peak residual.

INCENTIVE DOLLARS are ESTIMATED from the venue's published maker rate
(0.0125 x q x p x (1-p), banker's rounding to the cent per fill), because a
counterfactual fill has no venue credit. The estimate is checked against
what the venue actually credited on our R4 fills (USD 0.14 on 9 fills)
and the check is reported; nothing here is a realized number.

LIMITS. Public fills only: what L5 would have done AFTER a refused fill is
unknowable, so the replay keeps its later fills as they were (a complement
that no longer has a surplus to match becomes new exposure and is judged
by the same cap). That is the same simplification the R3L counterfactual
used and it biases every capped variant the same way.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from decimal import ROUND_HALF_EVEN, Decimal as D
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.l5_concurrency_study import CANONICAL_PATH, load_rows, provenance
from src.l5_r4_counterfactual import R3L_FILLS, R3L_RESOLUTION, cost
from src.l5_residual_risk import _f, _lane, dist

OUT_PATH = os.path.join("analysis", "l5", "l5_cap_tradeoff_curve.json")
R4_ORDERS_PATH = os.path.join("analysis", "l5", "r4_orders.jsonl")
CAP_UNITS: Tuple[Optional[float], ...] = (None, 1.0, 1.5, 2.0, 3.0, 4.0)
R4_ACTION_USD = D("2.50")
MAKER_RATE = D("0.0125")
CENT = D("0.01")
R4_RESOLUTION = {"aec-atpcq-aleher-tundur-2026-09-07": "YES",   # LONG resolved
                 "aec-atpcq-norgom-ollwal-2026-09-07": "NO"}    # settlement 0.0000
R4_OBSERVED_REBATE_USD = D("0.14")


def rebate_estimate(q: D, p: D) -> D:
    """Venue's published maker rebate for one fill, cent-rounded (banker's)."""
    return (MAKER_RATE * q * p * (D(1) - p)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def replay(fills: Sequence[tuple], cap_usd: Optional[D], resolution: Optional[str]) -> dict:
    """fills: (t, side YES/NO, yes_price, qty). Same refusal rule as R4."""
    qty = {"YES": D(0), "NO": D(0)}
    spend = {"YES": D(0), "NO": D(0)}
    kept, refused = 0, 0
    rel_kept: Counter = Counter()
    rel_all: Counter = Counter()
    peak = D(0)
    rebate = D(0)

    def unmatched() -> tuple:
        long_side = "YES" if qty["YES"] >= qty["NO"] else "NO"
        n = abs(qty["YES"] - qty["NO"])
        avg = (spend[long_side] / qty[long_side]) if qty[long_side] else D(0)
        return long_side, n, n * avg

    for _t, side, px, q in fills:
        px, q = D(str(px)), D(str(q))
        long_side, n_before, before = unmatched()
        if n_before == 0:
            rel = "open"
        elif side == long_side:
            rel = "extend"
        else:
            rel = "complete"
        rel_all[rel] += 1
        qty[side] += q
        spend[side] += q * cost(side, px)
        _, _, after = unmatched()
        if cap_usd is not None and after > before and after > cap_usd:
            qty[side] -= q
            spend[side] -= q * cost(side, px)
            refused += 1
            continue
        kept += 1
        rel_kept[rel] += 1
        peak = max(peak, after)
        rebate += rebate_estimate(q, cost(side, px))

    long_side, resid_qty, resid_usd = unmatched()
    matched = min(qty["YES"], qty["NO"])
    total = spend["YES"] + spend["NO"]
    pair_cost = ((total - resid_usd) / matched) if matched else None
    pair_pnl = (matched * (D(1) - pair_cost)) if matched else D(0)
    if resid_qty == 0 or resolution is None:
        resid_pnl = D(0)
    elif long_side == resolution:
        resid_pnl = resid_qty - resid_usd
    else:
        resid_pnl = -resid_usd
    return {
        "fills": kept + refused, "kept": kept, "refused": refused,
        "relation_all": dict(rel_all), "relation_kept": dict(rel_kept),
        "spent_usd": total, "matched_qty": matched,
        "pair_cost": pair_cost, "pair_pnl_usd": pair_pnl,
        "rebate_est_usd": rebate,
        "peak_unmatched_usd": peak, "final_unmatched_usd": resid_usd,
        "residual_pnl_usd": resid_pnl,
    }


# ------------------------------ L5 windows ------------------------------

def window_fills(rows: Sequence[dict]) -> Tuple[List[tuple], Optional[str], D]:
    rs = sorted(rows, key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
    fills = []
    for r in rs:
        if str(r.get("side") or "").upper() != "BUY":
            continue
        price = D(str(r["price"]))
        if str(r.get("outcome")) == "Up":
            fills.append((r["timestamp"], "YES", price, D(str(r["quantity"]))))
        else:
            fills.append((r["timestamp"], "NO", D(1) - price, D(str(r["quantity"]))))
    outcome = str(rs[-1].get("settlement_outcome") or "")
    resolution = "YES" if outcome == "Up" else ("NO" if outcome == "Down" else None)
    notionals = sorted(D(str(r["dollars"])) for r in rs if str(r.get("side") or "").upper() == "BUY")
    n = len(notionals)
    unit = (notionals[n // 2] if n % 2 else (notionals[n // 2 - 1] + notionals[n // 2]) / 2) if n else D(0)
    return fills, resolution, unit


def _l1(a: Counter, b: Counter) -> Optional[float]:
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return None
    keys = set(a) | set(b)
    return round(sum(abs(a[k] / na - b[k] / nb) for k in keys), 4)


def aggregate(results: Sequence[dict], cap_label: str) -> dict:
    fills = sum(r["fills"] for r in results)
    kept = sum(r["kept"] for r in results)
    rel_all: Counter = Counter()
    rel_kept: Counter = Counter()
    for r in results:
        rel_all.update(r["relation_all"])
        rel_kept.update(r["relation_kept"])
    pair = sum(r["pair_pnl_usd"] for r in results)
    reb = sum(r["rebate_est_usd"] for r in results)
    resid = [r["residual_pnl_usd"] for r in results]
    losses = [float(x) for x in resid if x < 0]
    peaks = [float(r["peak_unmatched_usd"]) for r in results]
    peak_sum = sum(r["peak_unmatched_usd"] for r in results)
    income = pair + reb
    return {
        "cap": cap_label,
        "fills": fills, "kept": kept, "refused": fills - kept,
        "fidelity_kept_share": round(kept / fills, 4) if fills else None,
        "relation_mix_kept": {k: round(v / kept, 4) for k, v in sorted(rel_kept.items())} if kept else {},
        "relation_mix_l1_vs_uncapped": _l1(rel_kept, rel_all),
        "spent_usd": str(sum(r["spent_usd"] for r in results).quantize(D("0.01"))),
        "matched_qty": str(sum(r["matched_qty"] for r in results).quantize(D("0.01"))),
        "pair_pnl_usd": str(pair.quantize(D("0.01"))),
        "rebate_est_usd": str(reb.quantize(D("0.01"))),
        "residual_pnl_usd": str(sum(resid).quantize(D("0.01"))),
        "residual_loss_usd": dist(losses, 2),
        "residual_pnl_per_window_usd": dist([float(x) for x in resid], 2),
        "peak_unmatched_usd": dist(peaks, 2),
        "worst_case_peak_unmatched_usd": round(max(peaks), 2) if peaks else None,
        "total_pnl_usd": str((income + sum(resid)).quantize(D("0.01"))),
        "income_per_dollar_of_peak_residual": (
            round(float(income / peak_sum), 4) if peak_sum else None),
        "labels": "pair_pnl derived from fills; rebate ESTIMATED at the published "
                  "rate; residual settled at the actual outcome; nothing realized",
    }


def l5_curve(rows: Optional[Sequence[dict]] = None, path: str = CANONICAL_PATH,
             caps: Sequence[Optional[float]] = CAP_UNITS) -> dict:
    rows = list(rows) if rows is not None else load_rows(path)
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    prepared = []
    for wid, rs in by_win.items():
        fills, resolution, unit = window_fills(rs)
        if not fills or unit <= 0:
            continue
        last = rs[-1]
        prepared.append((wid, fills, resolution, unit,
                         {"asset": last.get("asset", ""),
                          "duration_minutes": int(_f(last.get("duration_minutes")))}))
    out = {"windows": len(prepared), "curve": [], "by_lane": {}}
    lane_results: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for k in caps:
        label = "uncapped" if k is None else f"{k}_units"
        res = []
        for wid, fills, resolution, unit, meta in prepared:
            r = replay(fills, None if k is None else unit * D(str(k)), resolution)
            res.append(r)
            lane_results[_lane(meta)][label].append(r)
        out["curve"].append(aggregate(res, label))
    for lane, per_cap in sorted(lane_results.items()):
        out["by_lane"][lane] = [aggregate(v, label) for label, v in per_cap.items()]
    return out


# ------------------------------ our fills ------------------------------

def r4_fills(path: str = R4_ORDERS_PATH) -> Dict[str, List[tuple]]:
    out: Dict[str, List[tuple]] = defaultdict(list)
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows.sort(key=lambda o: (o.get("log_ts") or "", int(str(o.get("stage") or "_0").rsplit("_", 1)[-1] or 0)))
    for o in rows:
        q = D(str(o.get("filled_qty") or 0))
        if q <= 0:
            continue
        side = str(o.get("side") or "").upper()
        px = D(str(o.get("px")))
        yes_px = px if side == "YES" else D(1) - px
        out[o["slug"]].append((o.get("log_ts"), side, yes_px, q))
    return out


def our_curve(caps: Sequence[Optional[float]] = CAP_UNITS,
              r4_path: str = R4_ORDERS_PATH) -> dict:
    markets = {}
    for slug, fills in R3L_FILLS.items():
        markets[slug] = ([(t, s, D(p), D(q)) for t, s, p, q in fills], R3L_RESOLUTION[slug], "R3L")
    for slug, fills in r4_fills(r4_path).items():
        markets[slug] = (fills, R4_RESOLUTION.get(slug), "R4")
    out = {"markets": sorted(markets), "curve": [], "per_market": {}}
    for k in caps:
        label = "uncapped" if k is None else f"{k}_units"
        cap = None if k is None else R4_ACTION_USD * D(str(k))
        res = []
        for slug, (fills, resolution, cohort) in sorted(markets.items()):
            r = replay(fills, cap, resolution)
            r["cohort"] = cohort
            res.append(r)
            out["per_market"].setdefault(slug, {})[label] = {
                "cohort": cohort, "kept": r["kept"], "refused": r["refused"],
                "pair_pnl_usd": str(r["pair_pnl_usd"].quantize(D("0.0001"))),
                "rebate_est_usd": str(r["rebate_est_usd"]),
                "residual_pnl_usd": str(r["residual_pnl_usd"].quantize(D("0.0001"))),
                "peak_unmatched_usd": str(r["peak_unmatched_usd"].quantize(D("0.0001"))),
            }
        agg = aggregate(res, label)
        agg["cap_usd"] = str(cap) if cap is not None else None
        out["curve"].append(agg)
    # the estimate against the venue's credits on the R4 fills (uncapped)
    r4_est = sum(replay(f, None, R4_RESOLUTION.get(s))["rebate_est_usd"]
                 for s, (f, _r, c) in markets.items() if c == "R4")
    out["rebate_estimate_check"] = {
        "r4_fills_estimated_usd": str(r4_est),
        "r4_fills_venue_credited_usd": str(R4_OBSERVED_REBATE_USD),
        "note": "the venue rounds per PRINT (a 5.10 fill printed as 3.72 + 1.38 "
                "credits 0.01, the whole-fill estimate says 0.02); the estimate "
                "is an upper bound at this size",
    }
    return out


def study(rows: Optional[Sequence[dict]] = None, path: str = CANONICAL_PATH,
          r4_path: str = R4_ORDERS_PATH) -> dict:
    rows = list(rows) if rows is not None else load_rows(path)
    prov = provenance(rows)
    prov["limits"] = list(prov["limits"]) + [
        "cap in ACTION UNITS = multiples of the window's own median BUY notional; "
        "R4's cap is 1 unit",
        "refusal rule identical to R4: exposure-increasing fills above the cap are "
        "refused, exposure-reducing fills always allowed",
        "L5's behaviour after a refused fill is unknowable; later fills are kept as "
        "they were (same simplification as the R3L counterfactual)",
        "incentive dollars ESTIMATED at the published maker rate, cent-rounded per "
        "fill; checked against the venue's R4 credits",
    ]
    return {
        "owner_direction": "#167 5569709706 item 6",
        "objective": "maximize completed-pair + venue-credited incentive dollars per "
                     "dollar of residual risk, while preserving L5's sequential "
                     "decision behavior",
        "provenance": prov,
        "l5_windows": l5_curve(rows),
        "our_fills": our_curve(r4_path=r4_path),
        "r4_cap_in_units": 1.0,
        "r4_action_usd": str(R4_ACTION_USD),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_PATH)
    a = ap.parse_args(argv)
    out = study()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    for c in out["l5_windows"]["curve"]:
        print("L5", c["cap"], "kept", c["fidelity_kept_share"], "L1", c["relation_mix_l1_vs_uncapped"],
              "pair", c["pair_pnl_usd"], "reb", c["rebate_est_usd"], "resid", c["residual_pnl_usd"],
              "total", c["total_pnl_usd"], "peak p50/p90/max", c["peak_unmatched_usd"].get("p50"),
              c["peak_unmatched_usd"].get("p90"), c["worst_case_peak_unmatched_usd"],
              "income/peak", c["income_per_dollar_of_peak_residual"])
    for c in out["our_fills"]["curve"]:
        print("OURS", c["cap"], c.get("cap_usd"), "kept", c["fidelity_kept_share"], "pair", c["pair_pnl_usd"],
              "reb", c["rebate_est_usd"], "resid", c["residual_pnl_usd"], "total", c["total_pnl_usd"],
              "worst", c["worst_case_peak_unmatched_usd"], "income/peak", c["income_per_dollar_of_peak_residual"])
    print("rebate check", out["our_fills"]["rebate_estimate_check"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
