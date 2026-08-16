from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_live_probe_guard import (
    NestedLiveProbeSpec,
    build_v2_payloads,
    outcome_cost_to_v2_price,
    outcome_to_v2_book_side,
    validate_spec,
    v2_price_to_outcome_cost,
)

D = Decimal
NY = ZoneInfo("America/New_York")


def _spec(**overrides):
    values = dict(
        event_ticker="KXETHD-26AUG1620",
        event_close=datetime(2026, 8, 16, 20, 0, tzinfo=NY),
        strike_type="greater",
        lower_ticker="KXETHD-26AUG1620-T1754.99",
        lower_strike=D("1754.99"),
        higher_ticker="KXETHD-26AUG1620-T1819.99",
        higher_strike=D("1819.99"),
        maker_ticker="KXETHD-26AUG1620-T1754.99",
        maker_side="yes",
        maker_cost=D("0.95"),
        hedge_ticker="KXETHD-26AUG1620-T1819.99",
        hedge_side="no",
        hedge_cost=D("0.01"),
        fee_type="quadratic",
        fee_multiplier=D("1"),
        target_profit=D("0.03"),
        maker_rest_seconds=10,
    )
    values.update(overrides)
    return NestedLiveProbeSpec(**values)


def test_v2_yes_no_price_mapping_is_unified_yes_space():
    assert outcome_to_v2_book_side("yes") == "bid"
    assert outcome_to_v2_book_side("no") == "ask"
    assert outcome_cost_to_v2_price("yes", D("0.95")) == D("0.95")
    assert outcome_cost_to_v2_price("no", D("0.01")) == D("0.99")
    assert v2_price_to_outcome_cost("no", D("0.99")) == D("0.01")


def test_greater_pair_accepts_either_leg_as_maker():
    now = datetime(2026, 8, 16, 19, 0, tzinfo=NY)
    locked, fee = validate_spec(_spec(), now=now)
    assert locked == D("0.03")
    assert fee == D("0.01")

    reversed_spec = _spec(
        maker_ticker="KXETHD-26AUG1620-T1819.99",
        maker_side="no",
        maker_cost=D("0.01"),
        hedge_ticker="KXETHD-26AUG1620-T1754.99",
        hedge_side="yes",
        hedge_cost=D("0.95"),
    )
    locked2, _ = validate_spec(reversed_spec, now=now)
    assert locked2 == D("0.03")


def test_less_pair_orientation_is_mirrored_and_proven():
    spec = _spec(
        strike_type="less",
        maker_ticker="KXETHD-26AUG1620-T1754.99",
        maker_side="no",
        maker_cost=D("0.20"),
        hedge_ticker="KXETHD-26AUG1620-T1819.99",
        hedge_side="yes",
        hedge_cost=D("0.74"),
    )
    locked, _ = validate_spec(spec, now=datetime(2026, 8, 16, 19, 0, tzinfo=NY))
    assert locked >= D("0.03")


def test_rejects_wrong_nested_orientation_and_cross_event_leg():
    now = datetime(2026, 8, 16, 19, 0, tzinfo=NY)
    with pytest.raises(ValueError, match="proven nested payoff"):
        validate_spec(_spec(maker_side="no", hedge_side="yes"), now=now)
    with pytest.raises(ValueError, match="exact same event"):
        validate_spec(
            _spec(higher_ticker="KXETHD-26AUG1717-T1819.99", hedge_ticker="KXETHD-26AUG1717-T1819.99"),
            now=now,
        )


def test_rejects_zero_cent_boundary_fee_change_and_long_lockup():
    now = datetime(2026, 8, 16, 19, 0, tzinfo=NY)
    with pytest.raises(ValueError, match="1c..99c"):
        validate_spec(_spec(hedge_cost=D("0")), now=now)
    with pytest.raises(ValueError, match="scheduled fee change"):
        validate_spec(_spec(scheduled_fee_changes=({"new_fee_multiplier": "2"},)), now=now)
    with pytest.raises(ValueError, match="six-hour"):
        validate_spec(
            _spec(event_close=datetime(2026, 8, 17, 17, 0, tzinfo=NY)),
            now=now,
        )


def test_rejects_below_three_cent_exact_payload_economics():
    now = datetime(2026, 8, 16, 19, 0, tzinfo=NY)
    with pytest.raises(ValueError, match="preserve target"):
        validate_spec(_spec(maker_cost=D("0.96")), now=now)


def test_build_payloads_are_one_lot_post_only_maker_then_ioc_hedge(monkeypatch):
    monkeypatch.setattr(
        "src.kalshi_nested_live_probe_guard.datetime",
        type("Frozen", (), {"now": staticmethod(lambda tz=None: datetime(2026, 8, 16, 19, 0, tzinfo=NY))}),
    )
    maker, hedge = build_v2_payloads(_spec(), now_epoch=1786921200)
    assert maker["side"] == "bid"
    assert maker["price"] == "0.95"
    assert maker["count"] == "1.00"
    assert maker["post_only"] is True
    assert maker["time_in_force"] == "good_till_canceled"
    assert maker["expiration_time"] == 1786921210
    assert hedge["side"] == "ask"
    assert hedge["price"] == "0.99"
    assert hedge["count"] == "1.00"
    assert hedge["post_only"] is False
    assert hedge["time_in_force"] == "immediate_or_cancel"
