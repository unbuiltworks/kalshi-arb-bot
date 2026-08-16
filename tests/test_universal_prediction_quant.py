from decimal import Decimal as D

from src.universal_prediction_quant import (
    MarketQuote,
    best_lock_by_contract,
    find_cross_venue_locks,
    summarize,
)


def q(
    venue: str,
    *,
    yes: str,
    no: str,
    yes_depth: str = "100",
    no_depth: str = "100",
    ms: int = 1000,
    key: str = "btc-15m-123",
    category: str = "crypto",
    confidence: str = "FULL_RULES_AUDITED_SHADOW",
    settle: int | None = 5,
) -> MarketQuote:
    return MarketQuote(
        venue=venue,
        market_id=f"{venue}-{key}",
        settlement_key=key,
        category=category,
        yes_cost=D(yes),
        no_cost=D(no),
        yes_depth=D(yes_depth),
        no_depth=D(no_depth),
        received_ms=ms,
        settlement_confidence=confidence,
        expected_settlement_minutes=settle,
    )


def test_three_venues_selects_best_complementary_pair() -> None:
    rows = [
        q("gemini", yes="0.42", no="0.59", yes_depth="50", no_depth="40", ms=1000),
        q("kalshi", yes="0.48", no="0.53", yes_depth="80", no_depth="70", ms=1010),
        q("polymarket", yes="0.44", no="0.55", yes_depth="60", no_depth="90", ms=1005),
    ]
    opps = find_cross_venue_locks(rows, min_unit_profit=D("0.01"), max_quote_skew_ms=50)
    assert opps
    best = opps[0]
    assert best.yes_venue == "gemini"
    assert best.no_venue == "kalshi"
    assert best.unit_cost == D("0.95")
    assert best.unit_profit == D("0.05")
    assert best.max_quantity == D("50")
    assert best.max_locked_profit == D("2.50")


def test_never_combines_different_settlement_keys() -> None:
    rows = [
        q("gemini", yes="0.40", no="0.61", key="a"),
        q("kalshi", yes="0.60", no="0.40", key="b"),
    ]
    assert find_cross_venue_locks(rows) == []


def test_rejects_same_venue_complement() -> None:
    rows = [q("kalshi", yes="0.40", no="0.40")]
    assert find_cross_venue_locks(rows) == []


def test_confidence_gate_blocks_unreviewed_candidate() -> None:
    rows = [
        q("gemini", yes="0.40", no="0.61", confidence="NEEDS_RULE_AUDIT"),
        q("kalshi", yes="0.60", no="0.40", confidence="FULL_RULES_AUDITED_SHADOW"),
    ]
    assert find_cross_venue_locks(rows, min_confidence="FULL_RULES_AUDITED_SHADOW") == []


def test_quote_skew_gate() -> None:
    rows = [
        q("gemini", yes="0.40", no="0.61", ms=1000),
        q("kalshi", yes="0.60", no="0.40", ms=1100),
    ]
    assert find_cross_venue_locks(rows, max_quote_skew_ms=50) == []


def test_depth_caps_quantity() -> None:
    rows = [
        q("gemini", yes="0.40", no="0.61", yes_depth="7"),
        q("kalshi", yes="0.60", no="0.40", no_depth="3"),
    ]
    best = find_cross_venue_locks(rows)[0]
    assert best.max_quantity == D("3")
    assert best.max_capital == D("2.40")
    assert best.max_locked_profit == D("0.60")


def test_best_lock_returns_only_top_route_per_contract() -> None:
    rows = [
        q("gemini", yes="0.42", no="0.59"),
        q("kalshi", yes="0.48", no="0.53"),
        q("polymarket", yes="0.44", no="0.55"),
    ]
    best = best_lock_by_contract(rows)
    assert len(best) == 1
    assert best[0].yes_venue == "gemini"
    assert best[0].no_venue == "kalshi"


def test_summary_is_explicitly_read_only() -> None:
    rows = [
        q("gemini", yes="0.40", no="0.61"),
        q("kalshi", yes="0.60", no="0.40"),
    ]
    summary = summarize(find_cross_venue_locks(rows))
    assert summary["read_only"] is True
    assert summary["order_entry"] is False
    assert summary["opportunity_count"] == 1
