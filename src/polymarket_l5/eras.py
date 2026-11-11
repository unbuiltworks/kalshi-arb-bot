"""Era segmentation + temporal-pairing analysis over the L5 ledger.

Reads the ledger artifacts (l5_ledger.json + l5_ledger_windows.csv.gz) and
answers the development questions:

  * weekly economics: trading cash vs incentive income vs total, per dollar
    of turnover — the honest "did he get better" series;
  * behavior series per week: size, pair cost, first-leg price/TTE,
    opposite-side timing, residual share, asset/horizon mix;
  * the temporal-pairing test: are sub-$1 pairs manufactured by LEGGING IN
    OVER TIME (buy one side, wait, buy the other cheaper) rather than by
    hitting simultaneous arb? Answered by bucketing windows on the
    first-leg -> first-opposite-fill gap and comparing achieved pair cost.

Pure offline analysis of committed artifacts. Research only.

    python -m src.polymarket_l5.eras
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config

IN_JSON = config.OUTPUT_DIR / "l5_ledger.json"
IN_WINDOWS = config.OUTPUT_DIR / "l5_ledger_windows.csv.gz"
OUT_JSON = config.OUTPUT_DIR / "l5_eras.json"

GAP_BUCKETS = (
    ("simultaneous_le2s", 0, 2),
    ("fast_2_30s", 2, 30),
    ("legged_30_120s", 30, 120),
    ("legged_gt120s", 120, 10 ** 9),
)


def _f(row, key) -> Optional[float]:
    v = row.get(key, "")
    if v in ("", None, "None"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def load() -> Dict[str, Any]:
    ledger = json.loads(IN_JSON.read_text())
    windows: List[Dict[str, Any]] = []
    with gzip.open(IN_WINDOWS, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            ws = _f(row, "window_start")
            if not ws:
                continue
            windows.append({
                "ts": int(ws),
                "week": datetime.fromtimestamp(int(ws), tz=timezone.utc)
                .strftime("%G-W%V"),
                "asset": row.get("asset", ""),
                "horizon_m": int(_f(row, "horizon_m") or 0),
                "buy_usdc": _f(row, "buy_usdc") or 0.0,
                "net": _f(row, "net_trading_cash") or 0.0,
                "pair_locked": _f(row, "pair_locked") or 0.0,
                "residual": _f(row, "residual_and_timing") or 0.0,
                "pair_cost": _f(row, "pair_cost"),
                "matched_qty": _f(row, "matched_qty") or 0.0,
                "buy_up_qty": _f(row, "buy_up_qty") or 0.0,
                "buy_down_qty": _f(row, "buy_down_qty") or 0.0,
                "first_price": _f(row, "first_price"),
                "first_tte_s": _f(row, "first_tte_s"),
                "gap_s": _f(row, "opposite_gap_s"),
                "n_fills": int(_f(row, "n_fills") or 0),
            })
    windows.sort(key=lambda w: w["ts"])
    return {"ledger": ledger, "windows": windows}


def residual_share(w: Dict[str, Any]) -> Optional[float]:
    tot = w["buy_up_qty"] + w["buy_down_qty"]
    if tot <= 0:
        return None
    return abs(w["buy_up_qty"] - w["buy_down_qty"]) / tot


def weekly(windows: List[Dict[str, Any]], income_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_week: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for w in windows:
        by_week[w["week"]].append(w)
    income_by_week: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in income_rows:
        wk = datetime.fromtimestamp(int(r["ts"]), tz=timezone.utc).strftime("%G-W%V")
        income_by_week[wk][r["type"]] += r["usdc"]

    all_weeks = sorted(set(by_week) | set(income_by_week))
    rows = []
    for wk in all_weeks:
        ws = by_week.get(wk, [])
        turnover = sum(w["buy_usdc"] for w in ws)
        net = sum(w["net"] for w in ws)
        inc = income_by_week.get(wk, {})
        inc_total = sum(inc.values())
        both = [w for w in ws if w["pair_cost"] is not None]
        gaps = [w["gap_s"] for w in both if w["gap_s"] is not None]
        rows.append({
            "week": wk,
            "n_windows": len(ws),
            "turnover": round(turnover, 2),
            "net_trading_cash": round(net, 2),
            "pair_locked": round(sum(w["pair_locked"] for w in ws), 2),
            "residual": round(sum(w["residual"] for w in ws), 2),
            "income": {k: round(v, 2) for k, v in sorted(inc.items())},
            "income_total": round(inc_total, 2),
            "total": round(net + inc_total, 2),
            "trading_per_dollar": round(net / turnover, 4) if turnover else None,
            "total_per_dollar": round((net + inc_total) / turnover, 4) if turnover else None,
            "median_window_buy_usdc": round(_median([w["buy_usdc"] for w in ws]) or 0, 2),
            "median_pair_cost": round(_median([w["pair_cost"] for w in both]) or 0, 4) if both else None,
            "both_sides_pct": round(100 * len(both) / len(ws), 1) if ws else None,
            "median_opposite_gap_s": round(_median(gaps) or 0, 1) if gaps else None,
            "gap_le2s_pct": round(100 * sum(1 for g in gaps if g <= 2) / len(gaps), 1) if gaps else None,
            "median_first_price": round(_median([w["first_price"] for w in ws]) or 0, 3) if ws else None,
            "median_first_tte_s": round(_median([w["first_tte_s"] for w in ws]) or 0, 0) if ws else None,
            "median_residual_share": round(_median([residual_share(w) for w in ws]) or 0, 3) if ws else None,
            "m5_pct": round(100 * sum(1 for w in ws if w["horizon_m"] == 5) / len(ws), 1) if ws else None,
            "btc_pct": round(100 * sum(1 for w in ws if w["asset"] == "BTC") / len(ws), 1) if ws else None,
        })
    return rows


def temporal_pairing(windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Does he manufacture sub-$1 pairs by legging in over time?"""
    both = [w for w in windows
            if w["pair_cost"] is not None and w["gap_s"] is not None]
    out: Dict[str, Any] = {"n_windows_with_both_sides_and_gap": len(both)}
    buckets = {}
    for name, lo, hi in GAP_BUCKETS:
        sel = [w for w in both if lo < w["gap_s"] <= hi] if lo else \
              [w for w in both if w["gap_s"] <= hi]
        if not sel:
            buckets[name] = {"n": 0}
            continue
        pcs = [w["pair_cost"] for w in sel]
        buckets[name] = {
            "n": len(sel),
            "median_pair_cost": round(_median(pcs), 4),
            "pair_cost_lt_1_pct": round(100 * sum(1 for p in pcs if p < 1.0) / len(pcs), 1),
            "pair_cost_lt_098_pct": round(100 * sum(1 for p in pcs if p < 0.98) / len(pcs), 1),
            "median_matched_notional": round(_median(
                [w["matched_qty"] * w["pair_cost"] for w in sel]) or 0, 2),
            "sum_pair_locked": round(sum(w["pair_locked"] for w in sel), 2),
            "sum_residual": round(sum(w["residual"] for w in sel), 2),
            "sum_net": round(sum(w["net"] for w in sel), 2),
        }
    out["by_gap_bucket"] = buckets
    return out


def main() -> int:
    data = load()
    windows = data["windows"]
    income_rows = data["ledger"].get("income_rows", [])
    wk = weekly(windows, income_rows)
    tp = temporal_pairing(windows)
    # The same temporal test inside each month, so era shifts are visible.
    by_month: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for w in windows:
        by_month[datetime.fromtimestamp(w["ts"], tz=timezone.utc).strftime("%Y-%m")].append(w)
    tp_by_month = {m: temporal_pairing(ws) for m, ws in sorted(by_month.items())}

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [str(IN_JSON), str(IN_WINDOWS)],
        "weekly": wk,
        "temporal_pairing_overall": tp,
        "temporal_pairing_by_month": tp_by_month,
    }
    OUT_JSON.write_text(json.dumps(out, indent=1))

    print(f"{'week':<9}{'n':>6}{'turnover':>11}{'trade$':>9}{'income$':>9}"
          f"{'total$':>9}{'tot/$':>7}{'med$':>7}{'pair':>7}{'gap_s':>7}"
          f"{'<=2s%':>7}{'res%':>6}{'5m%':>5}")
    for r in wk:
        def fmt(v, spec):
            return format(v, spec) if v is not None else "  -"
        print(f"{r['week']:<9}{r['n_windows']:>6}{r['turnover']:>11.0f}"
              f"{r['net_trading_cash']:>9.0f}{r['income_total']:>9.0f}"
              f"{r['total']:>9.0f}{fmt(r['total_per_dollar'], '>7.3f')}"
              f"{r['median_window_buy_usdc']:>7.0f}{fmt(r['median_pair_cost'], '>7.3f')}"
              f"{fmt(r['median_opposite_gap_s'], '>7.1f')}{fmt(r['gap_le2s_pct'], '>7.1f')}"
              f"{fmt(r['median_residual_share'], '>6.2f')}{fmt(r['m5_pct'], '>5.0f')}")
    print("\ntemporal pairing (overall):")
    print(json.dumps(tp, indent=1))
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
