"""Deterministic READ-ONLY Gemini short-duration crypto symbol universe.

Keeps the existing BTC 5-minute research lane and adds BTC 15-minute windows
using Gemini's documented short-duration ticker format. No venue actions live
here; this module only generates public market symbols for collection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

SUPPORTED = (("BTC", 5), ("BTC", 15))


def _next_boundary(now: datetime, minutes: int) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    base = now.replace(second=0, microsecond=0)
    rem = base.minute % minutes
    add = (minutes - rem) % minutes
    if add == 0:
        add = minutes
    return base + timedelta(minutes=add)


def symbols(now: datetime, *, count_per_family: int = 2,
            families: Iterable[tuple[str, int]] = SUPPORTED) -> list[str]:
    out: list[str] = []
    for asset, minutes in families:
        marker = f"{minutes:02d}M"
        first = _next_boundary(now, minutes)
        for i in range(count_per_family):
            expiry = first + timedelta(minutes=minutes * i)
            out.append(f"GEMI-{asset}{marker}{expiry:%y%m%d%H%M}-UP")
    return out


def family(symbol: str) -> str:
    if "BTC15M" in symbol:
        return "BTC15M"
    if "BTC05M" in symbol:
        return "BTC05M"
    if "ETH15M" in symbol:
        return "ETH15M"
    if "ETH05M" in symbol:
        return "ETH05M"
    return "OTHER"
