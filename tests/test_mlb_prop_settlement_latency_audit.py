from decimal import Decimal

from src.cross_venue_ledger import make_event
from src.mlb_prop_settlement_latency_audit import audit_settlement_latency

D = Decimal


def _event(group, venue, event_type, ts_ms, side=None, payout=None):
    kwargs = dict(
        trade_group_id=group,
        settlement_key="mlb|example",
        category="mlb_prop_paper",
        venue=venue,
        market_id=f"{venue}-market",
        event_type=event_type,
        ts_ms=ts_ms,
    )
    if event_type == "fill":
        kwargs.update(side=side, quantity=D("1"), price=D("0.45"))
    if event_type == "settlement":
        kwargs.update(payout=D(str(payout or "0")))
    return make_event(**kwargs)


def test_verified_latency_requires_both_positive_observed_times():
    events = [
        _event("g1", "polymarket_us", "fill", 1000, side="YES"),
        _event("g1", "kalshi", "fill", 1000, side="NO"),
        _event("g1", "polymarket_us", "settlement", 4000, payout="1"),
        _event("g1", "kalshi", "settlement", 5500, payout="0"),
    ]
    report = audit_settlement_latency(events)
    assert report["verified_latency_groups"] == 1
    row = report["groups"][0]
    assert row["polymarket_us_latency_ms"] == 3000
    assert row["kalshi_latency_ms"] == 4500
    assert row["max_latency_ms"] == 4500
    assert row["latency_verified"] is True


def test_fill_timestamp_fallback_is_quarantined_not_zero_latency():
    events = [
        _event("g1", "polymarket_us", "fill", 1000, side="YES"),
        _event("g1", "kalshi", "fill", 1000, side="NO"),
        _event("g1", "polymarket_us", "settlement", 1000, payout="1"),
        _event("g1", "kalshi", "settlement", 5000, payout="0"),
    ]
    report = audit_settlement_latency(events)
    assert report["verified_latency_groups"] == 0
    assert report["unverified_latency_groups"] == 1
    row = report["groups"][0]
    assert row["polymarket_us_latency_ms"] is None
    assert row["max_latency_ms"] is None
    assert row["latency_verified"] is False
    assert row["status"] == "MISSING_OR_NONPOSITIVE_SETTLEMENT_TIME_FAIL_CLOSED"


def test_non_simultaneous_synthetic_fills_fail_closed():
    events = [
        _event("g1", "polymarket_us", "fill", 1000, side="YES"),
        _event("g1", "kalshi", "fill", 1001, side="NO"),
        _event("g1", "polymarket_us", "settlement", 4000, payout="1"),
        _event("g1", "kalshi", "settlement", 5000, payout="0"),
    ]
    report = audit_settlement_latency(events)
    row = report["groups"][0]
    assert row["latency_verified"] is False
    assert row["status"] == "NON_SIMULTANEOUS_SYNTHETIC_FILL_FAIL_CLOSED"
