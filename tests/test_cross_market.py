from decimal import Decimal

from src.cross_market import (
    KalshiNoQuote,
    SportsbookOverQuote,
    american_to_decimal,
    kalshi_taker_fee_cents,
    optimize_adjacent_total,
)


def test_american_odds():
    assert american_to_decimal(-120) == Decimal("1.833333333333333333333333333")
    assert american_to_decimal(150) == Decimal("2.5")


def test_fee_supports_fractional_contracts():
    fee = kalshi_taker_fee_cents(10000, 36)
    assert fee == 162


def test_screenshot_shape_finds_guaranteed_profit_and_middle():
    quotes = [
        SportsbookOverQuote(
            book="Caesars",
            event_key="GSW-MEM",
            line=Decimal("183.5"),
            american_odds=-120,
            max_stake_cents=30000,
        ),
        SportsbookOverQuote(
            book="Caesars",
            event_key="GSW-MEM",
            line=Decimal("184.5"),
            american_odds=-120,
            max_stake_cents=24000,
        ),
    ]
    kalshi = KalshiNoQuote(
        ticker="KXNBATOTAL-GSWMEM-184",
        event_key="GSW-MEM",
        line=Decimal("184.5"),
        no_price_cents=36,
        no_depth_hundredths=100000,
    )

    plan = optimize_adjacent_total(quotes, kalshi, bankroll_cents=100000)
    assert plan is not None
    assert plan.guaranteed_profit_cents > 0
    assert plan.middle_profit_cents > plan.guaranteed_profit_cents
    assert plan.middle_score == Decimal("184.0")
    assert sum(x.stake_cents for x in plan.sportsbook_legs) <= 54000


def test_rejects_mismatched_event():
    q = SportsbookOverQuote(
        book="Caesars",
        event_key="A",
        line=Decimal("183.5"),
        american_odds=-120,
        max_stake_cents=30000,
    )
    k = KalshiNoQuote(
        ticker="X",
        event_key="B",
        line=Decimal("184.5"),
        no_price_cents=36,
        no_depth_hundredths=10000,
    )
    assert optimize_adjacent_total([q], k, bankroll_cents=100000) is None


def test_stale_quotes_are_rejected():
    q = SportsbookOverQuote(
        book="Caesars",
        event_key="A",
        line=Decimal("183.5"),
        american_odds=-120,
        max_stake_cents=30000,
        observed_ms=1000,
    )
    k = KalshiNoQuote(
        ticker="X",
        event_key="A",
        line=Decimal("184.5"),
        no_price_cents=36,
        no_depth_hundredths=10000,
        observed_ms=1000,
    )
    assert optimize_adjacent_total(
        [q],
        k,
        bankroll_cents=100000,
        max_quote_age_ms=500,
        now_ms=2000,
    ) is None
