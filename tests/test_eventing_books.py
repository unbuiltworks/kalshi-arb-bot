from src.eventing_books import EventingPolyBookStore


def test_poly_store_emits_ws_notification():
    store = EventingPolyBookStore()
    ok = store.ingest(
        {
            "marketSlug": "mkt",
            "state": "MARKET_STATE_OPEN",
            "bids": [{"px": {"value": "0.40"}, "qty": "10"}],
            "offers": [{"px": {"value": "0.60"}, "qty": "10"}],
        },
        source="ws",
    )
    assert ok
    event = store.updates.get_nowait()
    assert event.venue == "polymarket"
    assert event.market_id == "mkt"
    assert event.kind == "ws"


def test_rest_seed_can_be_silenced():
    store = EventingPolyBookStore()
    store.notifications_enabled = False
    assert store.ingest(
        {
            "marketSlug": "mkt",
            "bids": [{"px": "0.40", "qty": "10"}],
            "offers": [{"px": "0.60", "qty": "10"}],
        },
        source="rest",
    )
    assert store.updates.empty()
