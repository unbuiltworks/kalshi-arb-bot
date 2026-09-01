"""Compact CLI wrapper for Gemini incentive/MM replay V2.

Writes the full replay JSON when requested but prints only aggregate economics so
AWS SSM/GitHub logs cannot be truncated by per-market detail. Read-only only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.gemini_incentive_mm_replay_v2 import replay_capture


def compact(result: dict) -> dict:
    return {
        "classification": result["classification"],
        "order_entry": result["order_entry"],
        "realized_pnl": result["realized_pnl"],
        "markets": result["markets"],
        "source_books": result["source_books"],
        "source_trades": result["source_trades"],
        "quote_quantity": result["quote_quantity"],
        "tape_touch": result["tape_touch"],
        "strict_trade_through": result["strict_trade_through"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay_capture(args.capture)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(compact(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
