from decimal import Decimal

import pytest

from src import gemini_kalshi_basis_risk_q1_phase2 as phase2


def _state(*, reservations=5, realized=5, losses=0, loss_usd="0", unresolved=False):
    return {
        "reservations": reservations,
        "reserved_usd": Decimal(str(reservations)),
        "realized": realized,
        "realized_losses": losses,
        "realized_loss_usd": Decimal(loss_usd),
        "realized_net_pnl": Decimal("0.3419"),
        "unresolved_reservations": ["x"] if unresolved else [],
        "unresolved_submissions": [],
    }


def test_phase2_requires_five_clean_fully_realized_baseline():
    assert phase2.phase2_rollover_allowed(_state(reservations=4, realized=4)) == (
        False,
        "FIVE_PROBE_BASELINE_NOT_PROVEN",
    )
    assert phase2.phase2_rollover_allowed(_state(reservations=5, realized=4)) == (
        False,
        "FIVE_PROBE_BASELINE_NOT_PROVEN",
    )
    assert phase2.phase2_rollover_allowed(_state()) == (True, "OK")


def test_phase2_allows_winning_turnover_without_increasing_loss_budget():
    assert phase2.PHASE2_PROBE_LIMIT == 10
    assert phase2.phase2_rollover_allowed(_state(reservations=9, realized=9)) == (True, "OK")
    assert phase2.phase2_rollover_allowed(_state(reservations=10, realized=10)) == (
        False,
        "PHASE2_PROBE_LIMIT_REACHED",
    )


def test_phase2_still_hard_stops_on_any_realized_loss_or_unresolved_exposure():
    assert phase2.phase2_rollover_allowed(_state(losses=1, loss_usd="0.01")) == (
        False,
        "REALIZED_LOSS_HARD_STOP",
    )
    assert phase2.phase2_rollover_allowed(_state(unresolved=True)) == (
        False,
        "UNRESOLVED_RESERVATION",
    )


def test_phase2_install_requires_explicit_approval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(phase2.APPROVAL_ENV, raising=False)
    with pytest.raises(PermissionError, match="explicit"):
        phase2.install()
