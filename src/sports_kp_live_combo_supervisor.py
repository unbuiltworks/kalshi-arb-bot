"""Stable Railway entrypoint for the production sports combo engine.

v42 preserves the v41/v40 chain and adds a read-only scan of already-existing
Kalshi MVEs for the tighter scalar-safe NO cover: combo NO plus any one selected
component. New MVE creation remains separately controlled; the v42 lane itself
never creates markets, accepts RFQ quotes, or submits orders.
"""
from src.sports_kp_live_combo_supervisor_v42 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
