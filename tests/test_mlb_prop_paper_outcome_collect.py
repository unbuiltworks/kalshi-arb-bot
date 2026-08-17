from decimal import Decimal

from src.cross_venue_ledger import make_event
from src.mlb_prop_paper_outcome_collect import (
    collect_complete_outcomes,
    parse_kalshi_market,
    parse_polymarket_settlement,
    pending_probe_markets,
)

D = Decimal


def _fills(group="g1"):
    common = dict(
        trade_group_id=group,
        settlement_key="mlb|2026-08-17|player|1|poly|kalshi",
        category="mlb_prop_paper",
        event_type="fill",
        quantity=D("2"),
        ts_ms=1000,
    )
    return [
        make_event(**common, venue="polymarket_us", market_id="poly-slug", side="YES", price=D("0.40")),
        make_event(**common, venue="kalshi", market_id="KX-TEST", side="NO", price=D("0.50")),
    ]


def test_parse_polymarket_settlement_fail_closed():
    assert parse_polymarket_settlement({"settlement": 1}) == "YES"
    assert parse_polymarket_settlement({"settlement": "0.00"}) == "NO"
    assert parse_polymarket_settlement({"settlement": "0.5"}) is None
    assert parse_polymarket_settlement({}) is None


def test_parse_kalshi_requires_settled_status_and_binary_result():
    assert parse_kalshi_market({"market": {"status": "settled", "result": "yes"}})[0] == "YES"
    assert parse_kalshi_market({"market": {"status": "closed", "result": "yes"}})[0] is None
    assert parse_kalshi_market({"market": {"status": "settled", "result": ""}})[0] is None


def test_pending_requires_exactly_both_venues_and_skips_completed():
    fills = _fills()
    assert set(pending_probe_markets(fills, set())) == {"g1"}
    assert pending_probe_markets(fills[:1], set()) == {}
    assert pending_probe_markets(fills, {"g1"}) == {}


def test_collect_writes_only_when_both_venues_resolved_and_flags_disagreement():
    calls = []

    def getter(url):
        calls.append(url)
        if "polymarket.us" in url:
            return {"slug": "poly-slug", "settlement": "1.00"}
        return {"market": {"status": "settled", "result": "no"}}

    rows, stats = collect_complete_outcomes(_fills(), set(), get_json=getter)
    assert len(rows) == 1
    assert rows[0]["polymarket_us_outcome"] == "YES"
    assert rows[0]["kalshi_outcome"] == "NO"
    assert rows[0]["outcomes_agree"] is False
    assert stats["both_settled"] == 1
    assert stats["venue_disagreements"] == 1
    assert len(calls) == 2


def test_collect_does_not_emit_partial_settlement():
    def getter(url):
        if "polymarket.us" in url:
            return {"settlement": "1.00"}
        return {"market": {"status": "closed", "result": "yes"}}

    rows, stats = collect_complete_outcomes(_fills(), set(), get_json=getter)
    assert rows == []
    assert stats["both_settled"] == 0
    assert stats["one_or_both_unsettled"] == 1


def test_existing_partial_ledger_settlement_fails_closed():
    fills = _fills()
    fills.append(
        make_event(
            trade_group_id="g1",
            settlement_key="mlb|2026-08-17|player|1|poly|kalshi",
            category="mlb_prop_paper",
            venue="polymarket_us",
            market_id="poly-slug",
            event_type="settlement",
            payout=D("2"),
            ts_ms=2000,
        )
    )
    assert pending_probe_markets(fills, set()) == {}
