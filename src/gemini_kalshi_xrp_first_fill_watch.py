"""Longer-lived sticky wrapper for the bounded XRP first-fill supervisor.

Only zero-mutation market-availability/certification blockers are allowed to continue.
Any order entry with a fill, ambiguous cancel, hedge/flatten, duplicate, or hard halt
still stops the underlying supervisor immediately. Existing q=1, $1 reservation,
$5 realized research-loss ceiling, 50ms paired-book, and >=3c post-fill gates are
unchanged.

Fillability / notification policy:
- one maker order per campaign instead of four rapid cancel/repost rounds;
- an eligible maker may rest up to 240s to retain queue priority;
- only enter when the modeled full-q1 edge has a 1c cushion above the 3c hard floor;
- after entry, the underlying canary still cancels immediately if live economics fall
  below the original 3c floor or if any execution is observed;
- require >330s to expiry before a 240s rest, preserving the existing 90s no-new-risk
  cutoff even if the order survives its entire sticky rest;
- after a clean zero-fill cancel, pause before considering another order.

Gemini may generate its own venue-native order lifecycle notifications. This wrapper
reduces those notifications by reducing actual order churn; it does not attempt to
hide or suppress venue events.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import os
from typing import Any

from src import gemini_kalshi_xrp_first_fill_supervisor as supervisor
from src import gemini_kalshi_xrp_maker_reprice_campaign as reprice
from src import gemini_kalshi_xrp_maker_triggered_canary as triggered

D = Decimal
ENTRY_CUSHION = D(os.getenv("CRYPTO_MAKER_STICKY_ENTRY_CUSHION", "0.01"))
STICKY_REST_SECONDS = max(60.0, min(float(os.getenv("CRYPTO_MAKER_STICKY_REST_SECONDS", "240")), 600.0))
STICKY_CYCLE_PAUSE_SECONDS = max(5.0, min(float(os.getenv("CRYPTO_MAKER_STICKY_PAUSE_SECONDS", "20")), 60.0))
BASE_MIN_SECONDS_TO_EXPIRY = float(triggered.MIN_SECONDS_TO_EXPIRY)
STICKY_MIN_SECONDS_TO_EXPIRY = STICKY_REST_SECONDS + BASE_MIN_SECONDS_TO_EXPIRY

SAFE_ZERO_MUTATION_TERMINALS = {
    "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER",
    "NO_FILLABLE_PARTIAL_SAFE_AT_BID_WINDOW",
}

_ORIGINAL_TERMINAL_REQUIRES_STOP = supervisor.terminal_requires_stop
_ORIGINAL_FILLABLE_QUOTE = triggered.fillable_quote


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


def cushioned_fillable_quote(**kwargs: Any) -> dict[str, Any] | None:
    """Use a small admission cushion while preserving the 3c live/post-fill floor."""
    original_target = triggered.TARGET
    triggered.TARGET = original_target + ENTRY_CUSHION
    try:
        return _ORIGINAL_FILLABLE_QUOTE(**kwargs)
    finally:
        triggered.TARGET = original_target


async def run() -> dict[str, Any]:
    original_terminal = supervisor.terminal_requires_stop
    original_cycles = supervisor.MAX_CYCLES
    original_pause = supervisor.CYCLE_PAUSE_SECONDS
    original_rounds = reprice.MAX_ROUNDS
    original_campaign = reprice.CAMPAIGN_SECONDS
    original_rest = reprice.ROUND_REST_SECONDS
    original_expiry = triggered.MIN_SECONDS_TO_EXPIRY
    original_quote_fn = triggered.fillable_quote

    supervisor.terminal_requires_stop = terminal_requires_stop
    # Bounded first-fill observation across multiple windows. One actual fill or any
    # unsafe terminal state still stops the supervisor.
    supervisor.MAX_CYCLES = 24
    supervisor.CYCLE_PAUSE_SECONDS = STICKY_CYCLE_PAUSE_SECONDS

    # True sticky maker behavior: one order may hold queue priority for up to four
    # minutes instead of being canceled every 15-20 seconds.
    reprice.MAX_ROUNDS = 1
    reprice.ROUND_REST_SECONDS = STICKY_REST_SECONDS
    reprice.CAMPAIGN_SECONDS = max(300.0, STICKY_REST_SECONDS + 30.0)
    triggered.MIN_SECONDS_TO_EXPIRY = STICKY_MIN_SECONDS_TO_EXPIRY
    triggered.fillable_quote = cushioned_fillable_quote
    try:
        return await supervisor.run()
    finally:
        supervisor.terminal_requires_stop = original_terminal
        supervisor.MAX_CYCLES = original_cycles
        supervisor.CYCLE_PAUSE_SECONDS = original_pause
        reprice.MAX_ROUNDS = original_rounds
        reprice.CAMPAIGN_SECONDS = original_campaign
        reprice.ROUND_REST_SECONDS = original_rest
        triggered.MIN_SECONDS_TO_EXPIRY = original_expiry
        triggered.fillable_quote = original_quote_fn


if __name__ == "__main__":
    asyncio.run(run())