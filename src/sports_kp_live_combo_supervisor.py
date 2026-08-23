"""Stable Railway entrypoint for the production sports combo engine.

v53 adds strict read-only Polymarket same-venue nested full-game totals,
same-team spreads, and same-game/same-player/same-stat MLB prop threshold ladders,
then runs the unchanged v50 combo/MVE chain. The scanners are fail-closed on
canonical semantic identity. No order-entry behavior is changed, and generic
high-volume MVE creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v53 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
