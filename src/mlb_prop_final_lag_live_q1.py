"""Bounded automatic q=1 MLB final-stat settlement-lag executor.

This is deliberately NOT classified as arbitrage. It consumes only the existing
FINAL_STAT_STABLE_RESEARCH_ONLY signal from :mod:`mlb_prop_decided_shadow`, then
revalidates the exact single-venue winner book immediately before sending a q=1
FOK order.

Routing policy:
* Kalshi / Polymarket are eligible for automatic execution here.
* FanDuel / sportsbooks are never routed to an automatic transport.
* Gemini final-lag execution is handled by the separate G/K hits lane because its
  market identity/rule audit differs from the strict K/P scanner.

Safety properties:
* official MLB state must already be Final and unchanged for >=60s / >=3 observations;
* fresh exact q=1 depth is re-fetched immediately before mutation;
* direct revalidation HTTP RTT must be <=100ms by default;
* conservative after-fee known-winner profit must be >=10c by default;
* q=1 FOK only, <=$1 capital;
* one unresolved final-lag fill at a time across the shared ledger;
* capital is durably reserved before order submission;
* the existing $5 sports live-probe cumulative ceiling is shared with sports_gk_*;
* any ambiguous submission hard-halts this process; it is never retried blindly;
* fills remain FILLED_NOT_SETTLED until venue settlement/reconciliation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any
import uuid

import httpx

from src import mlb_prop_decided_shadow as decided
from src import strict_mlb_prop_snapshot as base
from src.sports_kgp_micro_guard import MicroLeg
from src.sports_kgp_micro_transport import (
    KalshiV2Executor,
    PolymarketMicroExecutor,
    VenueResult,
    kalshi_payload,
    polymarket_payload,
)
from src.sports_pushover_alerts import SportsPushoverAlerts
from src.strict_mlb_prop_adapters import FAMILY_SERIES

logger = logging.getLogger("kalshi_arb.mlb_prop_final_lag_live_q1")
D = Decimal
ONE = D("1")

LIVE_ENV = "ENABLE_MLB_FINAL_LAG_LIVE"
STRATEGY = "sports_final_lag_kp_q1"
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
CUMULATIVE_BUDGET = D(os.getenv("SPORTS_LIVE_CUMULATIVE_BUDGET_USD", "5"))
MIN_PROFIT = D(os.getenv("FINAL_LAG_MIN_PROFIT_USD", "0.10"))
MAX_REVALIDATION_MS = int(os.getenv("FINAL_LAG_MAX_REVALIDATION_MS", "100"))

AUTO_VENUES = frozenset({"kalshi", "polymarket"})
MANUAL_ONLY_VENUES = frozenset({"fanduel", "sportsbook"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def live_enabled() -> bool:
    return os.getenv(LIVE_ENV, "NO").strip().upper() == "YES"


async def _trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [row for row in body if isinstance(row, dict)]


def _shared_sports_live_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        strategy = str(row.get("strategy") or "")
        if strategy.startswith("sports_gk_") or strategy.startswith("sports_final_lag_"):
            out.append(row)
    return out


def _budget_used(rows: list[dict[str, Any]]) -> D:
    total = D("0")
    for row in _shared_sports_live_rows(rows):
        actual = _d(row.get("capital_filled"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = _d(metadata.get("reserved_usd"))
        if str(row.get("status") or "") == "CAPITAL_RELEASED":
            reserved = D("0")
        total += max(actual, reserved)
    return total


def _unresolved_final_lag(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal = {"REALIZED", "TRADE_REALIZED", "SETTLED", "CAPITAL_RELEASED"}
    for row in _shared_sports_live_rows(rows):
        if not str(row.get("strategy") or "").startswith("sports_final_lag_"):
            continue
        if str(row.get("status") or "").upper() in terminal:
            continue
        actual = _d(row.get("capital_filled"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = _d(metadata.get("reserved_usd"))
        if actual > 0 or reserved > 0:
            return row
    return None


async def _post_required(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; live settlement-lag execution denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def _reserve(*, trade_id: str, context: dict[str, Any], amount: D) -> None:
    if amount <= 0 or amount > ONE:
        raise RuntimeError(f"final-lag q1 capital out of bounds: {amount}")
    rows = await _trades()
    unresolved = _unresolved_final_lag(rows)
    if unresolved is not None:
        raise RuntimeError(
            "unresolved final-lag trade blocks new execution: "
            f"trade_id={unresolved.get('trade_id')} status={unresolved.get('status')}"
        )
    used = _budget_used(rows)
    if used + amount > CUMULATIVE_BUDGET:
        raise RuntimeError(
            f"shared sports live budget exhausted: used={used} requested={amount} ceiling={CUMULATIVE_BUDGET}"
        )
    await _post_required({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": context["settlement_key"],
        "asset": context["player"],
        "venues": [context["venue"]],
        "side": f"BUY {context['outcome']}",
        "status": "CAPITAL_RESERVED",
        "amount": str(amount),
        "legs_required": 1,
        "legs_filled": 0,
        "metadata": {
            **context,
            "reserved_usd": str(amount),
            "cumulative_budget_usd": str(CUMULATIVE_BUDGET),
            "classification": "LIVE_SETTLEMENT_LAG_RULE_RISK_NOT_ARBITRAGE",
        },
    })


async def _release(*, trade_id: str, context: dict[str, Any]) -> None:
    await _post_required({
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": context["settlement_key"],
        "asset": context["player"],
        "venues": [context["venue"]],
        "side": f"BUY {context['outcome']}",
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 1,
        "legs_filled": 0,
        "metadata": {**context, "reserved_usd": "0"},
    })


async def _fresh_edge(row: dict[str, Any], venue: str) -> tuple[dict[str, Any], float] | None:
    outcome = str(row.get("known_outcome") or "").upper()
    if outcome not in {"YES", "NO"} or venue not in AUTO_VENUES:
        return None
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=3.0) as http:
        if venue == "polymarket":
            response = await http.get(f"{base.POLY}/v1/markets/{row['poly_slug']}/book")
            response.raise_for_status()
            book = base.poly_top_book(response.json())
            if book is None:
                return None
            edge = decided._known_winner_edge(venue="polymarket", outcome=outcome, book=book)
        else:
            response = await http.get(
                f"{base.KALSHI}/markets/{row['kalshi_ticker']}/orderbook",
                params={"depth": 1},
            )
            response.raise_for_status()
            book = base.kalshi_top_book(response.json())
            if book is None:
                return None
            series = FAMILY_SERIES.get(str(row.get("family") or ""))
            if not series:
                return None
            body = await http.get(f"{base.KALSHI}/series/{series}")
            body.raise_for_status()
            meta = body.json().get("series") or {}
            multiplier = base._d(meta.get("fee_multiplier"))
            if multiplier is None:
                return None
            edge = decided._known_winner_edge(
                venue="kalshi",
                outcome=outcome,
                book=book,
                kalshi_multiplier=multiplier,
            )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if edge is None:
        return None
    return edge, elapsed_ms


def _leg(row: dict[str, Any], edge: dict[str, Any], now_ms: int) -> MicroLeg:
    venue = str(edge["venue"]).lower()
    market_id = str(row["poly_slug"] if venue == "polymarket" else row["kalshi_ticker"])
    outcome = str(edge["outcome"]).upper()
    return MicroLeg(
        venue=venue,
        market_id=market_id,
        normalized_outcome=outcome,
        native_outcome=outcome,
        raw_outcome_price=_d(edge["winner_ask"]),
        source_side="YES",
        received_ms=now_ms,
    )


async def _submit(leg: MicroLeg) -> VenueResult:
    if leg.venue == "polymarket":
        executor = PolymarketMicroExecutor()
        try:
            await executor.preflight(leg)
            return await executor.place_fok(leg)
        finally:
            executor.close()
    if leg.venue == "kalshi":
        executor = KalshiV2Executor()
        try:
            result = await executor.place(kalshi_payload(leg))
            return VenueResult(
                venue="kalshi",
                order_id=str(result.client_order_id or "") or None,
                status=str(result.status).upper(),
                filled_quantity=_d(result.filled_quantity),
                average_price=_d(result.average_fill_price) if result.average_fill_price is not None else None,
                reason=str(result.reason) if result.reason else None,
                reconciled=bool(result.reconciled),
            )
        finally:
            await executor.close()
    raise PermissionError("automatic final-lag transport is prediction-market-only")


class FinalLagLiveHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lock = asyncio.Lock()
        self.halted = False
        self.attempted: set[str] = set()
        self.alerts = SportsPushoverAlerts()

    async def _push(self, title: str, message: str) -> None:
        if not self.alerts.configured:
            return
        try:
            await self.alerts.send(title=title[:250], message=message[:1024])
        except Exception:
            logger.exception("FINAL_LAG_LIVE_PUSHOVER_ERROR")

    async def _handle(self, row: dict[str, Any]) -> None:
        if self.halted or not live_enabled():
            return
        if str(row.get("decision_state") or "") != "FINAL_STAT_STABLE_RESEARCH_ONLY":
            return
        outcome = str(row.get("known_outcome") or "").upper()
        if outcome not in {"YES", "NO"}:
            return
        key = str(row.get("family") or "") + "|" + str(row.get("poly_slug") or "") + "|" + str(row.get("kalshi_ticker") or "")
        if key in self.attempted:
            return

        original = [
            edge for edge in (row.get("known_winner_edges") or [])
            if isinstance(edge, dict) and str(edge.get("venue") or "").lower() in AUTO_VENUES
        ]
        original.sort(key=lambda edge: _d(edge.get("known_winner_profit")), reverse=True)
        if not original:
            return

        async with self.lock:
            if self.halted or key in self.attempted:
                return
            self.attempted.add(key)
            chosen: tuple[dict[str, Any], float] | None = None
            for prior in original:
                venue = str(prior.get("venue") or "").lower()
                try:
                    refreshed = await _fresh_edge(row, venue)
                except Exception as exc:
                    logger.warning("FINAL_LAG_REVALIDATION_ERROR venue=%s type=%s", venue, type(exc).__name__)
                    continue
                if refreshed is None:
                    continue
                edge, elapsed_ms = refreshed
                profit = _d(edge.get("known_winner_profit"))
                if elapsed_ms > MAX_REVALIDATION_MS:
                    logger.info("FINAL_LAG_SKIP stale_revalidation venue=%s elapsed_ms=%.3f", venue, elapsed_ms)
                    continue
                if _d(edge.get("displayed_depth")) < ONE:
                    continue
                if profit < MIN_PROFIT:
                    logger.info("FINAL_LAG_SKIP profit_below_floor venue=%s profit=%s floor=%s", venue, profit, MIN_PROFIT)
                    continue
                chosen = (edge, elapsed_ms)
                break
            if chosen is None:
                return

            edge, elapsed_ms = chosen
            venue = str(edge["venue"]).lower()
            if venue in MANUAL_ONLY_VENUES or venue not in AUTO_VENUES:
                raise PermissionError("FanDuel/sportsbook opportunities must remain manual")
            now_ms = int(time.time() * 1000)
            leg = _leg(row, edge, now_ms)
            all_in = _d(edge.get("all_in_cost"))
            expected_profit = _d(edge.get("known_winner_profit"))
            settlement_key = f"{row.get('family')}|{venue}|{leg.market_id}|{outcome}"
            trade_id = _safe_id(f"final-lag:{venue}:{uuid.uuid4().hex[:20]}")
            context = {
                "settlement_key": settlement_key,
                "player": str(row.get("player") or "Unknown player"),
                "family": str(row.get("family") or ""),
                "threshold": str(row.get("threshold") or ""),
                "mlb_game_pk": row.get("mlb_game_pk"),
                "official_statistic": row.get("official_statistic"),
                "outcome": outcome,
                "venue": venue,
                "market_id": leg.market_id,
                "winner_ask": str(edge.get("winner_ask")),
                "fee_upper_bound": str(edge.get("fee_upper_bound")),
                "all_in_cost": str(all_in),
                "modeled_profit": str(expected_profit),
                "revalidation_ms": round(elapsed_ms, 3),
                "evidence": "MLB_FINAL_STABLE_60S_3OBS",
                "fanduel_required": False,
            }

            try:
                await _reserve(trade_id=trade_id, context=context, amount=all_in)
            except Exception as exc:
                logger.warning("FINAL_LAG_RESERVATION_BLOCKED type=%s reason=%s", type(exc).__name__, str(exc)[:220])
                return

            await _post_required({
                "event_id": _safe_id(f"{trade_id}:submitted"),
                "trade_id": trade_id,
                "event_type": "ORDER_SUBMITTED",
                "event_ts": _now(),
                "strategy": STRATEGY,
                "mode": "LIVE_PROBE",
                "market": settlement_key,
                "asset": context["player"],
                "venues": [venue],
                "side": f"BUY {outcome}",
                "status": "SUBMITTED",
                "quantity": "1",
                "legs_required": 1,
                "legs_filled": 0,
                "opportunity_net_profit": str(expected_profit),
                "metadata": context,
            })

            try:
                result = await _submit(leg)
            except Exception as exc:
                logger.exception("FINAL_LAG_SUBMIT_EXCEPTION")
                self.halted = True
                await self._push(
                    "MLB AUTO FINAL-LAG — HARD HALT",
                    f"{context['player']}\n{venue.title()} {outcome}\nSubmission exception: {type(exc).__name__}\nNo retry. Check Railway/venue account.",
                )
                return

            logger.warning("FINAL_LAG_EXECUTION_RESULT %s", json.dumps({
                "trade_id": trade_id,
                "context": context,
                "result": result.as_dict(),
            }, separators=(",", ":"), default=str))

            if result.ambiguous:
                self.halted = True
                await _post_required({
                    "event_id": _safe_id(f"{trade_id}:execution-result"),
                    "trade_id": trade_id,
                    "event_type": "EXECUTION_RESULT",
                    "event_ts": _now(),
                    "strategy": STRATEGY,
                    "mode": "LIVE_PROBE",
                    "market": settlement_key,
                    "asset": context["player"],
                    "venues": [venue],
                    "side": f"BUY {outcome}",
                    "status": "AMBIGUOUS_SUBMISSION_HARD_HALT",
                    "quantity": "1",
                    "legs_required": 1,
                    "legs_filled": 0,
                    "metadata": {**context, "execution": result.as_dict()},
                })
                await self._push(
                    "MLB AUTO FINAL-LAG — AMBIGUOUS",
                    f"{context['player']}\n{venue.title()} {outcome}\nOrder state UNKNOWN. Bot halted; no retry. Reconcile venue account.",
                )
                return

            if not result.filled_one:
                await _post_required({
                    "event_id": _safe_id(f"{trade_id}:execution-result"),
                    "trade_id": trade_id,
                    "event_type": "EXECUTION_RESULT",
                    "event_ts": _now(),
                    "strategy": STRATEGY,
                    "mode": "LIVE_PROBE",
                    "market": settlement_key,
                    "asset": context["player"],
                    "venues": [venue],
                    "side": f"BUY {outcome}",
                    "status": "FOK_NOT_FILLED",
                    "quantity": "1",
                    "legs_required": 1,
                    "legs_filled": 0,
                    "metadata": {**context, "execution": result.as_dict()},
                })
                try:
                    await _release(trade_id=trade_id, context=context)
                except Exception:
                    logger.exception("FINAL_LAG_CAPITAL_RELEASE_FAILED")
                    self.halted = True
                return

            # A single-venue final-lag fill is exposure until the venue itself settles.
            # We intentionally stop taking additional final-lag trades until that fill
            # is reconciled by the authoritative ledger.
            self.halted = True
            contract_price = leg.raw_outcome_price
            if result.average_price is not None:
                if venue == "kalshi" and outcome == "NO":
                    contract_price = ONE - result.average_price
                elif venue == "polymarket" and outcome == "NO":
                    contract_price = ONE - result.average_price
                else:
                    contract_price = result.average_price
            await _post_required({
                "event_id": _safe_id(f"{trade_id}:fill:{venue}"),
                "trade_id": trade_id,
                "event_type": "FILL",
                "event_ts": _now(),
                "strategy": STRATEGY,
                "mode": "LIVE_PROBE",
                "market": settlement_key,
                "asset": context["player"],
                "venues": [venue],
                "side": f"BUY {outcome}",
                "status": "FILLED",
                "quantity": "1",
                "price": str(contract_price),
                "amount": str(contract_price),
                "fee": str(edge.get("fee_upper_bound")),
                "legs_required": 1,
                "legs_filled": 1,
                "metadata": {**context, "execution": result.as_dict()},
            })
            await _post_required({
                "event_id": _safe_id(f"{trade_id}:execution-result"),
                "trade_id": trade_id,
                "event_type": "EXECUTION_RESULT",
                "event_ts": _now(),
                "strategy": STRATEGY,
                "mode": "LIVE_PROBE",
                "market": settlement_key,
                "asset": context["player"],
                "venues": [venue],
                "side": f"BUY {outcome}",
                "status": "FILLED_NOT_SETTLED",
                "quantity": "1",
                "legs_required": 1,
                "legs_filled": 1,
                "opportunity_net_profit": str(expected_profit),
                "metadata": {**context, "execution": result.as_dict()},
            })
            await self._push(
                f"MLB AUTO FILLED — {context['player']}",
                "\n".join((
                    f"AUTO BUY: 1 {outcome} on {venue.title()}",
                    f"Market: {leg.market_id}",
                    f"Revalidated ask: ${leg.raw_outcome_price}",
                    f"Modeled profit if settlement unchanged: +${expected_profit}",
                    "FanDuel hedge: NONE",
                    "Status: FILLED_NOT_SETTLED",
                    "SETTLEMENT-LAG RULE RISK — NOT ARBITRAGE / NOT REALIZED YET",
                )),
            )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            prefix = "DECIDED_MLB_PROP_SIGNAL "
            if not message.startswith(prefix):
                return
            row = json.loads(message[len(prefix):])
            asyncio.get_running_loop().create_task(self._handle(row))
        except Exception:
            logger.exception("FINAL_LAG_LIVE_HANDLER_ERROR")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    handler = FinalLagLiveHandler()
    decided.logger.addHandler(handler)
    logger.warning(
        "MLB_FINAL_LAG_LIVE_READY armed=%s q=1 auto_venues=%s manual_only=fanduel min_profit=%s max_revalidation_ms=%s cumulative_budget=%s classification=LIVE_SETTLEMENT_LAG_RULE_RISK_NOT_ARBITRAGE",
        live_enabled(),
        ",".join(sorted(AUTO_VENUES)),
        MIN_PROFIT,
        MAX_REVALIDATION_MS,
        CUMULATIVE_BUDGET,
    )
    asyncio.run(decided.run_forever())


if __name__ == "__main__":
    main()
