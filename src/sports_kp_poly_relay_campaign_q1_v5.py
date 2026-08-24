"""v5 bounded q1 same-Polymarket structural maker campaign.

This promotes only the safest generic structural family in the first live pass:
exact same-parent / same-sportsMarketType totals ladders with independently
corroborated numeric lines and "more than/over/above" wording.  For low < high,
YES(low)+NO(high) pays at least $1 in every outcome.

Other newly discovered structural families remain shadow-only until this lane
proves repeatable reconciled fills.  All v4 order, hedge, partial-fill, rate-limit,
and flatten protections are reused unchanged.

Important accounting rule: a reconciled two-leg execution is a locked execution,
not realized P&L.  v5 mirrors exact fills to Command Center as
AWAITING_SETTLEMENT and leaves realized=false until a settlement reconciler emits
the authoritative TRADE_REALIZED event.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
from src.sports_poly_structural_monotone import (
    discover_structural_pairs,
    fetch_books,
    score_structural_pairs,
    tradeable_structural_exact,
)
from src.sports_poly_structural_reporting import report_attempt

MAX_ATTEMPTS = 12
LIVE_FAMILY_PREFIXES = ("TOTAL::",)


def _live_family(row: Mapping[str, Any]) -> bool:
    family = str(row.get("family") or "")
    return any(family.startswith(prefix) for prefix in LIVE_FAMILY_PREFIXES)


async def run_once() -> dict[str, Any]:
    feed, pairs, discovery = await discover_structural_pairs(horizon_hours=72)
    books, quote_health = await fetch_books(set(feed), concurrency=6)
    all_scored = score_structural_pairs(feed, books, pairs)
    scored = [row for row in all_scored if _live_family(row)]
    shadow_only = [row for row in all_scored if not _live_family(row)]

    if not scored:
        out = {
            "status": "NO_EXACT_STRUCTURAL_TOTAL_Q1",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "all_candidates": len(all_scored),
            "live_family_candidates": 0,
            "shadow_only_candidates": len(shadow_only),
            "discovery": discovery,
            "quote_health": quote_health,
            "order_entry": False,
            "locked_execution": False,
            "realized": False,
        }
        print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V5 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    await asyncio.sleep(v4.POST_PUBLIC_SCAN_COOLDOWN_SECONDS)
    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=6.0,
    )
    # v4's attempt engine resolves this module global at call time. Swap only
    # its semantic gate; all mutation and reconciliation mechanics remain v4.
    v4._tradeable_exact = tradeable_structural_exact
    try:
        balances = await v4._read_call("account_balances", client.account.balances)
        first = scored[0]
        if os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") != "1":
            out = {
                "status": "READY_STRUCTURAL_TOTAL_CAMPAIGN_Q1",
                "strict_markets": len(feed),
                "strict_pairs": len(pairs),
                "all_candidates": len(all_scored),
                "live_family_candidates": len(scored),
                "shadow_only_candidates": len(shadow_only),
                "best_family": first["family"],
                "best_locked": str(first["locked_profit"]),
                "best_quote_locked": str(first["quote_locked_profit"]),
                "best_hedge_depth": str(first["hedge_depth"]),
                "quote_health": quote_health,
                "balance_response_present": bool(balances),
                "max_attempts": MAX_ATTEMPTS,
                "maker_rest_seconds": v4.POLL_SECONDS,
                "order_entry": False,
                "locked_execution": False,
                "realized": False,
            }
            print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V5 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        attempts: list[dict[str, Any]] = []
        for idx, row in enumerate(scored[:MAX_ATTEMPTS], start=1):
            try:
                result = await v4._attempt(client, row, feed, idx)
            except v4.ReadRateLimitExhausted as exc:
                await v4._clear_slug(client, str(row["maker_slug"]))
                result = {
                    "status": "READ_RATE_LIMIT_EXHAUSTED_SCOPED_CANCEL",
                    "attempt": idx,
                    "stage": exc.stage,
                    "family": row.get("family"),
                    "order_entry": False,
                    "realized": False,
                }
            except Exception as exc:
                await v4._clear_slug(client, str(row["maker_slug"]))
                result = {
                    "status": "ATTEMPT_ERROR_SCOPED_CANCEL",
                    "attempt": idx,
                    "stage": getattr(exc, "stage", "unknown"),
                    "error_type": type(exc).__name__,
                    "family": row.get("family"),
                    "order_entry": True,
                    "realized": False,
                }

            result = dict(result)
            result.setdefault("family", row.get("family"))
            # v4 historically called positive fully hedged economics "realized".
            # Preserve the signal under a precise name and never let it enter v5
            # reporting as realized P&L before venue settlement.
            result["locked_execution"] = bool(result.pop("realized", False))
            result["realized"] = False

            try:
                ledger = await report_attempt(client=client, row=row, result=result)
            except Exception as exc:
                ledger = {"ledger_reported": False, "error_type": type(exc).__name__}
            result["ledger"] = ledger

            attempts.append(result)
            if result.get("status") not in {
                "MAKER_NOT_FILLED_CANCELED",
                "MAKER_CANCELED_EDGE_GONE",
                "SKIP_BOOK_MISSING",
                "SKIP_HEDGE_DEPTH",
                "SKIP_FLOOR",
            }:
                break
            await asyncio.sleep(0.75)

        locked_execution = any(bool(a.get("locked_execution")) for a in attempts)
        out = {
            "status": "STRUCTURAL_TOTAL_CAMPAIGN_COMPLETE",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "all_candidate_count": len(all_scored),
            "candidate_count": len(scored),
            "shadow_only_candidates": len(shadow_only),
            "quote_health": quote_health,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "locked_execution": locked_execution,
            "realized": False,
        }
        print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V5 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
