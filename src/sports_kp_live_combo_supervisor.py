"""Stable Railway entrypoint for the production sports combo engine.

v37 preserves v36 complete-partition shadow scanning, v35 leg-limit telemetry,
v34 q5 shadow economics, v33 replay, v32 telemetry, and v31 hard-constraint
pacing. It adds a dedicated read-only same-Kalshi-market YES+NO Dutch-book scan,
including derived executable asks from opposite bids when list responses omit asks.
Live execution remains q=1 and unchanged; new partition lanes are shadow-only.
"""
from src.sports_kp_live_combo_supervisor_v37 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
