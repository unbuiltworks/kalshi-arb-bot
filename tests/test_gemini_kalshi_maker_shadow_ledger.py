from src.gemini_kalshi_maker_shadow_ledger import row_to_trade_event, stable_event_id


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
