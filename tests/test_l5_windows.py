"""Window/asset classification and scope filtering."""

from __future__ import annotations

from src.polymarket_l5 import config
from src.polymarket_l5.normalize import load_trade_records
from src.polymarket_l5.windows import (
    annotate_trades,
    build_market_meta,
    classify_asset,
    load_market_index,
    window_from_duration,
    window_from_text,
)
from tests.fixtures.l5_synthetic import WINDOW_START, market, standard_scenario


def test_asset_classification_requires_word_boundaries():
    assert classify_asset("bitcoin-up-or-down-5m") == ("BTC", "text")
    assert classify_asset("ETH Up or Down") == ("ETH", "text")
    assert classify_asset("btc vs eth spread")[0] == "AMBIGUOUS"
    assert classify_asset("something-method-ethics")[0] == ""
    assert classify_asset("solana up or down")[0] == ""


def test_window_from_duration_snaps_only_within_tolerance():
    assert window_from_duration(300) == 5
    assert window_from_duration(300 + config.WINDOW_TOLERANCE_S) == 5
    assert window_from_duration(300 + config.WINDOW_TOLERANCE_S + 1) is None
    assert window_from_duration(900) == 15
    assert window_from_duration(3600) is None
    assert window_from_duration(None) is None


def test_window_from_text_prefers_fifteen_over_five():
    assert window_from_text("BTC Up or Down 15 minute") == 15
    assert window_from_text("BTC 5-min") == 5
    assert window_from_text("BTC hourly") is None


def test_duration_wins_over_conflicting_title():
    meta = build_market_meta(
        market("0xz", "bitcoin-up-or-down-5-minute", "Bitcoin Up or Down 5 minute", WINDOW_START, 900)
    )
    assert meta.window_minutes == 15
    assert meta.window_source == "duration_over_text"
    assert any(flag.startswith("window_text_conflict") for flag in meta.flags)
    assert meta.in_scope is True


def test_text_only_classification_is_flagged_and_out_of_scope():
    raw = market("0xz", "bitcoin-up-or-down-5-minute", "Bitcoin Up or Down 5 minute", WINDOW_START, 300)
    raw.pop("startDate")
    raw.pop("endDate")
    meta = build_market_meta(raw)
    assert meta.window_minutes == 5
    assert meta.window_source == "text_only"
    assert "window_text_only" in meta.flags
    assert meta.in_scope is False


def test_off_bucket_duration_is_not_in_scope():
    meta = build_market_meta(
        market("0xz", "bitcoin-up-or-down-hourly", "Bitcoin Up or Down hourly", WINDOW_START, 3600)
    )
    assert meta.window_minutes is None
    assert meta.in_scope is False


def test_resolution_is_read_from_outcome_prices():
    up = build_market_meta(market("0xz", "s", "Bitcoin Up or Down", WINDOW_START, 300, winner="Up"))
    down = build_market_meta(market("0xz", "s", "Bitcoin Up or Down", WINDOW_START, 300, winner="Down"))
    assert up.winning_outcome == "Up"
    assert down.winning_outcome == "Down"
    assert up.resolution_source == "gamma_outcome_prices"

    open_market = market("0xz", "s", "Bitcoin Up or Down", WINDOW_START, 300, closed=False)
    assert build_market_meta(open_market).winning_outcome == ""


def test_annotate_trades_scopes_and_times_fills(tmp_path):
    cache = standard_scenario(tmp_path / "raw")
    fills, _report = load_trade_records(cache)
    index = load_market_index(cache)
    in_scope, out_scope = annotate_trades(fills, index)

    assert len(in_scope) == 5
    assert {row["asset"] for row in in_scope} == {"BTC", "ETH"}
    assert {row["window_minutes"] for row in in_scope} == {5, 15}

    reasons = {row["exclusion_reason"] for row in out_scope}
    assert reasons == {"window_not_5_or_15:none", "asset_not_btc_eth"}

    first = next(row for row in in_scope if row["transaction_hash"] == "0xa1")
    assert first["seconds_from_window_start"] == 30
    assert first["seconds_to_expiry"] == 270


def test_missing_market_metadata_is_never_guessed_into_scope(tmp_path):
    cache = standard_scenario(tmp_path / "raw")
    fills, _report = load_trade_records(cache)
    in_scope, out_scope = annotate_trades(fills, {})
    assert in_scope == []
    assert all(row["exclusion_reason"] == "no_market_metadata" for row in out_scope)
