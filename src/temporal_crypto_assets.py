"""Venue/asset registry for short-duration temporal crypto research.

This module is configuration only: it grants no order authority and does not
assert that a listed market has sufficient liquidity or profitable edge.
Availability must be re-discovered at runtime before a worker is activated.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemporalAsset:
    symbol: str
    name: str
    priority: int
    polymarket: bool = False
    kalshi: bool = False
    gemini_predictions: bool = False

    @property
    def venue_count(self) -> int:
        return sum((self.polymarket, self.kalshi, self.gemini_predictions))


# Availability snapshot for architecture/bootstrap only. Runtime discovery is
# authoritative because prediction-market listings can change quickly.
ASSETS: dict[str, TemporalAsset] = {
    "BTC": TemporalAsset("BTC", "Bitcoin", 1, True, True, True),
    "ETH": TemporalAsset("ETH", "Ethereum", 1, True, True, True),
    "SOL": TemporalAsset("SOL", "Solana", 1, True, True, True),
    "XRP": TemporalAsset("XRP", "XRP", 1, True, True, True),
    "DOGE": TemporalAsset("DOGE", "Dogecoin", 2, False, True, False),
    "BNB": TemporalAsset("BNB", "BNB", 2, False, True, False),
    "HYPE": TemporalAsset("HYPE", "Hyperliquid", 3, False, True, False),
    "ZEC": TemporalAsset("ZEC", "Zcash", 3, False, True, False),
    "NEAR": TemporalAsset("NEAR", "NEAR", 3, False, True, False),
}


def assets_for_venue(venue: str, *, max_priority: int | None = None) -> list[str]:
    field = {
        "polymarket": "polymarket",
        "kalshi": "kalshi",
        "gemini": "gemini_predictions",
        "gemini_predictions": "gemini_predictions",
    }.get(venue.strip().lower())
    if field is None:
        raise ValueError(f"unknown venue: {venue}")

    rows = [
        asset
        for asset in ASSETS.values()
        if getattr(asset, field)
        and (max_priority is None or asset.priority <= max_priority)
    ]
    return [asset.symbol for asset in sorted(rows, key=lambda a: (a.priority, a.symbol))]


def common_assets(*venues: str, max_priority: int | None = None) -> list[str]:
    if not venues:
        raise ValueError("at least one venue is required")
    sets = [set(assets_for_venue(v, max_priority=max_priority)) for v in venues]
    return sorted(set.intersection(*sets))
