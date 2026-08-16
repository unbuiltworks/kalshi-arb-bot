import json

from src.gemini_kalshi_multi_ws_analyze import analyze_asset, load_asset_rows


def _row(asset: str, ts: int):
    return {
        "asset": asset,
        "window_end_utc": "2026-08-15T20:00:00+00:00",
        "gemini_symbol": f"GEMI-{asset}15M-TEST-UP",
        "kalshi_ticker": f"KX{asset}15M-TEST",
        "receive_ts_ms": ts,
        "gemini_receive_ts_ms": ts - 2,
        "kalshi_receive_ts_ms": ts - 4,
        "kalshi_exchange_to_receive_ms": 8,
        "gemini_yes_bid": "0.60",
        "gemini_yes_bid_size": "10",
        "gemini_yes_ask": "0.61",
        "gemini_yes_ask_size": "10",
        "kalshi_yes_bid": "0.34",
        "kalshi_yes_bid_size": "10",
        "kalshi_yes_ask": "0.35",
        "kalshi_yes_ask_size": "10",
        "both_taker_fee_candidates": [],
        "read_only": True,
        "order_entry": False,
    }


def test_load_asset_rows_filters_asset(tmp_path):
    path = tmp_path / "multi.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in [_row("BTC", 100), _row("ETH", 200)]),
        encoding="utf-8",
    )
    rows = load_asset_rows([path], "ETH")
    assert len(rows) == 1
    assert rows[0]["asset"] == "ETH"


def test_analyze_asset_labels_result(tmp_path):
    path = tmp_path / "multi.jsonl"
    path.write_text(json.dumps(_row("SOL", 100)) + "\n", encoding="utf-8")
    result = analyze_asset([path], "SOL")
    assert result["asset"] == "SOL"
    assert result["rows"] == 1
    assert result["read_only"] is True
    assert result["order_entry"] is False
