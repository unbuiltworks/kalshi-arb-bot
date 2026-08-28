"""Compact CLI wrapper for the V4 l5 inventory replay.

Writes the full replay JSON to disk while printing aggregate economics plus the
small set of markets that completed pairs. No network or order entry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.gemini_l5_inventory_replay_v4 import replay_capture


def _paired_markets(summary: dict, ledger_key: str) -> list[dict]:
    out: list[dict] = []
    for market in summary.get("markets_detail", []):
        ledger = market.get(ledger_key, {})
        pairs = ledger.get("pair_records") or []
        if not pairs:
            continue
        out.append(
            {
                "ticker": market.get("ticker"),
                "asset": market.get("asset"),
                "horizon": market.get("horizon"),
                "pairs": pairs,
                "locked_edge_total_after_fees": ledger.get("locked_edge_total_after_fees"),
                "residual_side": ledger.get("residual_side"),
                "residual_qty": ledger.get("residual_qty"),
                "residual_acquisition_cost": ledger.get("residual_acquisition_cost"),
            }
        )
    return out


def compact(summary: dict) -> dict:
    return {
        "type": summary["type"],
        "classification": summary["classification"],
        "markets": summary["markets"],
        "source_plans": summary["source_plans"],
        "source_trades": summary["source_trades"],
        "pair_floor_after_fees": summary["pair_floor_after_fees"],
        "max_naked_units_per_market": summary["max_naked_units_per_market"],
        "max_completed_pairs_per_market": summary["max_completed_pairs_per_market"],
        "max_directional_entry_price": summary["max_directional_entry_price"],
        "tape_touch": summary["tape_touch"],
        "tape_touch_paired_markets": _paired_markets(summary, "tape_touch"),
        "strict_trade_through": summary["strict_trade_through"],
        "strict_paired_markets": _paired_markets(summary, "strict_trade_through"),
        "realized_pnl": summary["realized_pnl"],
        "order_entry": summary["order_entry"],
        "credentials_loaded": summary["credentials_loaded"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = replay_capture(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(compact(summary), sort_keys=True))


if __name__ == "__main__":
    main()
