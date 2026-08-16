"""Historical prospective-proxy calibration for Gemini/Kalshi 15-minute crypto.

Replays the same settled cross-venue windows used by the corrected agreement study,
but replaces hindsight-only final Kaiko margin with information that would have been
observable before close: the most recent public Gemini spot trade at fixed T-minus
offsets. This estimates how much buffer a live spot proxy would need before it can be
used as a conservative basis-risk gate.

The analysis is read-only. It makes four series-scoped Kalshi settlement queries plus
paced public Gemini requests and never imports or authorizes order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import logging
import math
from typing import Any

import httpx

from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kaiko_historical_proxy")
SPOT_SYMBOL = {"BTC": "btcusd", "ETH": "ethusd", "SOL": "solusd", "XRP": "xrpusd"}
GEMINI_REST = "https://api.gemini.com/v1"
DEFAULT_OFFSETS_S = (60.0, 30.0, 10.0, 5.0, 2.0, 1.0, 0.0)
DEFAULT_THRESHOLDS_BPS = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)


def _dec(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _trade_ms(trade: dict[str, Any]) -> int | None:
    raw = trade.get("timestampms")
    try:
        if raw is not None:
            return int(raw)
        sec = trade.get("timestamp")
        return int(float(sec) * 1000) if sec is not None else None
    except (TypeError, ValueError):
        return None


async def _spot_trades(
    http: httpx.AsyncClient,
    *,
    asset: str,
    start_ms: int,
    max_attempts: int = 6,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch up to 500 public spot trades after start_ms with paced 429 recovery."""
    response: httpx.Response | None = None
    for attempt in range(max_attempts):
        response = await http.get(
            f"{GEMINI_REST}/trades/{SPOT_SYMBOL[asset]}",
            params={"timestamp": start_ms, "limit_trades": 500, "include_breaks": "false"},
        )
        if response.status_code != 429:
            response.raise_for_status()
            payload = response.json()
            rows = list(payload) if isinstance(payload, list) else []
            rows.sort(key=lambda row: _trade_ms(row) or 0)
            return rows, len(rows) >= 500
        retry = response.headers.get("Retry-After")
        try:
            delay = float(retry) if retry else min(4.0, 0.25 * (2 ** attempt))
        except (TypeError, ValueError):
            delay = min(4.0, 0.25 * (2 ** attempt))
        logger.warning(
            "GEMINI_HIST_PROXY_429 asset=%s attempt=%d sleep_s=%.3f",
            asset, attempt + 1, delay,
        )
        await asyncio.sleep(max(0.05, delay))
    assert response is not None
    response.raise_for_status()
    return [], False


def _last_trade_at_or_before(
    trades: list[dict[str, Any]], target_ms: int
) -> tuple[Decimal | None, int | None]:
    price: Decimal | None = None
    ts: int | None = None
    for trade in trades:
        t = _trade_ms(trade)
        if t is None or t > target_ms:
            break
        p = _dec(trade.get("price"))
        if p is None or p <= 0:
            continue
        price, ts = p, t
    return price, ts


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


async def run(
    *,
    hours: int,
    gemini_limit: int,
    offsets_s: tuple[float, ...],
    thresholds_bps: tuple[float, ...],
    pace_s: float,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
        agreements = match_settled_true_windows(gemini, kalshi, 30.0)

        rows: list[dict[str, Any]] = []
        for idx, agreement in enumerate(agreements):
            if agreement.gemini_strike in {None, 0.0} or agreement.gemini_settlement is None:
                continue
            expiry_ms = int(agreement.expiry.timestamp() * 1000)
            earliest_ms = int(expiry_ms - (max(offsets_s) + 10.0) * 1000)
            trades, capped = await _spot_trades(
                http,
                asset=agreement.asset,
                start_ms=earliest_ms,
            )
            strike = D(str(agreement.gemini_strike))
            settlement = D(str(agreement.gemini_settlement))
            proxy: dict[str, Any] = {}
            for offset_s in offsets_s:
                target_ms = int(expiry_ms - offset_s * 1000)
                price, trade_ms = _last_trade_at_or_before(trades, target_ms)
                key = str(offset_s).rstrip("0").rstrip(".") if offset_s else "0"
                if price is None or trade_ms is None:
                    proxy[key] = None
                    continue
                margin_bps = (price - strike) / strike * D("10000")
                error_bps = (price - settlement) / settlement * D("10000") if settlement != 0 else None
                proxy[key] = {
                    "spot_price": str(price),
                    "trade_ts_ms": trade_ms,
                    "trade_age_at_target_ms": target_ms - trade_ms,
                    "spot_margin_bps": float(margin_bps),
                    "spot_error_vs_kaiko_bps": float(error_bps) if error_bps is not None else None,
                    "proxy_outcome": "yes" if price > strike else "no",
                    "matches_gemini_outcome": (
                        (price > strike and agreement.gemini_outcome == "yes")
                        or (price <= strike and agreement.gemini_outcome == "no")
                    ),
                }
            row = {
                "asset": agreement.asset,
                "expiry_utc": agreement.expiry.isoformat(),
                "gemini_ticker": agreement.gemini_ticker,
                "kalshi_ticker": agreement.kalshi_ticker,
                "gemini_outcome": agreement.gemini_outcome,
                "kalshi_outcome": agreement.kalshi_outcome,
                "cross_venue_agrees": agreement.agrees,
                "kaiko_strike": agreement.gemini_strike,
                "kaiko_settlement": agreement.gemini_settlement,
                "kaiko_final_margin_bps": agreement.gemini_margin_bps,
                "spot_trade_response_capped_500": capped,
                "spot_trade_count": len(trades),
                "proxy": proxy,
            }
            rows.append(row)
            if not agreement.agrees:
                logger.info(
                    "GEMINI_HIST_PROXY_DISAGREEMENT %s",
                    json.dumps(row, separators=(",", ":"), default=str),
                )
            if pace_s > 0 and idx + 1 < len(agreements):
                await asyncio.sleep(pace_s)

    by_offset: dict[str, Any] = {}
    for offset_s in offsets_s:
        key = str(offset_s).rstrip("0").rstrip(".") if offset_s else "0"
        available = [r for r in rows if r["proxy"].get(key) is not None]
        errors = [abs(float(r["proxy"][key]["spot_error_vs_kaiko_bps"])) for r in available]
        ages = [float(r["proxy"][key]["trade_age_at_target_ms"]) for r in available]
        outcome_matches = sum(1 for r in available if r["proxy"][key]["matches_gemini_outcome"])
        mismatched_rows = [r for r in available if not r["cross_venue_agrees"]]
        mismatch_abs_margins = [abs(float(r["proxy"][key]["spot_margin_bps"])) for r in mismatched_rows]

        gates: dict[str, Any] = {}
        for threshold in thresholds_bps:
            eligible = [
                r for r in available
                if abs(float(r["proxy"][key]["spot_margin_bps"])) >= threshold
            ]
            mismatches = sum(1 for r in eligible if not r["cross_venue_agrees"])
            gates[str(threshold).rstrip("0").rstrip(".") if threshold else "0"] = {
                "min_abs_spot_margin_bps": threshold,
                "eligible_windows": len(eligible),
                "agreements": len(eligible) - mismatches,
                "disagreements": mismatches,
                "agreement_rate": ((len(eligible) - mismatches) / len(eligible)) if eligible else None,
            }

        by_offset[key] = {
            "seconds_before_close": offset_s,
            "proxy_windows_available": len(available),
            "proxy_outcome_matches_final_gemini": outcome_matches,
            "proxy_outcome_accuracy": (outcome_matches / len(available)) if available else None,
            "abs_spot_error_vs_kaiko_bps": {
                "p50": _quantile(errors, 0.50),
                "p90": _quantile(errors, 0.90),
                "p95": _quantile(errors, 0.95),
                "p99": _quantile(errors, 0.99),
                "max": max(errors) if errors else None,
            },
            "trade_age_at_target_ms": {
                "p50": _quantile(ages, 0.50),
                "p95": _quantile(ages, 0.95),
                "max": max(ages) if ages else None,
            },
            "cross_venue_disagreements_with_proxy": len(mismatched_rows),
            "max_abs_spot_margin_bps_among_cross_venue_disagreements": (
                max(mismatch_abs_margins) if mismatch_abs_margins else None
            ),
            "gates": gates,
        }

    capped = sum(1 for r in rows if r["spot_trade_response_capped_500"])
    summary = {
        "hours": hours,
        "matched_cross_venue_windows": len(agreements),
        "proxy_rows": len(rows),
        "raw_cross_venue_disagreements": sum(1 for r in rows if not r["cross_venue_agrees"]),
        "spot_trade_requests_capped_500": capped,
        "offsets": by_offset,
        "proxy": "GEMINI_PUBLIC_SPOT_LAST_TRADE",
        "official_live_kaiko": False,
        "interpretation": "PROSPECTIVE_PROXY_CALIBRATION_NOT_LIVE_RISK_CERTIFICATION",
        "read_only": True,
        "order_entry": False,
    }
    logger.info("GEMINI_HIST_PROXY_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    parser.add_argument("--pace-s", type=float, default=0.12)
    parser.add_argument("--offsets-s", nargs="+", type=float, default=list(DEFAULT_OFFSETS_S))
    parser.add_argument("--thresholds-bps", nargs="+", type=float, default=list(DEFAULT_THRESHOLDS_BPS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run(
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        offsets_s=tuple(args.offsets_s),
        thresholds_bps=tuple(args.thresholds_bps),
        pace_s=args.pace_s,
    ))
    print(json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
