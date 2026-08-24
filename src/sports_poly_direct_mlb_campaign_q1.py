"""Continuous-safe q1 maker campaign for strict Polymarket MLB prop ladders.

Polymarket's general sports event feed does not reliably include player props. This
campaign uses the existing direct PROP + search inventory, but keeps the already-
reviewed strict MLB identity parser and the v4 maker->FOK hedge transport. It is
intended to run only inside the structural supervisor, so there is still exactly one
resting maker at a time across totals and MLB props.

A BOTH_FILLED_RECONCILED result is a locked two-leg execution, not realized P&L.
Authoritative settlement remains the source of TRADE_REALIZED accounting.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from decimal import Decimal
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_kp_poly_relay_live_q1 as relay_base
from src.current_polymarket_mlb_props import fetch_current_poly_mlb_props
from src.sports_poly_dynamic_monotone import _group_key, score_pairs
from src.sports_poly_structural_monotone import fetch_books
from src.sports_poly_structural_reporting import report_attempt
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_schedule_guard import polymarket_start_utc

D = Decimal
MAX_ATTEMPTS = int(os.getenv("SPORTS_MLB_DIRECT_MAX_ATTEMPTS", "12"))
DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("SPORTS_MLB_DIRECT_DISCOVERY_TIMEOUT_SECONDS", "30"))
BOOK_TIMEOUT_SECONDS = float(os.getenv("SPORTS_MLB_DIRECT_BOOK_TIMEOUT_SECONDS", "35"))
HORIZON_HOURS = int(os.getenv("SPORTS_STRUCTURAL_HORIZON_HOURS", "168"))
LOOKBACK_HOURS = int(os.getenv("SPORTS_STRUCTURAL_LOOKBACK_HOURS", "12"))
TIGHT_GAP = D(os.getenv("SPORTS_STRUCTURAL_TIGHT_GAP", "0.15"))
MID_GAP = D(os.getenv("SPORTS_STRUCTURAL_MID_GAP", "0.30"))
TIGHT_REST_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_TIGHT_REST_SECONDS", "60"))
MID_REST_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_MID_REST_SECONDS", "30"))
WIDE_REST_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WIDE_REST_SECONDS", "12"))


def _stage(stage: str, **fields: Any) -> None:
    print("SPORTS_MLB_DIRECT_STAGE " + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


def _rest_seconds(row: Mapping[str, Any]) -> float:
    try:
        gap = D(str(row.get("maker_gap_to_ask") or "1"))
    except Exception:
        return WIDE_REST_SECONDS
    if gap <= TIGHT_GAP:
        return TIGHT_REST_SECONDS
    if gap <= MID_GAP:
        return MID_REST_SECONDS
    return WIDE_REST_SECONDS


def build_strict_pairs(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]], dict[str, Any]]:
    """Group only exact same-family/player/game/date MLB thresholds."""
    groups: dict[tuple[str, str, tuple[str, str], str], dict[D, dict[str, Any]]] = defaultdict(dict)
    telemetry: Counter[str] = Counter()
    for row in rows:
        telemetry["rows_seen"] += 1
        identity = _poly_identity(row)
        if identity is None:
            telemetry["identity_rejected"] += 1
            continue
        slug = str(row.get("slug") or "").strip()
        if not slug:
            telemetry["missing_slug"] += 1
            continue
        key = _group_key(identity)
        threshold = D(identity[-1])
        prior = groups[key].get(threshold)
        if prior is not None and str(prior.get("slug") or "") != slug:
            groups[key][threshold] = {"_ambiguous": True}
            telemetry["ambiguous_thresholds"] += 1
        elif prior is None:
            groups[key][threshold] = dict(row)
            telemetry["strict_rows"] += 1

    market_map: dict[str, dict[str, Any]] = {}
    pairs: list[tuple[str, str]] = []
    family_pairs: Counter[str] = Counter()
    for key, threshold_map in groups.items():
        clean = [(line, row) for line, row in threshold_map.items() if not row.get("_ambiguous")]
        clean.sort(key=lambda x: x[0])
        if len(clean) < 2:
            continue
        for _, row in clean:
            market_map[str(row["slug"])] = row
        for i in range(len(clean)):
            for j in range(i + 1, len(clean)):
                pairs.append((str(clean[i][1]["slug"]), str(clean[j][1]["slug"])))
                family_pairs[str(key[0])] += 1
    telemetry["ladder_groups"] = sum(
        1 for threshold_map in groups.values()
        if len([row for row in threshold_map.values() if not row.get("_ambiguous")]) >= 2
    )
    telemetry["strict_ladder_markets"] = len(market_map)
    telemetry["strict_pairs"] = len(pairs)
    return market_map, pairs, {**dict(telemetry), "family_pairs": dict(family_pairs.most_common())}


async def discover_direct_mlb_pairs(
    *, horizon_hours: int = HORIZON_HOURS, lookback_hours: int = LOOKBACK_HOURS
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]], dict[str, Any]]:
    now = datetime.now(timezone.utc)
    newest = now + timedelta(hours=horizon_hours)
    oldest = now - timedelta(hours=lookback_hours)
    async with httpx.AsyncClient(timeout=30.0) as http:
        rows = await fetch_current_poly_mlb_props(http, ttl_seconds=30.0)
    current: list[dict[str, Any]] = []
    time_rejected = 0
    for raw in rows:
        row = dict(raw)
        start = polymarket_start_utc(row)
        if start is None or start < oldest or start > newest:
            time_rejected += 1
            continue
        current.append(row)
    feed, pairs, telemetry = build_strict_pairs(current)
    telemetry.update({
        "inventory_rows": len(rows),
        "current_rows": len(current),
        "time_rejected": time_rejected,
        "horizon_hours": horizon_hours,
        "lookback_hours": lookback_hours,
    })
    return feed, pairs, telemetry


async def current_direct_mlb_slugs() -> list[str]:
    feed, _, _ = await discover_direct_mlb_pairs(horizon_hours=HORIZON_HOURS, lookback_hours=24)
    return sorted(feed)


async def run_once() -> dict[str, Any]:
    _stage("DISCOVERY_START", timeout_seconds=DISCOVERY_TIMEOUT_SECONDS, horizon_hours=HORIZON_HOURS, lookback_hours=LOOKBACK_HOURS)
    feed, pairs, discovery = await asyncio.wait_for(
        discover_direct_mlb_pairs(), timeout=DISCOVERY_TIMEOUT_SECONDS
    )
    _stage("DISCOVERY_DONE", strict_markets=len(feed), strict_pairs=len(pairs), discovery=discovery)

    if not pairs:
        out = {
            "status": "NO_EXACT_DIRECT_MLB_PROP_Q1",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "discovery": discovery,
            "order_entry": False,
            "locked_execution": False,
            "realized": False,
        }
        print("SPORTS_POLY_DIRECT_MLB_Q1 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    _stage("BOOKS_START", markets=len(feed), timeout_seconds=BOOK_TIMEOUT_SECONDS)
    books, quote_health = await asyncio.wait_for(fetch_books(set(feed), concurrency=6), timeout=BOOK_TIMEOUT_SECONDS)
    scored = score_pairs(feed, books, pairs)
    for row in scored:
        row["family"] = f"MLB_PROP::{row.get('family') or 'UNKNOWN'}"
    _stage("SCORE_DONE", books=len(books), candidates=len(scored), quote_health=quote_health)

    if not scored:
        out = {
            "status": "NO_EXACT_DIRECT_MLB_PROP_Q1",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "candidate_count": 0,
            "discovery": discovery,
            "quote_health": quote_health,
            "order_entry": False,
            "locked_execution": False,
            "realized": False,
        }
        print("SPORTS_POLY_DIRECT_MLB_Q1 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    await asyncio.sleep(v4.POST_PUBLIC_SCAN_COOLDOWN_SECONDS)
    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=6.0)
    previous_exact = v4._tradeable_exact
    v4._tradeable_exact = relay_base._tradeable_exact
    try:
        balances = await v4._read_call("account_balances_mlb_direct", client.account.balances)
        if os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") != "1":
            first = scored[0]
            out = {
                "status": "READY_DIRECT_MLB_PROP_Q1",
                "strict_markets": len(feed),
                "strict_pairs": len(pairs),
                "candidate_count": len(scored),
                "best_locked": str(first.get("quote_locked_profit")),
                "best_hedge_depth": str(first.get("hedge_depth")),
                "balance_response_present": bool(balances),
                "order_entry": False,
                "locked_execution": False,
                "realized": False,
            }
            print("SPORTS_POLY_DIRECT_MLB_Q1 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        attempts: list[dict[str, Any]] = []
        for idx, row in enumerate(scored[:MAX_ATTEMPTS], start=1):
            rest_seconds = _rest_seconds(row)
            _stage("ATTEMPT_START", attempt=idx, family=row.get("family"), maker_slug=row.get("maker_slug"), hedge_slug=row.get("hedge_slug"), maker_rest_seconds=rest_seconds, quote_locked_profit=row.get("quote_locked_profit"))
            prior_poll = v4.POLL_SECONDS
            v4.POLL_SECONDS = rest_seconds
            try:
                result = await v4._attempt(client, row, feed, idx)
            except v4.ReadRateLimitExhausted as exc:
                await v4._clear_slug(client, str(row["maker_slug"]))
                result = {"status": "READ_RATE_LIMIT_EXHAUSTED_SCOPED_CANCEL", "attempt": idx, "stage": exc.stage, "order_entry": False, "realized": False}
            except Exception as exc:
                await v4._clear_slug(client, str(row["maker_slug"]))
                result = {"status": "ATTEMPT_ERROR_SCOPED_CANCEL", "attempt": idx, "stage": getattr(exc, "stage", "unknown"), "error_type": type(exc).__name__, "order_entry": True, "realized": False}
            finally:
                v4.POLL_SECONDS = prior_poll

            result = dict(result)
            result.setdefault("family", row.get("family"))
            result.setdefault("configured_rest_seconds", rest_seconds)
            result["locked_execution"] = bool(result.pop("realized", False))
            result["realized"] = False
            try:
                ledger = await report_attempt(client=client, row=row, result=result)
            except Exception as exc:
                ledger = {"ledger_reported": False, "error_type": type(exc).__name__}
            result["ledger"] = ledger
            attempts.append(result)
            _stage("ATTEMPT_RESULT", attempt=idx, status=result.get("status"), locked_execution=result.get("locked_execution"), actual_net_locked_profit=result.get("actual_net_locked_profit"), ledger_reported=ledger.get("ledger_reported"))
            if result.get("status") not in {
                "MAKER_NOT_FILLED_CANCELED", "MAKER_CANCELED_EDGE_GONE",
                "SKIP_BOOK_MISSING", "SKIP_HEDGE_DEPTH", "SKIP_FLOOR",
            }:
                break
            await asyncio.sleep(0.75)

        out = {
            "status": "DIRECT_MLB_PROP_CAMPAIGN_COMPLETE",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "candidate_count": len(scored),
            "attempts": attempts,
            "attempt_count": len(attempts),
            "locked_execution": any(bool(a.get("locked_execution")) for a in attempts),
            "realized": False,
        }
        print("SPORTS_POLY_DIRECT_MLB_Q1 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        v4._tradeable_exact = previous_exact
        client.close()
