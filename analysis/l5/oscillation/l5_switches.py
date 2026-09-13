"""P2 + P4: L5's oscillation mechanism and the price at which he switches.

    python3 analysis/l5/oscillation/l5_switches.py

Read-only research over the canonical per-fill sequence. Nothing here needs
book data, and nothing here infers maker/taker.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_oscillation as osc                          # noqa: E402
from src.polymarket_l5.action_model import (SYSTEMATIC_REGIME_EPOCH,  # noqa: E402
                                            _bucket, PRICE_BANDS, TTE_BUCKETS)
from src.polymarket_l5.action_model_v2 import _f, _windows      # noqa: E402

OUT = Path(__file__).resolve().parent / "l5_switches.json"
MOVE_BANDS = ((-0.05, "held_side_fell_>=5c"), (-0.01, "held_side_fell_1-5c"),
              (0.01, "flat_<1c"), (0.05, "held_side_rose_1-5c"),
              (1e9, "held_side_rose_>=5c"))


def main() -> int:
    all_sw, summaries = [], []
    for rows in _windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        if _f(rows[0]["timestamp"]) < SYSTEMATIC_REGIME_EPOCH:
            continue
        sw = osc.switches(rows)
        for s in sw:
            s["window"] = rows[0]["window_id"]
            s["asset"] = rows[0]["asset"]
        all_sw.extend(sw)
        ws = osc.window_summary(rows)
        if ws:
            ws["window"] = rows[0]["window_id"]
            summaries.append(ws)

    # --- P4: the pair price at which L5's inventory actually changes sides
    last_pair = [s["last_pair"] for s in all_sw]
    avg_pair = [s["avg_pair"] for s in all_sw if s["avg_pair"] is not None]
    p4 = {
        "definition": {
            "last_pair": "price of the LAST same-side fill + price of the "
                         "FIRST opposite-side fill",
            "avg_pair": "RUNNING AVERAGE cost of the held side + price of the "
                        "first opposite-side fill (the analogue of our "
                        "COMPLETE gate)",
            "maker_taker": "UNKNOWN for L5 and never inferred; this compares "
                           "ECONOMIC PRICE, not execution type",
        },
        "last_pair": {"distribution": osc.distribution(last_pair),
                      "bands": osc.band_histogram(last_pair)},
        "avg_pair": {"distribution": osc.distribution(avg_pair),
                     "bands": osc.band_histogram(avg_pair)},
    }
    # by TTE and price band, because a single number would hide the regime
    by_tte, by_band = defaultdict(list), defaultdict(list)
    for s in all_sw:
        if s["avg_pair"] is None:
            continue
        by_tte[_bucket(s["tte_s"], TTE_BUCKETS)].append(s["avg_pair"])
        by_band[_bucket(s["last_same_side_price"], PRICE_BANDS)].append(
            s["avg_pair"])
    p4["avg_pair_by_tte"] = {k: osc.distribution(v)
                             for k, v in sorted(by_tte.items())}
    p4["avg_pair_by_price_band"] = {k: osc.distribution(v)
                                    for k, v in sorted(by_band.items())}

    # --- P2: what the switch looks like mechanically
    moves = Counter()
    for s in all_sw:
        # the TEMPORAL move: how far the held side's own price drifted over
        # the run that preceded the switch. Negative = the held side got
        # cheaper, i.e. the market moved AGAINST the position.
        d = s.get("held_side_drift_during_run")
        if d is None:
            continue
        moves[_bucket(d, MOVE_BANDS)] += 1
    n_sw = len(all_sw) or 1
    near = [s for s in all_sw if s["tte_s"] <= 60]
    far = [s for s in all_sw if s["tte_s"] > 300]
    p2 = {
        "n_switches": len(all_sw),
        "n_windows": len(summaries),
        "A_price_moved_before_the_switch": {
            "held_side_drift_during_run": osc.distribution(
                [s["held_side_drift_during_run"] for s in all_sw]),
            "cross_side_gap_not_a_move": osc.distribution(
                [s["price_change_across_switch"] for s in all_sw]),
            "movement_classes": {k: {"n": v, "share": round(v / n_sw, 4)}
                                 for k, v in moves.most_common()},
        },
        "B_alternation_without_movement": {
            "share_with_held_side_drift_under_1c": round(
                sum(1 for s in all_sw
                    if s["held_side_drift_during_run"] is not None
                    and abs(s["held_side_drift_during_run"]) < 0.01) / n_sw, 4),
        },
        "C_switching_near_settlement": {
            "switches_with_tte<=60s": len(near),
            "switches_with_tte>300s": len(far),
            "run_length_before_switch_tte<=60s": osc.distribution(
                [s["run_length_before_switch"] for s in near]),
            "run_length_before_switch_tte>300s": osc.distribution(
                [s["run_length_before_switch"] for s in far]),
            "seconds_between_switches_tte<=60s": osc.distribution(
                [s["run_seconds"] for s in near]),
            "seconds_between_switches_tte>300s": osc.distribution(
                [s["run_seconds"] for s in far]),
        },
        "D_inventory_magnitude_at_switch": {
            "held_qty_before_switch": osc.distribution(
                [s["held_qty_before"] for s in all_sw]),
            "residual_before_switch_abs": osc.distribution(
                [abs(s["residual_before"]) for s in all_sw]),
            "matched_before_switch": osc.distribution(
                [s["matched_before"] for s in all_sw]),
        },
        "run_length_before_switch": osc.distribution(
            [s["run_length_before_switch"] for s in all_sw]),
        "seconds_between_switches": osc.distribution(
            [s["run_seconds"] for s in all_sw]),
        "tte_at_switch_s": osc.distribution([s["tte_s"] for s in all_sw]),
    }

    # --- P3 input: L5's own trajectory numbers
    p3 = {
        "switch_rate_per_fill": osc.distribution(
            [w["switch_rate_per_fill"] for w in summaries]),
        "switches_per_minute": osc.distribution(
            [w["switches_per_minute"] for w in summaries
             if w["switches_per_minute"] is not None]),
        "median_run_length": osc.distribution(
            [w["median_run_length"] for w in summaries]),
        "matched_fraction_at_settlement": osc.distribution(
            [w["matched_fraction"] for w in summaries]),
        "windows_with_zero_switches": sum(1 for w in summaries
                                          if w["switches"] == 0),
        "windows": len(summaries),
        "fills_per_window": osc.distribution([w["fills"] for w in summaries]),
    }

    doc = {"labels": "read-only; L5 public fills only; maker/taker UNKNOWN",
           "P4_switch_pair_price": p4,
           "P2_oscillation_mechanism": p2,
           "P3_l5_trajectory": p3}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(json.dumps(doc["P4_switch_pair_price"]["avg_pair"], indent=1))
    print(json.dumps(doc["P4_switch_pair_price"]["last_pair"]["bands"], indent=1))
    print(json.dumps(doc["P3_l5_trajectory"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
