"""Stable Railway entrypoint for the production sports combo engine.

v51 adds read-only live Polymarket same-venue nested-total Dutch-book scanning,
then runs the unchanged v50 combo/MVE chain. No order-entry behavior is changed,
and generic high-volume MVE creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v51 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
