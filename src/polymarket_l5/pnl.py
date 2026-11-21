"""Window P&L from two independent sources, never blended silently.

1. ``polymarket_positions`` -- realized P&L as Polymarket reports it, keyed by
   condition id. Only present for markets still returned by /positions.
2. ``derived_resolution`` -- computed from our own fills plus the market's
   resolved outcome:

       pnl = realized_trading_pnl + (winning_side_qty * $1) - remaining_cost_basis

   This is deterministic and reproducible from the cache, but it is a *trading*
   P&L only. It excludes gas, and it excludes any maker rebate, liquidity
   reward, or incentive program payout -- those never appear in fill data and
   must not be assumed to be included.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Optional

from .cache import RawCache
from .inventory import InventoryState


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def load_position_pnl(cache: RawCache) -> Dict[str, Dict[str, Any]]:
    """condition_id -> aggregated position P&L fields reported by Polymarket."""
    aggregate: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"realized_pnl": 0.0, "cash_pnl": 0.0, "rows": 0, "has_realized": False}
    )
    seen_rows = set()
    for tag in ("positions_all", "positions_redeemable", "positions_closed"):
        for batch in cache.payload_batches(tag):
            for item in batch:
                condition_id = item.get("conditionId") or item.get("condition_id") or ""
                if not condition_id:
                    continue
                key = (
                    condition_id,
                    str(item.get("asset") or ""),
                    str(item.get("size") or ""),
                    str(item.get("realizedPnl") or ""),
                    str(item.get("cashPnl") or ""),
                )
                if key in seen_rows:
                    continue
                seen_rows.add(key)
                bucket = aggregate[condition_id]
                realized = _num(item.get("realizedPnl"))
                cash = _num(item.get("cashPnl"))
                if realized is not None:
                    bucket["realized_pnl"] += realized
                    bucket["has_realized"] = True
                if cash is not None:
                    bucket["cash_pnl"] += cash
                bucket["rows"] += 1
    return dict(aggregate)


def derived_window_pnl(state: InventoryState, winning_outcome: str) -> Optional[float]:
    """Settlement-based trading P&L for a resolved window; None if unresolved."""
    if winning_outcome not in ("Up", "Down"):
        return None
    winning_qty = state.up.qty if winning_outcome == "Up" else state.down.qty
    remaining_basis = state.up.basis + state.down.basis
    return round(state.realized_pnl_trading + winning_qty * 1.0 - remaining_basis, 6)
