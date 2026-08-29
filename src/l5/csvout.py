"""CSV writers for the three deliverable tables (plus non-trade activity)."""

from __future__ import annotations

import csv
import os
from typing import Any, Iterable, Sequence

from src.l5.normalize import Trade
from src.l5.windows import Window

TRADE_COLUMNS = [
    "timestamp", "timestamp_iso", "asset", "window_minutes",
    "condition_id", "market_id", "market_slug", "event_slug", "event_title", "question",
    "outcome", "outcome_index", "side", "price", "shares", "dollars", "dollars_derived",
    "token_id", "transaction_hash",
    "window_start_ts", "window_end_ts",
    "seconds_from_window_start", "seconds_to_expiry",
    "window_source", "window_conflict", "bounds_reconstructed", "source",
]

SEQUENCE_COLUMNS = [
    # trade identity
    "condition_id", "asset", "window_minutes", "market_slug", "event_slug",
    "trade_seq", "timestamp", "timestamp_iso",
    "seconds_from_window_start", "seconds_to_expiry",
    "outcome", "side", "price", "shares", "dollars", "transaction_hash",
    # cumulative state after this trade
    "up_qty", "up_spend", "up_avg_price",
    "down_qty", "down_spend", "down_avg_price",
    "up_buy_qty", "up_buy_spend", "down_buy_qty", "down_buy_spend",
    "up_sell_qty", "down_sell_qty",
    "matched_qty", "matched_pair_cost_per_share", "matched_pair_cost_total",
    "excess_qty", "excess_abs", "excess_side", "dominant_side",
    "cum_trades", "cum_dollars_bought", "cum_dollars_sold", "cum_net_cash_out",
    "oversold",
]

SUMMARY_COLUMNS = [
    "condition_id", "market_id", "asset", "window_minutes",
    "market_slug", "event_slug", "event_title", "question",
    "window_start_ts", "window_start_iso", "window_end_ts", "window_end_iso",
    "first_side", "first_price", "first_action", "first_trade_ts",
    "first_seconds_from_window_start", "first_price_below_half",
    "total_trades", "total_buys", "total_sells",
    "total_dollars_bought", "total_dollars_sold", "total_dollars_net", "total_shares_bought",
    "max_price_bought", "min_price_bought",
    "bought_at_70c_plus", "bought_at_80c_plus", "bought_at_90c_plus",
    "qty_lt70", "qty_70to80", "qty_80to90", "qty_gte90",
    "dollars_lt70", "dollars_70to80", "dollars_80to90", "dollars_gte90",
    "final_up_qty", "final_down_qty", "final_up_avg_price", "final_down_avg_price",
    "final_up_gross_avg_buy_price", "final_down_gross_avg_buy_price",
    "final_up_spend", "final_down_spend",
    "matched_qty", "matched_pair_cost_per_share", "matched_pair_cost_total",
    "residual_qty", "residual_side", "residual_frac_of_larger_leg", "finished_balanced",
    "traded_both_sides", "seconds_to_opposite_side", "trades_before_opposite_side",
    "realized_pnl", "pnl_source", "pnl_rows",
    "window_source", "window_conflict", "bounds_reconstructed", "oversold",
]

NON_TRADE_COLUMNS = [
    "timestamp", "timestamp_iso", "activity_type", "asset", "window_minutes",
    "condition_id", "market_slug", "event_slug", "outcome",
    "size", "usdc_size", "price", "transaction_hash",
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # 10 significant digits: enough to round-trip share counts and prices
        # without re-emitting binary-float noise like 0.30000000000000004.
        # Plain `:g` would silently truncate to 6 and corrupt derived averages.
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


def _write(path: str, columns: Sequence[str], rows: Iterable[dict]) -> int:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row.get(column)) for column in columns])
            count += 1
    return count


def write_all_trades(path: str, trades: Sequence[Trade]) -> int:
    return _write(path, TRADE_COLUMNS, (t.as_dict() for t in trades))


def write_window_sequences(path: str, windows: Sequence[Window]) -> int:
    def rows():
        for window in windows:
            for trade, snapshot in zip(window.trades, window.snapshots):
                row = snapshot.as_dict()
                row.update(
                    {
                        "condition_id": trade.condition_id,
                        "asset": trade.asset,
                        "window_minutes": trade.window_minutes,
                        "market_slug": trade.market_slug,
                        "event_slug": trade.event_slug,
                        "timestamp": trade.timestamp,
                        "timestamp_iso": trade.timestamp_iso,
                        "seconds_from_window_start": trade.seconds_from_window_start,
                        "seconds_to_expiry": trade.seconds_to_expiry,
                        "outcome": trade.outcome,
                        "side": trade.side,
                        "price": trade.price,
                        "shares": trade.shares,
                        "dollars": trade.dollars,
                        "transaction_hash": trade.transaction_hash,
                    }
                )
                yield row

    return _write(path, SEQUENCE_COLUMNS, rows())


def write_windows_summary(path: str, windows: Sequence[Window]) -> int:
    return _write(
        path, SUMMARY_COLUMNS, (w.summary.as_dict() for w in windows if w.summary)
    )


def write_non_trade_activity(path: str, rows: Sequence[dict]) -> int:
    return _write(path, NON_TRADE_COLUMNS, rows)
