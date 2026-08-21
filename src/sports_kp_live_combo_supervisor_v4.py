"""Production sports combo supervisor with action-only Pushover notifications.

User preference: do not send startup, heartbeat, candidate, no-trade, or engine-error
Pushover messages.  The automatic combo lane should notify only when a trade is
actually confirmed filled.  Manual-arbitrage notifications are handled by the
separate manual sports alert service and are intentionally unaffected here.
"""
from __future__ import annotations

import os

# Preserve the generated-combo production defaults.
os.environ.setdefault("ENABLE_KALSHI_MVE_GENERATOR", "YES")
os.environ.setdefault("MVE_GENERATOR_MAX_CREATIONS_PER_RUN", "8")
os.environ.setdefault("MVE_GENERATOR_DAILY_CREATION_CAP", "600")
os.environ.setdefault("MVE_GENERATOR_EVERY_CYCLES", "45")
os.environ.setdefault("MVE_GENERATOR_MIN_LOCKED", "0.02")

from src import sports_kp_live_combo_supervisor_v2 as base  # noqa: E402

_original_notify = base._notify


async def _action_only_notify(title: str, message: str, *, priority: int = 0) -> bool:
    # Automatic combo lane: alert only after every leg is confirmed filled.
    # Everything else remains in Railway logs without buzzing the user.
    if title != "Sports q=1 FILLED":
        return False
    return await _original_notify(title, message, priority=priority)


base._notify = _action_only_notify
run = base.run
main = base.main


if __name__ == "__main__":
    main()
