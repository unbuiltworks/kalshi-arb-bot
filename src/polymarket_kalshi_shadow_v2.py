"""Hardened read-only Polymarket US <-> Kalshi shadow scanner.

This wrapper reuses the strict MLB market matcher and fee/plan logic from
`polymarket_kalshi_shadow`, but hardens live book ingestion:

* seeds every Polymarket book from the documented public book endpoint,
* keeps a low-rate REST refresh as a fallback,
* accepts both direct and envelope-shaped Polymarket WebSocket market data,
* uses Kalshi's explicit two-scale order-book convention for reciprocal asks,
* reports fresh-book counts so a silent feed cannot look like "no arbs".

There is intentionally no order-entry path in this module.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
import signal
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src.kalshi_cross_book_v2 import KalshiCrossBooks
from src.polymarket_kalshi_shadow import (
    Match,
    OpportunityTracker,
    PolyQuote,
    best_plan,
    discover_matches,
)

logger = logging.getLogger("kalshi_arb.poly_kalshi_shadow_v2")
POLY_GATEWAY = "https://gateway.polymarket.us"


class PolyBookStore:
    def __init__(self) -> None:
        self._quotes: dict[str, PolyQuote] = {}
        self.connected = False
        self.ws_updates = 0
        self.rest_updates = 0
        self.last_ws_message_ms = 0

    @property
    def quote_count(self) -> int:
        return len(self._quotes)

    @staticmethod
    def _amount(value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get("value")
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _quantity(level: Any) -> Decimal | None:
        if not isinstance(level, dict):
            return None
        raw = level.get("qty", level.get("quantity"))
        try:
            return Decimal(str(raw))
        except Exception:
            return None

    def ingest(self, payload: Any, *, source: str) -> bool:
        if not isinstance(payload, dict):
            return False
        data = payload.get("marketData")
        if isinstance(data, dict):
            payload = data
        elif isinstance(payload.get("market_data"), dict):
            payload = payload["market_data"]

        slug = str(payload.get("marketSlug") or payload.get("market_slug") or "")
        bids = payload.get("bids") or []
        offers = payload.get("offers") or payload.get("asks") or []
        if not slug or not bids or not offers:
            return False

        best_bid = bids[0]
        best_offer = offers[0]
        if not isinstance(best_bid, dict) or not isinstance(best_offer, dict):
            return False
        bid_px = self._amount(best_bid.get("px"))
        ask_px = self._amount(best_offer.get("px"))
        bid_qty = self._quantity(best_bid)
        ask_qty = self._quantity(best_offer)
        if None in (bid_px, ask_px, bid_qty, ask_qty):
            return False
        assert bid_px is not None and ask_px is not None
        assert bid_qty is not None and ask_qty is not None

        # Defensive binary-market sanity checks.
        if not (Decimal("0") <= bid_px <= Decimal("1")):
            return False
        if not (Decimal("0") <= ask_px <= Decimal("1")):
            return False
        if bid_px > ask_px or bid_qty <= 0 or ask_qty <= 0:
            return False

        now = int(time.time() * 1000)
        state = str(payload.get("state") or payload.get("market_state") or "")
        self._quotes[slug] = PolyQuote(
            slug=slug,
            yes_ask=ask_px,
            yes_depth=ask_qty,
            no_ask=Decimal("1") - bid_px,
            no_depth=bid_qty,
            state=state,
            received_ms=now,
        )
        if source == "ws":
            self.ws_updates += 1
            self.last_ws_message_ms = now
        else:
            self.rest_updates += 1
        return True

    def quote(self, slug: str, *, max_age_ms: int = 15000) -> PolyQuote | None:
        quote = self._quotes.get(slug)
        if quote is None:
            return None
        if int(time.time() * 1000) - quote.received_ms > max_age_ms:
            return None
        if quote.state and quote.state not in {"MARKET_STATE_OPEN", "OPEN"}:
            return None
        return quote


async def _fetch_poly_book(
    http: httpx.AsyncClient,
    store: PolyBookStore,
    slug: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    async with semaphore:
        try:
            response = await http.get(f"{POLY_GATEWAY}/v1/markets/{slug}/book")
            response.raise_for_status()
            return store.ingest(response.json(), source="rest")
        except Exception as exc:
            logger.debug("Polymarket book refresh failed for %s: %s", slug, exc)
            return False


async def refresh_poly_books(
    store: PolyBookStore,
    slugs: list[str],
    *,
    interval_s: float = 10.0,
) -> None:
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=8.0) as http:
        while True:
            results = await asyncio.gather(
                *[_fetch_poly_book(http, store, slug, semaphore) for slug in slugs]
            )
            refreshed = sum(1 for ok in results if ok)
            logger.info(
                "POLY_REST_REFRESH %s",
                json.dumps(
                    {
                        "requested": len(slugs),
                        "refreshed": refreshed,
                        "stored": store.quote_count,
                    },
                    separators=(",", ":"),
                ),
            )
            await asyncio.sleep(interval_s)


async def run_poly_ws(client: PolymarketUS, store: PolyBookStore, slugs: list[str]) -> None:
    ws = client.ws.markets()
    ws.on("market_data", lambda data: store.ingest(data, source="ws"))
    ws.on("error", lambda err: logger.warning("Polymarket WS error: %s", err))
    await ws.connect()
    store.connected = True
    try:
        for i in range(0, len(slugs), 100):
            chunk = slugs[i : i + 100]
            await ws.subscribe(
                f"arb-books-{i // 100}",
                "SUBSCRIPTION_TYPE_MARKET_DATA",
                chunk,
            )
        logger.info("Polymarket WS subscribed to %d matched market(s)", len(slugs))
        while True:
            await asyncio.sleep(3600)
    finally:
        store.connected = False
        await ws.close()


@dataclass
class CycleStats:
    fresh_poly: int = 0
    fresh_kalshi: int = 0
    comparable: int = 0
    positive: int = 0


async def scan_loop(
    matches: list[Match],
    poly_store: PolyBookStore,
    kalshi_books: KalshiCrossBooks,
    *,
    bankroll: Decimal,
    poly_fee_rate: Decimal,
    kalshi_fee_rate: Decimal,
    scan_ms: int,
) -> None:
    tracker = OpportunityTracker()
    by_key = {m.key: m for m in matches}
    total_comparisons = 0
    total_positive = 0
    last_summary = 0.0
    try:
        while True:
            stats = CycleStats()
            best_gross: tuple[Decimal, Match, str] | None = None
            for match in matches:
                p = poly_store.quote(match.poly.slug)
                if p is not None:
                    stats.fresh_poly += 1
                k = await kalshi_books.quote(match.kalshi.ticker, max_age_ms=30000)
                if k is not None:
                    stats.fresh_kalshi += 1
                if p is None or k is None:
                    tracker.update(match, None)
                    continue

                stats.comparable += 1
                total_comparisons += 1
                plan = best_plan(
                    p,
                    k,
                    bankroll=bankroll,
                    poly_fee_rate=poly_fee_rate,
                    kalshi_fee_rate=kalshi_fee_rate,
                )
                if plan is not None:
                    stats.positive += 1
                    total_positive += 1
                tracker.update(match, plan)

                gross_a = Decimal("1") - (p.yes_ask + k.no_ask)
                gross_b = Decimal("1") - (k.yes_ask + p.no_ask)
                gross = max(gross_a, gross_b)
                direction = (
                    "POLY_OVER_KALSHI_UNDER"
                    if gross_a >= gross_b
                    else "KALSHI_OVER_POLY_UNDER"
                )
                if best_gross is None or gross > best_gross[0]:
                    best_gross = (gross, match, direction)

            now = time.monotonic()
            if now - last_summary >= 10:
                summary: dict[str, Any] = {
                    "matched": len(matches),
                    "poly_connected": poly_store.connected,
                    "poly_stored_books": poly_store.quote_count,
                    "poly_ws_updates": poly_store.ws_updates,
                    "poly_rest_updates": poly_store.rest_updates,
                    "kalshi_initialized_books": kalshi_books.initialized_count,
                    "fresh_poly_this_cycle": stats.fresh_poly,
                    "fresh_kalshi_this_cycle": stats.fresh_kalshi,
                    "comparable_this_cycle": stats.comparable,
                    "positive_this_cycle": stats.positive,
                    "comparisons_since_start": total_comparisons,
                    "positive_observations_since_start": total_positive,
                }
                if best_gross is not None:
                    summary.update({
                        "best_gross_gap": str(best_gross[0]),
                        "best_event": best_gross[1].poly.event_title,
                        "best_line": str(best_gross[1].poly.line),
                        "best_direction": best_gross[2],
                    })
                logger.info("SUMMARY %s", json.dumps(summary, separators=(",", ":")))
                last_summary = now
            await asyncio.sleep(scan_ms / 1000)
    finally:
        tracker.close_all(by_key)


async def main_async(args: argparse.Namespace) -> None:
    bankroll = Decimal(str(args.bankroll))
    poly_fee_rate = Decimal(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05"))
    kalshi_fee_rate = Decimal(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))
    logger.info(
        "START_V2 shadow_only=true bankroll=%s poly_taker_rate=%s kalshi_taker_rate=%s",
        bankroll,
        poly_fee_rate,
        kalshi_fee_rate,
    )

    async with httpx.AsyncClient(timeout=10.0) as http:
        matches = await discover_matches(http)
    if not matches:
        raise SystemExit("No unambiguous Polymarket/Kalshi MLB full-game total matches found")

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required")

    slugs = [m.poly.slug for m in matches]
    poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
    poly_store = PolyBookStore()
    kalshi_books = KalshiCrossBooks([m.kalshi.ticker for m in matches])

    # REST refresh starts immediately and seeds books while the WS connects.
    rest_task = asyncio.create_task(refresh_poly_books(poly_store, slugs))
    poly_task = asyncio.create_task(run_poly_ws(poly_client, poly_store, slugs))
    kalshi_task = asyncio.create_task(kalshi_books.run())
    scan_task = asyncio.create_task(scan_loop(
        matches,
        poly_store,
        kalshi_books,
        bankroll=bankroll,
        poly_fee_rate=poly_fee_rate,
        kalshi_fee_rate=kalshi_fee_rate,
        scan_ms=args.scan_ms,
    ))
    stop_task = asyncio.create_task(_wait_for_stop())

    tasks = {rest_task, poly_task, kalshi_task, scan_task, stop_task}
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc

    for task in tasks:
        task.cancel()
    await kalshi_books.close()
    try:
        poly_client.close()
    except Exception:
        pass
    await asyncio.gather(*tasks, return_exceptions=True)


async def _wait_for_stop() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hardened Polymarket US <-> Kalshi read-only shadow scanner"
    )
    parser.add_argument("--bankroll", default="50", help="shadow capital ceiling; no orders are placed")
    parser.add_argument("--scan-ms", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
