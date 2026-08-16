"""One-shot public Gemini prediction-market metadata diagnostic.

Gemini-only by design: this must not touch Kalshi REST while the latency-sensitive
HOT scanner is running. It records active 15-minute crypto source/index metadata and
UP-contract strike fields needed to evaluate whether settlement distance can be
measured prospectively.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta, _gemini_source, _gemini_symbol

logger = logging.getLogger("kalshi_arb.gemini_active_metadata")


def _up_contract(event: dict):
    for contract in event.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        ticker = str(contract.get("ticker") or "").upper()
        symbol = str(contract.get("instrumentSymbol") or "").upper()
        label = str(contract.get("label") or "").lower()
        if ticker == "UP" or symbol.endswith("-UP") or label == "up":
            return contract
    return None


async def run() -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
    rows = 0
    for event in events:
        meta = _gemini_meta(event)
        if meta is None or meta[1] != 15:
            continue
        contract = _up_contract(event) or {}
        strike_obj = contract.get("strike") or {}
        agency, index = _gemini_source(event)
        payload = {
            "event_ticker": event.get("ticker"),
            "asset": meta[0],
            "window_end_utc": meta[2].isoformat(),
            "instrument_symbol": _gemini_symbol(event),
            "source_agency": agency,
            "source_index": index,
            "source_details": event.get("sourceDetails") or event.get("source_details"),
            "strike": strike_obj,
            "contract_keys": sorted(contract.keys()),
            "event_keys": sorted(event.keys()),
            "read_only": True,
        }
        logger.info("GEMINI_ACTIVE_15M_METADATA %s", json.dumps(payload, separators=(",", ":"), default=str))
        rows += 1
    logger.info("GEMINI_ACTIVE_METADATA_SUMMARY %s", json.dumps({"events_scanned": len(events), "active_15m_rows": rows, "kalshi_requests": 0, "read_only": True}, separators=(",", ":")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
