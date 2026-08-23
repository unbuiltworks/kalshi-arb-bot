"""Stable Railway entrypoint for the production sports combo engine.

v45 installs the same binary opposite-bid ask normalization already used by the
same-market scanner, then runs the v44 diagnostic/seed chain. This fixes targeted
MVE discovery only; no live gate, fee floor, q=1 depth rule, RFQ acceptance, or
order-entry behavior is weakened. Generic high-volume creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v45 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
