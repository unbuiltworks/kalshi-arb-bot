"""Inferred L5 decision rule (BTC 15m maker machine) + mimic-vs-filter comparison.

Measures the trader's behavioral policy from his own fill sequences (recent era,
15m windows) and replays a risk-wrapped behavior clone against the restrictive
"perfect-window" policy on the same tape. Fills-only caveat applies throughout:
resting orders are invisible; the quoting ladder is inferred from fill patterns.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .fill_toxicity import RECENT_START_TS, load_taker_fingerprints, _fingerprint_of_row

WINDOW_S = 900.0
MIMIC_PRICE_CAP = 0.95
MIMIC_STOP_BEFORE_EXPIRY_S = 60.0
MIMIC_SPEND_CAP_PER_WINDOW = 100.0  # at 10% scale => his median-ish window spend
SCALE = 0.10
EMPIRICAL_MAKER_REBATE_PER_DOLLAR = 0.0072


def _f(row: Dict[str, str], key: str) -> Optional[float]:
    value = row.get(key, "")
    try:
        return None if value in ("", None) else float(value)
    except ValueError:
        return None


def _rows_15m(trades_csv: Path) -> Dict[str, List[Dict[str, str]]]:
    by_window: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in csv.DictReader(open(trades_csv, encoding="utf-8", newline="")):
        ts = _f(row, "timestamp")
        if ts is None or ts < RECENT_START_TS:
            continue
        if row.get("side") != "BUY" or row.get("window_minutes") != "15":
            continue
        if row.get("outcome") not in ("Up", "Down"):
            continue
        by_window[row["window_id"]].append(row)
    for rows in by_window.values():
        rows.sort(key=lambda r: (float(r["timestamp"]), r.get("transaction_hash") or ""))
    return dict(by_window)


def infer_rule(by_window: Dict[str, List[Dict[str, str]]], taker_fps: set) -> Dict[str, Any]:
    first_s, last_expiry_s = [], []
    decile: Dict[int, Dict[str, Any]] = {d: {"n": 0, "taker": 0, "imb": [], "size": []} for d in range(10)}
    drift = Counter()
    light_ctrl = defaultdict(lambda: [0, 0])
    pace: Dict[int, List[float]] = defaultdict(list)
    heavy = Counter()

    for rows in by_window.values():
        first = _f(rows[0], "seconds_from_window_start")
        if first is not None:
            first_s.append(first)
        last = _f(rows[-1], "seconds_to_expiry")
        if last is not None:
            last_expiry_s.append(last)
        up = down = spent = 0.0
        total_spend = sum(_f(r, "dollars") or 0.0 for r in rows)
        last_price: Dict[str, float] = {}
        last_outcome: Optional[str] = None
        for row in rows:
            outcome = row["outcome"]
            price = _f(row, "price") or 0.0
            shares = _f(row, "shares") or 0.0
            dollars = _f(row, "dollars") or 0.0
            sfs = _f(row, "seconds_from_window_start")
            if sfs is not None:
                dec = min(9, int(sfs / WINDOW_S * 10))
                bucket = decile[dec]
                bucket["n"] += 1
                if _fingerprint_of_row(row) in taker_fps:
                    bucket["taker"] += 1
                total = up + down
                if total > 0:
                    bucket["imb"].append(abs(up - down) / total)
                bucket["size"].append(shares)
                spent += dollars
                if total_spend > 0:
                    pace[dec].append(spent / total_spend)
            if outcome in last_price:
                move = price - last_price[outcome]
                key = ("drop" if move < -0.001 else "rise" if move > 0.001 else "flat",
                       "same" if outcome == last_outcome else "switch")
                drift[key] += 1
            total = up + down
            if total > 20:
                imbalance = (up - down) / total
                light = "Down" if imbalance > 0 else "Up"
                bucket_id = min(4, int(abs(imbalance) * 10))
                light_ctrl[bucket_id][0] += 1
                if abs(imbalance) > 0.02 and outcome == light:
                    light_ctrl[bucket_id][1] += 1
            last_price[outcome] = price
            last_outcome = outcome
            if outcome == "Up":
                up += shares
            else:
                down += shares
        heavy["Up" if up > down else "Down" if down > up else "even"] += 1

    def med(values):
        return round(statistics.median(values), 3) if values else None

    return {
        "sample": {"windows": len(by_window), "fills": sum(len(r) for r in by_window.values())},
        "entry": {"first_fill_seconds_median": med(first_s)},
        "exit": {"last_fill_seconds_to_expiry_median": med(last_expiry_s),
                 "last_fill_seconds_to_expiry_p25": round(statistics.quantiles(last_expiry_s, n=4)[0], 1) if len(last_expiry_s) > 3 else None},
        "by_decile": {
            d: {
                "n": b["n"],
                "taker_share": round(b["taker"] / b["n"], 4) if b["n"] else None,
                "imbalance_ratio_median": med(b["imb"]),
                "size_median": med(b["size"]),
                "cum_spend_share_median": med(pace[d]),
            }
            for d, b in decile.items()
        },
        "same_side_after_own_price_move": {f"{a}/{b}": n for (a, b), n in sorted(drift.items())},
        "light_side_fill_probability_by_imbalance": {
            f"{b/10:.1f}-{(b+1)/10 if b < 4 else 1.0:.1f}": round(k / n, 3)
            for b, (n, k) in sorted(light_ctrl.items()) if n
        },
        "heavy_side_at_end": dict(heavy),
    }


def compare_policies(by_window: Dict[str, List[Dict[str, str]]],
                     winners: Dict[str, str], taker_fps: set) -> Dict[str, Any]:
    """Risk-wrapped behavior clone vs the tape-contra 'perfect window' filter,
    replayed on the same fills at 10% scale (same conventions as the clone-state
    replay; taker fills pass through both policies)."""
    from .inventory_aware_replay import TapeFlow, TAPE_LIGHT, PRICE_FLOOR

    def run_policy(name: str, maker_accept) -> Dict[str, Any]:
        pnl = fee_free_dollars = 0.0
        fills = rejected = 0
        window_hours = 0.0
        exposure_events: List[tuple] = []
        for window_id, rows in by_window.items():
            winner = winners.get(window_id, "")
            if winner not in ("Up", "Down"):
                continue
            window_hours += WINDOW_S / 3600.0
            tape = TapeFlow()
            spent = 0.0
            for row in rows:
                outcome = row["outcome"]
                price = _f(row, "price") or 0.0
                shares = (_f(row, "shares") or 0.0) * SCALE
                dollars = (_f(row, "dollars") or 0.0) * SCALE
                tte = _f(row, "seconds_to_expiry")
                is_taker = _fingerprint_of_row(row) in taker_fps
                accept = True
                if not is_taker:
                    accept = maker_accept(tape, outcome, price, tte, spent)
                if accept:
                    fills += 1
                    spent += dollars
                    pnl += shares * (1.0 if outcome == winner else 0.0) - dollars
                    fee_free_dollars += dollars
                else:
                    rejected += 1
                tape.add(outcome, shares)
            start = _f(rows[0], "timestamp") or 0
            end_ts = start + WINDOW_S
            exposure_events.append((start, spent))
            exposure_events.append((end_ts, -spent))
        exposure_events.sort()
        cur = peak = 0.0
        for _, delta in exposure_events:
            cur += delta
            peak = max(peak, cur)
        rebate = fee_free_dollars * EMPIRICAL_MAKER_REBATE_PER_DOLLAR
        return {
            "policy": name,
            "fills_accepted": fills,
            "fills_rejected": rejected,
            "turnover_dollars": round(fee_free_dollars, 2),
            "trading_pnl": round(pnl, 2),
            "modeled_rebate": round(rebate, 2),
            "pnl_plus_rebate": round(pnl + rebate, 2),
            "peak_concurrent_exposure": round(peak, 2),
            "dollars_per_hour_pnl_plus_rebate": round((pnl + rebate) / window_hours, 3) if window_hours else None,
        }

    def mimic(tape, outcome, price, tte, spent):
        return (
            price <= MIMIC_PRICE_CAP
            and (tte is None or tte >= MIMIC_STOP_BEFORE_EXPIRY_S)
            and spent < MIMIC_SPEND_CAP_PER_WINDOW
        )

    def perfect(tape, outcome, price, tte, spent):
        return tape.imbalance_toward(outcome) < TAPE_LIGHT and price >= PRICE_FLOOR

    return {
        "scale": SCALE,
        "policies": [
            run_policy("mimic_risk_wrapped(behavior clone + caps)", mimic),
            run_policy("perfect_window(tape-contra policy D)", perfect),
            run_policy("accept_all_baseline", lambda *a: True),
        ],
    }


def run(out_path: Optional[Path] = None) -> Dict[str, Any]:
    trades_csv = config.OUTPUT_DIR / config.TRADES_CSV
    summary_csv = config.OUTPUT_DIR / config.SUMMARY_CSV
    winners = {row["window_id"]: row.get("winning_outcome") or ""
               for row in csv.DictReader(open(summary_csv, encoding="utf-8", newline=""))}
    taker_fps = load_taker_fingerprints(config.RAW_DIR)
    by_window = _rows_15m(trades_csv)
    payload = {
        "note": (
            "Fills-only inference: resting orders are invisible, so the quoting "
            "ladder is inferred from fill patterns. His trading P&L is roughly "
            "breakeven by design; the strategy's output is rebate-eligible maker "
            "volume with bounded inventory, not per-trade locked profit."
        ),
        "decision_rule": infer_rule(by_window, taker_fps),
        "comparison": compare_policies(by_window, winners, taker_fps),
    }
    out_path = Path(out_path or (config.OUTPUT_DIR / "l5_decision_rule.json"))
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":  # pragma: no cover
    result = run()
    print(json.dumps(result["comparison"], indent=1))
