from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.gemini_kalshi_proxy_calibration import (
    CalibrationState,
    Pair,
    SpotQuote,
    _checkpoint,
    _btc_15m_gemini,
    composite_snapshot,
    kalshi_target,
)

D = Decimal


def test_composite_requires_two_fresh_sources():
    now = 10_000
    quotes = {"coinbase": SpotQuote("coinbase", D("100"), 9_900)}
    composite, count, spread, ages = composite_snapshot(quotes, now_ms=now, max_age_ms=500)
    assert composite is None
    assert count == 1
    assert spread is None
    assert ages["coinbase"] == 100


def test_composite_midpoint_and_spread():
    now = 10_000
    quotes = {
        "coinbase": SpotQuote("coinbase", D("100"), 9_900),
        "gemini_spot": SpotQuote("gemini_spot", D("102"), 9_800),
    }
    composite, count, spread, _ = composite_snapshot(quotes, now_ms=now, max_age_ms=500)
    assert composite == D("101")
    assert count == 2
    assert spread == D("2")


def test_stale_source_fails_closed():
    now = 10_000
    quotes = {
        "coinbase": SpotQuote("coinbase", D("100"), 9_900),
        "gemini_spot": SpotQuote("gemini_spot", D("102"), 8_000),
    }
    composite, count, _, _ = composite_snapshot(quotes, now_ms=now, max_age_ms=500)
    assert composite is None
    assert count == 1


def test_kalshi_target_only_when_unambiguous():
    assert kalshi_target({"floor_strike": 100, "cap_strike": 100}) == D("100")
    assert kalshi_target({"floor_strike": 100, "cap_strike": 101}) is None
    assert kalshi_target({"functional_strike": "100.5"}) == D("100.5")


def test_gemini_identity_uses_api_expiry_and_reference_window():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    event = {
        "ticker": "OPAQUE",
        "expiryDate": "2026-08-18T12:15:00Z",
        "sourceDetails": {"agency": "Kaiko", "index": "GRR-KAIKO_BTCUSD_60S"},
        "contracts": [{
            "instrumentSymbol": "GEMI-OPAQUE-UP",
            "expiryDate": "2026-08-18T12:15:00Z",
            "strike": {
                "type": "reference",
                "value": "60000.00",
                "availableAt": "2026-08-18T12:00:00Z",
            },
        }],
    }
    parsed = _btc_15m_gemini(event, now - timedelta(seconds=1))
    assert parsed is not None
    expiry, contract, strike = parsed
    assert expiry == datetime(2026, 8, 18, 12, 15, tzinfo=timezone.utc)
    assert contract["instrumentSymbol"] == "GEMI-OPAQUE-UP"
    assert strike == D("60000.00")


def test_checkpoint_calculates_dual_boundary_margins():
    expiry = datetime.fromtimestamp(20, tz=timezone.utc)
    pair = Pair(
        gemini_event_ticker="BTC15M",
        gemini_symbol="GEMI-BTC-UP",
        gemini_expiry=expiry,
        gemini_strike=D("100"),
        gemini_source_agency="Kaiko",
        gemini_source_index="GRR-KAIKO_BTCUSD_60S",
        kalshi_ticker="KXBTC15M-X",
        kalshi_expiry=expiry,
        kalshi_target=D("99"),
        kalshi_strike_type="greater",
    )
    state = CalibrationState(quotes={
        "coinbase": SpotQuote("coinbase", D("102"), 18_900),
        "gemini_spot": SpotQuote("gemini_spot", D("104"), 18_900),
    })
    c = _checkpoint(pair, state, 1, 19_000)
    assert c.composite_price == D("103")
    assert c.source_count == 2
    assert c.gemini_distance_dollars == D("3")
    assert c.gemini_distance_bps == D("300")
    assert c.kalshi_distance_dollars == D("4")
