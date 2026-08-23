from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import sports_kp_mve_monotonicity_seed as s

D = Decimal


def _sel(event, market, side, ask):
    return s.Selected(
        event_ticker=event,
        market_ticker=market,
        side=side,
        ask=D(ask),
        depth=D("5"),
        fee=s.g._fee(D(ask)),
        close_time=datetime.now(timezone.utc) + timedelta(hours=3),
    )


def test_rank_pairs_prefers_near_certain_booster_and_distinct_event():
    anchor = _sel("E1", "A", "yes", "0.60")
    good = _sel("E2", "B", "no", "0.99")
    weaker = _sel("E3", "C", "yes", "0.95")
    same_event = _sel("E1", "D", "yes", "0.99")
    pairs = s._rank_pairs(
        {"is_all_yes": False, "is_single_market_per_event": True},
        [anchor, good, weaker, same_event],
        limit=4,
    )
    assert pairs
    assert pairs[0] == (anchor, good)
    assert all(a.event_ticker != b.event_ticker for a, b in pairs)


def test_best_rfq_sides_uses_highest_bid_on_each_side():
    out = s._best_rfq_sides([
        {"status": "open", "yes_bid_dollars": "0.62", "no_bid_dollars": "0.30"},
        {"status": "open", "yes_bid_dollars": "0.67", "no_bid_dollars": "0.28"},
        {"status": "closed", "yes_bid_dollars": "0.99", "no_bid_dollars": "0.99"},
    ])
    assert out["valid_quotes"] == 2
    assert out["best_yes_bid"] == D("0.67")
    assert out["best_no_bid"] == D("0.30")


def test_minimal_no_score_is_shadow_only_math():
    # Anchor all-in = .61; combo NO from .97 YES bid = .03 + .01 fee.
    # Total .65 => 35c locked in this synthetic unit fixture.
    anchor = _sel("E1", "A", "yes", "0.60")
    out = s._score_minimal_no(anchor, D("0.97"))
    assert out is not None
    assert out["combo_no_equivalent"] == "0.03"
    assert D(out["locked_profit_conservative"]) == D("0.35")
    assert out["classification"] == "SHADOW_EXECUTABLE_MONOTONICITY"


def test_no_yes_bid_means_no_buy_no_price():
    anchor = _sel("E1", "A", "yes", "0.60")
    assert s._score_minimal_no(anchor, None) is None
