"""Stable Railway entrypoint for the production sports combo engine.

v34 preserves v33 custom-MVE replay, v32 telemetry, and v31 hard-constraint
quality pacing. It adds read-only q=5 replay RFQ economics with exact >=5 hedge
depth and quantity-rounded fees. Live execution remains q=1 only and all safety
gates are unchanged.
"""
from src.sports_kp_live_combo_supervisor_v34 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
