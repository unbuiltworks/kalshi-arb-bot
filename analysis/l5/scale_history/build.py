"""SMALL-L5 -> RAMPED-L5: did L5 scale up without changing shape?

THE GATING QUESTION WAS ANSWERED BEFORE ANY COLLECTION WAS PROPOSED, and the
answer is that we already hold the data. The paginated public-activity dumps in
data/l5/raw (activity.jsonl + activity_gap.jsonl.gz) carry 784,500 records,
779,897 of them unique, over 141 distinct days from 2026-03-19 to 2026-08-31.
The requested 2026-03-16..2026-05-15 interval is covered by 42 days and 87,993
records. No Goldsky/Polygon backfill is required for this question.

WHY THE CANONICAL CSV LOOKED LIKE A GAP AND IS NOT ONE. l5_canonical_sequence
holds 71,984 fills over only 22 days. That is not a truncated history; it is a
WINDOW-SELECTED corpus -- the subset reconstructed into complete up/down windows
for the behavioural model. The raw activity is the full tape and is what a
day-level scale question has to be asked of.

PROVENANCE IS PRESERVED, NOT FLATTENED. Records are de-duplicated on
(transactionHash, asset, timestamp, size, price) and every row keeps its hash
and conditionId, so any figure here can be walked back to a chain record.

MAKER/TAKER IS NOT CLAIMED. The public activity feed carries no aggressor flag.
Anything reported about maker share elsewhere in this repo comes from a venue
fill record, not from this tape, and is deliberately absent here rather than
guessed.
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

RAW = ["data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz"]


def _open(path: str):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def load_fills(paths: Sequence[str] = RAW) -> List[dict]:
    """Every unique TRADE record, in time order, with provenance kept."""
    seen = set()
    out: List[dict] = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with _open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    env = json.loads(line)
                except ValueError:
                    continue
                for r in (env.get("payload") or []):
                    ts = r.get("timestamp")
                    if ts is None or r.get("type") not in (None, "TRADE"):
                        continue
                    h = r.get("transactionHash")
                    key = (h, r.get("asset"), ts, r.get("size"), r.get("price"))
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        out.append({
                            "ts": int(ts),
                            "day": dt.datetime.utcfromtimestamp(
                                int(ts)).strftime("%Y-%m-%d"),
                            "cid": r.get("conditionId"),
                            "slug": r.get("eventSlug"),
                            "outcome": r.get("outcome"),
                            "oi": r.get("outcomeIndex"),
                            "side": r.get("side"),
                            "price": float(r.get("price") or 0),
                            "size": float(r.get("size") or 0),
                            "usd": float(r.get("usdcSize") or 0),
                            "hash": h,
                        })
                    except (TypeError, ValueError):
                        continue
    out.sort(key=lambda r: (r["ts"], str(r["hash"])))
    return out


# --------------------------------------------------------------------------
# per-window trajectory

def windows(fills: Sequence[dict]) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = {}
    for f in fills:
        if f["cid"]:
            by.setdefault(f["cid"], []).append(f)
    return by


def window_metrics(trades: Sequence[dict]) -> dict:
    """The inventory trajectory of one market, and its shape statistics.

    `up`/`dn` follow outcomeIndex, so a SELL reduces the side it sells. Matched
    is min(up, dn) and residual is the signed remainder -- the same convention
    the canonical sequence uses, so the two corpora stay comparable.
    """
    up = dn = 0.0
    up_spend = dn_spend = 0.0
    peak_inv = peak_resid = 0.0
    runs: List[int] = []
    switches = 0
    cur_side: Optional[int] = None
    run = 0
    pair_costs: List[float] = []
    for t in trades:
        oi = 1 if int(t["oi"] or 0) == 1 else 0
        sgn = 1.0 if t["side"] == "BUY" else -1.0
        if oi == 0:
            up += sgn * t["size"]
            up_spend += sgn * t["usd"]
        else:
            dn += sgn * t["size"]
            dn_spend += sgn * t["usd"]
        if cur_side is None or oi == cur_side:
            run += 1
        else:
            runs.append(run)
            switches += 1
            run = 1
        cur_side = oi
        matched = min(max(up, 0.0), max(dn, 0.0))
        resid = up - dn
        peak_inv = max(peak_inv, abs(up) + abs(dn))
        peak_resid = max(peak_resid, abs(resid))
        if matched > 0 and up > 0 and dn > 0:
            pair_costs.append((up_spend / up) + (dn_spend / dn))
    if run:
        runs.append(run)
    matched = min(max(up, 0.0), max(dn, 0.0))
    resid = up - dn
    sizes = sorted(t["size"] for t in trades)
    return {
        "n_fills": len(trades),
        "notional": sum(t["usd"] for t in trades),
        "median_size": sizes[len(sizes) // 2] if sizes else 0.0,
        "p90_size": sizes[min(len(sizes) - 1, int(.9 * len(sizes)))] if sizes else 0.0,
        "peak_inventory": peak_inv,
        "peak_residual": peak_resid,
        "matched": matched,
        "residual": abs(resid),
        "resid_over_matched": (abs(resid) / matched) if matched > 0 else None,
        "mean_run_len": (sum(runs) / len(runs)) if runs else 0.0,
        "max_run_len": max(runs) if runs else 0,
        "switches": switches,
        "switch_rate": switches / max(1, len(trades) - 1),
        "final_pair_cost": pair_costs[-1] if pair_costs else None,
        "median_pair_cost": (sorted(pair_costs)[len(pair_costs) // 2]
                             if pair_costs else None),
        "tte_span_s": trades[-1]["ts"] - trades[0]["ts"] if trades else 0,
        "day": trades[0]["day"] if trades else None,
    }


def daily(fills: Sequence[dict]) -> Dict[str, dict]:
    """Per-day activity, capital and normalized-shape aggregates."""
    by_day: Dict[str, List[dict]] = {}
    for f in fills:
        by_day.setdefault(f["day"], []).append(f)
    wins = windows(fills)
    wm = {c: window_metrics(t) for c, t in wins.items()}
    day_wins: Dict[str, List[dict]] = {}
    for m in wm.values():
        if m["day"]:
            day_wins.setdefault(m["day"], []).append(m)

    out: Dict[str, dict] = {}
    for d, ts in sorted(by_day.items()):
        sizes = sorted(t["size"] for t in ts)
        ws = day_wins.get(d, [])
        def med(key):
            vals = sorted(w[key] for w in ws if w.get(key) is not None)
            return vals[len(vals) // 2] if vals else None
        out[d] = {
            "day": d,
            "fills": len(ts),
            "markets": len({t["cid"] for t in ts}),
            "notional": sum(t["usd"] for t in ts),
            "median_size": sizes[len(sizes) // 2],
            "p90_size": sizes[min(len(sizes) - 1, int(.9 * len(sizes)))],
            "windows": len(ws),
            "fills_per_window": (len(ts) / len(ws)) if ws else None,
            "notional_per_window": (sum(t["usd"] for t in ts) / len(ws)) if ws else None,
            "median_peak_residual": med("peak_residual"),
            "median_peak_inventory": med("peak_inventory"),
            "median_matched": med("matched"),
            "median_resid_over_matched": med("resid_over_matched"),
            "median_run_len": med("mean_run_len"),
            "median_switch_rate": med("switch_rate"),
            "median_pair_cost": med("median_pair_cost"),
            "median_tte_span_s": med("tte_span_s"),
        }
    return out


# --------------------------------------------------------------------------
# change points

def cusum_breaks(series: Sequence[float], k: float = 1.0,
                 min_seg: int = 3) -> List[int]:
    """Binary segmentation on the mean, scored by variance reduction.

    Deliberately simple and deterministic: no library, no tuning knob that can
    be nudged until a break lands on a date someone expected.
    """
    def seg_cost(a: int, b: int) -> float:
        xs = series[a:b]
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs)

    def best_split(a: int, b: int) -> Tuple[Optional[int], float]:
        base = seg_cost(a, b)
        best, gain = None, 0.0
        for i in range(a + min_seg, b - min_seg + 1):
            g = base - (seg_cost(a, i) + seg_cost(i, b))
            if g > gain:
                best, gain = i, g
        return best, gain

    n = len(series)
    if n < 2 * min_seg:
        return []
    breaks: List[int] = []
    stack = [(0, n)]
    total = seg_cost(0, n)
    while stack:
        a, b = stack.pop()
        i, gain = best_split(a, b)
        if i is None or total <= 0 or gain / total < 0.02 * k:
            continue
        breaks.append(i)
        stack.extend([(a, i), (i, b)])
    return sorted(breaks)


def regime_table(days: Dict[str, dict], keys: Sequence[str],
                 k: float = 1.0) -> Dict[str, Any]:
    ordered = [days[d] for d in sorted(days)]
    labels = [d["day"] for d in ordered]
    out: Dict[str, Any] = {"days": len(ordered), "breaks": {}}
    for key in keys:
        series = [float(d[key]) for d in ordered if d.get(key) is not None]
        if len(series) != len(ordered):
            series = [float(d[key]) if d.get(key) is not None else 0.0
                      for d in ordered]
        idx = cusum_breaks(series, k=k)
        out["breaks"][key] = [labels[i] for i in idx]
    return out
