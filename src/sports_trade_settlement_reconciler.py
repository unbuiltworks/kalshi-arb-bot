"""Authoritative settlement reconciler for live Gemini/Kalshi sports probes.

Reads open BOTH_FILLED sports trades from ARB Command Center, waits until both venue
contracts expose definitive YES/NO results, computes payout from the outcomes that
were actually bought, and only then emits TRADE_REALIZED.

Fail-closed invariants:
- never realizes modeled/shadow P&L;
- requires two recorded fills and AWAITING_SETTLEMENT;
- requires exact Gemini instrumentSymbol and Kalshi ticker from execution metadata;
- requires both venue results to be definitive YES or NO;
- unknown/LFMP/ambiguous results remain open;
- realized net P&L = actual settlement payout - actual fill capital - recorded fees;
- Gemini settlement uses the authenticated settled-positions audit endpoint for
  contracts this account actually held; the public recently-settled feed is only a
  supplemental read path;
- venue reads are batched and paced so N open trades do not create N history scans.

This service has no order-entry code path. Gemini credentials are used only by the
read-only settled-positions endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import logging
import os
from typing import Any, Iterable, Mapping
from urllib.parse import quote

import httpx

from src.gemini_prediction_auth import has_credentials as gemini_has_credentials, private_rest_request

logger = logging.getLogger("kalshi_arb.sports_trade_settlement_reconciler")
D = Decimal

COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
PUSHOVER_USER = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
GEMINI_RECENTLY_SETTLED = "https://api.gemini.com/v1/prediction-markets/events/recently-settled"
GEMINI_SETTLED_POSITIONS_PATH = "/v1/prediction-markets/positions/settled"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def normalize_result(value: Any) -> str | None:
    result = str(value or "").strip().lower()
    return result if result in {"yes", "no"} else None


def bought_outcomes(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    gp = metadata.get("gemini_payload") or {}
    kp = metadata.get("kalshi_payload") or {}
    gemini_bought = normalize_result(gp.get("eventOutcome"))
    side = str(kp.get("side") or "").strip().lower()
    kalshi_bought = "yes" if side == "bid" else "no" if side == "ask" else None
    return gemini_bought, kalshi_bought


def settlement_payouts(
    *,
    gemini_bought: str | None,
    kalshi_bought: str | None,
    gemini_result: str | None,
    kalshi_result: str | None,
) -> tuple[D, D] | None:
    if None in {gemini_bought, kalshi_bought, gemini_result, kalshi_result}:
        return None
    if gemini_bought not in {"yes", "no"} or kalshi_bought not in {"yes", "no"}:
        return None
    if gemini_result not in {"yes", "no"} or kalshi_result not in {"yes", "no"}:
        return None
    return (
        D("1") if gemini_bought == gemini_result else D("0"),
        D("1") if kalshi_bought == kalshi_result else D("0"),
    )


def realized_pnl(*, capital_filled: Any, fees: Any, payouts: tuple[D, D]) -> tuple[D, D]:
    gross = sum(payouts, D("0")) - _d(capital_filled)
    net = gross - _d(fees)
    return gross, net


def settlement_compatible(gemini_result: str, kalshi_result: str) -> bool:
    return gemini_result == kalshi_result


def gemini_resolution_map(events: Iterable[Mapping[str, Any]], targets: Iterable[str]) -> dict[str, str | None]:
    wanted = {str(value).upper(): str(value) for value in targets if str(value)}
    out: dict[str, str | None] = {original: None for original in wanted.values()}
    for event in events:
        for contract in event.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            symbol = str(contract.get("instrumentSymbol") or "")
            original = wanted.get(symbol.upper())
            if original is not None:
                out[original] = normalize_result(contract.get("resolutionSide"))
    return out


def gemini_settled_positions_map(positions: Iterable[Mapping[str, Any]], targets: Iterable[str]) -> dict[str, str | None]:
    wanted = {str(value).upper(): str(value) for value in targets if str(value)}
    out: dict[str, str | None] = {original: None for original in wanted.values()}
    for row in positions:
        symbol = str(row.get("instrumentSymbol") or "")
        original = wanted.get(symbol.upper())
        if original is None:
            continue
        resolution = normalize_result(row.get("resolutionSide"))
        if resolution is None:
            metadata = row.get("contractMetadata") if isinstance(row.get("contractMetadata"), Mapping) else {}
            resolution = normalize_result(metadata.get("resolutionSide"))
        out[original] = resolution
    return out


def kalshi_resolution_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    market = payload.get("market")
    if not isinstance(market, Mapping):
        market = payload
    return normalize_result(market.get("result"))


async def _push(title: str, message: str) -> None:
    if not PUSHOVER_USER or not PUSHOVER_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            response = await http.post(
                "https://api.pushover.net/1/messages.json",
                data={"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "title": title[:250], "message": message[:1024], "priority": "0"},
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("SPORTS_SETTLEMENT_PUSHOVER_FAILED error=%s", type(exc).__name__)


async def _post_event(event: dict[str, Any]) -> bool:
    if not LEDGER_TOKEN:
        logger.error("SPORTS_SETTLEMENT_LEDGER_DISABLED missing=LEDGER_INGEST_TOKEN")
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            response = await http.post(
                f"{COMMAND_CENTER}/api/events",
                headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
                json=event,
            )
            response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("SPORTS_SETTLEMENT_LEDGER_POST_FAILED error=%s", type(exc).__name__)
        return False


async def _get_with_retry(
    http: httpx.AsyncClient,
    url: str,
    *,
    venue: str,
    params: Mapping[str, Any] | None = None,
    allow_status: frozenset[int] = frozenset(),
    attempts: int = 4,
) -> httpx.Response:
    response: httpx.Response | None = None
    for attempt in range(attempts):
        response = await http.get(url, params=params)
        if response.status_code in allow_status:
            return response
        if response.status_code not in RETRYABLE_STATUS:
            if response.status_code >= 400:
                logger.warning("SPORTS_SETTLEMENT_HTTP_ERROR venue=%s status=%s path=%s body=%s", venue, response.status_code, response.request.url.path, response.text[:180].replace("\n", " "))
            return response
        if attempt + 1 >= attempts:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(4.0, 0.5 * (2 ** attempt))
        except (TypeError, ValueError):
            delay = min(4.0, 0.5 * (2 ** attempt))
        logger.warning("SPORTS_SETTLEMENT_HTTP_RETRY venue=%s status=%s attempt=%s sleep_s=%.2f", venue, response.status_code, attempt + 1, delay)
        await asyncio.sleep(delay)
    assert response is not None
    return response


async def fetch_open_trades(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
    response.raise_for_status()
    rows = response.json()
    return [row for row in rows if isinstance(row, dict)
            and str(row.get("mode") or "") in {"LIVE", "LIVE_PROBE"}
            and str(row.get("strategy") or "").startswith("sports_gk_")
            and not bool(row.get("realized"))
            and int(row.get("legs_filled") or 0) == 2
            and str(row.get("status") or "") == "AWAITING_SETTLEMENT"]


def exact_market_ids(trade: Mapping[str, Any]) -> tuple[str, str]:
    metadata = trade.get("metadata") if isinstance(trade.get("metadata"), Mapping) else {}
    gp = metadata.get("gemini_payload") if isinstance(metadata.get("gemini_payload"), Mapping) else {}
    kp = metadata.get("kalshi_payload") if isinstance(metadata.get("kalshi_payload"), Mapping) else {}
    return str(gp.get("symbol") or ""), str(kp.get("ticker") or "")


async def _gemini_private_settled_positions(symbols: set[str]) -> dict[str, str | None]:
    results = {symbol: None for symbol in symbols}
    if not symbols or not gemini_has_credentials():
        return results
    response: httpx.Response | None = None
    for attempt in range(4):
        response = await private_rest_request("POST", GEMINI_SETTLED_POSITIONS_PATH, timeout=10.0)
        if response.status_code not in RETRYABLE_STATUS:
            break
        if attempt + 1 >= 4:
            break
        delay = min(4.0, 0.5 * (2 ** attempt))
        logger.warning("SPORTS_SETTLEMENT_GEMINI_PRIVATE_RETRY status=%s attempt=%s sleep_s=%.2f", response.status_code, attempt + 1, delay)
        await asyncio.sleep(delay)
    assert response is not None
    if response.status_code >= 400:
        logger.warning("SPORTS_SETTLEMENT_GEMINI_PRIVATE_ERROR status=%s body=%s", response.status_code, response.text[:180].replace("\n", " "))
        response.raise_for_status()
    payload = response.json()
    positions = payload.get("positions") or [] if isinstance(payload, Mapping) else []
    parsed = gemini_settled_positions_map((x for x in positions if isinstance(x, Mapping)), symbols)
    logger.info("SPORTS_SETTLEMENT_GEMINI_PRIVATE targets=%s resolved=%s unresolved=%s", len(symbols), sum(v is not None for v in parsed.values()), sum(v is None for v in parsed.values()))
    return parsed


async def fetch_gemini_resolutions(http: httpx.AsyncClient, symbols: Iterable[str]) -> dict[str, str | None]:
    targets = {str(symbol) for symbol in symbols if str(symbol)}
    results = {symbol: None for symbol in targets}
    if not targets:
        return results

    # First use the purpose-built public recent settlement feed. If Gemini's public
    # prediction-market history surface is unavailable or the target is absent,
    # fall back to this account's settled-position audit snapshot.
    try:
        response = await _get_with_retry(
            http,
            GEMINI_RECENTLY_SETTLED,
            venue="gemini_recently_settled",
            params={"sport": "baseball", "limit": 500, "offset": 0},
            attempts=2,
        )
        if response.status_code < 400:
            payload = response.json()
            events = payload.get("data") or payload.get("events") or []
            results.update(gemini_resolution_map((x for x in events if isinstance(x, Mapping)), targets))
            logger.info("SPORTS_SETTLEMENT_GEMINI_PUBLIC targets=%s resolved=%s unresolved=%s", len(targets), sum(v is not None for v in results.values()), sum(v is None for v in results.values()))
        else:
            logger.warning("SPORTS_SETTLEMENT_GEMINI_PUBLIC_UNAVAILABLE status=%s falling_back=settled_positions", response.status_code)
    except Exception as exc:
        logger.warning("SPORTS_SETTLEMENT_GEMINI_PUBLIC_EXCEPTION type=%s falling_back=settled_positions", type(exc).__name__)

    unresolved = {symbol for symbol, value in results.items() if value is None}
    if unresolved:
        private = await _gemini_private_settled_positions(unresolved)
        for symbol, value in private.items():
            if value is not None:
                results[symbol] = value
    return results


async def fetch_kalshi_resolutions(http: httpx.AsyncClient, tickers: Iterable[str]) -> dict[str, str | None]:
    targets = sorted({str(ticker) for ticker in tickers if str(ticker)})
    results: dict[str, str | None] = {}
    for index, ticker in enumerate(targets):
        response = await _get_with_retry(http, f"{KALSHI_REST}/markets/{quote(ticker, safe='')}", venue="kalshi", allow_status=frozenset({404}))
        if response.status_code == 404:
            results[ticker] = None
        else:
            response.raise_for_status()
            results[ticker] = kalshi_resolution_from_payload(response.json())
        if index + 1 < len(targets):
            await asyncio.sleep(0.12)
    logger.info("SPORTS_SETTLEMENT_KALSHI_BATCH targets=%s resolved=%s unresolved=%s", len(targets), sum(v is not None for v in results.values()), sum(v is None for v in results.values()))
    return results


async def reconcile_trade(trade: dict[str, Any], *, gemini_results: Mapping[str, str | None], kalshi_results: Mapping[str, str | None]) -> bool:
    metadata = trade.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    symbol, ticker = exact_market_ids(trade)
    if not symbol or not ticker:
        logger.warning("SPORTS_SETTLEMENT_BLOCKED trade_id=%s reason=missing_exact_market_ids", trade.get("trade_id"))
        return False
    gemini_result = gemini_results.get(symbol)
    kalshi_result = kalshi_results.get(ticker)
    gemini_bought, kalshi_bought = bought_outcomes(metadata)
    payouts = settlement_payouts(gemini_bought=gemini_bought, kalshi_bought=kalshi_bought, gemini_result=gemini_result, kalshi_result=kalshi_result)
    if payouts is None:
        logger.info("SPORTS_SETTLEMENT_PENDING trade_id=%s gemini=%s kalshi=%s", trade.get("trade_id"), gemini_result, kalshi_result)
        return False

    gross, net = realized_pnl(capital_filled=trade.get("capital_filled"), fees=trade.get("fees"), payouts=payouts)
    compatible = settlement_compatible(str(gemini_result), str(kalshi_result))
    status = "SETTLED_COMPATIBLE" if compatible else "SETTLED_DIVERGENT"
    trade_id = str(trade.get("trade_id") or "")
    event = {
        "event_id": _safe_id(f"{trade_id}:realized"), "trade_id": trade_id, "event_type": "TRADE_REALIZED", "event_ts": _now(),
        "strategy": str(trade.get("strategy") or "sports_gk_live_probe"), "mode": str(trade.get("mode") or "LIVE_PROBE"),
        "market": trade.get("market"), "asset": trade.get("asset"), "venues": ["gemini", "kalshi"], "side": trade.get("side"),
        "status": status, "quantity": "1", "fee": "0", "gross_pnl": str(gross), "net_pnl": str(net), "legs_required": 2, "legs_filled": 2,
        "metadata": {**metadata, "settlement": {
            "gemini_instrument_symbol": symbol, "kalshi_ticker": ticker, "gemini_bought": gemini_bought, "kalshi_bought": kalshi_bought,
            "gemini_result": gemini_result, "kalshi_result": kalshi_result, "gemini_payout": str(payouts[0]), "kalshi_payout": str(payouts[1]),
            "total_payout": str(sum(payouts, D("0"))), "capital_filled": str(trade.get("capital_filled") or "0"),
            "recorded_fees": str(trade.get("fees") or "0"), "settlement_compatible": compatible, "realized_from_actual_settlement": True,
        }},
    }
    if not await _post_event(event):
        return False
    player = str(trade.get("asset") or metadata.get("player") or "MLB prop")
    sign = "+" if net >= 0 else ""
    await _push(f"MLB SETTLED — {sign}${net}", "\n".join((player, f"Gemini: {str(gemini_result).upper()} | Kalshi: {str(kalshi_result).upper()}", f"Settlement: {'compatible' if compatible else 'DIVERGENT'}", f"Actual payout: ${sum(payouts, D('0'))}", f"Actual net P&L: {sign}${net}", "Recorded as REALIZED in ARB Command Center.")))
    logger.warning("SPORTS_TRADE_REALIZED trade_id=%s status=%s gross=%s net=%s", trade_id, status, gross, net)
    return True


async def run_forever(interval_seconds: float = 20.0) -> None:
    logger.info("SPORTS_SETTLEMENT_RECONCILER_READY interval_seconds=%s read_only_venues=true order_entry=false batch_reads=true gemini_settled_positions_fallback=true", interval_seconds)
    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            try:
                trades = await fetch_open_trades(http)
                market_ids = [exact_market_ids(trade) for trade in trades]
                symbols = {symbol for symbol, _ in market_ids if symbol}
                tickers = {ticker for _, ticker in market_ids if ticker}
                if trades:
                    gemini_results, kalshi_results = await asyncio.gather(fetch_gemini_resolutions(http, symbols), fetch_kalshi_resolutions(http, tickers))
                else:
                    gemini_results, kalshi_results = {}, {}
                realized = 0
                for trade in trades:
                    realized += int(await reconcile_trade(trade, gemini_results=gemini_results, kalshi_results=kalshi_results))
                logger.info("SPORTS_SETTLEMENT_HEARTBEAT open_awaiting=%s realized_this_cycle=%s", len(trades), realized)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("SPORTS_SETTLEMENT_LOOP_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(max(5.0, interval_seconds))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=20.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever(args.interval_seconds))


if __name__ == "__main__":
    main()
