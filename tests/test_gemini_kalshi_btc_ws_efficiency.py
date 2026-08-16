from datetime import datetime, timezone
from decimal import Decimal

from src.gemini_kalshi_btc_ws_analyze import episodes
from src.gemini_kalshi_btc_ws_efficiency import efficiency_summary
from src.gemini_kalshi_settlement_agreement import AgreementRow


def _row(ts: int, *, positive: bool = True):
    candidates = []
    if positive:
        candidates = [{
            "direction": "KALSHI_YES_GEMINI_NO",
            "quantity": "10",
            "net_cost": "9.50",
            "fees": "0.10",
            "locked_profit": "0.50",
            "roi": str(Decimal("0.50") / Decimal("9.50")),
            "quote_skew_ms": 5,
        }]
    return {
        "asset": "BTC",
        "window_end_utc": "2026-08-15T12:00:00+00:00",
        "gemini_symbol": "GEMI-BTC15M-W1-UP",
        "kalshi_ticker": "KXBTC15M-W1",
        "receive_ts_ms": ts,
        "both_taker_fee_candidates": candidates,
        "read_only": True,
        "order_entry": False,
    }


def _agreement(*, agrees: bool = True, margin_bps: float | None = 2.0) -> AgreementRow:
    return AgreementRow(
        asset="BTC",
        expiry=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        gemini_ticker="GEMI-BTC15M-W1-UP",
        kalshi_ticker="KXBTC15M-W1",
        gemini_outcome="yes",
        kalshi_outcome="yes" if agrees else "no",
        agrees=agrees,
        gemini_strike=100.0,
        gemini_settlement=100.01,
        gemini_margin_bps=margin_bps,
    )


def test_efficiency_uses_settlement_adjusted_pnl_over_same_capital_hours():
    eps = episodes([_row(1_000), _row(1_060), _row(1_120, positive=False)])
    result = efficiency_summary(eps, [_agreement()])
    entry = result["basis_margin_efficiency"]["1"]["entry"]
    pnl = Decimal(entry["settlement_adjusted_pnl_dollars"])
    hours = Decimal(entry["consumable_capital_hours"])
    assert pnl == Decimal("0.50")
    assert hours > 0
    assert Decimal(entry["settlement_adjusted_profit_per_capital_hour"]) == pnl / hours
    assert entry["consumable_contracts"] == "10"


def test_adverse_mismatch_flows_through_efficiency_numerator():
    eps = episodes([_row(1_000), _row(1_060), _row(1_120, positive=False)])
    result = efficiency_summary(eps, [_agreement(agrees=False)])
    entry = result["basis_margin_efficiency"]["1"]["entry"]
    assert Decimal(entry["settlement_adjusted_pnl_dollars"]) == Decimal("-9.50")
    assert Decimal(entry["settlement_adjusted_profit_per_capital_hour"]) < 0
    assert entry["consumable_mismatch_episodes"] == 1


def test_margin_filter_excludes_near_boundary_episode_from_both_numerator_and_denominator():
    eps = episodes([_row(1_000), _row(1_060), _row(1_120, positive=False)])
    result = efficiency_summary(eps, [_agreement(margin_bps=0.2772)])
    inside = result["basis_margin_efficiency"]["0.25"]["entry"]
    excluded = result["basis_margin_efficiency"]["0.5"]["entry"]
    assert inside["consumable_contracts"] == "10"
    assert excluded["consumable_contracts"] == "0"
    assert Decimal(excluded["consumable_capital_hours"]) == 0
    assert excluded["settlement_adjusted_profit_per_capital_hour"] is None


def test_settlement_delay_only_reduces_efficiency_via_longer_capital_lock():
    eps = episodes([_row(1_000), _row(1_060), _row(1_120, positive=False)])
    result = efficiency_summary(
        eps,
        [_agreement()],
        {"p50": Decimal("60"), "p95": Decimal("120")},
    )
    base = result["basis_margin_efficiency"]["1"]["entry"]
    p50 = result["settlement_delay_adjusted_basis_margin_efficiency"]["p50"]["1"]["entry"]
    p95 = result["settlement_delay_adjusted_basis_margin_efficiency"]["p95"]["1"]["entry"]
    assert base["settlement_adjusted_pnl_dollars"] == p50["settlement_adjusted_pnl_dollars"] == p95["settlement_adjusted_pnl_dollars"]
    assert Decimal(base["consumable_capital_hours"]) < Decimal(p50["consumable_capital_hours"]) < Decimal(p95["consumable_capital_hours"])
    assert Decimal(base["settlement_adjusted_profit_per_capital_hour"]) > Decimal(p50["settlement_adjusted_profit_per_capital_hour"]) > Decimal(p95["settlement_adjusted_profit_per_capital_hour"])
    assert result["read_only"] is True
    assert result["order_entry"] is False
