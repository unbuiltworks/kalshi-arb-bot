"""One-shot public-book shadow scan for cross-venue K/P maker->hedge capacity.

Discovers the existing strict MLB Polymarket US <-> Kalshi counterparts, reads the
current public top books, and evaluates all four cross-venue maker orientations at
q1/q5/q10/q25/q50/q100.  It deliberately uses the full conservative taker fee
model as the maker-fee proxy on BOTH venues until the exact current maker fee for
the specific product is independently certified.  That overstates rather than
understates modeled cost, but ``fee_schedule_certified`` remains false so no row is
execution-eligible.

This scanner has no credentials and imports no mutation client.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
from typing import Any

import httpx

from src.strict_mlb_prop_adapters import FAMILY_SERIES
from src.strict_mlb_prop_snapshot import (
    KALSHI,
    POLY,
    _d,
    _fetch_json,
    _fetch_kalshi_events,
    _fetch_poly_mlb,
    conservative_kalshi_fee,
    discover_strict_candidates,
    polymarket_fee,
)
from src.mlb_prop_rule_equivalence import assess_rule_equivalence
from src.sports_kp_cross_venue_maker_planner import (
    BinaryVenueBook,
    CrossVenueGates,
    SideBook,
    plan_both_venues,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_kp_cross_venue_maker_shadow")


def _px(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("value")
    return _d(value)


def _poly_binary_book(market_id: str, payload: dict[str, Any]) -> BinaryVenueBook | None:
    body = payload.get("marketData") if isinstance(payload.get("marketData"), dict) else payload
    bids = body.get("bids") or []
    offers = body.get("offers") or body.get("asks") or []
    if not bids or not offers or not isinstance(bids[0], dict) or not isinstance(offers[0], dict):
        return None
    bid, ask = bids[0], offers[0]
    bid_px, ask_px = _px(bid.get("px")), _px(ask.get("px"))
    bid_q = _d(bid.get("qty", bid.get("quantity")))
    ask_q = _d(ask.get("qty", ask.get("quantity")))
    if None in (bid_px, ask_px, bid_q, ask_q):
        return None
    assert bid_px is not None and ask_px is not None and bid_q is not None and ask_q is not None
    if not (D("0") < bid_px < ask_px < D("1")) or bid_q <= 0 or ask_q <= 0:
        return None
    return BinaryVenueBook(
        venue="polymarket",
        market_id=market_id,
        yes=SideBook(bid_px, bid_q, ask_px, ask_q),
        no=SideBook(D("1") - ask_px, ask_q, D("1") - bid_px, bid_q),
    )


def _kalshi_binary_book(market_id: str, payload: dict[str, Any]) -> BinaryVenueBook | None:
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes = book.get("yes_dollars") or []
    no = book.get("no_dollars") or []
    if not yes or not no:
        return None
    try:
        yes_bid, yes_q = _d(yes[-1][0]), _d(yes[-1][1])
        no_bid, no_q = _d(no[-1][0]), _d(no[-1][1])
    except (IndexError, TypeError):
        return None
    if None in (yes_bid, yes_q, no_bid, no_q):
        return None
    assert yes_bid is not None and yes_q is not None and no_bid is not None and no_q is not None
    if yes_q <= 0 or no_q <= 0:
        return None
    yes_ask = D("1") - no_bid
    no_ask = D("1") - yes_bid
    if not (D("0") < yes_bid < yes_ask < D("1")) or not (D("0") < no_bid < no_ask < D("1")):
        return None
    return BinaryVenueBook(
        venue="kalshi",
        market_id=market_id,
        yes=SideBook(yes_bid, yes_q, yes_ask, no_q),
        no=SideBook(no_bid, no_q, no_ask, yes_q),
    )


def _economic_rows(route: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in route.get("tiers") or []:
        if row.get("locked_profit") is None or row.get("profit_per_contract") is None:
            continue
        if D(str(row["profit_per_contract"])) >= D("0.03"):
            out.append(row)
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(_fetch_poly_mlb(http), _fetch_kalshi_events(http))
        candidates = discover_strict_candidates(poly_markets, kalshi_events)

        series_meta: dict[str, dict[str, Any]] = {}
        for series in sorted(set(FAMILY_SERIES.values())):
            body = await _fetch_json(http, f"{KALSHI}/series/{series}")
            series_meta[series] = dict(body.get("series") or {})

        semaphore = asyncio.Semaphore(8)

        async def inspect(candidate: Any) -> dict[str, Any]:
            async with semaphore:
                poly_raw, kalshi_raw = await asyncio.gather(
                    _fetch_json(http, f"{POLY}/v1/markets/{candidate.poly_slug}/book"),
                    _fetch_json(http, f"{KALSHI}/markets/{candidate.kalshi_ticker}/orderbook", params={"depth": 1}),
                )
            pbook = _poly_binary_book(candidate.poly_slug, poly_raw)
            kbook = _kalshi_binary_book(candidate.kalshi_ticker, kalshi_raw)
            series = FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = _d(meta.get("fee_multiplier"))
            assessment = assess_rule_equivalence(candidate.family)
            routes: list[dict[str, Any]] = []
            if (
                pbook is not None
                and kbook is not None
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
                and multiplier is not None
                and D("0") <= multiplier <= D("100")
            ):
                # Conservative proxy: charge the full current modeled taker cost on
                # the resting leg as well.  Exact maker fee certification is still
                # intentionally false and must be solved before promotion.
                def k_fee(price: Decimal, quantity: Decimal) -> Decimal:
                    return conservative_kalshi_fee(int(quantity), price, multiplier)

                def p_fee(price: Decimal, quantity: Decimal) -> Decimal:
                    return polymarket_fee(int(quantity), price)

                routes = plan_both_venues(
                    kalshi_book=kbook,
                    polymarket_book=pbook,
                    kalshi_maker_fee_fn=k_fee,
                    kalshi_taker_fee_fn=k_fee,
                    polymarket_maker_fee_fn=p_fee,
                    polymarket_taker_fee_fn=p_fee,
                    gates=CrossVenueGates(
                        exact_counterpart=True,
                        settlement_rules_certified=bool(assessment.settlement_equivalent_now),
                        fee_schedule_certified=False,
                        quote_fresh=False,
                        quote_skew_ok=False,
                        hedge_depth_exact=True,
                        capital_snapshot_fresh=False,
                        no_unresolved_exposure=False,
                    ),
                )
            economic_rows = [row for route in routes for row in _economic_rows(route)]
            best = max(economic_rows, key=lambda row: D(str(row["locked_profit"])), default=None)
            return {
                "family": candidate.family,
                "player": candidate.player,
                "threshold": str(candidate.threshold),
                "poly_slug": candidate.poly_slug,
                "kalshi_ticker": candidate.kalshi_ticker,
                "start_skew_seconds": candidate.start_skew_seconds,
                "rule_status": assessment.status.value,
                "required_participation_gate": assessment.required_gate,
                "kalshi_fee_type": fee_type,
                "kalshi_fee_multiplier": str(multiplier) if multiplier is not None else None,
                "book_comparable": pbook is not None and kbook is not None,
                "routes": routes,
                "economic_tiers_at_3c_proxy_fees": len(economic_rows),
                "best_economic_proxy_row": best,
                "read_only": True,
                "order_entry": False,
                "execution_eligible": False,
            }

        rows = await asyncio.gather(*(inspect(candidate) for candidate in candidates)) if candidates else []

    positives = [row for row in rows if row.get("best_economic_proxy_row")]
    result = {
        "strict_pairs": len(rows),
        "book_comparable": sum(bool(row["book_comparable"]) for row in rows),
        "pairs_with_3c_maker_capacity_under_taker_fee_proxy": len(positives),
        "top": sorted(
            positives,
            key=lambda row: D(str(row["best_economic_proxy_row"]["locked_profit"])),
            reverse=True,
        )[:25],
        "fee_model": "CONSERVATIVE_TAKER_FEE_USED_AS_MAKER_PROXY_BOTH_VENUES",
        "fee_schedule_certified": False,
        "rule_warning": "MLB prop settlement equivalence remains participation-conditional until the audited gate is observed.",
        "capacity_warning": "Snapshot maker capacity is not simultaneous daily capacity, not a fill, and not realized P&L.",
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
    }
    logger.warning("SPORTS_KP_CROSS_VENUE_MAKER_SHADOW %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = asyncio.run(run_once())
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
