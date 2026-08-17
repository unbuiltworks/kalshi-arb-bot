from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_auto_once import (
    AttemptLatch,
    candidate_to_spec,
    live_session_settings,
    prepare_live_arm_file,
    session_client_root,
    strike_from_ticker,
)
from src.kalshi_nested_rolling_shadow import Candidate

D = Decimal
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 16, 19, 0, tzinfo=NY)


def _candidate(**overrides):
    values = dict(
        series="KXSOLD",
        event_ticker="KXSOLD-26AUG1620",
        event_close=datetime(2026, 8, 16, 20, 0, tzinfo=NY),
        maker_ticker="KXSOLD-26AUG1620-T62.9999",
        maker_side="yes",
        hedge_ticker="KXSOLD-26AUG1620-T64.9999",
        hedge_side="no",
        price_ranges=({"start":"0.00","end":"1.00","step":"0.01"},),
        fee_multiplier=D("1"),
        target_profit=D("0.03"),
    )
    values.update(overrides)
    return Candidate(**values)


def _econ(**overrides):
    values = dict(
        reason="READY",
        maker_bid="0.94",
        maker_ask="1.00",
        hedge_price="0.01",
        hedge_size="10",
        taker_fee="0.01",
        locked_profit="0.03",
        max_maker_price="0.95",
    )
    values.update(overrides)
    return values


def _spec(candidate, quote=D("0.95"), economics=None, **overrides):
    params = dict(
        candidate=candidate,
        quote=quote,
        economics=economics or _econ(),
        fee_type="quadratic",
        fee_multiplier=D("1"),
        scheduled_fee_changes=(),
        maker_rest_seconds=10,
        now=NOW,
    )
    params.update(overrides)
    return candidate_to_spec(**params)


def test_attempt_latch_claims_exactly_once():
    latch = AttemptLatch()
    assert latch.claim() is True
    assert latch.claim() is False


def test_strike_parser():
    assert strike_from_ticker("KXSOLD-26AUG1620-T62.9999") == D("62.9999")


def test_session_client_root_is_stable_but_does_not_expose_raw_session():
    a = session_client_root("session-2026-08-16-alpha")
    b = session_client_root("session-2026-08-16-alpha")
    c = session_client_root("session-2026-08-16-beta")
    assert a == b
    assert a != c
    assert a.startswith("nested-auto-")
    assert "session-2026" not in a


def test_live_session_settings_fail_closed_without_all_env(monkeypatch):
    monkeypatch.delenv("ENABLE_KALSHI_NESTED_LIVE_PROBES", raising=False)
    monkeypatch.delenv("KALSHI_NESTED_AUTO_SESSION_ID", raising=False)
    monkeypatch.delenv("KALSHI_NESTED_AUTO_ARM_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="environment gate"):
        live_session_settings()


def test_live_session_settings_require_long_arm(monkeypatch):
    monkeypatch.setenv("ENABLE_KALSHI_NESTED_LIVE_PROBES", "YES")
    monkeypatch.setenv("KALSHI_NESTED_AUTO_SESSION_ID", "session-123")
    monkeypatch.setenv("KALSHI_NESTED_AUTO_ARM_TOKEN", "short")
    with pytest.raises(RuntimeError, match="arm token"):
        live_session_settings()


def test_prepare_live_arm_file_sets_generic_one_use_path(monkeypatch):
    root = session_client_root("session-file-test")
    path = prepare_live_arm_file(root, "0123456789abcdef0123456789abcdef")
    try:
        assert path.is_file()
        assert monkeypatch is not None
        assert path.read_text(encoding="utf-8").startswith("0123456789abcdef")
    finally:
        path.unlink(missing_ok=True)


def test_candidate_to_spec_accepts_yes_lower_maker_no_higher_seed():
    spec = _spec(_candidate())
    assert spec.strike_type == "greater"
    assert spec.lower_ticker.endswith("T62.9999")
    assert spec.lower_strike == D("62.9999")
    assert spec.higher_ticker.endswith("T64.9999")
    assert spec.higher_strike == D("64.9999")
    assert spec.maker_side == "yes"
    assert spec.hedge_side == "no"
    assert spec.quantity == D("1")


def test_candidate_to_spec_accepts_no_higher_maker_yes_lower_seed():
    candidate = _candidate(
        maker_ticker="KXSOLD-26AUG1620-T64.9999",
        maker_side="no",
        hedge_ticker="KXSOLD-26AUG1620-T62.9999",
        hedge_side="yes",
    )
    spec = _spec(candidate)
    assert spec.lower_ticker.endswith("T62.9999")
    assert spec.lower_strike == D("62.9999")
    assert spec.higher_ticker.endswith("T64.9999")
    assert spec.higher_strike == D("64.9999")
    assert spec.maker_ticker.endswith("T64.9999")
    assert spec.maker_side == "no"
    assert spec.hedge_ticker.endswith("T62.9999")
    assert spec.hedge_side == "yes"


def test_candidate_to_spec_rejects_wrong_side_set_even_when_strikes_nested():
    with pytest.raises(ValueError, match=r"exactly YES\(lower\)\+NO\(higher\)"):
        _spec(_candidate(maker_side="no", hedge_side="yes"))


def test_candidate_to_spec_rejects_two_yes_legs():
    with pytest.raises(ValueError, match=r"exactly YES\(lower\)\+NO\(higher\)"):
        _spec(_candidate(hedge_side="yes"))


def test_candidate_to_spec_rejects_equal_strikes():
    with pytest.raises(ValueError, match="nested strikes must differ"):
        _spec(_candidate(hedge_ticker="KXSOLD-26AUG1620-T62.9999"))


def test_candidate_to_spec_rejects_unknown_fee_type():
    with pytest.raises(ValueError, match="mapped quadratic"):
        _spec(_candidate(), fee_type="maker_weird")


def test_candidate_to_spec_rejects_scheduled_fee_change():
    with pytest.raises(ValueError, match="scheduled fee change"):
        _spec(_candidate(), scheduled_fee_changes=({"effective_ts":"soon"},))


def test_candidate_to_spec_rejects_below_three_cent_target():
    with pytest.raises(ValueError, match="target cannot be below three cents"):
        _spec(
            _candidate(target_profit=D("0.01")),
            quote=D("0.97"),
            economics=_econ(hedge_price="0.01", locked_profit="0.01"),
        )
