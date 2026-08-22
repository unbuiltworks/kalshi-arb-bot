"""Stable Railway entrypoint for the production sports combo engine.

v38 preserves v37 same-market YES+NO scanning and the v36-v31 stack, and adds a
non-sensitive startup probe of the installed Polymarket US SDK method surface for
combo/RFQ integration. No private account data is requested or logged. Live
execution remains unchanged; partition lanes remain shadow-only.
"""
from src.sports_kp_live_combo_supervisor_v38 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
