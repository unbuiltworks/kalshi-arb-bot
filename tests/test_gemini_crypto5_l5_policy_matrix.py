from decimal import Decimal as D
import time

from src.gemini_crypto5_l5_policy_matrix_shadow import (
    apply_tier_policy,
    events_from_rows_with_volume,
)
from src.gemini_l5_tape_dynamic_shadow import MarketState
from src.gemini_l5_threshold_shadow import Event


def _event(asset: str = "ETH") -> Event:
    return Event(
        asset=asset,
        horizon="5m",
        ticker=f"{asset}05M2608310400",
        symbol=f"GEMI-{asset}05M2608310400-UP",
        expiry_ms=int(time.time() * 1000) + 300_000,
        price_increment=D("0.01"),
    )


def test_discovery_rows_cover_all_supported_crypto5_assets_and_usd_volume() -> None:
    now_ms = 1_788_145_000_000
    expiry = "2026-08-31T04:00:00+00:00"
    rows = []
    for asset, volume in (("BTC", "1600.25"), ("ETH", "500"), ("SOL", "250"), ("XRP", "0")):
        rows.append({
            "ticker": f"{asset}05M2608310400",
            "volume": volume,
            "contracts": [{
                "instrumentSymbol": f"GEMI-{asset}05M2608310400-UP",
                "marketState": "open",
                "expiryDate": expiry,
                "priceIncrement": "0.01",
            }],
        })
    events, volumes = events_from_rows_with_volume(rows, now_ms=now_ms, max_seconds_to_expiry=650)
    assert {event.asset for event in events.values()} == {"BTC", "ETH", "SOL", "XRP"}
    assert volumes["GEMI-BTC05M2608310400-UP"] == D("1600.25")
    assert volumes["GEMI-XRP05M2608310400-UP"] == D("0")


def test_warm_tier_uses_best_bid_and_caps_target_at_one() -> None:
    state = MarketState(_event())
    state.on_book(D("0.68"), D("0.72"), "now")
    profile = apply_tier_policy(state, bid=D("0.68"), ask=D("0.72"), tier="WARM")
    assert state.favored_side == "YES"
    assert state.target_units == D("1")
    assert state.yes_quote == D("0.68")
    assert state.no_quote is None
    assert profile["effective_skew_ticks"] == 0


def test_hot_tier_keeps_original_l5_one_tick_behavior_and_caps_two() -> None:
    state = MarketState(_event())
    state.on_book(D("0.68"), D("0.72"), "now")
    original = state.yes_quote
    profile = apply_tier_policy(state, bid=D("0.68"), ask=D("0.72"), tier="HOT")
    assert state.target_units == D("2")
    assert state.yes_quote == original
    assert profile["effective_skew_ticks"] == 1


def test_very_hot_never_crosses_ask_or_erases_inventory() -> None:
    state = MarketState(_event())
    state.on_book(D("0.68"), D("0.72"), "now")
    state.tape.fill("YES", D("0.68"), D("1"), "before", "TEST")
    held_before = state.tape.yes_qty
    profile = apply_tier_policy(state, bid=D("0.68"), ask=D("0.72"), tier="VERY_HOT")
    assert state.yes_quote is not None
    assert state.yes_quote < D("0.72")
    assert state.tape.yes_qty == held_before
    assert D(profile["effective_target_units"]) <= D("3")


def test_cold_clears_only_resting_quote_intent() -> None:
    state = MarketState(_event())
    state.on_book(D("0.68"), D("0.72"), "now")
    state.tape.fill("YES", D("0.68"), D("1"), "before", "TEST")
    apply_tier_policy(state, bid=D("0.68"), ask=D("0.72"), tier="COLD")
    assert state.yes_quote is None and state.no_quote is None
    assert state.tape.yes_qty == D("1")
