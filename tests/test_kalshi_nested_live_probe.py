import asyncio
from decimal import Decimal

import pytest

from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_live_probe import (
    PROD_REST,
    PROD_WS,
    assert_fok_filled,
    build_fragment_hedge_payload,
    conservative_fragment_locked_profit,
    fill_fragment_quantity,
    maker_quote_is_inside,
    parse_create_state,
    purchased_ask,
    purchased_bid,
)
from src.kalshi_nested_live_probe_guard import ProbeLeg

D = Decimal


def test_uses_current_dedicated_external_trade_api_hosts():
    assert PROD_REST == "https://external-api.kalshi.com/trade-api/v2"
    assert PROD_WS == "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


def test_create_response_quantity_cap():
    state = parse_create_state({
        "order_id": "m1",
        "fill_count": "0.25",
        "remaining_count": "0.75",
        "ts_ms": 123,
    })
    assert state.fill_count == D("0.25")
    assert state.remaining_count == D("0.75")
    with pytest.raises(ValueError, match="quantity cap"):
        parse_create_state({
            "order_id": "bad",
            "fill_count": "0.50",
            "remaining_count": "0.51",
        })


def test_fok_response_must_be_exact_full_fragment():
    state = assert_fok_filled({
        "order_id": "h1",
        "fill_count": "0.25",
        "remaining_count": "0.00",
        "ts_ms": 456,
    }, expected_quantity=D("0.25"))
    assert state.order_id == "h1"
    with pytest.raises(RuntimeError, match="did not fully fill"):
        assert_fok_filled({
            "order_id": "h2",
            "fill_count": "0.20",
            "remaining_count": "0.05",
        }, expected_quantity=D("0.25"))


def test_private_fill_matches_exact_maker_order_and_accepts_fraction():
    assert fill_fragment_quantity({
        "order_id": "maker-1",
        "count_fp": "0.25",
    }, maker_order_id="maker-1") == D("0.25")
    assert fill_fragment_quantity({
        "order_id": "some-other-order",
        "count_fp": "1.00",
    }, maker_order_id="maker-1") is None
    with pytest.raises(RuntimeError, match="outside"):
        fill_fragment_quantity({
            "order_id": "maker-1",
            "count_fp": "1.01",
        }, maker_order_id="maker-1")


def test_orderbook_buy_costs_are_complementary_bids():
    book = KalshiOrderBook("M")
    book.yes = {D("0.03"): D("8"), D("0.04"): D("3")}
    book.no = {D("0.95"): D("7")}
    assert purchased_bid(book, "yes") == D("0.04")
    assert purchased_bid(book, "no") == D("0.95")
    assert purchased_ask(book, "yes") == (D("0.05"), D("7"))
    assert purchased_ask(book, "no") == (D("0.96"), D("3"))


def test_maker_quote_must_be_strictly_inside_spread():
    book = KalshiOrderBook("M")
    book.yes = {D("0.03"): D("8")}
    book.no = {D("0.95"): D("7")}
    assert maker_quote_is_inside(book, ProbeLeg("M", "yes", D("0.04")))
    assert not maker_quote_is_inside(book, ProbeLeg("M", "yes", D("0.03")))
    assert not maker_quote_is_inside(book, ProbeLeg("M", "yes", D("0.05")))


def test_fractional_hedge_payload_is_fok_and_never_expands_exposure():
    payload = build_fragment_hedge_payload(
        base_leg=ProbeLeg("H", "yes", D("0.01")),
        quantity=D("0.25"),
        live_cost=D("0.02"),
        client_order_id="frag-1",
    )
    assert payload["count"] == "0.25"
    assert payload["time_in_force"] == "fill_or_kill"
    assert payload["post_only"] is False
    assert payload["price"] == "0.0200"
    with pytest.raises(ValueError, match="0.01..1.00"):
        build_fragment_hedge_payload(
            base_leg=ProbeLeg("H", "yes", D("0.01")),
            quantity=D("1.01"),
            live_cost=D("0.02"),
            client_order_id="frag-too-big",
        )


def test_fractional_profit_model_is_conservative_and_quantity_aware():
    # 0.50 of the 95c maker + 1c hedge has $0.02 gross room before
    # the conservative whole-cent fee helper, leaving $0.01 modeled min P&L.
    assert conservative_fragment_locked_profit(
        quantity=D("0.50"),
        maker_cost=D("0.95"),
        hedge_cost=D("0.01"),
        hedge_outcome="yes",
        fee_multiplier=D("1"),
    ) == D("0.0100")


def test_module_import_and_helpers_do_not_place_orders():
    # This test intentionally exercises only pure helpers. Real transport is gated
    # inside run_live_probe and no call above can instantiate it or send a request.
    state = parse_create_state({
        "order_id": "fixture-only",
        "fill_count": "0.00",
        "remaining_count": "1.00",
    })
    assert state.order_id == "fixture-only"
