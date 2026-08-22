"""Stable Railway entrypoint for the production sports combo engine.

v33 preserves v32 telemetry and v31 hard-constraint/quality-pacing logic, and adds
a bounded replay lane that re-RFQs already-created custom MVE tickers without
consuming new creation allowance. All live q=1 execution and safety gates remain
unchanged.
"""
from src.sports_kp_live_combo_supervisor_v33 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
