from decimal import Decimal

from src import gemini_kalshi_basis_risk_q1_child_v6 as v6

D = Decimal


def state(**overrides):
    base = {
        "reservations": 6,
        "realized": 6,
        "realized_losses": 1,
        "realized_loss_usd": D("0.09"),
        "realized_net_pnl": D("0.2519"),
        "unresolved_reservations": [],
        "unresolved_submissions": [],
        "reviewed_execution_losses": [{
            "trade_id": "gkbasis-br0823131801-01",
            "status": "REALIZED_ONE_LEG_SETTLEMENT",
            "net_pnl": D("-0.09"),
        }],
        "unreviewed_realized_losses": [],
        "no_fill_releases": 0,
        "remaining_loss_budget_usd": D("4.91"),
    }
    base.update(overrides)
    return base


def test_exact_reviewed_execution_loss_does_not_reset_budget_or_block_continuation():
    allowed, reason = v6.automatic_rollover_allowed(state())
    assert allowed is True
    assert reason == "OK"
    assert state()["remaining_loss_budget_usd"] == D("4.91")


def test_any_new_or_unreviewed_loss_hard_stops():
    allowed, reason = v6.automatic_rollover_allowed(state(
        unreviewed_realized_losses=[{"trade_id": "future-loss", "net_pnl": D("-0.01")}]
    ))
    assert allowed is False
    assert reason == "NEW_OR_UNREVIEWED_REALIZED_LOSS_HARD_STOP"


def test_unresolved_exposure_hard_stops():
    allowed, reason = v6.automatic_rollover_allowed(state(
        unresolved_submissions=["open-trade"]
    ))
    assert allowed is False
    assert reason == "UNRESOLVED_SUBMISSION"


def test_approved_loss_budget_is_never_reset():
    allowed, reason = v6.automatic_rollover_allowed(state(
        realized_loss_usd=D("5"), remaining_loss_budget_usd=D("0")
    ))
    assert allowed is False
    assert reason == "APPROVED_LOSS_BUDGET_EXHAUSTED"
