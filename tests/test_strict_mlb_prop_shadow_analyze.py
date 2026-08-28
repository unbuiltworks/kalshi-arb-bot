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


def test_sparse_samples_cannot_claim_subsecond_survival():
    result = analyze([
        row(1000, qty=10, profit=1, capital=9),
        row(2000, qty=10, profit=1, capital=9),
        row(3000, qty=0, profit=0, capital=0, positive=False),
    ])
    ep = result["episodes"][0]
    assert ep["survival"]["50ms"] == "UNRESOLVED"
    assert ep["survival"]["100ms"] == "UNRESOLVED"
    assert ep["survival"]["250ms"] == "UNRESOLVED"
    assert ep["survival"]["500ms"] == "UNRESOLVED"
    assert ep["max_positive_sample_gap_ms"] == 1000


def test_dense_samples_can_support_survival_thresholds():
    result = analyze([
        row(1000, qty=10, profit=1, capital=9),
        row(1050, qty=10, profit=1, capital=9),
        row(1100, qty=10, profit=1, capital=9),
        row(1150, qty=10, profit=1, capital=9),
        row(1200, qty=10, profit=1, capital=9),
        row(1250, qty=10, profit=1, capital=9),
        row(1300, qty=10, profit=1, capital=9),
        row(1350, qty=10, profit=1, capital=9),
        row(1400, qty=10, profit=1, capital=9),
        row(1450, qty=10, profit=1, capital=9),
        row(1500, qty=10, profit=1, capital=9),
        row(1550, qty=0, profit=0, capital=0, positive=False),
    ])
    ep = result["episodes"][0]
    assert ep["survival"]["50ms"] == "PASSED"
    assert ep["survival"]["100ms"] == "PASSED"
    assert ep["survival"]["250ms"] == "PASSED"
    assert ep["survival"]["500ms"] == "PASSED"


def test_early_close_fails_longer_survival_and_reports_capital_hours():
    result = analyze([
        row(1000, qty=10, profit=1, capital=9),
        row(1050, qty=10, profit=1, capital=9),
        row(1080, qty=0, profit=0, capital=0, positive=False),
    ])
    ep = result["episodes"][0]
    assert ep["survival"]["50ms"] == "PASSED"
    assert ep["survival"]["100ms"] == "FAILED"
    assert ep["survival"]["250ms"] == "FAILED"
    assert ep["survival"]["500ms"] == "FAILED"
    assert Decimal(ep["capital_hours"]) == Decimal("9") * Decimal("50") / Decimal("3600000")
    assert Decimal(result["capital_hours"]) == Decimal(ep["capital_hours"])
