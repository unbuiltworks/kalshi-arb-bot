from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import sports_kp_mve_four_corner_seed as four
from src import sports_kp_mve_monotonicity_seed as seed
from src import sports_kp_nested_dutch_scan as nested

D = Decimal


def _sel(event, ticker, side, ask, fee="0.01"):
    return seed.Selected(
        event_ticker=event,
        market_ticker=ticker,
        side=side,
        ask=D(ask),
        depth=D("5"),
        fee=D(fee),
        close_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )


def test_nested_cover_math_guarantees_dollar_floor():
    low = nested.ThresholdMarket(
        "E", "E-TOTAL-7", "E|TOTAL", D("7.5"), "Over 7.5",
        _sel("E", "E-TOTAL-7", "yes", "0.40", "0.01"),
        _sel("E", "E-TOTAL-7", "no", "0.60", "0.01"),
    )
    high = nested.ThresholdMarket(
        "E", "E-TOTAL-8", "E|TOTAL", D("8.5"), "Over 8.5",
        _sel("E", "E-TOTAL-8", "yes", "0.30", "0.01"),
        _sel("E", "E-TOTAL-8", "no", "0.50", "0.01"),
    )
    out = nested.score_pair(low, high)
    assert out is not None
    assert D(out["all_in_cost"]) == D("0.92")
    assert D(out["locked_profit"]) == D("0.08")
    assert out["classification"] == "NESTED_SHADOW_EXECUTABLE"


def test_nested_rejects_reverse_or_different_family():
    a = nested.ThresholdMarket("E", "A", "E|A", D("2"), "2+", _sel("E", "A", "yes", "0.5"), _sel("E", "A", "no", "0.5"))
    b = nested.ThresholdMarket("E", "B", "E|B", D("3"), "3+", _sel("E", "B", "yes", "0.4"), _sel("E", "B", "no", "0.6"))
    assert nested.score_pair(a, b) is None
    assert nested.score_pair(b, a) is None


def test_family_key_groups_total_prop_and_spread_thresholds():
    assert nested._family_key("EV", "EV-9") == nested._family_key("EV", "EV-8")
    assert nested._family_key("EV", "EV-PLAYER27-1") == nested._family_key("EV", "EV-PLAYER27-2")
    assert nested._family_key("EV", "EV-NYY4") == nested._family_key("EV", "EV-NYY5")


def test_four_corner_partition_score():
    rows = []
    for corner, price in zip(("YY", "YN", "NY", "NN"), ("0.20", "0.21", "0.22", "0.23")):
        rows.append({"corner": corner, "best_yes": {"price": price, "fee": "0.01"}})
    out = four.score_partition(rows)
    assert out is not None
    assert D(out["all_in_partition_cost"]) == D("0.90")
    assert D(out["locked_profit"]) == D("0.10")
    assert out["classification"] == "FOUR_CORNER_SHADOW_EXECUTABLE"


def test_four_corner_requires_all_four_prices():
    assert four.score_partition([{"best_yes": {"price": "0.2", "fee": "0.01"}}] * 3) is None
