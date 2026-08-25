"""Read-only historical agreement study for Gemini vs Kalshi 15-minute crypto.

Matches settled BTC/ETH/SOL/XRP 15-minute Up/Down windows by asset and expiry.
Gemini and Kalshi use different settlement indexes, so this measures empirical
outcome agreement and basis-risk frequency; it never certifies risk-free arbitrage.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import (
    ASSET_ALIASES,
    GEMINI_REST,
    KALSHI_REST,
    _asset_from_text,
    _gemini_meta,
    _kalshi_duration,
    _kalshi_expiry,
)

logger = logging.getLogger("kalshi_arb.gemini_kalshi_settlement_agreement")


@dataclass(frozen=True)
class AgreementRow:
    asset: str
    expiry: datetime
    gemini_ticker: str
    kalshi_ticker: str
    gemini_outcome: str
    kalshi_outcome: str
    agrees: bool
    gemini_strike: float | None
    gemini_settlement: float | None
    gemini_margin_bps: float | None

    @property
    def expiry_utc(self) -> datetime:
        """Backward-compatible alias for read-only settlement-audit consumers.

        ``expiry`` is the canonical window-end field. Older audit code emitted
        ``settlement_expiry_utc`` from an ``expiry_utc`` attribute, so retain this
        deterministic alias rather than letting the settlement join crash after the
        AgreementRow field was normalized to ``expiry``.
        """
        return self.expiry

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "expiry_utc": self.expiry.isoformat(),
            "gemini_ticker": self.gemini_ticker,
            "kalshi_ticker": self.kalshi_ticker,
            "gemini_outcome": self.gemini_outcome,
            "kalshi_outcome": self.kalshi_outcome,
            "agrees": self.agrees,
            "gemini_strike": self.gemini_strike,
            "gemini_settlement": self.gemini_settlement,
            "gemini_margin_bps": self.gemini_margin_bps,
        }


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gemini_up_contract(event: dict[str, Any]) -> dict[str, Any] | None:
    for contract in event.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        ticker = str(contract.get("ticker") or "").upper()
        symbol = str(contract.get("instrumentSymbol") or "").upper()
        label = str(contract.get("label") or "").lower()
        if ticker == "UP" or symbol.endswith("-UP") or label == "up":
            return contract
    contracts = [c for c in event.get("contracts") or [] if isinstance(c, dict)]
    return contracts[0] if len(contracts) == 1 else None


def _gemini_outcome(event: dict[str, Any]) -> tuple[str | None, float | None, float | None, float | None]:
    contract = _gemini_up_contract(event)
    if contract is None:
        return None, None, None, None
    resolution = str(contract.get("resolutionSide") or "").lower()
    if resolution in {"yes", "no"}:
        outcome = resolution
    else:
        outcome = None
    strike_obj = contract.get("strike") or {}
    strike = _float(strike_obj.get("value") if isinstance(strike_obj, dict) else None)
    settlement = _float(contract.get("settlementValue"))
    if settlement is None:
        settlement_obj = event.get("settlement") or {}
        if isinstance(settlement_obj, dict):
            settlement = _float(settlement_obj.get("value"))
    if outcome is None and strike is not None and settlement is not None:
        outcome = "yes" if settlement > strike else "no"
    margin_bps = None
    if strike not in {None, 0.0} and settlement is not None:
        margin_bps = (settlement - strike) / strike * 10000.0
    return outcome, strike, settlement, margin_bps


def _kalshi_outcome(market: dict[str, Any]) -> str | None:
    result = str(market.get("result") or "").lower()
    return result if result in {"yes", "no"} else None


def match_settled(
    gemini_events: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    *,
    max_expiry_skew_s: float = 30.0,
) -> list[AgreementRow]:
    gemini_rows: list[tuple[dict[str, Any], str, datetime, str, float | None, float | None, float | None]] = []
    for event in gemini_events:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        asset, duration, expiry = meta
        if duration != 15 or asset not in ASSET_ALIASES:
            continue
        outcome, strike, settlement, margin_bps = _gemini_outcome(event)
        if outcome is None:
            continue
        gemini_rows.append((event, asset, expiry, outcome, strike, settlement, margin_bps))

    kalshi_rows: list[tuple[dict[str, Any], str, datetime, str]] = []
    for market in kalshi_markets:
        asset = _asset_from_text(market)
        expiry = _kalshi_expiry(market)
        outcome = _kalshi_outcome(market)
        if asset and expiry and outcome and _kalshi_duration(market) == 15:
            kalshi_rows.append((market, asset, expiry, outcome))

    matched: list[AgreementRow] = []
    used_kalshi: set[str] = set()
    for event, asset, expiry, g_out, strike, settlement, margin_bps in gemini_rows:
        candidates = []
        for market, k_asset, k_expiry, k_out in kalshi_rows:
            ticker = str(market.get("ticker") or "")
            if ticker in used_kalshi or k_asset != asset:
                continue
            skew = abs((expiry - k_expiry).total_seconds())
            if skew <= max_expiry_skew_s:
                candidates.append((skew, market, k_out))
        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0])
        _, market, k_out = candidates[0]
        ticker = str(market.get("ticker") or "")
        used_kalshi.add(ticker)
        matched.append(AgreementRow(
            asset=asset,
            expiry=expiry,
            gemini_ticker=str(event.get("ticker") or ""),
            kalshi_ticker=ticker,
            gemini_outcome=g_out,
            kalshi_outcome=k_out,
            agrees=(g_out == k_out),
            gemini_strike=strike,
            gemini_settlement=settlement,
            gemini_margin_bps=round(margin_bps, 4) if margin_bps is not None else None,
        ))
    matched.sort(key=lambda row: (row.expiry, row.asset))
    return matched


async def _gemini_settled(http: httpx.AsyncClient, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < limit:
        batch_limit = min(500, limit - len(rows))
        resp = await http.get(
            f"{GEMINI_REST}/events",
            params={"status": "settled", "category": "crypto", "limit": batch_limit, "offset": offset},
        )
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("data", [])
        rows.extend(batch)
        if len(batch) < batch_limit:
            break
        offset += batch_limit
    return rows


async def _kalshi_settled(http: httpx.AsyncClient, hours: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    while True:
        params: dict[str, Any] = {
            "status": "settled",
            "limit": 1000,
            "mve_filter": "exclude",
            "min_settled_ts": min_settled_ts,
        }
        if cursor:
            params["cursor"] = cursor
        resp = await http.get(f"{KALSHI_REST}/markets", params=params)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("markets", []))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break
    return rows


async def run(hours: int, gemini_limit: int, max_expiry_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled(http, hours),
        )
    rows = match_settled(gemini, kalshi, max_expiry_skew_s=max_expiry_skew_s)
    disagreements = [row for row in rows if not row.agrees]
    by_asset: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_asset.setdefault(row.asset, {"matched": 0, "agreed": 0, "disagreed": 0})
        bucket["matched"] += 1
        bucket["agreed" if row.agrees else "disagreed"] += 1
    for row in disagreements:
        logger.info("GEMINI_KALSHI_DISAGREEMENT %s", json.dumps(row.as_dict(), separators=(",", ":")))
    summary = {
        "gemini_settled_events_scanned": len(gemini),
        "kalshi_settled_markets_scanned": len(kalshi),
        "matched_15m_windows": len(rows),
        "agreements": len(rows) - len(disagreements),
        "disagreements": len(disagreements),
        "agreement_rate": ((len(rows) - len(disagreements)) / len(rows)) if rows else None,
        "by_asset": by_asset,
        "hours": hours,
        "max_expiry_skew_s": max_expiry_skew_s,
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
    }
    logger.info("GEMINI_KALSHI_AGREEMENT_SUMMARY %s", json.dumps(summary, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=168)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_expiry_skew_s))


if __name__ == "__main__":
    main()
