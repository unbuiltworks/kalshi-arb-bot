"""Stable Railway entrypoint for the production sports combo engine.

v50 adds MVE-NO + exact public Polymarket component economics, then runs the
unchanged v49 exact-fee chain. Positive cross-venue economics remain shadow until
settlement rules are explicitly certified. No quote acceptance or order entry is
changed, and generic high-volume MVE creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v50 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
