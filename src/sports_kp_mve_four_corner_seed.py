"""Bounded one-shot Kalshi four-corner MVE partition seed.

For two normally resolving binary component markets A and B, exactly one of
YY, YN, NY, NN is true. Buying one YES contract on all four conjunctions therefore
pays exactly $1. This probe deliberately manufactures one complete four-corner
partition, requests q=1 RFQs, and scores conservative after-fee buy cost.

Disabled unless ENABLE_MVE_FOUR_CORNER_SEED=YES. Hard cap: one component pair and
four market creations per process. It never accepts a quote or submits an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_combo_generator as g
from src import sports_kp_mve_market_quote_patch as patch

D = Decimal
ONE = D("1")
HALF = D("0.5")
TARGET = D("0.03")
CORNERS = (("yes", "yes"), ("yes", "no"), ("no", "yes"), ("no", "no"))


@dataclass(frozen=True)
class Component:
    event_ticker: str
    market_ticker: str
    yes: Any
    no: Any
    title: str

    @property
    def approximate_yes_mid(self) -> Decimal:
        yes_bid_equiv = ONE - self.no.ask
        return (self.yes.ask + yes_bid_equiv) / D("2")

    @property
    def min_depth(self) -> Decimal:
        return min(self.yes.depth, self.no.depth)


def _component(market: Mapping[str, Any], event: str, horizon: datetime) -> Component | None:
    yes = patch.selected_from_market(market, event, "yes", horizon)
    no = patch.selected_from_market(market, event, "no", horizon)
    ticker = str(market.get("ticker") or "")
    if yes is None or no is None or not ticker:
        return None
    mid = (yes.ask + (ONE - no.ask)) / D("2")
    # Favor genuinely two-sided components so all four corners have a chance to quote.
    if not (D("0.25") <= mid <= D("0.75")):
        return None
    return Component(event, ticker, yes, no, str(market.get("title") or market.get("yes_sub_title") or ticker))


def _rank_pair(components: list[Component]) -> tuple[Component, Component] | None:
    ranked: list[tuple[Decimal, Decimal, str, Component, Component]] = []
    for i, a in enumerate(components):
        for b in components[i + 1:]:
            if a.event_ticker == b.event_ticker or a.market_ticker == b.market_ticker:
                continue
            center = abs(a.approximate_yes_mid - HALF) + abs(b.approximate_yes_mid - HALF)
            depth = min(a.min_depth, b.min_depth)
            ranked.append((center, -depth, a.market_ticker + "|" + b.market_ticker, a, b))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    return (ranked[0][3], ranked[0][4]) if ranked else None


def _selected(component: Component, side: str) -> dict[str, str]:
    return {
        "event_ticker": component.event_ticker,
        "market_ticker": component.market_ticker,
        "side": side,
    }


def _best_yes_cost(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for q in quotes:
        if str(q.get("status") or "").lower() not in {"", "open"}:
            continue
        nb = g._d(q.get("no_bid_dollars"))
        if nb is None or not (D("0") < nb < ONE):
            continue
        px = ONE - nb
        row = {
            "quote_id": str(q.get("id") or ""),
            "price": px,
            "fee": g._fee(px),
            "updated_ts": q.get("updated_ts"),
        }
        if row["quote_id"] and (best is None or px < best["price"]):
            best = row
    return best


def score_partition(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) != 4 or any(row.get("best_yes") is None for row in rows):
        return None
    total = sum((D(str(row["best_yes"]["price"])) + D(str(row["best_yes"]["fee"])) for row in rows), D("0"))
    locked = ONE - total
    return {
        "all_in_partition_cost": str(total),
        "locked_profit": str(locked),
        "classification": "FOUR_CORNER_SHADOW_EXECUTABLE" if locked >= TARGET else "FOUR_CORNER_SHADOW_NO_TRADE",
        "normal_outcome_payout_floor": "1.00",
    }


async def run_once() -> dict[str, Any]:
    enabled = os.getenv("ENABLE_MVE_FOUR_CORNER_SEED", "NO").strip().upper() == "YES"
    if not enabled:
        out = {"status": "MVE_FOUR_CORNER_SEED_DISARMED", "market_creation": False, "order_entry": False, "realized": False}
        print("SPORTS_MVE_FOUR_CORNER_SEED " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    wait_s = max(D("0.75"), min(D(str(os.getenv("MVE_FOUR_CORNER_RFQ_WAIT_SECONDS", "1.75"))), D("4")))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    client = KalshiClient(base_url=g.BASE)
    open_rfqs: list[str] = []
    created: list[str] = []
    rows: list[dict[str, Any]] = []
    chosen_collection = ""
    chosen_pair: tuple[Component, Component] | None = None
    try:
        collections = [
            c for c in await g._open_collections(client)
            if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))
            and not bool(c.get("is_all_yes", False))
        ]
        for collection in collections:
            lo, hi = g._collection_leg_bounds(dict(collection))
            if lo > 2 or hi < 2:
                continue
            associated = [dict(x) for x in (collection.get("associated_events") or []) if isinstance(x, Mapping)]
            if not associated:
                associated = [{"ticker": x, "is_yes_only": False} for x in (collection.get("associated_event_tickers") or [])]
            components: list[Component] = []
            for erow in associated[:24]:
                if bool(erow.get("is_yes_only")):
                    continue
                event = str(erow.get("ticker") or "")
                if not event:
                    continue
                markets = await g._event_markets(client, event)
                candidates = [x for x in (_component(m, event, horizon) for m in markets) if x is not None]
                candidates.sort(key=lambda x: (abs(x.approximate_yes_mid - HALF), -x.min_depth, x.market_ticker))
                components.extend(candidates[:2])
            pair = _rank_pair(components)
            if pair is not None:
                chosen_collection = str(collection.get("collection_ticker") or "")
                chosen_pair = pair
                break

        if not chosen_collection or chosen_pair is None:
            out = {
                "status": "MVE_FOUR_CORNER_NO_COMPONENT_PAIR",
                "market_creation": False, "rfq": False, "quote_acceptance": False,
                "order_entry": False, "realized": False,
            }
            print("SPORTS_MVE_FOUR_CORNER_SEED " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        a, b = chosen_pair
        for aside, bside in CORNERS:
            response = await client.post(
                f"/multivariate_event_collections/{chosen_collection}",
                json={"selected_markets": [_selected(a, aside), _selected(b, bside)], "with_market_payload": True},
            )
            row: dict[str, Any] = {
                "corner": aside[0].upper() + bside[0].upper(),
                "a_side": aside,
                "b_side": bside,
                "creation_http": response.status_code,
                "order_entry": False,
            }
            if response.status_code != 200:
                row["classification"] = "CREATE_REJECTED"
                rows.append(row)
                continue
            body = response.json() if response.content else {}
            ticker = str(body.get("market_ticker") or "") if isinstance(body, Mapping) else ""
            row["ticker"] = ticker or None
            if not ticker:
                row["classification"] = "CREATE_NO_TICKER"
                rows.append(row)
                continue
            created.append(ticker)
            rid, quotes = await g._rfq_quotes(client, ticker, float(wait_s))
            if rid:
                open_rfqs.append(rid)
            best = _best_yes_cost(quotes)
            row.update({
                "rfq_id_present": bool(rid),
                "quote_count": len(quotes),
                "best_yes": None if best is None else {
                    "price": str(best["price"]),
                    "fee": str(best["fee"]),
                    "updated_ts": best["updated_ts"],
                },
            })
            rows.append(row)

        score = score_partition(rows)
        out = {
            "status": "MVE_FOUR_CORNER_SEED_V1",
            "observed_at": now.isoformat(),
            "collection_ticker": chosen_collection,
            "component_a": {
                "event": a.event_ticker, "ticker": a.market_ticker, "title": a.title,
                "approx_yes_mid": str(a.approximate_yes_mid), "min_depth": str(a.min_depth),
            },
            "component_b": {
                "event": b.event_ticker, "ticker": b.market_ticker, "title": b.title,
                "approx_yes_mid": str(b.approximate_yes_mid), "min_depth": str(b.min_depth),
            },
            "created_tickers": created,
            "corners_created": len(created),
            "corners_priced": sum(row.get("best_yes") is not None for row in rows),
            "score": score,
            "target_locked": str(TARGET),
            "math_identity": "YY+YN+NY+NN=1 for two normally-resolving binary components",
            "void_cancel_rule_gate": "REQUIRED_BEFORE_LIVE",
            "market_creation": bool(created),
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
            "rows": rows,
        }
        print("SPORTS_MVE_FOUR_CORNER_SEED " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        for rid in open_rfqs:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass
        await client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_once())
