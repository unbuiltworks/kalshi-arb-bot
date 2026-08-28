"""Read-only both-fee accounting adapter for aligned Gemini/Kalshi crypto books.

This module deliberately does not discover markets, open sockets, or submit orders.
It only combines synchronized top-of-book snapshots with authoritative Kalshi
Series fee metadata and Gemini's published Predictions taker fee. Unknown Kalshi
fee types fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_execution_shadow import (
    ShadowCandidate,
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata

D = Decimal


@dataclass(frozen=True)
class BothFeeShadowResult:
    candidates: tuple[ShadowCandidate, ...]
    kalshi_fee_status: str
    settlement_status: str = "INDEX_MISMATCH_RESEARCH_ONLY"
    read_only: bool = True
    order_entry: bool = False

    @property
    def positive(self) -> bool:
        return bool(self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "both_fee_candidates": [
                {
                    "direction": c.direction,
                    "quantity": str(c.quantity),
                    "raw_cost": str(c.raw_cost),
                    "fees": str(c.fees),
                    "net_cost": str(c.net_cost),
                    "locked_profit": str(c.locked_profit),
                    "roi": str(c.roi_on_cost),
                    "quote_skew_ms": c.quote_skew_ms,
                }
                for c in self.candidates
            ],
            "kalshi_fee_status": self.kalshi_fee_status,
            "settlement_status": self.settlement_status,
            "read_only": self.read_only,
            "order_entry": self.order_entry,
        }


def evaluate_both_fee_shadow(
    gemini: TopOfBook,
    kalshi: TopOfBook,
    *,
    kalshi_fee_metadata: KalshiSeriesFeeMetadata,
    max_quote_skew_ms: int = 500,
    max_quantity: Decimal | None = None,
) -> BothFeeShadowResult:
    """Evaluate synchronized complementary legs after both venues' taker fees.

    Kalshi fee handling is accepted only when the live Series metadata maps to a
    supported formula. Settlement remains a separate hard blocker even when the
    price lock is positive after fees.
    """
    if kalshi_fee_metadata.formula_status != "MAPPED_QUADRATIC_TAKER":
        return BothFeeShadowResult(
            candidates=(),
            kalshi_fee_status="UNMAPPED_DO_NOT_TRADE",
        )

    candidates = evaluate_pair(
        gemini,
        kalshi,
        gemini_fee=gemini_predictions_taker_fee,
        kalshi_fee=kalshi_fee_metadata.taker_fee_fn(),
        max_quote_skew_ms=max_quote_skew_ms,
        max_quantity=max_quantity,
    )
    return BothFeeShadowResult(
        candidates=tuple(candidates),
        kalshi_fee_status="MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY",
    )
