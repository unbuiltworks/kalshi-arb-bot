from decimal import Decimal

from src import gemini_kalshi_maker_dominance_guard as guard

D = Decimal


def test_higher_kalshi_target_only_allows_gemini_yes_kalshi_no():
    safe = guard.authorize_geometry(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike_value="100",
        kalshi_target_value="100.10",
    )
    dangerous = guard.authorize_geometry(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_strike_value="100",
        kalshi_target_value="100.10",
    )
    assert safe["authorized"] is True
    assert safe["dominance_safe_direction"] == "GEMINI_YES_KALSHI_NO"
    assert dangerous["authorized"] is False
    assert dangerous["blocker"] == "DANGEROUS_THRESHOLD_ORIENTATION"


def test_lower_kalshi_target_only_allows_kalshi_yes_gemini_no():
    safe = guard.authorize_geometry(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_strike_value="100",
        kalshi_target_value="99.90",
    )
    assert safe["authorized"] is True
    assert safe["dominance_safe_direction"] == "KALSHI_YES_GEMINI_NO"


def test_gap_below_half_bp_fails_closed():
    out = guard.authorize_geometry(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike_value="100",
        kalshi_target_value="100.004",
    )
    assert out["authorized"] is False
    assert out["blocker"] == "PROTECTIVE_GAP_BELOW_MINIMUM"


def test_equal_threshold_fails_closed():
    out = guard.authorize_geometry(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike_value="100",
        kalshi_target_value="100",
    )
    assert out["authorized"] is False
    assert out["blocker"] == "EQUAL_THRESHOLD_NO_PROTECTIVE_GAP"


def test_missing_threshold_fails_closed():
    out = guard.authorize_geometry(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike_value=None,
        kalshi_target_value="100",
    )
    assert out["authorized"] is False
    assert out["blocker"] == "MISSING_OR_INVALID_THRESHOLD"


def test_kalshi_target_requires_one_unambiguous_numeric_threshold():
    assert guard.kalshi_target({"floor_strike": "123.45"}) == D("123.45")
    assert guard.kalshi_target({"floor_strike": "123", "cap_strike": "124"}) is None
    assert guard.kalshi_target({"floor_strike": None, "functional_strike": "123"}) == D("123")


def test_gemini_strike_uses_up_contract_strike_value():
    event = {
        "contracts": [
            {
                "ticker": "UP",
                "instrumentSymbol": "SOL15M-TEST-UP",
                "strike": {"value": "142.5000"},
            }
        ]
    }
    assert guard.gemini_strike(event) == D("142.5000")
