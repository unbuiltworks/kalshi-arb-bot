from decimal import Decimal

from src.sports_trade_settlement_reconciler_v2 import (
    is_reconcilable_trade,
    one_leg_open_exposure_venue,
    one_leg_settlement_payout,
)

D = Decimal


def _metadata(*, gq="1", kq="0", flatten=None, gemini_outcome="NO", kalshi_side="bid"):
    execution = {
        "gemini": {"filled_quantity": gq, "status": "FILLED" if D(gq) > 0 else "CANCELED"},
        "kalshi": {"filled_quantity": kq, "status": "FILLED" if D(kq) > 0 else "REJECTED"},
        "flatten": flatten,
    }
    return {
        "gemini_payload": {"symbol": "GEMI-TEST", "eventOutcome": gemini_outcome},
        "kalshi_payload": {"ticker": "KALSHI-TEST", "side": kalshi_side},
        "execution": execution,
    }


def _trade(status="ONE_LEG_FILLED", legs=1):
    return {
        "trade_id": "sports-gk:test",
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": status,
        "legs_filled": legs,
        "realized": False,
        "capital_filled": "0.44",
        "fees": "0.02",
        "metadata": _metadata(),
    }


def test_one_leg_incident_enters_reconciliation_queue():
    assert is_reconcilable_trade(_trade()) is True


def test_original_two_leg_awaiting_trade_remains_reconcilable():
    assert is_reconcilable_trade(_trade(status="AWAITING_SETTLEMENT", legs=2)) is True


def test_known_caleb_shape_identifies_gemini_as_open_exposure():
    assert one_leg_open_exposure_venue(_metadata()) == "gemini"


def test_successful_flatten_is_not_misclassified_as_open_settlement_exposure():
    meta = _metadata(flatten={"gemini": {"filled_quantity": "1", "status": "FILLED"}})
    assert one_leg_open_exposure_venue(meta) is None


def test_ambiguous_or_two_original_fills_fail_closed():
    assert one_leg_open_exposure_venue(_metadata(gq="1", kq="1")) is None
    assert one_leg_open_exposure_venue(_metadata(gq="0", kq="0")) is None


def test_gemini_no_one_leg_payout_uses_only_gemini_authoritative_result():
    meta = _metadata(gemini_outcome="NO")
    assert one_leg_settlement_payout(
        venue="gemini", metadata=meta, gemini_result="no", kalshi_result=None
    ) == D("1")
    assert one_leg_settlement_payout(
        venue="gemini", metadata=meta, gemini_result="yes", kalshi_result=None
    ) == D("0")


def test_one_leg_never_realizes_without_filled_venue_resolution():
    assert one_leg_settlement_payout(
        venue="gemini", metadata=_metadata(), gemini_result=None, kalshi_result="yes"
    ) is None
