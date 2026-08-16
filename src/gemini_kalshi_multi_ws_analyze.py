"""Analyze one asset from multi-asset Gemini/Kalshi fast shadow logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_btc_ws_analyze import analyze

ASSETS = {"BTC", "ETH", "SOL", "XRP"}


def load_asset_rows(paths: list[Path], asset: str) -> list[dict[str, Any]]:
    asset = asset.upper()
    if asset not in ASSETS:
        raise ValueError(f"unsupported asset={asset}")
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("asset") == asset and row.get("read_only") is True:
                    rows.append(row)
    rows.sort(key=lambda r: int(r["receive_ts_ms"]))
    return rows


def analyze_asset(paths: list[Path], asset: str) -> dict[str, Any]:
    rows = load_asset_rows(paths, asset)
    result = analyze(rows)
    result["asset"] = asset.upper()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--asset", required=True, choices=sorted(ASSETS))
    args = parser.parse_args()
    print(json.dumps(analyze_asset(args.paths, args.asset), separators=(",", ":")))


if __name__ == "__main__":
    main()
