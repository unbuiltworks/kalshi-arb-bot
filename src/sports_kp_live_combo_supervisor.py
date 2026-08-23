"""Stable Railway entrypoint for the production sports combo engine.

v52 adds strict read-only Polymarket same-venue nested full-game half-point total
and same-team spread Dutch-book scans, then runs the unchanged v50 combo/MVE
chain. The nested scanners are fail-closed on metric/team/scope to prevent
cross-stat false positives. No order-entry behavior is changed, and generic
high-volume MVE creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v52 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
