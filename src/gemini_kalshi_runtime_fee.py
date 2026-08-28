"""Fail-closed runtime fee evaluation for Gemini/Kalshi 15-minute shadowing.

This module is intentionally network-free and order-free.  It bridges live watcher
quotes to the already-audited Kalshi Series fee metadata so runtime code can stop
labelling both-fee economics as unmodeled without weakening the separate
INDEX_MISMATCH_RESEARCH_ONLY settlement blocker.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from src.gemini_kalshi_execution_shadow import (
    ShadowCandidate,
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata

D = Decimal

SERIES_BY_ASSET = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}


@dataclass(frozen=True)
class RuntimeFeeResult:
    asset: str
    fee_status: str
    candidates: tuple[ShadowCandidate, ...]
    metadata: dict | None

    @property
    def modeled(self) -> bool:
        return self.fee_status == "MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY"


def evaluate_with_authoritative_kalshi_fee(
    asset: str,
    gemini: TopOfBook,
    kalshi: TopOfBook,
    metadata_by_asset: Mapping[str, KalshiSeriesFeeMetadata],
    *,
    max_quote_skew_ms: int = 500,
    max_quantity: Decimal | None = None,
) -> RuntimeFeeResult:
    """Evaluate both-taker economics using mapped live Kalshi Series metadata.

    Any missing metadata, wrong series, or unsupported fee type fails closed and
    returns no candidates.  Positive candidates remain research-only because fee
    completeness does not establish Gemini/Kalshi settlement-index equivalence.
    """
    normalized = asset.upper()
    expected_series = SERIES_BY_ASSET.get(normalized)
    if expected_series is None:
        return RuntimeFeeResult(
            asset=normalized,
            fee_status="UNKNOWN_ASSET_DO_NOT_TRADE",
            candidates=(),
            metadata=None,
        )

    meta = metadata_by_asset.get(normalized)
    if meta is None:
        return RuntimeFeeResult(
            asset=normalized,
            fee_status="FEE_METADATA_UNAVAILABLE_DO_NOT_TRADE",
            candidates=(),
            metadata=None,
        )
    if meta.series_ticker.upper() != expected_series:
        return RuntimeFeeResult(
            asset=normalized,
            fee_status="FEE_SERIES_MISMATCH_DO_NOT_TRADE",
            candidates=(),
            metadata=meta.as_dict(),
        )
    if meta.formula_status != "MAPPED_QUADRATIC_TAKER":
        return RuntimeFeeResult(
            asset=normalized,
            fee_status=meta.formula_status,
            candidates=(),
            metadata=meta.as_dict(),
        )

    candidates = evaluate_pair(
        gemini,
        kalshi,
        gemini_fee=gemini_predictions_taker_fee,
        kalshi_fee=meta.taker_fee_fn(),
        max_quote_skew_ms=max_quote_skew_ms,
        max_quantity=max_quantity,
    )
    return RuntimeFeeResult(
        asset=normalized,
        fee_status="MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY",
        candidates=tuple(candidates),
        metadata=meta.as_dict(),
    )
