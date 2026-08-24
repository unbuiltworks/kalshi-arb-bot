"""v5 bounded q1 same-Polymarket structural maker campaign.

This promotes only the safest generic structural family in the first live pass:
exact same-parent / same-sportsMarketType totals ladders with independently
corroborated numeric lines and "more than/over/above" wording. For low < high,
YES(low)+NO(high) pays at least $1 in every outcome.

The scanner can look farther ahead for additional independent events while the
maker dwell is adaptive: tight quotes get more time at the front of the book,
mid-gap quotes get a moderate dwell, and wide quotes keep the short probe. The
existing one-second hedge revalidation, >=3c after-fee floor, q1 sizing, partial
flattening, scoped cancellation and exact reconciliation remain unchanged.

Important accounting rule: a reconciled two-leg execution is a locked execution,
not realized P&L. v5 mirrors exact fills to Command Center as AWAITING_SETTLEMENT
and leaves realized=false until a settlement reconciler emits the authoritative
TRADE_REALIZED event.
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
DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_DISCOVERY_TIMEOUT_SECONDS", "25"))
BOOK_TIMEOUT_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_BOOK_TIMEOUT_SECONDS", "35"))
HORIZON_HOURS = int(os.getenv("SPORTS_STRUCTURAL_HORIZON_HOURS", "168"))
LOOKBACK_HOURS = int(os.getenv("SPORTS_STRUCTURAL_LOOKBACK_HOURS", "12"))
TIGHT_GAP = v4.D(os.getenv("SPORTS_STRUCTURAL_TIGHT_GAP", "0.15"))
MID_GAP = v4.D(os.getenv("SPORTS_STRUCTURAL_MID_GAP", "0.30"))
TIGHT_REST_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_TIGHT_REST_SECONDS", "60"))
MID_REST_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_MID_REST_SECONDS", "30"))
WIDE_REST_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WIDE_REST_SECONDS", "12"))


def _stage(stage: str, **fields: Any) -> None:
    print(
        "SPORTS_STRUCTURAL_STAGE "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _live_family(row: Mapping[str, Any]) -> bool:
    family = str(row.get("family") or "")
    return any(family.startswith(prefix) for prefix in LIVE_FAMILY_PREFIXES)


def _rest_seconds(row: Mapping[str, Any]) -> float:
    try:
        gap = v4.D(str(row.get("maker_gap_to_ask") or "1"))
    except Exception:
        return WIDE_REST_SECONDS
    if gap <= TIGHT_GAP:
        return TIGHT_REST_SECONDS
    if gap <= MID_GAP:
        return MID_REST_SECONDS
    return WIDE_REST_SECONDS


async def run_once() -> dict[str, Any]:
    _stage(
        "DISCOVERY_START",
        timeout_seconds=DISCOVERY_TIMEOUT_SECONDS,
        horizon_hours=HORIZON_HOURS,
        lookback_hours=LOOKBACK_HOURS,
    )
    feed, pairs, discovery = await asyncio.wait_for(
        discover_structural_pairs(horizon_hours=HORIZON_HOURS, lookback_hours=LOOKBACK_HOURS),
        timeout=DISCOVERY_TIMEOUT_SECONDS,
    )
    _stage(
        "DISCOVERY_DONE",
        strict_markets=len(feed),
        strict_pairs=len(pairs),
        horizon_hours=HORIZON_HOURS,
        lookback_hours=LOOKBACK_HOURS,
    )

    _stage("BOOKS_START", markets=len(feed), timeout_seconds=BOOK_TIMEOUT_SECONDS)
    books, quote_health = await asyncio.wait_for(
        fetch_books(set(feed), concurrency=6),
        timeout=BOOK_TIMEOUT_SECONDS,
    )
    _stage("BOOKS_DONE", books=len(books), quote_health=quote_health)

    all_scored = score_structural_pairs(feed, books, pairs)
    scored = [row for row in all_scored if _live_family(row)]
    shadow_only = [row for row in all_scored if not _live_family(row)]
    _stage(
        "SCORE_DONE",
        all_candidates=len(all_scored),
        live_family_candidates=len(scored),
        shadow_only_candidates=len(shadow_only),
    )

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
            "horizon_hours": HORIZON_HOURS,
            "lookback_hours": LOOKBACK_HOURS,
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
    v4._tradeable_exact = tradeable_structural_exact
    try:
        _stage("ACCOUNT_PREFLIGHT_START")
        balances = await v4._read_call("account_balances", client.account.balances)
        _stage("ACCOUNT_PREFLIGHT_DONE", balance_response_present=bool(balances))
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
                "best_maker_gap_to_ask": str(first["maker_gap_to_ask"]),
                "best_rest_seconds": _rest_seconds(first),
                "quote_health": quote_health,
                "balance_response_present": bool(balances),
                "max_attempts": MAX_ATTEMPTS,
                "horizon_hours": HORIZON_HOURS,
                "lookback_hours": LOOKBACK_HOURS,
                "order_entry": False,
                "locked_execution": False,
                "realized": False,
            }
            print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V5 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        attempts: list[dict[str, Any]] = []
        for idx, row in enumerate(scored[:MAX_ATTEMPTS], start=1):
            rest_seconds = _rest_seconds(row)
            _stage(
                "ATTEMPT_START",
                attempt=idx,
                family=row.get("family"),
                maker_slug=row.get("maker_slug"),
                maker_side=row.get("maker_side"),
                hedge_slug=row.get("hedge_slug"),
                hedge_side=row.get("hedge_side"),
                quoted_locked_profit=row.get("quote_locked_profit"),
                maker_gap_to_ask=row.get("maker_gap_to_ask"),
                maker_rest_seconds=rest_seconds,
            )
            prior_poll_seconds = v4.POLL_SECONDS
            v4.POLL_SECONDS = rest_seconds
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
            finally:
                v4.POLL_SECONDS = prior_poll_seconds

            result = dict(result)
            result.setdefault("family", row.get("family"))
            result.setdefault("configured_rest_seconds", rest_seconds)
            result["locked_execution"] = bool(result.pop("realized", False))
            result["realized"] = False
            _stage(
                "ATTEMPT_RESULT",
                attempt=idx,
                status=result.get("status"),
                error_type=result.get("error_type"),
                stage_name=result.get("stage"),
                locked_execution=result.get("locked_execution"),
                actual_net_locked_profit=result.get("actual_net_locked_profit"),
                configured_rest_seconds=rest_seconds,
            )

            try:
                ledger = await report_attempt(client=client, row=row, result=result)
            except Exception as exc:
                ledger = {"ledger_reported": False, "error_type": type(exc).__name__}
            result["ledger"] = ledger
            _stage(
                "LEDGER_RESULT",
                attempt=idx,
                ledger_reported=ledger.get("ledger_reported"),
                fills_reported=ledger.get("fills_reported"),
                awaiting_settlement=ledger.get("awaiting_settlement"),
            )

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
            "horizon_hours": HORIZON_HOURS,
            "lookback_hours": LOOKBACK_HOURS,
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
