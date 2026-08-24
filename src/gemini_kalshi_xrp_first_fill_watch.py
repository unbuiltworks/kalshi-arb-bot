"""Longer-lived wrapper for the bounded XRP first-fill supervisor.

Only zero-mutation market-availability/certification blockers are allowed to continue.
Any order entry with a fill, ambiguous cancel, hedge/flatten, duplicate, or hard halt
still stops the underlying supervisor immediately. Existing q=1, $1 reservation,
$5 realized research-loss ceiling, 50ms paired-book, and >=3c post-fill gates are
unchanged.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src import gemini_kalshi_xrp_first_fill_supervisor as supervisor

SAFE_ZERO_MUTATION_TERMINALS = {
    "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER",
    "NO_FILLABLE_PARTIAL_SAFE_AT_BID_WINDOW",
}

_ORIGINAL_TERMINAL_REQUIRES_STOP = supervisor.terminal_requires_stop


def terminal_requires_stop(result: dict[str, Any]) -> tuple[bool, str]:
    status = str(result.get("status") or "")
    if status == "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND":
        terminal = str(result.get("terminal_status") or "UNKNOWN_TERMINAL")
        terminal_result = result.get("terminal_result") if isinstance(result.get("terminal_result"), dict) else {}
        if (
            terminal in SAFE_ZERO_MUTATION_TERMINALS
            and not bool(terminal_result.get("order_entry"))
            and not bool(terminal_result.get("filled_quantity"))
            and not bool(terminal_result.get("realized"))
        ):
            return False, terminal
    return _ORIGINAL_TERMINAL_REQUIRES_STOP(result)


async def run() -> dict[str, Any]:
    original_terminal = supervisor.terminal_requires_stop
    original_cycles = supervisor.MAX_CYCLES
    original_pause = supervisor.CYCLE_PAUSE_SECONDS
    supervisor.terminal_requires_stop = terminal_requires_stop
    # Enough bounded observation to survive a full 15m rollover while still stopping
    # on the first actual fill or unsafe terminal state.
    supervisor.MAX_CYCLES = 24
    supervisor.CYCLE_PAUSE_SECONDS = 20.0
    try:
        return await supervisor.run()
    finally:
        supervisor.terminal_requires_stop = original_terminal
        supervisor.MAX_CYCLES = original_cycles
        supervisor.CYCLE_PAUSE_SECONDS = original_pause


if __name__ == "__main__":
    asyncio.run(run())
