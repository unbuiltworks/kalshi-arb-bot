"""Stable Railway entrypoint for the production sports combo engine.

v35 preserves v34 q5 shadow economics, v33 replay, v32 telemetry, and v31
hard-constraint pacing. It adds a read-only startup probe of Kalshi sports MVE
collection size_max values so >5-leg support can be evaluated before any generator
or live-execution expansion. Live execution remains q=1 and 2-5 legs only.
"""
from src.sports_kp_live_combo_supervisor_v35 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
