"""Paced read-only Gemini/Kalshi settlement-agreement probe.

Matches the actual 15-minute measurement-window endpoint: Gemini ticker timestamp
against Kalshi ``close_time``. Kalshi ``expected_expiration_time`` is settlement
latency metadata, not the contract boundary.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import time

import httpx

from src.gemini_kalshi_fast_overlap import ASSET_ALIASES, KALSHI_REST, _gemini_meta
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_outcome, _gemini_settled, _kalshi_outcome
from src.gemini_kalshi_window_identity import kalshi_15m_asset, kalshi_window_end

logger = logging.getLogger("kalshi_arb.gemini_kalshi_settlement_probe")


async def _kalshi_settled_paced(http: httpx.AsyncClient, hours: int) -> list[dict]:
    rows: list[dict] = []
    cursor = ""
    page = 0
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    while True:
        params = {
            "status": "settled",
            "limit": 1000,
            "mve_filter": "exclude",
            "min_settled_ts": min_settled_ts,
        }
        if cursor:
            params["cursor"] = cursor

        response = None
        for attempt in range(6):
            response = await http.get(f"{KALSHI_REST}/markets", params=params)
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(8.0, 0.75 * (2 ** attempt))
            except ValueError:
                delay = min(8.0, 0.75 * (2 ** attempt))
            logger.warning("KALSHI_SETTLED_429 page=%s attempt=%s sleep_s=%.2f", page, attempt + 1, delay)
            await asyncio.sleep(delay)

        assert response is not None
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get("markets", []))
        cursor = str(payload.get("cursor") or "")
        page += 1
        if not cursor:
            return rows
        await asyncio.sleep(0.35)


def match_settled_true_windows(gemini: list[dict], kalshi: list[dict], max_skew_s: float) -> list[AgreementRow]:
    gemini_rows = []
    for event in gemini:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        asset, duration, end = meta
        if duration != 15 or asset not in ASSET_ALIASES:
            continue
        outcome, strike, settlement, margin_bps = _gemini_outcome(event)
        if outcome is not None:
            gemini_rows.append((event, asset, end, outcome, strike, settlement, margin_bps))

    kalshi_rows = []
    for market in kalshi:
        asset = kalshi_15m_asset(market)
        end = kalshi_window_end(market)
        outcome = _kalshi_outcome(market)
        if asset and end and outcome:
            kalshi_rows.append((market, asset, end, outcome))

    matched: list[AgreementRow] = []
    used: set[str] = set()
    for event, asset, g_end, g_out, strike, settlement, margin_bps in gemini_rows:
        candidates = []
        for market, k_asset, k_end, k_out in kalshi_rows:
            ticker = str(market.get("ticker") or "")
            if ticker in used or asset != k_asset:
                continue
            skew = abs((g_end - k_end).total_seconds())
            if skew <= max_skew_s:
                candidates.append((skew, market, k_out))
        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0])
        _, market, k_out = candidates[0]
        ticker = str(market.get("ticker") or "")
        used.add(ticker)
        matched.append(AgreementRow(
            asset=asset,
            expiry=g_end,
            gemini_ticker=str(event.get("ticker") or ""),
            kalshi_ticker=ticker,
            gemini_outcome=g_out,
            kalshi_outcome=k_out,
            agrees=(g_out == k_out),
            gemini_strike=strike,
            gemini_settlement=settlement,
            gemini_margin_bps=round(margin_bps, 4) if margin_bps is not None else None,
        ))
    matched.sort(key=lambda row: (row.expiry, row.asset))
    return matched


async def run(hours: int, gemini_limit: int, max_expiry_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )

    rows = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    disagreements = [row for row in rows if not row.agrees]
    by_asset: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_asset.setdefault(row.asset, {"matched": 0, "agreed": 0, "disagreed": 0})
        bucket["matched"] += 1
        bucket["agreed" if row.agrees else "disagreed"] += 1
    for row in disagreements:
        logger.info("GEMINI_KALSHI_DISAGREEMENT %s", json.dumps(row.as_dict(), separators=(",", ":")))
    logger.info("GEMINI_KALSHI_AGREEMENT_SUMMARY %s", json.dumps({
        "gemini_settled_events_scanned": len(gemini),
        "kalshi_settled_markets_scanned": len(kalshi),
        "matched_15m_windows": len(rows),
        "agreements": len(rows) - len(disagreements),
        "disagreements": len(disagreements),
        "agreement_rate": ((len(rows) - len(disagreements)) / len(rows)) if rows else None,
        "by_asset": by_asset,
        "hours": hours,
        "max_window_end_skew_s": max_expiry_skew_s,
        "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--gemini-limit", type=int, default=500)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=300.0,
        help="Keep the credential-free research container observable after logging the summary.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_expiry_skew_s))
    # Railway can discard stdout from a very short-lived successful one-shot before
    # its log API exposes the runtime stream. Flush, then keep this research-only
    # process alive briefly; no credentials or order path exist in this service.
    logging.shutdown()
    if args.hold_seconds > 0:
        time.sleep(args.hold_seconds)


if __name__ == "__main__":
    main()