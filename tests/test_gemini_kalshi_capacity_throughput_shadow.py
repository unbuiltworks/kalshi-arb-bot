from decimal import Decimal

from src import gemini_kalshi_capacity_throughput_shadow as t

D = Decimal


def reset():
    t._WINDOWS_SEEN.clear()
    for rows in t._FIRST_SUPPORTED.values():
        rows.clear()


def checkpoint(*, ticker="KXETH15M-W1", direction="KALSHI_YES_GEMINI_NO", q5=True, q10=True, q25=False, p5="0.15", p10="0.31", p25="0.80"):
    def row(q, supported, profit):
        return {
            "quantity": q,
            "supported": supported,
            "cushioned_profit": profit,
            "cushioned_profit_per_contract": str(D(profit) / D(q)),
            "total_capital": str(D(q) - D(profit)),
        }
    return {
        "asset": "ETH",
        "kalshi_ticker": ticker,
        "direction": direction,
        "opened_ms": 1000,
        "sizes": [row("5", q5, p5), row("10", q10, p10), row("25", q25, p25)],
    }


def test_repeated_checkpoints_and_direction_flips_count_once_per_asset_window():
    reset()
    first = t._consume_checkpoint(checkpoint())
    second = t._consume_checkpoint(checkpoint(direction="GEMINI_YES_KALSHI_NO", p10="0.90"))
    assert first["newly_recorded_quantities"] == ["5", "10"]
    assert second["newly_recorded_quantities"] == []
    assert len(t._FIRST_SUPPORTED["10"]) == 1
    # First supported opportunity is frozen; later/hindsight-better price is ignored.
    assert t._sum_profit("10") == D("0.31")


def test_new_market_window_can_add_one_new_opportunity():
    reset()
    t._consume_checkpoint(checkpoint(ticker="KXETH15M-W1"))
    t._consume_checkpoint(checkpoint(ticker="KXETH15M-W2", p10="0.40"))
    assert len(t._WINDOWS_SEEN) == 2
    assert len(t._FIRST_SUPPORTED["10"]) == 2
    assert t._sum_profit("10") == D("0.71")


def test_q25_is_not_inferred_from_q10_support():
    reset()
    t._consume_checkpoint(checkpoint(q10=True, q25=False))
    assert len(t._FIRST_SUPPORTED["10"]) == 1
    assert len(t._FIRST_SUPPORTED["25"]) == 0
    assert t._sum_profit("25") == D("0")


def test_daily_projection_is_withheld_before_one_hour(monkeypatch):
    reset()
    t._consume_checkpoint(checkpoint())
    monkeypatch.setattr(t, "_START_MONO", 1000.0)
    early = t._summary(now_mono=1900.0)
    assert early["daily_projection_ready"] is False
    assert early["q10_shadow_modeled_daily_projection"] is None
    mature = t._summary(now_mono=4600.0)
    assert mature["daily_projection_ready"] is True
    # 31c in one hour -> $7.44/day modeled, explicitly not realized P&L.
    assert D(mature["q10_shadow_modeled_daily_projection"]) == D("7.44")
