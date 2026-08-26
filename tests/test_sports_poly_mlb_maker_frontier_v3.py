from decimal import Decimal as D

from src.sports_poly_direct_mlb_campaign_q1_v3 import (
    TARGET,
    _exact_locked,
    _highest_safe_post_only_quote,
)


def test_exact_frontier_can_create_price_far_from_touch() -> None:
    # Hedge fee rounds to $0 at 10c, so q1 can safely bid 87c and still lock 3c.
    quote = _highest_safe_post_only_quote(maker_ask=D("0.95"), hedge_ask=D("0.10"), tick=D("0.01"))
    assert quote == D("0.87")
    assert _exact_locked(quote, D("0.10")) == TARGET


def test_post_only_cap_wins_when_ask_is_tighter_than_economic_frontier() -> None:
    quote = _highest_safe_post_only_quote(maker_ask=D("0.82"), hedge_ask=D("0.10"), tick=D("0.01"))
    assert quote == D("0.81")
    assert quote < D("0.82")
    assert _exact_locked(quote, D("0.10")) > TARGET


def test_exact_taker_fee_rounding_reduces_frontier_when_fee_is_one_cent() -> None:
    # At an 18c taker hedge the current q1 taker fee rounds to 1c.
    quote = _highest_safe_post_only_quote(maker_ask=D("0.95"), hedge_ask=D("0.18"), tick=D("0.01"))
    assert quote == D("0.78")
    assert _exact_locked(quote, D("0.18")) == TARGET
    assert _exact_locked(D("0.79"), D("0.18")) < TARGET


def test_no_safe_quote_when_hedge_consumes_the_payout() -> None:
    assert _highest_safe_post_only_quote(maker_ask=D("0.50"), hedge_ask=D("0.98"), tick=D("0.01")) is None


def test_subcent_tick_is_respected() -> None:
    quote = _highest_safe_post_only_quote(maker_ask=D("0.905"), hedge_ask=D("0.10"), tick=D("0.005"))
    assert quote == D("0.870")
    assert quote < D("0.905")
    assert _exact_locked(quote, D("0.10")) >= TARGET
