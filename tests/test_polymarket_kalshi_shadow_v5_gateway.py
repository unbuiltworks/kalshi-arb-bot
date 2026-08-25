from __future__ import annotations

import asyncio

from src.polymarket_kalshi_shadow_v5_gateway import (
    POLY_PUBLIC_BOOK_HOST,
    _fetch_poly_book_public,
    _retryable_inventory_gap,
)


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"market": "x", "bids": [], "asks": []}


class _Http:
    def __init__(self):
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _Store:
    def __init__(self):
        self.rows = []

    def ingest(self, payload, *, source):
        self.rows.append((payload, source))
        return True


def test_public_seed_uses_gateway_and_origin_header():
    http = _Http()
    store = _Store()
    result = asyncio.run(
        _fetch_poly_book_public(http, store, "mlb-test-market", asyncio.Semaphore(1))
    )

    assert result is True
    assert http.calls[0][0] == f"{POLY_PUBLIC_BOOK_HOST}/v1/markets/mlb-test-market/book"
    headers = http.calls[0][1]["headers"]
    assert headers["Origin"] == "https://polymarket.us"
    assert "User-Agent" in headers
    assert store.rows[0][1] == "rest"


def test_only_known_no_inventory_states_are_retryable():
    assert _retryable_inventory_gap(
        RuntimeError("No strict Polymarket/Kalshi matches discovered")
    )
    assert _retryable_inventory_gap(
        RuntimeError("No strict pairs have a current two-sided Polymarket book")
    )
    assert not _retryable_inventory_gap(RuntimeError("startup readiness timeout"))
    assert not _retryable_inventory_gap(ValueError("No strict Polymarket/Kalshi matches discovered"))
