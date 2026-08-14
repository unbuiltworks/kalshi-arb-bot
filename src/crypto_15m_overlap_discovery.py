"""Read-only discovery for recurring 15-minute crypto overlap.

Goal: find same-asset, same-close-window candidates on Kalshi and Polymarket US
that may be suitable for a fast-settlement cross-market arbitrage lane.

This file performs public REST reads only. It does not authenticate and contains
no order-entry methods. Candidate matches are explicitly labeled NEEDS_RULE_AUDIT;
close-time alignment is not treated as proof of settlement equivalence.
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

logger = logging.getLogger("kalshi_arb.crypto_15m_overlap")

POLY_GATEWAY = "https://gateway.polymarket.us"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"

ASSETS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "eth"),
    "SOL": ("solana", "sol"),
    "DOGE": ("dogecoin", "doge"),
    "XRP": ("xrp",),
    "BNB": ("bnb", "binance coin"),
}

FIFTEEN_RE = re.compile(r"\b15[ -]?(?:minutes?|mins?|m)\b", re.I)
UPDOWN_RE = re.compile(r"\b(?:up\s*(?:or|/)\s*down|higher|lower)\b", re.I)


def _text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in (
            "ticker", "event_ticker", "title", "subtitle", "yes_sub_title",
            "no_sub_title", "rules_primary", "rules_secondary", "question",
            "description", "slug", "category", "subcategory",
        )
    ).lower()


def _asset(row: dict[str, Any]) -> str | None:
    text = _text(row)
    hits = []
    for symbol, aliases in ASSETS.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) for alias in aliases):
            hits.append(symbol)
    return hits[0] if len(hits) == 1 else None


def _looks_15m(row: dict[str, Any]) -> bool:
    text = _text(row)
    return bool(FIFTEEN_RE.search(text) and UPDOWN_RE.search(text))


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _kalshi_close(row: dict[str, Any]) -> datetime | None:
    return _dt(row.get("close_time") or row.get("expected_expiration_time") or row.get("expiration_time"))


def _poly_close(row: dict[str, Any]) -> datetime | None:
    return _dt(row.get("endDate") or row.get("end_date") or row.get("closeTime"))


@dataclass(frozen=True)
class CandidatePair:
    asset: str
    kalshi_ticker: str
    poly_slug: str
    kalshi_close: datetime
    poly_close: datetime
    close_skew_s: float
    kalshi_title: str
    poly_question: str
    kalshi_rules_primary: str
    kalshi_rules_secondary: str
    poly_description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "kalshi_ticker": self.kalshi_ticker,
            "poly_slug": self.poly_slug,
            "kalshi_close_utc": self.kalshi_close.isoformat(),
            "poly_close_utc": self.poly_close.isoformat(),
            "close_skew_s": round(self.close_skew_s, 3),
            "settlement_status": "NEEDS_RULE_AUDIT",
            "kalshi_title": self.kalshi_title,
            "poly_question": self.poly_question,
            "kalshi_rules_primary": self.kalshi_rules_primary,
            "kalshi_rules_secondary": self.kalshi_rules_secondary,
            "poly_description": self.poly_description,
        }


async def _kalshi_open_markets(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"limit": 1000, "status": "open", "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        response = await http.get(f"{KALSHI_REST}/markets", params=params)
        response.raise_for_status()
        payload = response.json()
        out.extend(payload.get("markets", []))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return out


async def _poly_open_markets(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    limit = 500
    while True:
        response = await http.get(
            f"{POLY_GATEWAY}/v1/markets",
            params={"limit": limit, "offset": offset, "active": "true", "closed": "false"},
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("markets", [])
        out.extend(rows)
        if len(rows) < limit:
            return out
        offset += limit
        if offset >= 5000:
            logger.warning("Stopping Polymarket pagination at defensive 5000-market ceiling")
            return out


def discover_candidates(
    kalshi_rows: list[dict[str, Any]],
    poly_rows: list[dict[str, Any]],
    *,
    max_close_skew_s: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[CandidatePair]]:
    kalshi = [r for r in kalshi_rows if _asset(r) and _looks_15m(r)]
    poly = [
        r for r in poly_rows
        if str(r.get("category") or "").lower() == "crypto" and _asset(r) and _looks_15m(r)
    ]

    pairs: list[CandidatePair] = []
    for k in kalshi:
        ka = _asset(k)
        kc = _kalshi_close(k)
        if ka is None or kc is None:
            continue
        for p in poly:
            if _asset(p) != ka:
                continue
            pc = _poly_close(p)
            if pc is None:
                continue
            skew = abs((kc - pc).total_seconds())
            if skew > max_close_skew_s:
                continue
            pairs.append(CandidatePair(
                asset=ka,
                kalshi_ticker=str(k.get("ticker") or ""),
                poly_slug=str(p.get("slug") or ""),
                kalshi_close=kc,
                poly_close=pc,
                close_skew_s=skew,
                kalshi_title=str(k.get("title") or k.get("subtitle") or ""),
                poly_question=str(p.get("question") or p.get("title") or ""),
                kalshi_rules_primary=str(k.get("rules_primary") or ""),
                kalshi_rules_secondary=str(k.get("rules_secondary") or ""),
                poly_description=str(p.get("description") or ""),
            ))
    pairs.sort(key=lambda x: (x.kalshi_close, x.asset, x.close_skew_s))
    return kalshi, poly, pairs


async def run_once(max_close_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        kalshi_rows, poly_rows = await asyncio.gather(
            _kalshi_open_markets(http),
            _poly_open_markets(http),
        )
    kalshi, poly, pairs = discover_candidates(
        kalshi_rows, poly_rows, max_close_skew_s=max_close_skew_s
    )

    for row in kalshi:
        logger.info("CRYPTO15_KALSHI %s", json.dumps({
            "asset": _asset(row),
            "ticker": row.get("ticker"),
            "event_ticker": row.get("event_ticker"),
            "title": row.get("title"),
            "close_time": row.get("close_time"),
            "rules_primary": row.get("rules_primary"),
            "rules_secondary": row.get("rules_secondary"),
        }, separators=(",", ":")))
    for row in poly:
        logger.info("CRYPTO15_POLY %s", json.dumps({
            "asset": _asset(row),
            "slug": row.get("slug"),
            "question": row.get("question"),
            "startDate": row.get("startDate"),
            "endDate": row.get("endDate"),
            "description": row.get("description"),
        }, separators=(",", ":")))
    for pair in pairs:
        logger.info("CRYPTO15_OVERLAP %s", json.dumps(pair.as_dict(), separators=(",", ":")))

    logger.info("CRYPTO15_SUMMARY %s", json.dumps({
        "kalshi_open_markets": len(kalshi_rows),
        "poly_open_markets": len(poly_rows),
        "kalshi_15m_candidates": len(kalshi),
        "poly_15m_candidates": len(poly),
        "same_asset_close_aligned_candidates": len(pairs),
        "max_close_skew_s": max_close_skew_s,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-close-skew-s", type=float, default=120.0)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = parse_args()
    asyncio.run(run_once(max_close_skew_s=args.max_close_skew_s))


if __name__ == "__main__":
    main()
