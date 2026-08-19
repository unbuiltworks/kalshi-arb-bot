from decimal import Decimal as D

import pytest

from src.sports_kgp_micro_guard import RuleApproval, build_micro_plan
from src.three_venue_pricing_core import PricedQuote
from src.universal_prediction_quant import LockOpportunity, MarketQuote


def test_gemini_discovery_only_depth_is_never_executable():
    now = 1_800_000_000_000
    key = "baseball|mlb|player_prop|game|player|john-doe|hits|GE|1"
    rows = []
    for venue, market, yes, no, source in (
        ("kalshi", "KX", "0.40", "0.60", "kalshi_public_orderbook_depth1_final"),
        ("gemini", "GEMI", "0.45", "0.55", "event_prices_q1_probe_only"),
    ):
        quote = MarketQuote(
            venue=venue, market_id=market + "#YES", settlement_key=key,
            category="sports_mlb_prop", yes_cost=D(yes), no_cost=D(no),
            yes_depth=D("1"), no_depth=D("1"), received_ms=now - 10,
            settlement_confidence="FULL_RULES_AUDITED_EXECUTABLE",
        )
        rows.append(PricedQuote(
            quote=quote, raw_yes_ask=D(yes), raw_no_ask=D(no),
            yes_fee=D("0"), no_fee=D("0"), depth_source=source, source_side="YES",
        ))
    lock = LockOpportunity(
        settlement_key=key, category="sports_mlb_prop",
        yes_venue="kalshi", yes_market_id="KX#YES",
        no_venue="gemini", no_market_id="GEMI#YES",
        unit_cost=D("0.95"), unit_profit=D("0.05"), max_quantity=D("1"),
        max_capital=D("0.95"), max_locked_profit=D("0.05"), quote_skew_ms=10,
        settlement_confidence="FULL_RULES_AUDITED_EXECUTABLE",
        expected_settlement_minutes=None, capital_velocity_score=D("1"),
    )
    approval = RuleApproval(
        key, "kalshi", "KX", "YES", "gemini", "GEMI", "YES", "a" * 64, True
    )
    with pytest.raises(PermissionError, match="gemini quote is not from an approved final execution book"):
        build_micro_plan(lock, rows, approval, now_ms=now)
