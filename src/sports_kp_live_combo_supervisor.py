"""Stable Railway entrypoint for the production sports combo engine.

v31 prioritizes mathematically constrained same-game spread/total structures and
quality-paces the finite MVE creation budget. All live q=1 execution and safety
gates remain unchanged.
"""
from src.sports_kp_live_combo_supervisor_v31 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
