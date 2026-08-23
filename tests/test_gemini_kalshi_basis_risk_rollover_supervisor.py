"""Safety tests for bounded automatic q=1 basis-risk rollover.

This file is also a watched-path deployment marker: the 2026-08-23 update forces
Railway to build a fresh source snapshot after switching the service to test-only mode,
avoiding Railway redeploy reuse of an older live start command.
"""
from decimal import Decimal
from datetime import datetime, timezone

from src import gemini_kalshi_basis_risk_q1_child_v5 as v5
from src import gemini_kalshi_basis_risk_rollover_supervisor as rollover


def _state(**overrides):
    base = {
        "reservations": 1,
        "reserved_usd": Decimal("1"),
        "realized": 1,
        "realized_losses": 0,
        "realized_loss_usd": Decimal("0"),
        "realized_net_pnl": Decimal("0.0337"),
        "unresolved_reservations": [],
        "unresolved_submissions": [],
    }
    base.update(overrides)
    return base


def test_profitable_settled_first_probe_allows_next_rollover():
    assert v5.automatic_rollover_allowed(_state()) == (True, "OK")


def test_any_realized_loss_hard_stops_rollover():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(realized_losses=1, realized_loss_usd=Decimal("0.10"), realized_net_pnl=Decimal("-0.10"))
    )
    assert not allowed
    assert reason == "REALIZED_LOSS_HARD_STOP"


def test_unresolved_reservation_blocks_overlap():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(unresolved_reservations=["gkbasis-test"])
    )
    assert not allowed
    assert reason == "UNRESOLVED_RESERVATION"


def test_three_probe_limit_is_absolute():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(
            reservations=3,
            reserved_usd=Decimal("3"),
            realized=3,
            realized_net_pnl=Decimal("0.09"),
        )
    )
    assert not allowed
    assert reason == "PROMOTED_PROBE_LIMIT_REACHED"


def test_campaign_id_is_short_and_unique_by_sequence():
    now = datetime(2026, 8, 23, 3, 5, tzinfo=timezone.utc)
    first = rollover._campaign_id(now, 1)
    second = rollover._campaign_id(now, 2)
    assert first != second
    assert len(first) < 24
    assert len(second) < 24


def test_lease_minutes_is_clamped(monkeypatch):
    monkeypatch.setenv(rollover.LEASE_MINUTES_ENV, "5")
    assert rollover._lease_minutes() == rollover.MIN_LEASE_MINUTES
    monkeypatch.setenv(rollover.LEASE_MINUTES_ENV, "999")
    assert rollover._lease_minutes() == rollover.MAX_LEASE_MINUTES
