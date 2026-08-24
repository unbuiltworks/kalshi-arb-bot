"""Bounded XRP 15m maker first-fill supervisor.

Purpose: keep the tested Gemini-maker -> Kalshi-FOK engine in the market across
multiple 15-minute windows until we observe the first *actual* Gemini maker fill.
It then stops permanently for reconciliation, regardless of whether that fill is
hedged or flattened. This prevents a research canary from silently scaling risk.

Safety invariants inherited from the underlying triggered canary:
- q <= 1 and exactly one resting maker order at a time;
- atomic Gemini-triggered exact Kalshi book pairing (<=50ms skew, <=250ms age);
- threshold-dominance authorization;
- Command Center unresolved-exposure block + $1 reservation;
- existing $5 cumulative realized research-loss ceiling, never changed here;
- first execution freezes/cancels the maker remainder;
- exact observed fill quantity/fees rechecked before hedge;
- exact-quantity Kalshi FOK hedge only if actual fill still locks >=3c/contract;
- otherwise Gemini flatten + hard halt;
- successful two-leg fill is NOT realized until authoritative settlement.

Fillability policy:
- admission is optimized for a full q=1 maker fill. Fractional fills therefore carry
  explicitly bounded research risk and are handled by exact post-fill hedge/flatten;
- use sticky quote rounds to preserve queue priority;
- zero-fill rounds/campaigns may continue; ANY fill/ambiguity/hard-halt ends supervisor;
- fixed run-id/cycle trade IDs make an accidental redeploy of the same run fail closed;
- market rollover without a published next pair and exact-strike refdata propagation
  are zero-mutation wait states and never consume a durable cycle id.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
from typing import Any, Awaitable, Callable

from src import gemini_kalshi_xrp_maker_live_canary as canary
from src import gemini_kalshi_xrp_maker_reprice_campaign as reprice
from src import gemini_kalshi_xrp_maker_triggered_canary as triggered

D = Decimal
ADMISSION_QTY = D("1.00")
MAX_CYCLES = max(1, min(int(os.getenv("CRYPTO_MAKER_SUPERVISOR_MAX_CYCLES", "8")), 24))
CYCLE_PAUSE_SECONDS = max(1.0, min(float(os.getenv("CRYPTO_MAKER_SUPERVISOR_PAUSE_SECONDS", "5")), 30.0))
RUN_ID = os.getenv("CRYPTO_MAKER_SUPERVISOR_RUN_ID", "").strip()
ARM = os.getenv("CRYPTO_MAKER_SUPERVISOR_ARM", "").strip()
NO_PAIR_TEXT = "no viable XRP 15m pair beyond expiry cutoff"
TRANSIENT_GEOMETRY_BLOCKERS = {"EXACT_GEMINI_STRIKE_UNAVAILABLE"}

SAFE_CAMPAIGN_TERMINALS = {
    "MAX_REPRICE_ROUNDS_REACHED",
    "REPRICE_CAMPAIGN_TIME_LIMIT",
    "PAIR_TOO_CLOSE_TO_EXPIRY",
}


def emit(stage: str, **fields: Any) -> None:
    print(
        "CRYPTO_XRP_FIRST_FILL_SUPERVISOR "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _armed() -> bool:
    return bool(RUN_ID and ARM and RUN_ID == ARM and canary.live_armed())


def cycle_root(run_id: str, cycle: int) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in run_id)[:48]
    return f"{safe}-c{int(cycle):02d}"[:64]


def transient_zero_order_terminal(result: dict[str, Any]) -> tuple[bool, str]:
    if str(result.get("status") or "") != "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND":
        return False, ""
    if str(result.get("terminal_status") or "") != "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER":
        return False, ""
    terminal = result.get("terminal_result") if isinstance(result.get("terminal_result"), dict) else {}
    if bool(terminal.get("order_entry")):
        return False, ""
    geometry = terminal.get("geometry") if isinstance(terminal.get("geometry"), dict) else {}
    blocker = str(geometry.get("blocker") or "")
    return blocker in TRANSIENT_GEOMETRY_BLOCKERS, blocker


def terminal_requires_stop(result: dict[str, Any]) -> tuple[bool, str]:
    status = str(result.get("status") or "")
    if status == "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND":
        terminal = str(result.get("terminal_status") or "UNKNOWN_TERMINAL")
        return True, terminal
    if status in SAFE_CAMPAIGN_TERMINALS:
        return False, status
    if "DUPLICATE" in status:
        return True, status
    return True, status or "UNKNOWN_STATUS"


async def _run_after_rollover(
    run_fn: Callable[[], Awaitable[dict[str, Any]]],
    *,
    cycle: int,
    root_trade_id: str,
    armed: bool,
) -> dict[str, Any]:
    """Retry only zero-mutation pair/refdata boundary states with the same cycle id."""
    while True:
        try:
            result = await run_fn()
        except RuntimeError as exc:
            if NO_PAIR_TEXT not in str(exc):
                raise
            emit(
                "ROLLOVER_WAIT",
                cycle=cycle,
                root_trade_id=root_trade_id,
                armed=armed,
                status="NO_VIABLE_PAIR_YET",
                order_entry=False,
            )
            if not armed:
                return {"status": "NO_VIABLE_PAIR_YET", "order_entry": False, "realized": False}
            await asyncio.sleep(CYCLE_PAUSE_SECONDS)
            continue

        transient, blocker = transient_zero_order_terminal(result)
        if transient:
            emit(
                "REFDATA_WAIT",
                cycle=cycle,
                root_trade_id=root_trade_id,
                armed=armed,
                status="TRANSIENT_DOMINANCE_REFDATA_WAIT",
                blocker=blocker,
                order_entry=False,
            )
            if not armed:
                return result
            await asyncio.sleep(CYCLE_PAUSE_SECONDS)
            continue
        return result


async def run() -> dict[str, Any]:
    armed = _armed()
    out: dict[str, Any] = {
        "mode": "LIVE_FIRST_FILL_SUPERVISOR" if armed else "READ_ONLY_PREFLIGHT",
        "armed": armed,
        "run_id": RUN_ID,
        "admission_quantity": str(ADMISSION_QTY),
        "max_cycles": MAX_CYCLES,
        "guaranteed_arbitrage": False,
        "classification": "LIVE_FULL_Q1_ADMISSION_FRACTIONAL_AND_INDEX_RISK_NOT_GUARANTEED_ARBITRAGE",
        "first_actual_fill_stops_supervisor": True,
        "realized_loss_ceiling_changed": False,
        "realized": False,
        "order_entry": False,
    }
    if RUN_ID != ARM and (RUN_ID or ARM):
        out["status"] = "SUPERVISOR_ARM_MISMATCH"
        emit("RESULT", **out)
        return out

    original_qty = triggered.MIN_SAFE_FIRST_FILL
    original_trade_id = canary.trade_id
    triggered.MIN_SAFE_FIRST_FILL = ADMISSION_QTY
    cycles: list[dict[str, Any]] = []
    try:
        if not armed:
            result = await _run_after_rollover(
                reprice.run, cycle=0, root_trade_id="READ_ONLY", armed=False
            )
            out.update({
                "status": "READ_ONLY_SUPERVISOR_PREFLIGHT",
                "underlying_status": result.get("status"),
                "window_end_utc": result.get("window_end_utc"),
                "order_entry": bool(result.get("order_entry")),
            })
            emit("RESULT", **out)
            return out

        for cycle in range(1, MAX_CYCLES + 1):
            root = cycle_root(RUN_ID, cycle)
            canary.trade_id = lambda root=root: root
            result = await _run_after_rollover(
                reprice.run, cycle=cycle, root_trade_id=root, armed=True
            )

            row = {
                "cycle": cycle,
                "root_trade_id": root,
                "status": result.get("status"),
                "terminal_status": result.get("terminal_status"),
                "window_end_utc": result.get("window_end_utc"),
                "rounds_completed": result.get("rounds_completed"),
                "order_entry": bool(result.get("order_entry")),
            }
            cycles.append(row)
            out["order_entry"] = bool(out["order_entry"] or row["order_entry"])
            emit("CYCLE", **row)

            stop, reason = terminal_requires_stop(result)
            if stop:
                terminal_result = result.get("terminal_result") if isinstance(result.get("terminal_result"), dict) else {}
                out.update({
                    "status": "SUPERVISOR_STOPPED_ON_TERMINAL",
                    "stop_reason": reason,
                    "terminal_result": terminal_result,
                    "actual_fill_observed": bool(terminal_result.get("filled_quantity")),
                })
                break
            await asyncio.sleep(CYCLE_PAUSE_SECONDS)
        else:
            out["status"] = "MAX_SUPERVISOR_CYCLES_REACHED_ZERO_TERMINAL_FILL"

        out["cycles_completed"] = len(cycles)
        out["cycles"] = cycles
        out["realized"] = False
        emit("RESULT", **out)
        return out
    finally:
        triggered.MIN_SAFE_FIRST_FILL = original_qty
        canary.trade_id = original_trade_id


if __name__ == "__main__":
    asyncio.run(run())
