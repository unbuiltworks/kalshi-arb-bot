from decimal import Decimal

from src.cross_venue_ledger import make_event
from src.mlb_prop_paper_settlement import settlement_events


def _fill(*, venue, market_id, side, quantity="2", settlement_key="same-key", ts_ms=100):
    return make_event(
        trade_group_id="g1",
        settlement_key=settlement_key,
        category="mlb_prop_paper",
        venue=venue,
        market_id=market_id,
        event_type="fill",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal("0.45" if venue == "polymarket_us" else "0.51"),
        ts_ms=ts_ms,
    )


def _outcome():
    return [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "YES",
        "kalshi_outcome": "YES",
        "settled_ts_ms": 200,
    }]


def test_distinct_same_venue_fill_variants_fail_closed():
    events = [
        _fill(venue="polymarket_us", market_id="poly-1", side="YES"),
        _fill(venue="polymarket_us", market_id="poly-other", side="YES", ts_ms=101),
        _fill(venue="kalshi", market_id="kalshi-1", side="NO"),
    ]
    settlements, audits = settlement_events(events, _outcome())
    assert settlements == []
    assert audits[0]["status"] == "AMBIGUOUS_FILL_LEGS_FAIL_CLOSED"
    assert audits[0]["settlement_events_created"] == 0


def test_identical_replayed_fill_is_harmless():
    poly = _fill(venue="polymarket_us", market_id="poly-1", side="YES")
    events = [
        poly,
        poly,
        _fill(venue="kalshi", market_id="kalshi-1", side="NO"),
    ]
    settlements, audits = settlement_events(events, _outcome())
    assert len(settlements) == 2
    assert audits[0]["status"] == "SETTLED_FROM_OBSERVED_OUTCOMES"


def test_mismatched_settlement_keys_fail_closed():
    events = [
        _fill(venue="polymarket_us", market_id="poly-1", side="YES", settlement_key="key-a"),
        _fill(venue="kalshi", market_id="kalshi-1", side="NO", settlement_key="key-b"),
    ]
    settlements, audits = settlement_events(events, _outcome())
    assert settlements == []
    assert audits[0]["status"] == "MALFORMED_COMPLEMENTARY_FILL_GROUP_FAIL_CLOSED"
    assert audits[0]["same_settlement_key"] is False


def test_mismatched_quantities_fail_closed():
    events = [
        _fill(venue="polymarket_us", market_id="poly-1", side="YES", quantity="2"),
        _fill(venue="kalshi", market_id="kalshi-1", side="NO", quantity="1"),
    ]
    settlements, audits = settlement_events(events, _outcome())
    assert settlements == []
    assert audits[0]["status"] == "MALFORMED_COMPLEMENTARY_FILL_GROUP_FAIL_CLOSED"
    assert audits[0]["same_positive_quantity"] is False


def test_non_complementary_sides_fail_closed():
    events = [
        _fill(venue="polymarket_us", market_id="poly-1", side="YES"),
        _fill(venue="kalshi", market_id="kalshi-1", side="YES"),
    ]
    settlements, audits = settlement_events(events, _outcome())
    assert settlements == []
    assert audits[0]["status"] == "MALFORMED_COMPLEMENTARY_FILL_GROUP_FAIL_CLOSED"
    assert audits[0]["complementary_sides"] is False
