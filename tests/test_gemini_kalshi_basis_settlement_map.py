from decimal import Decimal

from src.gemini_kalshi_basis_settlement_map import (
    append_new_rows,
    build_summary,
    kalshi_expiration_value,
    load_rows,
    match_settlement_values,
)


def _gemini(asset="BTC", end="2608170645", settlement="100.00", outcome="yes"):
    return {
        "ticker": f"{asset}15M{end}",
        "contracts": [{
            "ticker": "UP",
            "instrumentSymbol": f"GEMI-{asset}15M{end}-UP",
            "resolutionSide": outcome,
            "strike": {"value": "99.50"},
            "settlementValue": settlement,
        }],
    }


def _kalshi(asset="BTC", close="2026-08-17T06:45:00Z", value="100.05", outcome="yes"):
    return {
        "ticker": f"KX{asset}15M-TEST",
        "event_ticker": f"KX{asset}15M-TESTEVENT",
        "close_time": close,
        "result": outcome,
        "expiration_value": value,
    }


def test_extracts_canonical_expiration_value():
    value, field = kalshi_expiration_value({"expiration_value": "123.45"})
    assert value == Decimal("123.45")
    assert field == "expiration_value"


def test_matches_true_window_and_computes_signed_basis_bps():
    rows = match_settlement_values([_gemini()], [_kalshi()])
    assert len(rows) == 1
    row = rows[0]
    assert row.asset == "BTC"
    assert row.outcomes_agree is True
    assert Decimal(row.signed_basis) == Decimal("0.05")
    assert Decimal(row.signed_basis_bps) == Decimal("5")
    assert row.classification == "BASIS_RESEARCH_ONLY"
    assert row.trade_authorized is False


def test_negative_basis_and_outcome_disagreement_are_preserved():
    rows = match_settlement_values(
        [_gemini(asset="ETH", settlement="2000", outcome="yes")],
        [_kalshi(asset="ETH", value="1998", outcome="no")],
    )
    assert len(rows) == 1
    assert Decimal(rows[0].signed_basis) == Decimal("-2")
    assert Decimal(rows[0].signed_basis_bps) == Decimal("-10")
    assert rows[0].outcomes_agree is False


def test_rejects_window_mismatch():
    rows = match_settlement_values(
        [_gemini()],
        [_kalshi(close="2026-08-17T06:50:00Z")],
        max_window_skew_s=30,
    )
    assert rows == []


def test_append_is_deduplicated_across_restarts(tmp_path):
    path = tmp_path / "basis.jsonl"
    rows = match_settlement_values([_gemini()], [_kalshi()])
    assert append_new_rows(path, rows) == 1
    assert append_new_rows(path, rows) == 0
    assert len(load_rows(path)) == 1


def test_summary_reports_asset_basis_distribution_and_disagreements(tmp_path):
    path = tmp_path / "basis.jsonl"
    rows = match_settlement_values(
        [_gemini(settlement="100"), _gemini(asset="ETH", settlement="2000", outcome="yes")],
        [_kalshi(value="100.05"), _kalshi(asset="ETH", value="1998", outcome="no")],
    )
    append_new_rows(path, rows)
    summary = build_summary(load_rows(path))
    assert summary["overall"]["windows"] == 2
    assert summary["overall"]["outcome_disagreements"] == 1
    assert Decimal(summary["by_asset"]["BTC"]["max_positive_basis_bps"]) == Decimal("5")
    assert Decimal(summary["by_asset"]["ETH"]["max_negative_basis_bps"]) == Decimal("-10")
    assert summary["trade_authorized"] is False
