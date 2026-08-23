"""Read-only scan for the minimal scalar-safe Kalshi MVE NO cover.

For an MVE conjunction with selected settlement values x_i in [0,1]:

    combo_yes = product(x_i)
    combo_no  = 1 - product(x_i)

For ANY selected component k, product(x_i) <= x_k, hence:

    combo_no + x_k >= 1.

So the tightest guaranteed NO-side cover is one combo-NO contract plus one
selected component contract, not every selected component.  This scanner searches
already-existing active sports MVEs, checks public q=1 NO liquidity when present,
and otherwise requests a bounded q=1 RFQ to observe the maker YES bid (equivalent
to the requester buying NO at 1-YES-bid).  It never creates an MVE market, accepts
a quote, submits an order, or exposes capital.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_same_venue_scan as scan

D = Decimal
ONE = D("1")
TARGET = D("0.03")


def _selected_quote(market: Mapping[str, Any], side: str) -> dict[str, Any] | None:
    side = str(side or "").lower()
    if side == "yes":
        px = scan._d(market.get("yes_ask_dollars"))
        depth = scan._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        source = "yes_ask"
    elif side == "no":
        px = scan._d(market.get("no_ask_dollars"))
        depth = scan._d(market.get("no_ask_size_fp") or market.get("no_ask_size"))
        source = "no_ask"
        if px is None or depth is None:
            yes_bid = scan._d(market.get("yes_bid_dollars"))
            yes_depth = scan._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
            if yes_bid is not None and yes_depth is not None:
                px = ONE - yes_bid
                depth = yes_depth
                source = "derived_from_yes_bid"
    else:
        return None
    if px is None or depth is None or depth < ONE or not (D("0") < px < ONE):
        return None
    fee = scan._fee(px)
    return {
        "price": px,
        "depth": depth,
        "fee": fee,
        "all_in": px + fee,
        "source": source,
    }


def _public_combo_no(market: Mapping[str, Any]) -> dict[str, Any] | None:
    px = scan._d(market.get("no_ask_dollars"))
    depth = scan._d(market.get("no_ask_size_fp") or market.get("no_ask_size"))
    source = "no_ask"
    if px is None or depth is None:
        yes_bid = scan._d(market.get("yes_bid_dollars"))
        yes_depth = scan._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
        if yes_bid is not None and yes_depth is not None:
            px = ONE - yes_bid
            depth = yes_depth
            source = "derived_from_yes_bid"
    if px is None or depth is None or depth < ONE or not (D("0") < px < ONE):
        return None
    fee = scan._fee(px)
    return {"price": px, "depth": depth, "fee": fee, "all_in": px + fee, "source": source}


def _best_rfq_combo_no(quotes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, int]]:
    best: dict[str, Any] | None = None
    stats = {"quotes": 0, "positive_yes_bid": 0, "positive_no_bid": 0}
    for quote in quotes:
        if str(quote.get("status") or "").lower() not in {"", "open"}:
            continue
        stats["quotes"] += 1
        yes_bid = scan._d(quote.get("yes_bid_dollars"))
        no_bid = scan._d(quote.get("no_bid_dollars"))
        if no_bid is not None and no_bid > 0:
            stats["positive_no_bid"] += 1
        if yes_bid is None or not (D("0") < yes_bid < ONE):
            continue
        stats["positive_yes_bid"] += 1
        px = ONE - yes_bid
        fee = scan._fee(px)
        row = {
            "price": px,
            "fee": fee,
            "all_in": px + fee,
            "updated_ts": quote.get("updated_ts"),
            "source": "rfq_yes_bid_complement",
        }
        if best is None or px < best["price"]:
            best = row
    return best, stats


def _score(
    *,
    ticker: str,
    title: Any,
    close_time: str,
    leg_count: int,
    component: Mapping[str, Any],
    combo_no: Mapping[str, Any],
    price_source: str,
) -> dict[str, Any]:
    all_in = D(str(component["all_in"])) + D(str(combo_no["all_in"]))
    locked = ONE - all_in
    return {
        "ticker": ticker,
        "title": title,
        "close_time": close_time,
        "leg_count": leg_count,
        "direction": "COMBO_NO_PLUS_ONE_SELECTED_LEG",
        "settlement_identity": "1-product(x_i)+x_k>=1_for_any_selected_k",
        "selected_component": {
            "market_ticker": component["market_ticker"],
            "selected_side": component["selected_side"],
            "ask": str(component["price"]),
            "depth_fp": str(component["depth"]),
            "fee": str(component["fee"]),
            "all_in": str(component["all_in"]),
            "price_source": component["source"],
        },
        "combo_no": {
            "ask_equivalent": str(combo_no["price"]),
            "depth_fp": str(combo_no.get("depth")) if combo_no.get("depth") is not None else None,
            "fee": str(combo_no["fee"]),
            "all_in": str(combo_no["all_in"]),
            "price_source": combo_no["source"],
        },
        "quote_source": price_source,
        "all_in_cost_conservative": str(all_in),
        "locked_profit_conservative": str(locked),
        "classification": "SHADOW_EXECUTABLE_MINIMAL_NO_COVER" if locked >= TARGET else "SHADOW_NO_TRADE",
        "live_execution_enabled": False,
        "market_creation": False,
        "quote_acceptance": False,
        "order_entry": False,
        "realized": False,
    }


async def run_once() -> dict[str, Any]:
    max_rfqs = max(1, min(int(os.getenv("MVE_MINIMAL_NO_MAX_RFQS", "8")), 16))
    horizon_h = max(1, min(int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24")), 48))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_h)
    client = KalshiClient(base_url=scan.BASE)
    open_rfqs: list[str] = []
    market_cache: dict[str, dict[str, Any]] = {}
    try:
        active: dict[str, dict[str, Any]] = {}
        cursor = ""
        for _ in range(8):
            params: dict[str, Any] = {"limit": 200, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/events/multivariate", params=params)
            if response.status_code != 200:
                break
            body = response.json() if response.content else {}
            for event in body.get("events") or []:
                if not isinstance(event, Mapping):
                    continue
                for raw_market in event.get("markets") or []:
                    if not isinstance(raw_market, Mapping):
                        continue
                    market = dict(raw_market)
                    legs = [dict(x) for x in (market.get("mve_selected_legs") or []) if isinstance(x, Mapping)]
                    if len(legs) not in scan.SUPPORTED_LEG_COUNTS or not scan._sportsish(legs):
                        continue
                    close = scan._parse_dt(market.get("close_time") or market.get("expected_expiration_time"))
                    if (
                        str(market.get("status") or "").lower() not in {"active", "open"}
                        or close is None or close <= now or close > horizon
                    ):
                        continue
                    ticker = str(market.get("ticker") or "")
                    if ticker:
                        active[ticker] = {
                            "ticker": ticker,
                            "title": market.get("title") or market.get("yes_sub_title") or event.get("title"),
                            "close": close,
                            "market": market,
                            "legs": legs,
                        }
            cursor = str(body.get("cursor") or "") if isinstance(body, Mapping) else ""
            if not cursor:
                break

        async def component_market(ticker: str) -> dict[str, Any]:
            if ticker in market_cache:
                return market_cache[ticker]
            response = await client.get(f"/markets/{ticker}")
            market = scan._market(response.json() if response.content else {}) if response.status_code == 200 else {}
            market_cache[ticker] = market
            return market

        prequalified: list[dict[str, Any]] = []
        for row in sorted(active.values(), key=lambda x: (x["close"], len(x["legs"])))[:300]:
            best_component: dict[str, Any] | None = None
            for leg in row["legs"]:
                market_ticker = str(leg.get("market_ticker") or "")
                selected_side = str(leg.get("side") or "").lower()
                if not market_ticker:
                    continue
                component_body = await component_market(market_ticker)
                quote = _selected_quote(component_body, selected_side)
                if quote is None:
                    continue
                candidate = {
                    "market_ticker": market_ticker,
                    "selected_side": selected_side,
                    **quote,
                }
                if best_component is None or candidate["all_in"] < best_component["all_in"]:
                    best_component = candidate
            if best_component is None:
                continue
            public_no = _public_combo_no(row["market"])
            prequalified.append({**row, "best_component": best_component, "public_no": public_no})

        # First score any public q=1 NO side immediately.  No RFQ is needed.
        scored: list[dict[str, Any]] = []
        public_scored = 0
        for row in prequalified:
            public_no = row["public_no"]
            if public_no is None:
                continue
            public_scored += 1
            scored.append(_score(
                ticker=row["ticker"], title=row["title"], close_time=row["close"].isoformat(),
                leg_count=len(row["legs"]), component=row["best_component"], combo_no=public_no,
                price_source="PUBLIC_BOOK",
            ))

        # RFQ only a small diverse set where the public NO side is unavailable.
        needs_rfq = [row for row in prequalified if row["public_no"] is None]
        low = sorted(needs_rfq, key=lambda x: (x["best_component"]["all_in"], x["close"]))
        high = sorted(needs_rfq, key=lambda x: (-x["best_component"]["price"], x["close"]))
        chosen: list[dict[str, Any]] = []
        seen: set[str] = set()
        for bucket in (low, high):
            for row in bucket:
                if row["ticker"] in seen:
                    continue
                chosen.append(row)
                seen.add(row["ticker"])
                if len(chosen) >= max_rfqs:
                    break
            if len(chosen) >= max_rfqs:
                break

        rfq_quote_count = positive_yes_bids = positive_no_bids = 0
        for row in chosen:
            rid, quotes = await scan._quotes_for(client, row["ticker"], wait_s=1.25)
            if rid:
                open_rfqs.append(rid)
            best_no, stats = _best_rfq_combo_no(quotes)
            rfq_quote_count += stats["quotes"]
            positive_yes_bids += stats["positive_yes_bid"]
            positive_no_bids += stats["positive_no_bid"]
            if best_no is None:
                continue
            scored.append(_score(
                ticker=row["ticker"], title=row["title"], close_time=row["close"].isoformat(),
                leg_count=len(row["legs"]), component=row["best_component"], combo_no=best_no,
                price_source="RFQ",
            ))

        scored.sort(key=lambda x: D(str(x["locked_profit_conservative"])), reverse=True)
        executable = [x for x in scored if x["classification"] == "SHADOW_EXECUTABLE_MINIMAL_NO_COVER"]
        out = {
            "status": "SPORTS_MVE_MINIMAL_NO_SHADOW_V1",
            "observed_at": now.isoformat(),
            "active_short_duration_mves": len(active),
            "prequalified_q1_component": len(prequalified),
            "public_combo_no_scored": public_scored,
            "rfqs_requested": len(chosen),
            "rfq_quotes_seen": rfq_quote_count,
            "rfq_positive_yes_bids": positive_yes_bids,
            "rfq_positive_no_bids": positive_no_bids,
            "scored_cover_count": len(scored),
            "shadow_executable": len(executable),
            "best": scored[0] if scored else None,
            "target_locked": str(TARGET),
            "coverage_identity": "1-product(x_i)+x_k>=1 for any selected x_k in [0,1]",
            "live_execution_enabled": False,
            "market_creation": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_MVE_MINIMAL_NO_SHADOW " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        for rid in open_rfqs:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass
        await client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
