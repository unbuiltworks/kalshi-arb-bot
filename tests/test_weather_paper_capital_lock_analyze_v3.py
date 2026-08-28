from decimal import Decimal

import pytest

from src.cross_venue_ledger import LedgerEvent, make_event
from src.weather_certified_settlement_ledger_v3 import (
    ledger_event_from_weather_reconciliation_v3,
    parse_and_verify_weather_settlement_external_id_v3,
)
from src.weather_paper_capital_lock_analyze_v3 import analyze_weather_paper_capital_lock_v3


def _fill():
    return make_event(
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="fill",
        side="NO",
        quantity=Decimal("1"),
        price=Decimal("0.36"),
        ts_ms=1_000,
        external_id="g1:fill",
    )


def _fee():
    return make_event(
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="fee",
        side="NO",
        fee=Decimal("0.02"),
        ts_ms=1_000,
        external_id="g1:fee",
    )


def _reconciled(**overrides):
    row = {
        "reconciled": True,
        "category": "weather",
        "classification": "PAPER",
        "market_ticker": "KXHIGHMIA-26AUG21-B92.5",
        "market_date": "2026-08-21",
        "side": "NO",
        "quantity": 1,
        "paper_cost_cents": "38",
        "paper_payout_cents": "100",
        "paper_realized_pnl_cents": "62",
        "settlement_outcome": "NO",
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
    row.update(overrides)
    return row


def _settlement():
    return ledger_event_from_weather_reconciliation_v3(
        reconciliation=_reconciled(),
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        settlement_ts_ms=3_601_000,
    )


def test_v3_recomputable_provenance_can_realize_paper_capital_lock():
    settlement = _settlement()
    assert parse_and_verify_weather_settlement_external_id_v3(settlement) == {
        "provider": "THE_WEATHER_COMPANY",
        "source_url": "https://weather.com/kalshi",
    }
    result = analyze_weather_paper_capital_lock_v3([_fill(), _fee(), settlement])
    assert result["verified_settlement_lock_groups"] == 1
    assert result["right_censored_groups"] == 0
    assert result["verified_v3_settlement_events"] == 1
    assert result["legacy_unbound_or_tampered_settlement_events_quarantined"] == 0
    assert result["settlement_provenance_version"] == "RECOMPUTABLE_IDENTITY_BOUND_V3"


def test_digest_shaped_but_tampered_event_identity_is_quarantined():
    settlement = _settlement()
    tampered = LedgerEvent(
        trade_group_id=settlement.trade_group_id,
        settlement_key=settlement.settlement_key,
        category=settlement.category,
        venue=settlement.venue,
        market_id="KXHIGHMIA-26AUG21-B90.5",
        event_type=settlement.event_type,
        ts_ms=settlement.ts_ms,
        side=settlement.side,
        quantity=settlement.quantity,
        price=settlement.price,
        fee=settlement.fee,
        payout=settlement.payout,
        external_id=settlement.external_id,
    )
    assert parse_and_verify_weather_settlement_external_id_v3(tampered) is None
    result = analyze_weather_paper_capital_lock_v3([_fill(), _fee(), tampered])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["legacy_unbound_or_tampered_settlement_events_quarantined"] == 1


def test_v2_digest_shape_is_not_accepted_by_v3():
    fake_v2 = make_event(
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="settlement",
        side="NO",
        quantity=Decimal("1"),
        payout=Decimal("1"),
        ts_ms=3_601_000,
        external_id="weather-certified-settlement:v2:" + ("a" * 64),
    )
    result = analyze_weather_paper_capital_lock_v3([_fill(), _fee(), fake_v2])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["legacy_unbound_or_tampered_settlement_events_quarantined"] == 1


def test_unofficial_source_cannot_generate_v3_certified_event():
    with pytest.raises(ValueError):
        ledger_event_from_weather_reconciliation_v3(
            reconciliation=_reconciled(settlement_source_url="https://example.com/kalshi"),
            trade_group_id="g1",
            settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
            market_id="KXHIGHMIA-26AUG21-B92.5",
            settlement_ts_ms=3_601_000,
        )


def test_nws_cli_source_is_supported_for_historical_regime():
    event = ledger_event_from_weather_reconciliation_v3(
        reconciliation=_reconciled(
            settlement_source_provider="NWS",
            settlement_source_url="https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=MIA",
        ),
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        settlement_ts_ms=3_601_000,
    )
    assert parse_and_verify_weather_settlement_external_id_v3(event)["provider"] == "NWS"


def test_generic_nws_page_is_not_supported_for_historical_regime():
    with pytest.raises(ValueError):
        ledger_event_from_weather_reconciliation_v3(
            reconciliation=_reconciled(
                settlement_source_provider="NWS",
                settlement_source_url="https://www.weather.gov/wrh/Climate?wfo=mfl",
            ),
            trade_group_id="g1",
            settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
            market_id="KXHIGHMIA-26AUG21-B92.5",
            settlement_ts_ms=3_601_000,
        )
