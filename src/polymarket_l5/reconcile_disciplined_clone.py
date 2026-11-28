"""Reconcile no-policy fill replay to the harvest's own per-window P&L fields."""
from __future__ import annotations

import argparse
from pathlib import Path
import json

from .disciplined_clone import load_sequences, load_summary, replay_window, _f


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sequences", default="analysis/l5/l5_window_sequences.csv")
    p.add_argument("--summary", default="analysis/l5/l5_windows_summary.csv")
    p.add_argument("--scale", type=float, default=0.10)
    args = p.parse_args()

    summaries = load_summary(Path(args.summary))
    groups = load_sequences(Path(args.sequences), recent_only=True)
    rows = []
    for wid, seq in groups.items():
        summary = summaries.get(wid)
        if not summary:
            continue
        replay = replay_window(seq, summary, scale=args.scale, pair_ceiling=None, residual_cap=None)
        replay_pnl = replay.get("settlement_pnl")
        derived_raw = summary.get("pnl_derived_resolution")
        positions_raw = summary.get("pnl_polymarket_positions")
        derived = None if derived_raw in (None, "") else _f(derived_raw) * args.scale
        positions = None if positions_raw in (None, "") else _f(positions_raw) * args.scale
        if replay_pnl is None:
            continue
        rows.append({
            "window_id": wid,
            "replay": float(replay_pnl),
            "derived": derived,
            "positions": positions,
            "replay_minus_derived": None if derived is None else float(replay_pnl) - derived,
            "replay_minus_positions": None if positions is None else float(replay_pnl) - positions,
        })

    comparable_derived = [r for r in rows if r["derived"] is not None]
    comparable_positions = [r for r in rows if r["positions"] is not None]
    mismatches = sorted(
        [r for r in comparable_derived if abs(r["replay_minus_derived"]) > 1e-6],
        key=lambda r: abs(r["replay_minus_derived"]),
        reverse=True,
    )
    out = {
        "classification": "ANALYSIS_REPLAY_PNL_RECONCILIATION",
        "scale": args.scale,
        "replay_resolved_windows": len(rows),
        "derived_comparable_windows": len(comparable_derived),
        "positions_comparable_windows": len(comparable_positions),
        "replay_pnl_total": sum(r["replay"] for r in rows),
        "derived_pnl_total_scaled": sum(r["derived"] for r in comparable_derived),
        "positions_pnl_total_scaled": sum(r["positions"] for r in comparable_positions),
        "replay_minus_derived_total": sum(r["replay_minus_derived"] for r in comparable_derived),
        "replay_minus_positions_total": sum(r["replay_minus_positions"] for r in comparable_positions),
        "derived_mismatch_window_count": len(mismatches),
        "largest_derived_mismatches": mismatches[:10],
        "live_order_entry": False,
        "realized_pnl": False,
    }
    print("L5_DISCIPLINED_CLONE_RECONCILIATION=" + json.dumps(out, separators=(",", ":"), sort_keys=True))
    print("L5_DISCIPLINED_CLONE_RECONCILIATION_COMPLETE")


if __name__ == "__main__":
    main()
