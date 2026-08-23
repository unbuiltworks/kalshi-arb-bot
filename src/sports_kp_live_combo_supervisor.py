"""Stable Railway entrypoint for the production sports combo engine.

v48 adds direct hydration of recently manufactured active MVEs so the two-contract
minimal-NO Dutch-book identity is scored despite Kalshi's historical multivariate
catalog pagination. It then runs the unchanged v47 Dutch-book chain. No live gate,
fee floor, q=1 depth rule, quote acceptance, or order-entry behavior is weakened.
Generic high-volume creation remains disabled.
"""
from src.sports_kp_live_combo_supervisor_v48 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
