"""Stable Railway entrypoint for the production sports combo engine.

v49 adds exact centicent/series-aware fee rescoring for recent two-contract MVE
Dutch covers, including maker diagnostics. It then runs the unchanged v48 chain.
No live gate, q=1 depth rule, quote acceptance, or order-entry behavior is changed.
Generic high-volume creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v49 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
