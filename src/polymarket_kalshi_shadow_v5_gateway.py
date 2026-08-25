"""Read-only v5 K/P census entrypoint with public Polymarket REST seeding.

Polymarket market websocket authentication is unchanged. Only the startup
REST book seed is redirected to the public gateway endpoint already used by
older read-only collectors in this repository. Temporary no-inventory states
are retried in-process so Railway restart policy does not create an API-hammering
crash loop. No order-entry transport is imported or added here.

This wrapper also attaches the pre-committed decision telemetry to every
rule-certified two-sided evaluation: >=1c/>=2c/>=3c after-fee bins, q1 depth,
quote skew, process latency, signal lifetime, and >=80ms realistic capture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import Any

import httpx

from src import polymarket_kalshi_shadow_v5 as v5
from src.eventing_books import EventingPolyBookStore
from src.kp_census_decision_stats import CensusDecisionStats, CensusObservation

logger = logging.getLogger("kalshi_arb.polymarket_kalshi_shadow_v5_gateway")
POLY_PUBLIC_BOOK_HOST = "https://gateway.polymarket.us"
POLY_PUBLIC_HEADERS = {
    "Origin": "https://polymarket.us",
    "User-Agent": "Mozilla/5.0 arb-census/1.0",
    "Connection": "close",
}
INVENTORY_RETRY_SECONDS = 60
_RETRYABLE_INVENTORY_PREFIXES = (
    "No strict Polymarket/Kalshi matches discovered",
    "No strict pairs have a current two-sided Polymarket book",
)

_DECISION_STATS = CensusDecisionStats()
_ORIGINAL_EVALUATE = v5._evaluate


async def _fetch_poly_book_public(
    http: httpx.AsyncClient,
    store: EventingPolyBookStore,
    slug: str,
    sem: asyncio.Semaphore,
) -> bool:
    url = f"{POLY_PUBLIC_BOOK_HOST}/v1/markets/{slug}/book"
    async with sem:
        try:
            resp = await http.get(url, headers=POLY_PUBLIC_HEADERS)
            resp.raise_for_status()
            return store.ingest(resp.json(), source="rest")
        except Exception as exc:
            logger.warning("Polymarket public seed failed market=%s err=%s", slug, exc)
            return False


def _retryable_inventory_gap(exc: BaseException) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc)
    return any(message.startswith(prefix) for prefix in _RETRYABLE_INVENTORY_PREFIXES)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _evaluate_with_decision_stats(
    trigger: Any,
    match: Any,
    poly: Any,
    kalshi: Any,
    **kwargs: Any,
) -> None:
    """Run authoritative v5 evaluation, then record census-only decision stats.

    The second pricing pass is CPU-only against the already-loaded books. It
    performs no venue requests and has no mutation/order path.
    """
    started_ns = time.perf_counter_ns()
    await _ORIGINAL_EVALUATE(trigger, match, poly, kalshi, **kwargs)

    max_quote_age_ms = int(kwargs["max_quote_age_ms"])
    now_ms = int(time.time() * 1000)
    pq = poly.quote(match.poly.slug, max_age_ms=max_quote_age_ms)
    kq = await kalshi.quote(match.kalshi.ticker, max_age_ms=max_quote_age_ms)
    pair_prefix = f"{match.poly.slug}|{match.kalshi.ticker}|"

    if pq is None or kq is None:
        for key in tuple(_DECISION_STATS.open_signals):
            if key.startswith(pair_prefix):
                _DECISION_STATS._close(key, now_ms)
        return

    plan = v5.evaluate_both_directions(
        match.poly.slug,
        match.kalshi.ticker,
        poly_yes_ask=pq.yes_ask,
        poly_yes_depth=pq.yes_depth,
        poly_no_ask=pq.no_ask,
        poly_no_depth=pq.no_depth,
        kalshi_yes_ask=kq.yes_ask,
        kalshi_yes_depth=kq.yes_depth,
        kalshi_no_ask=kq.no_ask,
        kalshi_no_depth=kq.no_depth,
        bankroll=kwargs["bankroll"],
        poly_fee_rate=kwargs["poly_rate"],
        kalshi_fee_rate=kwargs["kalshi_rate"],
    )

    if plan is None:
        for key in tuple(_DECISION_STATS.open_signals):
            if key.startswith(pair_prefix):
                _DECISION_STATS._close(key, now_ms)
        return

    row = plan.as_dict()
    qty = max(1.0, _num(row.get("qty"), 1.0))
    edge_per_contract = _num(row.get("profit")) / qty
    q1_depth = min(_num(row.get("poly_depth")), _num(row.get("kalshi_depth")))
    skew_ms = abs(int(pq.received_ms) - int(kq.received_ms))
    process_latency_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
    semantic_key = f"{match.poly.slug}|{match.kalshi.ticker}|{row.get('direction', 'unknown')}"
    source_quality = (
        "REST_SNAPSHOT_NOT_WS_LIFETIME"
        if getattr(kalshi, "transport", "ws") == "rest_batch"
        else "WS_EVENT_LIFETIME"
    )

    _DECISION_STATS.observe(
        CensusObservation(
            semantic_key=semantic_key,
            observed_ms=float(now_ms),
            net_edge_usd=edge_per_contract,
            q1_depth=q1_depth,
            quote_skew_ms=float(skew_ms),
            process_latency_ms=process_latency_ms,
            source_quality=source_quality,
        )
    )

    if edge_per_contract < 0.01:
        for key in tuple(_DECISION_STATS.open_signals):
            if key.startswith(pair_prefix) and key != semantic_key:
                _DECISION_STATS._close(key, now_ms)


async def _heartbeat_with_decision_stats(stats: Any, poly: Any, kalshi: Any, active: Any) -> None:
    while True:
        await asyncio.sleep(10)
        payload = stats.payload(poly=poly, kalshi=kalshi)
        payload["active_signals"] = len(active)
        payload["decision_census"] = _DECISION_STATS.summary()
        payload["kalshi_transport"] = getattr(kalshi, "transport", "ws")
        payload["read_only"] = True
        payload["order_entry"] = False
        logger.info("EVENT_SUMMARY %s", json.dumps(payload, separators=(",", ":")))


def _run_forever(args: argparse.Namespace) -> None:
    while True:
        try:
            asyncio.run(v5.run(args))
            return
        except Exception as exc:
            if not _retryable_inventory_gap(exc):
                raise
            logger.warning(
                "CENSUS_NO_INVENTORY_RETRY wait_s=%d reason=%s",
                INVENTORY_RETRY_SECONDS,
                exc,
            )
            time.sleep(INVENTORY_RETRY_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # v5 remains the authoritative event-driven scanner. Override only its
    # read-only startup seed helper and attach transport-free decision metrics.
    v5._fetch_poly_book = _fetch_poly_book_public
    v5._evaluate = _evaluate_with_decision_stats
    v5._heartbeat = _heartbeat_with_decision_stats
    _run_forever(v5.parse_args())


if __name__ == "__main__":
    main()
