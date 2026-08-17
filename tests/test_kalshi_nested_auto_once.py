from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_auto_once import AttemptLatch, candidate_to_spec, strike_from_ticker
from src.kalshi_nested_rolling_shadow import Candidate

D = Decimal
NY = ZoneInfo("America/New_York")


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


def test_attempt_latch_claims_exactly_once():
    latch = AttemptLatch()
    assert latch.claim() is True
    assert latch.claim() is False


def test_strike_parser():
    assert strike_from_ticker("KXSOLD-26AUG1620-T62.9999") == D("62.9999")


def test_candidate_to_spec_freezes_proven_greater_orientation():
    spec = candidate_to_spec(
        candidate=_candidate(),
        quote=D("0.95"),
        economics=_econ(),
        fee_type="quadratic",
        fee_multiplier=D("1"),
        scheduled_fee_changes=(),
        maker_rest_seconds=10,
        now=datetime(2026, 8, 16, 19, 0, tzinfo=NY),
    )
    assert spec.strike_type == "greater"
    assert spec.lower_strike == D("62.9999")
    assert spec.higher_strike == D("64.9999")
    assert spec.maker_side == "yes"
    assert spec.hedge_side == "no"
    assert spec.quantity == D("1")


def test_candidate_to_spec_rejects_reverse_orientation():
    with pytest.raises(ValueError, match="only YES\(lower\)\+NO\(higher\)"):
        candidate_to_spec(
            candidate=_candidate(maker_side="no", hedge_side="yes"),
            quote=D("0.95"),
            economics=_econ(),
            fee_type="quadratic",
            fee_multiplier=D("1"),
            scheduled_fee_changes=(),
            maker_rest_seconds=10,
            now=datetime(2026, 8, 16, 19, 0, tzinfo=NY),
        )


def test_candidate_to_spec_rejects_non_nested_strikes():
    with pytest.raises(ValueError, match="maker strike lower"):
        candidate_to_spec(
            candidate=_candidate(
                maker_ticker="KXSOLD-26AUG1620-T66.9999",
                hedge_ticker="KXSOLD-26AUG1620-T64.9999",
            ),
            quote=D("0.95"),
            economics=_econ(),
            fee_type="quadratic",
            fee_multiplier=D("1"),
            scheduled_fee_changes=(),
            maker_rest_seconds=10,
            now=datetime(2026, 8, 16, 19, 0, tzinfo=NY),
        )


def test_candidate_to_spec_rejects_unknown_fee_type():
    with pytest.raises(ValueError, match="mapped quadratic"):
        candidate_to_spec(
            candidate=_candidate(),
            quote=D("0.95"),
            economics=_econ(),
            fee_type="maker_weird",
            fee_multiplier=D("1"),
            scheduled_fee_changes=(),
            maker_rest_seconds=10,
            now=datetime(2026, 8, 16, 19, 0, tzinfo=NY),
        )


def test_candidate_to_spec_rejects_scheduled_fee_change():
    with pytest.raises(ValueError, match="scheduled fee change"):
        candidate_to_spec(
            candidate=_candidate(),
            quote=D("0.95"),
            economics=_econ(),
            fee_type="quadratic",
            fee_multiplier=D("1"),
            scheduled_fee_changes=({"effective_ts":"soon"},),
            maker_rest_seconds=10,
            now=datetime(2026, 8, 16, 19, 0, tzinfo=NY),
        )


def test_candidate_to_spec_rejects_below_three_cent_target():
    with pytest.raises(ValueError, match="target cannot be below three cents"):
        candidate_to_spec(
            candidate=_candidate(target_profit=D("0.01")),
            quote=D("0.97"),
            economics=_econ(hedge_price="0.01", locked_profit="0.01"),
            fee_type="quadratic",
            fee_multiplier=D("1"),
            scheduled_fee_changes=(),
            maker_rest_seconds=10,
            now=datetime(2026, 8, 16, 19, 0, tzinfo=NY),
        )
