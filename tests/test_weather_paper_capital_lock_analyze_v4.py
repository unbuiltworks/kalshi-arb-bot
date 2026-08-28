from base64 import urlsafe_b64encode
from decimal import Decimal

from src.cross_venue_ledger import LedgerEvent, make_event
from src.weather_certified_settlement_ledger import (
    ledger_event_from_weather_reconciliation,
    parse_and_verify_weather_settlement_external_id,
)
from src.weather_paper_capital_lock_analyze_v4 import analyze_weather_paper_capital_lock_v4


def _fill():
    return make_event(trade_group_id="g1", settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather", venue="kalshi", market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="fill", side="NO", quantity=Decimal("1"), price=Decimal("0.36"),
        ts_ms=1_000, external_id="g1:fill")


def _fee():
    return make_event(trade_group_id="g1", settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather", venue="kalshi", market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="fee", side="NO", fee=Decimal("0.02"), ts_ms=1_000,
        external_id="g1:fee")


def _reconciled():
    return {"reconciled": True, "category": "weather", "classification": "PAPER",
        "market_ticker": "KXHIGHMIA-26AUG21-B92.5", "market_date": "2026-08-21",
        "side": "NO", "quantity": 1, "paper_cost_cents": "38",
        "paper_payout_cents": "100", "paper_realized_pnl_cents": "62",
        "settlement_outcome": "NO", "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
        "terminal_record_stage_verified": True, "terminal_record_stage": "FINAL",
        "read_only": True, "order_entry": False, "live_probe_ready": False}


def _settlement():
    return ledger_event_from_weather_reconciliation(reconciliation=_reconciled(),
        trade_group_id="g1", settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        market_id="KXHIGHMIA-26AUG21-B92.5", settlement_ts_ms=3_601_000)


def _copy(event, **overrides):
    values = dict(trade_group_id=event.trade_group_id, settlement_key=event.settlement_key,
        category=event.category, venue=event.venue, market_id=event.market_id,
        event_type=event.event_type, ts_ms=event.ts_ms, side=event.side,
        quantity=event.quantity, price=event.price, fee=event.fee, payout=event.payout,
        external_id=event.external_id)
    values.update(overrides)
    return LedgerEvent(**values)


def test_v6_verified_final_settlement_reaches_realized_paper_capital_lock():
    settlement = _settlement()
    provenance = parse_and_verify_weather_settlement_external_id(settlement)
    assert provenance == {
        "provider": "THE_WEATHER_COMPANY",
        "source_url": "https://weather.com/kalshi",
        "terminal_record_stage": "FINAL",
    }
    result = analyze_weather_paper_capital_lock_v4([_fill(), _fee(), settlement])
    assert result["verified_settlement_lock_groups"] == 1
    assert result["right_censored_groups"] == 0
    assert result["verified_v6_settlement_events"] == 1
    assert result["settlement_provenance_version"] == "SOURCE_FINAL_STAGE_AND_TERMINAL_PAYLOAD_BOUND_V6"
    assert result["verified_terminal_record_stages"] == {"FINAL": 1}


def test_v6_digest_cannot_be_replayed_onto_changed_payout_or_timestamp():
    settlement = _settlement()
    for tampered in (_copy(settlement, payout=Decimal("0")), _copy(settlement, ts_ms=settlement.ts_ms + 1)):
        assert parse_and_verify_weather_settlement_external_id(tampered) is None
        result = analyze_weather_paper_capital_lock_v4([_fill(), _fee(), tampered])
        assert result["verified_settlement_lock_groups"] == 0
        assert result["right_censored_groups"] == 1
        assert result["legacy_or_tampered_settlement_events_quarantined"] == 1


def test_v6_rejects_forged_non_authoritative_embedded_source_even_with_official_provider_token():
    settlement = _settlement()
    bad_source = urlsafe_b64encode(b"https://example.com/kalshi").decode("ascii").rstrip("=")
    forged = _copy(settlement, external_id=f"weather-certified-settlement:v6:THE_WEATHER_COMPANY:{bad_source}:FINAL:{'a'*64}")
    assert parse_and_verify_weather_settlement_external_id(forged) is None
    result = analyze_weather_paper_capital_lock_v4([_fill(), _fee(), forged])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1


def test_preliminary_stage_cannot_be_forged_into_v6_accounting():
    settlement = _settlement()
    source = urlsafe_b64encode(b"https://weather.com/kalshi").decode("ascii").rstrip("=")
    forged = _copy(settlement, external_id=f"weather-certified-settlement:v6:THE_WEATHER_COMPANY:{source}:PRELIMINARY:{'a'*64}")
    assert parse_and_verify_weather_settlement_external_id(forged) is None
    result = analyze_weather_paper_capital_lock_v4([_fill(), _fee(), forged])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1


def test_legacy_settlement_namespace_is_quarantined_by_v6_analyzer():
    settlement = _settlement()
    for legacy_id in (
        "weather-certified-settlement:v3:legacy",
        "weather-certified-settlement:v4:legacy",
        "weather-certified-settlement:v5:legacy",
    ):
        legacy = _copy(settlement, external_id=legacy_id)
        result = analyze_weather_paper_capital_lock_v4([_fill(), _fee(), legacy])
        assert result["verified_settlement_lock_groups"] == 0
        assert result["right_censored_groups"] == 1
