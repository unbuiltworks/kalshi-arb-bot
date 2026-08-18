"""Persistent read-only supervisor for the fast strict three-venue MLB prop scan.

Certified prop locks may be delivered through the shared Pushover sink.  The
notification adapter is deliberately fail-closed: research/conditional locks are
counted but never sent, and this module contains no order-entry path.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

from src.current_polymarket_mlb_props import cache_status, fetch_current_poly_mlb_props
from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE, SportsPushoverAlerts
from src import three_venue_mlb_prop_safe_scan as scan

# The league endpoint currently omits player props. Keep the strict scanner itself
# unchanged and inject the cached, current Polymarket discovery surface here so the
# persistent worker separates slow discovery from fast pricing.
scan._fetch_poly_mlb = fetch_current_poly_mlb_props

logger = logging.getLogger("kalshi_arb.three_venue_mlb_prop_supervisor")
_alerts = SportsPushoverAlerts()


def _append(path: str, payload: dict[str, Any]) -> None:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str) + "\n")
        handle.flush(); os.fsync(handle.fileno())


def _quote_adapter(row: dict[str, Any]) -> SimpleNamespace:
    quote = SimpleNamespace(
        venue=str(row.get("venue") or ""),
        market_id=str(row.get("market_id") or ""),
        settlement_key=str(row.get("settlement_key") or ""),
        yes_cost=row.get("all_in_yes_cost", "0"),
        no_cost=row.get("all_in_no_cost", "0"),
        received_ms=int(row.get("received_ms") or 0),
    )
    return SimpleNamespace(
        quote=quote,
        yes_fee=row.get("yes_fee", "0"),
        no_fee=row.get("no_fee", "0"),
    )


def _notification_candidates(result: dict[str, Any]) -> list[tuple[SimpleNamespace, list[SimpleNamespace], dict[str, Any], bool]]:
    """Rehydrate scanner JSON into the existing Pushover sink's tiny interface.

    The scanner remains the source of truth for certification.  This adapter never
    promotes confidence: a row is actionable only when both the rule assessment
    and the lock itself independently say it is execution-audited.
    """
    assessments = {
        str(row.get("settlement_key") or ""): row
        for row in result.get("rule_audit", {}).get("top_assessments", [])
        if isinstance(row, dict) and row.get("settlement_key")
    }
    quotes = [
        _quote_adapter(row)
        for row in result.get("positive_quote_audit", [])
        if isinstance(row, dict)
    ]
    out: list[tuple[SimpleNamespace, list[SimpleNamespace], dict[str, Any], bool]] = []
    for raw in result.get("top_one_contract_locks", []):
        if not isinstance(raw, dict) or not raw.get("settlement_key"):
            continue
        key = str(raw["settlement_key"])
        assessment = assessments.get(key, {})
        lock = SimpleNamespace(**raw)
        rows = [row for row in quotes if row.quote.settlement_key == key]
        actionable = bool(assessment.get("execution_eligible")) and (
            str(raw.get("settlement_confidence") or "") == ACTIONABLE_CONFIDENCE
        )
        out.append((lock, rows, assessment, actionable))
    return out


async def _deliver_prop_alerts(result: dict[str, Any], sink: SportsPushoverAlerts | None = None) -> dict[str, Any]:
    sink = sink or _alerts
    candidates = _notification_candidates(result)
    sent = 0
    errors: list[str] = []
    eligible = 0
    blocked = 0
    for lock, rows, assessment, actionable in candidates:
        if not actionable:
            blocked += 1
            continue
        eligible += 1
        try:
            if await sink.maybe_send(
                lock,
                rows,
                rule_status=str(assessment.get("status") or "CERTIFIED"),
                source="MLB_PLAYER_PROP",
                actionable=True,
            ):
                sent += 1
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{str(exc)[:200]}")
    return {
        "configured": sink.configured,
        "candidate_locks_this_scan": len(candidates),
        "eligible_this_scan": eligible,
        "blocked_by_rule_or_settlement_gate": blocked,
        "alerts_sent_this_scan": sent,
        "alert_errors_this_scan": len(errors),
        "errors": errors[:5],
        "qualification": (
            "FULL_RULES_AUDITED_EXECUTABLE + execution_eligible rule assessment + "
            "profit/freshness/skew + material_realert_only"
        ),
        "read_only": True,
        "order_entry": False,
    }


async def supervise(interval_seconds: float, journal_path: str | None, once: bool) -> None:
    if interval_seconds < 2:
        raise ValueError("interval_seconds must be >= 2")
    cycle = 0
    while True:
        cycle += 1; started = time.monotonic()
        try:
            result = await scan.run_once()
            result["polymarket_discovery_cache"] = cache_status()
            result["pushover"] = await _deliver_prop_alerts(result)
            envelope = {"ts_utc":datetime.now(timezone.utc).isoformat(),"cycle":cycle,"ok":True,"result":result}
        except Exception as exc:
            envelope = {"ts_utc":datetime.now(timezone.utc).isoformat(),"cycle":cycle,"ok":False,
                        "error":f"{type(exc).__name__}:{str(exc)[:500]}","read_only":True,"order_entry":False}
        envelope["supervisor_cycle_ms"] = round((time.monotonic()-started)*1000, 3)
        logger.info("THREE_VENUE_MLB_PROP_CYCLE %s", json.dumps(envelope,separators=(",",":"),sort_keys=True,default=str))
        if journal_path: _append(journal_path, envelope)
        if once: return
        await asyncio.sleep(max(0.25, interval_seconds - (time.monotonic()-started)))


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--interval-seconds",type=float,default=10.0)
    parser.add_argument("--journal-path",default=""); parser.add_argument("--once",action="store_true")
    args=parser.parse_args(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(supervise(args.interval_seconds,args.journal_path or None,args.once))


if __name__ == "__main__": main()
