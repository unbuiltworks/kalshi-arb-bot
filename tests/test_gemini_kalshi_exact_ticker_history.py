from datetime import datetime, timezone

from src.gemini_kalshi_exact_ticker_history import planned_tickers


def test_24h_plan_has_four_assets_for_96_settled_windows():
    plan = planned_tickers(
        now=datetime(2026, 8, 22, 15, 7, tzinfo=timezone.utc),
        hours=24,
    )
    assert len(plan) == 384
    assert plan[-4:] == [
        ("BTC", datetime(2026, 8, 22, 14, 45, tzinfo=timezone.utc), "BTC15M2608221445"),
        ("ETH", datetime(2026, 8, 22, 14, 45, tzinfo=timezone.utc), "ETH15M2608221445"),
        ("SOL", datetime(2026, 8, 22, 14, 45, tzinfo=timezone.utc), "SOL15M2608221445"),
        ("XRP", datetime(2026, 8, 22, 14, 45, tzinfo=timezone.utc), "XRP15M2608221445"),
    ]
    assert plan[0][2] == "BTC15M2608211500"


def test_plan_is_read_only_identifier_generation_only():
    plan = planned_tickers(
        now=datetime(2026, 8, 22, 1, 1, tzinfo=timezone.utc),
        hours=1,
    )
    assert [ticker for _asset, _expiry, ticker in plan] == [
        "BTC15M2608220000", "ETH15M2608220000", "SOL15M2608220000", "XRP15M2608220000",
        "BTC15M2608220015", "ETH15M2608220015", "SOL15M2608220015", "XRP15M2608220015",
        "BTC15M2608220030", "ETH15M2608220030", "SOL15M2608220030", "XRP15M2608220030",
        "BTC15M2608220045", "ETH15M2608220045", "SOL15M2608220045", "XRP15M2608220045",
    ]
