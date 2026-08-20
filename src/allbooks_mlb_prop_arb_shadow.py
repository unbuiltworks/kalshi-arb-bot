"""Exact sportsbook-vs-sportsbook MLB player-prop arbitrage shadow scanner.

Uses The Odds API's MLB event prop endpoint across all returned US sportsbooks. Pairs
only exact game/player/stat/threshold contracts with opposite real offered sides at
different books. Stakes are rounded to cents and capped at $5 total; reported locked
profit is the minimum payout across either outcome minus total stake.

Research-only. Book-pair settlement rules must be certified before alerts/manual action.
No order entry or notification code exists here.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import time
from typing import Any, Mapping

import httpx

from src import allbooks_kalshi_mlb_prop_multi_shadow as allbooks
from src import fanduel_kalshi_mlb_prop_multi_shadow as base
from src.mlb_game_side_normalization import canonical_team

D = Decimal
CENT = D("0.01")
MIN_STAKE = D("0.10")
MAX_CAPITAL = D("5.00")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def exact_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(row["teams"]), int(row["start_ms"]), row["family"],
        row["player_key"], D(row["threshold"]),
    )


def best_two_book_plan(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any] | None:
    if a["book"] == b["book"] or a["side"] == b["side"]:
        return None
    if exact_key(a) != exact_key(b):
        return None
    inv = (D("1") / D(a["decimal_odds"])) + (D("1") / D(b["decimal_odds"]))
    if inv >= D("1"):
        return None

    # Equal-payout ideal allocation, then search nearby cent-rounded stakes.
    ideal_a = MAX_CAPITAL * D(b["decimal_odds"]) / (D(a["decimal_odds"]) + D(b["decimal_odds"]))
    ideal_b = MAX_CAPITAL - ideal_a
    a0, b0 = _cents(ideal_a), _cents(ideal_b)
    candidates: list[tuple[Decimal, Decimal]] = []
    for da in range(-3, 4):
        for db in range(-3, 4):
            sa = a0 + CENT * da
            sb = b0 + CENT * db
            if sa < MIN_STAKE or sb < MIN_STAKE or sa + sb > MAX_CAPITAL:
                continue
            candidates.append((sa, sb))
    if not candidates:
        return None

    best = None
    for sa, sb in candidates:
        payout_a = sa * D(a["decimal_odds"])
        payout_b = sb * D(b["decimal_odds"])
        total = sa + sb
        locked = min(payout_a, payout_b) - total
        row = {
            "book_a": a["book"], "side_a": a["side"], "odds_a": str(a["decimal_odds"]), "stake_a": str(sa),
            "book_b": b["book"], "side_b": b["side"], "odds_b": str(b["decimal_odds"]), "stake_b": str(sb),
            "family": a["family"], "stat": a["stat"], "player": a["player"], "threshold": str(a["threshold"]),
            "teams": list(a["teams"]), "start_ms": int(a["start_ms"]),
            "total_stake": str(total), "payout_if_a": str(payout_a), "payout_if_b": str(payout_b),
            "shadow_locked_profit": str(locked),
            "shadow_roi_on_stake": str(locked / total) if total > 0 else "0",
            "quote_skew_ms": abs(int(a["quote_ms"]) - int(b["quote_ms"])),
            "oldest_quote_age_ms": max(int(time.time() * 1000) - int(a["quote_ms"]), int(time.time() * 1000) - int(b["quote_ms"])),
            "market_a": a["market_key"], "market_b": b["market_key"],
            "settlement_status": "NOT_EXECUTABLE_BOOK_PAIR_RULE_CERTIFICATION_REQUIRED",
        }
        if best is None or D(row["shadow_locked_profit"]) > D(best["shadow_locked_profit"]):
            best = row
    return best


async def run_once() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        odds_events, event_quota = await base._fetch_odds_events(http, api_key)
        async def fetch_event(event: Mapping[str, Any]):
            event_id = str(event.get("id") or "")
            response = await http.get(
                base.EVENT_ODDS_URL.format(event_id=event_id),
                params={"apiKey": api_key, "regions": "us", "markets": ",".join(sorted(base.FD_MARKET_TO_FAMILY)), "oddsFormat": "decimal", "dateFormat": "iso"},
            )
            response.raise_for_status()
            return event, response
        responses = await asyncio.gather(*(fetch_event(event) for event in odds_events if event.get("id")))

    legs: list[dict[str, Any]] = []
    quotas = []
    for event, response in responses:
        body = response.json()
        parsed = allbooks._sportsbook_legs(body if isinstance(body, Mapping) else {})
        legs.extend(parsed)
        quotas.append({
            "event_id": event.get("id"), "remaining": response.headers.get("x-requests-remaining", ""),
            "used": response.headers.get("x-requests-used", ""), "last_cost": response.headers.get("x-requests-last", ""),
        })

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in legs:
        grouped[exact_key(row)].append(row)
    plans = []
    for rows in grouped.values():
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                plan = best_two_book_plan(a, b)
                if plan is not None:
                    plans.append(plan)
    plans.sort(key=lambda row: D(row["shadow_locked_profit"]), reverse=True)
    positives = [row for row in plans if D(row["shadow_locked_profit"]) > 0]
    fresh10 = [row for row in positives if int(row["oldest_quote_age_ms"]) <= 10_000]
    fresh30 = [row for row in positives if int(row["oldest_quote_age_ms"]) <= 30_000]
    return {
        "status": "ALLBOOKS_MLB_PROP_ARB_SHADOW",
        "events": len(responses), "sportsbook_legs": len(legs), "exact_contracts": len(grouped),
        "candidate_pairs": len(plans), "positive_shadow_arbs": len(positives),
        "fresh_10s_positive_arbs": len(fresh10), "fresh_30s_positive_arbs": len(fresh30),
        "positive_by_book_pair": dict(Counter("|".join(sorted((r["book_a"], r["book_b"]))) for r in positives)),
        "positive_by_family": dict(Counter(r["family"] for r in positives)),
        "top_positive": positives[:80], "top_plans": plans[:100],
        "events_endpoint_quota": event_quota, "prop_quota": quotas,
        "max_capital": str(MAX_CAPITAL), "min_leg_stake": str(MIN_STAKE),
        "rule_gate": "BOOK_PAIR_COMPLETE_RULE_CERTIFICATION_REQUIRED",
        "alert_eligible": False, "execution_eligible": False, "realized_pnl": "0",
        "read_only": True, "order_entry": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
