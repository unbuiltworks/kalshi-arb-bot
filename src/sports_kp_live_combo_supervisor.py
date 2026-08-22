"""Stable Railway entrypoint for the production sports combo engine.

v39 preserves v38-v31 and adds a read-only Polymarket combo/RFQ authentication
and MLB outcome-symbol preflight. It requests no private values beyond internal
shape validation, logs no identifiers/balances, and performs no combo/RFQ writes
or order entry. New partition lanes remain shadow-only.
"""
from src.sports_kp_live_combo_supervisor_v39 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
