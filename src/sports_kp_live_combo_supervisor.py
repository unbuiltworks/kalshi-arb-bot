"""Stable Railway entrypoint for the production sports combo engine.

v51 adds the strict read-only Polymarket same-venue nested full-game half-point
total Dutch-book scan, then runs the unchanged v50 combo/MVE chain. The nested
scanner is fail-closed on explicit full_game_total metadata to prevent cross-stat
false positives. No order-entry behavior is changed, and generic high-volume MVE
creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v51 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
