"""Wiring between the raw cache, the derived tables, and the analysis."""

from __future__ import annotations

import glob
import json
import os
from typing import Any

from src.l5 import analysis as analysis_mod
from src.l5 import config, csvout, manual, report
from src.l5.fetch import (
    HttpJson,
    fetch_activity,
    fetch_markets,
    fetch_positions,
    fetch_trades,
    write_manifest,
)
from src.l5.http import RawCache
from src.l5.normalize import (
    build_market_index,
    condition_ids_in,
    extract_non_trade_activity,
    normalize,
)
from src.l5.windows import build_pnl_index, build_windows


def run_fetch(
    cache_dir: str,
    wallet: str = config.TARGET_WALLET,
    profile: str = config.TARGET_PROFILE,
    start_ts: int | None = None,
    end_ts: int | None = None,
    reset: bool = True,
) -> dict:
    """Pull everything the public endpoints will serve into the raw cache."""
    cache = RawCache(cache_dir)
    if reset:
        for name in (
            config.RAW_ACTIVITY_JSONL,
            config.RAW_TRADES_JSONL,
            config.RAW_POSITIONS_JSONL,
            config.RAW_MARKETS_JSONL,
        ):
            cache.truncate(name)

    http = HttpJson()
    stats = [
        fetch_activity(http, cache, wallet, start_ts=start_ts, end_ts=end_ts),
        fetch_trades(http, cache, wallet),
        fetch_positions(http, cache, wallet),
    ]

    # Market metadata is looked up only for markets this wallet actually
    # touched -- no broad market crawl.
    condition_ids: set[str] = set()
    for name in (
        config.RAW_ACTIVITY_JSONL,
        config.RAW_TRADES_JSONL,
        config.RAW_POSITIONS_JSONL,
    ):
        condition_ids |= condition_ids_in(cache.read_records(name))
    stats.append(fetch_markets(http, cache, condition_ids))

    return write_manifest(cache, wallet, profile, stats)


def load_cache(cache_dir: str) -> dict[str, list[dict]]:
    cache = RawCache(cache_dir)
    return {
        "activity": cache.read_records(config.RAW_ACTIVITY_JSONL),
        "trades": cache.read_records(config.RAW_TRADES_JSONL),
        "positions": cache.read_records(config.RAW_POSITIONS_JSONL),
        "markets": cache.read_records(config.RAW_MARKETS_JSONL),
    }


def load_manual(manual_dir: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Screenshot transcriptions, converted to API-shaped records."""
    paths = sorted(glob.glob(os.path.join(manual_dir, "*.json")))
    if not paths:
        return [], [], []
    activity, markets, stats = manual.load_batches(paths)
    return activity, markets, [s.as_dict() for s in stats]


def build(cache_dir: str, output_dir: str, manual_dir: str | None = None) -> dict[str, Any]:
    """Cache (+ any screenshot batches) -> the CSVs. Never touches the network."""
    raw = load_cache(cache_dir)
    manual_activity, manual_markets, manual_stats = load_manual(
        manual_dir or config.MANUAL_DIR
    )

    market_index = build_market_index(raw["markets"] + manual_markets)

    # Screenshot rows are a separate source, never merged into the API sources:
    # they carry no transaction hash, so cross-source dedup cannot match them
    # against fetched fills. Keeping them apart means a later fetch can be
    # compared against them rather than silently double-counting.
    trades, norm_stats = normalize(
        {
            "activity": raw["activity"],
            "trades": raw["trades"],
            "screenshot": manual_activity,
        },
        market_index,
    )
    non_trade = extract_non_trade_activity(
        raw["activity"] + manual_activity, market_index
    )
    pnl_index = build_pnl_index(raw["positions"])
    windows = build_windows(trades, pnl_index)

    os.makedirs(output_dir, exist_ok=True)
    written = {
        config.OUT_ALL_TRADES: csvout.write_all_trades(
            os.path.join(output_dir, config.OUT_ALL_TRADES), trades
        ),
        config.OUT_WINDOW_SEQUENCES: csvout.write_window_sequences(
            os.path.join(output_dir, config.OUT_WINDOW_SEQUENCES), windows
        ),
        config.OUT_WINDOWS_SUMMARY: csvout.write_windows_summary(
            os.path.join(output_dir, config.OUT_WINDOWS_SUMMARY), windows
        ),
        "l5_non_trade_activity.csv": csvout.write_non_trade_activity(
            os.path.join(output_dir, "l5_non_trade_activity.csv"), non_trade
        ),
    }

    return {
        "trades": trades,
        "windows": windows,
        "normalize_stats": norm_stats.as_dict(),
        "manual_stats": manual_stats,
        "markets_classified": len(market_index),
        "markets_in_scope": sum(1 for m in market_index.values() if m.in_scope),
        "rows_written": written,
    }


def analyze(
    cache_dir: str,
    output_dir: str,
    findings_path: str | None,
    manual_dir: str | None = None,
) -> dict[str, Any]:
    built = build(cache_dir, output_dir, manual_dir)
    results = analysis_mod.analyze(built["trades"], built["windows"])
    results["normalize_stats"] = built["normalize_stats"]
    results["manual_stats"] = built["manual_stats"]
    results["markets_classified"] = built["markets_classified"]
    results["markets_in_scope"] = built["markets_in_scope"]

    analysis_path = os.path.join(output_dir, config.OUT_ANALYSIS_JSON)
    with open(analysis_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=True, default=str)

    manifest = None
    manifest_path = os.path.join(cache_dir, config.FETCH_MANIFEST_JSON)
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)

    if findings_path:
        markdown = report.render(results, built["windows"], manifest)
        with open(findings_path, "w", encoding="utf-8") as handle:
            handle.write(markdown)

    return {
        "analysis": results,
        "analysis_path": analysis_path,
        "findings_path": findings_path,
        "rows_written": built["rows_written"],
        "normalize_stats": built["normalize_stats"],
        "manual_stats": built["manual_stats"],
    }
