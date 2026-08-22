"""Stable Railway entrypoint for the production sports combo engine.

Runtime throughput supports a bounded 12-RFQ manufacturing pass with side-aware
feedback and v29 prop-heavy three-leg prioritization. All live q=1 execution and
safety gates remain unchanged.
"""
from src.sports_kp_live_combo_supervisor_v29 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
