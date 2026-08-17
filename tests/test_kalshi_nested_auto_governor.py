from datetime import datetime, timedelta
from decimal import Decimal

from src.kalshi_nested_auto_governor import AutoRiskGovernor, GovernorLimits, ET

D = Decimal


def _now(hour: int = 12):
    return datetime(2026, 8, 16, hour, 0, tzinfo=ET)


def test_first_one_lot_attempt_allowed_and_recorded():
    g = AutoRiskGovernor()
    d = g.decision(
        event_ticker="EVT1",
        pair_key="PAIR1",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=100.0,
    )
    assert d.allowed is True
    g.record_trigger(
        event_ticker="EVT1",
        pair_key="PAIR1",
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=100.0,
    )
    assert g.triggers_today == 1
    assert g.seed_cash_risk_today == D("0.02")


def test_same_event_and_pair_are_blocked():
    g = AutoRiskGovernor(GovernorLimits(cooldown_seconds=0))
    g.record_trigger(
        event_ticker="EVT1",
        pair_key="PAIR1",
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=100.0,
    )
    event = g.decision(
        event_ticker="EVT1",
        pair_key="PAIR2",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=101.0,
    )
    pair = g.decision(
        event_ticker="EVT2",
        pair_key="PAIR1",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=101.0,
    )
    assert event.reason == "EVENT_TRIGGER_LIMIT"
    assert pair.reason == "PAIR_ALREADY_USED"


def test_cooldown_blocks_other_event_temporarily():
    g = AutoRiskGovernor(GovernorLimits(cooldown_seconds=60))
    g.record_trigger(
        event_ticker="EVT1",
        pair_key="PAIR1",
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=100.0,
    )
    blocked = g.decision(
        event_ticker="EVT2",
        pair_key="PAIR2",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=159.9,
    )
    allowed = g.decision(
        event_ticker="EVT2",
        pair_key="PAIR2",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=160.0,
    )
    assert blocked.reason == "COOLDOWN"
    assert allowed.allowed is True


def test_daily_trigger_and_seed_risk_caps_fail_closed():
    limits = GovernorLimits(
        max_triggers_per_day=2,
        cooldown_seconds=0,
        max_seed_cash_risk_per_attempt=D("0.02"),
        max_seed_cash_risk_per_day=D("0.04"),
    )
    g = AutoRiskGovernor(limits)
    for i in range(2):
        g.record_trigger(
            event_ticker=f"EVT{i}",
            pair_key=f"PAIR{i}",
            seed_cash_risk=D("0.02"),
            now=_now(),
            monotonic_now=100.0 + i,
        )
    d = g.decision(
        event_ticker="EVT3",
        pair_key="PAIR3",
        quantity=D("1"),
        seed_cash_risk=D("0.01"),
        now=_now(),
        monotonic_now=200.0,
    )
    assert d.reason == "DAILY_TRIGGER_LIMIT"


def test_quantity_and_attempt_seed_risk_cannot_expand():
    g = AutoRiskGovernor()
    q = g.decision(
        event_ticker="EVT1",
        pair_key="PAIR1",
        quantity=D("2"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=1.0,
    )
    r = g.decision(
        event_ticker="EVT1",
        pair_key="PAIR1",
        quantity=D("1"),
        seed_cash_risk=D("0.03"),
        now=_now(),
        monotonic_now=1.0,
    )
    assert q.reason == "QUANTITY_NOT_ONE"
    assert r.reason == "SEED_RISK_OVER_ATTEMPT_LIMIT"


def test_lockout_is_permanent_for_process():
    g = AutoRiskGovernor()
    g.lockout("AMBIGUOUS_ORDER_STATE")
    d = g.decision(
        event_ticker="EVT1",
        pair_key="PAIR1",
        quantity=D("1"),
        seed_cash_risk=D("0.01"),
        now=_now(),
        monotonic_now=1.0,
    )
    assert d.allowed is False
    assert d.reason == "LOCKED_OUT:AMBIGUOUS_ORDER_STATE"


def test_new_et_day_resets_daily_usage_but_not_lockout():
    g = AutoRiskGovernor(GovernorLimits(cooldown_seconds=0))
    g.record_trigger(
        event_ticker="EVT1",
        pair_key="PAIR1",
        seed_cash_risk=D("0.02"),
        now=_now(23),
        monotonic_now=10.0,
    )
    tomorrow = _now(23) + timedelta(days=1)
    d = g.decision(
        event_ticker="EVT1",
        pair_key="PAIR1",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=tomorrow,
        monotonic_now=20.0,
    )
    assert d.allowed is True
    g.lockout("TEST")
    later = g.decision(
        event_ticker="EVT2",
        pair_key="PAIR2",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=tomorrow + timedelta(days=1),
        monotonic_now=30.0,
    )
    assert later.reason == "LOCKED_OUT:TEST"
