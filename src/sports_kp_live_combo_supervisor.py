"""Stable Railway entrypoint for the production sports combo engine.

v41 preserves v40-v31 and adds read-only minimal scalar-safe NO-cover scoring:
combo NO plus any one selected component has a guaranteed $1 floor.  The new
coverage direction remains shadow-only; existing live execution gates are unchanged.
"""
from src.sports_kp_live_combo_supervisor_v41 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
