from decimal import Decimal

from src import gemini_kalshi_basis_risk_q1_child_v5 as v5


def _state(*, reservations=5, reserved_usd="5", realized=5, losses=0, loss_usd="0", unresolved=False):
    return {
        "reservations": reservations,
        "reserved_usd": Decimal(reserved_usd),
        "realized": realized,
        "realized_losses": losses,
        "realized_loss_usd": Decimal(loss_usd),
        "realized_net_pnl": Decimal("0.3419"),
        "unresolved_reservations": ["x"] if unresolved else [],
        "unresolved_submissions": [],
    }


def test_twenty_probe_limit_allows_more_after_five_clean_realizations():
    assert v5.PROMOTED_PROBE_LIMIT == 20
    assert v5.automatic_rollover_allowed(_state()) == (True, "OK")
    assert v5.automatic_rollover_allowed(
        _state(reservations=19, reserved_usd="19", realized=19)
    ) == (True, "OK")


def test_twenty_probe_limit_stops_at_twenty():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(reservations=20, reserved_usd="20", realized=20)
    )
    assert allowed is False
    assert reason == "PROMOTED_PROBE_LIMIT_REACHED"


def test_sequential_turnover_above_five_dollars_does_not_consume_loss_budget():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(reservations=6, reserved_usd="6", realized=6, losses=0, loss_usd="0")
    )
    assert allowed is True
    assert reason == "OK"


def test_any_realized_loss_still_hard_stops_before_next_probe():
    allowed, reason = v5.automatic_rollover_allowed(
        _state(reservations=5, reserved_usd="5", realized=5, losses=1, loss_usd="0.01")
    )
    assert allowed is False
    assert reason == "REALIZED_LOSS_HARD_STOP"


def test_unresolved_exposure_still_blocks_promotion():
    allowed, reason = v5.automatic_rollover_allowed(_state(unresolved=True))
    assert allowed is False
    assert reason == "UNRESOLVED_RESERVATION"
