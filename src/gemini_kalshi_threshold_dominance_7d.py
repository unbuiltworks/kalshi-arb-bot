"""Seven-day public-data-only Gemini/Kalshi 15m dominance/basis study.

Queries only the exact Kalshi BTC/ETH/SOL/XRP 15-minute series so a longer study can
run with a handful of Kalshi requests instead of paging the entire exchange. Enriches
matched windows with threshold ordering and numeric terminal-value basis, then emits
both threshold-disagreement and directional basis-cushion summaries.

No credentials, account reads, order transports, live approvals, or execution paths.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from typing import Any

import httpx

from src.gemini_kalshi_basis_cushion_analyze import analyze_row, summarize as summarize_cushion
from src.gemini_kalshi_fast_overlap import KALSHI_REST, _gemini_meta
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import match_settled_true_windows
from src.gemini_kalshi_threshold_dominance_study import classify, summarize as summarize_threshold

logger = logging.getLogger("kalshi_arb.gemini_kalshi_threshold_dominance_7d")
D = Decimal
ASSETS = ("BTC", "ETH", "SOL", "XRP")
KALSHI_15M_SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}


async def _kalshi_series(
    http: httpx.AsyncClient,
    *,
    hours: int,
    series_ticker: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    while True:
        params: dict[str, Any] = {
            "status": "settled",
            "limit": 1000,
            "mve_filter": "exclude",
            "min_settled_ts": min_settled_ts,
            "series_ticker": series_ticker,
        }
        if cursor:
            params["cursor"] = cursor
        response: httpx.Response | None = None
        for attempt in range(6):
            response = await http.get(f"{KALSHI_REST}/markets", params=params)
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(8.0, 0.75 * (2 ** attempt))
            except ValueError:
                delay = min(8.0, 0.75 * (2 ** attempt))
            logger.warning(
                "CRYPTO_THRESHOLD_7D_KALSHI_429 series=%s attempt=%s sleep_s=%.2f",
                series_ticker,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
        assert response is not None
        response.raise_for_status()
        payload = response.json()
        rows.extend(row for row in payload.get("markets", []) if isinstance(row, dict))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return rows
        await asyncio.sleep(0.30)


def _decimal(value: Any) -> Decimal:
    try:
        return D(str(value))
    except Exception:
        return D("-1")


async def run(hours: int, gemini_limit: int, max_window_end_skew_s: float) -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        gemini_task = asyncio.create_task(_gemini_settled(http, gemini_limit))
        kalshi_tasks = {
            asset: asyncio.create_task(
                _kalshi_series(http, hours=hours, series_ticker=series)
            )
            for asset, series in KALSHI_15M_SERIES.items()
        }
        gemini = await gemini_task
        kalshi_by_asset = {asset: await task for asset, task in kalshi_tasks.items()}

    gemini_15m: list[tuple[dict[str, Any], tuple[str, int, datetime]]] = []
    for event in gemini:
        meta = _gemini_meta(event)
        if meta is not None and meta[1] == 15 and meta[0] in ASSETS:
            gemini_15m.append((event, meta))

    kalshi = [
        market
        for asset in ASSETS
        for market in kalshi_by_asset.get(asset, [])
    ]
    matched = match_settled_true_windows(gemini, kalshi, max_window_end_skew_s)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}

    enriched: list[dict[str, Any]] = []
    for row in matched:
        event = event_by_ticker.get(str(row.gemini_ticker))
        market = market_by_ticker.get(str(row.kalshi_ticker))
        if event is None or market is None:
            continue
        enriched.append(classify(row, event, market))

    analyzed = [analyze_row(row) for row in enriched]
    basis_outliers = sorted(
        [row for row in analyzed if row.get("evidence_complete")],
        key=lambda row: _decimal(row.get("terminal_basis_abs_bps")),
        reverse=True,
    )[:12]
    enriched_by_g = {str(row.get("gemini_ticker") or ""): row for row in enriched}
    for out in basis_outliers:
        raw = enriched_by_g.get(str(out.get("gemini_ticker") or ""), {})
        logger.warning(
            "CRYPTO_THRESHOLD_7D_BASIS_OUTLIER %s",
            json.dumps({
                "asset": out.get("asset"),
                "gemini_ticker": out.get("gemini_ticker"),
                "kalshi_ticker": out.get("kalshi_ticker"),
                "gemini_strike": raw.get("gemini_strike"),
                "kalshi_target": raw.get("kalshi_target"),
                "gemini_final": raw.get("gemini_settlement"),
                "kalshi_final": raw.get("kalshi_expiration_value"),
                "gemini_outcome": raw.get("gemini_outcome"),
                "kalshi_outcome": raw.get("kalshi_outcome"),
                "safe_direction": out.get("shared_value_safe_direction"),
                "terminal_basis_signed_bps": out.get("terminal_basis_signed_bps"),
                "terminal_basis_abs_bps": out.get("terminal_basis_abs_bps"),
                "adverse_terminal_basis_bps": out.get("adverse_terminal_basis_bps"),
                "threshold_gap_abs_bps": out.get("threshold_gap_abs_bps"),
                "adverse_basis_to_gap_ratio": out.get("adverse_basis_to_threshold_gap_ratio"),
                "terminal_cushion_sufficient": out.get("terminal_cushion_sufficient"),
                "cross_index_dominance_breached": out.get("cross_index_dominance_breached"),
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str),
        )

    threshold_summary = summarize_threshold(enriched)
    cushion_summary = summarize_cushion(enriched)
    coverage_start = min((meta[2] for _, meta in gemini_15m), default=None)
    coverage_end = max((meta[2] for _, meta in gemini_15m), default=None)
    samples = [
        {
            "ticker": str(event.get("ticker") or ""),
            "title": str(event.get("title") or ""),
            "series": event.get("series"),
            "asset": meta[0],
            "expiry_utc": meta[2].isoformat(),
        }
        for event, meta in gemini_15m[:8]
    ]
    payload = {
        "requested_hours": hours,
        "gemini_settled_events_scanned": len(gemini),
        "gemini_15m_candidates_in_returned_page": len(gemini_15m),
        "gemini_15m_coverage_start_utc": coverage_start.isoformat() if coverage_start else None,
        "gemini_15m_coverage_end_utc": coverage_end.isoformat() if coverage_end else None,
        "gemini_15m_event_samples": samples,
        "kalshi_exact_series_counts": {
            asset: len(kalshi_by_asset.get(asset, [])) for asset in ASSETS
        },
        "matched_15m_windows": len(enriched),
        "threshold_outcomes": threshold_summary,
        "directional_basis_cushion": cushion_summary,
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info(
        "CRYPTO_THRESHOLD_7D_SUMMARY %s",
        json.dumps(payload, separators=(",", ":"), default=str),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=168)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    parser.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        raise SystemExit("--hours must be 1..168")
    if not 100 <= args.gemini_limit <= 10000:
        raise SystemExit("--gemini-limit must be 100..10000")
    if not 0 <= args.max_window_end_skew_s <= 120:
        raise SystemExit("--max-window-end-skew-s must be 0..120")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTO_THRESHOLD_7D_STUDY_ENABLED assets=BTC,ETH,SOL,XRP "
        "kalshi_scope=exact_15m_series trade_authorized=false read_only=true order_entry=false"
    )
    asyncio.run(run(args.hours, args.gemini_limit, args.max_window_end_skew_s))


if __name__ == "__main__":
    main()
