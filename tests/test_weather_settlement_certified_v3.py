import src.weather_settlement_certified_v3 as v3


MARKET_TICKER = "KXHIGHAUS-26AUG28-B100"
EVENT_TICKER = "KXHIGHAUS-26AUG28"
REQUEST_URL = f"https://external-api.kalshi.com/trade-api/v2/markets/{MARKET_TICKER}"
ORDERBOOK_URL = f"{REQUEST_URL}/orderbook"
OBSERVED_AT = "2026-08-28T14:00:00Z"
AUDIT_AT = "2026-08-28T14:00:00.100000Z"
QUOTE = {
    "yes_ask_cents": 45,
    "yes_ask_qty": 9,
    "no_ask_cents": 60,
    "no_ask_qty": 7,
    "quote_age_ms": 100,
    "observed_at": OBSERVED_AT,
}
ORDERBOOK = {"orderbook": {"yes": [[35, 2], [40, 7]], "no": [[50, 3], [55, 9]]}}
API_MARKET = {
    "ticker": MARKET_TICKER,
    "event_ticker": EVENT_TICKER,
    "strike_type": "between",
    "floor_strike": 99,
    "cap_strike": 100,
    "rules_primary": "Resolves from the specified Daily Climate Report.",
    "rules_secondary": "Settlement follows the market rules and corrections policy.",
}


def _call(
    monkeypatch,
    row,
    *,
    api_market=API_MARKET,
    request_url=REQUEST_URL,
    orderbook_url=ORDERBOOK_URL,
    orderbook=ORDERBOOK,
    quote=QUOTE,
    observed_at=OBSERVED_AT,
    audit_at=AUDIT_AT,
):
    monkeypatch.setattr(v3, "build_v2_shadow_row", lambda **_: row)
    return v3.build_exact_market_certified_shadow_row(
        series={},
        market={"ticker": MARKET_TICKER, "event_ticker": EVENT_TICKER},
        quote=quote,
        station={},
        forecast=None,
        exact_market_api_request_url=request_url,
        exact_market_api_market=api_market,
        exact_orderbook_api_request_url=orderbook_url,
        exact_orderbook_api_response=orderbook,
        orderbook_observed_at=observed_at,
        audit_observed_at=audit_at,
    )


def test_no_side_selection_rebinds_top_level_economics(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "best_shadow_side": {
            "side": "NO",
            "modeled_taker_fee_cents": "2",
            "modeled_after_fee_ev_cents": "18.5",
            "modeled_after_fee_edge_cents_per_contract": "3.7",
        },
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "4.0",
        "modeled_after_fee_edge_cents_per_contract": "0.8",
        "side_economics": [
            {
                "side": "YES",
                "modeled_taker_fee_cents": "1",
                "modeled_after_fee_ev_cents": "4.0",
                "modeled_after_fee_edge_cents_per_contract": "0.8",
            },
            {
                "side": "NO",
                "modeled_taker_fee_cents": "2",
                "modeled_after_fee_ev_cents": "18.5",
                "modeled_after_fee_edge_cents_per_contract": "3.7",
            },
        ],
        "blockers": [],
    }

    out = _call(monkeypatch, row)

    assert out["exact_market_contract_verified"] is True
    assert out["exact_quote_verified"] is True
    assert out["computed_quote_age_ms"] == 100
    assert out["selected_side_schema_verified"] is True
    assert out["best_shadow_side"] == "NO"
    assert out["modeled_taker_fee_cents"] == "2"
    assert out["modeled_after_fee_ev_cents"] == "18.5"
    assert out["modeled_after_fee_edge_cents_per_contract"] == "3.7"
    assert out["best_shadow_side_detail"]["side"] == "NO"
    assert out["settlement_certified_shadow"] is True
    assert out["ev_certified_shadow"] is True
    assert out["read_only"] is True
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_ambiguous_selected_side_fails_closed(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "best_shadow_side": "NO",
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "10",
        "side_economics": [
            {"side": "NO", "modeled_taker_fee_cents": "1", "modeled_after_fee_ev_cents": "10"},
            {"side": "NO", "modeled_taker_fee_cents": "1", "modeled_after_fee_ev_cents": "9"},
        ],
        "blockers": [],
    }

    out = _call(monkeypatch, row)

    assert out["exact_market_contract_verified"] is True
    assert out["exact_quote_verified"] is True
    assert out["selected_side_schema_verified"] is False
    # Side-economics ambiguity invalidates EV only; exact-contract settlement evidence remains valid.
    assert out["settlement_certified_shadow"] is True
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_taker_fee_cents"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert "SIDE_ECONOMICS_IDENTITY_UNVERIFIED" in out["blockers"]
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_missing_raw_exact_market_response_revokes_settlement_and_ev(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "best_shadow_side": {
            "side": "YES",
            "modeled_taker_fee_cents": "1",
            "modeled_after_fee_ev_cents": "8",
            "modeled_after_fee_edge_cents_per_contract": "4",
        },
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "8",
        "modeled_after_fee_edge_cents_per_contract": "4",
        "forecast_bracket_probability": "0.60",
        "side_economics": [
            {
                "side": "YES",
                "forecast_side_probability": "0.60",
                "modeled_taker_fee_cents": "1",
                "modeled_after_fee_ev_cents": "8",
                "modeled_after_fee_edge_cents_per_contract": "4",
            }
        ],
        "blockers": [],
    }

    out = _call(monkeypatch, row, api_market=None)

    assert out["exact_market_contract_verified"] is False
    assert out["exact_quote_verified"] is True
    assert out["settlement_certified_shadow"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert out["forecast_bracket_probability"] is None
    assert "EXACT_MARKET_API_RESPONSE_REQUIRED" in out["blockers"]
    assert "EXACT_MARKET_CONTRACT_AUDIT_REQUIRED" in out["blockers"]
    assert out["economics_status"] == "WITHHELD_EXACT_MARKET_CONTRACT_UNVERIFIED"
    assert out["read_only"] is True
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_sibling_market_response_fails_closed(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "best_shadow_side": None,
        "side_economics": [],
        "blockers": [],
    }
    sibling = dict(API_MARKET, ticker="KXHIGHAUS-26AUG28-B101")

    out = _call(monkeypatch, row, api_market=sibling)

    assert out["exact_market_contract_verified"] is False
    assert out["settlement_certified_shadow"] is False
    assert out["ev_certified_shadow"] is False
    assert "EXACT_MARKET_API_IDENTITY_MISMATCH" in out["blockers"]


def test_sibling_or_mismatched_orderbook_revokes_ev_but_not_settlement(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "best_shadow_side": "YES",
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "8",
        "side_economics": [{"side": "YES", "modeled_taker_fee_cents": "1", "modeled_after_fee_ev_cents": "8"}],
        "blockers": [],
    }
    sibling_url = "https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHAUS-26AUG28-B101/orderbook"

    out = _call(monkeypatch, row, orderbook_url=sibling_url)

    assert out["exact_market_contract_verified"] is True
    assert out["exact_quote_verified"] is False
    assert out["settlement_certified_shadow"] is True
    assert out["ev_certified_shadow"] is False
    assert out["modeled_after_fee_ev_cents"] is None
    assert "EXACT_MARKET_ORDERBOOK_IDENTITY_UNVERIFIED" in out["blockers"]
    assert "EXACT_MARKET_ORDERBOOK_AUDIT_REQUIRED" in out["blockers"]


def test_stale_or_depth_mismatched_orderbook_revokes_ev(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "best_shadow_side": "YES",
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "8",
        "side_economics": [{"side": "YES", "modeled_taker_fee_cents": "1", "modeled_after_fee_ev_cents": "8"}],
        "blockers": [],
    }
    bad_quote = dict(QUOTE, yes_ask_qty=8, quote_age_ms=3000)
    out = _call(monkeypatch, row, quote=bad_quote, audit_at="2026-08-28T14:00:03Z")

    assert out["settlement_certified_shadow"] is True
    assert out["exact_quote_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert "YES_EXECUTABLE_QUOTE_DEPTH_MISMATCH" in out["blockers"]
    assert "QUOTE_STALE" in out["blockers"]
