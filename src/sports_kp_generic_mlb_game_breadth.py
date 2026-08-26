"""Read-only K<->P MLB game-side breadth and maker-input census.

This lane deliberately does NOT certify settlement equivalence. It reuses the
normal-event moneyline/spread/total identity layer to find exact proposition
matches, snapshots both books, and measures q=1 after-fee taker economics plus
how much price improvement is still required to reach the fixed 3c net floor.

Every row is RULE_INCOMPLETE / NOT_EXECUTION_ELIGIBLE. No order-entry code is
imported, no lifetime/capture claims are made from these snapshots, and results
must not be counted as guaranteed-arbitrage or realized capacity.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
import statistics
import time
from typing import Any, Iterable, Mapping

import httpx

from src.kalshi_game_rest_books import _nested_quote
from src.mlb_game_side_normalization import (
    NormalizedSide,
    normalize_kalshi_mlb,
    normalize_polymarket_mlb,
)
from src.polymarket_kalshi_shadow import kalshi_taker_fee, polymarket_taker_fee
from src.polymarket_kalshi_shadow_v2 import PolyBookStore

logger = logging.getLogger("kalshi_arb.sports_kp_generic_mlb_game_breadth")
D = Decimal
POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_SERIES = ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL")
POLY_HEADERS = {
    "Origin": "https://polymarket.us",
    "User-Agent": "Mozilla/5.0 arb-breadth/1.0",
    "Connection": "close",
}
FIXED_TARGET = D("0.03")
MAX_START_SKEW_MS = 5 * 60 * 1000


@dataclass(frozen=True)
class Pair:
    poly: NormalizedSide
    kalshi: NormalizedSide
    start_skew_ms: int

    @property
    def contract_key(self) -> str:
        return self.poly.contract_key

    @property
    def family(self) -> str:
        return self.poly.identity.market_family

    @property
    def group_key(self) -> str:
        """Collapse complementary binary representations into one game market."""
        line = self.poly.identity.line
        line_key = "" if line is None else format(abs(line).normalize(), "f")
        return "|".join((self.poly.identity.event.event_key(), self.family, line_key))


@dataclass(frozen=True)
class OrientedBook:
    prop_ask: Decimal
    prop_depth: Decimal
    opp_ask: Decimal
    opp_depth: Decimal


def _orient(quote: Any, side: str) -> OrientedBook | None:
    side = str(side or "").upper()
    if side == "YES":
        return OrientedBook(quote.yes_ask, quote.yes_depth, quote.no_ask, quote.no_depth)
    if side == "NO":
        return OrientedBook(quote.no_ask, quote.no_depth, quote.yes_ask, quote.yes_depth)
    return None


def _distinct_physical_sides(rows: Iterable[NormalizedSide]) -> list[NormalizedSide]:
    """Return unique physical representations of one already-exact semantic key.

    A semantic proposition can legitimately be represented by multiple physical
    markets on one venue (for example YES on P and NO on an explicit complement
    market). The census must not choose an arbitrary representative before it
    sees executable books. Deduplicate only exact physical market+side mirrors;
    semantic identity has already been enforced by contract_key.
    """
    distinct: dict[tuple[str, str], NormalizedSide] = {}
    for row in rows:
        distinct[(row.venue_market_id, str(row.side).upper())] = row
    return list(distinct.values())


def _pair_normal_event_sides(
    poly: Iterable[NormalizedSide],
    kalshi: Iterable[NormalizedSide],
) -> tuple[list[Pair], int, int]:
    """Enumerate physical representations only inside exact shared semantic keys.

    The prior implementation rejected a key whenever either venue exposed more
    than one physical YES representation. That discarded real overlap before
    books could be priced. Here we keep the exact contract-key gate, enumerate
    each distinct physical representation, enforce the same start-skew gate,
    and let downstream executable-book pricing plus semantic group dedupe choose
    the best representation. No team/start/line/rule identity is loosened.

    Returns (pairs, unrecoverable_shared_keys, expanded_shared_keys).
    """
    pidx: dict[str, list[NormalizedSide]] = {}
    kidx: dict[str, list[NormalizedSide]] = {}
    for row in poly:
        pidx.setdefault(row.contract_key, []).append(row)
    for row in kalshi:
        kidx.setdefault(row.contract_key, []).append(row)

    pairs: list[Pair] = []
    unrecoverable = 0
    expanded = 0
    for key in sorted(set(pidx) & set(kidx)):
        prows = _distinct_physical_sides(pidx[key])
        krows = _distinct_physical_sides(kidx[key])
        if not prows or not krows:
            unrecoverable += 1
            continue
        if len(prows) > 1 or len(krows) > 1:
            expanded += 1
        emitted = 0
        for p in prows:
            for k in krows:
                skew = abs(p.identity.event.scheduled_start_ms - k.identity.event.scheduled_start_ms)
                if skew > MAX_START_SKEW_MS:
                    continue
                pairs.append(Pair(poly=p, kalshi=k, start_skew_ms=skew))
                emitted += 1
        if emitted == 0:
            unrecoverable += 1
    return pairs, unrecoverable, expanded


def _q1_economics(
    pair: Pair,
    poly_quote: Any,
    kalshi_quote: Any,
    *,
    poly_rate: Decimal,
    kalshi_rate: Decimal,
) -> dict[str, Any] | None:
    p = _orient(poly_quote, pair.poly.side)
    k = _orient(kalshi_quote, pair.kalshi.side)
    if p is None or k is None:
        return None

    candidates: list[dict[str, Any]] = []
    p_fee = polymarket_taker_fee(1, p.prop_ask, poly_rate)
    k_fee = kalshi_taker_fee(1, k.opp_ask, kalshi_rate)
    candidates.append({
        "direction": "POLY_PROP_KALSHI_OPP",
        "profit": D("1") - p.prop_ask - k.opp_ask - p_fee - k_fee,
        "q1_depth": min(p.prop_depth, k.opp_depth),
        "poly_price": p.prop_ask,
        "kalshi_price": k.opp_ask,
        "poly_fee": p_fee,
        "kalshi_fee": k_fee,
    })

    k_fee = kalshi_taker_fee(1, k.prop_ask, kalshi_rate)
    p_fee = polymarket_taker_fee(1, p.opp_ask, poly_rate)
    candidates.append({
        "direction": "KALSHI_PROP_POLY_OPP",
        "profit": D("1") - k.prop_ask - p.opp_ask - k_fee - p_fee,
        "q1_depth": min(k.prop_depth, p.opp_depth),
        "poly_price": p.opp_ask,
        "kalshi_price": k.prop_ask,
        "poly_fee": p_fee,
        "kalshi_fee": k_fee,
    })

    best = max(candidates, key=lambda row: row["profit"])
    best["missing_to_3c"] = max(D("0"), FIXED_TARGET - best["profit"])
    return best


def _stats(values: list[Decimal]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "p50": None, "max": None}
    nums = [float(x) for x in values]
    return {
        "avg": round(statistics.fmean(nums), 6),
        "p50": round(statistics.median(nums), 6),
        "max": round(max(nums), 6),
    }


def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (str(value) if isinstance(value, Decimal) else value) for key, value in row.items()}


async def _fetch_inputs(http: httpx.AsyncClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    poly_resp = await http.get(
        f"{POLY}/v2/leagues/mlb/events",
        params={"limit": 1000, "active": "true", "closed": "false"},
    )
    poly_resp.raise_for_status()
    poly_body = poly_resp.json()
    poly_rows = poly_body.get("events") or [] if isinstance(poly_body, Mapping) else poly_body
    poly_events = [row for row in poly_rows if isinstance(row, dict)]

    kalshi_events: list[dict[str, Any]] = []
    nested_quotes: dict[str, Any] = {}
    series_counts: dict[str, int] = {}
    for series in KALSHI_SERIES:
        response = await http.get(
            f"{KALSHI}/events",
            params={"limit": 200, "status": "open", "series_ticker": series, "with_nested_markets": "true"},
        )
        response.raise_for_status()
        body = response.json()
        rows = [row for row in (body.get("events") or []) if isinstance(row, dict)] if isinstance(body, Mapping) else []
        series_counts[series] = len(rows)
        kalshi_events.extend(rows)
        received_ms = int(time.time() * 1000)
        for event in rows:
            for market in event.get("markets") or []:
                if not isinstance(market, Mapping):
                    continue
                quote = _nested_quote(market, received_ms)
                if quote is not None:
                    nested_quotes[quote.ticker] = quote
    return poly_events, kalshi_events, {"quotes": nested_quotes, "series_counts": series_counts}


async def _seed_poly_books(http: httpx.AsyncClient, slugs: Iterable[str]) -> PolyBookStore:
    store = PolyBookStore()
    sem = asyncio.Semaphore(8)

    async def fetch(slug: str) -> None:
        async with sem:
            try:
                response = await http.get(f"{POLY}/v1/markets/{slug}/book", headers=POLY_HEADERS)
                response.raise_for_status()
                store.ingest(response.json(), source="rest")
            except Exception as exc:
                logger.debug("POLY_BREADTH_BOOK_ERROR slug=%s type=%s", slug, type(exc).__name__)

    await asyncio.gather(*(fetch(slug) for slug in sorted(set(slugs))))
    return store


async def run_once() -> dict[str, Any]:
    poly_rate = D(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05"))
    kalshi_rate = D(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))
    async with httpx.AsyncClient(timeout=10.0) as http:
        poly_events, kalshi_events, kal_meta = await _fetch_inputs(http)
        poly_sides = normalize_polymarket_mlb(poly_events)
        kalshi_sides = normalize_kalshi_mlb(kalshi_events)
        pairs, ambiguous, expanded = _pair_normal_event_sides(poly_sides, kalshi_sides)
        poly_store = await _seed_poly_books(http, (pair.poly.venue_market_id for pair in pairs))

    rows: list[dict[str, Any]] = []
    nested_quotes = kal_meta["quotes"]
    for pair in pairs:
        pq = poly_store.quote(pair.poly.venue_market_id, max_age_ms=120000)
        kq = nested_quotes.get(pair.kalshi.venue_market_id)
        if pq is None or kq is None:
            continue
        econ = _q1_economics(pair, pq, kq, poly_rate=poly_rate, kalshi_rate=kalshi_rate)
        if econ is None:
            continue
        rows.append({
            "family": pair.family,
            "group_key": pair.group_key,
            "contract_key": pair.contract_key,
            "poly_market": pair.poly.venue_market_id,
            "poly_side": pair.poly.side,
            "kalshi_market": pair.kalshi.venue_market_id,
            "kalshi_side": pair.kalshi.side,
            "start_skew_ms": pair.start_skew_ms,
            **econ,
        })

    best_by_group: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = best_by_group.get(row["group_key"])
        if current is None or row["profit"] > current["profit"]:
            best_by_group[row["group_key"]] = row
    unique_rows = list(best_by_group.values())

    families: dict[str, Any] = {}
    for family in ("moneyline", "spread", "game_total"):
        physical = [r for r in rows if r["family"] == family]
        unique = [r for r in unique_rows if r["family"] == family]
        families[family] = {
            "physical_book_comparable": len(physical),
            "semantic_unique_groups": len(unique),
            "gte_1c": sum(1 for r in unique if r["profit"] >= D("0.01")),
            "gte_2c": sum(1 for r in unique if r["profit"] >= D("0.02")),
            "gte_3c": sum(1 for r in unique if r["profit"] >= FIXED_TARGET),
            "positive": sum(1 for r in unique if r["profit"] > D("0")),
            "q1_depth": _stats([r["q1_depth"] for r in unique if r["q1_depth"] > 0]),
            "missing_to_3c": _stats([r["missing_to_3c"] for r in unique]),
        }

    top = sorted(unique_rows, key=lambda r: (r["profit"], r["q1_depth"]), reverse=True)[:20]
    maker_inputs = [r for r in top if D("0") < r["profit"] < FIXED_TARGET and r["q1_depth"] >= 1]
    payload = {
        "classification": "RULE_INCOMPLETE_NORMAL_EVENT_SHADOW_NOT_EXECUTION_ELIGIBLE",
        "source_quality": "REST_SNAPSHOT_DESCRIPTIVE_ONLY",
        "fixed_net_target": "0.03",
        "poly_events": len(poly_events),
        "kalshi_events": len(kalshi_events),
        "kalshi_series_events": kal_meta["series_counts"],
        "poly_normalized_sides": len(poly_sides),
        "kalshi_normalized_sides": len(kalshi_sides),
        "normal_event_pairs": len(pairs),
        "ambiguous_contract_keys_rejected": ambiguous,
        "expanded_shared_contract_keys": expanded,
        "book_comparable_physical_pairs": len(rows),
        "semantic_unique_groups": len(unique_rows),
        "families": families,
        "top_rows": [_jsonable(row) for row in top],
        "maker_input_rows": [_jsonable(row) for row in maker_inputs],
        "rules_complete": False,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "capacity_credit_usd_per_day": 0,
    }
    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    logger.warning("SPORTS_KP_MLB_GAME_BREADTH %s", encoded)
    print("SPORTS_KP_MLB_GAME_BREADTH " + encoded, flush=True)
    return payload


async def main_async(interval_seconds: float) -> None:
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("SPORTS_KP_MLB_GAME_BREADTH_ERROR")
        if interval_seconds <= 0:
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only K/P MLB game breadth census")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async(args.interval_seconds))


if __name__ == "__main__":
    main()
