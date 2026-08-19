"""Read-only post-Final Gemini <-> Kalshi MLB hits settlement-lag monitor.

This lane is intentionally *not* called arbitrage. The audited venue rules differ
before/during the game and retain one post-game difference: Kalshi treats player
statistics as final at game conclusion, while Gemini's MLB Hits series uses the
Source Agency value at its Expiration Time and can therefore reflect a pre-expiration
scoring revision.

Architecture:
* one cold public REST discovery at startup for exact G<->K Hits identities;
* public Gemini ``@bookTicker`` WebSockets for live prediction books;
* authenticated read-only Kalshi ``orderbook_delta`` WebSocket for live books;
* MLB Stats API only for official game/boxscore state.

After MLB reports Final, a Kalshi-qualifying starter has >=1 PA, and the same official
hit total is stable for >=60 seconds / >=3 observations, the monitor checks whether a
still-open live book offers the currently-known winning side below $1 after fees.
Signals are FINAL_STAT_CORRECTION_RISK_RESEARCH_ONLY. No order transport is imported.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.eventing_books import EventingKalshiCrossBooks
from src.gemini_kalshi_execution_shadow import TopOfBook, gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_kalshi_mlb_prop_hot_shadow import GeminiPropBooks, orient_tob, kalshi_tob
from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, _norm_name
from src.sports_pushover_alerts import SportsPushoverAlerts
from src.strict_mlb_prop_snapshot import KALSHI
from src.three_venue_mlb_prop_safe_scan import _kalshi_event_map
from src.three_venue_mlb_snapshot_pricing import _series_multipliers
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
ONE = D("1")
logger = logging.getLogger("kalshi_arb.gemini_kalshi_mlb_hits_final_lag_shadow")

MLB_POLL_SECONDS = 2.0
MIN_STABLE_MS = 60_000
MIN_STABLE_OBSERVATIONS = 3
ALERT_MIN_PROFIT = D("0.03")
BOOK_MAX_AGE_MS = 30_000


@dataclass
class StableState:
    fingerprint: str
    first_seen_ms: int
    last_seen_ms: int
    observations: int = 1


@dataclass(frozen=True)
class HitContract:
    key: str
    gemini_side: Any
    kalshi_side: Any
    player: str
    game_slug: str
    threshold: Decimal
    kalshi_fee_multiplier: Decimal


class StableTracker:
    def __init__(self) -> None:
        self.rows: dict[str, StableState] = {}

    def observe(self, key: str, fingerprint: str, now_ms: int) -> tuple[bool, dict[str, int]]:
        row = self.rows.get(key)
        if row is None or row.fingerprint != fingerprint:
            row = StableState(fingerprint, now_ms, now_ms)
            self.rows[key] = row
            return False, {"observations": 1, "stable_for_ms": 0}
        row.observations += 1
        row.last_seen_ms = now_ms
        stable_for = max(0, row.last_seen_ms - row.first_seen_ms)
        ready = row.observations >= MIN_STABLE_OBSERVATIONS and stable_for >= MIN_STABLE_MS
        return ready, {"observations": row.observations, "stable_for_ms": stable_for}


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


async def _fetch_kalshi_hits(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Cold discovery only. Never called by the live pricing loop."""
    response = await http.get(
        f"{KALSHI}/events",
        params={
            "limit": 200,
            "status": "open",
            "series_ticker": "KXMLBHIT",
            "with_nested_markets": "true",
        },
    )
    response.raise_for_status()
    return [row for row in (response.json().get("events") or []) if isinstance(row, dict)]


async def _discover(http: httpx.AsyncClient) -> dict[str, HitContract]:
    gemini_events, kalshi_events = await asyncio.gather(fetch_gemini_mlb(http), _fetch_kalshi_hits(http))
    sides, _venues, _joins, context = _candidate_sides(kalshi_events, gemini_events)
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for side in sides:
        if context.get(side.contract_key, {}).get("family") == "baseball_player_hits":
            grouped[side.contract_key][side.identity.venue].append(side)

    _event_map, ticker_to_series = _kalshi_event_map(kalshi_events)
    fee_errors: list[dict[str, str]] = []
    multipliers = await _series_multipliers(http, asyncio.Semaphore(2), ["KXMLBHIT"], fee_errors)
    multiplier = multipliers.get("KXMLBHIT")
    if multiplier is None:
        raise RuntimeError(f"missing KXMLBHIT fee multiplier: {fee_errors[:3]}")

    out: dict[str, HitContract] = {}
    for key, by_venue in grouped.items():
        g = {(row.venue_market_id, row.side): row for row in by_venue.get("gemini", [])}
        k = {(row.venue_market_id, row.side): row for row in by_venue.get("kalshi", [])}
        row_context = context.get(key)
        if len(g) != 1 or len(k) != 1 or row_context is None:
            continue
        gside = next(iter(g.values()))
        kside = next(iter(k.values()))
        if gside.identity.line is None:
            continue
        out[key] = HitContract(
            key=key,
            gemini_side=gside,
            kalshi_side=kside,
            player=row_context["player"],
            game_slug=row_context["game_slug"],
            threshold=D(str(gside.identity.line)),
            kalshi_fee_multiplier=D(str(multiplier)),
        )
    return out


def _winner_edge(
    *,
    venue: str,
    market_id: str,
    book: TopOfBook,
    outcome: str,
    fee_multiplier: Decimal | None = None,
) -> dict[str, Any] | None:
    if outcome == "YES":
        price = book.yes_ask
        depth = book.yes_ask_size
    else:
        price = ONE - book.yes_bid
        depth = book.yes_bid_size
    if depth < ONE or price <= 0 or price >= ONE:
        return None
    if venue == "gemini":
        fee = gemini_predictions_taker_fee(price, ONE)
    elif venue == "kalshi" and fee_multiplier is not None:
        fee = kalshi_quadratic_taker_fee(price, ONE, fee_multiplier=fee_multiplier)
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
        "raw_winner_ask": str(price),
        "fee_upper_bound": str(fee),
        "all_in_cost": str(cost),
        "displayed_depth": str(depth),
        "modeled_known_winner_profit": str(profit),
        "modeled_known_winner_roi": str(profit / cost) if cost > 0 else "0",
        "book_received_ms": book.ts_ms,
    }


async def _maybe_alert(alerts: SportsPushoverAlerts, payload: dict[str, Any], sent: set[str]) -> None:
    if not alerts.configured:
        return
    edge = payload.get("best_edge") or {}
    try:
        profit = D(str(edge.get("modeled_known_winner_profit") or "0"))
    except Exception:
        return
    if profit < ALERT_MIN_PROFIT:
        return
    key = "|".join((payload["settlement_key"], edge.get("venue", ""), payload["known_outcome"]))
    if key in sent:
        return
    title = f"MLB FINAL LAG — {payload['player']} {payload['threshold']}+ Hits"[:250]
    message = "\n".join((
        f"MLB Final — official hits: {payload['official_hits']} → {payload['known_outcome']}",
        f"Venue: {str(edge.get('venue') or '').title()}",
        f"Winner ask: ${edge.get('raw_winner_ask')} + fee cap ${edge.get('fee_upper_bound')}",
        f"Modeled discount to $1: +${edge.get('modeled_known_winner_profit')}/contract",
        f"Evidence stable: {payload['stability']['stable_for_ms']/1000:.0f}s / {payload['stability']['observations']} observations",
        "CORRECTION-RISK SETTLEMENT-LAG SIGNAL — NOT GUARANTEED ARBITRAGE",
        "Kalshi freezes stats at game conclusion; Gemini can reflect revisions before its expiration.",
        "Read-only: no order was submitted.",
    ))
    try:
        await alerts.send(title=title, message=message[:1024])
    except Exception as exc:
        logger.warning("MLB_GK_FINAL_LAG_PUSHOVER_ERROR type=%s", type(exc).__name__)
        return
    sent.add(key)
    logger.info("MLB_GK_FINAL_LAG_PUSHOVER_SENT %s", json.dumps({
        "key": key, "profit": str(profit), "read_only": True, "order_entry": False,
    }, separators=(",", ":")))


async def run_forever() -> None:
    tracker = StableTracker()
    alerts = SportsPushoverAlerts()
    sent: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0) as http:
        contracts = await _discover(http)
    if not contracts:
        raise RuntimeError("no exact Gemini/Kalshi MLB Hits contracts")

    symbols = sorted({row.gemini_side.venue_market_id for row in contracts.values()})
    tickers = sorted({row.kalshi_side.venue_market_id for row in contracts.values()})
    dates = sorted({_game_date(row.game_slug) for row in contracts.values()})
    gemini = GeminiPropBooks(symbols)
    kalshi = EventingKalshiCrossBooks(tickers)
    tasks = [
        asyncio.create_task(gemini.run(), name="gemini_final_lag_books"),
        asyncio.create_task(kalshi.run(), name="kalshi_final_lag_books"),
    ]
    logger.info("MLB_GK_FINAL_LAG_READY %s", json.dumps({
        "exact_hit_keys": len(contracts),
        "gemini_symbols": len(symbols),
        "kalshi_tickers": len(tickers),
        "pricing_transport": "GEMINI_BOOKTICKER_PLUS_KALSHI_ORDERBOOK_DELTA",
        "kalshi_rest_after_startup": False,
        "correction_risk": "KALSHI_FINAL_AT_GAME_CONCLUSION_VS_GEMINI_FINAL_AT_EXPIRATION",
        "pushover_configured": alerts.configured,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))

    cycle = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            while True:
                cycle += 1
                started = time.monotonic()
                now_ms = int(time.time() * 1000)
                errors: list[str] = []
                try:
                    games = await fetch_game_boxscores(http, dates)
                except Exception as exc:
                    games = {}
                    errors.append(f"MLB:{type(exc).__name__}")

                final_keys = 0
                stable_final_keys = 0
                comparable_final_keys = 0
                signals: list[dict[str, Any]] = []
                for key, row in contracts.items():
                    game = _game_for_slug(row.game_slug, games)
                    if game is None or str(game.get("status") or "").lower() != "final":
                        continue
                    final_keys += 1
                    proof = participation_proof(
                        family="baseball_player_hits",
                        player_name=row.player,
                        poly_slug=row.game_slug,
                        games=games,
                    )
                    if not (proof.started and proof.plate_appearance_recorded and proof.source):
                        continue
                    hits = _hits_for_player(game, row.player)
                    if hits is None:
                        continue
                    outcome = "YES" if D(hits) >= row.threshold else "NO"
                    fingerprint = "|".join((
                        str(game.get("gamePk") or ""), "Final", _norm_name(row.player),
                        str(hits), str(row.threshold), outcome,
                    ))
                    stable, stability = tracker.observe(key, fingerprint, now_ms)
                    if not stable:
                        continue
                    stable_final_keys += 1

                    g_raw = gemini.quote(row.gemini_side.venue_market_id, max_age_ms=BOOK_MAX_AGE_MS)
                    k_quote = await kalshi.quote(row.kalshi_side.venue_market_id, max_age_ms=BOOK_MAX_AGE_MS)
                    if g_raw is None or k_quote is None:
                        continue
                    g = orient_tob(g_raw, row.gemini_side.side)
                    k = orient_tob(kalshi_tob(k_quote, row.kalshi_side.venue_market_id), row.kalshi_side.side)
                    comparable_final_keys += 1
                    edges = [
                        edge for edge in (
                            _winner_edge(
                                venue="gemini",
                                market_id=row.gemini_side.venue_market_id,
                                book=g,
                                outcome=outcome,
                            ),
                            _winner_edge(
                                venue="kalshi",
                                market_id=row.kalshi_side.venue_market_id,
                                book=k,
                                outcome=outcome,
                                fee_multiplier=row.kalshi_fee_multiplier,
                            ),
                        ) if edge is not None
                    ]
                    edges.sort(key=lambda edge: D(edge["modeled_known_winner_profit"]), reverse=True)
                    if not edges:
                        continue
                    payload = {
                        "status": "MLB_GK_FINAL_STAT_CORRECTION_RISK_RESEARCH_ONLY",
                        "settlement_key": key,
                        "player": row.player,
                        "threshold": str(row.threshold),
                        "mlb_game_pk": int(game.get("gamePk") or 0),
                        "official_hits": hits,
                        "known_outcome": outcome,
                        "participation_proof": {
                            "started": proof.started,
                            "plate_appearance_recorded": proof.plate_appearance_recorded,
                            "source": proof.source,
                        },
                        "stability": stability,
                        "winner_edges": edges,
                        "best_edge": edges[0],
                        "rule_state": "NORMAL_RESULT_ALIGNED_POST_FINAL_BUT_STAT_CORRECTION_CUTOFF_DIFFERS",
                        "execution_eligible": False,
                        "approved_for_execution": False,
                        "read_only": True,
                        "order_entry": False,
                    }
                    signals.append(payload)
                    logger.info("MLB_GK_FINAL_LAG_SIGNAL %s", json.dumps(payload, separators=(",", ":"), default=str))
                    await _maybe_alert(alerts, payload, sent)

                best_profit = None if not signals else max(
                    D(signal["best_edge"]["modeled_known_winner_profit"]) for signal in signals
                )
                logger.info("MLB_GK_FINAL_LAG_HEARTBEAT %s", json.dumps({
                    "cycle": cycle,
                    "exact_hit_keys": len(contracts),
                    "final_keys": final_keys,
                    "stable_final_keys": stable_final_keys,
                    "comparable_final_keys": comparable_final_keys,
                    "positive_known_winner_signals": len(signals),
                    "best_modeled_profit": None if best_profit is None else str(best_profit),
                    "gemini_connected_symbols": len(gemini.connected_symbols),
                    "gemini_book_updates": gemini.book_updates,
                    "kalshi_initialized": kalshi.initialized_count,
                    "kalshi_rest_after_startup": False,
                    "pushover_configured": alerts.configured,
                    "errors": errors,
                    "cycle_ms": round((time.monotonic() - started) * 1000, 3),
                    "execution_eligible": False,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":"), default=str))
                await asyncio.sleep(max(0.2, MLB_POLL_SECONDS - (time.monotonic() - started)))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await kalshi.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
