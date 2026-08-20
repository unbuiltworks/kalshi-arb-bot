from decimal import Decimal

import pytest

from src import gemini_kalshi_crypto_campaign_child_reliable as child
from src.gemini_kalshi_crypto_parallel_campaign import (
    CapitalBook,
    VenueCapitalBook,
    _asset_child_env,
    _venue_costs,
)
from src.gemini_kalshi_gate_attribution import classify_gate

D = Decimal


def test_capital_book_reserves_before_execution_and_blocks_overcommit():
    book = CapitalBook(D("2"))
    book.reserve("btc", D("1"))
    book.reserve("eth", D("1"))
    assert book.reserved == D("2")
    assert book.available == D("0")
    with pytest.raises(RuntimeError):
        book.reserve("sol", D("1"))


def test_capital_book_converts_reservation_to_unsettled_then_releases_on_settlement():
    book = CapitalBook(D("2"))
    book.reserve("btc", D("1"))
    book.convert_to_deployed("btc", D("0.7553"))
    assert book.reserved == D("0")
    assert book.unsettled_deployed == D("0.7553")
    assert book.available == D("1.2447")
    book.settle(D("0.7553"))
    assert book.unsettled_deployed == D("0")
    assert book.available == D("2")


def test_actual_capital_cannot_exceed_reserved_amount():
    book = CapitalBook(D("5"))
    book.reserve("btc", D("1"))
    with pytest.raises(RuntimeError):
        book.convert_to_deployed("btc", D("1.01"))


def test_venue_book_blocks_reuse_of_same_gemini_or_kalshi_cash():
    venues = VenueCapitalBook({"gemini": D("2"), "kalshi": D("10")})
    venues.reserve_pair("btc", D("1"))
    venues.reserve_pair("eth", D("1"))
    assert venues.free_for("gemini") == D("0")
    assert venues.free_for("kalshi") == D("8")
    assert not venues.can_reserve_pair(D("1"))
    with pytest.raises(RuntimeError):
        venues.reserve_pair("sol", D("1"))


def test_venue_book_tracks_unsettled_by_venue_and_releases_after_settlement():
    venues = VenueCapitalBook({"gemini": D("5"), "kalshi": D("5")})
    venues.reserve_pair("btc", D("1"))
    actual = {"gemini": D("0.42"), "kalshi": D("0.33")}
    venues.convert_to_deployed("btc", actual)
    assert venues.unsettled == actual
    assert venues.free_for("gemini") == D("4.58")
    assert venues.free_for("kalshi") == D("4.67")
    venues.settle(actual)
    assert venues.unsettled == {"gemini": D("0"), "kalshi": D("0")}


def test_venue_costs_include_fees():
    result = {
        "gemini": {"filled_quantity": "1", "average_fill_price": "0.4000", "fee_paid": "0.0200"},
        "kalshi": {"filled_quantity": "1", "average_fill_price": "0.3200", "fee_paid": "0.0153"},
    }
    assert _venue_costs(result) == {"gemini": D("0.4200"), "kalshi": D("0.3353")}


def test_asset_child_env_pins_one_lane(monkeypatch):
    env = _asset_child_env("campaign-btc-01", "btc")
    assert env["CRYPTO_CANARY_ASSET"] == "BTC"
    monkeypatch.setenv("CRYPTO_CANARY_ASSET", "ETH")
    assert child._selected_assets() == ("ETH",)


def test_legacy_child_still_scans_all_assets_when_unpinned(monkeypatch):
    monkeypatch.delenv("CRYPTO_CANARY_ASSET", raising=False)
    assert child._selected_assets() == ("BTC", "ETH", "SOL", "XRP")


def test_invalid_asset_pin_fails_closed(monkeypatch):
    monkeypatch.setenv("CRYPTO_CANARY_ASSET", "DOGE")
    with pytest.raises(ValueError):
        child._selected_assets()


def gate_payload(**overrides):
    p = {
        "delay_ms": 50,
        "strict_micro_live_executable_20bp": False,
        "same_window": True,
        "same_original_episode": True,
        "gemini_price_ok": True,
        "kalshi_price_ok": True,
        "gemini_depth_ok": True,
        "kalshi_depth_ok": True,
        "post_activation_books": True,
        "current_quote_skew_ms": 10,
        "gemini_age_ms": 20,
        "kalshi_age_ms": 20,
        "strict_fresh_250ms_and_50ms_skew": True,
        "entry_double_boundary_20bp": True,
        "double_boundary_20bp_now": True,
        "double_boundary_20bp_held_through_episode": True,
    }
    p.update(overrides)
    return p


def test_gate_attribution_executable():
    assert classify_gate(gate_payload(strict_micro_live_executable_20bp=True)) == "EXECUTABLE"


def test_gate_attribution_skew():
    assert classify_gate(gate_payload(current_quote_skew_ms=51, strict_fresh_250ms_and_50ms_skew=False)) == "QUOTE_SKEW"


def test_gate_attribution_boundary():
    assert classify_gate(gate_payload(entry_double_boundary_20bp=False)) == "BOUNDARY_ENTRY"


def test_gate_attribution_depth_and_episode():
    assert classify_gate(gate_payload(gemini_depth_ok=False)) == "DEPTH_GONE"
    assert classify_gate(gate_payload(same_original_episode=False)) == "EPISODE_SURVIVAL"
