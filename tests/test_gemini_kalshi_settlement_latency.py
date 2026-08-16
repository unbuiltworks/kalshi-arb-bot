from src.gemini_kalshi_settlement_latency import latency_rows, summarize


def _market(
    ticker: str,
    *,
    close: str,
    expected: str,
    settled: str,
    timer: int = 60,
):
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "close_time": close,
        "expected_expiration_time": expected,
        "settlement_ts": settled,
        "settlement_timer_seconds": timer,
    }


def test_latency_rows_measure_close_to_actual_settlement():
    rows = latency_rows([
        _market(
            "KXBTC15M-26AUG151400-00",
            close="2026-08-15T18:00:00Z",
            expected="2026-08-15T18:00:05Z",
            settled="2026-08-15T18:01:05Z",
            timer=60,
        )
    ])
    assert len(rows) == 1
    row = rows[0]
    assert row["asset"] == "BTC"
    assert row["close_to_expected_resolution_s"] == 5.0
    assert row["expected_resolution_to_settlement_s"] == 60.0
    assert row["close_to_settlement_s"] == 65.0


def test_latency_rows_ignore_other_series_and_bad_negative_latency():
    rows = latency_rows([
        _market(
            "KXBTC5M-26AUG151400-00",
            close="2026-08-15T18:00:00Z",
            expected="2026-08-15T18:00:05Z",
            settled="2026-08-15T18:01:05Z",
        ),
        _market(
            "KXETH15M-26AUG151400-00",
            close="2026-08-15T18:00:00Z",
            expected="2026-08-15T18:00:05Z",
            settled="2026-08-15T17:59:59Z",
        ),
    ])
    assert rows == []


def test_summarize_reports_asset_percentiles_and_capital_warning():
    markets = [
        _market(
            "KXBTC15M-A",
            close="2026-08-15T18:00:00Z",
            expected="2026-08-15T18:00:05Z",
            settled="2026-08-15T18:01:05Z",
            timer=60,
        ),
        _market(
            "KXBTC15M-B",
            close="2026-08-15T18:15:00Z",
            expected="2026-08-15T18:15:05Z",
            settled="2026-08-15T18:17:05Z",
            timer=60,
        ),
        _market(
            "KXSOL15M-C",
            close="2026-08-15T18:00:00Z",
            expected="2026-08-15T18:00:10Z",
            settled="2026-08-15T18:01:10Z",
            timer=60,
        ),
    ]
    result = summarize(markets)
    assert result["matched_15m_markets"] == 3
    assert result["by_asset"]["BTC"]["markets"] == 2
    assert result["by_asset"]["BTC"]["close_to_settlement"]["median_s"] == 95.0
    assert result["by_asset"]["SOL"]["close_to_settlement"]["median_s"] == 70.0
    assert "WINDOW_END_ONLY_IS_A_LOWER_BOUND" in result["capital_lock_implication"]
    assert result["order_entry"] is False
