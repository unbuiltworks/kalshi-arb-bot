"""Participation-gated same-Polymarket MLB maker->FOK hedge q1 campaign.

This is the live-safe successor to the historical direct MLB campaign. It reuses the
already-proven v4 order transport, private-order stream hooks installed by the parent
supervisor, bounded flatten paths, exact hedge revalidation and reconciliation.

Additional fail-closed gates here:
- working current MLB event-feed discovery;
- exact strict same-game/player/stat threshold ladder identity;
- official MLB game must be LIVE;
- hitter: starter + >=1 PA; pitcher: starting pitcher + >=1 batter faced;
- both markets must report q1-compatible minimum quantity and positive tick size;
- authenticated exact market metadata must agree with discovery metadata;
- current safe maker quote must be at most one reported tick from the contra ask;
- 50ms pre-attempt persistence on the same maker/hedge direction;
- durable $1 Command Center reservation and global unresolved LIVE-sports guard;
- existing $5 realized rule-risk loss ceiling is checked and never auto-increased;
- separate SPORTS_POLY_DIRECT_MLB_LIVE_Q1 switch; shared totals flag cannot arm it;
- after the first reconciled two-leg MLB fill, return a review-required hard halt.

This remains LIVE RULE-RISK, not guaranteed arbitrage, because Polymarket's
suspension/delay LFMP branch can still affect settlement after participation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
from typing import Any, Mapping
import uuid

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_kp_poly_relay_live_q1 as relay_base
from src.sports_poly_direct_mlb_campaign_q1 import discover_direct_mlb_pairs
from src.sports_poly_dynamic_monotone import score_pairs
from src.sports_poly_mlb_participation_gate import (
    certify_pairs,
    maker_tick_for_pair,
    market_order_metadata,
)
from src.sports_poly_mlb_rule_risk_budget import STRATEGY as MLB_STRATEGY, release, reserve
from src.sports_poly_structural_monotone import fetch_books
from src.sports_poly_structural_reporting import report_attempt

D = Decimal
ONE = D("1")
LIVE_ENV = "SPORTS_POLY_DIRECT_MLB_LIVE_Q1"
PERSIST_SECONDS = float(os.getenv("SPORTS_POLY_MLB_PERSIST_SECONDS", "0.05"))
MAX_ATTEMPTS = int(os.getenv("SPORTS_POLY_MLB_MAX_ATTEMPTS", "8"))
MAX_NEAR_TOUCH_TICKS = D(os.getenv("SPORTS_POLY_MLB_MAX_NEAR_TOUCH_TICKS", "1"))
ZERO_FILL_RELEASE_STATUSES = {
    "MAKER_NOT_FILLED_CANCELED",
    "MAKER_CANCELED_EDGE_GONE",
    "SKIP_BOOK_MISSING",
    "SKIP_HEDGE_DEPTH",
    "SKIP_FLOOR",
}


def _stage(stage: str, **fields: Any) -> None:
    print(
        "SPORTS_POLY_MLB_Q1_V2_STAGE "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def live_enabled() -> bool:
    return os.getenv(LIVE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def _exact_with_metadata(
    feed_low: Mapping[str, Any],
    feed_high: Mapping[str, Any],
    low_resp: Mapping[str, Any],
    high_resp: Mapping[str, Any],
):
    low, high = relay_base._tradeable_exact(feed_low, feed_high, low_resp, high_resp)
    for label, feed_row, exact_row in (
        ("low", feed_low, low),
        ("high", feed_high, high),
    ):
        feed_meta = market_order_metadata(feed_row)
        exact_meta = market_order_metadata(exact_row)
        if not exact_meta["tick_ok"] or not exact_meta["q1_lot_ok"]:
            raise PermissionError(f"authenticated {label} tick/q1 lot metadata missing")
        if D(str(feed_meta["tick"])) != D(str(exact_meta["tick"])):
            raise PermissionError(f"authenticated {label} tick changed")
        if D(str(feed_meta["minimum_qty"])) != D(str(exact_meta["minimum_qty"])):
            raise PermissionError(f"authenticated {label} minimum quantity changed")
    return low, high


def _retick_rows(
    feed: Mapping[str, Mapping[str, Any]],
    scored: list[dict[str, Any]],
    *,
    require_near_touch: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in scored:
        row = dict(raw)
        try:
            tick = maker_tick_for_pair(feed, row)
            maker_bid = D(str(row["maker_price"]))
            maker_ask = D(str(row["maker_ask"]))
            hedge_ask = D(str(row["hedge_ask"]))
            max_for_floor = ONE - hedge_ask - v4.TAKER_FEE_FLOOR - v4.TARGET
            quote = max(maker_bid, min(maker_ask - tick, max_for_floor))
            locked = v4._locked(quote, hedge_ask)
            gap = maker_ask - quote
        except Exception:
            continue
        if quote <= 0 or quote >= maker_ask or locked < v4.TARGET:
            continue
        if require_near_touch and gap > tick * MAX_NEAR_TOUCH_TICKS:
            continue
        row.update(
            {
                "reported_maker_tick": tick,
                "executable_maker_quote": quote,
                "maker_gap_to_ask": gap,
                "quote_locked_profit": locked,
                "near_touch": gap <= tick * MAX_NEAR_TOUCH_TICKS,
                "rule_classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
                "guaranteed_arbitrage": False,
            }
        )
        out.append(row)
    out.sort(
        key=lambda r: (
            not bool(r.get("near_touch")),
            D(str(r.get("maker_gap_to_ask") or "1")),
            -D(str(r.get("hedge_depth") or "0")),
            -D(str(r.get("quote_locked_profit") or "0")),
        )
    )
    return out


async def _fresh_ranked(
    feed: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
    *,
    require_near_touch: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    slugs = {slug for pair in pairs for slug in pair}
    books, health = await fetch_books(slugs, concurrency=6)
    scored = score_pairs(feed, books, pairs)
    return _retick_rows(feed, scored, require_near_touch=require_near_touch), health


async def _persistent_row(
    feed: Mapping[str, Mapping[str, Any]],
    pair: tuple[str, str],
    expected: Mapping[str, Any],
) -> dict[str, Any] | None:
    first, _ = await _fresh_ranked(feed, [pair], require_near_touch=True)
    if not first:
        return None
    a = first[0]
    if (
        str(a.get("maker_slug")) != str(expected.get("maker_slug"))
        or str(a.get("hedge_slug")) != str(expected.get("hedge_slug"))
        or str(a.get("maker_side")) != str(expected.get("maker_side"))
        or str(a.get("hedge_side")) != str(expected.get("hedge_side"))
    ):
        return None
    await asyncio.sleep(max(0.0, PERSIST_SECONDS))
    second, _ = await _fresh_ranked(feed, [pair], require_near_touch=True)
    if not second:
        return None
    b = second[0]
    if (
        str(b.get("maker_slug")) != str(a.get("maker_slug"))
        or str(b.get("hedge_slug")) != str(a.get("hedge_slug"))
        or str(b.get("maker_side")) != str(a.get("maker_side"))
        or str(b.get("hedge_side")) != str(a.get("hedge_side"))
    ):
        return None
    return b


async def run_once() -> dict[str, Any]:
    feed, pairs, discovery = await discover_direct_mlb_pairs()
    _stage("DISCOVERY", strict_markets=len(feed), strict_pairs=len(pairs), discovery=discovery)
    if not pairs:
        out = {
            "status": "NO_STRICT_MLB_LADDERS_V2",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_POLY_MLB_Q1_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    ready_pairs, certs, cert_summary = await certify_pairs(feed, pairs)
    _stage("CERTIFICATION", **cert_summary)
    if not ready_pairs:
        out = {
            "status": "NO_LIVE_PARTICIPATION_CERTIFIED_MLB_PAIR",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "certification": cert_summary,
            "live_gate": live_enabled(),
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_POLY_MLB_Q1_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    ranked, quote_health = await _fresh_ranked(feed, ready_pairs, require_near_touch=True)
    _stage(
        "PRICING",
        participation_ready_pairs=len(ready_pairs),
        near_touch_candidates=len(ranked),
        quote_health=quote_health,
    )
    if not ranked:
        out = {
            "status": "LIVE_MLB_CERTIFIED_BUT_NO_NEAR_TOUCH_3C_Q1",
            "strict_pairs": len(pairs),
            "certification": cert_summary,
            "participation_ready_pairs": len(ready_pairs),
            "near_touch_candidates": 0,
            "live_gate": live_enabled(),
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_POLY_MLB_Q1_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    best = ranked[0]
    cert_key = f"{best['low_slug']}|{best['high_slug']}"
    best_cert = certs.get(cert_key) or {}
    if not live_enabled():
        out = {
            "status": "READY_CERTIFIED_NEAR_TOUCH_MLB_Q1_DISARMED",
            "candidate_count": len(ranked),
            "best": {
                "family": best.get("family"),
                "player": best.get("player"),
                "maker_slug": best.get("maker_slug"),
                "maker_side": best.get("maker_side"),
                "maker_quote": str(best.get("executable_maker_quote")),
                "maker_gap_to_ask": str(best.get("maker_gap_to_ask")),
                "maker_tick": str(best.get("reported_maker_tick")),
                "hedge_slug": best.get("hedge_slug"),
                "hedge_side": best.get("hedge_side"),
                "hedge_ask": str(best.get("hedge_ask")),
                "hedge_depth": str(best.get("hedge_depth")),
                "locked_profit": str(best.get("quote_locked_profit")),
            },
            "certification": best_cert,
            "live_gate": False,
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_POLY_MLB_Q1_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=6.0,
    )
    previous_exact = v4._tradeable_exact
    previous_tick = v4.TICK
    v4._tradeable_exact = _exact_with_metadata
    try:
        balances = await v4._read_call("account_balances_mlb_v2", client.account.balances)
        if not balances:
            raise PermissionError("Polymarket balance response missing")

        attempts: list[dict[str, Any]] = []
        for idx, initial in enumerate(ranked[:MAX_ATTEMPTS], start=1):
            pair = (str(initial["low_slug"]), str(initial["high_slug"]))
            persisted = await _persistent_row(feed, pair, initial)
            if persisted is None:
                attempts.append({
                    "status": "SKIP_MLB_NEAR_TOUCH_NOT_PERSISTENT",
                    "attempt": idx,
                    "order_entry": False,
                    "realized": False,
                })
                continue

            cert_key = f"{persisted['low_slug']}|{persisted['high_slug']}"
            cert = certs.get(cert_key) or {}
            if not bool(cert.get("certified_for_q1_transport")):
                attempts.append({
                    "status": "SKIP_MLB_CERT_LOST",
                    "attempt": idx,
                    "order_entry": False,
                    "realized": False,
                })
                continue

            trade_id = f"sports-poly-mlb-maker:{uuid.uuid4().hex}"
            attempt_row = dict(persisted)
            attempt_row["_trade_id"] = trade_id
            attempt_row["_strategy"] = MLB_STRATEGY
            try:
                budget = await reserve(row=attempt_row, certification=cert, trade_id=trade_id)
            except Exception as exc:
                attempts.append({
                    "status": "MLB_RULE_RISK_BUDGET_OR_UNRESOLVED_BLOCK",
                    "attempt": idx,
                    "error_type": type(exc).__name__,
                    "order_entry": False,
                    "realized": False,
                })
                break

            v4.TICK = maker_tick_for_pair(feed, attempt_row)
            rest_seconds = min(12.0, max(2.0, float(os.getenv("SPORTS_POLY_MLB_MAKER_REST_SECONDS", "8"))))
            prior_poll = v4.POLL_SECONDS
            v4.POLL_SECONDS = rest_seconds
            try:
                result = await v4._attempt(client, attempt_row, feed, idx)
            except v4.ReadRateLimitExhausted as exc:
                await v4._clear_slug(client, str(attempt_row["maker_slug"]))
                result = {
                    "status": "READ_RATE_LIMIT_EXHAUSTED_SCOPED_CANCEL",
                    "attempt": idx,
                    "stage": exc.stage,
                    "order_entry": False,
                    "realized": False,
                }
            except Exception as exc:
                await v4._clear_slug(client, str(attempt_row["maker_slug"]))
                result = {
                    "status": "MLB_V2_ATTEMPT_ERROR_SCOPED_CANCEL",
                    "attempt": idx,
                    "error_type": type(exc).__name__,
                    "order_entry": True,
                    "realized": False,
                }
            finally:
                v4.POLL_SECONDS = prior_poll

            result = dict(result)
            original_status = str(result.get("status") or "")
            result["family"] = f"MLB_PROP::{attempt_row.get('family') or 'UNKNOWN'}"
            result["rule_classification"] = "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE"
            result["guaranteed_arbitrage"] = False
            result["participation_certification"] = cert
            result["capital_reservation"] = budget
            result["locked_execution"] = bool(result.pop("realized", False))
            result["realized"] = False
            try:
                ledger = await report_attempt(client=client, row=attempt_row, result=result)
            except Exception as exc:
                ledger = {"ledger_reported": False, "error_type": type(exc).__name__}
            result["ledger"] = ledger

            if original_status in ZERO_FILL_RELEASE_STATUSES:
                try:
                    await release(row=attempt_row, trade_id=trade_id, reason=original_status)
                    result["capital_released"] = True
                except Exception as exc:
                    result["capital_released"] = False
                    result["capital_release_error_type"] = type(exc).__name__
                    result["status"] = "MLB_ZERO_FILL_RELEASE_UNCONFIRMED_REVIEW_REQUIRED"
                    attempts.append(result)
                    break

            if original_status == "BOTH_FILLED_RECONCILED":
                result["transport_status"] = original_status
                result["status"] = "MLB_Q1_CANARY_FILLED_REVIEW_REQUIRED"
                attempts.append(result)
                break

            attempts.append(result)
            if original_status not in ZERO_FILL_RELEASE_STATUSES:
                break
            await asyncio.sleep(0.5)

        out = {
            "status": "MLB_PARTICIPATION_GATED_Q1_CAMPAIGN_COMPLETE",
            "strict_pairs": len(pairs),
            "participation_ready_pairs": len(ready_pairs),
            "near_touch_candidates": len(ranked),
            "attempts": attempts,
            "attempt_count": len(attempts),
            "live_gate": True,
            "order_entry": any(bool(a.get("order_entry")) for a in attempts),
            "locked_execution": any(bool(a.get("locked_execution")) for a in attempts),
            "realized": False,
            "guaranteed_arbitrage": False,
        }
        print("SPORTS_POLY_MLB_Q1_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        v4._tradeable_exact = previous_exact
        v4.TICK = previous_tick
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
