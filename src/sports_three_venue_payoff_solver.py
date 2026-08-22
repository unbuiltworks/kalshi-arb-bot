"""Read-only three-venue MLB payoff-cover solver.

For every pair of normally-resolving binary MLB moneyline games, define the four
joint winner states. Each certified single-game outcome has a payoff vector that
covers two of those states. Find the cheapest q=1 portfolio across Gemini,
Polymarket US, and Kalshi whose payout is >= $1 in every state.

This is SHADOW only. Existing normal-event identity is not sufficient for live
execution: postponement/cancellation/void/extra-innings rule fingerprints must be
complete and equivalent, and all books/depth must be atomically revalidated.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal
from itertools import combinations
import json
import logging
from typing import Any

import httpx

from src.mlb_game_side_normalization import (
    NormalizedSide,
    normalize_gemini_mlb,
    normalize_polymarket_mlb,
)
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.three_venue_mlb_snapshot_pricing import price_counterparts_snapshot
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

D = Decimal
ONE = D("1")
logger = logging.getLogger("kalshi_arb.sports_three_venue_payoff_solver")


def _event_key(side: NormalizedSide) -> tuple[tuple[str, ...], int]:
    event = side.identity.event
    # Five-minute bucket only groups venue representations of the same scheduled game.
    return tuple(event.participants), int(event.scheduled_start_ms // 300000)


def _side_key(side: NormalizedSide) -> tuple[str, str]:
    return side.identity.venue, f"{side.venue_market_id}#{side.side}"


def _moneyline_sides(rows: list[NormalizedSide]) -> list[NormalizedSide]:
    return [r for r in rows if r.identity.market_family == "moneyline" and r.identity.subject_type == "team"]


def _instrument_vector(team: str, game_a: tuple[str, str], game_b: tuple[str, str]) -> tuple[int, int, int, int] | None:
    a0, a1 = game_a
    b0, b1 = game_b
    states = ((a0, b0), (a0, b1), (a1, b0), (a1, b1))
    if team in game_a:
        return tuple(1 if s[0] == team else 0 for s in states)  # type: ignore[return-value]
    if team in game_b:
        return tuple(1 if s[1] == team else 0 for s in states)  # type: ignore[return-value]
    return None


def _covers(vectors: list[tuple[int, int, int, int]]) -> bool:
    return all(sum(v[i] for v in vectors) >= 1 for i in range(4))


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, poly, kalshi = await asyncio.gather(
            fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http)
        )
        priced, errors, counterpart_keys = await price_counterparts_snapshot(http, gemini, poly, kalshi)

    normalized = _moneyline_sides(
        normalize_gemini_mlb(gemini) + normalize_polymarket_mlb(poly) + normalize_kalshi_mlb_eastern(kalshi)
    )
    by_side = {_side_key(s): s for s in normalized}
    quote_rows: list[dict[str, Any]] = []
    for p in priced:
        q = p.quote
        side = by_side.get((q.venue, q.market_id))
        if side is None or side.identity.market_family != "moneyline" or q.yes_depth < ONE:
            continue
        quote_rows.append({
            "venue": q.venue,
            "market_id": q.market_id,
            "team": side.identity.subject,
            "event_key": _event_key(side),
            "cost": q.yes_cost,
            "raw_ask": p.raw_yes_ask,
            "fee": p.yes_fee,
            "depth": q.yes_depth,
            "source": p.depth_source,
        })

    events: dict[tuple[tuple[str, ...], int], set[str]] = defaultdict(set)
    for row in quote_rows:
        events[row["event_key"]].add(row["team"])
    valid_events = [k for k, teams in events.items() if len(teams) == 2]

    results: list[dict[str, Any]] = []
    for event_a, event_b in combinations(valid_events, 2):
        game_a = tuple(sorted(events[event_a]))
        game_b = tuple(sorted(events[event_b]))
        if len(game_a) != 2 or len(game_b) != 2:
            continue
        # Keep each physical game distinct and avoid accidental duplicate representations.
        if event_a == event_b:
            continue
        candidates = []
        for row in quote_rows:
            if row["event_key"] not in {event_a, event_b}:
                continue
            vec = _instrument_vector(row["team"], game_a, game_b)
            if vec is not None:
                candidates.append((row, vec))
        if len(candidates) < 2:
            continue

        best = None
        # q=1 contracts; searching 2..4 instruments is sufficient for single-game
        # binary payoff vectors and leaves room for mixed redundant covers.
        for n in range(2, min(4, len(candidates)) + 1):
            for chosen in combinations(candidates, n):
                rows = [x[0] for x in chosen]
                vectors = [x[1] for x in chosen]
                if not _covers(vectors):
                    continue
                # Never buy the exact same venue market-side twice.
                keys = {(r["venue"], r["market_id"]) for r in rows}
                if len(keys) != len(rows):
                    continue
                total = sum((r["cost"] for r in rows), D("0"))
                locked = ONE - total
                candidate = (total, locked, rows, vectors)
                if best is None or total < best[0]:
                    best = candidate
        if best is None:
            continue
        total, locked, rows, vectors = best
        states = [f"{game_a[0]}+{game_b[0]}", f"{game_a[0]}+{game_b[1]}", f"{game_a[1]}+{game_b[0]}", f"{game_a[1]}+{game_b[1]}"]
        results.append({
            "games": [list(game_a), list(game_b)],
            "states": states,
            "all_in_cost": str(total),
            "normal_event_locked_profit": str(locked),
            "legs": [
                {
                    "venue": r["venue"], "market_id": r["market_id"], "team": r["team"],
                    "all_in_cost": str(r["cost"]), "raw_ask": str(r["raw_ask"]),
                    "fee": str(r["fee"]), "payoff_vector": list(v), "depth": str(r["depth"]),
                }
                for r, v in zip(rows, vectors)
            ],
            "classification": "PAYOFF_SHADOW_POSITIVE" if locked >= D("0.03") else "PAYOFF_SHADOW_NO_TRADE",
            "rule_certified": False,
            "execution_eligible": False,
        })

    results.sort(key=lambda r: D(r["normal_event_locked_profit"]), reverse=True)
    target = [r for r in results if {tuple(r["games"][0]), tuple(r["games"][1])} == {("ATL", "MIL"), ("MIA", "WSH")}]
    output = {
        "status": "THREE_VENUE_SPORTS_PAYOFF_SOLVER_V1",
        "raw_events": {"gemini": len(gemini), "polymarket": len(poly), "kalshi": len(kalshi)},
        "normal_event_counterpart_keys": counterpart_keys,
        "priced_moneyline_instruments": len(quote_rows),
        "two_game_grids_scored": len(results),
        "shadow_positive_ge_3c": sum(D(r["normal_event_locked_profit"]) >= D("0.03") for r in results),
        "best": results[0] if results else None,
        "atl_mil__wsh_mia": target[0] if target else None,
        "fetch_error_count": len(errors),
        "rule_gate": "FULL_CROSS_VENUE_VOID_POSTPONEMENT_EXTRA_INNINGS_FINGERPRINT_REQUIRED",
        "native_combo_vectors_included": False,
        "next_extension": "add Gemini/Kalshi/Polymarket native conjunction vectors to the same cover solver",
        "read_only": True,
        "order_entry": False,
    }
    logger.warning("THREE_VENUE_SPORTS_PAYOFF_SOLVER_V1 %s", json.dumps(output, separators=(",", ":"), default=str))
    print("THREE_VENUE_SPORTS_PAYOFF_SOLVER_V1 " + json.dumps(output, separators=(",", ":"), default=str), flush=True)
    return output


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
