"""Group fills into market windows and reduce each window to a summary row."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from src.l5 import config
from src.l5.classify import iso
from src.l5.inventory import EPS, Inventory, Snapshot
from src.l5.normalize import Trade, _ordering_key

BALANCED = "BALANCED"


def price_band(price: float) -> str:
    edges = config.PRICE_BAND_EDGES
    labels = config.PRICE_BAND_LABELS
    for index, label in enumerate(labels):
        if edges[index] <= price < edges[index + 1]:
            return label
    return labels[-1]


# --- realized P&L ------------------------------------------------------------


def build_pnl_index(position_records: Iterable[dict]) -> dict[str, dict[str, Any]]:
    """condition_id -> aggregated realized P&L from Polymarket position rows.

    These figures are Polymarket's own accounting of settled positions. They do
    not include liquidity rebates, maker incentives, or gas, and nothing here
    adds them.
    """
    index: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"realized_pnl": 0.0, "cash_pnl": 0.0, "rows": 0, "any_field": False}
    )
    for record in position_records:
        condition_id = str(record.get("conditionId") or "")
        if not condition_id:
            continue
        entry = index[condition_id]
        entry["rows"] += 1
        for source_key, target_key in (("realizedPnl", "realized_pnl"), ("cashPnl", "cash_pnl")):
            value = record.get(source_key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entry[target_key] += float(value)
                entry["any_field"] = True
            elif isinstance(value, str):
                try:
                    entry[target_key] += float(value)
                    entry["any_field"] = True
                except ValueError:
                    pass
    return dict(index)


# --- window model ------------------------------------------------------------


@dataclass
class WindowSummary:
    condition_id: str
    market_id: str
    asset: str
    window_minutes: int
    market_slug: str
    event_slug: str
    event_title: str
    question: str
    window_start_ts: int | None
    window_start_iso: str
    window_end_ts: int | None
    window_end_iso: str

    first_side: str
    first_price: float
    first_action: str
    first_trade_ts: int
    first_seconds_from_window_start: int | None
    first_price_below_half: bool

    total_trades: int
    total_buys: int
    total_sells: int
    total_dollars_bought: float
    total_dollars_sold: float
    total_dollars_net: float
    total_shares_bought: float

    max_price_bought: float | None
    min_price_bought: float | None
    bought_at_70c_plus: bool
    bought_at_80c_plus: bool
    bought_at_90c_plus: bool
    qty_lt70: float
    qty_70to80: float
    qty_80to90: float
    qty_gte90: float
    dollars_lt70: float
    dollars_70to80: float
    dollars_80to90: float
    dollars_gte90: float

    final_up_qty: float
    final_down_qty: float
    final_up_avg_price: float | None
    final_down_avg_price: float | None
    final_up_gross_avg_buy_price: float | None
    final_down_gross_avg_buy_price: float | None
    final_up_spend: float
    final_down_spend: float

    matched_qty: float
    matched_pair_cost_per_share: float | None
    matched_pair_cost_total: float | None

    residual_qty: float
    residual_side: str
    residual_frac_of_larger_leg: float | None
    finished_balanced: bool

    traded_both_sides: bool
    seconds_to_opposite_side: int | None
    trades_before_opposite_side: int | None

    realized_pnl: float | None
    pnl_source: str
    pnl_rows: int

    capture_complete: bool
    capture_note: str

    window_source: str
    window_conflict: bool
    bounds_reconstructed: bool
    oversold: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Window:
    condition_id: str
    trades: list[Trade] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    summary: WindowSummary | None = None


def group_trades(trades: Iterable[Trade]) -> dict[str, list[Trade]]:
    """Bucket fills by market/window ID, chronologically within each bucket.

    The condition ID is the window identity. Trades are *not* grouped by title
    or by clock time, so two same-named windows on different days never merge.
    """
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.condition_id].append(trade)
    for rows in grouped.values():
        rows.sort(key=_ordering_key)
    return dict(grouped)


def capture_bounds(trades: Iterable[Trade]) -> tuple[int | None, int | None]:
    """The time span the retrieved data actually covers."""
    stamps = [t.timestamp for t in trades]
    return (min(stamps), max(stamps)) if stamps else (None, None)


def build_windows(
    trades: Iterable[Trade], pnl_index: dict[str, dict[str, Any]] | None = None
) -> list[Window]:
    pnl_index = pnl_index or {}
    trades = list(trades)
    capture_start, capture_end = capture_bounds(trades)
    windows: list[Window] = []

    for condition_id, rows in group_trades(trades).items():
        inventory = Inventory()
        snapshots = [
            inventory.apply(t.outcome, t.side, t.shares, t.price, t.dollars) for t in rows
        ]
        window = Window(condition_id=condition_id, trades=rows, snapshots=snapshots)
        window.summary = _summarize(
            rows, snapshots, inventory, pnl_index.get(condition_id),
            capture_start, capture_end,
        )
        windows.append(window)

    windows.sort(
        key=lambda w: (
            w.summary.window_start_ts if w.summary and w.summary.window_start_ts else 0,
            w.condition_id,
        )
    )
    return windows


def _assess_capture(
    first: Trade, capture_start: int | None, capture_end: int | None
) -> tuple[bool, str]:
    """Did the retrieved data span this whole window?

    A window straddling either edge of the capture is missing fills, so its
    final inventory, matched quantity and balanced-finish flag describe a
    fragment rather than the window. Those windows are flagged here and
    excluded from the window-level statistics rather than silently averaged in.
    """
    start, end = first.window_start_ts, first.window_end_ts
    if start is None or end is None or capture_start is None or capture_end is None:
        return False, "window bounds or capture bounds unknown"
    if start < capture_start:
        return False, "window opened before the retrieved data begins"
    if end > capture_end:
        return False, "window had not closed when the data was captured"
    return True, ""


def _summarize(
    rows: list[Trade],
    snapshots: list[Snapshot],
    inventory: Inventory,
    pnl: dict[str, Any] | None,
    capture_start: int | None = None,
    capture_end: int | None = None,
) -> WindowSummary:
    first = rows[0]
    final = snapshots[-1]
    capture_complete, capture_note = _assess_capture(first, capture_start, capture_end)

    buys = [t for t in rows if t.side == "BUY"]
    sells = [t for t in rows if t.side == "SELL"]
    buy_prices = [t.price for t in buys]

    qty_by_band = {label: 0.0 for label in config.PRICE_BAND_LABELS}
    dollars_by_band = {label: 0.0 for label in config.PRICE_BAND_LABELS}
    for trade in buys:
        band = price_band(trade.price)
        qty_by_band[band] += trade.shares
        dollars_by_band[band] += trade.dollars

    # First trade on the side opposite the opening side.
    seconds_to_opposite: int | None = None
    trades_before_opposite: int | None = None
    for index, trade in enumerate(rows):
        if trade.outcome != first.outcome:
            seconds_to_opposite = trade.timestamp - first.timestamp
            trades_before_opposite = index
            break

    larger_leg = max(abs(final.up_qty), abs(final.down_qty))
    residual_frac = (final.excess_abs / larger_leg) if larger_leg > EPS else None
    finished_balanced = (
        final.excess_abs <= config.BALANCED_TOLERANCE_SHARES
        or (residual_frac is not None and residual_frac <= config.BALANCED_TOLERANCE_FRAC)
    )

    if pnl and pnl.get("any_field"):
        realized: float | None = float(pnl["realized_pnl"])
        pnl_source = "polymarket_positions.realizedPnl"
        pnl_rows = int(pnl["rows"])
    else:
        realized = None
        pnl_source = "unavailable"
        pnl_rows = int(pnl["rows"]) if pnl else 0

    return WindowSummary(
        condition_id=first.condition_id,
        market_id=first.market_id,
        asset=first.asset,
        window_minutes=first.window_minutes,
        market_slug=first.market_slug,
        event_slug=first.event_slug,
        event_title=first.event_title,
        question=first.question,
        window_start_ts=first.window_start_ts,
        window_start_iso=iso(first.window_start_ts),
        window_end_ts=first.window_end_ts,
        window_end_iso=iso(first.window_end_ts),
        first_side=first.outcome,
        first_price=first.price,
        first_action=first.side,
        first_trade_ts=first.timestamp,
        first_seconds_from_window_start=first.seconds_from_window_start,
        first_price_below_half=first.price < 0.5,
        total_trades=len(rows),
        total_buys=len(buys),
        total_sells=len(sells),
        total_dollars_bought=final.cum_dollars_bought,
        total_dollars_sold=final.cum_dollars_sold,
        total_dollars_net=final.cum_net_cash_out,
        total_shares_bought=final.up_buy_qty + final.down_buy_qty,
        max_price_bought=max(buy_prices) if buy_prices else None,
        min_price_bought=min(buy_prices) if buy_prices else None,
        bought_at_70c_plus=any(p >= 0.70 for p in buy_prices),
        bought_at_80c_plus=any(p >= 0.80 for p in buy_prices),
        bought_at_90c_plus=any(p >= 0.90 for p in buy_prices),
        qty_lt70=qty_by_band["lt70"],
        qty_70to80=qty_by_band["70to80"],
        qty_80to90=qty_by_band["80to90"],
        qty_gte90=qty_by_band["gte90"],
        dollars_lt70=dollars_by_band["lt70"],
        dollars_70to80=dollars_by_band["70to80"],
        dollars_80to90=dollars_by_band["80to90"],
        dollars_gte90=dollars_by_band["gte90"],
        final_up_qty=final.up_qty,
        final_down_qty=final.down_qty,
        final_up_avg_price=final.up_avg_price,
        final_down_avg_price=final.down_avg_price,
        final_up_gross_avg_buy_price=inventory.up.gross_avg_buy_price,
        final_down_gross_avg_buy_price=inventory.down.gross_avg_buy_price,
        final_up_spend=final.up_spend,
        final_down_spend=final.down_spend,
        matched_qty=final.matched_qty,
        matched_pair_cost_per_share=final.matched_pair_cost_per_share,
        matched_pair_cost_total=final.matched_pair_cost_total,
        residual_qty=final.excess_qty,
        residual_side=final.excess_side,
        residual_frac_of_larger_leg=residual_frac,
        finished_balanced=finished_balanced,
        traded_both_sides=seconds_to_opposite is not None,
        seconds_to_opposite_side=seconds_to_opposite,
        trades_before_opposite_side=trades_before_opposite,
        realized_pnl=realized,
        pnl_source=pnl_source,
        pnl_rows=pnl_rows,
        capture_complete=capture_complete,
        capture_note=capture_note,
        window_source=first.window_source,
        window_conflict=first.window_conflict,
        bounds_reconstructed=first.bounds_reconstructed,
        oversold=final.oversold,
    )
