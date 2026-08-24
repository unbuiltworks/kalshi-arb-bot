"""Bounded automatic Kalshi MVE combo generator and arb scorer.

This module actively constructs eligible 2-5 leg markets using Kalshi's public
Multivariate Event Collection API, then requests q=1 RFQs and scores the exact
scalar-safe synthetic hedge:

    long combo YES + long complement of every selected component.

Market creation/lookup is NOT order entry and risks no capital, but Kalshi caps
market creations at 5000/week.  The generator therefore uses a hard per-run
creation budget, only creates combinations whose component-hedge basket could
possibly clear the configured profit floor, and never accepts quotes.

The live supervisor may pass a generated ticker to the separately fail-closed
q=1 executor only after this shadow score is positive; the executor rebuilds
all quotes, depth, balance, position and fee inputs independently.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import itertools
import os
from typing import Any

from src.auth import KalshiClient

BASE = "https://external-api.kalshi.com/trade-api/v2"
D = Decimal
ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
SUPPORTED_LEGS = (2, 3, 4, 5)
SPORT_HINTS = (
    "NFL", "MLB", "NBA", "WNBA", "NHL", "NCAAF", "NCAAB", "ATP", "WTA",
    "PGA", "UFC", "SOCCER", "GAME", "FOOTBALL", "BASEBALL", "TENNIS",
    "HITS", "HOMER", "TOTALBASE", "STRIKEOUT", "PASS", "RUSH", "RECEIV",
    "GOAL", "ASSIST", "TOUCHDOWN", "OUTS",
)


@dataclass(frozen=True)
class Leg:
    event_ticker: str
    market_ticker: str
    side: str
    hedge_side: str
    hedge_price: Decimal
    hedge_fee: Decimal
    hedge_depth: Decimal
    close_time: datetime

    @property
    def hedge_all_in(self) -> Decimal:
        return self.hedge_price + self.hedge_fee

    def selected_payload(self) -> dict[str, str]:
        return {"event_ticker": self.event_ticker, "market_ticker": self.market_ticker, "side": self.side}



def _d(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def _ceil_cent(v: Decimal) -> Decimal:
    return (v / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


def _fee(p: Decimal) -> Decimal:
    if p <= ZERO or p >= ONE:
        return ZERO
    return _ceil_cent(D("0.07") * p * (ONE - p))


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _sportsish(*parts: Any) -> bool:
    text = " ".join(str(x or "") for x in parts).upper()
    return any(h in text for h in SPORT_HINTS)


def _leg_from_market(m: dict[str, Any], event_ticker: str, side: str, horizon: datetime) -> Leg | None:
    side = side.lower()
    if side not in {"yes", "no"}:
        return None
    if str(m.get("status") or "").lower() not in {"active", "open"}:
        return None
    close = _parse_dt(m.get("close_time") or m.get("expected_expiration_time") or m.get("expiration_time"))
    if close is None or close <= datetime.now(timezone.utc) or close > horizon:
        return None
    ticker = str(m.get("ticker") or "")
    if not ticker:
        return None
    if side == "yes":
        hedge_side = "no"
        px = _d(m.get("no_ask_dollars"))
        depth = _d(m.get("no_ask_size_fp") or m.get("no_ask_size"))
    else:
        hedge_side = "yes"
        px = _d(m.get("yes_ask_dollars"))
        depth = _d(m.get("yes_ask_size_fp") or m.get("yes_ask_size"))
    if px is None or depth is None or not (ZERO < px < ONE) or depth < ONE:
        return None
    return Leg(event_ticker, ticker, side, hedge_side, px, _fee(px), depth, close)


async def _open_collections(client: KalshiClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(10):
        params: dict[str, Any] = {"status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/multivariate_event_collections", params=params)
        if r.status_code != 200:
            break
        body = r.json() if r.content else {}
        rows = body.get("multivariate_contracts") or [] if isinstance(body, dict) else []
        out.extend(dict(x) for x in rows if isinstance(x, dict))
        cursor = str(body.get("cursor") or "") if isinstance(body, dict) else ""
        if not cursor:
            break
    return out


async def _event_markets(client: KalshiClient, event_ticker: str) -> list[dict[str, Any]]:
    r = await client.get("/markets", params={"event_ticker": event_ticker, "limit": 1000, "mve_filter": "exclude"})
    if r.status_code != 200:
        return []
    body = r.json() if r.content else {}
    return [dict(x) for x in (body.get("markets") or []) if isinstance(x, dict)] if isinstance(body, dict) else []


def _collection_leg_bounds(c: dict[str, Any]) -> tuple[int, int]:
    lo = int(c.get("size_min") or 2)
    hi = int(c.get("size_max") or 5)
    return max(2, lo), min(5, hi)


def _combo_key(collection: str, legs: tuple[Leg, ...]) -> str:
    return collection + "|" + "|".join(sorted(f"{x.event_ticker}:{x.market_ticker}:{x.side}" for x in legs))


def _candidate_combos(
    collection: dict[str, Any],
    event_legs: dict[str, list[Leg]],
    *,
    max_candidates: int,
    min_locked: Decimal,
) -> list[tuple[Decimal, tuple[Leg, ...]]]:
    lo, hi = _collection_leg_bounds(collection)
    single_per_event = bool(collection.get("is_single_market_per_event", True))
    all_yes = bool(collection.get("is_all_yes", False))
    pools: list[Leg] = []
    for event, rows in event_legs.items():
        # Keep the cheapest complement selections. This is an admissible pruning
        # for our synthetic objective because combo cost can never be negative.
        filtered = [x for x in rows if (not all_yes or x.side == "yes")]
        pools.extend(sorted(filtered, key=lambda x: x.hedge_all_in)[:4])
    pools.sort(key=lambda x: x.hedge_all_in)
    pools = pools[:40]

    threshold = ONE - min_locked
    ranked: list[tuple[Decimal, tuple[Leg, ...]]] = []
    for n in SUPPORTED_LEGS:
        if n < lo or n > hi or len(pools) < n:
            continue
        for legs in itertools.combinations(pools, n):
            if single_per_event and len({x.event_ticker for x in legs}) != n:
                continue
            hedge = sum((x.hedge_all_in for x in legs), ZERO)
            if hedge >= threshold:
                continue
            # Prefer more room for the combo quote; close-time tie-break is
            # handled by stable sorting from the underlying pool.
            ranked.append((hedge, legs))
    ranked.sort(key=lambda item: (item[0], max(x.close_time for x in item[1]), -len(item[1])))
    return ranked[:max_candidates]


async def _rfq_quotes(client: KalshiClient, ticker: str, wait_s: float) -> tuple[str | None, list[dict[str, Any]]]:
    r = await client.post("/communications/rfqs", json={
        "market_ticker": ticker, "contracts": 1, "rest_remainder": False, "replace_existing": True,
    })
    if r.status_code >= 300:
        return None, []
    body = r.json() if r.content else {}
    rid = str(body.get("id") or (body.get("rfq") or {}).get("id") or "") if isinstance(body, dict) else ""
    if not rid:
        return None, []
    deadline = asyncio.get_running_loop().time() + wait_s
    quotes: list[dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.25)
        qr = await client.get("/communications/quotes", params={"rfq_id": rid, "rfq_user_filter": "self", "limit": 100})
        if qr.status_code == 200:
            qb = qr.json() if qr.content else {}
            quotes = [dict(x) for x in (qb.get("quotes") or []) if isinstance(x, dict)] if isinstance(qb, dict) else []
    return rid, quotes


def _best_combo_yes(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for q in quotes:
        if str(q.get("status") or "").lower() not in {"", "open"}:
            continue
        nb = _d(q.get("no_bid_dollars"))
        if nb is None or not (ZERO < nb < ONE):
            continue
        cost = ONE - nb
        row = {"quote_id": str(q.get("id") or ""), "combo_yes_cost": cost, "combo_fee": _fee(cost), "updated_ts": q.get("updated_ts")}
        if row["quote_id"] and (best is None or cost < best["combo_yes_cost"]):
            best = row
    return best


async def generate_and_score() -> dict[str, Any]:
    enabled = os.getenv("ENABLE_KALSHI_MVE_GENERATOR", "NO").strip().upper() == "YES"
    if not enabled:
        return {"status": "MVE_GENERATOR_DISARMED", "market_creation": False, "order_entry": False, "ranked": []}

    max_create = max(1, min(int(os.getenv("MVE_GENERATOR_MAX_CREATIONS_PER_RUN", "20")), 50))
    max_candidates = max(max_create, min(int(os.getenv("MVE_GENERATOR_MAX_CANDIDATES", "60")), 250))
    horizon_h = max(1, min(int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24")), 48))
    min_locked = D(os.getenv("MVE_GENERATOR_MIN_LOCKED", "0.02"))
    wait_s = max(0.75, min(float(os.getenv("MVE_GENERATOR_RFQ_WAIT_SECONDS", "1.75")), 4.0))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_h)
    client = KalshiClient(base_url=BASE)
    open_rfqs: list[str] = []
    created = 0
    scored: list[dict[str, Any]] = []
    collections_seen = 0
    candidate_count = 0
    try:
        collections = await _open_collections(client)
        for c in collections:
            if created >= max_create:
                break
            collection = str(c.get("collection_ticker") or "")
            if not collection or not _sportsish(collection, c.get("title"), c.get("description"), c.get("series_ticker")):
                continue
            associated = [dict(x) for x in (c.get("associated_events") or []) if isinstance(x, dict)]
            if not associated:
                associated = [{"ticker": x, "is_yes_only": bool(c.get("is_all_yes", False))} for x in (c.get("associated_event_tickers") or [])]
            if len(associated) < 2:
                continue
            collections_seen += 1
            event_legs: dict[str, list[Leg]] = {}
            for erow in associated[:30]:
                event = str(erow.get("ticker") or "")
                if not event:
                    continue
                markets = await _event_markets(client, event)
                sides = ("yes",) if bool(erow.get("is_yes_only")) or bool(c.get("is_all_yes")) else ("yes", "no")
                legs: list[Leg] = []
                for m in markets:
                    for side in sides:
                        leg = _leg_from_market(m, event, side, horizon)
                        if leg is not None:
                            legs.append(leg)
                if legs:
                    event_legs[event] = legs
            if len(event_legs) < 2:
                continue

            candidates = _candidate_combos(c, event_legs, max_candidates=max_candidates, min_locked=min_locked)
            candidate_count += len(candidates)
            for hedge_all, legs in candidates:
                if created >= max_create:
                    break
                payload = {"selected_markets": [x.selected_payload() for x in legs], "with_market_payload": True}
                cr = await client.post(f"/multivariate_event_collections/{collection}", json=payload)
                if cr.status_code != 200:
                    continue
                created += 1
                cb = cr.json() if cr.content else {}
                ticker = str(cb.get("market_ticker") or "") if isinstance(cb, dict) else ""
                market = dict(cb.get("market") or {}) if isinstance(cb, dict) and isinstance(cb.get("market"), dict) else {}
                if not ticker:
                    continue
                rid, quotes = await _rfq_quotes(client, ticker, wait_s)
                if rid:
                    open_rfqs.append(rid)
                best = _best_combo_yes(quotes)
                row: dict[str, Any] = {
                    "ticker": ticker,
                    "collection_ticker": collection,
                    "title": market.get("title") or market.get("yes_sub_title"),
                    "close_time": (max(x.close_time for x in legs)).isoformat(),
                    "legs": [x.selected_payload() for x in legs],
                    "leg_count": len(legs),
                    "single_cost_with_fees": str(hedge_all),
                    "rfq_id": rid,
                    "quote_count": len(quotes),
                    "generated": True,
                    "market_creation_is_order_entry": False,
                }
                if best is None:
                    row["classification"] = "NO_QUOTE"
                else:
                    all_in = hedge_all + best["combo_yes_cost"] + best["combo_fee"]
                    locked = ONE - all_in
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

        scored.sort(key=lambda x: D(x.get("locked_profit_conservative") or "-9"), reverse=True)
        return {
            "status": "KALSHI_MVE_GENERATED_SCAN",
            "observed_at": now.isoformat(),
            "collections_seen": collections_seen,
            "candidate_baskets_considered": candidate_count,
            "markets_created_or_resolved": created,
            "creation_budget_per_run": max_create,
            "weekly_api_limit_note": "Kalshi documents 5000 MVE market creations/week",
            "supported_leg_counts": list(SUPPORTED_LEGS),
            "executable_candidates": sum(x.get("classification") == "EXECUTABLE_CANDIDATE" for x in scored),
            "ranked": scored[:max_candidates],
            "market_creation": True,
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
