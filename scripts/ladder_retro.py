"""RETROSPECTIVE / IN-SAMPLE replay of the frozen L-series arms.

Strictly diagnostic (audit protocol, 2026-08-31 night): replays the frozen
ladder_frontier arms over the FULL committed tape history — including
windows that closed before the 21:45 UTC evidence cutoff — to answer
mechanical questions tonight (bugs? join-only fills dominating? breadth vs
depth behaving as expected?) WITHOUT touching the frozen module or the
fresh clock. Nothing here is a pass/fail verdict; the sealed first read
stays at 2026-09-01 13:00 UTC on future-only windows.

Imports the frozen evaluate() — no strategy constants are redefined here.

    python3 scripts/ladder_retro.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gemini_maker.completion_frontier import load_ref_lag_windows
from src.gemini_maker.rescue_frontier import _win_close_epoch, load_settlements
from src.gemini_maker.ladder_frontier import ARMS, _in_scope, evaluate

OUT = Path("analysis/gemini/ladder_retro.json")


def main() -> int:
    windows = load_ref_lag_windows("data/ref_lag", 0.0)
    settlements = load_settlements()
    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "sample": "RETROSPECTIVE_IN_SAMPLE_DIAGNOSTIC_ONLY",
        "note": ("Full-tape replay incl. pre-cutoff windows. Mechanics "
                 "diagnostics only; never a pass/fail verdict."),
        "arms": {},
    }
    for arm, spec in ARMS.items():
        results = []
        for symbol, rows in windows.items():
            if not _in_scope(symbol, spec["scope"]):
                continue
            r = evaluate(symbol, rows, spec["offsets"], settlements.get(symbol))
            if r is not None:
                results.append(r)
        scored = [r for r in results if r["settlement_pnl"] is not None]
        total_q = sum(r["yes_qty"] + r["no_qty"] for r in results)
        rung: dict = defaultdict(int)
        for r in results:
            for k, v in r["rung_fill_counts"].items():
                rung[k] += v
        pcs = sorted(r["pair_cost"] for r in results if r["pair_cost"] is not None)
        agg = {
            "engaged": len(results),
            "scored_with_labels": len(scored),
            "n_fills": sum(r["n_fills"] for r in results),
            "turnover_usdc": round(sum(r["buy_usdc"] for r in results), 2),
            "matched_pct_qty": round(
                2 * sum(r["matched_qty"] for r in results) / max(total_q, 1e-9), 4),
            "both_sides_windows_pct": round(
                100 * sum(1 for r in results
                          if r["yes_qty"] > 0 and r["no_qty"] > 0) /
                max(len(results), 1), 1),
            "median_pair_cost": round(pcs[len(pcs) // 2], 4) if pcs else None,
            "pair_locked": round(sum(r["pair_locked"] for r in results), 4),
            "rung_fill_counts": dict(rung),
            "labeled_subset_diag": {
                "realized_settlement_pnl": round(
                    sum(r["settlement_pnl"] for r in scored), 4) if scored else None,
                "residual_pnl": round(
                    sum(r["settlement_pnl"] for r in scored) -
                    sum(r["pair_locked"] for r in scored), 4) if scored else None,
                "turnover": round(sum(r["buy_usdc"] for r in scored), 2),
                "max_single_window_loss": round(
                    min((r["settlement_pnl"] for r in scored), default=0.0), 4),
            },
        }
        out["arms"][arm] = agg
    OUT.write_text(json.dumps(out, indent=1))
    for arm, a in out["arms"].items():
        d = a["labeled_subset_diag"]
        print(f"{arm}: eng={a['engaged']} (labeled {a['scored_with_labels']}) "
              f"fills={a['n_fills']} turn=${a['turnover_usdc']} "
              f"matched={a['matched_pct_qty']} both%={a['both_sides_windows_pct']} "
              f"medPair={a['median_pair_cost']} rungs={a['rung_fill_counts']}")
        print(f"    diag(labeled): pnl={d['realized_settlement_pnl']} "
              f"residual={d['residual_pnl']} turn=${d['turnover']} "
              f"maxLoss={d['max_single_window_loss']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
