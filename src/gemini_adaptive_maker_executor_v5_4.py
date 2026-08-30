"""V5.4 live campaign wrapper: adaptive V5.3 + persistent global daily risk.

The underlying maker/fill/rescue implementation is the reviewed adaptive V5.3
campaign with V5.4 grid/catastrophic-loss admission. This wrapper adds an account-
level persistent UTC-day stop before every campaign, so process restarts and fresh
approval IDs cannot reset the daily loss baseline.
"""
from __future__ import annotations

from typing import Any

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_daily_risk_v5_4 as daily
from src import gemini_adaptive_maker_executor_v5_3 as v53


async def run_campaign_v54(*, guard: Any, campaign_no: int) -> dict[str, Any]:
    try:
        available, flat = await v53.v5.v1._preflight_flat()
        decision = daily.check(
            current_available_usd=available,
            loss_limit_usd=v53.v5.max_daily_loss(),
            account_flat=(flat.get("orders") == 0 and flat.get("positions") == 0),
            allow_rollover=True,
            persist_observation=True,
        )
    except Exception as exc:
        v53.v5.v1.emit(
            "V5_4_GLOBAL_DAILY_RISK_BLOCK",
            status="RISK_LEDGER_ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc)[:240],
            order_entry=False,
        )
        return {
            "status": "RISK_LEDGER_ERROR",
            "order_entry": False,
            "hard_halt": True,
            "error_type": type(exc).__name__,
        }

    v53.v5.v1.emit(
        "V5_4_GLOBAL_DAILY_RISK",
        **decision.as_dict(),
        order_entry=False,
    )
    if decision.blocked:
        return {
            "status": decision.status,
            "order_entry": False,
            "hard_halt": True,
            "daily_risk": decision.as_dict(),
        }

    return await v53.run_campaign_v53(guard=guard, campaign_no=campaign_no)


def install() -> None:
    """Install all adaptive admission patches, then the V5.4 per-campaign risk gate."""
    grid.install()
    v53.v5._run_campaign = run_campaign_v54
