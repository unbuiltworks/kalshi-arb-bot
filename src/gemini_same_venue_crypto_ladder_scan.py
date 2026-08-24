"""Read-only maker-frontier census for same-Gemini crypto threshold ladders.

This intentionally avoids the Gemini<->Kalshi settlement-index mismatch. It only
considers pairs of HI threshold contracts that belong to the same Gemini event and
share exact expiry + source metadata. For low strike L < high strike H:

    BUY YES(L) + BUY NO(H)

pays at least $1 at settlement. The scanner asks whether one leg can be posted as a
maker at the most aggressive post-only price while the other leg remains an exact
current taker hedge and the pair preserves >=3c per contract after Gemini fees.

READ ONLY. No authenticated calls and no order mutation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_FLOOR
import json
import logging
import re
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto

D = Decimal
ONE = D("1")
TARGET = D("0.03")
BASE = "https://api.gemini.com"
HI_RE = re.compile(r"-HI[0-9D]+$", re.I)
logger = logging.getLogger("kalshi_arb.gemini_same_venue_crypto_ladder_scan")


def _d(value: Any) -> D | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _source(event: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    for row in (contract, event):
        details = row.get("sourceDetails") or row.get("source_details") or {}
        if isinstance(details, Mapping):
            index = str(details.get("index") or "").strip()
            if index:
                return index
        source = str(row.get("source") or "").strip()
        if source:
            return source
    return ""


def _strike(contract: Mapping[str, Any]) -> D | None:
    raw = contract.get("strike") or {}
    if isinstance(raw, Mapping):
        value = _d(raw.get("value"))
        if value is not None:
            return value
    return None


def _symbol(contract: Mapping[str, Any]) -> str:
    return str(contract.get("instrumentSymbol") or contract.get("instrument_symbol") or "").strip()


def _q1_ok(contract: Mapping[str, Any]) -> bool:
    qmin = _d(contract.get("quantityMinimum") or contract.get("quantity_minimum") or "1")
    qinc = _d(contract.get("quantityIncrement") or contract.get("quantity_increment") or "1")
    if qmin is None or qinc is None or qmin <= 0 or qinc <= 0 or qmin > ONE:
        return False
    units = ONE / qinc
    return units == units.to_integral_value()


def _tick(contract: Mapping[str, Any]) -> D | None:
    tick = _d(contract.get("priceIncrement") or contract.get("price_increment") or "0.01")
    return tick if tick is not None and tick > 0 and tick < ONE else None


def _level(raw: Any) -> tuple[D, D] | None:
    if not isinstance(raw, Mapping):
        return None
    p = _d(raw.get("price"))
    q = _d(raw.get("amount") if raw.get("amount") is not None else raw.get("quantity"))
    if p is None or q is None or not (D("0") < p < ONE) or q <= 0:
        return None
    return p, q


def top(book: Mapping[str, Any]) -> dict[str, D | None]:
    bids = [x for x in (_level(r) for r in (book.get("bids") or [])) if x]
    asks = [x for x in (_level(r) for r in (book.get("asks") or [])) if x]
    bid = max(bids, key=lambda z: z[0]) if bids else None
    ask = min(asks, key=lambda z: z[0]) if asks else None
    return {
        "yes_bid": bid[0] if bid else None,
        "yes_bid_qty": bid[1] if bid else None,
        "yes_ask": ask[0] if ask else None,
        "yes_ask_qty": ask[1] if ask else None,
    }


def outcome_book(t: Mapping[str, D | None], outcome: str) -> dict[str, D | None]:
    if outcome == "YES":
        return {
            "bid": t.get("yes_bid"),
            "bid_qty": t.get("yes_bid_qty"),
            "ask": t.get("yes_ask"),
            "ask_qty": t.get("yes_ask_qty"),
        }
    yes_ask = t.get("yes_ask")
    yes_bid = t.get("yes_bid")
    return {
        "bid": None if yes_ask is None else ONE - yes_ask,
        "bid_qty": t.get("yes_ask_qty"),
        "ask": None if yes_bid is None else ONE - yes_bid,
        "ask_qty": t.get("yes_bid_qty"),
    }


def locked_profit(maker_price: D, hedge_price: D, quantity: D = ONE) -> D:
    return (
        quantity
        - quantity * (maker_price + hedge_price)
        - gemini_predictions_maker_fee(maker_price, quantity)
        - gemini_predictions_taker_fee(hedge_price, quantity)
    )


def safe_frontier(*, maker_ask: D | None, hedge_ask: D, tick: D, quantity: D = ONE) -> D | None:
    """Highest tick-valid strictly post-only maker price preserving TARGET/q."""
    cap = (maker_ask - tick) if maker_ask is not None else (ONE - tick)
    if cap <= 0:
        return None
    steps = (cap / tick).to_integral_value(rounding=ROUND_FLOOR)
    while steps > 0:
        price = steps * tick
        if price > 0 and locked_profit(price, hedge_ask, quantity) / quantity >= TARGET:
            return price
        steps -= 1
    return None


def ladder_contracts(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    event_expiry = str(event.get("expiryDate") or event.get("expiry_date") or "")
    out: list[dict[str, Any]] = []
    for raw in event.get("contracts") or []:
        if not isinstance(raw, Mapping):
            continue
        contract = dict(raw)
        symbol = _symbol(contract)
        strike = _strike(contract)
        expiry = str(contract.get("expiryDate") or contract.get("expiry_date") or event_expiry)
        source = _source(event, contract)
        if not symbol or not HI_RE.search(symbol) or strike is None or not source:
            continue
        if event_expiry and expiry and expiry != event_expiry:
            continue
        if str(contract.get("status") or "").lower() not in {"active", "approved"}:
            continue
        if str(contract.get("marketState") or contract.get("market_state") or "open").lower() != "open":
            continue
        if not _q1_ok(contract) or _tick(contract) is None:
            continue
        contract["_strike"] = strike
        contract["_source"] = source
        contract["_expiry"] = expiry
        out.append(contract)
    if not out:
        return []
    source_set = {str(x["_source"]) for x in out}
    expiry_set = {str(x["_expiry"]) for x in out}
    if len(source_set) != 1 or len(expiry_set) != 1:
        return []
    return sorted(out, key=lambda r: D(str(r["_strike"])))


async def _books(http: httpx.AsyncClient, symbols: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    books: dict[str, dict[str, Any]] = {}
    statuses: dict[str, int] = {}
    sem = asyncio.Semaphore(12)

    async def one(symbol: str) -> None:
        async with sem:
            try:
                response = await http.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 20, "limit_asks": 20})
                statuses[symbol] = response.status_code
                if response.status_code == 200:
                    parsed = response.json()
                    if isinstance(parsed, Mapping):
                        books[symbol] = dict(parsed)
            except Exception:
                statuses[symbol] = 0

    await asyncio.gather(*(one(symbol) for symbol in symbols))
    return books, statuses


def score(events: list[Mapping[str, Any]], books: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    telemetry = {"eligible_ladder_events": 0, "strict_ladder_contracts": 0, "adjacent_pairs": 0}
    for event in events:
        contracts = ladder_contracts(event)
        if len(contracts) < 2:
            continue
        telemetry["eligible_ladder_events"] += 1
        telemetry["strict_ladder_contracts"] += len(contracts)
        ticker = str(event.get("ticker") or "")
        title = str(event.get("title") or "")
        for low, high in zip(contracts, contracts[1:]):
            telemetry["adjacent_pairs"] += 1
            low_symbol, high_symbol = _symbol(low), _symbol(high)
            lb = books.get(low_symbol)
            hb = books.get(high_symbol)
            if not isinstance(lb, Mapping) or not isinstance(hb, Mapping):
                continue
            lt, ht = top(lb), top(hb)
            for direction, maker, maker_outcome, maker_top, hedge, hedge_outcome, hedge_top in (
                ("MAKER_LOW_YES_HEDGE_HIGH_NO", low, "YES", lt, high, "NO", ht),
                ("MAKER_HIGH_NO_HEDGE_LOW_YES", high, "NO", ht, low, "YES", lt),
            ):
                mb = outcome_book(maker_top, maker_outcome)
                hb2 = outcome_book(hedge_top, hedge_outcome)
                hedge_ask = hb2.get("ask")
                hedge_depth = hb2.get("ask_qty")
                tick = _tick(maker)
                if not isinstance(hedge_ask, D) or not isinstance(hedge_depth, D) or hedge_depth < ONE or not isinstance(tick, D):
                    continue
                quote = safe_frontier(maker_ask=mb.get("ask") if isinstance(mb.get("ask"), D) else None, hedge_ask=hedge_ask, tick=tick)
                if quote is None:
                    continue
                locked = locked_profit(quote, hedge_ask)
                maker_ask = mb.get("ask") if isinstance(mb.get("ask"), D) else None
                maker_bid = mb.get("bid") if isinstance(mb.get("bid"), D) else D("0")
                rows.append({
                    "event_ticker": ticker,
                    "event_title": title,
                    "expiry": maker.get("_expiry"),
                    "source": maker.get("_source"),
                    "low_symbol": low_symbol,
                    "low_strike": str(low.get("_strike")),
                    "high_symbol": high_symbol,
                    "high_strike": str(high.get("_strike")),
                    "direction": direction,
                    "maker_symbol": _symbol(maker),
                    "maker_outcome": maker_outcome,
                    "existing_maker_bid": str(maker_bid),
                    "maker_ask": None if maker_ask is None else str(maker_ask),
                    "maker_tick": str(tick),
                    "safe_maker_quote": str(quote),
                    "quote_improvement": str(quote - maker_bid),
                    "gap_to_ask": None if maker_ask is None else str(maker_ask - quote),
                    "hedge_symbol": _symbol(hedge),
                    "hedge_outcome": hedge_outcome,
                    "hedge_ask": str(hedge_ask),
                    "hedge_depth": str(hedge_depth),
                    "maker_fee_q1": str(gemini_predictions_maker_fee(quote, ONE)),
                    "taker_fee_q1": str(gemini_predictions_taker_fee(hedge_ask, ONE)),
                    "locked_profit_q1": str(locked),
                    "guaranteed_payout_floor_before_fees": "1",
                    "settlement_classification": "SAME_GEMINI_EVENT_EXACT_SOURCE_EXPIRY_HI_LADDER",
                    "read_only": True,
                    "order_entry": False,
                    "realized": False,
                })
    rows.sort(key=lambda r: (
        D(str(r["gap_to_ask"])) if r.get("gap_to_ask") is not None else D("1"),
        -D(str(r["hedge_depth"])),
        -D(str(r["locked_profit_q1"])),
    ))
    return rows, telemetry


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        strict = [(event, ladder_contracts(event)) for event in events]
        symbols = {_symbol(c) for _, contracts in strict for c in contracts}
        books, statuses = await _books(http, symbols)
    candidates, telemetry = score(events, books)
    out = {
        "gemini_active_crypto_events": len(events),
        **telemetry,
        "books_requested": len(symbols),
        "books_ok": sum(1 for symbol in symbols if statuses.get(symbol) == 200),
        "maker_frontier_3c_candidates": len(candidates),
        "top_candidates": candidates[:20],
        "fee_policy": "current Gemini predictions maker+taker fees, ceil-cent; maker rebates/rewards excluded",
        "target_per_contract": str(TARGET),
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }
    logger.warning("GEMINI_SAME_VENUE_CRYPTO_LADDER_SCAN %s", json.dumps(out, separators=(",", ":"), default=str))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
