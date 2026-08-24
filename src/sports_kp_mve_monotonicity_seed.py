"""Bounded one-shot Kalshi MVE monotonicity seed probe.

The guaranteed identity is the same scalar-safe inequality used by v41/v42:
for a conjunction with selected values x_i in [0,1],

    combo_NO + x_k = 1 - product(x_i) + x_k >= 1

for any selected component k.  Equivalently, a combo YES bid must never be worth
more than a selected component ask after costs.  This probe deliberately creates
a tiny number of 2-leg MVEs with one moderate-probability anchor and one near-
certain booster, then requests q=1 RFQs and measures whether the combo YES bid
overlaps the anchor ask enough to lock >=3c after conservative fees.

Market creation/RFQ observation is not order entry and risks no trading capital.
The probe never accepts an RFQ quote, submits an order, or promotes a candidate
live.  It is disabled unless ENABLE_MVE_MONOTONICITY_SEED=YES and hard-capped at
four creation attempts per process.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_combo_generator as g
from src import sports_kp_mve_same_venue_scan as scan

D = Decimal
ONE = D("1")
TARGET = D("0.03")
MAX_ATTEMPTS_HARD = 4


@dataclass(frozen=True)
class Selected:
    event_ticker: str
    market_ticker: str
    side: str
    ask: Decimal
    depth: Decimal
    fee: Decimal
    close_time: datetime

    @property
    def all_in(self) -> Decimal:
        return self.ask + self.fee

    def payload(self) -> dict[str, str]:
        return {
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "side": self.side,
        }


def _selected_from_market(market: Mapping[str, Any], event_ticker: str, side: str, horizon: datetime) -> Selected | None:
    side = str(side or "").lower()
    if side not in {"yes", "no"}:
        return None
    if str(market.get("status") or "").lower() not in {"active", "open"}:
        return None
    close = g._parse_dt(market.get("close_time") or market.get("expected_expiration_time") or market.get("expiration_time"))
    if close is None or close <= datetime.now(timezone.utc) or close > horizon:
        return None
    ticker = str(market.get("ticker") or "")
    if not ticker:
        return None
    if side == "yes":
        ask = g._d(market.get("yes_ask_dollars"))
        depth = g._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    else:
        ask = g._d(market.get("no_ask_dollars"))
        depth = g._d(market.get("no_ask_size_fp") or market.get("no_ask_size"))
        if ask is None or depth is None:
            yes_bid = g._d(market.get("yes_bid_dollars"))
            yes_depth = g._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
            if yes_bid is not None and yes_depth is not None:
                ask = ONE - yes_bid
                depth = yes_depth
    if ask is None or depth is None or depth < ONE or not (D("0") < ask < ONE):
        return None
    return Selected(event_ticker, ticker, side, ask, depth, g._fee(ask), close)


def _rank_pairs(collection: Mapping[str, Any], selected: list[Selected], *, limit: int) -> list[tuple[Selected, Selected]]:
    """Rank anchor+near-certain booster pairs for monotonicity observation."""
    all_yes = bool(collection.get("is_all_yes", False))
    single_per_event = bool(collection.get("is_single_market_per_event", True))
    anchors = [x for x in selected if D("0.20") <= x.ask <= D("0.90") and (not all_yes or x.side == "yes")]
    boosters = [x for x in selected if x.ask >= D("0.94") and (not all_yes or x.side == "yes")]
    ranked: list[tuple[Decimal, datetime, str, tuple[Selected, Selected]]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for anchor in anchors:
        for booster in boosters:
            if anchor.market_ticker == booster.market_ticker:
                continue
            if single_per_event and anchor.event_ticker == booster.event_ticker:
                continue
            key = tuple(sorted((f"{anchor.market_ticker}:{anchor.side}", f"{booster.market_ticker}:{booster.side}")))
            if key in seen:
                continue
            seen.add(key)
            # With a near-certain booster, fair conjunction should sit only a
            # little below the anchor.  Smaller rough product penalty is better.
            penalty = anchor.ask * (ONE - booster.ask)
            ranked.append((penalty, max(anchor.close_time, booster.close_time), anchor.market_ticker, (anchor, booster)))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[3] for x in ranked[:limit]]


def _best_rfq_sides(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    best_yes_bid: Decimal | None = None
    best_no_bid: Decimal | None = None
    valid = 0
    for q in quotes:
        if str(q.get("status") or "").lower() not in {"", "open"}:
            continue
        valid += 1
        yb = g._d(q.get("yes_bid_dollars"))
        nb = g._d(q.get("no_bid_dollars"))
        if yb is not None and D("0") < yb < ONE and (best_yes_bid is None or yb > best_yes_bid):
            best_yes_bid = yb
        if nb is not None and D("0") < nb < ONE and (best_no_bid is None or nb > best_no_bid):
            best_no_bid = nb
    return {"valid_quotes": valid, "best_yes_bid": best_yes_bid, "best_no_bid": best_no_bid}


def _score_minimal_no(anchor: Selected, best_yes_bid: Decimal | None) -> dict[str, Any] | None:
    if best_yes_bid is None:
        return None
    combo_no = ONE - best_yes_bid
    combo_fee = g._fee(combo_no)
    all_in = anchor.all_in + combo_no + combo_fee
    locked = ONE - all_in
    return {
        "anchor_market": anchor.market_ticker,
        "anchor_side": anchor.side,
        "anchor_ask": str(anchor.ask),
        "anchor_depth_fp": str(anchor.depth),
        "anchor_fee": str(anchor.fee),
        "combo_yes_bid": str(best_yes_bid),
        "combo_no_equivalent": str(combo_no),
        "combo_no_fee": str(combo_fee),
        "all_in_cost_conservative": str(all_in),
        "locked_profit_conservative": str(locked),
        "classification": "SHADOW_EXECUTABLE_MONOTONICITY" if locked >= TARGET else "SHADOW_NO_TRADE",
    }


async def run_once() -> dict[str, Any]:
    enabled = os.getenv("ENABLE_MVE_MONOTONICITY_SEED", "NO").strip().upper() == "YES"
    if not enabled:
        out = {"status": "MVE_MONOTONICITY_SEED_DISARMED", "market_creation": False, "order_entry": False, "realized": False}
        print("SPORTS_MVE_MONOTONICITY_SEED " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    max_attempts = max(1, min(int(os.getenv("MVE_MONOTONICITY_SEED_MAX", "4")), MAX_ATTEMPTS_HARD))
    wait_s = max(0.75, min(float(os.getenv("MVE_MONOTONICITY_SEED_RFQ_WAIT_SECONDS", "1.5")), 3.0))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    client = KalshiClient(base_url=g.BASE)
    open_rfqs: list[str] = []
    attempts: list[dict[str, Any]] = []
    created_tickers: list[str] = []
    try:
        collections = [c for c in await g._open_collections(client) if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))]
        candidate_pool: list[tuple[Decimal, str, Selected, Selected]] = []
        for collection in collections:
            lo, hi = g._collection_leg_bounds(collection)
            if lo > 2 or hi < 2:
                continue
            associated = [dict(x) for x in (collection.get("associated_events") or []) if isinstance(x, Mapping)]
            if not associated:
                associated = [{"ticker": x, "is_yes_only": bool(collection.get("is_all_yes", False))} for x in (collection.get("associated_event_tickers") or [])]
            selected: list[Selected] = []
            # Bounded public discovery; enough breadth to find moderate anchors and
            # near-certain boosters without reviving the old high-volume generator.
            for erow in associated[:18]:
                event = str(erow.get("ticker") or "")
                if not event:
                    continue
                markets = await g._event_markets(client, event)
                sides = ("yes",) if bool(erow.get("is_yes_only")) or bool(collection.get("is_all_yes", False)) else ("yes", "no")
                for market in markets:
                    for side in sides:
                        item = _selected_from_market(market, event, side, horizon)
                        if item is not None:
                            selected.append(item)
            pairs = _rank_pairs(collection, selected, limit=max_attempts * 3)
            for anchor, booster in pairs:
                penalty = anchor.ask * (ONE - booster.ask)
                candidate_pool.append((penalty, str(collection.get("collection_ticker") or ""), anchor, booster))

        candidate_pool.sort(key=lambda x: (x[0], max(x[2].close_time, x[3].close_time), x[1]))
        used_payloads: set[str] = set()
        for _penalty, collection_ticker, anchor, booster in candidate_pool:
            if len(attempts) >= max_attempts:
                break
            payload_key = "|".join(sorted((f"{anchor.market_ticker}:{anchor.side}", f"{booster.market_ticker}:{booster.side}")))
            if payload_key in used_payloads or not collection_ticker:
                continue
            used_payloads.add(payload_key)
            response = await client.post(
                f"/multivariate_event_collections/{collection_ticker}",
                json={"selected_markets": [anchor.payload(), booster.payload()], "with_market_payload": True},
            )
            row: dict[str, Any] = {
                "collection_ticker": collection_ticker,
                "anchor": {"market_ticker": anchor.market_ticker, "side": anchor.side, "ask": str(anchor.ask), "fee": str(anchor.fee)},
                "booster": {"market_ticker": booster.market_ticker, "side": booster.side, "ask": str(booster.ask), "fee": str(booster.fee)},
                "creation_http": response.status_code,
                "market_creation_is_order_entry": False,
                "order_entry": False,
            }
            if response.status_code != 200:
                row["classification"] = "CREATE_REJECTED"
                attempts.append(row)
                continue
            body = response.json() if response.content else {}
            ticker = str(body.get("market_ticker") or "") if isinstance(body, Mapping) else ""
            row["ticker"] = ticker or None
            if not ticker:
                row["classification"] = "CREATE_NO_TICKER"
                attempts.append(row)
                continue
            created_tickers.append(ticker)
            rid, quotes = await g._rfq_quotes(client, ticker, wait_s)
            if rid:
                open_rfqs.append(rid)
            sides = _best_rfq_sides(quotes)
            row.update({
                "rfq_id_present": bool(rid),
                "quote_count": len(quotes),
                "valid_quote_count": sides["valid_quotes"],
                "positive_yes_bid": sides["best_yes_bid"] is not None,
                "positive_no_bid": sides["best_no_bid"] is not None,
                "minimal_no": _score_minimal_no(anchor, sides["best_yes_bid"]),
            })
            minimal = row.get("minimal_no") or {}
            row["classification"] = minimal.get("classification") or "NO_BUY_NO_QUOTE_SIDE"
            attempts.append(row)

        executable = [x for x in attempts if x.get("classification") == "SHADOW_EXECUTABLE_MONOTONICITY"]
        priced = [x for x in attempts if isinstance(x.get("minimal_no"), Mapping)]
        priced.sort(key=lambda x: D(str((x.get("minimal_no") or {}).get("locked_profit_conservative") or "-9")), reverse=True)
        out = {
            "status": "MVE_MONOTONICITY_SEED_V1",
            "observed_at": now.isoformat(),
            "creation_attempt_cap": max_attempts,
            "attempts": len(attempts),
            "created_tickers": created_tickers,
            "positive_yes_bid_count": sum(bool(x.get("positive_yes_bid")) for x in attempts),
            "positive_no_bid_count": sum(bool(x.get("positive_no_bid")) for x in attempts),
            "shadow_executable": len(executable),
            "best": priced[0] if priced else None,
            "target_locked": str(TARGET),
            "market_creation": bool(created_tickers),
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
            "rows": attempts,
        }
        print("SPORTS_MVE_MONOTONICITY_SEED " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
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
