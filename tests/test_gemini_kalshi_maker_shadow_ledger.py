from pathlib import Path

from src.gemini_kalshi_maker_shadow_ledger import (
    JsonlLedgerForwarder,
    row_to_trade_event,
    stable_event_id,
)


def test_simulated_fill_never_becomes_realized_trade():
    row = {
        "event": "SIMULATED_FILL",
        "asset": "SOL",
        "receive_ts_ms": 1_800_000_000_000,
        "trade_ts_ms": 1_800_000_000_000,
        "quote_opened_ms": 1_799_999_999_000,
        "kalshi_ticker": "KXSOL15M-TEST",
        "key": "KALSHI_YES_GEMINI_NO|JOIN|0.03",
        "direction": "KALSHI_YES_GEMINI_NO",
        "fill_qty": "1",
        "maker_price": "0.52",
        "maker_fee": "0",
        "hedge_price": "0.42",
        "hedge_fee": "0.01",
        "simulated_fill_profit": "0.05",
    }
    event = row_to_trade_event(row)
    assert event is not None
    assert event["mode"] == "SHADOW"
    assert event["event_type"] == "SHADOW_MAKER_FILL"
    assert event["net_pnl"] is None
    assert event["gross_pnl"] is None
    assert event["opportunity_net_profit"] == "0.05"
    assert event["legs_filled"] == 2
    assert event["metadata"]["realized"] is False
    assert event["metadata"]["shadow_only"] is True


def test_unhedgeable_fill_records_one_sided_shadow_only():
    row = {
        "event": "FILL_HEDGE_UNAVAILABLE",
        "asset": "XRP",
        "receive_ts_ms": 1_800_000_000_000,
        "kalshi_ticker": "KXXRP15M-TEST",
        "key": "GEMINI_YES_KALSHI_NO|MAX_SAFE|0.02",
        "fill_qty": "1",
        "maker_price": "0.55",
    }
    event = row_to_trade_event(row)
    assert event is not None
    assert event["mode"] == "SHADOW"
    assert event["event_type"] == "SHADOW_MAKER_HEDGE_UNAVAILABLE"
    assert event["legs_filled"] == 1
    assert event["opportunity_net_profit"] is None


def test_latency_stress_is_shadow_and_never_realized():
    row = {
        "event": "HEDGE_LATENCY_STRESS",
        "asset": "BTC",
        "receive_ts_ms": 1_800_000_000_100,
        "fill_receive_ts_ms": 1_800_000_000_000,
        "kalshi_ticker": "KXBTC15M-TEST",
        "key": "GEMINI_YES_KALSHI_NO|JOIN|0.03",
        "direction": "GEMINI_YES_KALSHI_NO",
        "fill_qty": "1",
        "maker_price": "0.40",
        "maker_fee": "0",
        "hedge_fee": "0.01",
        "stressed_fill_profit": "0.02",
        "survives_target": False,
        "hedge_depth_ok": True,
        "delay_ms": 100,
    }
    event = row_to_trade_event(row)
    assert event is not None
    assert event["mode"] == "SHADOW"
    assert event["event_type"] == "SHADOW_MAKER_HEDGE_STRESS"
    assert event["status"] == "SHADOW_HEDGE_STRESS_TARGET_FAILED"
    assert event["net_pnl"] is None
    assert event["opportunity_net_profit"] == "0.02"


def test_quote_context_flags_target_created_by_maker_price(tmp_path: Path):
    fwd = JsonlLedgerForwarder(tmp_path)
    opened = {
        "event": "OPEN",
        "asset": "ETH",
        "key": "KALSHI_YES_GEMINI_NO|JOIN|0.03",
        "best_bid": "0.50",
        "best_ask": "0.58",
        "economic_max": "0.55",
        "queue_ahead": "4",
    }
    assert fwd._with_quote_context(opened) == opened
    fill = {
        "event": "SIMULATED_FILL",
        "asset": "ETH",
        "key": opened["key"],
        "maker_price": "0.51",
    }
    enriched = fwd._with_quote_context(fill)
    assert enriched["quote_open_best_ask"] == "0.58"
    assert enriched["quote_open_economic_max"] == "0.55"
    assert enriched["maker_price_advantage_vs_open_ask"] == "0.07"
    assert enriched["maker_created_target_by_price_alone"] is True


def test_quote_context_is_conservative_when_taker_ask_already_within_max(tmp_path: Path):
    fwd = JsonlLedgerForwarder(tmp_path)
    opened = {
        "event": "OPEN",
        "asset": "BTC",
        "key": "GEMINI_YES_KALSHI_NO|MAX_SAFE|0.02",
        "best_bid": "0.40",
        "best_ask": "0.44",
        "economic_max": "0.45",
        "queue_ahead": "0",
    }
    fwd._with_quote_context(opened)
    enriched = fwd._with_quote_context({
        "event": "SIMULATED_FILL",
        "asset": "BTC",
        "key": opened["key"],
        "maker_price": "0.43",
    })
    assert enriched["maker_created_target_by_price_alone"] is False


def test_non_fill_noise_is_not_forwarded():
    assert row_to_trade_event({"event": "OPEN"}) is None
    assert row_to_trade_event({"event": "REPRICE"}) is None
    assert row_to_trade_event({"event": "CANCEL"}) is None


def test_event_id_is_stable_and_content_addressed():
    row = {"event": "SIMULATED_FILL", "receive_ts_ms": 1, "maker_price": "0.5"}
    assert stable_event_id(row) == stable_event_id(dict(row))
    changed = dict(row)
    changed["maker_price"] = "0.51"
    assert stable_event_id(row) != stable_event_id(changed)
