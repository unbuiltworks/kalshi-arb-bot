"""Railway launcher for the shared one-shot crypto v2 live canary.

Railway has intermittently launched the configured module while dropping trailing
Start Command arguments. This launcher makes the arm deadline and fixed canary
scanner arguments process-local, while preserving v2's fail-closed gates.

BTC, ETH, SOL, and XRP compete for the same persistent one-shot attempt claim. The
first asset that satisfies every live gate may consume the arm; the remaining assets
stay unable to submit after that claim is consumed. This broadens opportunity without
multiplying the number of authorized live attempts or the experiment budget.
"""
from __future__ import annotations

import os
import sys

from src import gemini_kalshi_sol_live_canary_v2 as v2

DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"


def main() -> None:
    deadline = os.getenv(DEADLINE_ENV, "").strip()
    if not deadline:
        raise RuntimeError(f"{DEADLINE_ENV} is required for live canary launcher")

    sys.argv = [
        sys.argv[0],
        "--arm-deadline-utc", deadline,
        "--assets", "BTC", "ETH", "SOL", "XRP",
        "--seconds", "0",
        "--max-quote-skew-ms", "50",
        "--capital-limit", "1",
        "--min-profit", "0.03",
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-live-canary-v2",
    ]
    v2.main()


if __name__ == "__main__":
    main()
