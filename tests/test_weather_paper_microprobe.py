from decimal import Decimal

from src.weather_paper_microprobe import (
    ledger_events_for_paper_probe,
    score_paper_probe_plans,
    select_paper_probe,
)


def _row(**overrides):
    ticker = "KXHIGHMIA-26AUG21-B92.5"
    market_date = "2026-08-21"
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "exact_market_source_verified": True,
        "exact_market_source_audit": {
            "verified": True,
            "market_ticker": ticker,
            "market_date": market_date,
            "settlement_source_provider": "THE_WEATHER_COMPANY",
            "settlement_source_url": "https://weather.com/kalshi",
        },
        "blockers": [],
        "market_ticker": ticker,
        "market_date": market_date,
        "quote_observed_at": "2026-08-21T11:55:00+00:00",
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "best_shadow_side": {
            "side": "NO",
            "ask_cents": 36,
            "available_contracts": 10,
            "forecast_side_probability": "0.891",
            "modeled_after_fee_ev_cents": "0",
            "modeled_after_fee_edge_cents_per_contract": "0",
        },
    }
    row.update(overrides)
    return row


def test_scores_one_and_five_dollar_alternative_plans_with_q_specific_fees():
    plans = score_paper_probe_plans(_row())
    assert [p["budget_cents"] for p in plans] == [100, 500]
    assert plans[0]["paper_cost_cents"] <= 100
    assert plans[1]["paper_cost_cents"] <= 500
    assert plans[0]["quantity"] < plans[1]["quantity"]
    assert Decimal(plans[0]["modeled_after_fee_edge_cents_per_contract"]) >= Decimal("3")
    assert Decimal(plans[1]["modeled_after_fee_edge_cents_per_contract"]) >= Decimal("3")


def test_selects_only_one_budget_tier_and_creates_stable_trade_group():
    first = select_paper_probe(_row())
    second = select_paper_probe(_row())
    assert first is not None
    assert first == second
    assert first["budget_cents"] == 500
    assert first["trade_group_id"].startswith("paper-weather-")
    assert first["settlement_key"] == "weather:KXHIGHMIA-26AUG21-B92.5"
    assert first["simultaneous_synthetic_fill"] is True
    assert first["live_probe_ready"] is False


def test_uncertified_or_stringly_certified_rows_never_create_probe():
    assert select_paper_probe(_row(ev_certified_shadow=False)) is None
    assert select_paper_probe(_row(ev_certified_shadow="true")) is None
    assert select_paper_probe(_row(settlement_certified_shadow="true")) is None
    assert select_paper_probe(_row(exact_market_source_verified="true")) is None
    assert select_paper_probe(_row(blockers=["TWC_SOURCE_ACCESS_REQUIRED"])) is None


def test_boolean_exact_source_flag_without_matching_exact_audit_cannot_create_probe():
    assert select_paper_probe(_row(exact_market_source_audit=None)) is None
    assert select_paper_probe(_row(exact_market_source_audit={
        "verified": True,
        "market_ticker": "KXHIGHMIA-26AUG20-B92.5",
        "market_date": "2026-08-20",
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
    })) is None


def test_minimum_three_cent_edge_is_enforced_after_q_specific_fee():
    weak = _row(best_shadow_side={
        "side": "YES",
        "ask_cents": 50,
        "available_contracts": 10,
        "forecast_side_probability": "0.53",
        "modeled_after_fee_ev_cents": "999",
        "modeled_after_fee_edge_cents_per_contract": "999",
    })
    assert score_paper_probe_plans(weak) == []
    assert select_paper_probe(weak) is None


def test_shared_ledger_events_are_fill_plus_separate_fee_and_remain_paper_only():
    plan = select_paper_probe(_row())
    assert plan is not None
    events = ledger_events_for_paper_probe(plan, ts_ms=123456789)
    assert [event.event_type for event in events] == ["fill", "fee"]
    fill, fee = events
    assert fill.trade_group_id == fee.trade_group_id == plan["trade_group_id"]
    assert fill.category == fee.category == "weather"
    assert fill.venue == fee.venue == "kalshi"
    assert fill.market_id == fee.market_id == "KXHIGHMIA-26AUG21-B92.5"
    assert fill.side == "NO"
    assert fill.quantity == Decimal(plan["quantity"])
    assert fill.price == Decimal("0.36")
    assert fee.fee == Decimal(plan["modeled_fee_cents"]) / Decimal(100)


def test_ledger_emission_fails_closed_if_plan_is_marked_live_ready():
    plan = select_paper_probe(_row())
    assert plan is not None
    plan["live_probe_ready"] = True
    assert ledger_events_for_paper_probe(plan, ts_ms=1) == []
