import asyncio

from src.eventing_books import EventingPolyBookStore


def test_poly_store_emits_snapshot_notification():
    async def run():
        store = EventingPolyBookStore(["mkt"])
        store.replace_from_book(
            "mkt",
            {
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.60", "size": "10"}],
            },
        )
        event = store.updates.get_nowait()
        assert event.venue == "polymarket"
        assert event.market_id == "mkt"
        assert event.kind == "snapshot"

    asyncio.run(run())
