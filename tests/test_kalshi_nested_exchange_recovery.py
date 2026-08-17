from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.kalshi_nested_auto_governor import GovernorLimits, ET
from src.kalshi_nested_exchange_recovery import (
    event_from_market_ticker,
    reconstruct_attempts,
    recover_governor_from_orders,
)

D = Decimal


def _now():
    return datetime(2026, 8, 16, 21, 0, tzinfo=ET)


def _row(*, root="nested-auto-abc12345", leg="seed", ticker="KXETHD-26AUG1621-T1889.99", status="executed", fill="1.00", created=None):
    when = created or datetime(2026, 8, 16, 20, 30, tzinfo=ET)
    return {
        "order_id": f"oid-{root}-{leg}",
        "client_order_id": f"{root}-{leg}",
        "ticker": ticker,
        "status": status,
        "fill_count_fp": fill,
        "remaining_count_fp": "0.00" if status != "resting" else "1.00",
        "created_time": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def test_event_from_market_ticker():
    assert event_from_market_ticker("KXETHD-26AUG1621-T1889.99") == "KXETHD-26AUG1621"


def test_reconstruct_seed_and_maker_attempt():
    root = "nested-auto-abc12345"
    rows = [
        _row(root=root, leg="seed", ticker="KXETHD-26AUG1621-T1889.99"),
        _row(root=root, leg="maker", ticker="KXETHD-26AUG1621-T1919.99"),
    ]
    attempts = reconstruct_attempts(rows)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.root == root
    assert attempt.event_ticker == "KXETHD-26AUG1621"
    assert attempt.seed_fill_count == D("1.00")
    assert attempt.maker_fill_count == D("1.00")
    assert attempt.seeded_without_full_maker is False


def test_seed_only_attempt_is_recovered_without_inventing_maker():
    attempts = reconstruct_attempts([_row()])
    assert len(attempts) == 1
    assert attempts[0].seeded_without_full_maker is True
    assert attempts[0].maker_order is None


def test_non_own_orders_are_ignored():
    row = _row()
    row["client_order_id"] = "something-else"
    assert reconstruct_attempts([row]) == ()


def test_malformed_own_client_id_fails_closed():
    row = _row()
    row["client_order_id"] = "nested-auto-malformed"
    with pytest.raises(ValueError, match="malformed own client_order_id"):
        reconstruct_attempts([row])


def test_duplicate_leg_fails_closed():
    row = _row()
    duplicate = dict(row)
    duplicate["order_id"] = "other"
    with pytest.raises(ValueError, match="duplicate seed"):
        reconstruct_attempts([row, duplicate])


def test_attempt_spanning_events_fails_closed():
    root = "nested-auto-abc12345"
    rows = [
        _row(root=root, leg="seed", ticker="KXETHD-26AUG1621-T1889.99"),
        _row(root=root, leg="maker", ticker="KXETHD-26AUG1717-T1919.99"),
    ]
    with pytest.raises(ValueError, match="multiple events"):
        reconstruct_attempts(rows)


def test_unknown_order_status_fails_closed():
    with pytest.raises(ValueError, match="unknown order status"):
        reconstruct_attempts([_row(status="mystery")])


def test_recovery_counts_each_exchange_visible_attempt_conservatively():
    limits = GovernorLimits(
        max_triggers_per_day=5,
        max_triggers_per_event=1,
        cooldown_seconds=60,
        max_seed_cash_risk_per_attempt=D("0.02"),
        max_seed_cash_risk_per_day=D("0.10"),
    )
    rows = [
        _row(root="nested-auto-attempt01", ticker="KXETHD-26AUG1621-T1889.99"),
        _row(root="nested-auto-attempt02", ticker="KXSOLD-26AUG1621-T61.9999", created=_now() - timedelta(minutes=5)),
    ]
    recovered = recover_governor_from_orders(rows, limits=limits, now=_now())
    assert recovered.safe_to_scan is True
    assert recovered.governor.triggers_today == 2
    assert recovered.governor.seed_cash_risk_today == D("0.04")
    assert recovered.governor.event_counts == {
        "KXETHD-26AUG1621": 1,
        "KXSOLD-26AUG1621": 1,
    }


def test_yesterday_does_not_consume_today_limits():
    limits = GovernorLimits()
    rows = [_row(created=_now() - timedelta(days=1))]
    recovered = recover_governor_from_orders(rows, limits=limits, now=_now())
    assert recovered.attempts == ()
    assert recovered.governor.triggers_today == 0


def test_resting_order_at_restart_locks_out():
    limits = GovernorLimits()
    recovered = recover_governor_from_orders(
        [_row(status="resting", fill="0.00")], limits=limits, now=_now()
    )
    assert recovered.safe_to_scan is False
    assert any(reason.startswith("RESTING_ORDER_AT_STARTUP") for reason in recovered.lockout_reasons)


def test_maker_without_seed_locks_out():
    limits = GovernorLimits()
    recovered = recover_governor_from_orders(
        [_row(leg="maker")], limits=limits, now=_now()
    )
    assert recovered.safe_to_scan is False
    assert any(reason.startswith("MAKER_WITHOUT_SEED") for reason in recovered.lockout_reasons)


def test_recovered_event_overuse_locks_out():
    limits = GovernorLimits(max_triggers_per_event=1, cooldown_seconds=0)
    rows = [
        _row(root="nested-auto-attempt01", ticker="KXETHD-26AUG1621-T1889.99"),
        _row(root="nested-auto-attempt02", ticker="KXETHD-26AUG1621-T1894.99", created=_now() - timedelta(minutes=1)),
    ]
    recovered = recover_governor_from_orders(rows, limits=limits, now=_now())
    assert recovered.safe_to_scan is False
    assert "RECOVERED_EVENT_TRIGGER_LIMIT_EXCEEDED" in recovered.lockout_reasons


def test_recovery_preserves_wall_clock_cooldown():
    limits = GovernorLimits(cooldown_seconds=60)
    created = _now() - timedelta(seconds=20)
    recovered = recover_governor_from_orders(
        [_row(created=created)], limits=limits, now=_now()
    )
    decision = recovered.governor.decision(
        event_ticker="KXSOLD-26AUG1621",
        pair_key="new-pair",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=1.0,
    )
    assert decision.reason == "COOLDOWN"
