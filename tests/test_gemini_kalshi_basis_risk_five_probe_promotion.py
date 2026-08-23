from decimal import Decimal

from src import gemini_kalshi_basis_risk_q1_child_v5 as v5


def _state(*, reservations=3, reserved_usd="3", realized=3, losses=0, loss_usd="0", unresolved=False):
    return {
        "reservations": reservations,
        "reserved_usd": Decimal(reserved_usd),
        "realized": realized,
        "realized_losses": losses,
        "realized_loss_usd": Decimal(loss_usd),
        "realized_net_pnl": Decimal("0.1862"),
        "unresolved_reservations": ["x"] if unresolved else [],
        "unresolved_submissions": [],
    }


def test_five_probe_limit_allows_two_more_after_three_clean_realizations():
    assert v5.PROMOTED_PROBE_LIMIT == 5
    assert v5.automatic_rollover_allowed(_state()) == (True, "OK")
    assert v5.automatic_rollover_allowed(_state(reservations=4, reserved_usd="4", realized=4)) == (True, "OK")


def test_five_probe_limit_stops_at_five_without_increasing_budget():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(reservations=5, reserved_usd="5", realized=5)
    )
    assert allowed is False
    assert reason == "PROMOTED_PROBE_LIMIT_REACHED"


def test_any_realized_loss_still_hard_stops_before_next_probe():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(reservations=3, reserved_usd="3", realized=3, losses=1, loss_usd="0.01")
    )
    assert allowed is False
    assert reason == "REALIZED_LOSS_HARD_STOP"


def test_unresolved_exposure_still_blocks_promotion():
    allowed, reason = v5.automatic_rollover_allowed(_state(unresolved=True))
    assert allowed is False
    assert reason == "UNRESOLVED_RESERVATION"
