"""Stable Railway entrypoint for the production sports combo engine.

v43 adds an explicitly armed, at-most-four-market monotonicity seed probe before
the v42/v41/v40 shadow chain. The seed observes RFQs only: no quote acceptance,
no order submission, and no live promotion. Generic high-volume MVE generation
remains separately controlled and stays disabled while this targeted probe is
validated.
"""
from src.sports_kp_live_combo_supervisor_v43 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
