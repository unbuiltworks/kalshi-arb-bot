"""Read-only Gemini + Polymarket US + Kalshi 15-minute crypto universe discovery.

This is the first adapter into the universal quant layer. It discovers currently
aligned Gemini/Kalshi pairs using the existing audited discovery code, discovers
Polymarket US 15-minute crypto candidates from public REST, and identifies
same-asset/same-window three-venue clusters.

A cluster is NOT settlement certification. Every Polymarket join is labeled
NEEDS_RULE_AUDIT and no order-entry code exists here.
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

from src.gemini_kalshi_aligned_watch import Pair, _discover_pairs

logger = logging.getLogger("kalshi_arb.universal_three_venue_crypto")
POLY_GATEWAY = "https://gateway.polymarket.us"

ASSETS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "eth"),
    "SOL": ("solana", "sol"),
    "XRP": ("xrp",),
    "DOGE": ("dogecoin", "doge"),
    "BNB": ("bnb", "binance coin"),
}
FIFTEEN_RE = re.compile(r"\b15[ -]?(?:minutes?|mins?|m)\b", re.I)
UPDOWN_RE = re.compile(r"\b(?:up\s*(?:or|/)\s*down|higher|lower)\b", re.I)


def _text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "") for k in (
        "slug", "question", "title", "description", "category", "subcategory"
    )).lower()


def _asset(row: dict[str, Any]) -> str | None:
    text = _text(row)
    hits: list[str] = []
    for symbol, aliases in ASSETS.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) for alias in aliases):
            hits.append(symbol)
    return hits[0] if len(hits) == 1 else None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _poly_close(row: dict[str, Any]) -> datetime | None:
    return _dt(row.get("endDate") or row.get("end_date") or row.get("closeTime"))


def _looks_15m(row: dict[str, Any]) -> bool:
    text = _text(row)
    return str(row.get("category") or "").lower() == "crypto" and bool(
        FIFTEEN_RE.search(text) and UPDOWN_RE.search(text)
    )


@dataclass(frozen=True)
class PolyCandidate:
    asset: str
    slug: str
    close: datetime
    question: str
    description: str


@dataclass(frozen=True)
class ThreeVenueCluster:
    asset: str
    window_end: datetime
    gemini_symbol: str
    kalshi_ticker: str
    poly_slug: str
    poly_close: datetime
    poly_skew_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "window_end_utc": self.window_end.isoformat(),
            "gemini_symbol": self.gemini_symbol,
            "kalshi_ticker": self.kalshi_ticker,
            "poly_slug": self.poly_slug,
            "poly_close_utc": self.poly_close.isoformat(),
            "poly_window_skew_s": round(self.poly_skew_s, 3),
            "gemini_kalshi_status": "INDEX_MISMATCH_RESEARCH_ONLY",
            "polymarket_join_status": "NEEDS_RULE_AUDIT",
            "read_only": True,
            "order_entry": False,
        }


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
        rows = list(payload.get("markets", []))
        out.extend(rows)
        if len(rows) < limit:
            return out
        offset += limit
        if offset >= 10000:
            logger.warning("Stopping Polymarket pagination at defensive 10k ceiling")
            return out


def poly_candidates(rows: list[dict[str, Any]]) -> list[PolyCandidate]:
    out: list[PolyCandidate] = []
    for row in rows:
        if not _looks_15m(row):
            continue
        asset = _asset(row)
        close = _poly_close(row)
        slug = str(row.get("slug") or "")
        if asset is None or close is None or not slug:
            continue
        out.append(PolyCandidate(
            asset=asset,
            slug=slug,
            close=close,
            question=str(row.get("question") or row.get("title") or ""),
            description=str(row.get("description") or ""),
        ))
    return out


def join_three_venues(
    gemini_kalshi: list[Pair],
    poly: list[PolyCandidate],
    *,
    max_poly_window_skew_s: float,
) -> list[ThreeVenueCluster]:
    out: list[ThreeVenueCluster] = []
    for pair in gemini_kalshi:
        for candidate in poly:
            if pair.asset != candidate.asset:
                continue
            skew = abs((pair.window_end - candidate.close).total_seconds())
            if skew > max_poly_window_skew_s:
                continue
            out.append(ThreeVenueCluster(
                asset=pair.asset,
                window_end=pair.window_end,
                gemini_symbol=pair.gemini_symbol,
                kalshi_ticker=pair.kalshi_ticker,
                poly_slug=candidate.slug,
                poly_close=candidate.close,
                poly_skew_s=skew,
            ))
    out.sort(key=lambda x: (x.window_end, x.asset, x.poly_skew_s))
    return out


async def run_once(*, max_window_skew_s: float, max_poly_window_skew_s: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini_kalshi, poly_rows = await asyncio.gather(
            _discover_pairs(http, max_window_skew_s),
            _poly_open_markets(http),
        )
    poly = poly_candidates(poly_rows)
    clusters = join_three_venues(
        gemini_kalshi, poly, max_poly_window_skew_s=max_poly_window_skew_s
    )
    for cluster in clusters:
        logger.info("UNIVERSAL_CRYPTO_CLUSTER %s", json.dumps(cluster.as_dict(), separators=(",", ":")))
    summary = {
        "gemini_kalshi_pairs": len(gemini_kalshi),
        "polymarket_open_markets": len(poly_rows),
        "polymarket_15m_crypto_candidates": len(poly),
        "three_venue_clusters": len(clusters),
        "assets_in_clusters": sorted({x.asset for x in clusters}),
        "max_gemini_kalshi_skew_s": max_window_skew_s,
        "max_polymarket_skew_s": max_poly_window_skew_s,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("UNIVERSAL_CRYPTO_SUMMARY %s", json.dumps(summary, separators=(",", ":")))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-window-skew-s", type=float, default=30.0)
    parser.add_argument("--max-poly-window-skew-s", type=float, default=120.0)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = parse_args()
    asyncio.run(run_once(
        max_window_skew_s=args.max_window_skew_s,
        max_poly_window_skew_s=args.max_poly_window_skew_s,
    ))


if __name__ == "__main__":
    main()
