"""Episode-aware attribution for Gemini/Kalshi crypto candidates before live execution.

This module is observation-only. It wraps the existing exact revalidation evaluator,
lets the proven evaluator make every state/episode/probe decision first, then
reconstructs the same candidate set to explain why a newly opened >=min-profit episode
is or is not shaped like a q=1 live candidate.

It deliberately does not alter market data, candidate economics, active episodes,
readiness thresholds, probe timing, authorization, or order transport. Repeated
threshold evaluations inside an already-open episode are counted separately so they
are not mistaken for independent missed trades.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import logging
import os
from typing import Any

from src import gemini_kalshi_basis_execution_revalidation as exact
from src import gemini_kalshi_micro_live_readiness_20bp as readiness

basis = exact.basis
hot = exact.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_preprobe_attribution")
_ORIGINAL_EVALUATE = exact._evaluate_with_revalidation
_INSTALLED = False

COUNTERS = {
    "threshold_direction_events": 0,
    "new_episodes": 0,
    "existing_episode_repeats": 0,
    "unexpected_episode_not_opened": 0,
    "selected_q1": 0,
    "selected_q_gt1": 0,
    "q1_qualifies_but_best_q_gt1": 0,
    "no_basis_context": 0,
    "no_kalshi_target": 0,
    "stale_basis_proxy": 0,
    "boundary_side_mismatch": 0,
    "boundary_margin_unavailable": 0,
    "boundary_below_min": 0,
    "entry_boundary_pass": 0,
    "revalidation_probe_scheduled": 0,
    "q1_and_entry_boundary_pass": 0,
}


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def classify_entry(
    *,
    selected_q: int,
    selected_profit: Decimal,
    q1_profit: Decimal | None,
    min_profit: Decimal,
    snapshot: dict[str, Any] | None,
    min_boundary_bps: Decimal,
) -> dict[str, Any]:
    """Pure entry-time classification; does not authorize or reject anything."""
    q1_qualifies = bool(q1_profit is not None and D(q1_profit) >= D(min_profit))
    selected_q1 = int(selected_q) == 1
    reasons: list[str] = []

    if not selected_q1:
        reasons.append("BEST_TOTAL_PROFIT_Q_GT1")
        if q1_qualifies:
            reasons.append("Q1_QUALIFIES_BUT_BEST_Q_GT1")

    boundary_pass = False
    probe_scheduled = False
    boundary_margin = None
    if snapshot is None:
        reasons.append("NO_BASIS_CONTEXT")
    else:
        # Exact revalidation schedules from the historical >=2bp Gemini-basis gate.
        # The later micro-live readiness layer adds the double-boundary requirement.
        probe_scheduled = bool(snapshot.get("research_proxy_gate_2bp_fresh_1000ms"))
        boundary_margin = _dec(snapshot.get("min_abs_double_boundary_margin_bps"))
        target = _dec(snapshot.get("kalshi_target"))
        fresh = bool(snapshot.get("proxy_fresh_1000ms"))
        same_side = bool(snapshot.get("same_side_of_both_boundaries"))

        if target is None:
            reasons.append("NO_KALSHI_TARGET")
        if not fresh:
            reasons.append("STALE_BASIS_PROXY")
        if not same_side:
            reasons.append("BOUNDARY_SIDE_MISMATCH")
        if boundary_margin is None:
            reasons.append("BOUNDARY_MARGIN_UNAVAILABLE")
        elif boundary_margin < D(min_boundary_bps):
            reasons.append("BOUNDARY_BELOW_MIN")

        boundary_pass = bool(
            target is not None
            and fresh
            and same_side
            and boundary_margin is not None
            and boundary_margin >= D(min_boundary_bps)
        )
        if boundary_pass:
            reasons.append("ENTRY_BOUNDARY_PASS")

    return {
        "selected_q": int(selected_q),
        "selected_profit": str(D(selected_profit)),
        "selected_q1": selected_q1,
        "q1_profit": str(D(q1_profit)) if q1_profit is not None else None,
        "q1_qualifies": q1_qualifies,
        "min_profit": str(D(min_profit)),
        "minimum_double_boundary_bps": str(D(min_boundary_bps)),
        "entry_boundary_margin_bps": str(boundary_margin) if boundary_margin is not None else None,
        "entry_boundary_pass": boundary_pass,
        "revalidation_probe_scheduled": probe_scheduled,
        "q1_and_entry_boundary_pass": bool(selected_q1 and boundary_pass),
        "reasons": reasons,
    }


def _increment_classification(classification: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
    if classification["selected_q1"]:
        COUNTERS["selected_q1"] += 1
    else:
        COUNTERS["selected_q_gt1"] += 1
    if "Q1_QUALIFIES_BUT_BEST_Q_GT1" in classification["reasons"]:
        COUNTERS["q1_qualifies_but_best_q_gt1"] += 1
    if snapshot is None:
        COUNTERS["no_basis_context"] += 1
    else:
        for reason, key in (
            ("NO_KALSHI_TARGET", "no_kalshi_target"),
            ("STALE_BASIS_PROXY", "stale_basis_proxy"),
            ("BOUNDARY_SIDE_MISMATCH", "boundary_side_mismatch"),
            ("BOUNDARY_MARGIN_UNAVAILABLE", "boundary_margin_unavailable"),
            ("BOUNDARY_BELOW_MIN", "boundary_below_min"),
        ):
            if reason in classification["reasons"]:
                COUNTERS[key] += 1
    if classification["entry_boundary_pass"]:
        COUNTERS["entry_boundary_pass"] += 1
    if classification["revalidation_probe_scheduled"]:
        COUNTERS["revalidation_probe_scheduled"] += 1
    if classification["q1_and_entry_boundary_pass"]:
        COUNTERS["q1_and_entry_boundary_pass"] += 1


def _evaluate_with_attribution(
    *,
    feed,
    event,
    kalshi_fee,
    max_quote_skew_ms,
    capital_limit,
    min_profit,
    active_episodes,
    fh,
) -> None:
    # Snapshot identity before the real evaluator runs. Do not mutate it.
    before = {
        direction: int(ep.start_ms)
        for direction, ep in active_episodes.items()
    }

    _ORIGINAL_EVALUATE(
        feed=feed,
        event=event,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
        min_profit=min_profit,
        active_episodes=active_episodes,
        fh=fh,
    )

    receive_ms = int(event[1])
    if receive_ms < int(feed.boundary_ms):
        return
    g = getattr(feed, "eval_gemini", None)
    k = getattr(feed, "eval_kalshi", None)
    if g is None or k is None:
        return

    all_small = hot._small_q_candidates(
        g,
        k,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
    )
    best: dict[str, tuple[int, Any]] = {}
    q1: dict[str, Any] = {}
    for q, candidate in all_small:
        if int(q) == 1:
            prior_q1 = q1.get(candidate.direction)
            if prior_q1 is None or candidate.locked_profit > prior_q1.locked_profit:
                q1[candidate.direction] = candidate
        prior = best.get(candidate.direction)
        if prior is None or candidate.locked_profit > prior[1].locked_profit:
            best[candidate.direction] = (int(q), candidate)

    qualifying = {
        direction: item
        for direction, item in best.items()
        if item[1].locked_profit >= min_profit
    }
    if not qualifying:
        return

    min_boundary_bps = D(readiness.MIN_DOUBLE_BOUNDARY_BPS)
    trigger_venue = str(event[0])
    for direction, (q, candidate) in qualifying.items():
        COUNTERS["threshold_direction_events"] += 1
        if direction in before:
            COUNTERS["existing_episode_repeats"] += 1
            continue
        ep = active_episodes.get(direction)
        if ep is None:
            COUNTERS["unexpected_episode_not_opened"] += 1
            logger.warning(
                "CRYPTO_PREPROBE_ATTRIBUTION_INVARIANT %s",
                json.dumps({
                    "asset": feed.asset,
                    "direction": direction,
                    "receive_ms": receive_ms,
                    "reason": "QUALIFYING_DIRECTION_DID_NOT_OPEN_EPISODE",
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
            continue

        COUNTERS["new_episodes"] += 1
        snapshot = basis.basis_snapshot(feed.asset, receive_ms)
        q1_candidate = q1.get(direction)
        classification = classify_entry(
            selected_q=q,
            selected_profit=D(candidate.locked_profit),
            q1_profit=(D(q1_candidate.locked_profit) if q1_candidate is not None else None),
            min_profit=D(min_profit),
            snapshot=snapshot,
            min_boundary_bps=min_boundary_bps,
        )
        _increment_classification(classification, snapshot)

        logger.info(
            "CRYPTO_PREPROBE_ATTRIBUTION_EPISODE %s",
            json.dumps({
                "asset": feed.asset,
                "direction": direction,
                "opened_ms": int(ep.start_ms),
                "trigger_venue": trigger_venue,
                "gemini_symbol": feed.pair.gemini_symbol,
                "kalshi_ticker": feed.pair.kalshi_ticker,
                "selected_capital": str(candidate.net_cost),
                "selected_fees": str(candidate.fees),
                "selected_quote_skew_ms": candidate.quote_skew_ms,
                **classification,
                "basis_context_available": snapshot is not None,
                "kalshi_target": snapshot.get("kalshi_target") if snapshot else None,
                "proxy_fresh_1000ms": bool(snapshot and snapshot.get("proxy_fresh_1000ms")),
                "same_side_of_both_boundaries": bool(snapshot and snapshot.get("same_side_of_both_boundaries")),
                "counters": dict(COUNTERS),
                "decision_effect": "NONE_OBSERVATION_ONLY",
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str),
        )


def install() -> None:
    """Install attribution without changing any gate or transport."""
    global _INSTALLED
    if _INSTALLED:
        return
    exact._evaluate_with_revalidation = _evaluate_with_attribution
    _INSTALLED = True


def main() -> None:
    # Standalone research mode only. Match the live core's current 10bp entry threshold
    # unless explicitly overridden, but never import or enable order transport.
    readiness.MIN_DOUBLE_BOUNDARY_BPS = D(
        os.getenv("CRYPTO_PREPROBE_MIN_DOUBLE_BOUNDARY_BPS", "10")
    )
    install()
    logger.info(
        "CRYPTO_PREPROBE_ATTRIBUTION_ENABLED minimum_double_boundary_bps=%s "
        "episode_aware=true decision_effect=none trade_authorized=false read_only=true order_entry=false",
        readiness.MIN_DOUBLE_BOUNDARY_BPS,
    )
    readiness.main()


if __name__ == "__main__":
    main()
