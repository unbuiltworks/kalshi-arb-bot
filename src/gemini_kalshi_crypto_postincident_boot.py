"""Post-incident boot for the q=1 parallel Gemini/Kalshi campaign.

Adds only two reliability layers before launching the existing safe parallel
supervisor:
1. confirmed emergency-flatten ledger accounting; and
2. a required, explicitly enabled fee-aware Kalshi hedge cushion that preserves the
   existing >=3c after-fee floor.

All existing q=1, boundary, freshness, skew, capital, reservation and hard-halt rules
remain unchanged.
"""
from __future__ import annotations

import os

from src import gemini_kalshi_crypto_campaign_child_reliable as child
from src import gemini_kalshi_crypto_parallel_safe_boot as safe_boot
from src import gemini_kalshi_incident_accounting as incident_accounting


def main() -> None:
    if os.getenv(child.HEDGE_CUSHION_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(
            "post-incident live campaign requires CRYPTO_KALSHI_HEDGE_CUSHION=YES"
        )
    incident_accounting.install()
    safe_boot.main()


if __name__ == "__main__":
    main()
