"""Stable Railway entrypoint for the production sports combo engine.

Runtime throughput supports a bounded 12-RFQ manufacturing pass with side-aware
feedback, prop-heavy three-leg prioritization, and pairwise GAME+SPREAD redundancy
filtering. All live q=1 execution and safety gates remain unchanged.
"""
from src.sports_kp_live_combo_supervisor_v30 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
