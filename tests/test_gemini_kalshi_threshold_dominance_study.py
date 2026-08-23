from datetime import datetime, timezone
from decimal import Decimal

from src.gemini_kalshi_settlement_agreement import AgreementRow
from src.gemini_kalshi_threshold_dominance_study import (
    DIR_GYES_KNO,
    DIR_KYES_GNO,
    classify,
    dominance_profile,
    kalshi_target,
    threshold_gap_bps,
)


def test_incident_threshold_gap_is_about_60bp_and_protects_reverse_orientation():
    g = Decimal("76863.63")
    k = Decimal("77325.16")
    profile = dominance_profile(g, k)
    assert Decimal(profile["threshold_gap"]) == Decimal("461.53")
    assert Decimal(profile["threshold_gap_bps"]) > Decimal("60")
    assert Decimal(profile["threshold_gap_bps"]) < Decimal("60.1")
    assert profile["dominance_safe_direction_shared_value"] == DIR_GYES_KNO
    assert profile["dangerous_gap_direction_shared_value"] == DIR_KYES_GNO


def test_reverse_threshold_order_reverses_safe_direction():
    profile = dominance_profile(Decimal("101"), Decimal("100"))
    assert profile["dominance_safe_direction_shared_value"] == DIR_KYES_GNO
    assert profile["dangerous_gap_direction_shared_value"] == DIR_GYES_KNO
    assert threshold_gap_bps(Decimal("101"), Decimal("100")) < 0


def test_kalshi_target_requires_one_unambiguous_numeric_threshold():
    assert kalshi_target({"floor_strike": 123.45, "cap_strike": None}) == Decimal("123.45")
    assert kalshi_target({"floor_strike": 123, "cap_strike": 124}) is None
    assert kalshi_target({"functional_strike": "123.5"}) == Decimal("123.5")


def test_incident_disagreement_is_explained_by_thresholds_without_index_divergence():
    row = AgreementRow(
        asset="BTC",
        expiry=datetime(2026, 8, 22, 5, 30, tzinfo=timezone.utc),
        gemini_ticker="BTC15M2608220530",
        kalshi_ticker="KXBTC15M-26AUG220130-30",
        gemini_outcome="yes",
        kalshi_outcome="no",
        agrees=False,
        gemini_strike=76863.63,
        gemini_settlement=77275.31415094339,
        gemini_margin_bps=53.5603,
    )
    event = {
        "ticker": row.gemini_ticker,
        "sourceDetails": {"agency": "Kaiko", "index": "GRR-KAIKO_RFR_BTCUSD_60S"},
    }
    market = {
        "ticker": row.kalshi_ticker,
        "floor_strike": 77325.16,
        "strike_type": "greater",
        "result": "no",
    }
    out = classify(row, event, market)
    assert out["kalshi_outcome_using_gemini_final_shared_value"] == "no"
    assert out["threshold_only_explains_observed_disagreement"] is True
    assert out["index_divergence_required_to_explain_observed_disagreement"] is False
    assert out["dangerous_gap_direction_shared_value"] == DIR_KYES_GNO
    assert out["both_lose_orientation"] == DIR_KYES_GNO
    assert out["observed_disagreement_harms_dominance_safe_direction"] is False


def test_actual_numeric_kalshi_value_can_measure_cross_index_basis_when_present():
    row = AgreementRow(
        asset="BTC",
        expiry=datetime(2026, 8, 22, 5, 30, tzinfo=timezone.utc),
        gemini_ticker="BTC15M2608220530",
        kalshi_ticker="KXBTC15M-26AUG220130-30",
        gemini_outcome="yes",
        kalshi_outcome="no",
        agrees=False,
        gemini_strike=100.0,
        gemini_settlement=101.0,
        gemini_margin_bps=100.0,
    )
    out = classify(
        row,
        {"ticker": row.gemini_ticker},
        {"ticker": row.kalshi_ticker, "floor_strike": 102, "expiration_value": "100.5"},
    )
    assert Decimal(out["kalshi_minus_gemini_final_value"]) == Decimal("-0.5")
    assert Decimal(out["kalshi_minus_gemini_final_bps"]) < 0
