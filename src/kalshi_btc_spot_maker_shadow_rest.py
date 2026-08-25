"""REST market-data fallback for the read-only BTC maker shadow.

Kalshi currently rejects additional authenticated WebSocket sessions with HTTP
429 on this account.  Shadow measurement does not need another scarce WS slot:
current orderbooks and public trades are available through read-only REST.

This wrapper monkey-patches ONLY the market-data transport used by
``kalshi_btc_spot_maker_shadow``.  Fair value, queue logic, fees, markouts,
settlement accounting, and the no-order-entry guarantee are unchanged.

To stay pessimistic despite polling latency:
- the first poll seeds trade ids and can never create a fill;
- a later trade is eligible only if its exchange timestamp is at least 50ms
  after the previous book snapshot was received;
- at most one (the most aggressive) print per taker direction is emitted per
  poll, using only that print's quantity rather than aggregating favorable flow;
- book snapshots are emitted after those prints, so hypothetical quotes are not
  credited with knowledge of the post-trade book before the trade occurs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import time
from typing import Any

import httpx

from src import kalshi_btc_spot_maker_shadow as maker

D = Decimal
POLL_S = 0.25
NEW_QUOTE_SAFETY_MS = 50


def _created_ms(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def _trade_from_rest(row: dict[str, Any]) -> maker.Trade | None:
    try:
        yes = D(str(row["yes_price_dollars"]))
        no = D(str(row["no_price_dollars"]))
        qty = D(str(row.get("count_fp") or "0"))
    except Exception:
        return None
    side = str(row.get("taker_side") or "").lower()
    ts_ms = _created_ms(row.get("created_time"))
    if qty <= 0 or side not in {"yes", "no"} or ts_ms is None:
        return None
    return maker.Trade(yes_price=yes, no_price=no, quantity=qty, taker_side=side, ts_ms=ts_ms)


def _apply_rest_book(book: maker.Book, payload: dict[str, Any], receive_ms: int) -> None:
    raw = payload.get("orderbook_fp") or {}
    book.yes = maker.Book._levels(raw.get("yes_dollars"))
    book.no = maker.Book._levels(raw.get("no_dollars"))
    book.last_receive_ms = receive_ms


def _most_aggressive(rows: list[maker.Trade], side: str) -> maker.Trade | None:
    side_rows = [x for x in rows if x.taker_side == side]
    if not side_rows:
        return None
    # A NO taker sells YES into bids: lower YES price is more aggressive.
    # A YES taker sells NO into NO bids: lower NO price is more aggressive.
    if side == "no":
        return min(side_rows, key=lambda x: (x.yes_price, x.ts_ms))
    return min(side_rows, key=lambda x: (x.no_price, x.ts_ms))


async def rest_market_stream(ticker: str, queue: asyncio.Queue) -> None:
    book = maker.Book(ticker)
    seen: set[str] = set()
    prior_book_receive_ms: int | None = None
    initialized = False

    async with httpx.AsyncClient(timeout=5.0) as http:
        while True:
            cycle_started = time.monotonic()
            now_ms = int(time.time() * 1000)
            min_ts = max(0, int(((prior_book_receive_ms or now_ms) - 2000) / 1000))
            trades_resp, book_resp = await asyncio.gather(
                http.get(
                    f"{maker.KALSHI_REST}/markets/trades",
                    params={"ticker": ticker, "limit": 200, "min_ts": min_ts},
                ),
                http.get(f"{maker.KALSHI_REST}/markets/{ticker}/orderbook", params={"depth": 100}),
            )
            trades_resp.raise_for_status()
            book_resp.raise_for_status()
            receive_ms = int(time.time() * 1000)

            fresh: list[maker.Trade] = []
            for raw in trades_resp.json().get("trades") or []:
                if not isinstance(raw, dict):
                    continue
                trade_id = str(raw.get("trade_id") or "")
                if not trade_id or trade_id in seen:
                    continue
                seen.add(trade_id)
                trade = _trade_from_rest(raw)
                if trade is None:
                    continue
                if initialized and prior_book_receive_ms is not None and trade.ts_ms >= prior_book_receive_ms + NEW_QUOTE_SAFETY_MS:
                    fresh.append(trade)

            # The first poll only seeds history.  Subsequent polls emit at most one
            # adverse-capable print per direction, deliberately understating fills.
            if initialized:
                for side in ("no", "yes"):
                    trade = _most_aggressive(fresh, side)
                    if trade is not None:
                        # Fill markouts align to exchange trade time rather than slow
                        # REST detection time.  The +50ms eligibility rule above keeps
                        # pre-quote trades from being credited as fills.
                        await queue.put(("trade", trade.ts_ms, trade, book))

            _apply_rest_book(book, book_resp.json(), receive_ms)
            await queue.put(("book", receive_ms, None, book))
            prior_book_receive_ms = receive_ms
            initialized = True

            elapsed = time.monotonic() - cycle_started
            await asyncio.sleep(max(0.02, POLL_S - elapsed))


# Replace only market-data transport; all maker economics stay in the core module.
maker.market_stream = rest_market_stream


if __name__ == "__main__":
    maker.main()
