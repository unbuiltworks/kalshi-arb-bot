"""Automatic q=1 Kalshi/Polymarket MLB prop rule-risk executor.

This lane is deliberately NOT guaranteed arbitrage. It automates the strict live
K/P prop signals that were previously human-action alerts after the player
participation gate has resolved, while preserving the known lifecycle-rule risk.

Safety:
- exact strict candidate identity (game/player/stat/threshold) from existing adapters;
- participation must be proven by official MLB live evidence;
- q=1 only, prediction markets only;
- current Polymarket US 6% theta taker fee + conservative Kalshi fee upper bound;
- >=3c modeled after-fee minimum;
- two direct REST revalidations separated by >=50ms;
- each direct request <=100ms and cross-venue completion skew <=50ms;
- exact q=1 depth on both legs;
- durable shared-sports capital reservation before mutation;
- thinner displayed leg is submitted FOK first; second leg is revalidated after
  the first fill and sent FOK only if >=3c still remains;
- any ambiguous/one-leg outcome hard-stops and attempts bounded flattening;
- no sportsbook routing.

The service can run continuously while disarmed. Live mutation additionally
requires ENABLE_MLB_KP_RULE_RISK_LIVE=YES.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
import json
import logging
import os
import time
from typing import Any, Mapping
import uuid

import httpx
from polymarket_us import PolymarketUS

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict
from src.gemini_kalshi_probe_transport import KalshiV2Executor

base._fetch_poly_mlb = _fetch_poly_mlb_search

logger = logging.getLogger("kalshi_arb.mlb_kp_rule_risk_live_q1")
D = Decimal
ONE = D("1")
CENT = D("0.01")
POLY_TAKER_THETA = D("0.06")
MIN_PROFIT = D(os.getenv("MLB_KP_RULE_RISK_MIN_PROFIT_USD", "0.03"))
MAX_RTT_MS = int(os.getenv("MLB_KP_RULE_RISK_MAX_RTT_MS", "100"))
MAX_SKEW_MS = int(os.getenv("MLB_KP_RULE_RISK_MAX_SKEW_MS", "50"))
PERSIST_MS = int(os.getenv("MLB_KP_RULE_RISK_PERSIST_MS", "50"))
CUMULATIVE_BUDGET = D(os.getenv("SPORTS_LIVE_CUMULATIVE_BUDGET_USD", "20"))
LIVE_ENV = "ENABLE_MLB_KP_RULE_RISK_LIVE"
STRATEGY = "sports_kp_rule_risk_q1"
COMMAND_CENTER = os.getenv("ARB_COMMAND_CENTER_URL", "https://arb-command-center-production.up.railway.app").rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
POLY_BASE = "https://gateway.polymarket.us"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class DirectPlan:
    candidate: base.StrictCandidate
    direction: str
    poly_outcome: str
    poly_price: D
    poly_depth: D
    kalshi_outcome: str
    kalshi_price: D
    kalshi_depth: D
    poly_fee: D
    kalshi_fee: D
    all_in_cost: D
    locked_profit: D
    poly_rtt_ms: float
    kalshi_rtt_ms: float
    completion_skew_ms: float
    proof: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["candidate"] = {
            "family": self.candidate.family,
            "poly_slug": self.candidate.poly_slug,
            "kalshi_ticker": self.candidate.kalshi_ticker,
            "player": self.candidate.player,
            "threshold": str(self.candidate.threshold),
            "game_date": self.candidate.game_date,
        }
        for key in ("poly_price", "poly_depth", "kalshi_price", "kalshi_depth", "poly_fee", "kalshi_fee", "all_in_cost", "locked_profit"):
            row[key] = str(row[key])
        return row


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def live_enabled() -> bool:
    return os.getenv(LIVE_ENV, "NO").strip().upper() == "YES"


def poly_taker_fee(price: D, quantity: int = 1) -> D:
    raw = POLY_TAKER_THETA * D(quantity) * price * (ONE - price)
    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)


def score_q1(
    candidate: base.StrictCandidate,
    pbook: base.TopBook,
    kbook: base.TopBook,
    *,
    kalshi_fee_multiplier: D,
    poly_rtt_ms: float = 0.0,
    kalshi_rtt_ms: float = 0.0,
    completion_skew_ms: float = 0.0,
    proof: dict[str, Any] | None = None,
) -> DirectPlan | None:
    options = (
        ("POLY_YES_KALSHI_NO", "YES", pbook.yes_ask, pbook.yes_depth, "NO", kbook.no_ask, kbook.no_depth),
        ("KALSHI_YES_POLY_NO", "NO", pbook.no_ask, pbook.no_depth, "YES", kbook.yes_ask, kbook.yes_depth),
    )
    best: DirectPlan | None = None
    for direction, poutcome, ppx, pdepth, koutcome, kpx, kdepth in options:
        if pdepth < ONE or kdepth < ONE:
            continue
        pfee = poly_taker_fee(ppx)
        kfee = base.conservative_kalshi_fee(1, kpx, kalshi_fee_multiplier)
        cost = ppx + kpx + pfee + kfee
        locked = ONE - cost
        row = DirectPlan(
            candidate=candidate,
            direction=direction,
            poly_outcome=poutcome,
            poly_price=ppx,
            poly_depth=pdepth,
            kalshi_outcome=koutcome,
            kalshi_price=kpx,
            kalshi_depth=kdepth,
            poly_fee=pfee,
            kalshi_fee=kfee,
            all_in_cost=cost,
            locked_profit=locked,
            poly_rtt_ms=poly_rtt_ms,
            kalshi_rtt_ms=kalshi_rtt_ms,
            completion_skew_ms=completion_skew_ms,
            proof=proof or {},
        )
        if best is None or row.locked_profit > best.locked_profit:
            best = row
    return best


async def _fetch_direct(http: httpx.AsyncClient, candidate: base.StrictCandidate, multiplier: D, proof: dict[str, Any]) -> DirectPlan | None:
    async def get(url: str, *, params: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, float, float]:
        started = time.perf_counter()
        try:
            response = await http.get(url, params=params)
            ended = time.perf_counter()
            if response.status_code != 200:
                return None, (ended - started) * 1000.0, ended
            return response.json(), (ended - started) * 1000.0, ended
        except Exception:
            ended = time.perf_counter()
            return None, (ended - started) * 1000.0, ended

    p_task = asyncio.create_task(get(f"{POLY_BASE}/v1/markets/{candidate.poly_slug}/book"))
    k_task = asyncio.create_task(get(f"{KALSHI_BASE}/markets/{candidate.kalshi_ticker}/orderbook", params={"depth": 1}))
    (pbody, prtt, pdone), (kbody, krtt, kdone) = await asyncio.gather(p_task, k_task)
    if pbody is None or kbody is None:
        return None
    skew = abs(pdone - kdone) * 1000.0
    if prtt > MAX_RTT_MS or krtt > MAX_RTT_MS or skew > MAX_SKEW_MS:
        return None
    pbook = base.poly_top_book(pbody)
    kbook = base.kalshi_top_book(kbody)
    if pbook is None or kbook is None:
        return None
    return score_q1(
        candidate, pbook, kbook,
        kalshi_fee_multiplier=multiplier,
        poly_rtt_ms=prtt,
        kalshi_rtt_ms=krtt,
        completion_skew_ms=skew,
        proof=proof,
    )


async def persistent_plan(http: httpx.AsyncClient, candidate: base.StrictCandidate, multiplier: D, proof: dict[str, Any]) -> DirectPlan | None:
    first = await _fetch_direct(http, candidate, multiplier, proof)
    if first is None or first.locked_profit < MIN_PROFIT:
        return None
    await asyncio.sleep(max(0, PERSIST_MS) / 1000.0)
    second = await _fetch_direct(http, candidate, multiplier, proof)
    if second is None or second.direction != first.direction or second.locked_profit < MIN_PROFIT:
        return None
    return second


async def _trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [row for row in body if isinstance(row, dict)]


def _shared_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefixes = ("sports_gk_", "sports_final_lag_", "sports_kp_rule_risk_")
    return [
        row for row in rows
        if str(row.get("mode") or "") in {"LIVE", "LIVE_PROBE"}
        and str(row.get("strategy") or "").startswith(prefixes)
    ]


def _budget_used(rows: list[dict[str, Any]]) -> D:
    total = D("0")
    for row in _shared_rows(rows):
        actual = D(str(row.get("capital_filled") or "0"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = D(str(metadata.get("reserved_usd") or "0"))
        if str(row.get("status") or "").upper() == "CAPITAL_RELEASED":
            reserved = D("0")
        total += max(actual, reserved)
    return total


def _unresolved(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal = {"REALIZED", "TRADE_REALIZED", "SETTLED", "CAPITAL_RELEASED", "SETTLED_COMPATIBLE"}
    for row in _shared_rows(rows):
        if str(row.get("status") or "").upper() in terminal:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if D(str(row.get("capital_filled") or "0")) > 0 or D(str(metadata.get("reserved_usd") or "0")) > 0:
            return row
    return None


async def _post_event(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; live execution denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(plan: DirectPlan, trade_id: str) -> None:
    if plan.all_in_cost <= 0 or plan.all_in_cost > ONE:
        raise RuntimeError("q1 paired capital out of bounds")
    rows = await _trades()
    blocked = _unresolved(rows)
    if blocked is not None:
        raise RuntimeError(f"unresolved sports live trade blocks execution: {blocked.get('trade_id')}")
    used = _budget_used(rows)
    if used + plan.all_in_cost > CUMULATIVE_BUDGET:
        raise RuntimeError(f"shared sports budget exhausted: used={used} requested={plan.all_in_cost} ceiling={CUMULATIVE_BUDGET}")
    await _post_event({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{plan.candidate.family}|{plan.candidate.player}|{plan.candidate.threshold}",
        "asset": plan.candidate.player,
        "venues": ["polymarket", "kalshi"],
        "side": plan.direction,
        "status": "CAPITAL_RESERVED",
        "amount": str(plan.all_in_cost),
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": str(plan.all_in_cost),
            "cumulative_budget_usd": str(CUMULATIVE_BUDGET),
            "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
            "known_rule_differences": ["official_source", "interruption", "cancellation", "stat_corrections"],
            "plan": plan.as_dict(),
        },
    })


async def release(plan: DirectPlan, trade_id: str, reason: str) -> None:
    await _post_event({
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{plan.candidate.family}|{plan.candidate.player}|{plan.candidate.threshold}",
        "asset": plan.candidate.player,
        "venues": ["polymarket", "kalshi"],
        "side": plan.direction,
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {"reserved_usd": "0", "reason": reason, "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB"},
    })


class PolyExecutor:
    def __init__(self) -> None:
        self.client = PolymarketUS(
            key_id=os.environ["POLYMARKET_KEY_ID"],
            secret_key=os.environ["POLYMARKET_SECRET_KEY"],
            timeout=5.0,
        )

    def close(self) -> None:
        self.client.close()

    async def position(self, slug: str) -> D:
        body = await asyncio.to_thread(self.client.portfolio.positions, {"market": slug, "limit": 10})
        positions = body.get("positions") or {}
        row = positions.get(slug) if isinstance(positions, Mapping) else None
        if row is None and isinstance(positions, list):
            row = next((x for x in positions if str(x.get("marketSlug") or x.get("market") or "") == slug), {})
        return D(str((row or {}).get("netPosition") or "0"))

    async def cash(self) -> D:
        body = await asyncio.to_thread(self.client.account.balances)
        candidates: list[Any] = []
        if isinstance(body, dict):
            candidates += [body.get("available"), body.get("availableBalance"), body.get("buyingPower")]
            for row in body.get("balances") or []:
                if isinstance(row, dict) and str(row.get("currency") or "USD").upper() in {"USD", "USDC"}:
                    candidates += [row.get("available"), row.get("availableBalance"), row.get("balance")]
        values = [D(str(x)) for x in candidates if x is not None]
        if not values:
            raise RuntimeError("Polymarket available cash unavailable")
        return max(values)

    async def place_fok(self, slug: str, outcome: str, raw_outcome_price: D) -> dict[str, Any]:
        yes_space = raw_outcome_price if outcome == "YES" else ONE - raw_outcome_price
        payload = {
            "marketSlug": slug,
            "intent": "ORDER_INTENT_BUY_LONG" if outcome == "YES" else "ORDER_INTENT_BUY_SHORT",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(yes_space), "currency": "USD"},
            "quantity": 1,
            "tif": "TIME_IN_FORCE_FILL_OR_KILL",
            "participateDontInitiate": False,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": True,
            "maxBlockTime": "2",
        }
        try:
            body = await asyncio.to_thread(self.client.orders.create, payload)
            qty = D("0")
            for execution in body.get("executions") or []:
                order = execution.get("order") or {}
                qty = max(qty, D(str(order.get("cumQuantity") or "0")))
            return {"venue": "polymarket", "status": "FILLED" if qty == ONE else "CANCELED", "filled_quantity": str(qty), "order_id": body.get("id"), "payload": payload}
        except Exception as exc:
            try:
                pos = await self.position(slug)
                expected = ONE if outcome == "YES" else -ONE
                if pos == expected:
                    return {"venue": "polymarket", "status": "FILLED", "filled_quantity": "1", "order_id": None, "reason": "RECONCILED_POSITION", "payload": payload}
            except Exception:
                pass
            return {"venue": "polymarket", "status": "UNKNOWN", "filled_quantity": "0", "order_id": None, "reason": f"{type(exc).__name__}:{str(exc)[:160]}", "payload": payload}

    async def flatten(self, slug: str, raw_outcome_price: D) -> dict[str, Any]:
        try:
            await asyncio.to_thread(self.client.orders.close_position, {
                "marketSlug": slug,
                "slippageTolerance": {"currentPrice": {"value": str(raw_outcome_price), "currency": "USD"}, "ticks": 20},
            })
            for _ in range(20):
                if await self.position(slug) == 0:
                    return {"venue": "polymarket", "status": "FLATTENED"}
                await asyncio.sleep(0.1)
            return {"venue": "polymarket", "status": "UNKNOWN", "reason": "POSITION_NOT_ZERO_AFTER_FLATTEN"}
        except Exception as exc:
            return {"venue": "polymarket", "status": "UNKNOWN", "reason": f"{type(exc).__name__}:{str(exc)[:160]}"}


def kalshi_payload(plan: DirectPlan) -> dict[str, Any]:
    outcome = plan.kalshi_outcome
    raw = plan.kalshi_price
    return {
        "ticker": plan.candidate.kalshi_ticker,
        "client_order_id": f"kp-risk-{uuid.uuid4().hex[:20]}-k",
        "side": "bid" if outcome == "YES" else "ask",
        "count": "1.00",
        "price": str(raw if outcome == "YES" else ONE - raw),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


async def _balances(poly: PolyExecutor, kalshi: KalshiV2Executor) -> dict[str, D]:
    p, response = await asyncio.gather(poly.cash(), kalshi.client.get("/portfolio/balance"))
    response.raise_for_status()
    k = D(str(response.json().get("balance") or "0")) / D("100")
    if p < D("1") or k < D("1"):
        raise RuntimeError(f"insufficient venue cash polymarket={p} kalshi={k}")
    return {"polymarket": p, "kalshi": k}


async def _submit_first(plan: DirectPlan, first: str, poly: PolyExecutor, kalshi: KalshiV2Executor) -> tuple[dict[str, Any], dict[str, Any]]:
    kpayload = kalshi_payload(plan)
    if first == "polymarket":
        return await poly.place_fok(plan.candidate.poly_slug, plan.poly_outcome, plan.poly_price), kpayload
    result = await kalshi.place(kpayload)
    return {"venue": "kalshi", "status": result.status, "filled_quantity": result.filled_quantity, "reason": result.reason, "payload": kpayload}, kpayload


async def _submit_second(plan: DirectPlan, second: str, poly: PolyExecutor, kalshi: KalshiV2Executor, kpayload: dict[str, Any]) -> dict[str, Any]:
    if second == "polymarket":
        return await poly.place_fok(plan.candidate.poly_slug, plan.poly_outcome, plan.poly_price)
    result = await kalshi.place(kpayload)
    return {"venue": "kalshi", "status": result.status, "filled_quantity": result.filled_quantity, "reason": result.reason, "payload": kpayload}


async def _flatten_first(plan: DirectPlan, first: str, result: dict[str, Any], poly: PolyExecutor, kalshi: KalshiV2Executor, kpayload: dict[str, Any]) -> dict[str, Any]:
    if first == "polymarket":
        return await poly.flatten(plan.candidate.poly_slug, plan.poly_price)
    flattened = await kalshi.emergency_flatten(kpayload)
    return {"venue": "kalshi", "status": flattened.status, "filled_quantity": flattened.filled_quantity, "reason": flattened.reason}


async def execute(plan: DirectPlan, http: httpx.AsyncClient, multiplier: D) -> tuple[dict[str, Any], bool]:
    trade_id = _safe_id(f"sports-kp-risk-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    await reserve(plan, trade_id)
    poly = PolyExecutor()
    kalshi = KalshiV2Executor()
    hard_halt = False
    try:
        balances = await _balances(poly, kalshi)
        if await poly.position(plan.candidate.poly_slug) != 0:
            await release(plan, trade_id, "preexisting_polymarket_position")
            return {"trade_id": trade_id, "outcome": "PREEXISTING_POSITION_BLOCK"}, False

        first = "polymarket" if plan.poly_depth <= plan.kalshi_depth else "kalshi"
        second = "kalshi" if first == "polymarket" else "polymarket"
        first_result, kpayload = await _submit_first(plan, first, poly, kalshi)
        if first_result.get("status") == "UNKNOWN":
            hard_halt = True
            return {"trade_id": trade_id, "outcome": "AMBIGUOUS_FIRST_LEG", "first": first_result, "balances": {k: str(v) for k, v in balances.items()}}, True
        if D(str(first_result.get("filled_quantity") or "0")) != ONE:
            await release(plan, trade_id, "first_leg_no_fill")
            return {"trade_id": trade_id, "outcome": "FIRST_LEG_NO_FILL", "first": first_result}, False

        # Revalidate the opposite hedge only after the first leg is confirmed. Use
        # the original first-leg limit price as the conservative worst-case fill.
        fresh = await _fetch_direct(http, plan.candidate, multiplier, plan.proof)
        if fresh is None or fresh.direction != plan.direction or fresh.locked_profit < MIN_PROFIT:
            flatten = await _flatten_first(plan, first, first_result, poly, kalshi, kpayload)
            hard_halt = str(flatten.get("status") or "").upper() not in {"FLATTENED", "FILLED"}
            return {"trade_id": trade_id, "outcome": "HEDGE_EDGE_DISAPPEARED", "first": first_result, "flatten": flatten}, hard_halt

        # Freeze the newest hedge price into the second order while retaining the
        # already-filled first-leg limit as a conservative cost bound.
        if second == "polymarket":
            plan2 = DirectPlan(**{**plan.__dict__, "poly_price": fresh.poly_price, "poly_depth": fresh.poly_depth})
        else:
            plan2 = DirectPlan(**{**plan.__dict__, "kalshi_price": fresh.kalshi_price, "kalshi_depth": fresh.kalshi_depth})
            kpayload = kalshi_payload(plan2)
        second_result = await _submit_second(plan2, second, poly, kalshi, kpayload)
        if second_result.get("status") == "UNKNOWN" or D(str(second_result.get("filled_quantity") or "0")) != ONE:
            flatten = await _flatten_first(plan, first, first_result, poly, kalshi, kpayload)
            hard_halt = True
            return {"trade_id": trade_id, "outcome": "SECOND_LEG_NOT_CONFIRMED", "first": first_result, "second": second_result, "flatten": flatten}, True

        report = {
            "trade_id": trade_id,
            "outcome": "BOTH_FILLED",
            "first_venue": first,
            "second_venue": second,
            "first": first_result,
            "second": second_result,
            "plan": plan2.as_dict(),
            "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
            "balances_preflight": {k: str(v) for k, v in balances.items()},
        }
        await _post_event({
            "event_id": _safe_id(f"{trade_id}:execution-result"),
            "trade_id": trade_id,
            "event_type": "EXECUTION_RESULT",
            "event_ts": _now(),
            "strategy": STRATEGY,
            "mode": "LIVE_PROBE",
            "market": f"{plan.candidate.family}|{plan.candidate.player}|{plan.candidate.threshold}",
            "asset": plan.candidate.player,
            "venues": ["polymarket", "kalshi"],
            "side": plan.direction,
            "status": "FILLED_NOT_SETTLED",
            "amount": str(plan2.all_in_cost),
            "legs_required": 2,
            "legs_filled": 2,
            "metadata": {**report, "reserved_usd": str(plan2.all_in_cost)},
        })
        return report, False
    finally:
        poly.close()
        await kalshi.close()


async def _notify(message: str, *, title: str = "Sports K/P auto") -> None:
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    if not user or not token:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post("https://api.pushover.net/1/messages.json", data={"token": token, "user": user, "title": title, "message": message})
    except Exception:
        return


async def run() -> int:
    logger.info("MLB_KP_RULE_RISK_START live=%s q=1 min_profit=%s budget=%s rtt_ms=%s skew_ms=%s persist_ms=%s", live_enabled(), MIN_PROFIT, CUMULATIVE_BUDGET, MAX_RTT_MS, MAX_SKEW_MS, PERSIST_MS)
    async with httpx.AsyncClient(timeout=8.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(base._fetch_poly_mlb(http), base._fetch_kalshi_events(http))
        candidates = base.discover_strict_candidates(poly_markets, kalshi_events)
        series_meta: dict[str, D] = {}
        for series in sorted(set(base.FAMILY_SERIES.values())):
            body, error = await v3._fetch_optional(http, f"{KALSHI_BASE}/series/{series}")
            if body is not None and error is None:
                meta = body.get("series") or {}
                mult = base._d(meta.get("fee_multiplier"))
                if mult is not None:
                    series_meta[series] = mult
        logger.info("MLB_KP_RULE_RISK_DISCOVERY candidates=%d series_meta=%d", len(candidates), len(series_meta))

        while True:
            games = await fetch_game_boxscores(http, {c.game_date for c in candidates})
            cleared: list[tuple[base.StrictCandidate, dict[str, Any]]] = []
            for c in candidates:
                proof = participation_proof(family=c.family, player_name=c.player, poly_slug=c.poly_slug, games=games)
                assessment = base.assess_rule_equivalence(c.family, proof)
                if assessment.settlement_equivalent_now:
                    cleared.append((c, proof_dict(proof)))
            logger.info("MLB_KP_RULE_RISK_CYCLE strict=%d participation_proven=%d live=%s", len(candidates), len(cleared), live_enabled())

            # Trigger from Polymarket-side availability first, then touch only the
            # exact Kalshi ticker for candidates whose participation is already proven.
            for candidate, proof in cleared:
                multiplier = series_meta.get(base.FAMILY_SERIES[candidate.family])
                if multiplier is None:
                    continue
                plan = await persistent_plan(http, candidate, multiplier, proof)
                if plan is None:
                    await asyncio.sleep(0.01)
                    continue
                logger.warning("MLB_KP_RULE_RISK_EXECUTABLE %s", json.dumps(plan.as_dict(), separators=(",", ":"), default=str))
                if not live_enabled():
                    continue
                try:
                    report, hard_halt = await execute(plan, http, multiplier)
                except Exception as exc:
                    logger.exception("MLB_KP_RULE_RISK_EXECUTION_ERROR candidate=%s", candidate.key)
                    await _notify(f"AUTO K/P ERROR\n{candidate.player} {candidate.threshold} {candidate.family}\n{type(exc).__name__}: {str(exc)[:200]}", title="Sports K/P auto HALT")
                    return 70
                logger.warning("MLB_KP_RULE_RISK_EXECUTION_RESULT %s", json.dumps(report, separators=(",", ":"), default=str))
                if report.get("outcome") == "BOTH_FILLED":
                    p = report["plan"]
                    await _notify(
                        f"AUTO K/P q=1 FILLED\n{candidate.player} {candidate.threshold} {candidate.family}\n"
                        f"BUY Polymarket {plan.poly_outcome} @ {p['poly_price']}\n"
                        f"BUY Kalshi {plan.kalshi_outcome} @ {p['kalshi_price']}\n"
                        f"Modeled after-fee lock: ${p['locked_profit']}\n"
                        "RULE RISK — not guaranteed until settlement",
                        title="Sports K/P auto FILLED",
                    )
                    return 0
                if hard_halt:
                    await _notify(f"AUTO K/P HARD HALT\n{candidate.player}\n{report.get('outcome')}", title="Sports K/P auto HALT")
                    return 70
            await asyncio.sleep(1.0)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
