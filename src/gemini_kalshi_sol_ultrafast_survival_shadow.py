"""Read-only ultrafast survivorship wrapper for SOL Gemini/Kalshi 15-minute research.

Reuses the existing opportunity matrix unchanged but probes exact original limits at
10/15/25/50/100ms. This measures whether 5bp+ SOL opportunities that disappear before
25ms are theoretically capturable with lower-latency infrastructure. It contains no
order transport and cannot authorize trading.
"""
from __future__ import annotations

import logging

from src import gemini_kalshi_opportunity_matrix_shadow as matrix

logger = logging.getLogger("kalshi_arb.gemini_kalshi_sol_ultrafast_survival_shadow")
FAST_DELAYS_MS = (10, 15, 25, 50, 100)


async def _probe_ultrafast(*, feed, plan, active_episodes, delays_ms=FAST_DELAYS_MS) -> None:
    await matrix._probe_matrix(
        feed=feed,
        plan=plan,
        active_episodes=active_episodes,
        delays_ms=delays_ms,
    )


def install() -> None:
    matrix.install()
    matrix.exact._probe = _probe_ultrafast


def main() -> None:
    install()
    logger.info(
        "CRYPTO_SOL_ULTRAFAST_SURVIVAL_ENABLED asset=SOL delays_ms=10,15,25,50,100 "
        "profit_floors=0.02,0.03 boundary_bps=5,10,15,20 "
        "trade_authorized=false read_only=true order_entry=false"
    )
    matrix.safe.main()


if __name__ == "__main__":
    main()
