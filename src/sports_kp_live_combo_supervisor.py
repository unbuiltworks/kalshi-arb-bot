"""Stable Railway entrypoint for the production sports combo engine.

v36 preserves v35 leg-limit telemetry, v34 q5 shadow economics, v33 replay, v32
telemetry, and v31 hard-constraint pacing. It adds a shadow complete-partition
scanner for four 2-leg MVE corners (YY/YN/NY/NN). Live execution remains q=1 and
unchanged; partition execution is disabled pending rule/void certification.
"""
from src.sports_kp_live_combo_supervisor_v36 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
