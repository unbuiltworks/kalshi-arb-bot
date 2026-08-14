"""Read-only Gemini Predictions <-> Kalshi 15-minute crypto overlap discovery.

Purpose: find recurring same-asset, same-expiry 15-minute candidates for shadow
research. Gemini 5-minute events are intentionally ignored because Kalshi does not
currently expose a matching 5-minute BTC lane in this project. This module uses
public REST endpoints only and NEVER submits orders.

Time alignment is not settlement equivalence: Gemini short-duration crypto uses
its own source/index metadata while Kalshi crypto markets use CF Benchmarks. Every
overlap therefore remains research-only until historical outcome agreement,
fee/depth, and settlement rules are quantified.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("kalshi_arb.gemini_kalshi_fast_overlap")

GEMINI_REST = "https://api.gemini.com/v1/prediction-markets"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"

GEMINI_EVENT_RE = re.compile(r"^(BTC|ETH|SOL|XRP)15M(\d{10})$", re.I)
DURATION_RE = re.compile(r"\b15[ -]?(?:minute|min|m)\b", re.I)
UPDOWN_RE = re.compile(r"\b(?:up\s*(?:or|/)\s*down|higher|lower)\b", re.I)

ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "eth"),
    "SOL": ("solana", "sol"),
    "XRP": ("xrp",),
}


def _text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in (
            "ticker", "event_ticker", "title", "subtitle", "yes_sub_title",
            "no_sub_title", "rules_primary", "rules_secondary", "category",
        )
    ).lower()


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _asset_from_text(row: dict[str, Any]) -> str | None:
    text = _text(row)
    hits: list[str] = []
    for symbol, aliases in ASSET_ALIASES.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) for alias in aliases):
            hits.append(symbol)
    return hits[0] if len(hits) == 1 else None


def _kalshi_duration(row: dict[str, Any]) -> int | None:
    return 15 if DURATION_RE.search(_text(row)) else None


def _kalshi_expiry(row: dict[str, Any]) -> datetime | None:
    return _dt(
        row.get("expected_expiration_time")
        or row.get("expiration_time")
        or row.get("close_time")
    )


def _gemini_meta(event: dict[str, Any]) -> tuple[str, int, datetime] | None:
    ticker = str(event.get("ticker") or "")
    match = GEMINI_EVENT_RE.match(ticker)
    if not match:
        return None
    asset, stamp = match.groups()
    try:
        expiry = datetime.strptime(stamp, "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return asset.upper(), 15, expiry


def _gemini_source(event: dict[str, Any]) -> tuple[str, str]:
    details = event.get("sourceDetails") or event.get("source_details") or {}
    if isinstance(details, dict):
        agency = str(details.get("agency") or "")
        index = str(details.get("index") or "")
        if agency or index:
            return agency, index
    source = str(event.get("source") or "")
    return "", source


@dataclass(frozen=True)
class Overlap:
    asset: str
    duration_minutes: int
    gemini_event_ticker: str
    gemini_instrument_symbol: str
    kalshi_ticker: str
    gemini_expiry: datetime
    kalshi_expiry: datetime
    expiry_skew_s: float
    gemini_source_agency: str
    gemini_source_index: str
    kalshi_rules_primary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "duration_minutes": self.duration_minutes,
            "gemini_event_ticker": self.gemini_event_ticker,
            "gemini_instrument_symbol": self.gemini_instrument_symbol,
            "kalshi_ticker": self.kalshi_ticker,
            "gemini_expiry_utc": self.gemini_expiry.isoformat(),
            "kalshi_expiry_utc": self.kalshi_expiry.isoformat(),
            "expiry_skew_s": round(self.expiry_skew_s, 3),
            "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            "gemini_source_agency": self.gemini_source_agency,
            "gemini_source_index": self.gemini_source_index,
            "kalshi_rules_primary": self.kalshi_rules_primary,
        }


async def _gemini_active_crypto(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = await http.get(
            f"{GEMINI_REST}/events",
            params={"status": "active", "category": "crypto", "limit": 500, "offset": offset},
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data", [])
        rows.extend(batch)
        if len(batch) < 500:
            return rows
        offset += 500
        if offset >= 5000:
            return rows


async def _kalshi_open(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        response = await http.get(f"{KALSHI_REST}/markets", params=params)
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get("markets", []))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return rows


def _gemini_symbol(event: dict[str, Any]) -> str:
    contracts = event.get("contracts") or []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        symbol = str(contract.get("instrumentSymbol") or contract.get("instrument_symbol") or "")
        if symbol:
            return symbol
    return ""


def discover_overlaps(
    gemini_events: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    *,
    max_expiry_skew_s: float = 30.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Overlap]]:
    gemini_15m: list[dict[str, Any]] = []
    for event in gemini_events:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        asset, duration, _ = meta
        if asset in ASSET_ALIASES and duration == 15:
            gemini_15m.append(event)

    kalshi_15m: list[dict[str, Any]] = []
    for market in kalshi_markets:
        asset = _asset_from_text(market)
        duration = _kalshi_duration(market)
        if asset and duration == 15 and UPDOWN_RE.search(_text(market)):
            kalshi_15m.append(market)

    overlaps: list[Overlap] = []
    for event in gemini_15m:
        meta = _gemini_meta(event)
        assert meta is not None
        asset, duration, expiry = meta
        agency, index = _gemini_source(event)
        symbol = _gemini_symbol(event)
        for market in kalshi_15m:
            if _asset_from_text(market) != asset:
                continue
            k_expiry = _kalshi_expiry(market)
            if k_expiry is None:
                continue
            skew = abs((expiry - k_expiry).total_seconds())
            if skew > max_expiry_skew_s:
                continue
            overlaps.append(Overlap(
                asset=asset,
                duration_minutes=duration,
                gemini_event_ticker=str(event.get("ticker") or ""),
                gemini_instrument_symbol=symbol,
                kalshi_ticker=str(market.get("ticker") or ""),
                gemini_expiry=expiry,
                kalshi_expiry=k_expiry,
                expiry_skew_s=skew,
                gemini_source_agency=agency,
                gemini_source_index=index,
                kalshi_rules_primary=str(market.get("rules_primary") or ""),
            ))

    overlaps.sort(key=lambda x: (x.gemini_expiry, x.asset, x.expiry_skew_s))
    return gemini_15m, kalshi_15m, overlaps


async def run_once(max_expiry_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini_events, kalshi_markets = await asyncio.gather(
            _gemini_active_crypto(http), _kalshi_open(http)
        )
    gemini_15m, kalshi_15m, overlaps = discover_overlaps(
        gemini_events, kalshi_markets, max_expiry_skew_s=max_expiry_skew_s
    )

    for event in gemini_15m:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        agency, index = _gemini_source(event)
        logger.info("GEMINI_15M %s", json.dumps({
            "ticker": event.get("ticker"),
            "asset": meta[0],
            "duration_minutes": 15,
            "expiry_utc": meta[2].isoformat(),
            "instrument_symbol": _gemini_symbol(event),
            "source_agency": agency,
            "source_index": index,
        }, separators=(",", ":")))

    for market in kalshi_15m:
        logger.info("KALSHI_15M %s", json.dumps({
            "ticker": market.get("ticker"),
            "event_ticker": market.get("event_ticker"),
            "asset": _asset_from_text(market),
            "duration_minutes": 15,
            "expiry_utc": _kalshi_expiry(market).isoformat() if _kalshi_expiry(market) else None,
            "rules_primary": market.get("rules_primary"),
        }, separators=(",", ":")))

    for overlap in overlaps:
        logger.info("GEMINI_KALSHI_15M_OVERLAP %s", json.dumps(overlap.as_dict(), separators=(",", ":")))

    logger.info("GEMINI_KALSHI_15M_SUMMARY %s", json.dumps({
        "gemini_active_events": len(gemini_events),
        "gemini_15m_candidates": len(gemini_15m),
        "kalshi_open_markets": len(kalshi_markets),
        "kalshi_15m_candidates": len(kalshi_15m),
        "same_asset_expiry_overlaps": len(overlaps),
        "max_expiry_skew_s": max_expiry_skew_s,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = parse_args()
    asyncio.run(run_once(args.max_expiry_skew_s))


if __name__ == "__main__":
    main()
