"""Read-only post-Final Gemini <-> Kalshi MLB hits settlement-lag monitor.

This lane is intentionally *not* called arbitrage. The audited venue rules differ
before/during the game and retain one post-game difference: Kalshi treats player
statistics as final at game conclusion, while Gemini's MLB Hits series uses the
Source Agency value at its Expiration Time (one hour after official final stats) and
can therefore reflect a pre-expiration scoring revision.

After MLB reports Final, a Kalshi-qualifying starter has >=1 PA, and the same official
hit total is stable for >=60 seconds / >=3 observations, this monitor asks whether a
still-open executable quote offers the currently-known winning side at a positive
after-fee discount to $1. Identities refresh every 30 seconds; executable Gemini and
KXMLBHIT books refresh every cycle and are never replaced with stale price snapshots.

Signals are FINAL_STAT_CORRECTION_RISK_RESEARCH_ONLY. No order transport is imported
and no signal is execution eligible.
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

from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, _norm_name
from src.sports_pushover_alerts import SportsPushoverAlerts
from src.strict_mlb_prop_snapshot import KALSHI
from src.three_venue_mlb_prop_safe_scan import _price_sides
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_mlb_hits_final_lag_shadow")

POLL_SECONDS = 2.0
DISCOVERY_SECONDS = 30.0
MIN_STABLE_MS = 60_000
MIN_STABLE_OBSERVATIONS = 3
ALERT_MIN_PROFIT = D("0.03")


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
    body = response.json()
    return [row for row in body.get("events") or [] if isinstance(row, dict)]


def _winner_edge(priced: Any, outcome: str) -> dict[str, Any] | None:
    quote = priced.quote
    if outcome == "YES":
        cost, depth, raw_ask, fee = quote.yes_cost, quote.yes_depth, priced.raw_yes_ask, priced.yes_fee
    else:
        cost, depth, raw_ask, fee = quote.no_cost, quote.no_depth, priced.raw_no_ask, priced.no_fee
    if depth < 1:
        return None
    profit = D("1") - cost
    if profit <= 0:
        return None
    return {
        "venue": quote.venue,
        "market_id": quote.market_id,
        "outcome": outcome,
        "raw_winner_ask": str(raw_ask),
        "fee_upper_bound": str(fee),
        "all_in_cost": str(cost),
        "displayed_depth_capped": str(depth),
        "modeled_known_winner_profit": str(profit),
        "modeled_known_winner_roi": str(profit / cost) if cost > 0 else "0",
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
    last_discovery = 0.0
    cached: tuple[list[Any], dict[str, dict[str, str]]] | None = None
    cycle = 0

    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            cycle += 1
            started = time.monotonic()
            now_ms = int(time.time() * 1000)
            errors: list[str] = []

            # Prices must be fresh even when identity discovery is cached. Fail closed
            # for this cycle if either live venue snapshot cannot be refreshed.
            try:
                gemini_events, kalshi_hit_events = await asyncio.gather(
                    fetch_gemini_mlb(http), _fetch_kalshi_hits(http)
                )
                price_snapshot_fresh = True
            except Exception as exc:
                gemini_events, kalshi_hit_events = [], []
                price_snapshot_fresh = False
                errors.append(f"LIVE_PRICE_SNAPSHOT:{type(exc).__name__}")

            if price_snapshot_fresh and (cached is None or started - last_discovery >= DISCOVERY_SECONDS):
                try:
                    sides, _venues, _joins, context = _candidate_sides(kalshi_hit_events, gemini_events)
                    hit_keys = {key for key, row in context.items() if row.get("family") == "baseball_player_hits"}
                    sides = [side for side in sides if side.contract_key in hit_keys]
                    context = {key: row for key, row in context.items() if key in hit_keys}
                    cached = (sides, context)
                    last_discovery = started
                    logger.info("MLB_GK_FINAL_LAG_READY %s", json.dumps({
                        "exact_hit_keys": len(context),
                        "gemini_sides": sum(side.identity.venue == "gemini" for side in sides),
                        "kalshi_sides": sum(side.identity.venue == "kalshi" for side in sides),
                        "correction_risk": "KALSHI_FINAL_AT_GAME_CONCLUSION_VS_GEMINI_FINAL_AT_EXPIRATION",
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":")))
                except Exception as exc:
                    errors.append(f"DISCOVERY:{type(exc).__name__}")

            signals: list[dict[str, Any]] = []
            final_keys = 0
            stable_final_keys = 0
            priced_quotes = 0
            if cached is not None:
                sides, context = cached
                dates = {_game_date(row["game_slug"]) for row in context.values()}
                try:
                    games = await fetch_game_boxscores(http, dates)
                except Exception as exc:
                    games = {}
                    errors.append(f"MLB:{type(exc).__name__}")

                if price_snapshot_fresh:
                    try:
                        priced, price_errors = await _price_sides(http, sides, gemini_events, kalshi_hit_events)
                        priced_quotes = len(priced)
                        if price_errors:
                            errors.extend(f"PRICE:{row.get('venue')}:{row.get('error')}" for row in price_errors[:5])
                    except Exception as exc:
                        priced = []
                        errors.append(f"PRICE:{type(exc).__name__}")
                else:
                    priced = []
                priced_by_key: dict[str, list[Any]] = defaultdict(list)
                for row in priced:
                    priced_by_key[row.quote.settlement_key].append(row)

                representative: dict[str, Any] = {}
                for side in sides:
                    representative.setdefault(side.contract_key, side)

                for key, row in context.items():
                    side = representative.get(key)
                    if side is None or side.identity.line is None:
                        continue
                    game = _game_for_slug(row["game_slug"], games)
                    if game is None or str(game.get("status") or "").lower() != "final":
                        continue
                    final_keys += 1
                    proof = participation_proof(
                        family="baseball_player_hits",
                        player_name=row["player"],
                        poly_slug=row["game_slug"],
                        games=games,
                    )
                    if not (proof.started and proof.plate_appearance_recorded and proof.source):
                        continue
                    hits = _hits_for_player(game, row["player"])
                    if hits is None:
                        continue
                    threshold = side.identity.line
                    outcome = "YES" if D(hits) >= threshold else "NO"
                    fingerprint = "|".join((
                        str(game.get("gamePk") or ""), "Final", _norm_name(row["player"]),
                        str(hits), str(threshold), outcome,
                    ))
                    stable, stability = tracker.observe(key, fingerprint, now_ms)
                    if not stable:
                        continue
                    stable_final_keys += 1
                    edges = [
                        edge for edge in (_winner_edge(p, outcome) for p in priced_by_key.get(key, []))
                        if edge is not None
                    ]
                    edges.sort(key=lambda edge: D(edge["modeled_known_winner_profit"]), reverse=True)
                    if not edges:
                        continue
                    payload = {
                        "status": "MLB_GK_FINAL_STAT_CORRECTION_RISK_RESEARCH_ONLY",
                        "settlement_key": key,
                        "player": row["player"],
                        "threshold": str(threshold),
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

            best_profit = None
            if signals:
                best_profit = max(D(signal["best_edge"]["modeled_known_winner_profit"]) for signal in signals)
            logger.info("MLB_GK_FINAL_LAG_HEARTBEAT %s", json.dumps({
                "cycle": cycle,
                "exact_hit_keys": 0 if cached is None else len(cached[1]),
                "fresh_price_snapshot": price_snapshot_fresh,
                "priced_quotes": priced_quotes,
                "final_keys": final_keys,
                "stable_final_keys": stable_final_keys,
                "positive_known_winner_signals": len(signals),
                "best_modeled_profit": None if best_profit is None else str(best_profit),
                "pushover_configured": alerts.configured,
                "errors": errors[:10],
                "cycle_ms": round((time.monotonic() - started) * 1000, 3),
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
            await asyncio.sleep(max(0.2, POLL_SECONDS - (time.monotonic() - started)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
