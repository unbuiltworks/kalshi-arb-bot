"""Read-only Kalshi fee handling for 15-minute crypto series.

Kalshi's public Series API exposes the active ``fee_type`` and
``fee_multiplier`` and the fee-changes endpoint exposes scheduled updates.
For the documented ``quadratic`` fee type we map the published taker formula:

    ceil_cent(0.07 * fee_multiplier * contracts * price * (1 - price))

Unknown fee types continue to fail closed. This module contains no order-entry
path and is intended only for execution-shadow accounting.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import json
import logging
from typing import Any, Callable

import httpx

from src.gemini_kalshi_fast_overlap import KALSHI_REST

D = Decimal
FeeFn = Callable[[Decimal, Decimal], Decimal]
logger = logging.getLogger("kalshi_arb.gemini_kalshi_kalshi_fees")
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")


def _ceil_cent(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("fee cannot be negative")
    return (value * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def kalshi_quadratic_taker_fee(
    price: Decimal,
    quantity: Decimal,
    *,
    fee_multiplier: Decimal = D("1"),
) -> Decimal:
    """Published Kalshi quadratic taker fee at total-order level.

    The standard quadratic coefficient is 0.07. ``fee_multiplier`` comes from
    the live Series API and scales that coefficient. Total-order fee is rounded
    up to the next cent, matching Kalshi's published schedule for standard
    cent-priced event contracts. Fractional/subpenny fill-level rounding is a
    separate execution detail and should be measured from actual fills before
    any live-trading claim.
    """
    if quantity < 0 or price < 0 or price > 1 or fee_multiplier < 0:
        raise ValueError("invalid Kalshi fee inputs")
    return _ceil_cent(
        D("0.07") * fee_multiplier * quantity * price * (D("1") - price)
    )


@dataclass(frozen=True)
class KalshiSeriesFeeMetadata:
    series_ticker: str
    fee_type: str
    fee_multiplier: str
    last_updated_ts: str
    scheduled_changes: tuple[dict[str, Any], ...]

    @property
    def formula_status(self) -> str:
        return (
            "MAPPED_QUADRATIC_TAKER"
            if self.fee_type == "quadratic"
            else "UNMAPPED_DO_NOT_TRADE"
        )

    def taker_fee_fn(self) -> FeeFn:
        """Return a fee callable only for a documented mapped fee type."""
        if self.fee_type != "quadratic":
            raise ValueError(
                f"unsupported Kalshi fee_type={self.fee_type!r} for {self.series_ticker}"
            )
        multiplier = D(self.fee_multiplier)
        if multiplier < 0:
            raise ValueError("Kalshi fee_multiplier cannot be negative")

        def fee(price: Decimal, quantity: Decimal) -> Decimal:
            return kalshi_quadratic_taker_fee(
                price, quantity, fee_multiplier=multiplier
            )

        return fee

    def as_dict(self) -> dict[str, Any]:
        return {
            "series_ticker": self.series_ticker,
            "fee_type": self.fee_type,
            "fee_multiplier": self.fee_multiplier,
            "last_updated_ts": self.last_updated_ts,
            "scheduled_changes": list(self.scheduled_changes),
            "formula_status": self.formula_status,
            "read_only": True,
        }


def normalize_series_fee(series_ticker: str, series_payload: dict[str, Any], changes_payload: dict[str, Any]) -> KalshiSeriesFeeMetadata:
    series = series_payload.get("series") or {}
    if str(series.get("ticker") or "").upper() != series_ticker.upper():
        raise ValueError(f"series ticker mismatch for {series_ticker}")
    fee_type = str(series.get("fee_type") or "").strip()
    fee_multiplier = str(series.get("fee_multiplier") if series.get("fee_multiplier") is not None else "").strip()
    if not fee_type or not fee_multiplier:
        raise ValueError(f"missing Kalshi fee metadata for {series_ticker}")
    raw_changes = changes_payload.get("series_fee_change_arr") or []
    changes = tuple(
        row for row in raw_changes
        if isinstance(row, dict) and str(row.get("series_ticker") or "").upper() == series_ticker.upper()
    )
    return KalshiSeriesFeeMetadata(
        series_ticker=series_ticker.upper(),
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        last_updated_ts=str(series.get("last_updated_ts") or ""),
        scheduled_changes=changes,
    )


async def fetch_series_fee_metadata(http: httpx.AsyncClient, series_ticker: str) -> KalshiSeriesFeeMetadata:
    series_resp, changes_resp = await asyncio.gather(
        http.get(f"{KALSHI_REST}/series/{series_ticker}"),
        http.get(f"{KALSHI_REST}/series/fee_changes", params={"series_ticker": series_ticker}),
    )
    series_resp.raise_for_status()
    changes_resp.raise_for_status()
    return normalize_series_fee(series_ticker, series_resp.json(), changes_resp.json())


async def run_once() -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        rows = await asyncio.gather(*(fetch_series_fee_metadata(http, ticker) for ticker in SERIES))
    for row in rows:
        logger.info("KALSHI_15M_FEE_METADATA %s", json.dumps(row.as_dict(), separators=(",", ":")))
    logger.info("KALSHI_15M_FEE_METADATA_SUMMARY %s", json.dumps({
        "series": len(rows),
        "all_metadata_present": all(bool(row.fee_type and row.fee_multiplier) for row in rows),
        "all_formula_status": sorted({row.formula_status for row in rows}),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
