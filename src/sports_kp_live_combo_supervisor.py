"""Stable Railway entrypoint for the production sports combo engine.

v44 runs a bounded read-only anchor/booster geometry diagnostic before the v43
chain. It creates no MVE, opens no RFQ, accepts no quote, and submits no order.
The targeted seed remains separately armed and generic high-volume MVE generation
remains disabled while we identify economically plausible constructions.
"""
from src.sports_kp_live_combo_supervisor_v44 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
