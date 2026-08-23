"""Stable Railway entrypoint for the production sports combo engine.

v46 installs the binary opposite-bid ask normalization, emits one bounded public
MVE component field-shape diagnostic, then runs the v44 diagnostic/seed chain.
No live gate, fee floor, q=1 depth rule, RFQ acceptance, or order-entry behavior
is weakened. Generic high-volume creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v46 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
