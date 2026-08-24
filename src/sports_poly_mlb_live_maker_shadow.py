"""Continuous read-only, participation-gated Polymarket MLB maker shadow.

Architecture:
- slow broad discovery reuses the strict same-player/stat integer-ladder census;
- official MLB Stats API participation proof gates every candidate;
- only participation-ready candidates receive hot exact book polling;
- market-reported tick/minimum quantity are required (no hard-coded live tick);
- maker quote is recomputed to preserve >=3c after the current taker hedge;
- crossed-quote/near-touch telemetry measures fillability without placing orders.

Even after participation, Polymarket's delayed/suspended-game last-fair-market-price
branch remains residual rule risk. Therefore every row is shadow-only and explicitly
NOT guaranteed arbitrage. No authentication or mutation code is imported.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import os
from typing import Any, Mapping

import httpx

from src.mlb_live_participation import (
    candidate_game,
    fetch_game_boxscores,
    participation_proof,
)
from src.mlb_prop_rule_equivalence import assess_rule_equivalence
from src.sports_poly_current_fees import maker_then_taker_locked_profit
from src.sports_poly_mlb_maker_touch_probe import run_once as discovery_scan
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

D = Decimal
ONE = D("1")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"
PAGE = 100
MAX_PAGES = 50
DISCOVERY_SECONDS = max(60.0, float(os.getenv("SPORTS_POLY_MLB_MAKER_DISCOVERY_SECONDS", "180")))
PARTICIPATION_SECONDS = max(5.0, float(os.getenv("SPORTS_POLY_MLB_PARTICIPATION_SECONDS", "5")))
HOT_SECONDS = max(1.0, float(os.getenv("SPORTS_POLY_MLB_MAKER_HOT_SECONDS", "2")))


def _d(value: Any) -> D | None:
    try:
        return D(str(value))
    except Exception:
        return None


def _side(book: TopBook, side: str) -> tuple[D, D, D]:
    """Return (bid, ask, ask_depth) for YES or NO."""
    if side == "YES":
        return ONE - book.no_ask, book.yes_ask, book.yes_depth
    return ONE - book.yes_ask, book.no_ask, book.no_depth


def _safe_quote(
    *,
    maker_book: TopBook,
    maker_side: str,
    hedge_book: TopBook,
    hedge_side: str,
    tick: D,
    minimum_qty: D,
) -> dict[str, Any] | None:
    if tick <= 0 or minimum_qty <= 0 or minimum_qty > ONE:
        return None
    maker_bid, maker_ask, _ = _side(maker_book, maker_side)
    _, hedge_ask, hedge_depth = _side(hedge_book, hedge_side)
    if hedge_depth < ONE or maker_bid <= 0 or maker_ask <= maker_bid:
        return None
    upper = maker_ask - tick
    if upper < maker_bid:
        return None

    raw_steps = (upper - maker_bid) / tick
    steps = int(raw_steps.to_integral_value(rounding=ROUND_FLOOR))
    best: D | None = None
    best_econ: Mapping[str, D] | None = None
    for i in range(min(steps + 1, 5000)):
        px = maker_bid + tick * D(i)
        econ = maker_then_taker_locked_profit(
            maker_price=px, taker_price=hedge_ask, quantity=1
        )
        if D(str(econ["locked_profit"])) >= TARGET:
            best = px
            best_econ = econ
        else:
            break
    if best is None or best_econ is None:
        return None

    gap = maker_ask - best
    gap_ticks = gap / tick if tick > 0 else D("999")
    return {
        "maker_best_bid": str(maker_bid),
        "maker_ask": str(maker_ask),
        "safe_maker_price": str(best),
        "maker_tick": str(tick),
        "minimum_trade_qty": str(minimum_qty),
        "gap_to_touch": str(gap),
        "gap_to_touch_ticks": str(gap_ticks),
        "one_tick_from_touch": bool(gap_ticks <= ONE),
        "hedge_ask": str(hedge_ask),
        "hedge_q1_depth": str(hedge_depth),
        "maker_rebate": str(best_econ["maker_rebate"]),
        "hedge_taker_fee": str(best_econ["taker_fee"]),
        "safe_all_in": str(best_econ["all_in_cost"]),
        "safe_locked_profit": str(best_econ["locked_profit"]),
    }


async def _market_metadata(
    http: httpx.AsyncClient, slugs: set[str]
) -> dict[str, dict[str, Any]]:
    if not slugs:
        return {}
    found: dict[str, dict[str, Any]] = {}
    horizon = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat().replace("+00:00", "Z")
    for page in range(MAX_PAGES):
        response = await http.get(
            f"{BASE}/v1/events",
            params={
                "limit": PAGE,
                "offset": page * PAGE,
                "active": "true",
                "closed": "false",
                "ended": "false",
                "categories": "sports",
                "startTimeMax": horizon,
                "orderBy": "startTime",
                "orderDirection": "desc",
            },
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        events = body.get("events") or [] if isinstance(body, Mapping) else []
        for event in events:
            if not isinstance(event, Mapping):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, Mapping):
                    continue
                slug = str(market.get("slug") or "")
                if slug not in slugs:
                    continue
                found[slug] = {
                    "orderPriceMinTickSize": market.get("orderPriceMinTickSize"),
                    "minimumTradeQty": market.get("minimumTradeQty"),
                    "acceptingOrders": market.get("acceptingOrders"),
                    "active": market.get("active"),
                    "closed": market.get("closed"),
                    "status": market.get("status"),
                    "gameStartTime": market.get("gameStartTime"),
                }
        if slugs.issubset(found) or len(events) < PAGE:
            break
    return found


async def _book(http: httpx.AsyncClient, slug: str) -> TopBook | None:
    try:
        response = await http.get(f"{BASE}/v1/markets/{slug}/book")
        if response.status_code != 200:
            return None
        return poly_top_book(response.json())
    except Exception:
        return None


def _candidate_key(row: Mapping[str, Any]) -> str:
    return "|".join(
        str(row.get(k) or "")
        for k in ("direction", "maker_slug", "hedge_slug")
    )


async def _participation(
    http: httpx.AsyncClient,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dates = {
        game[0]
        for row in rows
        for game in [candidate_game(str(row.get("maker_slug") or ""))]
        if game is not None
    }
    try:
        games = await fetch_game_boxscores(http, dates)
    except Exception as exc:
        return [], {"error": f"{type(exc).__name__}: {str(exc)[:240]}"}

    ready: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    gate_counts: Counter[str] = Counter()
    for row in rows:
        family = str(row.get("family") or "")
        proof = participation_proof(
            family=family,
            player_name=str(row.get("player") or ""),
            poly_slug=str(row.get("maker_slug") or ""),
            games=games,
        )
        assessment = assess_rule_equivalence(family, proof)
        status_counts[assessment.status.value] += 1
        gate_counts[assessment.required_gate] += 1
        if assessment.settlement_equivalent_now:
            ready_row = dict(row)
            ready_row["participation_proof_source"] = proof.source
            ready.append(ready_row)
    return ready, {
        "games_with_live_or_final_boxscore": len(games),
        "assessed": len(rows),
        "ready": len(ready),
        "status_counts": dict(status_counts),
        "gate_counts": dict(gate_counts),
    }


async def main() -> None:
    state: dict[str, dict[str, Any]] = defaultdict(dict)
    candidates: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, Any]] = {}
    ready: list[dict[str, Any]] = []
    participation_diag: dict[str, Any] = {"assessed": 0, "ready": 0}
    ready_keys: set[str] = set()
    next_discovery = 0.0
    next_participation = 0.0
    cycle = 0

    timeout = httpx.Timeout(12.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        while True:
            loop = asyncio.get_running_loop()
            now_mono = loop.time()
            if not candidates or now_mono >= next_discovery:
                try:
                    discovery = await discovery_scan()
                    candidates = [dict(r) for r in (discovery.get("top") or []) if isinstance(r, Mapping)]
                    slugs = {
                        str(r.get(field) or "")
                        for r in candidates
                        for field in ("maker_slug", "hedge_slug")
                        if str(r.get(field) or "")
                    }
                    metadata = await _market_metadata(http, slugs)
                    next_discovery = loop.time() + DISCOVERY_SECONDS
                    next_participation = 0.0
                    print(
                        "SPORTS_POLY_MLB_LIVE_MAKER_DISCOVERY "
                        + json.dumps(
                            {
                                "observed_at": datetime.now(timezone.utc).isoformat(),
                                "safe_discovery_candidates": len(candidates),
                                "metadata_slugs": len(metadata),
                                "discovery_seconds": DISCOVERY_SECONDS,
                                "participation_seconds": PARTICIPATION_SECONDS,
                                "hot_seconds": HOT_SECONDS,
                                "order_entry": False,
                            },
                            separators=(",", ":"),
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        "SPORTS_POLY_MLB_LIVE_MAKER_DISCOVERY_ERROR "
                        + json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}", "order_entry": False}),
                        flush=True,
                    )
                    await asyncio.sleep(PARTICIPATION_SECONDS)
                    continue

            now_mono = loop.time()
            if now_mono >= next_participation:
                ready, participation_diag = await _participation(http, candidates)
                next_participation = loop.time() + PARTICIPATION_SECONDS
                new_ready_keys = {_candidate_key(row) for row in ready}
                if new_ready_keys != ready_keys:
                    entered = sorted(new_ready_keys - ready_keys)
                    exited = sorted(ready_keys - new_ready_keys)
                    print(
                        "SPORTS_POLY_MLB_PARTICIPATION_TRANSITION "
                        + json.dumps(
                            {
                                "observed_at": datetime.now(timezone.utc).isoformat(),
                                "ready": len(new_ready_keys),
                                "entered": entered[:25],
                                "exited": exited[:25],
                                "order_entry": False,
                            },
                            separators=(",", ":"),
                        ),
                        flush=True,
                    )
                    ready_keys = new_ready_keys

            hot_rows: list[dict[str, Any]] = []
            near_touch = 0
            crossed = 0
            safe_now = 0
            sem = asyncio.Semaphore(10)

            async def price(row: dict[str, Any]) -> dict[str, Any] | None:
                maker_slug = str(row.get("maker_slug") or "")
                hedge_slug = str(row.get("hedge_slug") or "")
                maker_meta = metadata.get(maker_slug) or {}
                hedge_meta = metadata.get(hedge_slug) or {}
                tick = _d(maker_meta.get("orderPriceMinTickSize"))
                min_qty = _d(maker_meta.get("minimumTradeQty"))
                hedge_min_qty = _d(hedge_meta.get("minimumTradeQty"))
                if tick is None or min_qty is None or hedge_min_qty is None or hedge_min_qty > ONE:
                    return None
                if maker_meta.get("closed") is True or maker_meta.get("active") is False:
                    return None
                if hedge_meta.get("closed") is True or hedge_meta.get("active") is False:
                    return None
                async with sem:
                    maker_book, hedge_book = await asyncio.gather(
                        _book(http, maker_slug), _book(http, hedge_slug)
                    )
                if maker_book is None or hedge_book is None:
                    return None
                scored = _safe_quote(
                    maker_book=maker_book,
                    maker_side=str(row.get("maker_side") or ""),
                    hedge_book=hedge_book,
                    hedge_side=str(row.get("hedge_side") or ""),
                    tick=tick,
                    minimum_qty=min_qty,
                )
                if scored is None:
                    return None
                key = _candidate_key(row)
                prev_quote = _d(state[key].get("last_safe_maker_price"))
                maker_ask = _d(scored.get("maker_ask"))
                crossed_proxy = bool(
                    prev_quote is not None and maker_ask is not None and maker_ask <= prev_quote
                )
                state[key]["cycles"] = int(state[key].get("cycles") or 0) + 1
                state[key]["near_touch_cycles"] = int(state[key].get("near_touch_cycles") or 0) + int(bool(scored["one_tick_from_touch"]))
                state[key]["crossed_proxy_count"] = int(state[key].get("crossed_proxy_count") or 0) + int(crossed_proxy)
                state[key]["last_safe_maker_price"] = scored["safe_maker_price"]
                state[key].setdefault("first_seen", datetime.now(timezone.utc).isoformat())
                state[key]["last_seen"] = datetime.now(timezone.utc).isoformat()
                return {
                    **row,
                    **scored,
                    "crossed_prior_shadow_quote_proxy": crossed_proxy,
                    "tracking": dict(state[key]),
                    "participation_gate": "PASSED",
                    "rule_classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
                    "execution_eligible": False,
                    "order_entry": False,
                    "realized": False,
                }

            if ready:
                priced = await asyncio.gather(*(price(row) for row in ready))
                hot_rows = [r for r in priced if r is not None]
                safe_now = len(hot_rows)
                near_touch = sum(bool(r.get("one_tick_from_touch")) for r in hot_rows)
                crossed = sum(bool(r.get("crossed_prior_shadow_quote_proxy")) for r in hot_rows)

            cycle += 1
            summary = {
                "status": "SPORTS_POLY_MLB_LIVE_MAKER_SHADOW_V2",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "cycle": cycle,
                "discovery_candidates": len(candidates),
                "participation_ready": len(ready),
                "post_participation_safe_3c": safe_now,
                "post_participation_one_tick_from_touch_3c": near_touch,
                "crossed_prior_shadow_quote_proxy": crossed,
                "tracked_candidates": len(state),
                "participation": participation_diag,
                "top": sorted(
                    hot_rows,
                    key=lambda r: (
                        bool(r.get("crossed_prior_shadow_quote_proxy")),
                        bool(r.get("one_tick_from_touch")),
                        D(str(r.get("safe_locked_profit") or "0")),
                    ),
                    reverse=True,
                )[:25],
                "target_after_fee_profit": str(TARGET),
                "maker_tick_source": "market.orderPriceMinTickSize",
                "minimum_qty_source": "market.minimumTradeQty",
                "fillability_note": "crossed_prior_shadow_quote_proxy is evidence only; it is not a confirmed fill or realized P&L",
                "rule_warning": "Participation gate removes player-nonparticipation branch, but suspension/delay LFMP remains residual rule risk",
                "guaranteed_arbitrage": False,
                "execution_eligible": False,
                "order_entry": False,
                "realized": False,
            }
            print(
                "SPORTS_POLY_MLB_LIVE_MAKER_SHADOW "
                + json.dumps(summary, separators=(",", ":"), default=str),
                flush=True,
            )
            await asyncio.sleep(HOT_SECONDS if ready else PARTICIPATION_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
