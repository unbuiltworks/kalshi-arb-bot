from decimal import Decimal as D
import time

import pytest

from src.kalshi_cross_book import TwoSidedQuote
from src.kp_unemployment_certification import APPROVALS, validate_poly_market
from src.kp_unemployment_live_probe import _arm_state, _leg
from src.polymarket_kalshi_shadow import PolyQuote, best_plan


def test_three_exact_approvals_have_expected_fingerprints():
    expected = {
        "4.0": "1ed70da925d93f3b40ca7727ebb4937e91dcdda38d7f40d6b179b65302c2e08a",
        "4.2": "01e417e188028bf010d60b41fa3bf271bbbc8d87110200b2470abe334664ab5c",
        "4.4": "370f4cc3cb5bd07847c59962691270e7b503b03d637ae7e71c1eb1d4521f2f78",
    }
    assert len(APPROVALS) == 3
    assert {str(a.threshold): a.fingerprint for a in APPROVALS} == expected


def test_poly_rule_drift_fails_closed():
    approval = APPROVALS[0]
    good = {
        "slug": approval.polymarket_slug,
        "active": True,
        "closed": False,
        "archived": False,
        "question": "August Unemployment Rate",
        "title": "Above 4.0%",
        "description": (
            "seasonally adjusted unemployment rate (U-3) reported by the Bureau of Labor Statistics "
            "in the Employment Situation Report for August 2026 above 4.0%. Subsequent revisions "
            "will not be considered. If unavailable within three months use the most recent previous month."
        ),
    }
    validate_poly_market(approval, good)
    bad = dict(good); bad["description"] = "Different source and revision policy"
    with pytest.raises(PermissionError):
        validate_poly_market(approval, bad)


def test_one_dollar_bankroll_hard_caps_profitable_plan_to_q1():
    now = int(time.time() * 1000)
    # q=1 costs comfortably less than $1 after fees; q=2 cannot fit the $1 cap.
    poly = PolyQuote("x", D("0.25"), D("100"), D("0.76"), D("100"), "OPEN", now)
    kalshi = TwoSidedQuote("y", D("0.37"), D("100"), D("0.60"), D("100"), now, None, 1)
    plan = best_plan(poly, kalshi, bankroll=D("1"), poly_fee_rate=D("0.05"), kalshi_fee_rate=D("0.07"))
    assert plan is not None
    assert plan.quantity == 1
    assert plan.profit > 0


def test_no_side_converts_to_yes_book_limit_without_changing_quantity():
    leg = _leg("polymarket", "slug", "UNDER", D("0.63"))
    assert leg.native_outcome == "NO"
    assert leg.yes_book_limit == D("0.37")


def test_arm_requires_live_gate_unique_run_and_short_future_deadline(monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    monkeypatch.setenv("KP_UNEMPLOYMENT_RUN_ID", "u3-test-1")
    monkeypatch.setenv("KP_UNEMPLOYMENT_ARM_UNTIL", str(int(time.time()) + 120))
    armed, run_id, deadline = _arm_state()
    assert armed is True and run_id == "u3-test-1" and deadline is not None


def test_arm_rejects_long_lived_authorization(monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    monkeypatch.setenv("KP_UNEMPLOYMENT_RUN_ID", "u3-test-2")
    monkeypatch.setenv("KP_UNEMPLOYMENT_ARM_UNTIL", str(int(time.time()) + 900))
    with pytest.raises(PermissionError):
        _arm_state()
