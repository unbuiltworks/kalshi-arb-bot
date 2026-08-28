import src.weather_settlement_certified_v3 as v3


MARKET_TICKER = "KXHIGHAUS-26AUG28-B100"
EVENT_TICKER = "KXHIGHAUS-26AUG28"
REQUEST_URL = f"https://external-api.kalshi.com/trade-api/v2/markets/{MARKET_TICKER}"
API_MARKET = {
    "ticker": MARKET_TICKER,
    "event_ticker": EVENT_TICKER,
    "strike_type": "between",
    "floor_strike": 99,
    "cap_strike": 100,
    "rules_primary": "Resolves from the specified Daily Climate Report.",
    "rules_secondary": "Settlement follows the market rules and corrections policy.",
}


def _call(monkeypatch, row, *, api_market=API_MARKET, request_url=REQUEST_URL):
    monkeypatch.setattr(v3, "build_v2_shadow_row", lambda **_: row)
    return v3.build_exact_market_certified_shadow_row(
        series={},
        market={"ticker": MARKET_TICKER, "event_ticker": EVENT_TICKER},
        quote={},
        station={},
        forecast=None,
        exact_market_api_request_url=request_url,
        exact_market_api_market=api_market,
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
        # Legacy v2 shape: full selected object, but stale YES top-level economics.
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
    assert out["selected_side_schema_verified"] is False
    assert out["settlement_certified_shadow"] is False
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