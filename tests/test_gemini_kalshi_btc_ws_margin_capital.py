from datetime import datetime, timezone
from decimal import Decimal

from src.gemini_kalshi_btc_ws_analyze import episodes
from src.gemini_kalshi_btc_ws_margin_capital import margin_capital_economics
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


def _agreement(margin_bps: float | None) -> AgreementRow:
    return AgreementRow(
        asset="BTC",
        expiry=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        gemini_ticker="GEMI-BTC15M-W1-UP",
        kalshi_ticker="KXBTC15M-W1",
        gemini_outcome="yes",
        kalshi_outcome="yes",
        agrees=True,
        gemini_strike=100.0,
        gemini_settlement=100.01,
        gemini_margin_bps=margin_bps,
    )


def test_margin_cohort_capital_matches_exact_filtered_contracts():
    rows = [_row(1_000), _row(1_060), _row(1_120, positive=False)]
    result = margin_capital_economics(episodes(rows), [_agreement(0.2772)])

    inside = result["basis_margin_capital"]["0.25"]["entry"]
    assert inside["consumable_contracts"] == "10"
    assert Decimal(inside["consumable_capital_deployed_dollars"]) == Decimal("9.50")
    assert Decimal(inside["consumable_capital_hours"]) > 0

    excluded = result["basis_margin_capital"]["0.5"]["entry"]
    assert excluded["consumable_contracts"] == "0"
    assert excluded["excluded_inside_margin_candidates"] == 1
    assert Decimal(excluded["consumable_capital_hours"]) == 0


def test_unknown_margin_fails_closed_in_all_margin_cohorts():
    rows = [_row(1_000), _row(1_060)]
    result = margin_capital_economics(episodes(rows), [_agreement(None)])
    cohort = result["basis_margin_capital"]["0"]["entry"]
    assert cohort["consumable_contracts"] == "0"
    assert cohort["excluded_unknown_gemini_margin_candidates"] == 1


def test_settlement_delay_increases_same_cohort_capital_hours_without_changing_contracts():
    rows = [_row(1_000), _row(1_060), _row(1_120, positive=False)]
    result = margin_capital_economics(
        episodes(rows),
        [_agreement(2.0)],
        {"p50": Decimal("60"), "p95": Decimal("120")},
    )
    base = result["basis_margin_capital"]["1"]["entry"]
    p50 = result["settlement_delay_adjusted_basis_margin_capital"]["p50"]["1"]["entry"]
    p95 = result["settlement_delay_adjusted_basis_margin_capital"]["p95"]["1"]["entry"]
    assert base["consumable_contracts"] == p50["consumable_contracts"] == p95["consumable_contracts"] == "10"
    assert Decimal(base["consumable_capital_hours"]) < Decimal(p50["consumable_capital_hours"])
    assert Decimal(p50["consumable_capital_hours"]) < Decimal(p95["consumable_capital_hours"])


def test_survival_cohort_uses_same_margin_filter():
    rows = [_row(1_000), _row(1_060), _row(1_120, positive=False)]
    result = margin_capital_economics(episodes(rows), [_agreement(0.2772)])
    assert result["basis_margin_capital"]["0.25"]["survival"]["50"]["consumable_contracts"] == "10"
    assert result["basis_margin_capital"]["0.5"]["survival"]["50"]["consumable_contracts"] == "0"
    assert result["read_only"] is True
    assert result["order_entry"] is False
