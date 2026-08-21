"""Production entrypoint for generated 2-5 leg sports combo search.

The user explicitly authorized automatic combo generation.  This wrapper arms
market creation/lookup (not order entry) while preserving the existing live q=1
execution gates and every fail-closed check in supervisor_v2 / live_q1_2_5.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENABLE_KALSHI_MVE_GENERATOR", "YES")
os.environ.setdefault("MVE_GENERATOR_MAX_CREATIONS_PER_RUN", "8")
os.environ.setdefault("MVE_GENERATOR_DAILY_CREATION_CAP", "600")
os.environ.setdefault("MVE_GENERATOR_EVERY_CYCLES", "45")
os.environ.setdefault("MVE_GENERATOR_MIN_LOCKED", "0.02")

from src.sports_kp_live_combo_supervisor_v2 import main, run  # noqa: E402,F401


if __name__ == "__main__":
    main()
