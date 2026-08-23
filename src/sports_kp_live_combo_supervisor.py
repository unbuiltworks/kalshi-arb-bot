"""Stable Railway entrypoint for the production sports combo engine.

v47 adds explicit read-only Dutch-book scans: nested monotone threshold covers and
a separately disarmed bounded four-corner MVE partition seed. It then runs the v46
field-shape/monotonicity chain. No live gate, fee floor, q=1 depth rule, quote
acceptance, or order-entry behavior is weakened. Generic high-volume creation
remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v47 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
