from decimal import Decimal

import pytest

from src.weather_certified_settlement_ledger import (
    PREFIX,
    certified_weather_settlement_external_id,
    ledger_event_from_weather_reconciliation,
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


def test_certified_weather_settlement_event_is_identity_bound_and_deterministic():
    kwargs = dict(
        reconciliation=_reconciled(),
        trade_group_id="weather:g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        settlement_ts_ms=2_000,
    )
    first = ledger_event_from_weather_reconciliation(**kwargs)
    second = ledger_event_from_weather_reconciliation(**kwargs)
    assert first == second
    assert first.external_id.startswith(PREFIX)
    assert first.payout == Decimal("1")
    assert first.quantity == Decimal("1")
    assert first.side == "NO"
    assert first.event_type == "settlement"
    assert first.category == "weather"


def _external_id(**overrides):
    kwargs = {
        "trade_group_id": "g1",
        "market_id": "KXHIGHMIA-26AUG21-B92.5",
        "settlement_key": "weather:KXHIGHMIA-26AUG21-B92.5",
        "provider": "THE_WEATHER_COMPANY",
        "source_url": "https://weather.com/kalshi",
        "side": "NO",
        "quantity": Decimal("1"),
        "payout": Decimal("1"),
        "settlement_ts_ms": 2_000,
    }
    kwargs.update(overrides)
    return certified_weather_settlement_external_id(**kwargs)


def test_external_id_changes_when_exact_provenance_changes():
    assert _external_id() != _external_id(
        provider="NWS",
        source_url="https://www.weather.gov/wrh/Climate?wfo=mfl",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("side", "YES"),
        ("quantity", Decimal("2")),
        ("payout", Decimal("0")),
        ("settlement_ts_ms", 2_001),
    ],
)
def test_external_id_binds_terminal_payload(field, value):
    assert _external_id() != _external_id(**{field: value})


@pytest.mark.parametrize(
    "provider,source_url",
    [
        ("THE_WEATHER_COMPANY", "https://weather.com/weather/today"),
        ("THE_WEATHER_COMPANY", "http://weather.com/kalshi"),
        ("THE_WEATHER_COMPANY", "https://weather.example.com/kalshi"),
        ("NWS", "https://example.com/nws"),
        ("UNAUDITED_PROVIDER", "https://weather.com/kalshi"),
    ],
)
def test_external_id_rejects_unofficial_or_mismatched_terminal_sources(provider, source_url):
    with pytest.raises(ValueError):
        _external_id(provider=provider, source_url=source_url)


@pytest.mark.parametrize(
    "reconciliation",
    [
        _reconciled(reconciled=False),
        _reconciled(order_entry=True),
        _reconciled(live_probe_ready=True),
        _reconciled(market_ticker="KXHIGHMIA-26AUG21-B90.5"),
        _reconciled(quantity="NaN"),
        _reconciled(quantity="1.5", paper_payout_cents="150"),
        _reconciled(paper_payout_cents="99"),
        _reconciled(settlement_source_provider=""),
        _reconciled(settlement_source_url=""),
        _reconciled(settlement_source_provider="UNAUDITED_PROVIDER", settlement_source_url="https://weather.com/kalshi"),
        _reconciled(settlement_source_provider="THE_WEATHER_COMPANY", settlement_source_url="https://example.com/kalshi"),
        _reconciled(settlement_source_provider="THE_WEATHER_COMPANY", settlement_source_url="https://weather.com/weather/today"),
    ],
)
def test_bad_or_incomplete_reconciliation_fails_closed(reconciliation):
    with pytest.raises(ValueError):
        ledger_event_from_weather_reconciliation(
            reconciliation=reconciliation,
            trade_group_id="weather:g1",
            settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
            market_id="KXHIGHMIA-26AUG21-B92.5",
            settlement_ts_ms=2_000,
        )


def test_stringly_reconciled_true_does_not_pass():
    with pytest.raises(ValueError):
        ledger_event_from_weather_reconciliation(
            reconciliation=_reconciled(reconciled="true"),
            trade_group_id="weather:g1",
            settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
            market_id="KXHIGHMIA-26AUG21-B92.5",
            settlement_ts_ms=2_000,
        )
