"""Compact CLI wrapper for the read-only Gemini q10 retained-tape replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.gemini_dual_maker_replay_q10 import replay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = replay(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    compact = {k: v for k, v in result.items() if k != "markets_detail"}
    print("Q10_REPLAY_SUMMARY")
    print(json.dumps(compact, sort_keys=True))

    assert result["classification"] == "SHADOW_REPLAY_NOT_REALIZED"
    assert result["order_entry"] is False
    assert result["credentials_loaded"] is False
    assert result["realized_pnl"] is None
    print(f"Q10_REPLAY_OUTPUT={args.output}")


if __name__ == "__main__":
    main()
