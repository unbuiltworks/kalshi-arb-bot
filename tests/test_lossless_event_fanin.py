from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.lossless_event_fanin import next_update_lossless


@dataclass(frozen=True)
class Update:
    venue: str
    mono_ns: int


class Store:
    def __init__(self) -> None:
        self.updates: asyncio.Queue[Update] = asyncio.Queue()


def test_simultaneous_venue_updates_are_both_returned() -> None:
    async def scenario() -> None:
        poly = Store()
        kalshi = Store()
        await poly.updates.put(Update("polymarket", 10))
        await kalshi.updates.put(Update("kalshi", 20))

        first = await next_update_lossless(poly, kalshi)
        second = await next_update_lossless(poly, kalshi)

        assert first.venue == "polymarket"
        assert second.venue == "kalshi"
        assert poly.updates.empty()
        assert kalshi.updates.empty()

    asyncio.run(scenario())


def test_buffer_is_cleared_when_store_instances_change() -> None:
    async def scenario() -> None:
        poly1 = Store()
        kalshi1 = Store()
        await poly1.updates.put(Update("polymarket-old", 10))
        await kalshi1.updates.put(Update("kalshi-old", 20))
        assert (await next_update_lossless(poly1, kalshi1)).venue == "polymarket-old"

        poly2 = Store()
        kalshi2 = Store()
        await poly2.updates.put(Update("polymarket-new", 30))
        result = await next_update_lossless(poly2, kalshi2)
        assert result.venue == "polymarket-new"

    asyncio.run(scenario())
