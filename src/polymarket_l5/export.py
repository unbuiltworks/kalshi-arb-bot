"""Build the three study CSVs (plus an exclusion audit) from the raw cache.

Everything downstream of the network lives here: given a raw cache, `build`
is deterministic and offline.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .cache import RawCache, write_jsonl
from .inventory import InventoryState, apply_trade, running_states
from .normalize import load_non_trade_activity, load_trade_records
from .pnl import derived_window_pnl, load_position_pnl
from .timeutil import iso_utc
from .windows import MarketMeta, annotate_trades, load_market_index

TRADE_COLUMNS = [
    "timestamp",
    "datetime_utc",
    "asset",
    "window_minutes",
    "window_id",
    "market_id",
    "market_slug",
    "event_slug",
    "event_title",
    "title",
    "outcome",
    "side",
    "price",
    "shares",
    "dollars",
    "condition_id",
    "transaction_hash",
    "seconds_from_window_start",
    "seconds_to_expiry",
    "window_start_utc",
    "window_end_utc",
    "window_source",
    "token_id",
    "outcome_raw",
    "outcome_mapping",
    "source",
    "market_flags",
]

SEQUENCE_COLUMNS = [
    "window_id",
    "asset",
    "window_minutes",
    "market_slug",
    "event_slug",
    "trade_seq",
    "timestamp",
    "datetime_utc",
    "seconds_from_window_start",
    "seconds_to_expiry",
    "seconds_since_prev_trade",
    "outcome",
    "side",
    "price",
    "shares",
    "dollars",
    "cum_up_qty",
    "cum_up_spend",
    "cum_down_qty",
    "cum_down_spend",
    "wavg_up_price",
    "wavg_down_price",
    "matched_qty",
    "matched_pair_unit_cost",
    "matched_pair_cost",
    "excess_qty",
    "excess_abs_qty",
    "excess_cost",
    "dominant_side",
    "cum_gross_dollars_bought",
    "cum_realized_pnl_trading",
    "cum_trade_count",
    "transaction_hash",
    "anomalies",
]


def summary_columns() -> List[str]:
    columns = [
        "window_id",
        "asset",
        "window_minutes",
        "market_slug",
        "event_slug",
        "title",
        "window_start_utc",
        "window_end_utc",
        "window_source",
        "first_trade_utc",
        "first_side",
        "first_price",
        "first_action",
        "first_trade_seconds_from_start",
        "total_trades",
        "total_buy_trades",
        "total_sell_trades",
        "total_dollars_bought",
        "total_dollars_sold",
        "max_price_bought",
        "max_price_bought_side",
    ]
    for threshold in config.PRICE_THRESHOLDS:
        columns.append(f"any_buy_ge_{int(threshold * 100)}c")
    for name, _low, _high in config.PRICE_BANDS:
        columns.append(f"qty_band_{name}")
        columns.append(f"dollars_band_{name}")
    columns += [
        "final_up_qty",
        "final_down_qty",
        "final_up_avg_price",
        "final_down_avg_price",
        "buy_up_qty",
        "buy_down_qty",
        "buy_up_avg_price",
        "buy_down_avg_price",
        "matched_qty",
        "matched_pair_unit_cost",
        "matched_pair_cost",
        "residual_qty_signed",
        "residual_qty_abs",
        "residual_side",
        "residual_cost",
        "balanced_at_end",
        "seconds_first_to_opposite_side",
        "opposite_side_ever_bought",
        "cheaper_side_first",
        "first_side_price_vs_other",
        "winning_outcome",
        "resolution_source",
        "pnl_derived_resolution",
        "pnl_polymarket_positions",
        "pnl_source",
        "realized_trading_pnl_within_window",
        "anomalies",
    ]
    return columns


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def group_by_window(trades: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group fills by window id (condition id), each group time-ordered."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[trade.get("window_id") or trade.get("condition_id") or ""].append(trade)
    for window_id, rows in groups.items():
        rows.sort(
            key=lambda row: (
                row.get("timestamp") or 0,
                row.get("trade_seq_hint", 0),
                row.get("transaction_hash") or "",
            )
        )
    return dict(groups)


def price_band_name(price: Optional[float]) -> str:
    if price is None:
        return ""
    for name, low, high in config.PRICE_BANDS:
        if low <= price < high:
            return name
    return ""


def build_trade_rows(trades: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for trade in trades:
        row = dict(trade)
        row["window_start_utc"] = iso_utc(trade.get("window_start_ts"))
        row["window_end_utc"] = iso_utc(trade.get("window_end_ts"))
        rows.append(row)
    return rows


def build_sequence_rows(groups: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for window_id in sorted(groups, key=lambda key: (groups[key][0].get("timestamp") or 0, key)):
        window_trades = groups[window_id]
        for trade, state in zip(window_trades, running_states(window_trades)):
            row = dict(trade)
            row.update(state)
            rows.append(row)
    return rows


def _first_opposite_latency(window_trades: Sequence[Dict[str, Any]]) -> Tuple[Optional[int], bool, str]:
    """Seconds from the first BUY to the first BUY on the opposite outcome."""
    first_buy = next((t for t in window_trades if (t.get("side") or "").upper() == "BUY"), None)
    if first_buy is None:
        return None, False, ""
    first_side = first_buy.get("outcome") or ""
    opposite = "Down" if first_side == "Up" else "Up"
    for trade in window_trades:
        if (trade.get("side") or "").upper() != "BUY" or trade.get("outcome") != opposite:
            continue
        start, end = first_buy.get("timestamp"), trade.get("timestamp")
        latency = end - start if (start is not None and end is not None) else None
        return latency, True, first_side
    return None, False, first_side


def build_summary_row(
    window_id: str,
    window_trades: Sequence[Dict[str, Any]],
    meta: Optional[MarketMeta],
    position_pnl: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    state = InventoryState()
    band_qty: Dict[str, float] = {name: 0.0 for name, _l, _h in config.PRICE_BANDS}
    band_dollars: Dict[str, float] = {name: 0.0 for name, _l, _h in config.PRICE_BANDS}
    max_price, max_price_side = None, ""
    buy_dollars = sell_dollars = 0.0
    buy_count = sell_count = 0

    for trade in window_trades:
        apply_trade(state, trade)
        action = (trade.get("side") or "").upper()
        price = trade.get("price")
        dollars = float(trade.get("dollars") or 0.0)
        shares = float(trade.get("shares") or 0.0)
        if action == "BUY":
            buy_count += 1
            buy_dollars += dollars
            band = price_band_name(price)
            if band:
                band_qty[band] += shares
                band_dollars[band] += dollars
            if price is not None and (max_price is None or price > max_price):
                max_price, max_price_side = price, trade.get("outcome") or ""
        elif action == "SELL":
            sell_count += 1
            sell_dollars += dollars

    first_trade = window_trades[0] if window_trades else {}
    first_buy = next((t for t in window_trades if (t.get("side") or "").upper() == "BUY"), first_trade)
    latency, opposite_bought, first_side = _first_opposite_latency(window_trades)

    # Was the first side bought the cheaper of the two at the time of that buy?
    first_price = first_buy.get("price")
    cheaper_side_first = ""
    first_vs_other = None
    if first_price is not None:
        # Complement pricing: on a binary Up/Down market the two sides sum to ~$1,
        # so the first side is the cheaper one exactly when its price < $0.50.
        first_vs_other = round(first_price - (1.0 - first_price), 6)
        cheaper_side_first = "yes" if first_price < 0.5 else ("tie" if first_price == 0.5 else "no")

    winning_outcome = meta.winning_outcome if meta else ""
    derived = derived_window_pnl(state, winning_outcome)
    reported = position_pnl.get(window_id)
    reported_value = (
        round(reported["realized_pnl"], 6) if reported and reported.get("has_realized") else None
    )
    if reported_value is not None:
        pnl_source = "polymarket_positions"
    elif derived is not None:
        pnl_source = "derived_resolution"
    else:
        pnl_source = "unavailable"

    row: Dict[str, Any] = {
        "window_id": window_id,
        "asset": (meta.asset if meta else first_trade.get("asset", "")),
        "window_minutes": (meta.window_minutes if meta else first_trade.get("window_minutes")),
        "market_slug": (meta.slug if meta else first_trade.get("market_slug", "")),
        "event_slug": (meta.event_slug if meta else first_trade.get("event_slug", "")),
        "title": (meta.title if meta else first_trade.get("title", "")),
        "window_start_utc": iso_utc(meta.start_ts if meta else None),
        "window_end_utc": iso_utc(meta.end_ts if meta else None),
        "window_source": (meta.window_source if meta else ""),
        "first_trade_utc": first_trade.get("datetime_utc", ""),
        "first_side": first_side or (first_buy.get("outcome") or ""),
        "first_price": first_price,
        "first_action": (first_trade.get("side") or ""),
        "first_trade_seconds_from_start": first_trade.get("seconds_from_window_start"),
        "total_trades": len(window_trades),
        "total_buy_trades": buy_count,
        "total_sell_trades": sell_count,
        "total_dollars_bought": round(buy_dollars, 6),
        "total_dollars_sold": round(sell_dollars, 6),
        "max_price_bought": max_price,
        "max_price_bought_side": max_price_side,
        "final_up_qty": round(state.up.qty, 6),
        "final_down_qty": round(state.down.qty, 6),
        "final_up_avg_price": round(state.up.wavg_price, 6),
        "final_down_avg_price": round(state.down.wavg_price, 6),
        "buy_up_qty": round(state.up.buy_qty, 6),
        "buy_down_qty": round(state.down.buy_qty, 6),
        "buy_up_avg_price": round(state.up.wavg_buy_price, 6),
        "buy_down_avg_price": round(state.down.wavg_buy_price, 6),
        "matched_qty": round(state.matched_qty, 6),
        "matched_pair_unit_cost": (
            None if state.matched_pair_unit_cost is None else round(state.matched_pair_unit_cost, 6)
        ),
        "matched_pair_cost": round(state.matched_pair_cost, 6),
        "residual_qty_signed": round(state.excess_qty, 6),
        "residual_qty_abs": round(abs(state.excess_qty), 6),
        "residual_side": state.dominant_side,
        "residual_cost": round(state.excess_cost, 6),
        "balanced_at_end": "yes" if state.dominant_side == "BALANCED" else "no",
        "seconds_first_to_opposite_side": latency,
        "opposite_side_ever_bought": "yes" if opposite_bought else "no",
        "cheaper_side_first": cheaper_side_first,
        "first_side_price_vs_other": first_vs_other,
        "winning_outcome": winning_outcome,
        "resolution_source": (meta.resolution_source if meta else "none"),
        "pnl_derived_resolution": derived,
        "pnl_polymarket_positions": reported_value,
        "pnl_source": pnl_source,
        "realized_trading_pnl_within_window": round(state.realized_pnl_trading, 6),
        "anomalies": ";".join(sorted(set(state.anomalies))),
    }
    for threshold in config.PRICE_THRESHOLDS:
        key = f"any_buy_ge_{int(threshold * 100)}c"
        row[key] = "yes" if (max_price is not None and max_price >= threshold) else "no"
    for name, _low, _high in config.PRICE_BANDS:
        row[f"qty_band_{name}"] = round(band_qty[name], 6)
        row[f"dollars_band_{name}"] = round(band_dollars[name], 6)
    return row


def build(
    cache: Optional[RawCache] = None,
    output_dir: Optional[Path] = None,
    normalized_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Full offline build. Returns a report dict for the manifest."""
    cache = cache or RawCache(config.RAW_DIR)
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    # Normalized records sit beside the raw cache they were derived from.
    normalized_dir = Path(normalized_dir or (Path(cache.root).parent / "normalized"))

    fills, dedup_report = load_trade_records(cache)
    market_index = load_market_index(cache)
    in_scope, out_scope = annotate_trades(fills, market_index)
    position_pnl = load_position_pnl(cache)
    non_trade = load_non_trade_activity(cache)

    groups = group_by_window(in_scope)
    trade_rows = build_trade_rows(in_scope)
    sequence_rows = build_sequence_rows(groups)
    summary_rows = [
        build_summary_row(window_id, groups[window_id], market_index.get(window_id), position_pnl)
        for window_id in sorted(groups, key=lambda key: (groups[key][0].get("timestamp") or 0, key))
    ]

    n_trades = _write_csv(output_dir / config.TRADES_CSV, TRADE_COLUMNS, trade_rows)
    n_seq = _write_csv(output_dir / config.SEQUENCES_CSV, SEQUENCE_COLUMNS, sequence_rows)
    n_sum = _write_csv(output_dir / config.SUMMARY_CSV, summary_columns(), summary_rows)
    n_excl = _write_csv(
        output_dir / config.EXCLUSIONS_CSV,
        [
            "timestamp",
            "datetime_utc",
            "condition_id",
            "market_slug",
            "event_slug",
            "title",
            "asset",
            "window_minutes",
            "window_source",
            "exclusion_reason",
            "market_flags",
            "side",
            "outcome",
            "price",
            "shares",
            "dollars",
        ],
        out_scope,
    )

    write_jsonl(normalized_dir / "fills_in_scope.jsonl", in_scope)
    write_jsonl(normalized_dir / "fills_out_of_scope.jsonl", out_scope)
    write_jsonl(normalized_dir / "non_trade_activity.jsonl", non_trade)

    return {
        "dedup": dedup_report,
        "markets_indexed": len(market_index),
        "markets_in_scope": sum(1 for meta in market_index.values() if meta.in_scope),
        "fills_in_scope": len(in_scope),
        "fills_out_of_scope": len(out_scope),
        "windows": len(groups),
        "non_trade_activity_rows": len(non_trade),
        "positions_with_reported_pnl": sum(
            1 for value in position_pnl.values() if value.get("has_realized")
        ),
        "rows_written": {
            config.TRADES_CSV: n_trades,
            config.SEQUENCES_CSV: n_seq,
            config.SUMMARY_CSV: n_sum,
            config.EXCLUSIONS_CSV: n_excl,
        },
        "output_dir": str(output_dir),
        "normalized_dir": str(normalized_dir),
    }
