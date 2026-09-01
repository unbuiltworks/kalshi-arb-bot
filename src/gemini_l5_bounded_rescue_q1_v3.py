"""Low-latency bounded rescue entry point for Gemini crypto-5m q1.

V2 proved the 5c FOK loss cap and exact-id cancellation gates live, but its first
canary also proved that waiting through profit/breakeven maker stages is too slow
for a volatile five-minute book. V3 keeps every V2 safety invariant while changing
one priority after the first authoritative fill:

    cancel exact resting sibling -> prove inactive/race-reconcile -> bounded FOK

There is no breakeven-maker repost before the emergency decision. The launcher
also shortens the pre-fill quote TTL so an unfilled pair is cancelled and rescanned
instead of remaining stale long enough to be picked off.

The first live V3 session also showed that a public market-data websocket timeout
can occur while the account is completely flat. Public candidate timeouts are
therefore fail-soft in V3: they are logged and treated as an idle scan. Private
account failures, mutation failures, residuals and execution exceptions remain
fail-closed.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from src import gemini_l5_pair_q1 as pair
from src import gemini_l5_bounded_rescue_q1 as v1
from src import gemini_l5_bounded_rescue_q1_v2 as v2
from src import gemini_l5_crypto5m_selector as selector

_ORIGINAL_SELECT_BEST = selector.select_best_candidate


async def _skip_breakeven_maker(*args: Any, **kwargs: Any) -> None:
    """V3 goes directly from exact-sibling cancellation to bounded FOK."""
    return None


async def _fail_soft_public_select(*args: Any, **kwargs: Any):
    """A public book read timeout is not an execution/account safety failure."""
    try:
        return await _ORIGINAL_SELECT_BEST(*args, **kwargs)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        print(
            "GEMINI_L5_DYNAMIC_Q1 "
            + json.dumps(
                {
                    "stage": "PUBLIC_SCAN_TIMEOUT",
                    "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "error_type": type(exc).__name__,
                    "private_account_read": False,
                    "order_entry": False,
                    "realized_pnl": None,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return None


def install() -> None:
    """Install V2 safety hooks, low-latency rescue, and fail-soft public scans."""
    v2.install()
    # Stage-1 wait is intentionally zero. V2 still cancels the exact sibling,
    # proves it inactive and performs the authoritative terminal-race check before
    # any FOK client ID can exist.
    v1.PROFIT_STAGE_SECONDS = 0.0
    v1.BREAKEVEN_STAGE_SECONDS = 0.0
    v2._post_breakeven_maker = _skip_breakeven_maker
    pair._dynamic_complement_rescue = v2._dynamic_complement_rescue
    selector.select_best_candidate = _fail_soft_public_select


def main() -> None:
    install()
    from src import gemini_l5_dynamic_q1 as owner

    owner.MATCHED_STATUSES = frozenset(
        set(owner.MATCHED_STATUSES) | {"MATCHED_BOUNDED_FOK"}
    )
    owner.main()


if __name__ == "__main__":
    main()
