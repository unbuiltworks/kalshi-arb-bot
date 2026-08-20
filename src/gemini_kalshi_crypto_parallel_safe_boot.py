"""Fail-closed boot wrapper for the globally coordinated parallel crypto canary."""
from __future__ import annotations

import os

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_parallel_campaign as p
from src import gemini_kalshi_crypto_parallel_campaign_safe as safe
from src.gemini_kalshi_crypto_parallel_boot import ensure_fresh_campaign_state


def main() -> None:
    campaign_id = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
    ensure_fresh_campaign_state(campaign_id, p.STATE_DIR)
    safe.main()


if __name__ == "__main__":
    main()
