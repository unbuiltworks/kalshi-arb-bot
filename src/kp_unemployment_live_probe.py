"""Isolated q=1 LIVE_PROBE for certified August 2026 U-3 K/P pairs.

No sports discovery is used. Only the three exact approvals in
config/kp_unemployment_execution_approvals.json can enter the state machine.
Deployment is safe while disarmed. A live attempt additionally requires:
  * ENABLE_SPORTS_KP_LIVE=YES on this service only,
  * exact live rule text/market identity validation,
  * fresh two-venue books (<=100 ms, <=50 ms receive skew),
  * q=1 after-fee profit >= $0.03 observed persistently,
  * hardened zero-position/market-state preflight immediately before mutation.

Manual mode also requires a unique run id plus an arm deadline no more than five
minutes ahead. Auto-live mode (KP_UNEMPLOYMENT_AUTO_LIVE=YES) watches continuously
but does not keep an ARM token open: it creates a process-local, single-use token
only after a qualifying persistent lock is observed. The hardened coordinator
then performs venue preflight and fresh-plan revalidation before consuming that
token immediately before order mutation. At most one submission attempt occurs
per process; Railway must run it with restartPolicy=NEVER.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.kp_unemployment_certification import APPROVALS, U3Approval, validate_kalshi_market, validate_poly_market
from src.polymarket_kalshi_shadow import best_plan
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.sports_kp_execution import ExecutionLeg, KPExecutionPlan
from src.sports_kp_live_coordinator import execute_after_fresh_revalidation
from src.sports_kp_venue_hardening import HardenedKalshiExecutor, HardenedPolymarketExecutor

D = Decimal
logger = logging.getLogger("kalshi_arb.kp_unemployment_live_probe")
POLY_BASE = "https://gateway.polymarket.us"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
MAX_BOOK_AGE_MS = 100
MAX_SKEW_MS = 50
MIN_PROFIT = D("0.03")
PERSIST_MS = 10
MAX_ARM_SECONDS = 300
HEARTBEAT_SECONDS = 30


@dataclass
class Seen:
    first_ms: int
    fingerprint: tuple[str, ...]


def _fee_rates() -> tuple[D, D]:
    return (
        D(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05")),
        D(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07")),
    )


def _load_config() -> dict[str, dict[str, Any]]:
    path = Path(os.getenv("KP_UNEMPLOYMENT_APPROVALS", "config/kp_unemployment_execution_approvals.json"))
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("mode") != "LIVE_PROBE" or int(body.get("max_quantity", 0)) != 1:
        raise PermissionError("approval file is not q=1 LIVE_PROBE")
    rows = {str(row["polymarket_slug"]): row for row in body.get("approvals", [])}
    if set(rows) != {a.polymarket_slug for a in APPROVALS}:
        raise PermissionError("approval file must contain exactly the three audited U3 contracts")
    for approval in APPROVALS:
        row = rows[approval.polymarket_slug]
        if not bool(row.get("approved_for_execution")):
            raise PermissionError("approval row is not execution-approved")
        expected = {
            "settlement_key": approval.settlement_key,
            "kalshi_ticker": approval.kalshi_ticker,
            "rule_fingerprint_sha256": approval.fingerprint,
            "polymarket_source_side": "CONTRACT",
            "kalshi_source_side": "CONTRACT",
        }
        for key, value in expected.items():
            if str(row.get(key) or "") != value:
                raise PermissionError(f"approval drift for {approval.polymarket_slug}: {key}")
    return rows


async def _validate_live_rules(http: httpx.AsyncClient, approval: U3Approval) -> None:
    p_resp, k_resp = await asyncio.gather(
        http.get(f"{POLY_BASE}/v1/market/slug/{approval.polymarket_slug}"),
        http.get(f"{KALSHI_BASE}/markets/{approval.kalshi_ticker}"),
    )
    p_resp.raise_for_status(); k_resp.raise_for_status()
    p_body = p_resp.json(); k_body = k_resp.json()
    p_market = p_body.get("market") or {}
    k_market = k_body.get("market") or {}
    validate_poly_market(approval, p_market)
    validate_kalshi_market(approval, k_market)


def _leg(venue: str, market_id: str, side: str, price: D) -> ExecutionLeg:
    native = "YES" if side == "OVER" else "NO"
    yes_limit = price if native == "YES" else D("1") - price
    return ExecutionLeg(
        venue=venue,
        market_id=market_id,
        normalized_outcome=native,
        native_outcome=native,
        raw_outcome_price=price,
        yes_book_limit=yes_limit,
        source_side="CONTRACT",
    )


async def _evaluate_plan(
    approval: U3Approval,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
) -> tuple[KPExecutionPlan | None, str, dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    pq = poly.quote(approval.polymarket_slug, max_age_ms=60_000)
    kq = await kalshi.quote(approval.kalshi_ticker, max_age_ms=60_000)
    if pq is None or kq is None or not poly.connected:
        return None, "missing_book", {}
    poly_age = max(0, now_ms - int(pq.received_ms))
    kalshi_age = max(0, now_ms - int(kq.received_ms))
    metrics: dict[str, Any] = {
        "poly_age_ms": poly_age,
        "kalshi_age_ms": kalshi_age,
    }
    if poly_age > MAX_BOOK_AGE_MS:
        return None, "poly_stale", metrics
    if kalshi_age > MAX_BOOK_AGE_MS:
        return None, "kalshi_stale", metrics
    skew = abs(int(pq.received_ms) - int(kq.received_ms))
    metrics["quote_skew_ms"] = skew
    if skew > MAX_SKEW_MS:
        return None, "quote_skew", metrics
    poly_fee, kalshi_fee = _fee_rates()
    shadow = best_plan(pq, kq, bankroll=D("1"), poly_fee_rate=poly_fee, kalshi_fee_rate=kalshi_fee)
    if shadow is None:
        return None, "no_complementary_plan", metrics
    metrics.update({
        "quantity": shadow.quantity,
        "after_fee_profit": str(shadow.profit),
        "poly_side": shadow.poly_side,
        "poly_price": str(shadow.poly_price),
        "kalshi_side": shadow.kalshi_side,
        "kalshi_price": str(shadow.kalshi_price),
    })
    if shadow.quantity != 1:
        return None, "not_q1", metrics
    if shadow.profit < MIN_PROFIT:
        return None, "profit_below_threshold", metrics
    plan = KPExecutionPlan(
        settlement_key=approval.settlement_key,
        unit_profit=shadow.profit,
        quote_skew_ms=skew,
        created_ms=now_ms,
        poly=_leg("polymarket", approval.polymarket_slug, shadow.poly_side, shadow.poly_price),
        kalshi=_leg("kalshi", approval.kalshi_ticker, shadow.kalshi_side, shadow.kalshi_price),
        rule_fingerprint_sha256=approval.fingerprint,
    )
    return plan, "qualified", metrics


async def _plan(
    approval: U3Approval,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
) -> KPExecutionPlan | None:
    plan, _, _ = await _evaluate_plan(approval, poly, kalshi)
    return plan


def _fp(plan: KPExecutionPlan) -> tuple[str, ...]:
    return (
        plan.settlement_key,
        plan.poly.native_outcome,
        str(plan.poly.raw_outcome_price),
        plan.kalshi.native_outcome,
        str(plan.kalshi.raw_outcome_price),
        plan.rule_fingerprint_sha256,
    )


def _auto_live_requested() -> bool:
    return os.getenv("KP_UNEMPLOYMENT_AUTO_LIVE", "NO").strip().upper() == "YES"


def _arm_state() -> tuple[bool, str, int | None]:
    live = os.getenv("ENABLE_SPORTS_KP_LIVE", "NO").strip().upper() == "YES"
    auto_live = _auto_live_requested()
    if auto_live:
        if not live:
            return False, "", None
        return True, f"u3-auto-{int(time.time())}-{os.getpid()}", None
    run_id = os.getenv("KP_UNEMPLOYMENT_RUN_ID", "").strip()
    raw = os.getenv("KP_UNEMPLOYMENT_ARM_UNTIL", "").strip()
    if not live or not run_id or not raw:
        return False, run_id, None
    try:
        deadline = int(raw)
    except ValueError:
        raise PermissionError("invalid KP_UNEMPLOYMENT_ARM_UNTIL")
    now = int(time.time())
    if deadline <= now or deadline - now > MAX_ARM_SECONDS:
        raise PermissionError("arm deadline must be in the next five minutes")
    return True, run_id, deadline


async def _next_update(poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    pt = asyncio.create_task(poly.updates.get()); kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait({pt, kt}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending: task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def run() -> int:
    _load_config()
    auto_live = _auto_live_requested()
    armed, run_id, arm_deadline = _arm_state()
    preflight_only = os.getenv("KP_UNEMPLOYMENT_PREFLIGHT", "YES").strip().upper() == "YES"
    logger.info("KP_U3_START %s", json.dumps({
        "mode": "LIVE_PROBE", "pairs": len(APPROVALS), "q_max": 1,
        "armed": armed, "auto_live": auto_live, "run_id": run_id or None,
        "arm_deadline": arm_deadline, "preflight_only": preflight_only,
        "max_book_age_ms": MAX_BOOK_AGE_MS, "max_skew_ms": MAX_SKEW_MS,
        "min_profit": str(MIN_PROFIT), "order_entry": bool(armed and not preflight_only),
    }, separators=(",", ":")))

    async with httpx.AsyncClient(timeout=15.0) as http:
        await asyncio.gather(*(_validate_live_rules(http, a) for a in APPROVALS))
        poly = EventingPolyBookStore()
        sem = asyncio.Semaphore(6)
        poly.notifications_enabled = False
        try:
            await asyncio.gather(*(_fetch_poly_book(http, poly, a.polymarket_slug, sem) for a in APPROVALS))
        finally:
            poly.notifications_enabled = True

    key_id = os.environ["POLYMARKET_KEY_ID"]; secret = os.environ["POLYMARKET_SECRET_KEY"]
    pclient = PolymarketUS(key_id=key_id, secret_key=secret, timeout=8.0)
    kalshi = EventingKalshiCrossBooks([a.kalshi_ticker for a in APPROVALS])
    poly_task = asyncio.create_task(run_poly_ws(pclient, poly, [a.polymarket_slug for a in APPROVALS]))
    kalshi_task = asyncio.create_task(kalshi.run())
    try:
        startup_deadline = time.monotonic() + 20
        while time.monotonic() < startup_deadline:
            if poly_task.done() or kalshi_task.done():
                raise RuntimeError("market-data task exited during startup")
            if poly.connected and kalshi.initialized_count == len(APPROVALS): break
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError("U3 K/P market-data startup timeout")

        pexec = HardenedPolymarketExecutor(); kexec = HardenedKalshiExecutor()
        try:
            for approval in APPROVALS:
                pq = poly.quote(approval.polymarket_slug, max_age_ms=60_000)
                kq = await kalshi.quote(approval.kalshi_ticker, max_age_ms=60_000)
                if pq is None or kq is None:
                    raise RuntimeError("initial book unavailable for preflight")
                await asyncio.gather(
                    pexec.preflight(_leg("polymarket", approval.polymarket_slug, "OVER", pq.yes_ask)),
                    kexec.preflight(_leg("kalshi", approval.kalshi_ticker, "OVER", kq.yes_ask)),
                )
        finally:
            try: pexec.close()
            except Exception: pass
            try: await kexec.close()
            except Exception: pass

        logger.info("KP_U3_PREFLIGHT_OK %s", json.dumps({
            "live_rules_verified": 3, "authenticated_venue_preflights": 3,
            "zero_position_required": True, "q_max": 1, "armed": armed,
            "auto_live": auto_live, "order_entry": bool(armed and not preflight_only),
        }, separators=(",", ":")))
        if preflight_only or not armed:
            return 0

        assert run_id
        arm_token = Path(f"/tmp/.kp-u3-arm-{run_id}")
        if not auto_live:
            assert arm_deadline is not None
            arm_token.write_text(str(arm_deadline), encoding="utf-8")

        seen: dict[str, Seen] = {}
        rejects: Counter[str] = Counter()
        best_profit: D | None = None
        best_snapshot: dict[str, Any] | None = None
        last_heartbeat = time.monotonic()
        by_poly = {a.polymarket_slug: a for a in APPROVALS}
        by_kalshi = {a.kalshi_ticker: a for a in APPROVALS}

        while auto_live or (arm_deadline is not None and int(time.time()) < arm_deadline):
            if auto_live:
                trigger = await _next_update(poly, kalshi)
            else:
                assert arm_deadline is not None
                trigger = await asyncio.wait_for(
                    _next_update(poly, kalshi), timeout=max(0.25, arm_deadline - time.time())
                )
            approval = by_poly.get(trigger.market_id) if trigger.venue == "polymarket" else by_kalshi.get(trigger.market_id)
            if approval is None:
                continue
            plan, reason, metrics = await _evaluate_plan(approval, poly, kalshi)
            rejects[reason] += 1
            if "after_fee_profit" in metrics:
                observed_profit = D(metrics["after_fee_profit"])
                if best_profit is None or observed_profit > best_profit:
                    best_profit = observed_profit
                    best_snapshot = {
                        "settlement_key": approval.settlement_key,
                        "reason": reason,
                        **metrics,
                    }
            if plan is None:
                seen.pop(approval.settlement_key, None)
            else:
                now_ms = int(time.time() * 1000); fp = _fp(plan); prior = seen.get(approval.settlement_key)
                if prior is None or prior.fingerprint != fp:
                    seen[approval.settlement_key] = Seen(now_ms, fp)
                elif now_ms - prior.first_ms >= PERSIST_MS:
                    persistence_ms = now_ms - prior.first_ms
                    logger.info("KP_U3_QUALIFIED %s", json.dumps({
                        "mode": "LIVE_PROBE", "auto_live": auto_live, "run_id": run_id,
                        "persistence_ms": persistence_ms, "plan": plan.as_dict(),
                    }, separators=(",", ":"), default=str))

                    async def fresh() -> KPExecutionPlan:
                        async with httpx.AsyncClient(timeout=8.0) as http:
                            await _validate_live_rules(http, approval)
                        latest = await _plan(approval, poly, kalshi)
                        if latest is None:
                            raise PermissionError("fresh U3 lock disappeared")
                        return latest

                    if auto_live:
                        arm_token.write_text(str(int(time.time()) + 30), encoding="utf-8")
                        logger.info("KP_U3_AUTO_ARM_MINTED run_id=%s ttl_seconds=30 order_entry=true", run_id)
                    result = await execute_after_fresh_revalidation(
                        plan, fresh, arm_token=arm_token, live_requested=True,
                    )
                    result["mode"] = "LIVE_PROBE"; result["run_id"] = run_id; result["auto_live"] = auto_live
                    logger.info("KP_U3_LIVE_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
                    return 70 if result.get("hard_halt_required") else 0

            if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                logger.info("KP_U3_WATCH_HEARTBEAT %s", json.dumps({
                    "auto_live": auto_live,
                    "evaluations": sum(rejects.values()),
                    "reasons": dict(rejects),
                    "best_after_fee_profit": None if best_profit is None else str(best_profit),
                    "best_snapshot": best_snapshot,
                    "order_entry": bool(armed and not preflight_only),
                }, separators=(",", ":"), default=str))
                last_heartbeat = time.monotonic()

        try: arm_token.unlink()
        except FileNotFoundError: pass
        logger.info("KP_U3_ARM_EXPIRED_NO_TRADE run_id=%s order_entry=false", run_id)
        return 0
    finally:
        poly_task.cancel(); kalshi_task.cancel(); await kalshi.close()
        try: pclient.close()
        except Exception: pass
        await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
