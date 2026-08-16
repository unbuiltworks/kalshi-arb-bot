"""One-shot public-book economics snapshot for strict MLB prop counterparts.

This is research-only. It discovers exact Polymarket US <-> Kalshi player-prop
identities, requires scheduled-time agreement, fetches current top-of-book depth and
current Kalshi series fee metadata, and reports conservative two-leg economics.

MLB player props remain CONDITIONAL_PARTICIPATION until the relevant first plate
appearance / first pitch gate has been observed. No credentials or order-entry code
are imported or used.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import json
import logging
from typing import Any, Iterable

import httpx

from src.mlb_prop_rule_equivalence import assess_rule_equivalence
from src.strict_mlb_prop_adapters import (
    FAMILY_SERIES,
    _kalshi_identity,
    _name,
    _poly_identity,
)
from src.strict_mlb_prop_schedule_guard import (
    MAX_START_SKEW_SECONDS,
    kalshi_event_start_utc,
    polymarket_start_utc,
)

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot")
POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY_TAKER_THETA = Decimal("0.05")
MAX_Q = 100


@dataclass(frozen=True)
class TopBook:
    yes_ask: Decimal
    yes_depth: Decimal
    no_ask: Decimal
    no_depth: Decimal


@dataclass(frozen=True)
class StrictCandidate:
    family: str
    poly_slug: str
    kalshi_ticker: str
    player: str
    threshold: Decimal
    game_date: str
    start_skew_seconds: float

    @property
    def key(self) -> str:
        return f"{self.family}|{self.poly_slug}|{self.kalshi_ticker}"


def _d(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def poly_top_book(payload: dict[str, Any]) -> TopBook | None:
    body = payload.get("marketData") if isinstance(payload.get("marketData"), dict) else payload
    bids = body.get("bids") or []
    offers = body.get("offers") or body.get("asks") or []
    if not bids or not offers:
        return None
    bid, offer = bids[0], offers[0]
    if not isinstance(bid, dict) or not isinstance(offer, dict):
        return None
    bid_px = _d((bid.get("px") or {}).get("value") if isinstance(bid.get("px"), dict) else bid.get("px"))
    ask_px = _d((offer.get("px") or {}).get("value") if isinstance(offer.get("px"), dict) else offer.get("px"))
    bid_qty = _d(bid.get("qty", bid.get("quantity")))
    ask_qty = _d(offer.get("qty", offer.get("quantity")))
    if None in (bid_px, ask_px, bid_qty, ask_qty):
        return None
    assert bid_px is not None and ask_px is not None and bid_qty is not None and ask_qty is not None
    if not (Decimal("0") <= bid_px <= ask_px <= Decimal("1")):
        return None
    if bid_qty <= 0 or ask_qty <= 0:
        return None
    return TopBook(
        yes_ask=ask_px,
        yes_depth=ask_qty,
        no_ask=Decimal("1") - bid_px,
        no_depth=bid_qty,
    )


def kalshi_top_book(payload: dict[str, Any]) -> TopBook | None:
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes = book.get("yes_dollars") or []
    no = book.get("no_dollars") or []
    if not yes or not no:
        return None
    try:
        yes_px, yes_qty = _d(yes[-1][0]), _d(yes[-1][1])
        no_px, no_qty = _d(no[-1][0]), _d(no[-1][1])
    except (IndexError, TypeError):
        return None
    if None in (yes_px, yes_qty, no_px, no_qty):
        return None
    assert yes_px is not None and yes_qty is not None and no_px is not None and no_qty is not None
    if yes_qty <= 0 or no_qty <= 0:
        return None
    return TopBook(
        yes_ask=Decimal("1") - no_px,
        yes_depth=no_qty,
        no_ask=Decimal("1") - yes_px,
        no_depth=yes_qty,
    )


def polymarket_fee(quantity: int, price: Decimal) -> Decimal:
    raw = POLY_TAKER_THETA * Decimal(quantity) * price * (Decimal("1") - price)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def conservative_kalshi_fee(quantity: int, price: Decimal, multiplier: Decimal) -> Decimal:
    """Conservative whole-cent upper bound for a quadratic taker fee.

    Current Kalshi execution can involve sub-cent fee/balance rounding. Rounding the
    quadratic fee up to the next full cent intentionally overstates cost for this
    snapshot, preventing a rounding detail from manufacturing a positive arb.
    """
    raw = multiplier * Decimal(quantity) * price * (Decimal("1") - price)
    return (raw * Decimal("100")).to_integral_value(rounding=ROUND_CEILING) / Decimal("100")


def best_conservative_plan(
    poly: TopBook,
    kalshi: TopBook,
    *,
    kalshi_fee_multiplier: Decimal,
    max_quantity: int = MAX_Q,
) -> dict[str, Any] | None:
    directions = (
        ("POLY_YES_KALSHI_NO", poly.yes_ask, poly.yes_depth, kalshi.no_ask, kalshi.no_depth),
        ("KALSHI_YES_POLY_NO", kalshi.yes_ask, kalshi.yes_depth, poly.no_ask, poly.no_depth),
    )
    best: dict[str, Any] | None = None
    for direction, first_px, first_depth, second_px, second_depth in directions:
        depth_q = min(first_depth, second_depth)
        qmax = min(max_quantity, int(depth_q))
        for q in range(1, qmax + 1):
            if direction == "POLY_YES_KALSHI_NO":
                poly_px, kalshi_px = first_px, second_px
            else:
                kalshi_px, poly_px = first_px, second_px
            p_fee = polymarket_fee(q, poly_px)
            k_fee = conservative_kalshi_fee(q, kalshi_px, kalshi_fee_multiplier)
            cost = Decimal(q) * (poly_px + kalshi_px) + p_fee + k_fee
            profit = Decimal(q) - cost
            row = {
                "direction": direction,
                "quantity": q,
                "poly_price": str(poly_px),
                "kalshi_price": str(kalshi_px),
                "poly_fee": str(p_fee),
                "kalshi_fee_upper_bound": str(k_fee),
                "total_cost_upper_bound": str(cost),
                "conservative_locked_profit": str(profit),
                "conservative_roi": str(profit / cost) if cost > 0 else "0",
            }
            if best is None or Decimal(row["conservative_locked_profit"]) > Decimal(best["conservative_locked_profit"]):
                best = row
    if best is None or Decimal(best["conservative_locked_profit"]) <= 0:
        return None
    return best


def discover_strict_candidates(
    poly_markets: Iterable[dict[str, Any]],
    kalshi_events: Iterable[dict[str, Any]],
) -> list[StrictCandidate]:
    """Exact identity + scheduled-time matcher, intentionally independent of rule equivalence."""
    p_rows: list[tuple[dict[str, Any], tuple[str, str, tuple[str, str], str, Decimal]]] = []
    for poly in poly_markets:
        identity = _poly_identity(poly)
        if identity is not None and polymarket_start_utc(poly) is not None:
            p_rows.append((poly, identity))

    out: list[StrictCandidate] = []
    seen: set[tuple[str, str]] = set()
    for poly, p in p_rows:
        family, player, teams, game_date, threshold = p
        expected_series = FAMILY_SERIES[family]
        p_start = polymarket_start_utc(poly)
        assert p_start is not None
        for event in kalshi_events:
            if str(event.get("series_ticker") or "") != expected_series:
                continue
            for market in event.get("markets") or []:
                k = _kalshi_identity(event, market, family)
                if k is None:
                    continue
                k_player, k_threshold = k
                if _name(player) != _name(k_player) or threshold != k_threshold:
                    continue
                ticker = str(market.get("ticker") or "")
                event_ticker = str(event.get("event_ticker") or market.get("event_ticker") or "").upper()
                a, b = (x.upper() for x in teams)
                if not (event_ticker.endswith(a + b) or event_ticker.endswith(b + a)):
                    continue
                k_start = kalshi_event_start_utc(event, market)
                if k_start is None:
                    continue
                skew = abs((p_start - k_start).total_seconds())
                if skew > MAX_START_SKEW_SECONDS:
                    continue
                if game_date != k_start.astimezone().date().isoformat() and game_date not in str(poly.get("slug") or ""):
                    continue
                pair_key = (str(poly.get("slug") or ""), ticker)
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                out.append(
                    StrictCandidate(
                        family=family,
                        poly_slug=pair_key[0],
                        kalshi_ticker=ticker,
                        player=player,
                        threshold=threshold,
                        game_date=game_date,
                        start_skew_seconds=skew,
                    )
                )
    return out


async def _fetch_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


async def _fetch_poly_mlb(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    body = await _fetch_json(
        http,
        f"{POLY}/v2/leagues/mlb/events",
        params={"limit": 1000, "active": "true", "closed": "false"},
    )
    rows: list[dict[str, Any]] = []
    for event in body.get("events") or []:
        event_start = event.get("startTime")
        for market in event.get("markets") or []:
            family = str(market.get("sportsMarketType") or "")
            if family not in FAMILY_SERIES:
                continue
            row = dict(market)
            if not row.get("gameStartTime") and event_start:
                row["eventStartTime"] = event_start
            rows.append(row)
    return rows


async def _fetch_kalshi_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    async def one(series: str) -> list[dict[str, Any]]:
        body = await _fetch_json(
            http,
            f"{KALSHI}/events",
            params={
                "limit": 200,
                "status": "open",
                "series_ticker": series,
                "with_nested_markets": "true",
            },
        )
        return list(body.get("events") or [])

    groups = await asyncio.gather(*(one(series) for series in FAMILY_SERIES.values()))
    return [event for group in groups for event in group]


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(
            _fetch_poly_mlb(http), _fetch_kalshi_events(http)
        )
        candidates = discover_strict_candidates(poly_markets, kalshi_events)

        series_meta: dict[str, dict[str, Any]] = {}
        for series in sorted(set(FAMILY_SERIES.values())):
            body = await _fetch_json(http, f"{KALSHI}/series/{series}")
            series_meta[series] = dict(body.get("series") or {})

        semaphore = asyncio.Semaphore(12)

        async def price(candidate: StrictCandidate) -> dict[str, Any]:
            async with semaphore:
                poly_body, kalshi_body = await asyncio.gather(
                    _fetch_json(http, f"{POLY}/v1/markets/{candidate.poly_slug}/book"),
                    _fetch_json(http, f"{KALSHI}/markets/{candidate.kalshi_ticker}/orderbook", params={"depth": 1}),
                )
            pbook = poly_top_book(poly_body)
            kbook = kalshi_top_book(kalshi_body)
            series = FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = _d(meta.get("fee_multiplier"))
            plan = None
            if (
                pbook is not None
                and kbook is not None
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
                and multiplier is not None
                and Decimal("0") <= multiplier <= Decimal("1")
            ):
                plan = best_conservative_plan(
                    pbook, kbook, kalshi_fee_multiplier=multiplier
                )
            assessment = assess_rule_equivalence(candidate.family)
            return {
                "family": candidate.family,
                "poly_slug": candidate.poly_slug,
                "kalshi_ticker": candidate.kalshi_ticker,
                "player": candidate.player,
                "threshold": str(candidate.threshold),
                "game_date": candidate.game_date,
                "start_skew_seconds": candidate.start_skew_seconds,
                "rule_status": assessment.status.value,
                "required_participation_gate": assessment.required_gate,
                "settlement_equivalent_now": False,
                "kalshi_fee_type": fee_type,
                "kalshi_fee_multiplier": str(multiplier) if multiplier is not None else None,
                "book_comparable": pbook is not None and kbook is not None,
                "positive_conservative_plan": plan,
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }

        priced = await asyncio.gather(*(price(c) for c in candidates)) if candidates else []

    positive = [row for row in priced if row.get("positive_conservative_plan")]
    by_family: dict[str, dict[str, int]] = {}
    for family in FAMILY_SERIES:
        family_rows = [row for row in priced if row["family"] == family]
        by_family[family] = {
            "strict_pairs": len(family_rows),
            "book_comparable": sum(bool(row["book_comparable"]) for row in family_rows),
            "gross_and_fee_conservative_positive": sum(bool(row["positive_conservative_plan"]) for row in family_rows),
        }

    result = {
        "strict_pairs": len(priced),
        "book_comparable": sum(bool(row["book_comparable"]) for row in priced),
        "conservative_positive_pairs": len(positive),
        "families": by_family,
        "top_positive": sorted(
            positive,
            key=lambda row: Decimal(row["positive_conservative_plan"]["conservative_locked_profit"]),
            reverse=True,
        )[:25],
        "rule_warning": (
            "MLB player props are participation-conditional across venues. Positive pricing is research-only "
            "until the required first-PA/first-pitch gate is independently observed."
        ),
        "fee_warning": (
            "Kalshi fee is deliberately rounded up to a whole-cent upper bound per modeled order; this is "
            "more conservative than current sub-cent execution rounding and is not a fill simulation."
        ),
        "read_only": True,
        "order_entry": False,
        "execution_eligible_pairs": 0,
    }
    logger.info("STRICT_MLB_PROP_SNAPSHOT %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
