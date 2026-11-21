"""Generic ``python -m src`` entrypoint = the Kalshi arb bot.

OWNER 5556354536 (2026-09-06): a Railway service with no explicit start
command fell through to this entrypoint and tried to start the order-
capable Kalshi bot (it crashed on the missing private key). Under a
managed runtime this module refuses to start unless the operator has
explicitly named it as the Kalshi bot service; local ``python -m src``
runs are unaffected.
"""
from __future__ import annotations

import os
import sys

MANAGED_RUNTIME_MARKERS = ("RAILWAY_SERVICE_NAME", "RAILWAY_ENVIRONMENT",
                           "RAILWAY_PROJECT_ID", "ECS_CONTAINER_METADATA_URI",
                           "KUBERNETES_SERVICE_HOST")
KALSHI_BOT_OPT_IN_ENV = "KALSHI_ARB_BOT_SERVICE"


def refuse_reason(env=os.environ) -> str | None:
    """Why the Kalshi bot must NOT start here (None = allowed)."""
    managed = [m for m in MANAGED_RUNTIME_MARKERS if env.get(m)]
    if managed and env.get(KALSHI_BOT_OPT_IN_ENV, "").strip() != "1":
        return (f"managed runtime detected ({', '.join(managed)}) and "
                f"{KALSHI_BOT_OPT_IN_ENV}=1 is not set: the generic "
                "src.__main__ entrypoint never starts the order-capable "
                "Kalshi bot by fall-through; give the service an explicit "
                "start command (see deploy/*/railway.json)")
    return None


def main() -> int:
    why = refuse_reason()
    if why:
        print("KALSHI_BOT_REFUSED " + why, flush=True)
        return 0                                # exit clean, never restart-loop
    from src.main import main as bot_main
    bot_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
