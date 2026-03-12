"""
Kalshi arb bot entry point.

Usage:
    python -m src.main                # live mode (places real orders)
    python -m src.main --paper        # paper mode (logs trades, no real orders)
    python -m src.main --check        # just test API connectivity and exit
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

from config.markets import discover_markets
from config.settings import (
    LOG_FILE,
    MAX_FILL_WAIT_MS,
    MARKET_REFRESH_INTERVAL_S,
)
from src.auth import KalshiClient
from src.order_manager import OrderManager
from src.orderbook import OrderBookManager
from src.risk import RiskManager
from src.spread_scanner import SpreadScanner
from src.ws_client import KalshiWS

logger = logging.getLogger("kalshi_arb")

# ---------------------------------------------------------------------------
# Global paper-trading flag — importable by other modules
# ---------------------------------------------------------------------------
PAPER_MODE: bool = False


def setup_logging(level: str = "INFO") -> None:
    fmt = "%(asctime)s %(levelname)-8s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ]
    logging.basicConfig(level=getattr(logging, level), format=fmt, handlers=handlers)


async def check_connectivity() -> bool:
    """Verify we can authenticate and reach the Kalshi API."""
    client = KalshiClient()
    try:
        status = await client.get_exchange_status()
        logger.info("Exchange status: %s", status)
        return True
    except Exception as e:
        logger.error("Connectivity check failed: %s", e)
        return False
    finally:
        await client.close()


async def market_refresh_loop(
    client: KalshiClient,
    ws: KalshiWS,
    interval_s: int = MARKET_REFRESH_INTERVAL_S,
) -> None:
    """Periodically re-discover markets and subscribe to new ones."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            tickers = await discover_markets(client)
            await ws.subscribe(tickers)  # subscribe() deduplicates internally
        except Exception:
            logger.exception("Market refresh failed")


async def run_bot() -> None:
    """Main bot loop — wires all components and runs until interrupted."""
    if PAPER_MODE:
        logger.info("PAPER MODE — no real orders will be placed")
    else:
        logger.info("LIVE MODE — real orders will be placed")

    client = KalshiClient()
    ws = KalshiWS()

    # --- Verify connectivity ---
    try:
        status = await client.get_exchange_status()
        logger.info("Connected to exchange: %s", status)
    except Exception as e:
        logger.error("Startup failed — cannot reach exchange: %s", e)
        await client.close()
        return

    # --- Discover markets ---
    try:
        tickers = await discover_markets(client)
    except Exception as e:
        logger.error("Market discovery failed: %s", e)
        await client.close()
        return

    if not tickers:
        logger.error("No markets found — nothing to track. Exiting.")
        await client.close()
        return

    # --- Build pipeline ---
    # OrderBook manager: WS messages → local book state
    book_mgr = OrderBookManager()
    ws.on_orderbook_snapshot(book_mgr.handle_snapshot)
    ws.on_orderbook_delta(book_mgr.handle_delta)

    # Order manager: executes trades (or logs in paper mode)
    order_mgr = OrderManager(
        client=client,
        paper=PAPER_MODE,
        max_fill_wait_ms=MAX_FILL_WAIT_MS,
    )
    ws.on_fill(order_mgr.handle_fill)

    # Risk manager: gates signals before they reach order manager
    risk_mgr = RiskManager(order_mgr)
    order_mgr.on_leg_fail(risk_mgr.handle_leg_fail)

    # Spread scanner: book updates → arb detection → risk gate → execution
    scanner = SpreadScanner()
    scanner.on_signal(risk_mgr.check_and_forward)
    book_mgr.on_book_update(scanner.scan)

    logger.info(
        "Pipeline ready: WS → OrderBook → Scanner → Risk → OrderManager (%s)",
        "paper" if PAPER_MODE else "live",
    )

    # --- Graceful shutdown ---
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # --- Launch tasks ---
    ws_task = asyncio.create_task(ws.connect(market_tickers=tickers))
    refresh_task = asyncio.create_task(market_refresh_loop(client, ws))

    logger.info("Bot running. Press Ctrl+C to stop.")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # --- Teardown ---
    logger.info("Shutting down...")
    refresh_task.cancel()
    await ws.close()

    try:
        await ws_task
    except asyncio.CancelledError:
        pass
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass

    await client.close()
    order_mgr.clear_completed()
    logger.info("Shutdown complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kalshi single-market arb bot")
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Paper-trading mode: log what trades would execute without placing real orders",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Test API connectivity and exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    global PAPER_MODE
    args = parse_args()
    # CLI flag takes priority, then BOT_MODE env var, default to paper
    PAPER_MODE = args.paper or os.getenv("BOT_MODE", "paper").lower() != "live"

    setup_logging(args.log_level)

    if args.check:
        ok = asyncio.run(check_connectivity())
        sys.exit(0 if ok else 1)

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
