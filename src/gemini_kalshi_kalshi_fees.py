"""Read-only Kalshi fee-metadata probe for 15-minute crypto series.

This module deliberately does not guess fee formulas. Kalshi's public Series API
exposes the active ``fee_type`` and ``fee_multiplier`` and the public fee-changes
endpoint exposes scheduled updates. We fetch and normalize that metadata so the
execution shadow can fail closed until each returned fee type is mapped to a
published formula.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import KALSHI_REST

logger = logging.getLogger("kalshi_arb.gemini_kalshi_kalshi_fees")
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")


@dataclass(frozen=True)
class KalshiSeriesFeeMetadata:
    series_ticker: str
    fee_type: str
    fee_multiplier: str
    last_updated_ts: str
    scheduled_changes: tuple[dict[str, Any], ...]

    @property
    def formula_status(self) -> str:
        # Intentionally fail closed. Mapping a fee_type string to arithmetic is a
        # separate change that must cite Kalshi's published fee schedule.
        return "UNMAPPED_DO_NOT_TRADE"

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
