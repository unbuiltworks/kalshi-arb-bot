from src.weather_exact_quote_guard import audit_exact_weather_quote


TICKER = "KXHIGHAUS-26AUG28-B100"
URL = f"https://external-api.kalshi.com/trade-api/v2/markets/{TICKER}/orderbook"
OBSERVED = "2026-08-28T15:55:00+00:00"
AUDITED = "2026-08-28T15:55:00.250000+00:00"


def _quote(**overrides):
    row = {
        "yes_ask_cents": 62,
        "yes_ask_qty": 5,
        "no_ask_cents": 41,
        "no_ask_qty": 7,
        "observed_at": OBSERVED,
        "quote_age_ms": 250.0,
    }
    row.update(overrides)
    return row


def _current_book():
    # YES bid 59c x7 => executable NO ask 41c x7.
    # NO bid 38c x5 => executable YES ask 62c x5.
    return {
        "orderbook_fp": {
            "yes_dollars": [["0.5900", "7.00"], ["0.5500", "12.00"]],
            "no_dollars": [["0.3800", "5.00"], ["0.3500", "9.00"]],
        }
    }


def test_accepts_current_kalshi_orderbook_fp_schema():
    result = audit_exact_weather_quote(
        target_market_ticker=TICKER,
        quote=_quote(),
        api_request_url=URL,
        api_orderbook_response=_current_book(),
        orderbook_observed_at=OBSERVED,
        audit_observed_at=AUDITED,
    )
    assert result["exact_quote_verified"] is True
    assert result["blockers"] == []
    assert result["orderbook_representation"] == "orderbook_fp"
    assert result["derived_yes_ask_cents"] == 62
    assert result["derived_yes_ask_qty"] == 5
    assert result["derived_no_ask_cents"] == 41
    assert result["derived_no_ask_qty"] == 7


def test_current_schema_still_fails_closed_on_quote_depth_mismatch():
    result = audit_exact_weather_quote(
        target_market_ticker=TICKER,
        quote=_quote(yes_ask_qty=6),
        api_request_url=URL,
        api_orderbook_response=_current_book(),
        orderbook_observed_at=OBSERVED,
        audit_observed_at=AUDITED,
    )
    assert result["exact_quote_verified"] is False
    assert "YES_EXECUTABLE_QUOTE_DEPTH_MISMATCH" in result["blockers"]


def test_subcent_current_price_fails_closed_instead_of_rounding():
    book = _current_book()
    book["orderbook_fp"]["no_dollars"][0][0] = "0.3850"
    result = audit_exact_weather_quote(
        target_market_ticker=TICKER,
        quote=_quote(),
        api_request_url=URL,
        api_orderbook_response=book,
        orderbook_observed_at=OBSERVED,
        audit_observed_at=AUDITED,
    )
    assert result["exact_quote_verified"] is False
    assert "YES_EXECUTABLE_QUOTE_DEPTH_MISMATCH" in result["blockers"] or "EXACT_MARKET_ORDERBOOK_EMPTY" in result["blockers"]


def test_unrecognized_orderbook_schema_is_explicit_blocker():
    result = audit_exact_weather_quote(
        target_market_ticker=TICKER,
        quote=_quote(),
        api_request_url=URL,
        api_orderbook_response={"book": {}},
        orderbook_observed_at=OBSERVED,
        audit_observed_at=AUDITED,
    )
    assert result["exact_quote_verified"] is False
    assert "EXACT_MARKET_ORDERBOOK_SCHEMA_UNRECOGNIZED" in result["blockers"]
