"""Reprice aligned Gemini/Kalshi watcher JSONL with live Kalshi Series fees.

Read-only research helper. It fetches authoritative current Series fee metadata for
BTC/ETH/SOL/XRP and reuses the episode-aware watcher analyzer so repeated resting
one-second depth is not multiplied into fictitious P&L. Unknown/unmapped fee types
fail closed. Settlement remains INDEX_MISMATCH_RESEARCH_ONLY.

Because Series fee metadata is current-state metadata, this command labels its fee
scope explicitly. Do not use it to silently reprice logs spanning a historical fee
change without separately reconstructing the fee schedule at each observation time.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import httpx

from src.gemini_kalshi_kalshi_fees import (
    KalshiSeriesFeeMetadata,
    fetch_series_fee_metadata,
)
from src.gemini_kalshi_watch_analyze import analyze_rows, load_rows

D = Decimal
SERIES_BY_ASSET = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}


def analyze_with_authoritative_metadata(
    rows: list[dict[str, Any]],
    metadata_by_asset: dict[str, KalshiSeriesFeeMetadata],
    *,
    max_quote_skew_ms: int = 500,
) -> dict[str, Any]:
    """Analyze each asset with its own mapped Series multiplier; fail closed otherwise."""
    by_asset: dict[str, Any] = {}
    total_profit = D("0")
    total_episodes = 0
    mapped_assets: list[str] = []
    blocked_assets: dict[str, str] = {}

    for asset in sorted(SERIES_BY_ASSET):
        asset_rows = [row for row in rows if str(row.get("asset") or "").upper() == asset]
        meta = metadata_by_asset.get(asset)
        if meta is None:
            blocked_assets[asset] = "FEE_METADATA_UNAVAILABLE_DO_NOT_TRADE"
            by_asset[asset] = {
                "rows": len(asset_rows),
                "fee_status": "FEE_METADATA_UNAVAILABLE_DO_NOT_TRADE",
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            }
            continue
        if meta.formula_status != "MAPPED_QUADRATIC_TAKER":
            blocked_assets[asset] = meta.formula_status
            by_asset[asset] = {
                "rows": len(asset_rows),
                "fee_status": meta.formula_status,
                "fee_metadata": meta.as_dict(),
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            }
            continue

        result = analyze_rows(
            asset_rows,
            fee_multiplier=D(meta.fee_multiplier),
            max_quote_skew_ms=max_quote_skew_ms,
        )
        asset_result = dict(result.get("by_asset", {}).get(asset, {}))
        asset_result.update({
            "rows": len(asset_rows),
            "fee_status": "MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY",
            "fee_metadata": meta.as_dict(),
            "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        })
        by_asset[asset] = asset_result
        mapped_assets.append(asset)
        total_episodes += int(result.get("unique_positive_episodes", 0))
        total_profit += D(str(result.get("shadow_profit_dollars_one_execution_per_episode", "0")))

    timestamps = [int(row["ts_ms"]) for row in rows if "ts_ms" in row]
    observation_ms = max(timestamps) - min(timestamps) if timestamps else 0
    projected_day = (
        total_profit * D("86400000") / D(observation_ms)
        if observation_ms > 0 else D("0")
    )
    return {
        "by_asset": by_asset,
        "mapped_assets": mapped_assets,
        "blocked_assets": blocked_assets,
        "unique_positive_episodes": total_episodes,
        "shadow_profit_dollars_one_execution_per_episode": str(total_profit),
        "observation_ms": observation_ms,
        "naive_projected_shadow_profit_per_day": str(projected_day),
        "projection_warning": "NAIVE_SHORT_SAMPLE_NOT_A_FORECAST",
        "fee_metadata_scope": "CURRENT_SERIES_METADATA_NOT_HISTORICAL_SNAPSHOT",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "repeated_depth_counted_as_pnl": False,
        "read_only": True,
        "order_entry": False,
    }


async def run(paths: list[Path], *, max_quote_skew_ms: int) -> dict[str, Any]:
    rows = load_rows(paths)
    async with httpx.AsyncClient(timeout=20.0) as http:
        fetched = await asyncio.gather(
            *(fetch_series_fee_metadata(http, ticker) for ticker in SERIES_BY_ASSET.values()),
            return_exceptions=True,
        )
    metadata: dict[str, KalshiSeriesFeeMetadata] = {}
    fetch_errors: dict[str, str] = {}
    for asset, value in zip(SERIES_BY_ASSET, fetched):
        if isinstance(value, Exception):
            fetch_errors[asset] = f"{type(value).__name__}: {value}"
        else:
            metadata[asset] = value
    result = analyze_with_authoritative_metadata(
        rows,
        metadata,
        max_quote_skew_ms=max_quote_skew_ms,
    )
    result["fee_metadata_fetch_errors"] = fetch_errors
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    args = parser.parse_args()
    result = asyncio.run(run(args.paths, max_quote_skew_ms=args.max_quote_skew_ms))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
