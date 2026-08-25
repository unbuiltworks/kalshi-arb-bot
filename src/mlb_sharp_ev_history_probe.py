"""Read-only proof that the MLB sharp-EV backtest data sources are usable.

This intentionally spends exactly one The Odds API historical featured-market
request (US / h2h = 10 credits when data is returned) and makes public Kalshi
historical-data reads. It places no orders and writes no trading state.

Success means all three prerequisites for the Aug. 28 backtest are real rather
than assumed:
  1. the configured Odds API key can read paid historical MLB snapshots;
  2. a settled Kalshi MLB market exposes historical 1-minute bid/ask candles;
  3. at least one Kalshi candle is timestamp-aligned to the sportsbook snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from typing import Any

import httpx

ODDS_URL = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

# Known settled game: Arizona at Philadelphia, Apr. 10 2026, 18:40 ET / 22:40Z.
# Probe one hour pregame so both the sharp-line snapshot and Kalshi book should exist.
PROBE_DATE = "2026-04-10T21:40:00Z"
KALSHI_TICKER = "KXMLBGAME-26APR101840AZPHI-AZ"
KALSHI_SERIES = "KXMLBGAME"


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def _moneyline_outcomes(event: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            out.append({
                "book": book.get("key") or book.get("title"),
                "last_update": market.get("last_update") or book.get("last_update"),
                "outcomes": [
                    {"name": row.get("name"), "price": row.get("price")}
                    for row in (market.get("outcomes") or [])
                ],
            })
    return out


def _matching_game(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        teams = {str(event.get("home_team") or "").lower(), str(event.get("away_team") or "").lower()}
        if any("arizona" in x for x in teams) and any("philadelphia" in x for x in teams):
            return event
    return None


def _price_close(side: Any) -> float | None:
    if not isinstance(side, dict):
        return None
    for key in ("close_dollars", "close"):
        raw = side.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


def main() -> None:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")

    result: dict[str, Any] = {
        "status": "STARTED",
        "read_only": True,
        "order_entry": False,
        "probe_date_requested": PROBE_DATE,
        "kalshi_ticker": KALSHI_TICKER,
    }

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as http:
            odds = http.get(
                ODDS_URL,
                params={
                    "apiKey": api_key,
                    "regions": "us",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                    "date": PROBE_DATE,
                },
            )
            result["odds_http_status"] = odds.status_code
            result["odds_quota"] = {
                "remaining": odds.headers.get("x-requests-remaining"),
                "used": odds.headers.get("x-requests-used"),
                "last_cost": odds.headers.get("x-requests-last"),
            }
            if odds.status_code != 200:
                result["status"] = "ODDS_HISTORY_FAILED"
                result["odds_error"] = odds.text[:500]
                print(json.dumps(result, sort_keys=True))
                raise SystemExit(2)

            payload = odds.json()
            snapshot_iso = str(payload.get("timestamp") or "")
            events = [x for x in (payload.get("data") or []) if isinstance(x, dict)]
            game = _matching_game(events)
            result["odds_snapshot_timestamp"] = snapshot_iso
            result["odds_event_count"] = len(events)
            result["odds_matching_game_found"] = game is not None
            if game is not None:
                result["odds_matching_game"] = {
                    "id": game.get("id"),
                    "home_team": game.get("home_team"),
                    "away_team": game.get("away_team"),
                    "commence_time": game.get("commence_time"),
                    "bookmaker_count": len(game.get("bookmakers") or []),
                    "moneylines": _moneyline_outcomes(game),
                }

            market = http.get(f"{KALSHI_BASE}/historical/markets/{KALSHI_TICKER}")
            result["kalshi_market_http_status"] = market.status_code
            if market.status_code != 200:
                result["status"] = "KALSHI_MARKET_FAILED"
                result["kalshi_market_error"] = market.text[:500]
                print(json.dumps(result, sort_keys=True))
                raise SystemExit(3)
            market_row = (market.json() or {}).get("market") or {}
            result["kalshi_market"] = {
                "ticker": market_row.get("ticker"),
                "event_ticker": market_row.get("event_ticker"),
                "status": market_row.get("status"),
                "result": market_row.get("result"),
                "occurrence_datetime": market_row.get("occurrence_datetime"),
                "settlement_ts": market_row.get("settlement_ts"),
                "title": market_row.get("title"),
                "yes_sub_title": market_row.get("yes_sub_title"),
            }

            if not snapshot_iso:
                result["status"] = "ODDS_SNAPSHOT_TIMESTAMP_MISSING"
                print(json.dumps(result, sort_keys=True))
                raise SystemExit(4)

            snapshot_ts = _ts(snapshot_iso)
            candles = http.get(
                f"{KALSHI_BASE}/historical/markets/{KALSHI_TICKER}/candlesticks",
                params={
                    "start_ts": snapshot_ts - 5 * 60,
                    "end_ts": snapshot_ts + 5 * 60,
                    "period_interval": 1,
                },
            )
            result["kalshi_candles_http_status"] = candles.status_code
            if candles.status_code != 200:
                result["status"] = "KALSHI_CANDLES_FAILED"
                result["kalshi_candles_error"] = candles.text[:500]
                print(json.dumps(result, sort_keys=True))
                raise SystemExit(5)

            candle_rows = [x for x in ((candles.json() or {}).get("candlesticks") or []) if isinstance(x, dict)]
            aligned = None
            aligned_delta = None
            for row in candle_rows:
                try:
                    end_ts = int(row.get("end_period_ts"))
                except (TypeError, ValueError):
                    continue
                delta = abs(end_ts - snapshot_ts)
                if aligned_delta is None or delta < aligned_delta:
                    aligned_delta = delta
                    aligned = row

            result["kalshi_candle_count_10m_window"] = len(candle_rows)
            result["alignment_delta_seconds"] = aligned_delta
            result["alignment_within_120s"] = aligned_delta is not None and aligned_delta <= 120
            if aligned is not None:
                result["aligned_kalshi_candle"] = {
                    "end_period_ts": aligned.get("end_period_ts"),
                    "yes_bid_close": _price_close(aligned.get("yes_bid")),
                    "yes_ask_close": _price_close(aligned.get("yes_ask")),
                    "trade_price_close": _price_close(aligned.get("price")),
                    "volume": aligned.get("volume_fp", aligned.get("volume")),
                }

            if game is None:
                result["status"] = "NO_MATCHING_ODDS_GAME"
                code = 6
            elif not result["alignment_within_120s"]:
                result["status"] = "NO_TIMESTAMP_ALIGNMENT"
                code = 7
            else:
                result["status"] = "HISTORY_PROBE_OK"
                code = 0

            print(json.dumps(result, sort_keys=True, default=str))
            raise SystemExit(code)
    except httpx.HTTPError as exc:
        result["status"] = "HTTP_ERROR"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(8)


if __name__ == "__main__":
    main()
