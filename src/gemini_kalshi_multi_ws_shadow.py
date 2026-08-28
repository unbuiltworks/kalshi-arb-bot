"""Read-only concurrent Gemini/Kalshi 15-minute crypto WebSocket shadow collector.

Runs the already-tested real-time Gemini bookTicker + authenticated Kalshi
orderbook_delta path for BTC, ETH, SOL and XRP concurrently. Each asset writes a
separate JSONL file so analysis and failure isolation stay simple.

No order entry exists. Settlement remains INDEX_MISMATCH_RESEARCH_ONLY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any

import httpx

from config.settings import API_KEY_ID
from src.auth import load_private_key
from src.gemini_kalshi_aligned_watch import Pair, _discover_pairs
from src.gemini_kalshi_btc_ws_shadow import (
    _candidate_rows,
    _gemini_stream,
    _kalshi_stream,
    _raise_if_stream_failed,
)
from src.gemini_kalshi_execution_shadow import TopOfBook, evaluate_pair, gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata, fetch_series_fee_metadata

logger = logging.getLogger("kalshi_arb.gemini_kalshi_multi_ws_shadow")
ASSET_SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}

_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
_ASSET_RETRY_STAGGER_S = {"BTC": 0.0, "ETH": 1.0, "SOL": 2.0, "XRP": 3.0}


def _status_from_exception(exc: BaseException) -> int | None:
    """Best-effort status extraction without depending on one websockets version."""
    for obj in (exc, getattr(exc, "response", None)):
        if obj is None:
            continue
        for name in ("status_code", "status"):
            raw = getattr(obj, name, None)
            if raw is None:
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return None


def _is_transient_ws_error(exc: BaseException) -> bool:
    """Return True only for failures that are reasonable to retry read-only.

    Authentication, permission, malformed-message and programming failures remain
    fatal. Admission/rate-limit and temporary upstream failures are retried with
    bounded backoff so a 429 cannot turn a short data gap into a dead collector.
    """
    status = _status_from_exception(exc)
    if status is not None:
        return status in _TRANSIENT_HTTP_STATUSES

    text = str(exc).lower()
    transient_markers = (
        "http 429",
        "status code 429",
        "rate limit",
        "too many requests",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "temporarily unavailable",
        "service unavailable",
    )
    return any(marker in text for marker in transient_markers)


def _retry_backoff_s(attempt: int, asset: str) -> float:
    """Bounded exponential admission backoff with deterministic asset staggering."""
    if attempt < 0:
        raise ValueError("attempt must be nonnegative")
    normalized = asset.upper()
    if normalized not in ASSET_SERIES:
        raise ValueError(f"unsupported asset={asset}")
    base = min(60.0, 5.0 * (2 ** min(attempt, 4)))
    return min(60.0, base + _ASSET_RETRY_STAGGER_S[normalized])


class PairDiscoveryCache:
    """Share one paced REST discovery across all asset workers.

    The first multi-asset implementation let every asset call ``_discover_pairs``
    independently. Since that helper already queries all four Kalshi 15-minute
    series, four concurrent workers multiplied the same REST calls and could trip
    Kalshi's rate limit. This cache serializes discovery and reuses the result for
    a short TTL while the WebSocket collectors do the high-frequency work.
    """

    def __init__(self, ttl_s: float = 1.0) -> None:
        self.ttl_s = ttl_s
        self._lock = asyncio.Lock()
        self._fetched_monotonic = 0.0
        self._pairs: list[Pair] = []

    async def current_pair(self, http: httpx.AsyncClient, asset: str) -> Pair | None:
        async with self._lock:
            now_mono = time.monotonic()
            if not self._pairs or now_mono - self._fetched_monotonic >= self.ttl_s:
                self._pairs = await _discover_pairs(http, 30.0)
                self._fetched_monotonic = time.monotonic()

            now = time.time()
            pairs = [
                p for p in self._pairs
                if p.asset == asset and p.window_end.timestamp() > now
            ]
            return pairs[0] if pairs else None


async def _capture_pair(
    *,
    asset: str,
    pair: Pair,
    kalshi_fee,
    overall_deadline: float,
    output: Path,
    max_quote_skew_ms: int,
    stream_epoch: int = 0,
) -> tuple[int, int]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100000)
    tasks = {
        "gemini": asyncio.create_task(_gemini_stream(pair.gemini_symbol, queue)),
        "kalshi": asyncio.create_task(_kalshi_stream(pair.kalshi_ticker, queue)),
    }
    g: TopOfBook | None = None
    k: TopOfBook | None = None
    latest_k_exchange_ts: int | None = None
    samples = 0
    positive = 0
    unflushed = 0

    remaining_window_s = max(0.0, pair.window_end.timestamp() - time.time() + 0.75)
    pair_deadline = min(overall_deadline, time.monotonic() + remaining_window_s)

    try:
        with output.open("a", encoding="utf-8") as fh:
            while time.monotonic() < pair_deadline:
                _raise_if_stream_failed(tasks)
                timeout = max(0.05, min(0.5, pair_deadline - time.monotonic()))
                try:
                    venue, receive_ms, exchange_ms, top = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    _raise_if_stream_failed(tasks)
                    continue

                if venue == "gemini":
                    g = top
                else:
                    k = top
                    latest_k_exchange_ts = exchange_ms
                if g is None or k is None:
                    continue

                samples += 1
                candidates = evaluate_pair(
                    g,
                    k,
                    gemini_fee=gemini_predictions_taker_fee,
                    kalshi_fee=kalshi_fee,
                    max_quote_skew_ms=max_quote_skew_ms,
                )
                if candidates:
                    positive += 1

                row = {
                    "asset": asset,
                    "window_end_utc": pair.window_end.isoformat(),
                    "event_venue": venue,
                    "receive_ts_ms": receive_ms,
                    "gemini_receive_ts_ms": g.ts_ms,
                    "kalshi_receive_ts_ms": k.ts_ms,
                    "kalshi_exchange_ts_ms": latest_k_exchange_ts,
                    "kalshi_exchange_to_receive_ms": (
                        k.ts_ms - latest_k_exchange_ts
                        if latest_k_exchange_ts is not None
                        else None
                    ),
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "gemini_yes_bid": str(g.yes_bid),
                    "gemini_yes_bid_size": str(g.yes_bid_size),
                    "gemini_yes_ask": str(g.yes_ask),
                    "gemini_yes_ask_size": str(g.yes_ask_size),
                    "kalshi_yes_bid": str(k.yes_bid),
                    "kalshi_yes_bid_size": str(k.yes_bid_size),
                    "kalshi_yes_ask": str(k.yes_ask),
                    "kalshi_yes_ask_size": str(k.yes_ask_size),
                    "both_taker_fee_candidates": _candidate_rows(candidates),
                    "collector_stream_epoch": stream_epoch,
                    "collector_reconnected_after_gap": stream_epoch > 0,
                    "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                    "read_only": True,
                    "order_entry": False,
                }
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                unflushed += 1
                if unflushed >= 256:
                    fh.flush()
                    unflushed = 0
            fh.flush()
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    logger.info("MULTI_WS_WINDOW_SUMMARY %s", json.dumps({
        "asset": asset,
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "window_end_utc": pair.window_end.isoformat(),
        "samples": samples,
        "both_taker_fee_positive_event_samples": positive,
        "collector_stream_epoch": stream_epoch,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    return samples, positive


async def collect_asset(
    *,
    http: httpx.AsyncClient,
    discovery: PairDiscoveryCache,
    asset: str,
    seconds: float,
    output: Path,
    max_quote_skew_ms: int,
    fee_meta: KalshiSeriesFeeMetadata,
) -> dict:
    if asset not in ASSET_SERIES:
        raise ValueError(f"unsupported asset={asset}")
    output.parent.mkdir(parents=True, exist_ok=True)
    overall_deadline = time.monotonic() + seconds
    total_samples = 0
    total_positive = 0
    windows = 0
    transient_reconnects = 0
    retry_attempt = 0
    stream_epoch = 0
    kalshi_fee = fee_meta.taker_fee_fn()

    while time.monotonic() < overall_deadline:
        pair = await discovery.current_pair(http, asset)
        if pair is None:
            logger.info("MULTI_WS_WAITING_FOR_PAIR asset=%s", asset)
            await asyncio.sleep(min(2.0, max(0.0, overall_deadline - time.monotonic())))
            continue

        logger.info("MULTI_WS_DISCOVERY %s", json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "fee_type": fee_meta.fee_type,
            "fee_multiplier": fee_meta.fee_multiplier,
            "gemini_stream": "bookTicker",
            "collector_stream_epoch": stream_epoch,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

        try:
            samples, positive = await _capture_pair(
                asset=asset,
                pair=pair,
                kalshi_fee=kalshi_fee,
                overall_deadline=overall_deadline,
                output=output,
                max_quote_skew_ms=max_quote_skew_ms,
                stream_epoch=stream_epoch,
            )
        except Exception as exc:
            if not _is_transient_ws_error(exc):
                raise
            remaining = max(0.0, overall_deadline - time.monotonic())
            if remaining <= 0:
                break
            backoff_s = min(_retry_backoff_s(retry_attempt, asset), remaining)
            transient_reconnects += 1
            stream_epoch += 1
            logger.warning("MULTI_WS_TRANSIENT_RECONNECT %s", json.dumps({
                "asset": asset,
                "kalshi_ticker": pair.kalshi_ticker,
                "failure": type(exc).__name__,
                "failure_text": str(exc)[:300],
                "retry_attempt": retry_attempt,
                "backoff_s": backoff_s,
                "next_stream_epoch": stream_epoch,
                "research_data_gap_detected": True,
                "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
            retry_attempt += 1
            await asyncio.sleep(backoff_s)
            continue

        retry_attempt = 0
        windows += 1
        total_samples += samples
        total_positive += positive

    result = {
        "asset": asset,
        "windows_captured": windows,
        "samples": total_samples,
        "both_taker_fee_positive_event_samples": total_positive,
        "transient_ws_reconnects": transient_reconnects,
        "research_data_gap_detected": transient_reconnects > 0,
        "stream_epochs": stream_epoch + 1,
        "output": str(output),
        "gemini_stream": "bookTicker",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }
    logger.info("MULTI_WS_ASSET_SUMMARY %s", json.dumps(result, separators=(",", ":")))
    return result


async def collect_all(
    *,
    assets: list[str],
    seconds: float,
    output_dir: Path,
    max_quote_skew_ms: int,
    tag: str,
) -> list[dict]:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    load_private_key()

    normalized = [asset.upper() for asset in assets]
    unknown = [asset for asset in normalized if asset not in ASSET_SERIES]
    if unknown:
        raise ValueError(f"unsupported assets={unknown}")

    async with httpx.AsyncClient(timeout=20.0) as http:
        # Fetch fee metadata once per asset, sequentially, before starting workers.
        # Each metadata fetch uses two REST requests internally; pacing here avoids
        # a startup burst against the same Kalshi API used for pair discovery.
        fee_meta: dict[str, KalshiSeriesFeeMetadata] = {}
        for asset in normalized:
            fee_meta[asset] = await fetch_series_fee_metadata(http, ASSET_SERIES[asset])
            await asyncio.sleep(0.15)

        discovery = PairDiscoveryCache(ttl_s=1.0)
        tasks = []
        for asset in normalized:
            output = output_dir / f"gemini_kalshi_{asset.lower()}_bookticker_{tag}.jsonl"
            tasks.append(collect_asset(
                http=http,
                discovery=discovery,
                asset=asset,
                seconds=seconds,
                output=output,
                max_quote_skew_ms=max_quote_skew_ms,
                fee_meta=fee_meta[asset],
            ))
        results = await asyncio.gather(*tasks)

    logger.info("MULTI_WS_SUMMARY %s", json.dumps({
        "assets": normalized,
        "results": results,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--seconds", type=float, default=900.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path("logs"))
    parser.add_argument("--tag", default="multi")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(collect_all(
        assets=args.assets,
        seconds=args.seconds,
        output_dir=args.output_dir,
        max_quote_skew_ms=args.max_quote_skew_ms,
        tag=args.tag,
    ))


if __name__ == "__main__":
    main()
