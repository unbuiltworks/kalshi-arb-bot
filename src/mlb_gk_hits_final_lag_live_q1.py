"""Automatic q=1 MLB Hits final-stat settlement-lag execution on Gemini or Kalshi.

This is a rule-risk settlement-lag lane, NOT guaranteed arbitrage. Exact Gemini ↔
Kalshi contract identity is used as a certification anchor, but only one venue is
bought: whichever still offers the MLB-final known winner at a sufficient discount.

FanDuel/sportsbooks are intentionally absent from every order path.

Safety invariants:
* exact direct-orientation Gemini/Kalshi MLB Hits identity only;
* MLB Stats API reports Final, starter + >=1 PA, unchanged statistic >=60s / >=3 obs;
* q=1 FOK only; no resting order;
* >=10c conservative after-fee modeled profit by default;
* Gemini book <=100ms and survives 50ms revalidation, or Kalshi exact REST request
  start age/RTT <=100ms and survives a second 50ms exact revalidation;
* durable Command Center reservation before mutation, sharing the existing
  sports_gk_* cumulative $5 ceiling;
* at most one unresolved final-lag fill at a time across sports_gk_final_lag_*;
* unresolved/ambiguous submission hard-halts with no blind retry;
* a fill is FILLED_NOT_SETTLED until authoritative venue settlement reconciliation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any
import uuid

import httpx

from src.gemini_kalshi_execution_shadow import TopOfBook, gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_kalshi_mlb_prop_hot_shadow import GeminiPropBooks, discover_hot, kalshi_tob, orient_tob
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor, ExecutionResult
from src.gemini_prediction_auth import private_rest_request
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, _norm_name
from src.sports_pushover_alerts import SportsPushoverAlerts
from src.strict_mlb_prop_snapshot import KALSHI, kalshi_top_book

D = Decimal
ONE = D("1")
FAMILY = "baseball_player_hits"
STRATEGY = "sports_gk_final_lag_gk_hits_q1"
LIVE_ENV = "ENABLE_MLB_GK_FINAL_LAG_LIVE"
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
CUMULATIVE_BUDGET = D(os.getenv("SPORTS_LIVE_CUMULATIVE_BUDGET_USD", "5"))
MIN_PROFIT = D(os.getenv("GK_FINAL_LAG_MIN_PROFIT_USD", "0.10"))
MAX_AGE_MS = int(os.getenv("GK_FINAL_LAG_MAX_BOOK_AGE_MS", "100"))
PERSIST_MS = int(os.getenv("GK_FINAL_LAG_PERSIST_MS", "50"))
DISCOVERY_SECONDS = float(os.getenv("GK_FINAL_LAG_DISCOVERY_SECONDS", "180"))
MIN_STABLE_MS = 60_000
MIN_STABLE_OBSERVATIONS = 3
logger = logging.getLogger("kalshi_arb.mlb_gk_hits_final_lag_live_q1")


@dataclass
class StableState:
    fingerprint: str
    first_seen_ms: int
    last_seen_ms: int
    observations: int = 1


class StableTracker:
    def __init__(self) -> None:
        self.rows: dict[str, StableState] = {}

    def observe(self, key: str, fingerprint: str, now_ms: int) -> tuple[bool, dict[str, int]]:
        row = self.rows.get(key)
        if row is None or row.fingerprint != fingerprint:
            self.rows[key] = StableState(fingerprint, now_ms, now_ms)
            return False, {"observations": 1, "stable_for_ms": 0}
        row.observations += 1
        row.last_seen_ms = now_ms
        stable_for = max(0, row.last_seen_ms - row.first_seen_ms)
        return bool(row.observations >= MIN_STABLE_OBSERVATIONS and stable_for >= MIN_STABLE_MS), {
            "observations": row.observations,
            "stable_for_ms": stable_for,
        }


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def armed() -> bool:
    return bool(
        os.getenv(LIVE_ENV, "NO").strip().upper() == "YES"
        and os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() == "YES"
    )


def _game_date(game_slug: str) -> str:
    return game_slug[-10:]


def _game_teams(game_slug: str) -> tuple[str, str] | None:
    parts = game_slug.split("-")
    if len(parts) < 6 or parts[0] != "mlb":
        return None
    return tuple(sorted((parts[1].lower(), parts[2].lower())))


def _game_for_slug(game_slug: str, games: dict[tuple[str, tuple[str, str]], dict[str, Any]]) -> dict[str, Any] | None:
    teams = _game_teams(game_slug)
    if teams is None:
        return None
    return games.get((_game_date(game_slug), teams))


def _hits_for_player(game: dict[str, Any], player_name: str) -> int | None:
    target = _norm_name(player_name)
    box = game.get("boxscore") or {}
    for side in ("away", "home"):
        players = (((box.get("teams") or {}).get(side) or {}).get("players") or {})
        for row in players.values():
            person = row.get("person") or {}
            if _norm_name(person.get("fullName")) != target:
                continue
            try:
                return int((((row.get("stats") or {}).get("batting") or {}).get("hits")) or 0)
            except (TypeError, ValueError):
                return None
    return None


def _sports_live_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if str(row.get("mode") or "") in {"LIVE", "LIVE_PROBE"}
        and str(row.get("strategy") or "").startswith("sports_gk_")
    ]


def _budget_used(rows: list[dict[str, Any]]) -> D:
    total = D("0")
    for row in _sports_live_rows(rows):
        actual = _d(row.get("capital_filled"))
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = _d(meta.get("reserved_usd"))
        if str(row.get("status") or "") == "CAPITAL_RELEASED":
            reserved = D("0")
        total += max(actual, reserved)
    return total


def _unresolved_final_lag(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal = {"REALIZED", "TRADE_REALIZED", "SETTLED", "CAPITAL_RELEASED"}
    for row in _sports_live_rows(rows):
        if "final_lag" not in str(row.get("strategy") or ""):
            continue
        if str(row.get("status") or "").upper() in terminal:
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if _d(row.get("capital_filled")) > 0 or _d(meta.get("reserved_usd")) > 0:
            return row
    return None


async def _trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [row for row in body if isinstance(row, dict)]


async def _post_required(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; live final-lag execution denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def _reserve(*, trade_id: str, context: dict[str, Any], amount: D) -> None:
    if amount <= 0 or amount > ONE:
        raise RuntimeError(f"q1 final-lag capital out of bounds: {amount}")
    rows = await _trades()
    unresolved = _unresolved_final_lag(rows)
    if unresolved is not None:
        raise RuntimeError(
            f"unresolved final-lag exposure blocks new trade: {unresolved.get('trade_id')} {unresolved.get('status')}"
        )
    used = _budget_used(rows)
    if used + amount > CUMULATIVE_BUDGET:
        raise RuntimeError(
            f"sports cumulative budget exhausted: used={used} requested={amount} ceiling={CUMULATIVE_BUDGET}"
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
            "classification": "LIVE_SETTLEMENT_LAG_CORRECTION_RISK_NOT_ARBITRAGE",
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


def _edge(*, venue: str, market_id: str, book: TopOfBook, outcome: str, fee_multiplier: D | None = None) -> dict[str, Any] | None:
    if outcome == "YES":
        price, depth = D(book.yes_ask), D(book.yes_ask_size)
        yes_space = price
    else:
        price, depth = ONE - D(book.yes_bid), D(book.yes_bid_size)
        yes_space = D(book.yes_bid)
    if depth < ONE or not (D("0") < price < ONE):
        return None
    if venue == "gemini":
        fee = gemini_predictions_taker_fee(price, ONE)
    elif venue == "kalshi" and fee_multiplier is not None:
        fee = kalshi_quadratic_taker_fee(yes_space, ONE, fee_multiplier=fee_multiplier)
    else:
        return None
    cost = price + fee
    profit = ONE - cost
    if profit <= 0:
        return None
    return {
        "venue": venue,
        "market_id": market_id,
        "outcome": outcome,
        "winner_ask": str(price),
        "yes_space_price": str(yes_space),
        "displayed_depth": str(depth),
        "fee_upper_bound": str(fee),
        "all_in_cost": str(cost),
        "modeled_profit": str(profit),
        "book_received_ms": int(book.ts_ms),
    }


async def _kalshi_exact(ticker: str) -> tuple[TopOfBook | None, float]:
    request_start_ms = int(time.time() * 1000)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=2.0) as http:
        response = await http.get(f"{KALSHI}/markets/{ticker}/orderbook", params={"depth": 1})
    elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        return None, elapsed_ms
    book = kalshi_top_book(response.json())
    if book is None:
        return None, elapsed_ms
    return TopOfBook(
        venue="kalshi",
        market_id=ticker,
        ts_ms=request_start_ms,
        yes_bid=ONE - D(book.no_ask),
        yes_bid_size=D(book.no_depth),
        yes_ask=D(book.yes_ask),
        yes_ask_size=D(book.yes_depth),
    ), elapsed_ms


def _native_direct(row: Any) -> bool:
    return bool(
        row.gemini_side.side in {"YES", "CONTRACT"}
        and row.kalshi_side.side in {"YES", "CONTRACT"}
    )


def _gemini_payload(row: Any, edge: dict[str, Any], trade_id: str) -> dict[str, Any]:
    return {
        "symbol": row.gemini_side.venue_market_id,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(edge["winner_ask"]),
        "quantity": "1",
        "eventOutcome": str(edge["outcome"]),
        "clientOrderId": _safe_id(f"{trade_id}-g")[:64],
    }


def _kalshi_payload(row: Any, edge: dict[str, Any], trade_id: str) -> dict[str, Any]:
    outcome = str(edge["outcome"])
    return {
        "ticker": row.kalshi_side.venue_market_id,
        "client_order_id": _safe_id(f"{trade_id}-k")[:64],
        "side": "bid" if outcome == "YES" else "ask",
        "count": "1.00",
        "price": str(edge["yes_space_price"]),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


async def _venue_preflight(venue: str) -> None:
    if venue == "gemini":
        terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=4.0)
        terms.raise_for_status()
        if not bool(terms.json().get("hasAcceptedLatest")):
            raise RuntimeError("Gemini prediction terms not accepted")
        balances = await private_rest_request("POST", "/v1/balances", timeout=4.0)
        balances.raise_for_status()
        usd = D("0")
        for row in balances.json() if isinstance(balances.json(), list) else []:
            if str(row.get("currency") or "").upper() == "USD":
                usd = D(str(row.get("available") or "0"))
                break
        if usd < ONE:
            raise RuntimeError("Gemini available USD below $1")
        return
    if venue == "kalshi":
        executor = KalshiV2Executor()
        try:
            status = await executor.client.get("/exchange/status")
            status.raise_for_status()
            balance = await executor.client.get("/portfolio/balance")
            balance.raise_for_status()
            if D(str(balance.json().get("balance") or "0")) / D("100") < ONE:
                raise RuntimeError("Kalshi available balance below $1")
        finally:
            await executor.close()
        return
    raise PermissionError("FanDuel/sportsbook automatic transport forbidden")


async def _execute(row: Any, edge: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
    venue = str(edge["venue"])
    trade_id = context["trade_id"]
    if venue == "gemini":
        session = GeminiTradingSession()
        try:
            await session.connect()
            return await session.place_fok(_gemini_payload(row, edge, trade_id))
        finally:
            await session.close()
    if venue == "kalshi":
        executor = KalshiV2Executor()
        try:
            return await executor.place(_kalshi_payload(row, edge, trade_id))
        finally:
            await executor.close()
    raise PermissionError("automatic final-lag execution is prediction-market-only")


async def _push(alerts: SportsPushoverAlerts, title: str, message: str) -> None:
    if not alerts.configured:
        return
    try:
        await alerts.send(title=title[:250], message=message[:1024])
    except Exception:
        logger.exception("MLB_GK_FINAL_LAG_LIVE_PUSHOVER_ERROR")


async def run_forever() -> None:
    tracker = StableTracker()
    alerts = SportsPushoverAlerts()
    attempted: set[str] = set()
    hard_halt = False
    logger.warning(
        "MLB_GK_FINAL_LAG_LIVE_READY armed=%s q=1 family=hits auto_venues=gemini,kalshi manual_only=fanduel min_profit=%s max_age_ms=%s persist_ms=%s cumulative_budget=%s classification=LIVE_SETTLEMENT_LAG_CORRECTION_RISK_NOT_ARBITRAGE",
        armed(), MIN_PROFIT, MAX_AGE_MS, PERSIST_MS, CUMULATIVE_BUDGET,
    )

    while True:
        if hard_halt:
            await asyncio.sleep(30)
            continue
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                hot, _market_to_keys, diagnostics = await discover_hot(http)
            contracts = {
                key: row for key, row in hot.items()
                if row.family == FAMILY and _native_direct(row)
            }
        except Exception as exc:
            logger.warning("MLB_GK_FINAL_LAG_DISCOVERY_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(30)
            continue

        logger.info("MLB_GK_FINAL_LAG_DISCOVERY %s", json.dumps({
            **diagnostics,
            "eligible_direct_hit_keys": len(contracts),
            "armed": armed(),
            "read_only_scanner": True,
            "automatic_mutation_layer": armed(),
        }, separators=(",", ":"), default=str))
        if not contracts:
            await asyncio.sleep(min(60.0, DISCOVERY_SECONDS))
            continue

        symbols = sorted({row.gemini_side.venue_market_id for row in contracts.values()})
        dates = sorted({_game_date(row.game_slug) for row in contracts.values()})
        gemini_books = GeminiPropBooks(symbols)
        book_task = asyncio.create_task(gemini_books.run(), name="gk_final_lag_gemini_books")
        epoch_end = time.monotonic() + DISCOVERY_SECONDS
        try:
            while time.monotonic() < epoch_end and not hard_halt:
                cycle_started = time.monotonic()
                now_ms = int(time.time() * 1000)
                try:
                    async with httpx.AsyncClient(timeout=10.0) as http:
                        games = await fetch_game_boxscores(http, dates)
                except Exception as exc:
                    logger.warning("MLB_GK_FINAL_LAG_MLB_ERROR type=%s", type(exc).__name__)
                    await asyncio.sleep(2)
                    continue

                stable_count = 0
                positive = 0
                for key, row in contracts.items():
                    if hard_halt:
                        break
                    game = _game_for_slug(row.game_slug, games)
                    if game is None or str(game.get("status") or "").lower() != "final":
                        continue
                    proof = participation_proof(
                        family=FAMILY,
                        player_name=row.player,
                        poly_slug=row.game_slug,
                        games=games,
                    )
                    if not (proof.started and proof.plate_appearance_recorded and proof.source):
                        continue
                    hits = _hits_for_player(game, row.player)
                    if hits is None:
                        continue
                    threshold = D(str(row.gemini_side.identity.line))
                    outcome = "YES" if D(hits) >= threshold else "NO"
                    fingerprint = "|".join((
                        str(game.get("gamePk") or ""), "Final", _norm_name(row.player),
                        str(hits), str(threshold), outcome,
                    ))
                    stable, stability = tracker.observe(key, fingerprint, now_ms)
                    if not stable:
                        continue
                    stable_count += 1
                    execution_key = f"{key}|{outcome}"
                    if execution_key in attempted or not armed():
                        continue

                    edges: list[dict[str, Any]] = []
                    graw = gemini_books.quote(row.gemini_side.venue_market_id, max_age_ms=MAX_AGE_MS)
                    if graw is not None:
                        gedge = _edge(
                            venue="gemini",
                            market_id=row.gemini_side.venue_market_id,
                            book=orient_tob(graw, row.gemini_side.side),
                            outcome=outcome,
                        )
                        if gedge is not None and _d(gedge["modeled_profit"]) >= MIN_PROFIT:
                            edges.append(gedge)
                    try:
                        kraw, krtt = await _kalshi_exact(row.kalshi_side.venue_market_id)
                    except Exception:
                        kraw, krtt = None, 999999.0
                    if kraw is not None and krtt <= MAX_AGE_MS:
                        kedge = _edge(
                            venue="kalshi",
                            market_id=row.kalshi_side.venue_market_id,
                            book=orient_tob(kalshi_tob(kraw, row.kalshi_side.venue_market_id), row.kalshi_side.side),
                            outcome=outcome,
                            fee_multiplier=row.kalshi_fee_multiplier,
                        )
                        if kedge is not None and _d(kedge["modeled_profit"]) >= MIN_PROFIT:
                            kedge["revalidation_rtt_ms"] = round(krtt, 3)
                            edges.append(kedge)
                    if not edges:
                        continue
                    positive += 1
                    edges.sort(key=lambda x: _d(x["modeled_profit"]), reverse=True)
                    initial = edges[0]
                    venue = str(initial["venue"])

                    try:
                        await _venue_preflight(venue)
                    except Exception as exc:
                        logger.warning("MLB_GK_FINAL_LAG_PREFLIGHT_BLOCK venue=%s type=%s", venue, type(exc).__name__)
                        continue
                    await asyncio.sleep(PERSIST_MS / 1000.0)

                    if venue == "gemini":
                        g2raw = gemini_books.quote(row.gemini_side.venue_market_id, max_age_ms=MAX_AGE_MS)
                        if g2raw is None:
                            continue
                        refreshed = _edge(
                            venue="gemini",
                            market_id=row.gemini_side.venue_market_id,
                            book=orient_tob(g2raw, row.gemini_side.side),
                            outcome=outcome,
                        )
                    else:
                        k2raw, k2rtt = await _kalshi_exact(row.kalshi_side.venue_market_id)
                        if k2raw is None or k2rtt > MAX_AGE_MS:
                            continue
                        refreshed = _edge(
                            venue="kalshi",
                            market_id=row.kalshi_side.venue_market_id,
                            book=orient_tob(kalshi_tob(k2raw, row.kalshi_side.venue_market_id), row.kalshi_side.side),
                            outcome=outcome,
                            fee_multiplier=row.kalshi_fee_multiplier,
                        )
                        if refreshed is not None:
                            refreshed["revalidation_rtt_ms"] = round(k2rtt, 3)
                    if refreshed is None:
                        continue
                    if _d(refreshed["winner_ask"]) > _d(initial["winner_ask"]):
                        continue
                    if _d(refreshed["displayed_depth"]) < ONE or _d(refreshed["modeled_profit"]) < MIN_PROFIT:
                        continue

                    trade_id = _safe_id(f"gk-final-lag:{uuid.uuid4().hex[:20]}")
                    settlement_key = f"gk-final-lag|{key}|{outcome}"
                    context = {
                        "trade_id": trade_id,
                        "settlement_key": settlement_key,
                        "player": row.player,
                        "family": FAMILY,
                        "threshold": str(threshold),
                        "mlb_game_pk": int(game.get("gamePk") or 0),
                        "official_hits": hits,
                        "outcome": outcome,
                        "venue": venue,
                        "market_id": refreshed["market_id"],
                        "winner_ask": refreshed["winner_ask"],
                        "fee_upper_bound": refreshed["fee_upper_bound"],
                        "all_in_cost": refreshed["all_in_cost"],
                        "modeled_profit": refreshed["modeled_profit"],
                        "stability": stability,
                        "fanduel_required": False,
                        "rule_state": "NORMAL_RESULT_ALIGNED_POST_FINAL_BUT_STAT_CORRECTION_CUTOFF_DIFFERS",
                    }
                    try:
                        await _reserve(trade_id=trade_id, context=context, amount=_d(refreshed["all_in_cost"]))
                    except Exception as exc:
                        logger.warning("MLB_GK_FINAL_LAG_RESERVATION_BLOCK type=%s reason=%s", type(exc).__name__, str(exc)[:220])
                        continue

                    attempted.add(execution_key)
                    await _post_required({
                        "event_id": _safe_id(f"{trade_id}:submitted"),
                        "trade_id": trade_id,
                        "event_type": "ORDER_SUBMITTED",
                        "event_ts": _now(),
                        "strategy": STRATEGY,
                        "mode": "LIVE_PROBE",
                        "market": settlement_key,
                        "asset": row.player,
                        "venues": [venue],
                        "side": f"BUY {outcome}",
                        "status": "SUBMITTED",
                        "quantity": "1",
                        "legs_required": 1,
                        "legs_filled": 0,
                        "opportunity_net_profit": refreshed["modeled_profit"],
                        "metadata": context,
                    })
                    try:
                        result = await _execute(row, refreshed, context)
                    except Exception as exc:
                        hard_halt = True
                        logger.exception("MLB_GK_FINAL_LAG_SUBMIT_EXCEPTION")
                        await _push(alerts, "MLB AUTO FINAL-LAG — HARD HALT", f"{row.player}\n{venue.title()} {outcome}\nSubmission exception: {type(exc).__name__}\nNo retry. Reconcile venue account.")
                        break

                    logger.warning("MLB_GK_FINAL_LAG_EXECUTION_RESULT %s", json.dumps({
                        "trade_id": trade_id,
                        "context": context,
                        "result": result.as_dict(),
                    }, separators=(",", ":"), default=str))
                    if result.status == "UNKNOWN":
                        hard_halt = True
                        await _post_required({
                            "event_id": _safe_id(f"{trade_id}:execution-result"),
                            "trade_id": trade_id,
                            "event_type": "EXECUTION_RESULT",
                            "event_ts": _now(),
                            "strategy": STRATEGY,
                            "mode": "LIVE_PROBE",
                            "market": settlement_key,
                            "asset": row.player,
                            "venues": [venue],
                            "side": f"BUY {outcome}",
                            "status": "AMBIGUOUS_SUBMISSION_HARD_HALT",
                            "quantity": "1",
                            "legs_required": 1,
                            "legs_filled": 0,
                            "metadata": {**context, "execution": result.as_dict()},
                        })
                        await _push(alerts, "MLB AUTO FINAL-LAG — AMBIGUOUS", f"{row.player}\n{venue.title()} {outcome}\nOrder state UNKNOWN. Bot halted; no retry. Reconcile venue account.")
                        break

                    filled = _d(result.filled_quantity) == ONE
                    if not filled:
                        await _post_required({
                            "event_id": _safe_id(f"{trade_id}:execution-result"),
                            "trade_id": trade_id,
                            "event_type": "EXECUTION_RESULT",
                            "event_ts": _now(),
                            "strategy": STRATEGY,
                            "mode": "LIVE_PROBE",
                            "market": settlement_key,
                            "asset": row.player,
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
                            logger.exception("MLB_GK_FINAL_LAG_CAPITAL_RELEASE_FAILED")
                            hard_halt = True
                            break
                        continue

                    await _post_required({
                        "event_id": _safe_id(f"{trade_id}:fill:{venue}"),
                        "trade_id": trade_id,
                        "event_type": "FILL",
                        "event_ts": _now(),
                        "strategy": STRATEGY,
                        "mode": "LIVE_PROBE",
                        "market": settlement_key,
                        "asset": row.player,
                        "venues": [venue],
                        "side": f"BUY {outcome}",
                        "status": "FILLED",
                        "quantity": "1",
                        "price": refreshed["winner_ask"],
                        "amount": refreshed["all_in_cost"],
                        "fee": result.fee_paid if result.fee_paid is not None else refreshed["fee_upper_bound"],
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
                        "asset": row.player,
                        "venues": [venue],
                        "side": f"BUY {outcome}",
                        "status": "FILLED_NOT_SETTLED",
                        "quantity": "1",
                        "legs_required": 1,
                        "legs_filled": 1,
                        "opportunity_net_profit": refreshed["modeled_profit"],
                        "metadata": {**context, "execution": result.as_dict()},
                    })
                    hard_halt = True
                    await _push(alerts, f"MLB AUTO FILLED — {row.player}", "\n".join((
                        f"AUTO BUY: 1 {outcome} on {venue.title()}",
                        f"Market: {refreshed['market_id']}",
                        f"Revalidated ask: ${refreshed['winner_ask']}",
                        f"Modeled profit if settlement unchanged: +${refreshed['modeled_profit']}",
                        "FanDuel hedge: NONE",
                        "Status: FILLED_NOT_SETTLED",
                        "SETTLEMENT-LAG CORRECTION RISK — NOT ARBITRAGE / NOT REALIZED YET",
                    )))
                    break

                logger.info("MLB_GK_FINAL_LAG_HEARTBEAT stable=%s positive=%s gemini_connected=%s gemini_updates=%s armed=%s hard_halt=%s cycle_ms=%.1f", stable_count, positive, len(gemini_books.connected_symbols), gemini_books.book_updates, armed(), hard_halt, (time.monotonic() - cycle_started) * 1000)
                await asyncio.sleep(max(0.2, 2.0 - (time.monotonic() - cycle_started)))
        finally:
            book_task.cancel()
            await asyncio.gather(book_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
