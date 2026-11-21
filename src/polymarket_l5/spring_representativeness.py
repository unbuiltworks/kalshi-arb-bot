"""Strict-spring representativeness study on the canonical L5 sequence
(issue #167 comment 5532993594).

READ-ONLY research: quantifies whether the current strict spring gate
(open 45-55c, completion pair <= 0.96, repeated >=2 / review >=3 inside
a rolling hour) is representative of L5's actual historical behavior —
it changes no gate, arms nothing, and touches no execution code.

Input: analysis/l5/l5_canonical_sequence.csv.gz (71,984 rows, per-trade
cumulative inventory columns, settlement-labelled). Output:
analysis/l5/l5_spring_representativeness.json plus a printed summary.

No-hindsight sequencing: window eligibility for a (band, cap) definition
is decided at the window's FIRST BUY price only; completion economics
are then measured forward from the recorded fills.
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[2]
SEQ = ROOT / "analysis/l5/l5_canonical_sequence.csv.gz"
OUT = ROOT / "analysis/l5/l5_spring_representativeness.json"

BANDS = {"45-55": (0.45, 0.55), "40-60": (0.40, 0.60),
         "35-65": (0.35, 0.65), "30-70": (0.30, 0.70),
         "25-75": (0.25, 0.75)}
PAIR_CAPS = (0.94, 0.96, 0.98, 1.00, 1.02)
GRID_CAPS = (0.94, 0.96, 0.98, 1.00)
REPEAT_SPAN_S = 3600.0


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load_rows(path: Path = SEQ) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return list(csv.DictReader(f))


def windows(rows: Iterable[Mapping[str, str]]) -> list[dict]:
    """Fold per-trade rows into per-window facts (buys only drive the
    behavioral stats; cum/matched columns are taken as authoritative)."""
    grouped: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for r in rows:
        grouped[r["window_id"]].append(r)
    out = []
    for wid, rs in grouped.items():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        buys = [r for r in rs if r["side"] == "BUY"]
        if not buys:
            continue
        first = buys[0]
        open_px = _f(first["price"])
        unit = _f(first["quantity"]) or 0.0
        first_out = first["outcome"]
        t_open = _f(first["timestamp"])
        # first fill on the opposite outcome = first complement
        t_comp = next((_f(b["timestamp"]) for b in buys
                       if b["outcome"] != first_out), None)
        # relation of every post-open BUY vs the running residual
        rels = []
        for prev, cur in zip(rs, rs[1:]):
            if cur["side"] != "BUY":
                continue
            resid = (_f(prev["cum_up_qty"]) or 0.0) - \
                (_f(prev["cum_down_qty"]) or 0.0)
            up = cur["outcome"] == "Up"
            if abs(resid) < 1e-9:
                rel = "open"
            elif (resid > 0) == up:
                rel = "extend"
            else:
                rel = "complete"
            rels.append({"rel": rel, "px": _f(cur["price"]),
                         "tte": _f(cur["seconds_to_settlement"]),
                         "imb_units": (abs(resid) / unit) if unit else None})
        last = rs[-1]
        matched = _f(last["matched_qty"]) or 0.0
        pair = _f(last["matched_pair_unit_cost"]) if matched > 0 else None
        # completion (first matched>0) timestamp for latency/repetition
        t_matched = next((_f(r["timestamp"]) for r in rs
                          if (_f(r["matched_qty"]) or 0.0) > 0), None)
        buy_notional = sum(_f(b["dollars"]) or 0.0 for b in buys)
        out.append({
            "window_id": wid, "asset": first["asset"],
            "duration": first["duration_minutes"],
            "stream": f"{first['asset']}-{first['duration_minutes']}m",
            "open_px": open_px, "open_t": t_open,
            "open_tte": _f(first["seconds_to_settlement"]),
            "n_buys": len(buys), "buy_notional": buy_notional,
            "buy_pxs": [_f(b["price"]) for b in buys],
            "comp_latency_s": (t_comp - t_open)
            if (t_comp is not None and t_open is not None) else None,
            "rels": rels,
            "matched": matched, "pair_cost": pair,
            "t_matched": t_matched,
            "residual_end": (_f(last["cum_up_qty"]) or 0.0)
            - (_f(last["cum_down_qty"]) or 0.0),
        })
    out.sort(key=lambda w: w["open_t"] or 0.0)
    return out


def _in(band: tuple, px: Optional[float]) -> bool:
    return px is not None and band[0] <= px <= band[1]


def _pct(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def _latency_stats(vals: list) -> Optional[dict]:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return {"n": len(vals), "p50_s": round(median(vals), 1),
            "p90_s": round(vals[int(0.9 * (len(vals) - 1))], 1),
            "gt_30s": _pct(sum(v > 30 for v in vals), len(vals)),
            "gt_60s": _pct(sum(v > 60 for v in vals), len(vals)),
            "gt_120s": _pct(sum(v > 120 for v in vals), len(vals))}


def _max_repeats_in_hour(times: list[float]) -> int:
    times = sorted(times)
    best = 0
    for i, t in enumerate(times):
        j = i
        while j < len(times) and times[j] - t <= REPEAT_SPAN_S:
            j += 1
        best = max(best, j - i)
    return best


def compute(rows: list[dict]) -> dict:
    ws = windows(rows)
    n_w = len(ws)
    all_buy_px = [p for w in ws for p in w["buy_pxs"] if p is not None]
    total_notional = sum(w["buy_notional"] for w in ws)

    # 1. entry/action price band coverage — precise denominators
    bands_report = {}
    for name, b in BANDS.items():
        opens_in = sum(1 for w in ws if _in(b, w["open_px"]))
        acts_in = sum(1 for p in all_buy_px if b[0] <= p <= b[1])
        bands_report[name] = {
            "window_opens_in_band": opens_in,
            "window_opens_frac": _pct(opens_in, n_w),
            "buy_actions_in_band": acts_in,
            "buy_actions_frac": _pct(acts_in, len(all_buy_px))}

    # 2. pair-cost CDF + completion latency, split by stream
    def pair_block(sub):
        matched = [w for w in sub if w["pair_cost"] is not None]
        cdf = {f"le_{c:.2f}": _pct(
            sum(1 for w in matched if w["pair_cost"] <= c + 1e-9),
            len(matched)) for c in PAIR_CAPS}
        return {"windows": len(sub), "matched_windows": len(matched),
                "matched_frac": _pct(len(matched), len(sub)),
                "pair_cost_cdf": cdf,
                "pair_cost_p50": (round(median(
                    w["pair_cost"] for w in matched), 4)
                    if matched else None),
                "completion_latency": _latency_stats(
                    [w["comp_latency_s"] for w in sub])}
    streams: dict[str, dict] = {"ALL": pair_block(ws)}
    for s in sorted({w["stream"] for w in ws}):
        streams[s] = pair_block([w for w in ws if w["stream"] == s])

    # 3. post-first-side behavior: EXTEND vs COMPLETE
    def rel_block(rels):
        n = len(rels)
        return {"n_actions": n,
                "extend_frac": _pct(
                    sum(r["rel"] == "extend" for r in rels), n),
                "complete_frac": _pct(
                    sum(r["rel"] == "complete" for r in rels), n)}
    post = [r for w in ws for r in w["rels"]]
    by_band = {name: rel_block([r for r in post if _in(b, r["px"])])
               for name, b in BANDS.items()}
    tte_buckets = {"tte_gt_120s": lambda t: t is not None and t > 120,
                   "tte_30_120s": lambda t: t is not None and 30 <= t <= 120,
                   "tte_lt_30s": lambda t: t is not None and t < 30}
    by_tte = {k: rel_block([r for r in post if f(r["tte"])])
              for k, f in tte_buckets.items()}
    by_imb = {"imb_le_1u": rel_block(
        [r for r in post if r["imb_units"] is not None
         and r["imb_units"] <= 1.5]),
        "imb_gt_1u": rel_block(
        [r for r in post if r["imb_units"] is not None
         and r["imb_units"] > 1.5])}

    # 4. KEY: replay the CURRENT strict definition on history
    def gate_replay(band, cap):
        eligible = [w for w in ws if _in(band, w["open_px"])]
        completed = [w for w in eligible if w["pair_cost"] is not None
                     and w["pair_cost"] <= cap + 1e-9]
        per_stream_rep = {}
        for s in sorted({w["stream"] for w in completed}):
            times = [w["t_matched"] for w in completed
                     if w["stream"] == s and w["t_matched"] is not None]
            per_stream_rep[s] = {
                "completions": len(times),
                "max_completions_in_any_60min": _max_repeats_in_hour(times)}
        span_days = None
        ts = [w["open_t"] for w in ws if w["open_t"]]
        if ts:
            span_days = max(1e-9, (max(ts) - min(ts)) / 86400.0)
        return {
            "eligible_windows": len(eligible),
            "eligible_frac_of_windows": _pct(len(eligible), n_w),
            "completed_windows": len(completed),
            "completion_rate_of_eligible": _pct(len(completed),
                                                len(eligible)),
            "completions_per_day": (round(len(completed) / span_days, 2)
                                    if span_days else None),
            "share_of_buy_notional": _pct(
                int(sum(w["buy_notional"] for w in completed)),
                int(total_notional) or 1),
            "share_of_buy_actions": _pct(
                sum(w["n_buys"] for w in completed),
                sum(w["n_buys"] for w in ws)),
            "complete_relation_frac_in_eligible": rel_block(
                [r for w in eligible for r in w["rels"]])["complete_frac"],
            "repeated_ge2_within_hour_streams": sum(
                1 for v in per_stream_rep.values()
                if v["max_completions_in_any_60min"] >= 2),
            "repeated_ge3_within_hour_streams": sum(
                1 for v in per_stream_rep.values()
                if v["max_completions_in_any_60min"] >= 3),
            "per_stream_repetition": per_stream_rep,
        }
    current = gate_replay(BANDS["45-55"], 0.96)

    # 5. evidence grid (research only; identical no-hindsight sequencing)
    grid = {}
    for bname, band in BANDS.items():
        for cap in GRID_CAPS:
            g = gate_replay(band, cap)
            eligible = [w for w in ws if _in(band, w["open_px"])]
            completed = [w for w in eligible
                         if w["pair_cost"] is not None
                         and w["pair_cost"] <= cap + 1e-9]
            grid[f"{bname}|le_{cap:.2f}"] = {
                **{k: g[k] for k in (
                    "eligible_windows", "completed_windows",
                    "completion_rate_of_eligible", "completions_per_day",
                    "share_of_buy_notional",
                    "repeated_ge2_within_hour_streams",
                    "repeated_ge3_within_hour_streams")},
                "mean_pair_cost": (round(sum(
                    w["pair_cost"] for w in completed) / len(completed), 4)
                    if completed else None),
                "mean_end_residual_abs": (round(sum(
                    abs(w["residual_end"]) for w in eligible)
                    / len(eligible), 4) if eligible else None),
            }

    return {
        "labels": "READ-ONLY research on the canonical L5 sequence; no "
                  "gate, admission, execution, or risk change; "
                  "REPLICATION-fidelity metrics, not executable-economics "
                  "guarantees",
        "denominators": {
            "windows_with_any_buy": n_w,
            "buy_actions": len(all_buy_px),
            "post_open_buy_actions": len(post),
            "total_buy_notional_usd": round(total_notional, 2),
        },
        "entry_price_bands": bands_report,
        "pair_cost_and_latency_by_stream": streams,
        "post_first_side_behavior": {"by_price_band": by_band,
                                     "by_tte": by_tte,
                                     "by_imbalance": by_imb},
        "current_strict_gate_replay_45_55_le096": current,
        "evidence_grid": grid,
    }


def main() -> int:
    report = compute(load_rows())
    OUT.write_text(json.dumps(report, indent=1))
    cur = report["current_strict_gate_replay_45_55_le096"]
    print("windows:", report["denominators"]["windows_with_any_buy"])
    for name, b in report["entry_price_bands"].items():
        print(f"band {name}: opens {b['window_opens_frac']} "
              f"actions {b['buy_actions_frac']}")
    print("current strict gate:", json.dumps(
        {k: cur[k] for k in ("eligible_frac_of_windows",
                             "completion_rate_of_eligible",
                             "completions_per_day",
                             "share_of_buy_notional",
                             "repeated_ge2_within_hour_streams",
                             "repeated_ge3_within_hour_streams")}))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
