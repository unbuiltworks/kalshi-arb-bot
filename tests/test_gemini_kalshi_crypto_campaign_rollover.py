from datetime import datetime, timezone

import pytest

from src.gemini_kalshi_crypto_campaign_rollover import plan_rollover


def _clean_state() -> dict:
    return {
        "campaign_id": "gk15m-old",
        "completed_total": 4,
        "execution_attempts_total": 12,
        "halted": False,
        "halt_reason": None,
        "reserved_capital_usd": "0",
        "unsettled_deployed_usd": "0",
        "reservations": {},
        "venue_reserved_usd": {"gemini": "0", "kalshi": "0"},
        "venue_unsettled_usd": {"gemini": "0", "kalshi": "0"},
        "lanes": {
            asset: {
                "status": "IDLE",
                "active_run_id": None,
                "reserved_usd": "0",
            }
            for asset in ("BTC", "ETH", "SOL", "XRP")
        },
    }


def test_expired_clean_campaign_plans_fresh_short_lived_namespace():
    now = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    plan = plan_rollover(
        state=_clean_state(),
        prior_arm_deadline_utc="2026-08-21T19:00:00Z",
        now=now,
        max_completed_trades=5,
        max_execution_attempts=25,
        arm_window_minutes=30,
    )
    assert plan.prior_campaign_id == "gk15m-old"
    assert plan.new_campaign_id != plan.prior_campaign_id
    assert plan.reason == "deadline_expired"
    assert plan.arm_not_after_utc == "2026-08-21T20:30:00Z"
    assert plan.env()["CRYPTO_CANARY_CAMPAIGN_ID"] == plan.new_campaign_id


def test_refuses_rollover_while_prior_campaign_is_still_active_window():
    now = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="still within its arm window"):
        plan_rollover(
            state=_clean_state(),
            prior_arm_deadline_utc="2026-08-21T20:30:00Z",
            now=now,
            max_completed_trades=5,
            max_execution_attempts=25,
        )


def test_completed_cap_can_end_campaign_before_deadline():
    state = _clean_state()
    state["completed_total"] = 5
    now = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    plan = plan_rollover(
        state=state,
        prior_arm_deadline_utc="2026-08-21T20:30:00Z",
        now=now,
        max_completed_trades=5,
        max_execution_attempts=25,
    )
    assert plan.reason == "completed_trade_cap"


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda s: s.update(halted=True, halt_reason="boom"), "is halted"),
        (lambda s: s.update(reservations={"run": "1"}), "capital reservations"),
        (lambda s: s.update(unsettled_deployed_usd="0.42"), "unsettled deployed capital"),
        (lambda s: s["lanes"]["BTC"].update(active_run_id="run-1"), "has active run"),
        (lambda s: s["lanes"]["ETH"].update(status="WAITING_SETTLEMENT"), "unresolved status"),
        (lambda s: s["lanes"]["SOL"].update(reserved_usd="1"), "still has reserved USD"),
    ],
)
def test_refuses_any_locally_unreconciled_prior_state(mutator, match):
    state = _clean_state()
    mutator(state)
    with pytest.raises(RuntimeError, match=match):
        plan_rollover(
            state=state,
            prior_arm_deadline_utc="2026-08-21T19:00:00Z",
            now=datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc),
            max_completed_trades=5,
            max_execution_attempts=25,
        )


def test_refuses_unbounded_rollover_arm_window():
    with pytest.raises(ValueError, match="between 5 and 60"):
        plan_rollover(
            state=_clean_state(),
            prior_arm_deadline_utc="2026-08-21T19:00:00Z",
            now=datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc),
            max_completed_trades=5,
            max_execution_attempts=25,
            arm_window_minutes=240,
        )
