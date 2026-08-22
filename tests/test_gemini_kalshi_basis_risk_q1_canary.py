from decimal import Decimal

import pytest

from src import gemini_kalshi_crypto_postincident_boot as postincident
from src.gemini_kalshi_basis_risk_q1_child import dominance_authorization

D = Decimal


def test_kalshi_higher_only_allows_gemini_yes_kalshi_no():
    safe = dominance_authorization(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike="100",
        kalshi_target="101",
    )
    assert safe["authorized"] is True
    assert safe["dominance_safe_direction"] == "GEMINI_YES_KALSHI_NO"
    assert D(safe["threshold_gap_abs_bps"]) == D("100")

    dangerous = dominance_authorization(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_strike="100",
        kalshi_target="101",
    )
    assert dangerous["authorized"] is False
    assert dangerous["blocker"] == "DANGEROUS_THRESHOLD_ORIENTATION"


def test_gemini_higher_only_allows_kalshi_yes_gemini_no():
    safe = dominance_authorization(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_strike="101",
        kalshi_target="100",
    )
    assert safe["authorized"] is True
    assert safe["dominance_safe_direction"] == "KALSHI_YES_GEMINI_NO"

    dangerous = dominance_authorization(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike="101",
        kalshi_target="100",
    )
    assert dangerous["authorized"] is False
    assert dangerous["blocker"] == "DANGEROUS_THRESHOLD_ORIENTATION"


def test_gap_below_ten_bp_rejected():
    out = dominance_authorization(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike="100",
        kalshi_target="100.05",
    )
    assert out["authorized"] is False
    assert D(out["threshold_gap_abs_bps"]) == D("5")
    assert out["blocker"] == "PROTECTIVE_GAP_BELOW_MINIMUM"


def test_cannot_configure_canary_below_approved_ten_bp_floor():
    out = dominance_authorization(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike="100",
        kalshi_target="101",
        min_gap_bps=D("5"),
    )
    assert out["authorized"] is False
    assert out["blocker"] == "MIN_GAP_BELOW_APPROVED_10BP"


@pytest.mark.parametrize(
    "gemini_strike,kalshi_target,blocker",
    [
        (None, "101", "MISSING_OR_INVALID_THRESHOLD"),
        ("100", None, "MISSING_OR_INVALID_THRESHOLD"),
        ("100", "100", "EQUAL_THRESHOLD_NO_PROTECTIVE_GAP"),
    ],
)
def test_missing_or_equal_thresholds_fail_closed(gemini_strike, kalshi_target, blocker):
    out = dominance_authorization(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike=gemini_strike,
        kalshi_target=kalshi_target,
    )
    assert out["authorized"] is False
    assert out["blocker"] == blocker


def test_guaranteed_arb_postincident_hard_stop_is_unchanged(monkeypatch):
    monkeypatch.delenv(postincident.SETTLEMENT_EQUIVALENCE_ENV, raising=False)
    with pytest.raises(PermissionError):
        postincident._require_settlement_equivalence()
