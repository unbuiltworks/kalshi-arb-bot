"""One-attempt K/G/P sports micro-live canary service.

The service may be deployed continuously in a harmless state. It cannot submit an
order unless ALL of the following are true at the same time:

1. an exact pair is present in config/sports_kgp_execution_approvals.json,
2. that record is approved and carries a SHA-256 rule fingerprint,
3. the live scanner independently returns FULL_RULES_AUDITED_EXECUTABLE and an
   execution-eligible rule assessment for that exact settlement key,
4. the q=1 micro guard revalidates IDs/orientation/profit/freshness/capital,
5. --live was supplied, ENABLE_SPORTS_KGP_MICRO_LIVE=YES is set, and
6. a one-use arm-token file exists.

The arm token is consumed immediately before the first paired submission. This
service never creates the token and exits after one live attempt.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from src.current_polymarket_mlb_props import fetch_current_poly_mlb_props
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request
from src.sports_kgp_micro_guard import (
    DEFAULT_MAX_CAPITAL,
    DEFAULT_MAX_QUOTE_SKEW_MS,
    DEFAULT_MIN_UNIT_PROFIT,
    EXECUTABLE_CONFIDENCE,
    RuleApproval,
    build_micro_plan,
)
from src.sports_kgp_micro_transport import (
    DEFAULT_ARM_TOKEN,
    PolymarketMicroExecutor,
    consume_arm_token,
    live_authorized,
    submit_micro_pair,
)
from src.three_venue_pricing_core import PricedQuote
from src.universal_prediction_quant import MarketQuote, find_cross_venue_locks
from src import three_venue_mlb_prop_safe_scan as scan

scan._fetch_poly_mlb = fetch_current_poly_mlb_props

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_kgp_micro_service")
DEFAULT_APPROVALS = Path("config/sports_kgp_execution_approvals.json")
DEFAULT_LOG = Path("/data/sports-kgp-micro-live.jsonl")
MIN_VENUE_CASH = D("2.00")


def _write(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True, default=str) + "\n")
        handle.flush()


def load_approvals(path: Path) -> dict[str, list[RuleApproval]]:
    body = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[RuleApproval]] = {}
    for raw in body.get("approvals") or []:
        approval = RuleApproval.from_mapping(raw)
        approval.validate()
        out.setdefault(approval.settlement_key, []).append(approval)
    return out


def _base_market_id(value: Any) -> str:
    return str(value or "").split("#", 1)[0]


def _priced(raw: dict[str, Any], *, certified: bool) -> PricedQuote:
    confidence = EXECUTABLE_CONFIDENCE if certified else str(raw.get("settlement_confidence") or "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE")
    quote = MarketQuote(
        venue=str(raw["venue"]),
        market_id=str(raw["market_id"]),
        settlement_key=str(raw["settlement_key"]),
        category="sports_mlb_prop",
        yes_cost=D(str(raw["all_in_yes_cost"])),
        no_cost=D(str(raw["all_in_no_cost"])),
        yes_depth=D(str(raw.get("yes_depth") or "0")),
        no_depth=D(str(raw.get("no_depth") or "0")),
        received_ms=int(raw["received_ms"]),
        settlement_confidence=confidence,
        expected_settlement_minutes=None,
    )
    return PricedQuote(
        quote=quote,
        raw_yes_ask=D(str(raw["raw_yes_ask"])),
        raw_no_ask=D(str(raw["raw_no_ask"])),
        yes_fee=D(str(raw.get("yes_fee") or "0")),
        no_fee=D(str(raw.get("no_fee") or "0")),
        depth_source=str(raw.get("depth_source") or "unknown"),
        source_side=str(raw.get("source_side") or ""),
    )


def _approved_pair_rows(
    raw_rows: list[dict[str, Any]],
    approval: RuleApproval,
) -> list[PricedQuote]:
    """Return only the exact market IDs/orientations frozen by one rule approval."""
    rows: list[PricedQuote] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        venue = str(raw.get("venue") or "")
        if venue not in approval.venues:
            continue
        if _base_market_id(raw.get("market_id")) != approval.market_for(venue):
            continue
        if str(raw.get("source_side") or "") != approval.source_side_for(venue):
            continue
        rows.append(_priced(raw, certified=True))
    return rows


def candidate_plans(
    result: dict[str, Any],
    approvals: dict[str, list[RuleApproval]],
    *,
    now_ms: int | None = None,
) -> tuple[list[tuple[Any, RuleApproval]], list[dict[str, str]]]:
    """Recompute approved q=1 locks from final books, never discovery economics.

    ``top_one_contract_locks`` is used only as an independent scanner-certification
    gate. The prices/profit sent to the execution guard are rebuilt from the latest
    ``positive_quote_audit`` rows (including the Gemini depth10 overlay when used).
    This prevents an earlier discovery spread from authorizing a worse final book.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    assessments = {
        str(row.get("settlement_key") or ""): row
        for row in result.get("rule_audit", {}).get("top_assessments", [])
        if isinstance(row, dict) and row.get("settlement_key")
    }
    certified_scanner_keys = {
        str(raw.get("settlement_key") or "")
        for raw in result.get("top_one_contract_locks", [])
        if isinstance(raw, dict)
        and raw.get("settlement_key")
        and str(raw.get("settlement_confidence") or "") == EXECUTABLE_CONFIDENCE
    }
    quotes_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in result.get("positive_quote_audit", []):
        if isinstance(row, dict) and row.get("settlement_key"):
            quotes_by_key.setdefault(str(row["settlement_key"]), []).append(row)

    plans: list[tuple[Any, RuleApproval]] = []
    rejected: list[dict[str, str]] = []
    for key, available_approvals in approvals.items():
        assessment = assessments.get(key, {})
        if not bool(assessment.get("execution_eligible")):
            rejected.append({"settlement_key": key, "reason": "rule_assessment_not_execution_eligible"})
            continue
        if key not in certified_scanner_keys:
            rejected.append({"settlement_key": key, "reason": "scanner_lock_not_execution_audited"})
            continue

        raw_rows = quotes_by_key.get(key, [])
        for approval in available_approvals:
            try:
                rows = _approved_pair_rows(raw_rows, approval)
                if {row.quote.venue for row in rows} != set(approval.venues):
                    raise ValueError("exact current approved quote pair is incomplete")
                fresh_locks = find_cross_venue_locks(
                    [row.quote for row in rows],
                    min_unit_profit=DEFAULT_MIN_UNIT_PROFIT,
                    max_quote_skew_ms=DEFAULT_MAX_QUOTE_SKEW_MS,
                    min_confidence=EXECUTABLE_CONFIDENCE,
                )
                # find_cross_venue_locks returns both directions when profitable;
                # every row is already exact-ID/orientation scoped to the approval.
                if not fresh_locks:
                    raise ValueError("final execution books no longer contain a >=3c lock")
                for lock in fresh_locks:
                    plan = build_micro_plan(lock, rows, approval, now_ms=now_ms)
                    plans.append((plan, approval))
            except Exception as exc:
                rejected.append({"settlement_key": key, "reason": f"{type(exc).__name__}:{str(exc)[:180]}"})
                continue
    plans.sort(key=lambda item: (item[0].locked_profit, -item[0].quote_skew_ms), reverse=True)
    return plans, rejected


async def _venue_cash(plan: Any, *, poly: PolymarketMicroExecutor | None, kalshi: KalshiV2Executor | None) -> dict[str, Decimal]:
    balances: dict[str, Decimal] = {}
    venues = {plan.yes_leg.venue, plan.no_leg.venue}
    if "gemini" in venues:
        terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
        terms.raise_for_status()
        if not bool(terms.json().get("hasAcceptedLatest")):
            raise RuntimeError("Gemini Prediction Markets terms are not accepted")
        response = await private_rest_request("POST", "/v1/balances", timeout=5.0)
        response.raise_for_status()
        usd = next((row for row in response.json() if str(row.get("currency") or "").upper() == "USD"), {})
        balances["gemini"] = D(str(usd.get("available") or "0"))
    if "kalshi" in venues:
        assert kalshi is not None
        response = await kalshi.client.get("/portfolio/balance")
        response.raise_for_status()
        balances["kalshi"] = D(str(response.json().get("balance") or "0")) / D("100")
    if "polymarket" in venues:
        assert poly is not None
        body = await asyncio.to_thread(poly.client.account.balances)
        candidates = []
        if isinstance(body, dict):
            candidates.extend([body.get("available"), body.get("availableBalance"), body.get("buyingPower")])
            for row in body.get("balances") or []:
                if isinstance(row, dict) and str(row.get("currency") or "USD").upper() in {"USD", "USDC"}:
                    candidates.extend([row.get("available"), row.get("availableBalance"), row.get("balance")])
        parsed = [D(str(value)) for value in candidates if value is not None]
        if not parsed:
            raise RuntimeError("Polymarket available cash could not be determined")
        balances["polymarket"] = max(parsed)
    for venue, cash in balances.items():
        if cash < MIN_VENUE_CASH:
            raise RuntimeError(f"{venue} available cash is below ${MIN_VENUE_CASH}")
    return balances


async def run(*, live_requested: bool, once: bool, interval_seconds: float, approvals_path: Path, token_path: Path, log_path: Path) -> int:
    approvals = load_approvals(approvals_path)
    _write(log_path, {
        "ts": time.time(),
        "mode": "SPORTS_KGP_MICRO_START",
        "approved_settlement_keys": len(approvals),
        "approved_pair_records": sum(len(rows) for rows in approvals.values()),
        "live_requested": bool(live_requested),
        "env_live_gate": os.getenv("ENABLE_SPORTS_KGP_MICRO_LIVE", "NO").strip().upper() == "YES",
        "arm_token_present": token_path.is_file(),
        "armed": live_authorized(live_requested=live_requested, token_path=token_path),
        "quantity_cap": "1",
        "capital_ceiling": str(DEFAULT_MAX_CAPITAL),
        "order_entry": False,
    })

    if not approvals:
        _write(log_path, {
            "ts": time.time(), "mode": "WAITING_FOR_CERTIFIED_APPROVAL",
            "reason": "approval_registry_empty", "order_entry": False,
        })
        if once:
            return 0

    while True:
        result = await scan.run_once()
        plans, rejected = candidate_plans(result, approvals)
        _write(log_path, {
            "ts": time.time(), "mode": "SPORTS_KGP_MICRO_CYCLE",
            "scanner_positive_locks": int(result.get("one_contract_positive_locks") or 0),
            "scanner_certified_locks": int(result.get("certified_actionable_locks") or 0),
            "approved_plans": len(plans),
            "rejected_approved_candidates": rejected[:10],
            "armed": live_authorized(live_requested=live_requested, token_path=token_path),
            "order_entry": False,
        })
        if not plans:
            if once:
                return 0
            await asyncio.sleep(interval_seconds)
            continue

        plan, approval = plans[0]
        if not live_authorized(live_requested=live_requested, token_path=token_path):
            _write(log_path, {
                "ts": time.time(), "mode": "DISARMED_APPROVED_PLAN",
                "plan": plan.as_dict(), "order_entry": False,
            })
            if once:
                return 0
            await asyncio.sleep(interval_seconds)
            continue

        venues = {plan.yes_leg.venue, plan.no_leg.venue}
        gemini: GeminiTradingSession | None = GeminiTradingSession() if "gemini" in venues else None
        kalshi: KalshiV2Executor | None = KalshiV2Executor() if "kalshi" in venues else None
        poly: PolymarketMicroExecutor | None = PolymarketMicroExecutor() if "polymarket" in venues else None
        try:
            if gemini is not None:
                await gemini.connect()
            balances = await _venue_cash(plan, poly=poly, kalshi=kalshi)
            if not consume_arm_token(token_path):
                _write(log_path, {"ts": time.time(), "mode": "ARM_TOKEN_RACE_DISARMED", "order_entry": False})
                continue
            _write(log_path, {
                "ts": time.time(), "mode": "LIVE_ATTEMPT_START",
                "plan": plan.as_dict(),
                "rule_fingerprint_sha256": approval.rule_fingerprint_sha256,
                "venue_cash_preflight": {k: str(v) for k, v in balances.items()},
                "single_use_token_consumed": True,
                "order_entry": True,
            })
            execution = await submit_micro_pair(plan, gemini=gemini, kalshi=kalshi, polymarket=poly)
            _write(log_path, {
                "ts": time.time(), "mode": "LIVE_ATTEMPT_RESULT",
                "plan": plan.as_dict(), "execution": execution,
                "single_use_token_consumed": True,
            })
            return 70 if execution.get("hard_halt_required") else 0
        finally:
            if gemini is not None:
                await gemini.close()
            if kalshi is not None:
                await kalshi.close()
            if poly is not None:
                poly.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--approvals", type=Path, default=DEFAULT_APPROVALS)
    parser.add_argument("--arm-token", type=Path, default=DEFAULT_ARM_TOKEN)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(
        live_requested=args.live,
        once=args.once,
        interval_seconds=max(0.25, args.interval_seconds),
        approvals_path=args.approvals,
        token_path=args.arm_token,
        log_path=args.log,
    )))


if __name__ == "__main__":
    main()
