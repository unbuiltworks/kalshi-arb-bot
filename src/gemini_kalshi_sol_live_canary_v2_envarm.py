"""Railway launcher for the SOL v2 live canary.

Railway has intermittently launched the configured module while dropping trailing
Start Command arguments. This launcher makes the arm deadline and fixed canary
scanner arguments process-local, while preserving v2's fail-closed gates.
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
        "--assets", "SOL",
        "--seconds", "0",
        "--max-quote-skew-ms", "50",
        "--capital-limit", "1",
        "--min-profit", "0.03",
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/sol-live-canary-v2",
    ]
    v2.main()


if __name__ == "__main__":
    main()
