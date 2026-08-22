"""Stable Railway entrypoint for the production sports combo engine.

v32 retains v31's mathematically constrained same-game spread/total prioritization
and quality pacing, and adds best-effort non-P&L RFQ cycle telemetry. All live q=1
execution and safety gates remain unchanged.
"""
from src.sports_kp_live_combo_supervisor_v32 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
