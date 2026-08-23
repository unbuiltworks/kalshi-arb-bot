"""Stable Railway entrypoint for the production sports combo engine.

v54 adds a strict read-only Polymarket maker-first trigger census using the current
2026 US fee/rebate schedule, then runs the v53 same-venue Dutch suite and unchanged
v50 combo/MVE chain. No maker or taker orders are submitted by the new census;
generic high-volume MVE creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v54 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
