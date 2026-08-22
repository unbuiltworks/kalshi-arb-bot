"""Stable Railway entrypoint for the production sports combo engine.

v40 preserves v39-v31 and adds a read-only signed-auth probe against the
Polymarket exchange combo/RFQ host. It logs only status/schema and performs no
writes or order entry. New partition lanes remain shadow-only.
"""
from src.sports_kp_live_combo_supervisor_v40 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
