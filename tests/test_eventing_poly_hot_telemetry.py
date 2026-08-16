from __future__ import annotations

from src.eventing_books import EventingPolyBookStore


def _book(slug: str, *, two_sided: bool = True) -> dict:
    payload = {
        "marketSlug": slug,
        "bids": [{"px": "0.40", "qty": "5"}],
        "state": "OPEN",
    }
    if two_sided:
        payload["offers"] = [{"px": "0.50", "qty": "4"}]
    return payload


def test_raw_ws_telemetry_distinguishes_usable_and_rejected_books() -> None:
    store = EventingPolyBookStore()

    assert store.ingest(_book("usable"), source="ws") is True
    assert store.ingest(_book("one-sided", two_sided=False), source="ws") is False

    assert store.raw_ws_messages == 2
    assert store.raw_ws_seen_slugs == {"usable", "one-sided"}
    assert store.raw_ws_rejected_messages == 1
    assert store.raw_ws_rejected_slugs == {"one-sided"}
    assert store.quote_count == 1
    assert store.ws_updates == 1


def test_each_accepted_message_emits_exactly_one_market_trigger() -> None:
    store = EventingPolyBookStore()

    assert store.ingest(_book("alpha"), source="ws") is True
    assert store.updates.qsize() == 1
    first = store.updates.get_nowait()
    assert first.market_id == "alpha"
    assert first.venue == "polymarket"

    changed = _book("alpha")
    changed["offers"][0]["px"] = "0.49"
    assert store.ingest(changed, source="ws") is True
    assert store.updates.qsize() == 1
    second = store.updates.get_nowait()
    assert second.market_id == "alpha"

    # A rejected one-sided message is visible in raw telemetry but cannot wake the
    # arb evaluator because it did not produce an executable two-sided quote.
    assert store.ingest(_book("beta", two_sided=False), source="ws") is False
    assert store.updates.empty()
