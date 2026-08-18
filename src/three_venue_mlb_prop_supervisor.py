"""Persistent read-only supervisor for the fast strict three-venue MLB prop scan.

Two notification tiers are intentionally separated:

* Certified locks use the existing fail-closed SportsPushoverAlerts path and require
  FULL_RULES_AUDITED_EXECUTABLE plus an independently execution-eligible rule audit.
* Rule-risk candidates are informational only. They are restricted to direct
  prediction-market pairs, require official MLB participation proof, a real
  after-fee positive lock, tight freshness/skew, and are always labeled NOT
  GUARANTEED. They never promote settlement confidence and never enter an order path.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
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

D = Decimal
PREDICTION_VENUES = frozenset({"kalshi", "polymarket", "gemini"})
STAT_LABELS = {
    "hits": "Hits",
    "total_bases": "Total Bases",
    "home_runs": "Home Runs",
    "home_run": "Home Runs",
    "strikeouts": "Strikeouts",
    "pitcher_strikeouts": "Strikeouts",
    "runs_batted_in": "RBI",
    "rbis": "RBI",
    "rbi": "RBI",
    "runs": "Runs",
    "walks": "Walks",
    "stolen_bases": "Stolen Bases",
    "singles": "Singles",
    "doubles": "Doubles",
    "triples": "Triples",
}
logger = logging.getLogger("kalshi_arb.three_venue_mlb_prop_supervisor")
_alerts = SportsPushoverAlerts()
_candidate_seen: dict[str, tuple[Decimal, Decimal]] = {}


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        out = D(str(value))
        return out if out.is_finite() else D(default)
    except Exception:
        return D(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuleRiskPolicy:
    enabled: bool = True
    min_unit_profit: Decimal = D("0.03")
    min_total_profit: Decimal = D("0.03")
    max_quote_skew_ms: int = 5000
    max_quote_age_ms: int = 10000
    realert_profit_abs: Decimal = D("0.02")

    @classmethod
    def from_env(cls) -> "RuleRiskPolicy":
        def env_d(name: str, default: str) -> Decimal:
            return _d(os.getenv(name, default), default)

        def env_i(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=_env_bool("SPORTS_PROP_RULE_RISK_ALERTS_ENABLED", True),
            min_unit_profit=env_d("SPORTS_PROP_RULE_RISK_MIN_UNIT_PROFIT", "0.03"),
            min_total_profit=env_d("SPORTS_PROP_RULE_RISK_MIN_TOTAL_PROFIT", "0.03"),
            max_quote_skew_ms=env_i("SPORTS_PROP_RULE_RISK_MAX_SKEW_MS", 5000),
            max_quote_age_ms=env_i("SPORTS_PROP_RULE_RISK_MAX_AGE_MS", 10000),
            realert_profit_abs=env_d("SPORTS_PROP_RULE_RISK_REALERT_PROFIT_ABS", "0.02"),
        )


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
    """Rehydrate scanner JSON into the notification adapters' tiny interface.

    The scanner remains the source of truth for certification. This adapter never
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
    """Certified-arb notification path. Deliberately unchanged by candidate alerts."""
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


def _candidate_leg(lock: Any, rows: list[SimpleNamespace], *, yes: bool, now_ms: int) -> dict[str, Any] | None:
    venue = str(lock.yes_venue if yes else lock.no_venue)
    market_id = str(lock.yes_market_id if yes else lock.no_market_id)
    for row in rows:
        q = row.quote
        if str(q.venue) != venue or str(q.market_id) != market_id:
            continue
        return {
            "venue": venue,
            "market_id": market_id,
            "side": "YES" if yes else "NO",
            "price": _d(q.yes_cost if yes else q.no_cost),
            "received_ms": int(q.received_ms),
            "age_ms": max(0, now_ms - int(q.received_ms)),
        }
    return None


def _rule_risk_qualification(
    lock: Any,
    rows: list[SimpleNamespace],
    assessment: dict[str, Any],
    policy: RuleRiskPolicy,
    *,
    now_ms: int,
) -> tuple[bool, str, dict[str, Any] | None, dict[str, Any] | None]:
    """Qualify an informational rule-risk candidate without promoting confidence."""
    if not policy.enabled:
        return False, "disabled", None, None
    if bool(assessment.get("execution_eligible")) or str(getattr(lock, "settlement_confidence", "")) == ACTIONABLE_CONFIDENCE:
        return False, "certified_path_only", None, None
    venues = {str(getattr(lock, "yes_venue", "")), str(getattr(lock, "no_venue", ""))}
    if len(venues) != 2 or not venues.issubset(PREDICTION_VENUES):
        return False, "not_direct_prediction_pair", None, None
    if not bool(assessment.get("participation_proven")):
        return False, "participation_not_proven", None, None
    blockers = [str(x) for x in assessment.get("remaining_blockers", []) if str(x)]
    if not blockers:
        return False, "missing_rule_risk_context", None, None
    if _d(getattr(lock, "unit_profit", "0")) < policy.min_unit_profit:
        return False, "unit_profit_below_candidate_floor", None, None
    if _d(getattr(lock, "max_locked_profit", "0")) < policy.min_total_profit:
        return False, "total_profit_below_candidate_floor", None, None
    if int(getattr(lock, "quote_skew_ms", 10**9)) > policy.max_quote_skew_ms:
        return False, "quote_skew_too_large", None, None
    yes = _candidate_leg(lock, rows, yes=True, now_ms=now_ms)
    no = _candidate_leg(lock, rows, yes=False, now_ms=now_ms)
    if yes is None or no is None:
        return False, "missing_quote_leg", yes, no
    if max(int(yes["age_ms"]), int(no["age_ms"])) > policy.max_quote_age_ms:
        return False, "quote_too_old", yes, no
    return True, "qualified_rule_risk_candidate", yes, no


def _candidate_key(lock: Any) -> str:
    return "|".join((
        str(lock.settlement_key), str(lock.yes_venue), str(lock.yes_market_id),
        str(lock.no_venue), str(lock.no_market_id),
    ))


def _candidate_should_realert(lock: Any, policy: RuleRiskPolicy) -> bool:
    key = _candidate_key(lock)
    profit = _d(getattr(lock, "max_locked_profit", "0"))
    qty = _d(getattr(lock, "max_quantity", "0"))
    prior = _candidate_seen.get(key)
    if prior is None:
        return True
    prior_profit, prior_qty = prior
    return profit - prior_profit >= policy.realert_profit_abs or qty > prior_qty


def _mark_candidate_sent(lock: Any) -> None:
    _candidate_seen[_candidate_key(lock)] = (
        _d(getattr(lock, "max_locked_profit", "0")),
        _d(getattr(lock, "max_quantity", "0")),
    )


def _fmt_cents(value: Any, *, signed: bool = False) -> str:
    cents = _d(value) * D("100")
    sign = "+" if signed and cents >= 0 else ""
    if cents == cents.to_integral_value():
        return f"{sign}{int(cents)}¢"
    text = f"{cents:.1f}".rstrip("0").rstrip(".")
    return f"{sign}{text}¢"


def _pretty_venue(value: Any) -> str:
    raw = str(value or "").strip()
    aliases = {"polymarket": "Polymarket", "kalshi": "Kalshi", "gemini": "Gemini"}
    return aliases.get(raw.lower(), raw.replace("_", " ").title() or "Unknown")


def _prop_identity(lock: Any, assessment: dict[str, Any]) -> tuple[str, str]:
    player = str(assessment.get("player") or "MLB Player").strip()
    parts = [part for part in str(getattr(lock, "settlement_key", "")).split("|") if part]
    stat = "prop"
    comparator = ""
    threshold = ""
    try:
        player_idx = parts.index("player")
        stat = parts[player_idx + 2]
        comparator = parts[player_idx + 3]
        threshold = parts[player_idx + 4]
    except (ValueError, IndexError):
        family = str(assessment.get("family") or "")
        stat = family.removeprefix("baseball_player_") or "prop"

    stat_label = STAT_LABELS.get(stat.lower(), stat.replace("_", " ").title())
    threshold_text = threshold
    if comparator == "GE" and threshold:
        threshold_text = f"{threshold}+"
    elif comparator == "LE" and threshold:
        threshold_text = f"{threshold} or fewer"
    elif comparator == "GT" and threshold:
        threshold_text = f"Over {threshold}"
    elif comparator == "LT" and threshold:
        threshold_text = f"Under {threshold}"
    elif comparator == "EQ" and threshold:
        threshold_text = threshold

    market_label = f"{threshold_text} {stat_label}".strip()
    return player, market_label


def _candidate_message(
    lock: Any,
    assessment: dict[str, Any],
    yes: dict[str, Any],
    no: dict[str, Any],
) -> tuple[str, str]:
    profit = _d(getattr(lock, "max_locked_profit", "0"))
    unit_profit = _d(getattr(lock, "unit_profit", "0"))
    qty = _d(getattr(lock, "max_quantity", "0"))
    cost = _d(getattr(lock, "max_capital", "0"))
    blockers = [str(x) for x in assessment.get("remaining_blockers", []) if str(x)]
    risk_text = ", ".join(x.replace("_", " ") for x in blockers[:4]) or "incomplete settlement fingerprint"
    player, market_label = _prop_identity(lock, assessment)

    title = f"{player} • {market_label} • {_fmt_cents(unit_profit, signed=True)} • RULE RISK"
    message = "\n".join((
        f"{player} — {market_label}",
        "",
        f"YES — {_pretty_venue(yes['venue'])}: {_fmt_cents(yes['price'])}",
        f"NO — {_pretty_venue(no['venue'])}: {_fmt_cents(no['price'])}",
        "",
        f"After-fee gap: {_fmt_cents(unit_profit, signed=True)} per contract",
        f"Max size: {qty} • Cost: ${cost:.2f}",
        f"Max locked profit: ${profit:.2f}",
        "",
        "Participation: PROVEN",
        "",
        f"Rule differences: {risk_text}",
        "",
        "NOT GUARANTEED • Read-only • no order entry",
    ))
    return title[:250], message[:1024]


async def _deliver_rule_risk_alerts(
    result: dict[str, Any],
    sink: SportsPushoverAlerts | None = None,
    policy: RuleRiskPolicy | None = None,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Deliver clearly labeled post-participation direct-market candidates.

    This path never calls ``maybe_send`` because that method is reserved for true
    execution-audited arbs. It uses only the transport method after this module's
    separate rule-risk qualification, preserving a hard semantic boundary.
    """
    sink = sink or _alerts
    policy = policy or RuleRiskPolicy.from_env()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    candidates = _notification_candidates(result)
    reasons: Counter[str] = Counter()
    eligible = 0
    sent = 0
    suppressed_realerts = 0
    errors: list[str] = []

    for lock, rows, assessment, _actionable in candidates:
        ok, reason, yes, no = _rule_risk_qualification(
            lock, rows, assessment, policy, now_ms=now_ms
        )
        if not ok:
            reasons[reason] += 1
            continue
        eligible += 1
        if not sink.configured:
            reasons["pushover_not_configured"] += 1
            continue
        if not _candidate_should_realert(lock, policy):
            suppressed_realerts += 1
            continue
        assert yes is not None and no is not None
        title, message = _candidate_message(lock, assessment, yes, no)
        try:
            await sink.send(title=title, message=message)
            _mark_candidate_sent(lock)
            sent += 1
            logger.info("MLB_PROP_RULE_RISK_PUSHOVER_SENT key=%s", lock.settlement_key)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{str(exc)[:200]}")

    return {
        "enabled": policy.enabled,
        "configured": sink.configured,
        "candidate_locks_this_scan": len(candidates),
        "eligible_rule_risk_candidates": eligible,
        "alerts_sent_this_scan": sent,
        "suppressed_realerts": suppressed_realerts,
        "blocked_reasons": dict(reasons),
        "alert_errors_this_scan": len(errors),
        "errors": errors[:5],
        "qualification": (
            "DIRECT_PREDICTION_PAIR + MLB_PARTICIPATION_PROVEN + positive_after_fee + "
            "freshness/skew + remaining_rule_risk + NOT_GUARANTEED label"
        ),
        "min_unit_profit": str(policy.min_unit_profit),
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
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
            result["rule_risk_pushover"] = await _deliver_rule_risk_alerts(result)
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
