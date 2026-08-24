"""Read-only diagnostics for targeted Kalshi MVE monotonicity manufacturing.

This answers one narrow question before spending any more finite MVE creation
allowance: among the exact component markets eligible for each open sports MVE
collection, do we currently have q=1 selected sides that can form a useful
moderate-probability anchor plus high-probability booster pair?

No MVE is created, no RFQ is opened, and no order is submitted.  The output is
compact enough to keep in Railway logs and deliberately contains no credentials.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_combo_generator as g
from src import sports_kp_mve_monotonicity_seed as seed

D = Decimal
ONE = D("1")
EVENTS_PER_COLLECTION = 18
TOP_ROWS = 8
BOOSTER_THRESHOLDS = (D("0.90"), D("0.92"), D("0.94"), D("0.96"))


def _pair_allowed(collection: Mapping[str, Any], anchor: seed.Selected, booster: seed.Selected) -> bool:
    if anchor.market_ticker == booster.market_ticker:
        return False
    if bool(collection.get("is_single_market_per_event", True)) and anchor.event_ticker == booster.event_ticker:
        return False
    if bool(collection.get("is_all_yes", False)) and (anchor.side != "yes" or booster.side != "yes"):
        return False
    return True


def _pair_stats(collection: Mapping[str, Any], selected: list[seed.Selected]) -> dict[str, Any]:
    anchors = [x for x in selected if D("0.20") <= x.ask <= D("0.90")]
    if bool(collection.get("is_all_yes", False)):
        anchors = [x for x in anchors if x.side == "yes"]
    counts: dict[str, int] = {}
    best_by_threshold: dict[str, dict[str, Any] | None] = {}
    for threshold in BOOSTER_THRESHOLDS:
        boosters = [x for x in selected if x.ask >= threshold]
        if bool(collection.get("is_all_yes", False)):
            boosters = [x for x in boosters if x.side == "yes"]
        pairs: list[tuple[D, seed.Selected, seed.Selected]] = []
        for anchor in anchors:
            for booster in boosters:
                if not _pair_allowed(collection, anchor, booster):
                    continue
                # For conjunction fair value, an anchor+booster product loses
                # approximately anchor*(1-booster) versus the anchor alone.
                penalty = anchor.ask * (ONE - booster.ask)
                pairs.append((penalty, anchor, booster))
        pairs.sort(key=lambda x: (x[0], max(x[1].close_time, x[2].close_time), x[1].market_ticker, x[2].market_ticker))
        key = str(threshold)
        counts[key] = len(pairs)
        if pairs:
            penalty, anchor, booster = pairs[0]
            best_by_threshold[key] = {
                "rough_product_penalty": str(penalty),
                "anchor": {"event": anchor.event_ticker, "ticker": anchor.market_ticker, "side": anchor.side, "ask": str(anchor.ask), "depth": str(anchor.depth)},
                "booster": {"event": booster.event_ticker, "ticker": booster.market_ticker, "side": booster.side, "ask": str(booster.ask), "depth": str(booster.depth)},
            }
        else:
            best_by_threshold[key] = None
    return {
        "anchor_count": len(anchors),
        "booster_counts": {str(t): sum(1 for x in selected if x.ask >= t and (not bool(collection.get("is_all_yes", False)) or x.side == "yes")) for t in BOOSTER_THRESHOLDS},
        "allowed_pair_counts": counts,
        "best_pair_by_threshold": best_by_threshold,
    }


def _top_rows(selected: list[seed.Selected]) -> list[dict[str, Any]]:
    rows = sorted(selected, key=lambda x: (-x.ask, x.close_time, x.market_ticker))[:TOP_ROWS]
    return [
        {
            "event": x.event_ticker,
            "ticker": x.market_ticker,
            "side": x.side,
            "ask": str(x.ask),
            "depth": str(x.depth),
            "fee": str(x.fee),
            "close": x.close_time.isoformat(),
        }
        for x in rows
    ]


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    client = KalshiClient(base_url=g.BASE)
    collection_rows: list[dict[str, Any]] = []
    total_events = 0
    total_selected = 0
    try:
        collections = [
            c for c in await g._open_collections(client)
            if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))
        ]
        for collection in collections:
            associated = [dict(x) for x in (collection.get("associated_events") or []) if isinstance(x, Mapping)]
            if not associated:
                associated = [
                    {"ticker": x, "is_yes_only": bool(collection.get("is_all_yes", False))}
                    for x in (collection.get("associated_event_tickers") or [])
                ]
            visited = 0
            selected: list[seed.Selected] = []
            market_rows_seen = 0
            events_with_q1_selected = 0
            for erow in associated[:EVENTS_PER_COLLECTION]:
                event = str(erow.get("ticker") or "")
                if not event:
                    continue
                visited += 1
                total_events += 1
                markets = await g._event_markets(client, event)
                market_rows_seen += len(markets)
                sides = ("yes",) if bool(erow.get("is_yes_only")) or bool(collection.get("is_all_yes", False)) else ("yes", "no")
                before = len(selected)
                for market in markets:
                    for side in sides:
                        item = seed._selected_from_market(market, event, side, horizon)
                        if item is not None:
                            selected.append(item)
                if len(selected) > before:
                    events_with_q1_selected += 1
            total_selected += len(selected)
            asks = [x.ask for x in selected]
            stats = _pair_stats(collection, selected)
            collection_rows.append({
                "collection": str(collection.get("collection_ticker") or ""),
                "size_min": collection.get("size_min"),
                "size_max": collection.get("size_max"),
                "is_all_yes": bool(collection.get("is_all_yes", False)),
                "single_market_per_event": bool(collection.get("is_single_market_per_event", True)),
                "associated_total": len(associated),
                "events_visited": visited,
                "events_with_q1_selected": events_with_q1_selected,
                "market_rows_seen": market_rows_seen,
                "selected_q1_count": len(selected),
                "selected_yes_count": sum(x.side == "yes" for x in selected),
                "selected_no_count": sum(x.side == "no" for x in selected),
                "min_selected_ask": str(min(asks)) if asks else None,
                "max_selected_ask": str(max(asks)) if asks else None,
                **stats,
                "top_selected_by_ask": _top_rows(selected),
            })
        out = {
            "status": "SPORTS_MVE_MONOTONICITY_CANDIDATE_DIAGNOSTIC_V1",
            "observed_at": now.isoformat(),
            "horizon_hours": 24,
            "events_per_collection_cap": EVENTS_PER_COLLECTION,
            "collection_count": len(collection_rows),
            "events_visited": total_events,
            "selected_q1_count": total_selected,
            "collections": collection_rows,
            "market_creation": False,
            "rfq": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_MVE_MONOTONICITY_CANDIDATE_DIAGNOSTIC " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
