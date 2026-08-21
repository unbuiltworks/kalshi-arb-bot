"""Persistent-budget automatic Kalshi MVE combo manufacturer.

Builds on sports_kp_mve_combo_generator but keeps process-local de-duplication,
rotates collections, and enforces a daily creation ceiling comfortably below
Kalshi's documented 5000/week market-creation allowance.  It creates/resolves
combo markets and requests RFQs only; it never accepts a quote or submits an
order.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from typing import Any

from src.auth import KalshiClient
from src import sports_kp_mve_combo_generator as g

_SEEN: set[str] = set()
_DAY = ""
_CREATED_TODAY = 0
_COLLECTION_CURSOR = 0


def _reset_day() -> None:
    global _DAY, _CREATED_TODAY, _SEEN
    today = datetime.now(timezone.utc).date().isoformat()
    if today != _DAY:
        _DAY = today
        _CREATED_TODAY = 0
        _SEEN = set()


async def generate_and_score() -> dict[str, Any]:
    global _CREATED_TODAY, _COLLECTION_CURSOR
    _reset_day()
    enabled = os.getenv("ENABLE_KALSHI_MVE_GENERATOR", "NO").strip().upper() == "YES"
    if not enabled:
        return {"status": "MVE_GENERATOR_DISARMED", "market_creation": False, "order_entry": False, "ranked": []}

    per_run = max(1, min(int(os.getenv("MVE_GENERATOR_MAX_CREATIONS_PER_RUN", "8")), 25))
    daily_cap = max(per_run, min(int(os.getenv("MVE_GENERATOR_DAILY_CREATION_CAP", "600")), 650))
    remaining = max(0, daily_cap - _CREATED_TODAY)
    budget = min(per_run, remaining)
    if budget <= 0:
        return {
            "status": "MVE_GENERATOR_DAILY_CAP",
            "created_today": _CREATED_TODAY,
            "daily_cap": daily_cap,
            "market_creation": False,
            "order_entry": False,
            "ranked": [],
        }

    horizon_h = max(1, min(int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24")), 48))
    min_locked = Decimal(os.getenv("MVE_GENERATOR_MIN_LOCKED", "0.02"))
    max_candidates = max(20, min(int(os.getenv("MVE_GENERATOR_MAX_CANDIDATES", "80")), 250))
    wait_s = max(0.75, min(float(os.getenv("MVE_GENERATOR_RFQ_WAIT_SECONDS", "1.75")), 4.0))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_h)
    client = KalshiClient(base_url=g.BASE)
    open_rfqs: list[str] = []
    created = 0
    scored: list[dict[str, Any]] = []
    collections_seen = 0
    candidates_considered = 0
    try:
        collections = [c for c in await g._open_collections(client) if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))]
        if collections:
            shift = _COLLECTION_CURSOR % len(collections)
            collections = collections[shift:] + collections[:shift]
            _COLLECTION_CURSOR = (_COLLECTION_CURSOR + 1) % len(collections)

        for c in collections:
            if created >= budget:
                break
            collection = str(c.get("collection_ticker") or "")
            associated = [dict(x) for x in (c.get("associated_events") or []) if isinstance(x, dict)]
            if not associated:
                associated = [{"ticker": x, "is_yes_only": bool(c.get("is_all_yes", False))} for x in (c.get("associated_event_tickers") or [])]
            if len(associated) < 2:
                continue
            collections_seen += 1

            event_legs: dict[str, list[g.Leg]] = {}
            for erow in associated[:40]:
                event = str(erow.get("ticker") or "")
                if not event:
                    continue
                markets = await g._event_markets(client, event)
                sides = ("yes",) if bool(erow.get("is_yes_only")) or bool(c.get("is_all_yes")) else ("yes", "no")
                rows: list[g.Leg] = []
                for m in markets:
                    for side in sides:
                        leg = g._leg_from_market(m, event, side, horizon)
                        if leg is not None:
                            rows.append(leg)
                if rows:
                    event_legs[event] = rows
            if len(event_legs) < 2:
                continue

            candidates = g._candidate_combos(c, event_legs, max_candidates=max_candidates, min_locked=min_locked)
            candidates_considered += len(candidates)
            for hedge_all, legs in candidates:
                if created >= budget:
                    break
                key = g._combo_key(collection, legs)
                if key in _SEEN:
                    continue
                _SEEN.add(key)
                payload = {"selected_markets": [x.selected_payload() for x in legs], "with_market_payload": True}
                cr = await client.post(f"/multivariate_event_collections/{collection}", json=payload)
                if cr.status_code != 200:
                    continue
                created += 1
                _CREATED_TODAY += 1
                cb = cr.json() if cr.content else {}
                ticker = str(cb.get("market_ticker") or "") if isinstance(cb, dict) else ""
                market = dict(cb.get("market") or {}) if isinstance(cb, dict) and isinstance(cb.get("market"), dict) else {}
                if not ticker:
                    continue

                rid, quotes = await g._rfq_quotes(client, ticker, wait_s)
                if rid:
                    open_rfqs.append(rid)
                best = g._best_combo_yes(quotes)
                row: dict[str, Any] = {
                    "ticker": ticker,
                    "collection_ticker": collection,
                    "title": market.get("title") or market.get("yes_sub_title"),
                    "close_time": max(x.close_time for x in legs).isoformat(),
                    "legs": [x.selected_payload() for x in legs],
                    "leg_count": len(legs),
                    "single_cost_with_fees": str(hedge_all),
                    "rfq_id": rid,
                    "quote_count": len(quotes),
                    "generated": True,
                    "classification": "NO_QUOTE",
                }
                if best is not None:
                    all_in = hedge_all + best["combo_yes_cost"] + best["combo_fee"]
                    locked = g.ONE - all_in
                    row.update({
                        "best_quote": {
                            "quote_id": best["quote_id"],
                            "combo_yes_equivalent": str(best["combo_yes_cost"]),
                            "combo_fee_conservative": str(best["combo_fee"]),
                            "updated_ts": best["updated_ts"],
                        },
                        "all_in_cost_conservative": str(all_in),
                        "locked_profit_conservative": str(locked),
                        "classification": "EXECUTABLE_CANDIDATE" if locked >= min_locked else "NO_TRADE",
                    })
                scored.append(row)

        scored.sort(key=lambda x: Decimal(x.get("locked_profit_conservative") or "-9"), reverse=True)
        return {
            "status": "KALSHI_MVE_GENERATED_SCAN_V2",
            "observed_at": now.isoformat(),
            "collections_seen": collections_seen,
            "candidate_baskets_considered": candidates_considered,
            "markets_created_or_resolved": created,
            "created_today": _CREATED_TODAY,
            "daily_cap": daily_cap,
            "weekly_limit_documented": 5000,
            "seen_combo_keys_process": len(_SEEN),
            "supported_leg_counts": list(g.SUPPORTED_LEGS),
            "executable_candidates": sum(x.get("classification") == "EXECUTABLE_CANDIDATE" for x in scored),
            "ranked": scored,
            "market_creation": created > 0,
            "order_entry": False,
            "funds_at_risk": False,
        }
    finally:
        for rid in open_rfqs:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass
        await client.close()


def main() -> None:
    import json
    print(json.dumps(asyncio.run(generate_and_score()), separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
