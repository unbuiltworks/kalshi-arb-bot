"""One-shot read-only batch check of known live FanDuel/Gemini MLB prop candidates."""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
import os
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_live_shadow import gemini_book_ticker_once

D = Decimal
ONE = D("1")
API = "https://api.odds-api.io/v3/odds"

CANDIDATES = [
    ("63302053", "Jared Young", "Hits", D("0.5"), "GEMI-MLB-2608172310-SD-NYM-PPHITS-JAYOUNGO0"),
    ("63301899", "Bryan Reynolds", "Hits", D("0.5"), "GEMI-MLB-2608172305-DET-PIT-PPHITS-BREYNOLDSO0"),
    ("63303451", "Byron Buxton", "Hits", D("3.5"), "GEMI-MLB-2608172340-ATL-MIN-PPHITS-BBUXTONO3"),
]


def fee(price: Decimal, qty: Decimal) -> Decimal:
    raw = D("0.07") * qty * price * (ONE - price)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def find_fd(body: Mapping[str, Any], player: str, stat: str, line: Decimal) -> Decimal | None:
    books = body.get("bookmakers") if isinstance(body.get("bookmakers"), Mapping) else {}
    markets = books.get("FanDuel") or books.get("fanduel") or []
    if not isinstance(markets, list):
        return None
    wanted = f"{player} ({stat})".lower()
    for market in markets:
        if not isinstance(market, Mapping) or str(market.get("name") or "").lower() != "player props":
            continue
        for row in market.get("odds") or []:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("label") or "").strip().lower() != wanted:
                continue
            try:
                hdp = D(str(row.get("hdp")))
                price = D(str(row.get("over")))
            except Exception:
                continue
            if hdp == line and price > ONE:
                return price
    return None


async def check(http: httpx.AsyncClient, api_key: str, item: tuple[str, str, str, Decimal, str]) -> dict[str, Any]:
    event_id, player, stat, line, symbol = item
    response = await http.get(API, params={"apiKey": api_key, "eventId": event_id, "bookmakers": "FanDuel"})
    response.raise_for_status()
    body = response.json()
    fd = find_fd(body, player, stat, line) if isinstance(body, Mapping) else None
    status = str(body.get("status") or "") if isinstance(body, Mapping) else ""
    updated = None
    if isinstance(body, Mapping):
        books = body.get("bookmakers") if isinstance(body.get("bookmakers"), Mapping) else {}
        for market in books.get("FanDuel") or []:
            if isinstance(market, Mapping) and str(market.get("name") or "").lower() == "player props":
                updated = market.get("updatedAt")
                break
    result: dict[str, Any] = {"event_id": event_id, "status": status, "player": player, "stat": stat, "line": str(line), "fanduel_decimal": None if fd is None else str(fd), "fanduel_updatedAt": updated, "gemini_symbol": symbol, "rate_remaining": response.headers.get("x-ratelimit-remaining", "")}
    if status.lower() != "live" or fd is None:
        return result
    try:
        tob = await gemini_book_ticker_once(symbol, timeout_s=5.0)
    except Exception as exc:
        result["gemini_error"] = type(exc).__name__
        return result
    no_price = tob.no_ask
    qty = tob.no_ask_size
    result.update({"gemini_no_price": str(no_price), "gemini_no_depth": str(qty), "gemini_yes_bid": str(tob.yes_bid)})
    if qty <= 0:
        return result
    fd_stake = qty / fd
    gfee = fee(no_price, qty)
    gem_raw = qty * no_price
    capital = fd_stake + gem_raw + gfee
    profit = qty - capital
    result.update({"matched_payout": str(qty), "fanduel_stake": str(fd_stake), "gemini_raw_cost": str(gem_raw), "gemini_fee": str(gfee), "total_capital": str(capital), "locked_profit_if_normal_settlement": str(profit), "roi_on_capital": str(profit / capital) if capital > 0 else None})
    return result


async def main() -> None:
    key = os.getenv("ODDS_API_IO_API_KEY", "").strip()
    if not key:
        raise SystemExit("ODDS_API_IO_API_KEY required")
    async with httpx.AsyncClient(timeout=15.0) as http:
        results = []
        for item in CANDIDATES:
            results.append(await check(http, key, item))
    print("KNOWN_PROP_BATCH " + json.dumps(results, separators=(",", ":"), default=str))


if __name__ == "__main__":
    asyncio.run(main())
