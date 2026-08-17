"""Read-only CLI audit for exchange-backed nested auto recovery."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from decimal import Decimal
import json
import logging

from config.settings import API_KEY_ID
from src.kalshi_nested_auto_governor import GovernorLimits, ET
from src.kalshi_nested_exchange_recovery import AUTO_PREFIX, recover_from_exchange
from src.kalshi_nested_probe_transport import KalshiV2RestTransport

D = Decimal
logger = logging.getLogger("kalshi_arb.kalshi_nested_exchange_recovery_audit")


async def audit(*, lookback_seconds: int = 48 * 3600) -> dict:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    limits = GovernorLimits(
        max_quantity=D("1"),
        max_triggers_per_day=5,
        max_triggers_per_event=1,
        cooldown_seconds=60.0,
        max_seed_cash_risk_per_attempt=D("0.02"),
        max_seed_cash_risk_per_day=D("0.10"),
    )
    tx = KalshiV2RestTransport()
    try:
        recovery = await recover_from_exchange(
            tx,
            limits=limits,
            now=datetime.now(ET),
            prefix=AUTO_PREFIX,
            lookback_seconds=lookback_seconds,
        )
    finally:
        await tx.close()
    row = {
        "status": "EXCHANGE_RECOVERY_AUDIT",
        **recovery.as_dict(),
        "client_order_prefix": AUTO_PREFIX,
        "lookback_seconds": lookback_seconds,
        "actual_order_placed": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("NESTED_EXCHANGE_RECOVERY_AUDIT %s", json.dumps(row, separators=(",", ":"), default=str))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-seconds", type=int, default=48 * 3600)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    print(json.dumps(asyncio.run(audit(lookback_seconds=args.lookback_seconds)), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
