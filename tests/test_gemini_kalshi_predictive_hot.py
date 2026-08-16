from datetime import datetime, timezone

from src.gemini_kalshi_predictive_hot import _next_window_end, predicted_pair


def ts(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


def test_next_window_end_uses_next_quarter_hour_at_exact_boundary():
    assert _next_window_end(ts("2026-08-16T20:00:00")).isoformat() == "2026-08-16T20:15:00+00:00"


def test_predicts_observed_august_16_2015_contract_ids():
    pair = predicted_pair("BTC", ts("2026-08-16T20:00:00"))
    assert pair.window_end.isoformat() == "2026-08-16T20:15:00+00:00"
    assert pair.gemini_symbol == "GEMI-BTC15M2608162015-UP"
    assert pair.kalshi_ticker == "KXBTC15M-26AUG161615-15"


def test_predicts_observed_august_16_2030_contract_ids():
    pair = predicted_pair("BTC", ts("2026-08-16T20:15:01"))
    assert pair.window_end.isoformat() == "2026-08-16T20:30:00+00:00"
    assert pair.gemini_symbol == "GEMI-BTC15M2608162030-UP"
    assert pair.kalshi_ticker == "KXBTC15M-26AUG161630-30"


def test_close_minute_suffix_tracks_quarter_hour():
    cases = [
        ("2026-08-16T20:00:00", "15"),
        ("2026-08-16T20:15:00", "30"),
        ("2026-08-16T20:30:00", "45"),
        ("2026-08-16T20:45:00", "00"),
    ]
    for instant, suffix in cases:
        assert predicted_pair("ETH", ts(instant)).kalshi_ticker.endswith(f"-{suffix}")


def test_predicts_all_supported_asset_prefixes():
    expected = {
        "BTC": "KXBTC15M-26AUG161615-15",
        "ETH": "KXETH15M-26AUG161615-15",
        "SOL": "KXSOL15M-26AUG161615-15",
        "XRP": "KXXRP15M-26AUG161615-15",
    }
    for asset, kalshi in expected.items():
        pair = predicted_pair(asset, ts("2026-08-16T20:00:01"))
        assert pair.gemini_symbol == f"GEMI-{asset}15M2608162015-UP"
        assert pair.kalshi_ticker == kalshi


def test_dst_conversion_uses_new_york_clock_not_fixed_offset():
    summer = predicted_pair("BTC", ts("2026-08-16T20:00:00"))
    assert "161615" in summer.kalshi_ticker

    winter = predicted_pair("BTC", ts("2026-01-16T20:00:00"))
    assert winter.kalshi_ticker == "KXBTC15M-26JAN161515-15"
