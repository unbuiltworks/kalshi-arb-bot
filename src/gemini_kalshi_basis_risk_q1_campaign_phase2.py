"""Phase-2 basis-risk q1 campaign wrapper.

Not used by the production service unless its entrypoint is explicitly changed later.
"""
from __future__ import annotations

import logging

from src import gemini_kalshi_basis_risk_q1_campaign_v5 as campaign_v5
from src import gemini_kalshi_basis_risk_q1_phase2 as phase2


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    phase2.install()
    campaign_v5.main()


if __name__ == "__main__":
    main()
