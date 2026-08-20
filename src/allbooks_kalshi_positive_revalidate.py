"""Targeted fresh revalidation of current all-books/Kalshi positive MLB prop shadows.

Re-queries only event/market families that produced positive plans in the immediately
preceding all-books slate scan, and fetches each exact Kalshi depth-1 order book.
Recomputes q=1..5 economics under the $5 ceiling. Research-only; no alert or order path.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

import httpx

from src import allbooks_kalshi_mlb_prop_multi_shadow as allbooks
from src import fanduel_kalshi_mlb_prop_multi_shadow as base
from src.strict_mlb_prop_snapshot import kalshi_top_book

D = Decimal
KALSHI = "https://external-api.kalshi.com/trade-api/v2"

TARGETS = {
    "48f1a38db4ac9c09c80dc35075975eff": {
        "markets": ("pitcher_strikeouts_alternate",),
        "tickers": ("KXMLBKS-26AUG201240STLCIN-CINBSINGER51-6",),
    },
    "0e5490a74e6eb5ea18cf0dc7b8cf02cd": {
        "markets": ("pitcher_strikeouts_alternate",),
        "tickers": ("KXMLBKS-26AUG201410ATLCWS-ATLGHOLMES66-4",),
    },
    "f71617a2d08c1daac109bf9f87e7ddae": {
        "markets": ("pitcher_strikeouts_alternate",),
        "tickers": (
            "KXMLBKS-26AUG201410SEAMIL-SEAGKIRBY68-6",
            "KXMLBKS-26AUG201410SEAMIL-SEAGKIRBY68-7",
            "KXMLBKS-26AUG201410SEAMIL-SEAGKIRBY68-8",
        ),
    },
    "4641a1b30b5bae3cfb30564156dc2003": {
        "markets": ("pitcher_strikeouts_alternate", "batter_total_bases_alternate"),
        "tickers": (
            "KXMLBKS-26AUG201835NYYBAL-BALKBRADISH38-4",
            "KXMLBTB-26AUG201835NYYBAL-BALGHENDERSON2-2",
            "KXMLBTB-26AUG201835NYYBAL-BALGHENDERSON2-3",
            "KXMLBTB-26AUG201835NYYBAL-BALPALONSO25-2",
            "KXMLBTB-26AUG201835NYYBAL-BALPALONSO25-3",
        ),
    },
}


def _ticker_identity(ticker: str, k_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in k_rows if row.get("ticker") == ticker), None)


async def run_once() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        # Refresh exact Kalshi identity inventory and fee metadata first.
        k_rows: list[dict[str, Any]] = []
        multipliers: dict[str, Decimal] = {}
        for series in base.SERIES_FAMILY:
            events, multiplier = await base._fetch_series(http, series)
            multipliers[series] = multiplier
            for event in events:
                for market in event.get("markets") or []:
                    if isinstance(market, Mapping):
                        row = base._kalshi_row(event, market)
                        if row is not None:
                            k_rows.append(row)
            await asyncio.sleep(0.05)

        async def sportsbook_call(event_id: str, markets: tuple[str, ...]):
            response = await http.get(
                base.EVENT_ODDS_URL.format(event_id=event_id),
                params={"apiKey":api_key,"regions":"us","markets":",".join(markets),"oddsFormat":"decimal","dateFormat":"iso"},
            )
            response.raise_for_status()
            return event_id, response

        sb_results = await asyncio.gather(*(sportsbook_call(event_id, cfg["markets"]) for event_id, cfg in TARGETS.items()))
        sb_legs: list[dict[str, Any]] = []
        quota = []
        for event_id, response in sb_results:
            body = response.json()
            parsed = allbooks._sportsbook_legs(body if isinstance(body, Mapping) else {})
            sb_legs.extend(parsed)
            quota.append({"event_id":event_id,"last_cost":response.headers.get("x-requests-last",""),"remaining":response.headers.get("x-requests-remaining",""),"used":response.headers.get("x-requests-used","")})

        # Fetch the exact candidate books after sportsbook calls so the two sides are
        # as close in time as these REST feeds allow.
        ticker_list = [ticker for cfg in TARGETS.values() for ticker in cfg["tickers"]]
        async def kbook(ticker: str):
            response = await http.get(f"{KALSHI}/markets/{ticker}/orderbook", params={"depth":1})
            received_ms = int(time.time() * 1000)
            response.raise_for_status()
            return ticker, response.json(), received_ms
        kbook_results = await asyncio.gather(*(kbook(ticker) for ticker in ticker_list))

    fresh_k: dict[str, dict[str, Any]] = {}
    for ticker, payload, received_ms in kbook_results:
        identity = _ticker_identity(ticker, k_rows)
        book = kalshi_top_book(payload if isinstance(payload, dict) else {})
        if identity is None or book is None:
            continue
        row = dict(identity); row["book"] = book; row["received_ms"] = received_ms
        fresh_k[ticker] = row

    plans = []
    for sb in sb_legs:
        for ticker, k in fresh_k.items():
            # Reuse generalized sizing, but feed the actual Kalshi receive time for
            # each exact market rather than one slate-wide snapshot time.
            row = allbooks.best_plan(sb, k, multiplier=multipliers[k["series"]], snapshot_ms=int(k["received_ms"]))
            if row is not None:
                plans.append(row)
    plans.sort(key=lambda row: D(row["shadow_locked_profit"]), reverse=True)
    positives = [row for row in plans if D(row["shadow_locked_profit"]) > 0]
    fresh5 = [row for row in positives if int(row["quote_skew_ms"]) <= 5_000]
    fresh10 = [row for row in positives if int(row["quote_skew_ms"]) <= 10_000]
    return {
        "status":"ALLBOOKS_KALSHI_TARGETED_REVALIDATION",
        "target_events":len(TARGETS),
        "target_tickers":len(ticker_list),
        "sportsbook_legs":len(sb_legs),
        "kalshi_books":len(fresh_k),
        "exact_revalidated_plans":len(plans),
        "positive_revalidated_plans":len(positives),
        "positive_fresh_5s":len(fresh5),
        "positive_fresh_10s":len(fresh10),
        "positive_by_book":dict(Counter(row["sportsbook"] for row in positives)),
        "positive_by_family":dict(Counter(row["family"] for row in positives)),
        "top_plans":plans[:50],
        "top_positive":positives[:30],
        "quota":quota,
        "rule_gate":"BOOKMAKER_SPECIFIC_COMPLETE_RULE_CERTIFICATION_REQUIRED",
        "alert_eligible":False,
        "execution_eligible":False,
        "realized_pnl":"0",
        "read_only":True,
        "order_entry":False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
