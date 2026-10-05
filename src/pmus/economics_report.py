"""Render the frozen PMUS BTC15m sizing / risk / expectancy tables.

Outcome-blind: reads no tape. Run with

    python -m src.pmus.economics_report            # markdown
    python -m src.pmus.economics_report --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json

from src.pmus import btc15m_spec as spec


def _d(cents) -> str:
    return "-" if cents is None else f"${cents / 100:,.2f}"


def markdown() -> str:
    rows = spec.sizing_table()
    p = spec.provenance()
    out: list[str] = []
    out.append("# PMUS BTC15m underdog — frozen economics & risk tables")
    out.append("")
    out.append(f"Freeze `{p['freeze_label']}` ({p['freeze_date']}). Generated from "
               "`src/pmus/btc15m_spec.py`; do not hand-edit.")
    out.append("")
    out.append(f"- Fee source: `{p['fee_source']}`")
    out.append(f"- Fee schedule fingerprint: `{p['fee_schedule_fingerprint']}`")
    out.append(f"- Staking: {p['staking_rule']}")
    out.append("")
    out.append("## 1. >= $5 NET sizing table (executable ask 1c..49c)")
    out.append("")
    out.append("| Ask | Contracts | Spent | Fee | Net if win | Loss if lose | "
               "Fee-incl. break-even win prob | Required touch depth |")
    out.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        be = "-" if r.breakeven_win_prob is None else f"{r.breakeven_win_prob:.4f}"
        out.append(
            f"| {r.entry_ask_cents}c | {r.contracts if r.feasible else 'INFEASIBLE'} | "
            f"{_d(r.spend_cents)} | {_d(r.entry_fee_cents)} | {_d(r.net_profit_win_cents)} | "
            f"{_d(r.loss_if_loser_cents)} | {be} | "
            f"{r.required_touch_depth if r.feasible else '-'} |"
        )
    out.append("")
    out.append("Break-even is fee-inclusive: `spend / (spend + net_win)`. A strategy at "
               "entry price X is unprofitable unless the TRUE win rate exceeds that column, "
               "with uncertainty accounted for.")
    out.append("")
    out.append("## 2. Consecutive-loss bankroll consequences (flat stakes, no Martingale)")
    out.append("")
    out.append("| Ask | Stake | " + " | ".join(f"{k} losses" for k in spec.LOSS_STREAKS) + " |")
    out.append("|---:|---:|" + "---:|" * len(spec.LOSS_STREAKS))
    for r in rows:
        cells = " | ".join(_d(spec.loss_streak_drawdown_cents(r, k)) for k in spec.LOSS_STREAKS)
        out.append(f"| {r.entry_ask_cents}c | {_d(r.loss_if_loser_cents)} | {cells} |")
    out.append("")
    out.append("Losses are total: the underdog settles at 0 and the whole stake is lost. "
               "Stake never depends on prior results.")
    out.append("")
    out.append("## 3. Expected P&L per trade vs hypothetical TRUE win probability")
    out.append("")
    grid = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    out.append("| Ask | BE | " + " | ".join(f"p={g:.2f}" for g in grid) + " |")
    out.append("|---:|---:|" + "---:|" * len(grid))
    for r in rows:
        be = "-" if r.breakeven_win_prob is None else f"{r.breakeven_win_prob:.3f}"
        cells = " | ".join(_d(spec.expected_pnl_cents(r, g)) for g in grid)
        out.append(f"| {r.entry_ask_cents}c | {be} | {cells} |")
    out.append("")
    out.append("The full grid (" + f"{spec.WIN_PROB_GRID[0]:.3f}..{spec.WIN_PROB_GRID[-1]:.3f}"
               ") is available via `--json`. When empirical calibration arrives, read the "
               "observed win rate (and its interval) straight into this table to obtain "
               "expectancy per trade; multiply by observed opportunities/day for $/day.")
    return "\n".join(out) + "\n"


def payload() -> dict:
    rows = spec.sizing_table()
    return {
        "provenance": spec.provenance(),
        "checkpoints_min_remaining": list(spec.CHECKPOINTS_MIN_REMAINING),
        "price_buckets": [{"name": n, "lo_cents": lo, "hi_cents": hi}
                          for n, lo, hi in spec.PRICE_BUCKETS],
        "separate_state": spec.NO_UNDERDOG_BUCKET,
        "windows_per_day": spec.WINDOWS_PER_DAY,
        "sizing": [r.as_dict() for r in rows],
        "loss_streaks": {
            str(r.entry_ask_cents): {str(k): spec.loss_streak_drawdown_cents(r, k)
                                     for k in spec.LOSS_STREAKS}
            for r in rows
        },
        "expectancy_cents": {
            str(r.entry_ask_cents): {f"{g:.3f}": spec.expected_pnl_cents(r, g)
                                     for g in spec.WIN_PROB_GRID}
            for r in rows
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()
    print(json.dumps(payload(), indent=2) if args.json else markdown(), end="")


if __name__ == "__main__":
    main()
