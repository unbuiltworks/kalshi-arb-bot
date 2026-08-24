"""One-attempt promoted basis campaign using v6 Kalshi-first execution.

A Kalshi-first FOK rejection is a safe no-fill.  Fully BOTH_FILLED q=1 positions may
now await settlement while another asset-specific campaign scans, subject to v6's
bounded paired-risk budget.  Any in-flight/unpaired execution risk, new realized loss,
settlement anomaly or budget violation still fail-closes.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic_policy
from src import gemini_kalshi_basis_risk_q1_campaign as campaign
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v6 as child_v6
from src import gemini_kalshi_crypto_live_campaign as crypto
from src import gemini_kalshi_sol_live_canary_v2 as v2

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_campaign_v6")
_ORIGINAL_BASIS_EMIT_EXECUTION = campaign._basis_emit_execution


async def _obtain_v6_execution(run_id: str, arm_deadline: datetime) -> dict[str, Any]:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        with result_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if claim.is_file():
        raise RuntimeError(f"basis-risk local claim exists without result for {run_id}")

    child_env = os.environ.copy()
    child_env[v2.RUN_ID_ENV] = run_id
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_basis_risk_q1_child_v6",
        env=child_env,
    )
    wait_task = asyncio.create_task(process.wait())
    try:
        while crypto._utcnow() <= arm_deadline:
            if result_path.is_file():
                with result_path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            if wait_task.done():
                code = wait_task.result()
                raise RuntimeError(f"v6 basis-risk child exited {code} before persistent result")
            await asyncio.sleep(crypto.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"basis-risk arm deadline reached before execution for {run_id}")
    finally:
        await crypto._stop_child(process)
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if arm.is_file() and not claim.is_file():
            try:
                arm.unlink()
            except FileNotFoundError:
                pass


async def _basis_emit_execution_v6(http, run_id: str, record: dict[str, Any]):
    result = record.get("result") or {}
    outcome = str(result.get("outcome") or "")
    if outcome != "NEITHER_FILLED":
        return await _ORIGINAL_BASIS_EMIT_EXECUTION(http, run_id, record)

    try:
        return await _ORIGINAL_BASIS_EMIT_EXECUTION(http, run_id, record)
    except RuntimeError:
        gp = record.get("gemini_payload") or {}
        kp = record.get("kalshi_payload") or {}
        symbol = str(gp.get("symbol") or "")
        ticker = str(kp.get("ticker") or "")
        event_ticker = crypto._event_ticker_from_symbol(symbol)
        asset = crypto._asset_from_event_ticker(event_ticker)
        old_trade_id = f"gk15m-{run_id}"
        await campaign._basis_post_event(http, {
            "event_id": f"{old_trade_id}-realized-no-fill",
            "trade_id": old_trade_id,
            "event_type": "TRADE_REALIZED",
            "event_ts": crypto._iso(),
            "strategy": "gemini_kalshi_15m_crypto",
            "mode": "LIVE_PROBE",
            "market": f"{event_ticker}|{ticker}",
            "asset": asset,
            "venues": ["Gemini", "Kalshi"],
            "status": "NO_FILL_RELEASED",
            "quantity": "1",
            "amount": "0",
            "fee": "0",
            "gross_pnl": "0",
            "net_pnl": "0",
            "legs_required": 2,
            "legs_filled": 0,
            "metadata": {
                "execution_sequence": result.get("execution_sequence") or "KALSHI_THEN_GEMINI",
                "classification": "LIVE_BASIS_RISK_RESEARCH_NO_FILL_RELEASE",
                "no_fill": True,
                "new_pair_trade_authorized": False,
            },
        })
        logger.warning(
            "CRYPTO_BASIS_RISK_NO_FILL_RELEASED run_id=%s execution_sequence=%s",
            run_id,
            result.get("execution_sequence"),
        )
        raise


def install() -> None:
    campaign.DEFAULT_MIN_GAP_BPS = dynamic_policy.DYNAMIC_MIN_GAP_BPS
    child.MODE = child_v2.LEDGER_MODE
    campaign.MODE = child_v2.LEDGER_MODE
    campaign._obtain_basis_execution = _obtain_v6_execution
    campaign._basis_emit_execution = _basis_emit_execution_v6


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    # Install the v6 risk-aware reconciliation before the parent campaign checks the
    # ledger; fully paired open positions are allowed, unpaired/in-flight ones are not.
    child_v6.install()
    child_v2.assert_prior_ledger_reconciled()
    os.environ[child.BASIS_RECONCILED_ENV] = "YES"
    with child_v2._connect() as conn:
        state = child_v6.promoted_state(conn)
    allowed, reason = child_v6.automatic_rollover_allowed(state)
    if not allowed:
        raise PermissionError(f"v6 promoted basis campaign blocked before boot: {reason}")
    install()
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_CAMPAIGN_ENABLED ledger_mode=%s probe_limit=%s reservations=%s realized=%s "
        "open_paired=%s open_paired_reserved_usd=%s realized_loss_usd=%s remaining_loss_budget_usd=%s "
        "tranche_usd=%s execution_sequence=KALSHI_THEN_GEMINI max_open_paired=%s "
        "one_inflight_unpaired=true new_loss_hard_stop=true guaranteed_arb=false parent_min_gap_bps=%s",
        child_v2.LEDGER_MODE,
        child_v6.PROMOTED_PROBE_LIMIT,
        state["reservations"],
        state["realized"],
        state["open_paired_count"],
        state["open_paired_reserved_usd"],
        state["realized_loss_usd"],
        state["remaining_loss_budget_usd"],
        child.INITIAL_TRANCHE_USD,
        child_v6.MAX_OPEN_PAIRED_RISK,
        dynamic_policy.DYNAMIC_MIN_GAP_BPS,
    )
    campaign.main()


if __name__ == "__main__":
    main()
