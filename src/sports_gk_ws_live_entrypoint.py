"""Authoritative G/K live supervisor production policy entrypoint.

Keeps the explicitly authorized cumulative sports ceiling at $20, the fractional-
fill-safe q=1 mutation transport, and the production REST market-data fallback.
Read-only revenue monitors are paced so they cannot authorize orders or monopolize
venue request capacity.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import logging
import os

import src.sports_live_budget_guard as budget_guard
import src.sports_gk_autonomous_supervisor as supervisor
from src.kalshi_prop_rest_sync_books import KalshiPropRestSyncBooks
from src.kalshi_prop_ws_exact_books import KalshiPropWsExactBooks
from src.gemini_kalshi_probe_transport_fractional_safe import submit_dual_probe_fractional_safe
from src.sports_gk_funnel_instrumentation import install as install_funnel
from src.sports_gk_general_inventory import run_once as run_general_inventory
from src.sports_nfl_gk_diagnostic import run_once as run_nfl_shape_diagnostic
from src.three_venue_mlb_price_scan import run_once as run_mlb_game_price_scan

D = Decimal
AUTHORIZED_CUMULATIVE_SPORTS_BUDGET = D("20")
WS_ENV = "SPORTS_KALSHI_WS_FIRST"


def install() -> str:
    budget_guard.CUMULATIVE_BUDGET = AUTHORIZED_CUMULATIVE_SPORTS_BUDGET
    supervisor.CUMULATIVE_BUDGET = AUTHORIZED_CUMULATIVE_SPORTS_BUDGET
    ws_enabled = os.getenv(WS_ENV, "NO").strip().upper() == "YES"
    supervisor.KalshiPropRestBooks = KalshiPropWsExactBooks if ws_enabled else KalshiPropRestSyncBooks
    original_install_families = supervisor._install_unified_families
    def install_families_with_funnel() -> None:
        original_install_families(); install_funnel(supervisor.canary)
    supervisor._install_unified_families = install_families_with_funnel
    supervisor.canary.submit_dual_probe = submit_dual_probe_fractional_safe
    return "AUTHENTICATED_WS_DELTA_PLUS_CANDIDATE_EXACT_REST" if ws_enabled else "REST_SERIES_PLUS_BOUNDED_GEMINI_TRIGGERED_EXACT_PLUS_FINAL_EXACT"


async def _inventory_monitor() -> None:
    log = logging.getLogger("kalshi_arb.sports_gk_ws_live_entrypoint")
    try:
        await asyncio.wait_for(run_nfl_shape_diagnostic(), timeout=30.0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("SPORTS_NFL_GK_SHAPE_DIAGNOSTIC_ERROR type=%s", type(exc).__name__)
    while True:
        try:
            await asyncio.wait_for(run_general_inventory(), timeout=30.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("SPORTS_GK_GENERAL_INVENTORY_ERROR type=%s", type(exc).__name__)
        try:
            await asyncio.wait_for(run_mlb_game_price_scan(), timeout=90.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("SPORTS_GK_MLB_GAME_PRICE_SCAN_ERROR type=%s", type(exc).__name__)
        await asyncio.sleep(600.0)


async def _run(*, epoch_seconds: float, cooldown_seconds: float) -> None:
    monitor = asyncio.create_task(_inventory_monitor())
    try:
        await supervisor.run_forever(epoch_seconds=epoch_seconds, cooldown_seconds=cooldown_seconds)
    finally:
        monitor.cancel(); await asyncio.gather(monitor, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--epoch-seconds", type=float, default=600.0); parser.add_argument("--cooldown-seconds", type=float, default=15.0); args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    transport = install(); epoch_seconds = max(30.0, args.epoch_seconds); cooldown_seconds = max(5.0, args.cooldown_seconds)
    logging.getLogger("kalshi_arb.sports_gk_ws_live_entrypoint").warning(
        "SPORTS_GK_LIVE_POLICY cumulative_budget_usd=%s kalshi_transport=%s ws_opt_in=%s fractional_fill_policy=EXACT_QUANTITY_FLATTEN_AND_HALT funnel_telemetry=true revenue_monitors_read_only=true monitor_interval_seconds=600 epoch_seconds=%s cooldown_seconds=%s",
        AUTHORIZED_CUMULATIVE_SPORTS_BUDGET, transport, os.getenv(WS_ENV, "NO").strip().upper() == "YES", epoch_seconds, cooldown_seconds,
    )
    asyncio.run(_run(epoch_seconds=epoch_seconds, cooldown_seconds=cooldown_seconds))


if __name__ == "__main__": main()
