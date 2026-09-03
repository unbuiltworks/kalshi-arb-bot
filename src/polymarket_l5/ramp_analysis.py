"""L5 ramp re-analysis — how the mature strategy emerged (issue #167
comment 5520342503).

Pre-ramp / ramp / mature comparison of SEQUENTIAL behavior over the
canonical provenance-backed history, plus a changepoint MEASURED from
the data (Apr 9 is a hypothesis to test, not an assumption). Epochs:

  early        Mar 19-25       experimentation
  transition   Mar 26
  developing   Mar 27 - Apr 5  systematic regime, pre-ramp
  ramp         Apr 6-10        hypothesized ramp onset
  mature_apr   Apr 13-30       mature onset (April sample)
  mature_aug   Aug 28-31       current mature (93% of all fills) — this
                               doubles as the "is he still doing it
                               today" comparison arm

Honesty constraints baked in:
* maker_taker is populated ONLY for Aug 28-31 in the canonical data;
  every spring row is 'unknown'. Maker/taker-by-TTE is therefore
  reported for mature_aug only and marked NOT MEASURABLE for the ramp.
* credited incentives (MAKER_REBATE/REWARD rows in the accounting
  ledger) are dated from their own timestamps and compared with the
  measured behavior changepoint — reported as timing, never causality.
* research-only: reads committed artifacts, writes one JSON artifact,
  no network, nothing armed.

    python3 -m src.polymarket_l5.ramp_analysis
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config
from .action_model import _relation, _bucket, PRICE_BANDS, TTE_BUCKETS

SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
LEDGER_JSON = config.OUTPUT_DIR / "l5_ledger.json"
OUT = config.OUTPUT_DIR / "l5_ramp_analysis.json"

EPOCH_BOUNDS = (("early", "2026-03-25"), ("transition", "2026-03-26"),
                ("developing", "2026-04-05"), ("ramp", "2026-04-10"),
                ("mature_apr", "2026-04-30"), ("mature_aug", "9999-12-31"))


def epoch_of(day: str) -> str:
    for name, until in EPOCH_BOUNDS:
        if day <= until:
            return name
    return "mature_aug"


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _windows():
    with gzip.open(SEQUENCE_CSV, "rt", newline="", encoding="utf-8") as fh:
        cur, wid = [], None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def _mean(xs, nd=3):
    return round(st.mean(xs), nd) if xs else None


def _median(xs, nd=3):
    return round(st.median(xs), nd) if xs else None


def window_features(rows: List[dict]) -> dict:
    rows = sorted(rows, key=lambda r: (int(r["timestamp"]),
                                       int(r["trade_seq"] or 0)))
    buys = [r for r in rows if r["side"] == "BUY"]
    day = rows[0]["datetime_utc"][:10]
    span_s = max(_f(rows[-1]["timestamp"]) - _f(rows[0]["timestamp"]), 1.0)

    runs, run = [], 0
    prev_out = prev_px = None
    nearby = same_side_pairs = 0
    for r in buys:
        if r["outcome"] == prev_out:
            run += 1
            same_side_pairs += 1
            if prev_px is not None and abs(_f(r["price"]) - prev_px) <= 0.01:
                nearby += 1
        else:
            if run:
                runs.append(run)
            run = 1
        prev_out, prev_px = r["outcome"], _f(r["price"])
    if run:
        runs.append(run)

    trans = Counter()
    flip_gaps = []
    complete_prices = []
    for i in range(len(rows) - 1):
        prev, nxt = rows[i], rows[i + 1]
        if nxt["side"] != "BUY":
            continue
        prev_resid = _f(prev["residual_qty_signed"])
        rel_prev = _relation(
            _f(rows[i - 1]["residual_qty_signed"]) if i else 0.0,
            prev["outcome"], prev["side"])
        rel_next = _relation(prev_resid, nxt["outcome"], nxt["side"])
        trans[(rel_prev, rel_next)] += 1
        if rel_next == "complete":
            complete_prices.append(_f(nxt["price"]))
        if prev["side"] == "BUY" and nxt["outcome"] != prev["outcome"]:
            flip_gaps.append(_f(nxt["timestamp"]) - _f(prev["timestamp"]))

    resid_abs = [abs(_f(r["residual_qty_signed"])) for r in rows]
    peak = max(resid_abs) if resid_abs else 0.0
    peak_i = resid_abs.index(peak) if resid_abs else 0
    repair_s = None
    for r in rows[peak_i:]:
        if peak > 0 and abs(_f(r["residual_qty_signed"])) <= 0.5 * peak:
            repair_s = _f(r["timestamp"]) - _f(rows[peak_i]["timestamp"])
            break
    matched_rows = [r for r in rows if _f(r["matched_qty"]) > 0
                    and r["matched_pair_unit_cost"]]
    pc_first = _f(matched_rows[0]["matched_pair_unit_cost"]) \
        if matched_rows else None
    pc_final = _f(matched_rows[-1]["matched_pair_unit_cost"]) \
        if matched_rows else None

    spend = defaultdict(float)
    for r in buys:
        spend[r["outcome"]] += _f(r["dollars"])
    dominant_up = spend.get("Up", 0.0) >= spend.get("Down", 0.0)
    final = rows[-1]
    final_resid = _f(final["residual_qty_signed"])
    resid_side = final["residual_side"] or None
    won = (final["settlement_outcome"] or "") == (resid_side or "")

    mt_tte = Counter()
    for r in rows:
        if r.get("maker_taker") in ("maker", "taker"):
            mt_tte[(_bucket(_f(r["seconds_to_settlement"]), TTE_BUCKETS),
                    r["maker_taker"])] += 1

    return {
        "day": day, "epoch": epoch_of(day),
        "asset": rows[0]["asset"], "duration": rows[0]["duration_minutes"],
        "t0": _f(rows[0]["timestamp"]), "t1": _f(rows[-1]["timestamp"]),
        "n_fills": len(rows), "n_buys": len(buys),
        "trades_per_min": len(rows) / (span_s / 60.0),
        "qtys": [_f(r["quantity"]) for r in buys],
        "runs": runs,
        "nearby_frac": (nearby / same_side_pairs) if same_side_pairs else None,
        "trans": trans, "flip_gaps": flip_gaps,
        "peak_resid": peak, "repair_s": repair_s,
        "final_resid_abs": abs(final_resid),
        "pc_first": pc_first, "pc_final": pc_final,
        "first_price": _f(buys[0]["price"]) if buys else None,
        "dominant_up": dominant_up, "resid_won": won if resid_side else None,
        "complete_prices": complete_prices,
        "mt_tte": mt_tte,
    }


def _concurrency(windows: List[dict]) -> None:
    """Per window: mean number of simultaneously-active windows over its
    span (activity measured by fill-span overlap)."""
    spans = [(w["t0"], w["t1"], i) for i, w in enumerate(windows)]
    for i, w in enumerate(windows):
        overlap = sum(1 for a, b, j in spans
                      if j != i and a <= w["t1"] and b >= w["t0"])
        w["concurrency"] = overlap + 1


def epoch_summary(ws: List[dict]) -> dict:
    qtys = [q for w in ws for q in w["qtys"]]
    runs = [r for w in ws for r in w["runs"]]
    gaps = [g for w in ws for g in w["flip_gaps"]]
    trans = Counter()
    for w in ws:
        trans.update(w["trans"])
    from_extend = sum(v for (a, _), v in trans.items() if a == "extend")
    ee = trans.get(("extend", "extend"), 0)
    ec = trans.get(("extend", "complete"), 0)
    pcs = [w["pc_final"] for w in ws if w["pc_final"]]
    pc_moves = [w["pc_final"] - w["pc_first"] for w in ws
                if w["pc_first"] and w["pc_final"]]
    mt = Counter()
    for w in ws:
        mt.update(w["mt_tte"])
    mt_total = sum(mt.values())
    conc = [w["concurrency"] for w in ws]
    qty_by_conc = defaultdict(list)
    for w in ws:
        key = "1" if w["concurrency"] <= 1 else \
            ("2-3" if w["concurrency"] <= 3 else "4+")
        qty_by_conc[key].extend(w["qtys"])
    entry_bands = Counter(_bucket(w["first_price"], PRICE_BANDS)
                          for w in ws if w["first_price"] is not None)
    resid_calls = [w["resid_won"] for w in ws if w["resid_won"] is not None]
    splits = defaultdict(lambda: {"windows": 0, "fills": 0, "qty": []})
    for w in ws:
        k = f"{w['asset']}-{w['duration']}m"
        splits[k]["windows"] += 1
        splits[k]["fills"] += w["n_fills"]
        splits[k]["qty"].extend(w["qtys"])
    return {
        "windows": len(ws),
        "fills": sum(w["n_fills"] for w in ws),
        "trades_per_window": _mean([w["n_fills"] for w in ws], 1),
        "trades_per_min_median": _median([w["trades_per_min"] for w in ws]),
        "qty_mean": _mean(qtys), "qty_median": _median(qtys),
        "run_len_mean": _mean(runs, 2),
        "run_len_p90": (round(sorted(runs)[int(0.9 * (len(runs) - 1))], 1)
                        if runs else None),
        "nearby_price_frac": _mean([w["nearby_frac"] for w in ws
                                    if w["nearby_frac"] is not None]),
        "p_extend_given_extend": round(ee / from_extend, 3)
        if from_extend else None,
        "p_complete_given_extend": round(ec / from_extend, 3)
        if from_extend else None,
        "flip_gap_median_s": _median(gaps, 1),
        "flip_gap_p75_s": (round(sorted(gaps)[int(0.75 * (len(gaps) - 1))], 1)
                           if gaps else None),
        "peak_residual_mean": _mean([w["peak_resid"] for w in ws], 1),
        "final_residual_mean": _mean([w["final_resid_abs"] for w in ws], 1),
        "repair_halflife_median_s": _median(
            [w["repair_s"] for w in ws if w["repair_s"] is not None], 1),
        "pair_cost_final_mean": _mean(pcs, 4),
        "pair_cost_drift_mean": _mean(pc_moves, 4),
        "complete_price_mean": _mean(
            [p for w in ws for p in w["complete_prices"]], 4),
        "entry_price_bands": dict(entry_bands.most_common(4)),
        "favored_up_share": _mean([w["dominant_up"] for w in ws]),
        "residual_on_winner_rate": _mean(resid_calls),
        "concurrency_mean": _mean(conc, 2),
        "concurrency_max": max(conc) if conc else None,
        "qty_mean_by_concurrency": {k: _mean(v)
                                    for k, v in sorted(qty_by_conc.items())},
        "maker_share_by_tte": (
            {tte: round(mt.get((tte, "maker"), 0) /
                        max(mt.get((tte, "maker"), 0)
                            + mt.get((tte, "taker"), 0), 1), 3)
             for tte in {k[0] for k in mt}}
            if mt_total else "NOT_MEASURABLE_maker_taker_unknown"),
        "splits": {k: {"windows": v["windows"], "fills": v["fills"],
                       "qty_mean": _mean(v["qty"])}
                   for k, v in sorted(splits.items())},
    }


def changepoint(daily: Dict[str, dict], metric: str) -> dict:
    """Max mean-shift boundary over the ordered active-day series."""
    days = sorted(d for d in daily if daily[d].get(metric) is not None)
    xs = [daily[d][metric] for d in days]
    best = {"boundary_after": None, "t_stat": 0.0}
    for i in range(2, len(xs) - 1):
        a, b = xs[:i], xs[i:]
        sa = st.pstdev(a) if len(a) > 1 else 0.0
        sb = st.pstdev(b) if len(b) > 1 else 0.0
        pooled = math.sqrt((sa * sa) / len(a) + (sb * sb) / len(b)) or 1e-9
        t = abs(st.mean(a) - st.mean(b)) / pooled
        if t > best["t_stat"]:
            best = {"boundary_after": days[i - 1], "first_new_day": days[i],
                    "t_stat": round(t, 2),
                    "mean_before": round(st.mean(a), 3),
                    "mean_after": round(st.mean(b), 3)}
    return best


def incentive_timing() -> dict:
    try:
        led = json.loads(LEDGER_JSON.read_text())
        rows = led.get("income_rows") or []
    except Exception:
        return {"error": "ledger_unavailable"}
    dated = defaultdict(list)
    for r in rows:
        ts = r.get("ts")
        if ts:
            day = datetime.fromtimestamp(float(ts),
                                         timezone.utc).strftime("%Y-%m-%d")
            dated[r.get("type") or "?"].append(day)
    return {k: {"first": min(v), "last": max(v), "n": len(v)}
            for k, v in dated.items()}


def main() -> int:
    windows = [window_features(rows) for rows in _windows()]
    _concurrency(windows)
    by_epoch: Dict[str, List[dict]] = defaultdict(list)
    daily: Dict[str, dict] = defaultdict(lambda: {"fills": 0, "windows": 0,
                                                  "qty": [], "ee": 0,
                                                  "from_e": 0})
    for w in windows:
        by_epoch[w["epoch"]].append(w)
        d = daily[w["day"]]
        d["fills"] += w["n_fills"]
        d["windows"] += 1
        d["qty"].extend(w["qtys"])
        d["ee"] += w["trans"].get(("extend", "extend"), 0)
        d["from_e"] += sum(v for (a, _), v in w["trans"].items()
                           if a == "extend")
    daily_metrics = {
        day: {"fills": v["fills"], "windows": v["windows"],
              "qty_mean": _mean(v["qty"]),
              "p_ee": round(v["ee"] / v["from_e"], 3) if v["from_e"] else None}
        for day, v in daily.items()}

    out: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "labels": "research-only sequential re-analysis; provenance-backed "
                  "canonical history; nothing armed; timing not causality",
        "epochs": {name: epoch_summary(ws)
                   for name, ws in sorted(by_epoch.items())},
        "daily": dict(sorted(daily_metrics.items())),
        "changepoints_measured": {
            m: changepoint(daily_metrics, m)
            for m in ("fills", "windows", "qty_mean", "p_ee")},
        # spring-only: the Apr21->Aug28 data gap otherwise dominates every
        # series; testing 'is Apr 9 the changepoint' needs spring days only
        "changepoints_spring_only": {
            m: changepoint({d: v for d, v in daily_metrics.items()
                            if d <= "2026-04-30"}, m)
            for m in ("fills", "windows", "qty_mean", "p_ee")},
        "maker_taker_coverage_note": "maker/taker labels exist ONLY for "
                                     "Aug 28-31 rows; spring epochs are "
                                     "'unknown' and reported as such",
        "incentive_timing": incentive_timing(),
    }
    OUT.write_text(json.dumps(out, indent=1, default=str))
    for name, s in out["epochs"].items():
        print(f"{name:>11}: win={s['windows']:>4} fills={s['fills']:>6} "
              f"q={s['qty_mean']} run={s['run_len_mean']} "
              f"P(E|E)={s['p_extend_given_extend']} "
              f"gap={s['flip_gap_median_s']}s conc={s['concurrency_mean']} "
              f"pc={s['pair_cost_final_mean']}")
    print("changepoints:", json.dumps(out["changepoints_measured"]))
    print("incentives:", json.dumps(out["incentive_timing"]))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
