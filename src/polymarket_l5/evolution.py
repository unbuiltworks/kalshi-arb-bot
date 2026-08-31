"""L5 evolution study: how the trader changed from his first day to now.

Question (user, 2026-08-31): his history starts 2026-03-19 with +$0.16 and
gets progressively better — does the early period teach us anything, or is
it written off because of the Polymarket venue?

What we look for (behavior, not P&L levels — P&L doesn't transfer across
fee regimes; behavior might):
  - size ramp: how long did he stay tiny before scaling?
  - step-change vs gradual: did profitability arrive with a discrete
    behavior change (a changepoint), and if so what changed at it?
  - selection evolution: assets, 5m vs 15m, time-of-day, expensive-side
    chasing (buys >= 80c), pair discipline (matched pair unit cost,
    balanced-at-end rate).

Input:  analysis/l5/l5_windows_summary.csv  (one row per traded window)
Output: analysis/l5/l5_evolution.json + a compact stdout report.

Per-window P&L column: pnl_derived_resolution = realized trading within
the window + settlement of the residual at the derived outcome. Falls back
to pnl_polymarket_positions when the derived number is missing.

This is offline research on committed historical data. It reads nothing
from the live tapes and touches no sealed experiment code.
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

SUMMARY_CSV = "analysis/l5/l5_windows_summary.csv"
OUT_JSON = "analysis/l5/l5_evolution.json"

ET_OFFSET_H = -4  # EDT throughout Mar 19 .. Aug — good enough for hour-of-day buckets


def _f(row, key, default=None):
    v = row.get(key, "")
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def load_windows(path=SUMMARY_CSV):
    out = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            ts = row.get("window_start_utc", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            pnl = _f(row, "pnl_derived_resolution")
            pnl_src = "derived_resolution"
            if pnl is None:
                pnl = _f(row, "pnl_polymarket_positions")
                pnl_src = "polymarket_positions"
            out.append({
                "dt": dt,
                "asset": row.get("asset", ""),
                "wm": int(_f(row, "window_minutes", 0) or 0),
                "bought": _f(row, "total_dollars_bought", 0.0) or 0.0,
                "pair_cost": _f(row, "matched_pair_unit_cost"),
                "matched_qty": _f(row, "matched_qty", 0.0) or 0.0,
                "balanced": row.get("balanced_at_end", "") == "yes",
                "opposite": row.get("opposite_side_ever_bought", "") == "yes",
                "chase80": row.get("any_buy_ge_80c", "") == "yes",
                "pnl": pnl,
                "pnl_src": pnl_src if pnl is not None else None,
            })
    out.sort(key=lambda w: w["dt"])
    return out


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _et_hour(dt: datetime) -> int:
    return (dt + timedelta(hours=ET_OFFSET_H)).hour


def weekly_buckets(windows):
    weeks = defaultdict(list)
    for w in windows:
        weeks[_week_key(w["dt"])].append(w)
    rows = []
    for wk in sorted(weeks):
        ws = weeks[wk]
        scored = [w for w in ws if w["pnl"] is not None]
        turnover = sum(w["bought"] for w in ws)
        pnl = sum(w["pnl"] for w in scored)
        days = {w["dt"].date() for w in ws}
        evening = [w for w in ws if _et_hour(w["dt"]) in (19, 20, 21, 22, 23, 0)]
        pcs = [w["pair_cost"] for w in ws
               if w["pair_cost"] is not None and w["matched_qty"] > 0]
        rows.append({
            "week": wk,
            "first_day": min(days).isoformat(),
            "n_windows": len(ws),
            "n_scored": len(scored),
            "active_days": len(days),
            "windows_per_active_day": round(len(ws) / len(days), 1),
            "turnover_dollars": round(turnover, 2),
            "median_window_bought": round(_median([w["bought"] for w in ws]) or 0, 2),
            "pnl": round(pnl, 2),
            "pnl_per_dollar": round(pnl / turnover, 4) if turnover > 0 else None,
            "win_rate": round(sum(1 for w in scored if w["pnl"] > 0) / len(scored), 3)
            if scored else None,
            "median_pair_unit_cost": round(_median(pcs), 4) if pcs else None,
            "balanced_pct": round(100 * sum(w["balanced"] for w in ws) / len(ws), 1),
            "opposite_pct": round(100 * sum(w["opposite"] for w in ws) / len(ws), 1),
            "chase80_pct": round(100 * sum(w["chase80"] for w in ws) / len(ws), 1),
            "evening_share_pct": round(100 * len(evening) / len(ws), 1),
            "btc_share_pct": round(
                100 * sum(1 for w in ws if w["asset"] == "BTC") / len(ws), 1),
            "m5_share_pct": round(
                100 * sum(1 for w in ws if w["wm"] == 5) / len(ws), 1),
        })
    return rows


EARLY_ERA_END = datetime(2026, 5, 2, tzinfo=timezone.utc)


def early_era_daily(windows):
    """Daily grain for the Mar 19 - May 1 ramp era (user focus). Cumulative
    P&L is carried so the compounding hypothesis is checkable: if size ramp
    outruns cumulative profit, the ramp was capital injection, not
    compounding."""
    days = defaultdict(list)
    for w in windows:
        if w["dt"] < EARLY_ERA_END:
            days[w["dt"].date()].append(w)
    rows, cum = [], 0.0
    for d in sorted(days):
        ws = days[d]
        scored = [w for w in ws if w["pnl"] is not None]
        pnl = sum(w["pnl"] for w in scored)
        cum += pnl
        rows.append({
            "day": d.isoformat(),
            "n_windows": len(ws),
            "median_window_bought": round(_median([w["bought"] for w in ws]) or 0, 2),
            "pnl": round(pnl, 2),
            "cum_pnl": round(cum, 2),
            "win_rate": round(sum(1 for w in scored if w["pnl"] > 0) / len(scored), 3)
            if scored else None,
            "opposite_pct": round(100 * sum(w["opposite"] for w in ws) / len(ws), 1),
            "median_pair_unit_cost": (lambda pcs: round(_median(pcs), 4) if pcs else None)(
                [w["pair_cost"] for w in ws
                 if w["pair_cost"] is not None and w["matched_qty"] > 0]),
        })
    return rows


def changepoint(windows, min_side=30):
    """Single best split by difference in mean pnl-per-window before/after,
    weighted by a t-like statistic so tiny sides don't win. Descriptive, not
    a hypothesis test — it locates WHERE the record changed most."""
    scored = [w for w in windows if w["pnl"] is not None]
    best = None
    for i in range(min_side, len(scored) - min_side):
        a = [w["pnl"] for w in scored[:i]]
        b = [w["pnl"] for w in scored[i:]]
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a) / max(len(a) - 1, 1)
        vb = sum((x - mb) ** 2 for x in b) / max(len(b) - 1, 1)
        se = math.sqrt(va / len(a) + vb / len(b)) or 1e-9
        t = abs(mb - ma) / se
        if best is None or t > best["t"]:
            best = {"i": i, "t": t, "mean_before": ma, "mean_after": mb,
                    "date": scored[i]["dt"].isoformat()}
    if best:
        best = {**best, "t": round(best["t"], 2),
                "mean_before": round(best["mean_before"], 4),
                "mean_after": round(best["mean_after"], 4)}
    return best


def behavior_around(windows, split_dt, span_days=21):
    """Compare behavior markers in the windows before/after a datetime."""
    lo = split_dt - timedelta(days=span_days)
    hi = split_dt + timedelta(days=span_days)
    pre = [w for w in windows if lo <= w["dt"] < split_dt]
    post = [w for w in windows if split_dt <= w["dt"] < hi]

    def mark(ws):
        if not ws:
            return None
        pcs = [w["pair_cost"] for w in ws
               if w["pair_cost"] is not None and w["matched_qty"] > 0]
        return {
            "n": len(ws),
            "median_window_bought": round(_median([w["bought"] for w in ws]) or 0, 2),
            "median_pair_unit_cost": round(_median(pcs), 4) if pcs else None,
            "balanced_pct": round(100 * sum(w["balanced"] for w in ws) / len(ws), 1),
            "chase80_pct": round(100 * sum(w["chase80"] for w in ws) / len(ws), 1),
            "opposite_pct": round(100 * sum(w["opposite"] for w in ws) / len(ws), 1),
            "evening_share_pct": round(
                100 * sum(1 for w in ws
                          if _et_hour(w["dt"]) in (19, 20, 21, 22, 23, 0)) / len(ws), 1),
            "m5_share_pct": round(
                100 * sum(1 for w in ws if w["wm"] == 5) / len(ws), 1),
        }
    return {"span_days": span_days, "pre": mark(pre), "post": mark(post)}


def main():
    windows = load_windows()
    weeks = weekly_buckets(windows)
    cp = changepoint(windows)
    around = None
    if cp:
        around = behavior_around(
            windows, datetime.fromisoformat(cp["date"]))

    scored = [w for w in windows if w["pnl"] is not None]
    total_pnl = sum(w["pnl"] for w in scored)
    total_turnover = sum(w["bought"] for w in windows)
    first_scale_week = next(
        (r["week"] for r in weeks if r["median_window_bought"] >= 10.0), None)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": SUMMARY_CSV,
        "caveat": ("Polymarket venue: ~zero trading fees. P&L LEVELS do not "
                   "transfer to fee-charging venues; behavior markers might."),
        "coverage": {
            "first_window": windows[0]["dt"].isoformat(),
            "last_window": windows[-1]["dt"].isoformat(),
            "n_windows": len(windows),
            "n_scored": len(scored),
            "total_pnl": round(total_pnl, 2),
            "total_turnover": round(total_turnover, 2),
        },
        "weeks_until_median_window_ge_10usd": first_scale_week,
        "api_coverage_note": ("Polymarket activity API caps historical offset "
                              "at 5000: dataset holds the FIRST ~5,500 fills "
                              "(2026-03-19 .. ~2026-04-25) plus the recent "
                              "trades feed (late Aug). May-mid-Aug is "
                              "unfetchable from that endpoint."),
        "weekly": weeks,
        "early_era_daily": early_era_daily(windows),
        "changepoint": cp,
        "behavior_around_changepoint": around,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as fh:
        json.dump(out, fh, indent=1)

    print(f"L5 evolution: {len(windows)} windows "
          f"{windows[0]['dt'].date()} .. {windows[-1]['dt'].date()}, "
          f"total pnl ${total_pnl:,.2f} on ${total_turnover:,.2f} turnover")
    print(f"{'week':<9}{'n':>5}{'d':>3}{'med$':>8}{'pnl$':>10}{'pnl/$':>8}"
          f"{'pair':>7}{'bal%':>6}{'ch80%':>7}{'eve%':>6}{'5m%':>6}")
    for r in weeks:
        print(f"{r['week']:<9}{r['n_windows']:>5}{r['active_days']:>3}"
              f"{r['median_window_bought']:>8.2f}{r['pnl']:>10.2f}"
              f"{(r['pnl_per_dollar'] if r['pnl_per_dollar'] is not None else float('nan')):>8.4f}"
              f"{(r['median_pair_unit_cost'] if r['median_pair_unit_cost'] is not None else float('nan')):>7.3f}"
              f"{r['balanced_pct']:>6.1f}{r['chase80_pct']:>7.1f}"
              f"{r['evening_share_pct']:>6.1f}{r['m5_share_pct']:>6.1f}")
    if cp:
        print(f"\nchangepoint: {cp['date']} (t={cp['t']}) "
              f"mean pnl/window {cp['mean_before']} -> {cp['mean_after']}")
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
