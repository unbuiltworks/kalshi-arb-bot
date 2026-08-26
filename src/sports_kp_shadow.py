"""Compatibility pricing wrapper for the read-only K<->P event-driven census.

This module intentionally exposes pricing only. It delegates to the existing
`polymarket_kalshi_shadow.best_plan` fee/capital arithmetic and has no
order-entry capability.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.polymarket_kalshi_shadow import ShadowPlan, best_plan


def evaluate_both_directions(
    poly_slug: str,
    kalshi_ticker: str,
    *,
    poly_yes_ask: Decimal,
    poly_yes_depth: Decimal,
    poly_no_ask: Decimal,
    poly_no_depth: Decimal,
    kalshi_yes_ask: Decimal,
    kalshi_yes_depth: Decimal,
    kalshi_no_ask: Decimal,
    kalshi_no_depth: Decimal,
    bankroll: Decimal,
    poly_fee_rate: Decimal,
    kalshi_fee_rate: Decimal,
) -> ShadowPlan | None:
    """Return the best positive after-fee plan using canonical K<->P math."""
    del poly_slug, kalshi_ticker
    poly = SimpleNamespace(
        yes_ask=poly_yes_ask,
        yes_depth=poly_yes_depth,
        no_ask=poly_no_ask,
        no_depth=poly_no_depth,
    )
    kalshi = SimpleNamespace(
        yes_ask=kalshi_yes_ask,
        yes_depth=kalshi_yes_depth,
        no_ask=kalshi_no_ask,
        no_depth=kalshi_no_depth,
    )
    return best_plan(
        poly,
        kalshi,
        bankroll=bankroll,
        poly_fee_rate=poly_fee_rate,
        kalshi_fee_rate=kalshi_fee_rate,
    )
