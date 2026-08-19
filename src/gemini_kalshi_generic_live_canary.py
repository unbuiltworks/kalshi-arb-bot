"""Asset-selectable wrapper around the proven Gemini/Kalshi 15m canary transport.

This wrapper does not relax any execution gate. It only selects one supported asset and
maps generic canary environment names onto the existing persistent one-shot canary.
The underlying transport remains fail-closed and requires all existing live gates,
persistent arm state, exact price/depth revalidation, and one-contract limits.
"""
from __future__ import annotations

import os
import sys

from src import gemini_kalshi_sol_live_canary_v2 as canary

SUPPORTED_ASSETS = {"BTC", "ETH", "SOL", "XRP"}
ASSET_ENV = "CRYPTO_15M_CANARY_ASSET"
ARM_ENV = "CRYPTO_15M_CANARY_ARM"
RUN_ID_ENV = "CRYPTO_15M_CANARY_RUN_ID"


def _asset() -> str:
    asset = os.getenv(ASSET_ENV, "SOL").strip().upper()
    if asset not in SUPPORTED_ASSETS:
        raise ValueError(f"unsupported 15m canary asset: {asset}")
    return asset


def main() -> None:
    asset = _asset()

    # Preserve the proven v2 persistent authorization implementation while presenting
    # asset-neutral operator variables. Absence of ARM remains fail-closed.
    if RUN_ID_ENV in os.environ:
        os.environ[canary.RUN_ID_ENV] = os.environ[RUN_ID_ENV]
    if ARM_ENV in os.environ:
        os.environ[canary.ARM_ENV] = os.environ[ARM_ENV]

    # The underlying scanner is already asset-parameterized. Force exactly one asset so
    # one arm token can never fan out across multiple markets/coins.
    sys.argv = [sys.argv[0], "--assets", asset, *sys.argv[1:]]
    canary.main()


if __name__ == "__main__":
    main()
