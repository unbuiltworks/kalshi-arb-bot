"""Settlement-aware Polymarket temporal shadow.

Wraps the existing public-data temporal collector and adds conservative scoring
for stranded first legs. A stranded leg is scored only after Gamma reports the
market closed with an unambiguous 1/0 outcome vector.

READ ONLY: no credentials, signing, order submission, cancellation, or mutation
endpoints exist here. All P&L remains SHADOW and realized_pnl is always zero.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

from src.polymarket_temporal_live_shadow import (
    GAMMA_API,
    PolymarketTemporalShadow,
    discover_markets,
    fetch_best_ask,
    _jsonish_list,
)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def fetch_resolved_winner(http: httpx.Client, condition_id: str) -> str | None:
    """Return UP/DOWN only for an explicit closed 1/0 Gamma result."""
    response = http.get(
        f"{GAMMA_API}/markets",
        params={"condition_ids": condition_id, "limit": 5},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return None

    row = next(
        (
            item
            for item in payload
            if isinstance(item, dict)
            and str(item.get("conditionId") or "").lower() == condition_id.lower()
        ),
        None,
    )
    if row is None or row.get("closed") is not True:
        return None

    outcomes = [str(x).strip().upper() for x in _jsonish_list(row.get("outcomes"))]
    raw_prices = _jsonish_list(row.get("outcomePrices"))
    if len(outcomes) != len(raw_prices) or len(outcomes) != 2:
        return None
    try:
        prices = [float(x) for x in raw_prices]
    except (TypeError, ValueError):
        return None

    winners = [outcome for outcome, price in zip(outcomes, prices) if price >= 0.999]
    losers = [outcome for outcome, price in zip(outcomes, prices) if price <= 0.001]
    if len(winners) != 1 or len(losers) != 1:
        return None
    if winners[0] not in {"UP", "DOWN"}:
        return None
    return winners[0]


class ResolutionAwarePolymarketShadow(PolymarketTemporalShadow):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.stranded_results: dict[str, dict[str, Any]] = {}

    def resolve_expired(self, http: httpx.Client, timestamp_ms: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for market_id, engine in self.engines.items():
            if not engine.expired or engine.first_leg is None:
                continue
            if market_id in self.stranded_results:
                continue
            try:
                winner = fetch_resolved_winner(http, market_id)
            except Exception as exc:
                events.append(
                    {
                        "type": "POLY_TEMPORAL_RESOLUTION_ERROR",
                        "market_id": market_id,
                        "stream": engine.stream.name,
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    }
                )
                continue
            if winner is None:
                continue

            leg = engine.first_leg
            payout = leg.quantity if leg.outcome == winner else 0.0
            pnl = payout - leg.cost_usd
            result = {
                "winner": winner,
                "first_outcome": leg.outcome,
                "first_price": leg.price,
                "first_cost_usd": leg.cost_usd,
                "payout_usd": payout,
                "shadow_pnl_usd": pnl,
                "resolved_at_ms": timestamp_ms,
                "stream": engine.stream.name,
            }
            self.stranded_results[market_id] = result
            events.append(
                {
                    "type": "TEMPORAL_STRANDED_SETTLEMENT_SHADOW",
                    "market_id": market_id,
                    **result,
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                }
            )
        return events

    def summary(self) -> dict[str, Any]:
        base = super().summary()
        resolved = list(self.stranded_results.values())
        stranded_pnl = sum(float(row["shadow_pnl_usd"]) for row in resolved)
        locked_pnl = float(base["shadow_locked_profit_usd"])
        expired_with_leg = sum(
            engine.expired and engine.first_leg is not None
            for engine in self.engines.values()
        )
        by_stream_settlement: dict[str, dict[str, float | int]] = {}
        for row in resolved:
            agg = by_stream_settlement.setdefault(
                str(row["stream"]),
                {"resolved_stranded": 0, "stranded_wins": 0, "stranded_losses": 0, "stranded_pnl_usd": 0.0},
            )
            agg["resolved_stranded"] += 1
            won = row["winner"] == row["first_outcome"]
            agg["stranded_wins"] += int(won)
            agg["stranded_losses"] += int(not won)
            agg["stranded_pnl_usd"] += float(row["shadow_pnl_usd"])

        base.update(
            {
                "stranded_resolved": len(resolved),
                "stranded_resolution_pending": max(0, expired_with_leg - len(resolved)),
                "stranded_wins": sum(row["winner"] == row["first_outcome"] for row in resolved),
                "stranded_losses": sum(row["winner"] != row["first_outcome"] for row in resolved),
                "shadow_stranded_pnl_usd": stranded_pnl,
                "shadow_total_strategy_pnl_usd": locked_pnl + stranded_pnl,
                "by_stream_settlement": by_stream_settlement,
                "accounting_note": "locked pairs plus explicitly resolved stranded q=1 first legs; unresolved legs excluded",
            }
        )
        return base


def run(
    *,
    poll_seconds: float,
    discovery_seconds: float,
    resolution_seconds: float,
    summary_seconds: float,
    min_net_edge: float,
    first_leg_max_price: float,
) -> None:
    shadow = ResolutionAwarePolymarketShadow(
        min_net_edge=min_net_edge,
        first_leg_max_price=first_leg_max_price,
    )
    next_discovery = 0.0
    next_resolution = 0.0
    next_summary = 0.0

    with httpx.Client(timeout=10.0, headers={"User-Agent": "arb-temporal-resolved-shadow/1.0"}) as http:
        while True:
            now = time.time()
            timestamp_ms = int(now * 1000)

            if now >= next_discovery:
                try:
                    markets = discover_markets(http, horizons=shadow.horizons)
                    for event in shadow.sync_markets(markets):
                        _emit(event)
                    _emit(
                        {
                            "type": "POLY_TEMPORAL_DISCOVERY_SUMMARY",
                            "found": len(markets),
                            "streams": sorted({m.stream.name for m in markets}),
                            "shadow_only": True,
                            "order_entry": False,
                            "realized_pnl": 0.0,
                        }
                    )
                except Exception as exc:
                    _emit(
                        {
                            "type": "POLY_TEMPORAL_DISCOVERY_ERROR",
                            "error": type(exc).__name__,
                            "message": str(exc)[:300],
                            "shadow_only": True,
                        }
                    )
                next_discovery = now + discovery_seconds

            for market in list(shadow.markets.values()):
                engine = shadow.engines[market.condition_id]
                if engine.completed_pair is not None or engine.expired:
                    continue
                try:
                    up_ask, up_size = fetch_best_ask(http, market.up_token_id)
                    down_ask, down_size = fetch_best_ask(http, market.down_token_id)
                    if up_size is not None and up_size < shadow.quantity:
                        up_ask = None
                    if down_size is not None and down_size < shadow.quantity:
                        down_ask = None
                    for event in shadow.observe_market(
                        market,
                        up_ask=up_ask,
                        down_ask=down_ask,
                        timestamp_ms=timestamp_ms,
                    ):
                        _emit(event)
                except Exception as exc:
                    _emit(
                        {
                            "type": "POLY_TEMPORAL_BOOK_ERROR",
                            "stream": market.stream.name,
                            "market_id": market.condition_id,
                            "error": type(exc).__name__,
                            "message": str(exc)[:300],
                            "shadow_only": True,
                        }
                    )

            if now >= next_resolution:
                for event in shadow.resolve_expired(http, timestamp_ms):
                    _emit(event)
                next_resolution = now + resolution_seconds

            if now >= next_summary:
                _emit(shadow.summary())
                next_summary = now + summary_seconds

            time.sleep(max(0.05, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Settlement-aware read-only Polymarket temporal shadow")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--discovery-seconds", type=float, default=20.0)
    parser.add_argument("--resolution-seconds", type=float, default=10.0)
    parser.add_argument("--summary-seconds", type=float, default=10.0)
    parser.add_argument("--min-net-edge", type=float, default=0.03)
    parser.add_argument("--first-leg-max-price", type=float, default=0.45)
    args = parser.parse_args()
    run(
        poll_seconds=args.poll_seconds,
        discovery_seconds=args.discovery_seconds,
        resolution_seconds=args.resolution_seconds,
        summary_seconds=args.summary_seconds,
        min_net_edge=args.min_net_edge,
        first_leg_max_price=args.first_leg_max_price,
    )


if __name__ == "__main__":
    main()
