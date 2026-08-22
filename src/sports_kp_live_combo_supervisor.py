"""Stable Railway entrypoint for the production sports combo engine.

Runtime throughput currently supports a bounded 12-RFQ manufacturing pass while
all live q=1 execution and safety gates remain unchanged.
"""
from src.sports_kp_live_combo_supervisor_v28 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
