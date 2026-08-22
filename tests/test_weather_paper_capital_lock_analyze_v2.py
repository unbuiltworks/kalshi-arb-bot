from decimal import Decimal

from src.cross_venue_ledger import make_event
from src.weather_certified_settlement_ledger import ledger_event_from_weather_reconciliation
from src.weather_paper_capital_lock_analyze_v2 import analyze_weather_paper_capital_lock_v2


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


def _reconciled():
    return {
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


def _certified_settlement():
    return ledger_event_from_weather_reconciliation(
        reconciliation=_reconciled(),
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        settlement_ts_ms=3_601_000,
    )


def test_self_verifying_identity_bound_settlement_can_realize_paper_capital_lock():
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), _certified_settlement()])
    assert result["verified_settlement_lock_groups"] == 1
    assert result["right_censored_groups"] == 0
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 0
    assert result["settlement_provenance_version"] == "SELF_VERIFYING_V4"


def test_legacy_free_form_prefix_is_quarantined_not_realized():
    legacy = make_event(
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="settlement",
        side="NO",
        payout=Decimal("1"),
        ts_ms=3_601_000,
        external_id="weather-certified-settlement:g1:anything",
    )
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), legacy])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["all_filled_groups_resolved"] is False
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 1
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "RIGHT_CENSORED_UNAUDITED_SETTLEMENT_EVENT"


def test_merely_well_shaped_random_v4_digest_is_quarantined():
    fake = make_event(
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
        external_id=(
            "weather-certified-settlement:v4:THE_WEATHER_COMPANY:"
            + "a" * 64
            + ":"
            + "b" * 64
        ),
    )
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), fake])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 1


def test_valid_v4_id_cannot_be_reused_for_different_trade_identity():
    certified = _certified_settlement()
    copied_to_other_identity = make_event(
        trade_group_id="g1-tampered",
        settlement_key=certified.settlement_key,
        category="weather",
        venue="kalshi",
        market_id=certified.market_id,
        event_type="settlement",
        side=certified.side,
        quantity=certified.quantity,
        payout=certified.payout,
        ts_ms=certified.ts_ms,
        external_id=certified.external_id,
    )
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), copied_to_other_identity])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 1


def test_valid_v4_id_cannot_be_replayed_with_different_terminal_payload():
    certified = _certified_settlement()
    tampered = make_event(
        trade_group_id=certified.trade_group_id,
        settlement_key=certified.settlement_key,
        category="weather",
        venue="kalshi",
        market_id=certified.market_id,
        event_type="settlement",
        side=certified.side,
        quantity=certified.quantity,
        payout=Decimal("0"),
        ts_ms=certified.ts_ms,
        external_id=certified.external_id,
    )
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), tampered])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 1


def test_valid_v4_id_cannot_be_replayed_with_different_settlement_timestamp():
    certified = _certified_settlement()
    tampered = make_event(
        trade_group_id=certified.trade_group_id,
        settlement_key=certified.settlement_key,
        category="weather",
        venue="kalshi",
        market_id=certified.market_id,
        event_type="settlement",
        side=certified.side,
        quantity=certified.quantity,
        payout=certified.payout,
        ts_ms=certified.ts_ms + 1,
        external_id=certified.external_id,
    )
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), tampered])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 1


def test_malformed_old_v3_digest_is_quarantined():
    malformed = make_event(
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="settlement",
        side="NO",
        payout=Decimal("1"),
        ts_ms=3_601_000,
        external_id="weather-certified-settlement:v3:not-a-sha256",
    )
    result = analyze_weather_paper_capital_lock_v2([_fill(), _fee(), malformed])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["legacy_or_unbound_settlement_events_quarantined"] == 1
