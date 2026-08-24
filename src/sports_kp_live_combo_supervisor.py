"""Stable Railway entrypoint for the production sports combo engine.

v55 preserves the existing read-only maker-trigger census and adds an authenticated,
mutation-free q1 preview for the richest strict same-game/player/stat MLB threshold
pair before running the unchanged v53 Dutch/combo/MVE chain. No orders are created
by the new preflight.
"""
from src.sports_kp_live_combo_supervisor_v55 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
