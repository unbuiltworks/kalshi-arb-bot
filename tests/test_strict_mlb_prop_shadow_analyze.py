from decimal import Decimal

from src.strict_mlb_prop_shadow_analyze import analyze


def row(ts, *, qty, profit, capital, fp="a", positive=True):
    return {
        "ts_ms": ts,
        "pair_key": "hits|poly|kalshi",
        "family": "baseball_player_hits",
        "direction": "POLY_YES_KALSHI_NO",
        "quantity": str(qty),
        "locked_profit_dollars": str(profit if positive else 0),
        "capital_dollars": str(capital if positive else 0),
        "poly_quote_age_ms": 12,
        "kalshi_quote_age_ms": 8,
        "quote_skew_ms": 4,
        "price_fingerprint": fp,
    }


def test_repeated_resting_depth_is_not_double_counted():
    result = analyze([
        row(1000, qty=10, profit=1, capital=9),
        row(1100, qty=10, profit=1, capital=9),
        row(1200, qty=12, profit=Decimal("1.2"), capital=Decimal("10.8")),
        row(1300, qty=0, profit=0, capital=0, positive=False),
    ])
    assert Decimal(result["theoretical_sample_sum_profit_dollars"]) == Decimal("3.2")
    assert Decimal(result["consumable_shadow_profit_dollars"]) == Decimal("1.2")
    assert Decimal(result["consumable_shadow_capital_dollars"]) == Decimal("10.8")
    assert result["unique_episodes"] == 1


def test_price_change_resets_consumable_level():
    result = analyze([
        row(1000, qty=10, profit=1, capital=9, fp="a"),
        row(1100, qty=10, profit=2, capital=8, fp="b"),
    ])
    assert Decimal(result["consumable_shadow_profit_dollars"]) == Decimal("3")
    assert result["unique_episodes"] == 1


def test_close_then_reopen_creates_new_episode_and_resets_depth():
    result = analyze([
        row(1000, qty=5, profit=Decimal("0.5"), capital=Decimal("4.5")),
        row(1100, qty=0, profit=0, capital=0, positive=False),
        row(1200, qty=5, profit=Decimal("0.5"), capital=Decimal("4.5")),
    ])
    assert result["unique_episodes"] == 2
    assert Decimal(result["consumable_shadow_profit_dollars"]) == Decimal("1.0")


def test_reports_quote_age_skew_and_family_rollup():
    result = analyze([row(1000, qty=2, profit=Decimal("0.2"), capital=Decimal("1.8"))])
    ep = result["episodes"][0]
    assert ep["max_quote_age_ms"] == 12
    assert ep["max_quote_skew_ms"] == 4
    fam = result["by_family"]["baseball_player_hits"]
    assert fam["episodes"] == 1
    assert Decimal(fam["consumable_profit_dollars"]) == Decimal("0.2")
    assert result["read_only"] is True
    assert result["order_entry"] is False
