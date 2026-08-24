"""The Odds API MLB shadow + read-only qualified Pushover alerts.

This replaces no pricing logic and performs no order entry. It reuses the exact
full-game-only v2 scanner primitives, then routes only fully rule-audited,
actionable retail-vs-prediction locks through sports_pushover_alerts.

Physical-event identity is a second hard gate. Semantic contract keys intentionally
omit scheduled start, so repeated MLB series can otherwise make Monday and Tuesday
games look identical after quote normalization. Retail-vs-prediction opportunities
must therefore have authoritative normalized scheduled starts within five minutes
before they can enter rule certification or alerting.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import logging
import os
import time
from typing import Any, Mapping

import httpx

import src.the_odds_api_mlb_shadow_v2 as base
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_rule_certifications import CertificationStatus, apply_certification
from src.mlb_game_side_normalization import normalize_gemini_mlb, normalize_polymarket_mlb
from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE, SportsPushoverAlerts

logger = logging.getLogger("kalshi_arb.sports_pushover_odds_api_mlb")
_ALERTS = SportsPushoverAlerts()
RULE_STATUS = "FULL_GAME_ONLY; PHYSICAL_START_MATCH_REQUIRED; MACHINE_RULE_CERTIFICATION_REQUIRED"
MAX_PHYSICAL_START_SKEW_MS = 5 * 60 * 1000


async def _soft_prediction_fetch(name: str, awaitable: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Fail one prediction venue closed without discarding healthy venue data."""
    try:
        rows = await awaitable
        return list(rows), None
    except Exception as exc:
        detail = f"{type(exc).__name__}:{str(exc)[:240]}"
        logger.warning(
            "SPORTS_PREDICTION_SOURCE_FETCH_ERROR venue=%s detail=%s scan_continues=true stale_fallback=false",
            name,
            detail,
        )
        return [], detail


def _sportsbook_start_map(events: list[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw in events:
        event_id = str(raw.get("id") or "").strip()
        start = base._start_ms(raw.get("commence_time"))
        if event_id and start is not None:
            out[event_id] = start
    return out


def _prediction_start_map(
    gemini: list[dict[str, Any]],
    poly: list[dict[str, Any]],
    kalshi: list[dict[str, Any]],
) -> dict[tuple[str, str], int]:
    """Map normalized prediction-market IDs to their physical scheduled start."""
    out: dict[tuple[str, str], int] = {}
    rows = (
        normalize_gemini_mlb(gemini)
        + normalize_polymarket_mlb(poly)
        + normalize_kalshi_mlb_eastern(kalshi)
    )
    for row in rows:
        out[(row.identity.venue, row.venue_market_id)] = row.identity.event.scheduled_start_ms
    return out


def _leg_start_ms(
    venue: str,
    market_id: str,
    sportsbook_starts: Mapping[str, int],
    prediction_starts: Mapping[tuple[str, str], int],
) -> int | None:
    venue_name = str(venue)
    market = str(market_id)
    if venue_name.startswith("sportsbook:"):
        event_id = market.split(":", 1)[0]
        return sportsbook_starts.get(event_id)
    normalized_market = market.split("#", 1)[0]
    return prediction_starts.get((venue_name, normalized_market))


def _same_physical_game(
    lock: Any,
    sportsbook_starts: Mapping[str, int],
    prediction_starts: Mapping[tuple[str, str], int],
    *,
    max_skew_ms: int = MAX_PHYSICAL_START_SKEW_MS,
) -> tuple[bool, int | None]:
    yes_start = _leg_start_ms(
        str(lock.yes_venue), str(lock.yes_market_id), sportsbook_starts, prediction_starts
    )
    no_start = _leg_start_ms(
        str(lock.no_venue), str(lock.no_market_id), sportsbook_starts, prediction_starts
    )
    if yes_start is None or no_start is None:
        return False, None
    skew = abs(yes_start - no_start)
    return skew <= max(0, int(max_skew_ms)), skew


async def run_once(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        sportsbook_result, gemini_result, poly_result, kalshi_result = await asyncio.gather(
            base.fetch_sportsbooks(http, api_key),
            _soft_prediction_fetch("gemini", base.fetch_gemini_mlb(http)),
            _soft_prediction_fetch("polymarket", base.fetch_polymarket_mlb(http)),
            _soft_prediction_fetch("kalshi", base.fetch_kalshi_mlb(http)),
        )
        events, quota = sportsbook_result
        gemini, gemini_error = gemini_result
        poly_raw, poly_error = poly_result
        kalshi, kalshi_error = kalshi_result
        source_errors = {
            name: error
            for name, error in (
                ("gemini", gemini_error),
                ("polymarket", poly_error),
                ("kalshi", kalshi_error),
            )
            if error is not None
        }
        poly = base.full_game_only_events(poly_raw)
        direct, errors, direct_keys = await base.price_counterparts(http, gemini, poly, kalshi)

    received_ms = int(time.time() * 1000)
    retail = base.sportsbook_quotes(events, received_ms=received_ms)
    direct_quotes = [row.quote for row in direct if base._is_full_game_direct_market(row.quote.market_id)]
    direct_key_set = {q.settlement_key for q in direct_quotes}
    matched_retail = [q for q in retail if q.settlement_key in direct_key_set]

    locks = base._dedupe(base.find_cross_venue_locks(
        direct_quotes + matched_retail,
        max_quote_skew_ms=70000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    retail_vs_prediction_before_time_guard = [
        row for row in locks
        if base._is_retail(row.yes_venue) != base._is_retail(row.no_venue)
    ]

    sportsbook_starts = _sportsbook_start_map(events)
    prediction_starts = _prediction_start_map(gemini, poly, kalshi)
    retail_vs_prediction: list[Any] = []
    physical_start_skews: list[int] = []
    missing_start_count = 0
    cross_game_filtered = 0
    for row in retail_vs_prediction_before_time_guard:
        same_game, start_skew = _same_physical_game(row, sportsbook_starts, prediction_starts)
        if start_skew is None:
            missing_start_count += 1
            continue
        if not same_game:
            cross_game_filtered += 1
            continue
        physical_start_skews.append(start_skew)
        retail_vs_prediction.append(row)

    certified_rows: list[Any] = []
    assessments: list[Any] = []
    audit_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    for row in retail_vs_prediction:
        upgraded, assessment = apply_certification(row)
        certified_rows.append(upgraded)
        assessments.append(assessment)
        audit_counts[assessment.status.value] += 1
        blocker_counts.update(assessment.blockers)

    actionable_locks = [
        row for row, assessment in zip(certified_rows, assessments)
        if assessment.execution_eligible and row.settlement_confidence == ACTIONABLE_CONFIDENCE
    ]
    tally = base._update_tally(retail_vs_prediction)

    alert_rows: list[Any] = [*direct, *matched_retail]
    alerts_sent = 0
    alert_errors = 0
    for lock, assessment in zip(certified_rows, assessments):
        actionable = assessment.execution_eligible and lock.settlement_confidence == ACTIONABLE_CONFIDENCE
        blockers = ",".join(assessment.blockers) if assessment.blockers else "none"
        rule_status = f"{assessment.status.value}; blockers={blockers}"
        try:
            if await _ALERTS.maybe_send(
                lock,
                alert_rows,
                rule_status=rule_status,
                source="odds-api-mlb",
                actionable=actionable,
            ):
                alerts_sent += 1
        except Exception as exc:
            alert_errors += 1
            logger.warning("SPORTS_PUSHOVER_ERROR type=%s detail=%s", type(exc).__name__, str(exc)[:240])

    result = {
        "status": "THE_ODDS_API_MLB_SHADOW_V2_FULL_GAME_ONLY_PUSHOVER",
        "sportsbook_events": len(events),
        "sportsbook_quotes": len(retail),
        "sportsbook_quotes_matching_direct_keys": len(matched_retail),
        "books": dict(Counter(q.venue for q in retail)),
        "prediction_sources_available": {
            "gemini": gemini_error is None,
            "polymarket": poly_error is None,
            "kalshi": kalshi_error is None,
        },
        "prediction_source_errors": source_errors,
        "direct_counterpart_keys": direct_keys,
        "direct_priced_quotes": len(direct_quotes),
        "retail_vs_prediction_positive_locks_before_physical_start_guard": len(retail_vs_prediction_before_time_guard),
        "retail_vs_prediction_positive_locks": len(retail_vs_prediction),
        "retail_vs_prediction_actionable_locks": len(actionable_locks),
        "physical_event_guard": {
            "max_start_skew_ms": MAX_PHYSICAL_START_SKEW_MS,
            "cross_game_filtered": cross_game_filtered,
            "missing_start_filtered": missing_start_count,
            "max_surviving_start_skew_ms": max(physical_start_skews) if physical_start_skews else None,
            "requirement": "SPORTSBOOK_AND_PREDICTION_SCHEDULED_START_WITHIN_5_MIN",
        },
        "top_retail_vs_prediction_locks": [x.as_dict() for x in certified_rows[:30]],
        "sportsbook_opportunity_tally": tally,
        "rule_audit": {
            "status_counts": dict(audit_counts),
            "blocker_counts": dict(blocker_counts),
            "certification_required": CertificationStatus.CERTIFIED.value,
            "second_gate": True,
        },
        "pushover": {
            "configured": _ALERTS.configured,
            "alerts_sent_this_scan": alerts_sent,
            "alert_errors_this_scan": alert_errors,
            "blocked_by_rule_or_settlement_gate": len(retail_vs_prediction) - len(actionable_locks),
            "qualification": "PHYSICAL_EVENT_MATCH + FULL_RULES_AUDITED_EXECUTABLE + CERTIFIED_RULE_REGISTRY + positive_after_fee + freshness/skew + material_realert_only",
        },
        "quota": quota,
        "fetch_error_count": len(errors) + len(source_errors),
        "scope_gate": "FULL_GAME_ONLY_HARD_FILTER",
        "rule_gate": RULE_STATUS,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("THE_ODDS_API_MLB_PUSHOVER %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


async def _send_startup_test() -> None:
    if os.getenv("SPORTS_PUSHOVER_TEST_ON_START", "NO").upper() != "YES":
        return
    request_id = await _ALERTS.send(
        title="Sports alerts connected",
        message="Pushover test succeeded. Sports scanners remain read-only; no live order entry is enabled.",
    )
    logger.info("PUSHOVER_TEST_OK request_id=%s", request_id)


def _remaining(result: Mapping[str, Any]) -> int | None:
    quota = result.get("quota") if isinstance(result.get("quota"), Mapping) else {}
    try:
        return int(str(quota.get("remaining")))
    except (TypeError, ValueError):
        return None


async def supervise(api_key: str, interval_s: float, quota_floor: int) -> None:
    await _send_startup_test()
    while True:
        started = time.monotonic()
        try:
            result = await run_once(api_key)
            remaining = _remaining(result)
            if remaining is not None and remaining <= quota_floor:
                logger.warning(
                    "THE_ODDS_API_QUOTA_GUARD remaining=%s floor=%s polling_paused=true",
                    remaining, quota_floor,
                )
                while True:
                    await asyncio.sleep(3600.0)
        except Exception:
            logger.exception("THE_ODDS_API_MLB_PUSHOVER_ERROR")
        await asyncio.sleep(max(5.0, interval_s - (time.monotonic() - started)))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--quota-floor", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")
    if args.once:
        print(json.dumps(asyncio.run(run_once(api_key)), sort_keys=True, default=str))
    else:
        asyncio.run(supervise(api_key, args.interval_seconds, max(0, args.quota_floor)))


if __name__ == "__main__":
    main()
