"""Production combo supervisor v13: one-shot read-only Polymarket NFL diagnostic."""
from __future__ import annotations

import asyncio
import json

from src import sports_kp_live_combo_supervisor_v11 as v11
from src import sports_kp_live_combo_supervisor_v12 as v12
from src.sports_poly_pregame_nfl_diagnostic import run_diagnostic

logger = v11.logger


async def run() -> None:
    try:
        diag = await run_diagnostic(24)
        logger.warning("SPORTS_POLY_PREGAME_NFL_DIAGNOSTIC %s", json.dumps(diag, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.error("SPORTS_POLY_PREGAME_NFL_DIAGNOSTIC_ERROR %s", json.dumps({"type":type(exc).__name__,"detail":str(exc)[:300]}))
    await v12.run()


def main() -> None:
    import logging, os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
