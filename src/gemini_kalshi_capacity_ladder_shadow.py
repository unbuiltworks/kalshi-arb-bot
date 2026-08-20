"""Extended read-only capacity ladder for Gemini/Kalshi 15m opportunities.

This wraps ``gemini_kalshi_capacity_shadow`` without changing any live execution
code.  The base shadow already requires the production-shaped q=5 trigger, >=10bp
whole-episode double-boundary clearance, exact 50ms survival, <=50ms quote skew,
>=3c after-fee profit per contract, displayed Gemini depth, full cumulative Kalshi
depth, and at most a 2c fee-aware Kalshi cushion.

The only change here is the research ladder: q=5/10/15/25/50/100.  Order entry
remains impossible.
"""
from __future__ import annotations

from decimal import Decimal
import logging

from src import gemini_kalshi_capacity_shadow as base

D = Decimal
CAPACITY_LADDER = (D("5"), D("10"), D("15"), D("25"), D("50"), D("100"))
logger = logging.getLogger("kalshi_arb.gemini_kalshi_capacity_ladder_shadow")

_ORIGINAL_EVALUATE = base.evaluate_capacity_sizes


def _evaluate_extended(*, direction, gemini_price, original_kalshi_yes_price,
                       gemini_available_size, kalshi_book, kalshi_fee_multiplier,
                       sizes=CAPACITY_LADDER):
    return _ORIGINAL_EVALUATE(
        direction=direction,
        gemini_price=gemini_price,
        original_kalshi_yes_price=original_kalshi_yes_price,
        gemini_available_size=gemini_available_size,
        kalshi_book=kalshi_book,
        kalshi_fee_multiplier=kalshi_fee_multiplier,
        sizes=sizes,
    )


def install() -> None:
    base.SIZES = CAPACITY_LADDER
    base.evaluate_capacity_sizes = _evaluate_extended
    logger.warning(
        "CRYPTO_CAPACITY_LADDER_SHADOW_INSTALLED sizes=5,10,15,25,50,100 "
        "trigger_q=5 min_profit_per_contract=0.03 double_boundary_min_bps=10 "
        "checkpoint_ms=50 max_quote_skew_ms=50 max_cushion_cents=2 "
        "risk_gates_changed=false trade_authorized=false order_entry=false"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    install()
    base.main()


if __name__ == "__main__":
    main()
