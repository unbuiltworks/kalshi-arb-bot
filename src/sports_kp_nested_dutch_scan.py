"""Read-only Kalshi sports nested-market Dutch-book scanner.

For monotone binary thresholds A_low and A_high with A_high subset A_low,

    YES(A_low) + NO(A_high) >= 1

in every normal outcome. Examples include Over 7.5 + NOT Over 8.5, a player 1+
stat + NOT 2+ stat, or a team win-margin >1.5 + NOT >2.5. This module searches
current MVE-eligible sports components for that exact logical cover and scores q=1
asks with conservative Kalshi fees.

SHADOW ONLY: no MVE creation, RFQ, quote acceptance, or order entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_combo_generator as g
from src import sports_kp_mve_market_quote_patch as patch

D = Decimal
ONE = D("1")
TARGET = D("0.03")
EVENT_CAP = 72


@dataclass(frozen=True)
class ThresholdMarket:
    event_ticker: str
    market_ticker: str
    family_key: str
    threshold: Decimal
    title: str
    yes: Any
    no: Any


def _threshold(market: Mapping[str, Any]) -> Decimal | None:
    title = str(market.get("title") or market.get("yes_sub_title") or "")
    strike = str(market.get("strike_type") or "").lower()
    if strike == "greater" or re.search(r"\bover\s+\d", title, re.I):
        m = re.search(r"(?:over\s+)?(-?\d+(?:\.\d+)?)", title, re.I)
        return g._d(m.group(1)) if m else None
    if strike == "structured" or "+" in title:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*\+", title)
        return g._d(m.group(1)) if m else None
    return None


def _family_key(event_ticker: str, market_ticker: str) -> str:
    suffix = market_ticker
    prefix = event_ticker + "-"
    if suffix.startswith(prefix):
        suffix = suffix[len(prefix):]
    # Props commonly end PLAYERID-2 while spreads/totals end TEAM4 or 9.
    if re.search(r"-\d+$", suffix):
        suffix = re.sub(r"-\d+$", "", suffix)
    else:
        suffix = re.sub(r"\d+$", "", suffix)
    return f"{event_ticker}|{suffix}"


def _from_market(market: Mapping[str, Any], event: str, horizon: datetime) -> ThresholdMarket | None:
    threshold = _threshold(market)
    ticker = str(market.get("ticker") or "")
    if threshold is None or not ticker:
        return None
    yes = patch.selected_from_market(market, event, "yes", horizon)
    no = patch.selected_from_market(market, event, "no", horizon)
    if yes is None or no is None:
        return None
    return ThresholdMarket(
        event_ticker=event,
        market_ticker=ticker,
        family_key=_family_key(event, ticker),
        threshold=threshold,
        title=str(market.get("title") or market.get("yes_sub_title") or ticker),
        yes=yes,
        no=no,
    )


def score_pair(low: ThresholdMarket, high: ThresholdMarket) -> dict[str, Any] | None:
    if low.family_key != high.family_key or not low.threshold < high.threshold:
        return None
    total = low.yes.all_in + high.no.all_in
    locked = ONE - total
    return {
        "family_key": low.family_key,
        "low": {
            "ticker": low.market_ticker, "title": low.title,
            "threshold": str(low.threshold), "side": "yes",
            "ask": str(low.yes.ask), "depth": str(low.yes.depth), "fee": str(low.yes.fee),
        },
        "high": {
            "ticker": high.market_ticker, "title": high.title,
            "threshold": str(high.threshold), "side": "no",
            "ask": str(high.no.ask), "depth": str(high.no.depth), "fee": str(high.no.fee),
        },
        "all_in_cost": str(total),
        "locked_profit": str(locked),
        "classification": "NESTED_SHADOW_EXECUTABLE" if locked >= TARGET else "NESTED_SHADOW_NO_TRADE",
        "identity": "YES(low_threshold)+NO(high_threshold)>=1",
        "rule_gate": "NORMAL_RESOLUTION_AND_MONOTONE_RULE_CERTIFICATION_REQUIRED_BEFORE_LIVE",
        "order_entry": False,
    }


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    client = KalshiClient(base_url=g.BASE)
    try:
        collections = [
            c for c in await g._open_collections(client)
            if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))
        ]
        events: list[str] = []
        seen: set[str] = set()
        for collection in collections:
            associated = [dict(x) for x in (collection.get("associated_events") or []) if isinstance(x, Mapping)]
            if not associated:
                associated = [{"ticker": x} for x in (collection.get("associated_event_tickers") or [])]
            for row in associated:
                event = str(row.get("ticker") or "")
                if event and event not in seen:
                    seen.add(event)
                    events.append(event)
                if len(events) >= EVENT_CAP:
                    break
            if len(events) >= EVENT_CAP:
                break

        rows: list[ThresholdMarket] = []
        market_rows = 0
        for event in events:
            markets = await g._event_markets(client, event)
            market_rows += len(markets)
            for market in markets:
                item = _from_market(market, event, horizon)
                if item is not None:
                    rows.append(item)

        by_family: dict[str, list[ThresholdMarket]] = {}
        for row in rows:
            by_family.setdefault(row.family_key, []).append(row)
        results: list[dict[str, Any]] = []
        for family in by_family.values():
            family.sort(key=lambda x: x.threshold)
            for i, low in enumerate(family):
                for high in family[i + 1:]:
                    scored = score_pair(low, high)
                    if scored is not None:
                        results.append(scored)
        results.sort(key=lambda x: D(str(x["locked_profit"])), reverse=True)
        out = {
            "status": "SPORTS_NESTED_DUTCH_SHADOW_V1",
            "observed_at": now.isoformat(),
            "events_scanned": len(events),
            "market_rows_seen": market_rows,
            "threshold_markets_q1": len(rows),
            "families": len(by_family),
            "pairs_scored": len(results),
            "shadow_executable": sum(x["classification"] == "NESTED_SHADOW_EXECUTABLE" for x in results),
            "best": results[0] if results else None,
            "target_locked": str(TARGET),
            "market_creation": False,
            "rfq": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_NESTED_DUTCH_SHADOW " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_once())
