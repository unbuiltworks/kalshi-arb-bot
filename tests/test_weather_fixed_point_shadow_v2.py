from decimal import Decimal

from src.weather_fixed_point_shadow_v2 import build_fixed_point_research_economics_v2


def _quote():
    return {
        "market_ticker": "KXHIGHMIA-26AUG21-B92.5",
        "quote_observed_at": "2026-08-21T18:00:00Z",
        "no_ask_dollars": "0.3600",
        "no_ask_qty_fp": "1.00",
    }


def _fee_context():
    return {
        "context_verified": True,
        "market_ticker": "KXHIGHMIA-26AUG21-B92.5",
        "quote_observed_at": "2026-08-21T18:00:00Z",
        "evidence_source": "KALSHI_FILL_RECONCILIATION",
        "sides": {
            "NO": {
                "ask_dollars": "0.3600",
                "quantity_fp": "1.00",
                "net_fee_dollars": "0.0162",
            }
        },
    }


def test_bound_fee_context_can_emit_exact_shadow_economics():
    result = build_fixed_point_research_economics_v2(
        quote=_quote(),
        fee_type="quadratic",
        fee_multiplier=1,
        yes_probability="0.109",
        exact_fee_context=_fee_context(),
    )
    assert result["exact_fee_context_verified"] is True
    assert result["exact_after_fee_economics_verified"] is True
    assert result["economics_status"] == "EXACT_AFTER_FEE_SHADOW_ECONOMICS"
    row = result["book_sides"][0]
    assert Decimal(row["exact_after_fee_model_ev_dollars"]) == Decimal("0.5148")
    assert result["live_probe_ready"] is False


def test_unbound_exact_fee_falls_back_to_conservative_economics():
    bad = _fee_context()
    bad["market_ticker"] = "KXHIGHCHI-26AUG21-B80.5"
    result = build_fixed_point_research_economics_v2(
        quote=_quote(),
        fee_type="quadratic",
        fee_multiplier=1,
        yes_probability="0.109",
        exact_fee_context=bad,
    )
    assert result["exact_fee_context_verified"] is False
    assert result["exact_after_fee_economics_verified"] is False
    assert result["economics_status"] == "CONSERVATIVE_FEE_BOUNDED_SHADOW_ECONOMICS"
    row = result["book_sides"][0]
    assert row["exact_net_fee_dollars"] is None
    assert row["exact_after_fee_model_ev_dollars"] is None
    assert result["live_probe_ready"] is False
