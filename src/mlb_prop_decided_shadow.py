"""Read-only MLB decided-player-prop settlement-lag research.

This lane is intentionally separate from the game-total terminal shadow and from
all execution services.  It asks a narrower question: once public MLB game state
makes a strict Kalshi/Polymarket US player prop strongly determined, do either of
those venues still expose a tradeable stale winner price?

Supported strict families are cumulative >= thresholds:
* hits
* hits + runs + RBIs
* total bases
* pitcher strikeouts

Two research states are emitted:
* THRESHOLD_REACHED_STABLE_RESEARCH_ONLY -- during a live game after the player's
  official MLB boxscore statistic has reached/exceeded the threshold.
* FINAL_STAT_STABLE_RESEARCH_ONLY -- after MLB reports the game Final and the
  player's final statistic determines either YES or NO.

Both states require repeated identical observations.  A threshold-reached state is
NOT called final because official scoring corrections can still happen.  Nothing
in this module authenticates to a venue, mutates approvals, or submits orders.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.current_polymarket_mlb_props import fetch_current_poly_mlb_props
from src.mlb_live_participation import candidate_game, fetch_game_boxscores, _norm_name
from src.strict_mlb_prop_adapters import FAMILY_SERIES

logger = logging.getLogger("kalshi_arb.mlb_prop_decided_shadow")

DEFAULT_DISCOVERY_SECONDS = 180.0
DEFAULT_RETENTION_SECONDS = 6 * 60 * 60
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_STABLE_MS = 60_000
DEFAULT_STABLE_OBSERVATIONS = 3
PRICE_CONCURRENCY = 8

FAMILY_STAT = {
    "baseball_player_hits": ("batting", "hits"),
    "baseball_player_hits_runs_rbis": ("batting", "hrr"),
    "baseball_player_total_bases": ("batting", "totalBases"),
    "baseball_player_strikeouts": ("pitching", "strikeOuts"),
}


@dataclass(frozen=True)
class PropObservation:
    game_pk: int
    game_status: str
    player: str
    family: str
    statistic: int
    threshold: Decimal
    outcome: str | None
    decision_state: str | None
    source: str

    @property
    def fingerprint(self) -> str:
        return "|".join(
            [
                str(self.game_pk),
                self.game_status,
                _norm_name(self.player),
                self.family,
                str(self.statistic),
                str(self.threshold),
                str(self.outcome or ""),
                str(self.decision_state or ""),
            ]
        )


@dataclass
class _Stable:
    fingerprint: str
    first_seen_ms: int
    observations: int
    last_seen_ms: int


class StablePropTracker:
    """Require repeated identical MLB evidence before exposing a research signal."""

    def __init__(
        self,
        *,
        minimum_stable_ms: int = DEFAULT_STABLE_MS,
        minimum_observations: int = DEFAULT_STABLE_OBSERVATIONS,
    ) -> None:
        self.minimum_stable_ms = minimum_stable_ms
        self.minimum_observations = minimum_observations
        self._state: dict[str, _Stable] = {}

    def observe(self, key: str, observation: PropObservation, now_ms: int) -> bool:
        current = self._state.get(key)
        if current is None or current.fingerprint != observation.fingerprint:
            self._state[key] = _Stable(observation.fingerprint, now_ms, 1, now_ms)
            return False
        current.observations += 1
        current.last_seen_ms = now_ms
        return bool(
            current.observations >= self.minimum_observations
            and now_ms - current.first_seen_ms >= self.minimum_stable_ms
        )

    def status(self, key: str) -> dict[str, Any]:
        row = self._state.get(key)
        if row is None:
            return {"observations": 0, "stable_for_ms": 0}
        return {
            "observations": row.observations,
            "stable_for_ms": max(0, row.last_seen_ms - row.first_seen_ms),
        }


def _integer(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _player_stat(row: dict[str, Any], family: str) -> int | None:
    section_key = FAMILY_STAT.get(family)
    if section_key is None:
        return None
    section, stat_key = section_key
    stats = (row.get("stats") or {}).get(section) or {}
    if stat_key == "hrr":
        hits = _integer(stats.get("hits"))
        runs = _integer(stats.get("runs"))
        rbi = _integer(stats.get("rbi"))
        if None in (hits, runs, rbi):
            return None
        assert hits is not None and runs is not None and rbi is not None
        return hits + runs + rbi
    return _integer(stats.get(stat_key))


def prop_observation(
    *,
    family: str,
    player_name: str,
    poly_slug: str,
    threshold: Decimal,
    games: dict[tuple[str, tuple[str, str]], dict[str, Any]],
) -> PropObservation | None:
    game_key = candidate_game(poly_slug)
    if game_key is None:
        return None
    game = games.get(game_key)
    if game is None:
        return None
    target = _norm_name(player_name)
    box = game.get("boxscore") or {}
    for side in ("away", "home"):
        players = (((box.get("teams") or {}).get(side) or {}).get("players") or {})
        for row in players.values():
            person = row.get("person") or {}
            if _norm_name(person.get("fullName")) != target:
                continue
            statistic = _player_stat(row, family)
            if statistic is None:
                return None
            status = str(game.get("status") or "")
            final = status.lower() == "final"
            reached = Decimal(statistic) >= threshold
            if final:
                outcome = "YES" if reached else "NO"
                decision_state = "FINAL_STAT_STABLE_RESEARCH_ONLY"
            elif reached:
                outcome = "YES"
                decision_state = "THRESHOLD_REACHED_STABLE_RESEARCH_ONLY"
            else:
                outcome = None
                decision_state = None
            return PropObservation(
                game_pk=int(game.get("gamePk") or 0),
                game_status=status,
                player=player_name,
                family=family,
                statistic=statistic,
                threshold=threshold,
                outcome=outcome,
                decision_state=decision_state,
                source=f"MLB_STATS_API_GAME_{game.get('gamePk')}",
            )
    return None


def _known_winner_edge(
    *,
    venue: str,
    outcome: str,
    book: base.TopBook,
    kalshi_multiplier: Decimal | None = None,
) -> dict[str, Any] | None:
    ask = book.yes_ask if outcome == "YES" else book.no_ask
    depth = book.yes_depth if outcome == "YES" else book.no_depth
    if depth < 1:
        return None
    if venue == "polymarket":
        fee = base.polymarket_fee(1, ask)
    elif venue == "kalshi":
        if kalshi_multiplier is None:
            return None
        fee = base.conservative_kalshi_fee(1, ask, kalshi_multiplier)
    else:
        return None
    cost = ask + fee
    profit = Decimal("1") - cost
    if profit <= 0:
        return None
    return {
        "venue": venue,
        "outcome": outcome,
        "quantity": 1,
        "winner_ask": str(ask),
        "displayed_depth": str(depth),
        "fee_upper_bound": str(fee),
        "all_in_cost": str(cost),
        "known_winner_profit": str(profit),
        "known_winner_roi": str(profit / cost) if cost > 0 else "0",
    }


async def _optional_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = await http.get(url, **kwargs)
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"
        return response.json(), None
    except (httpx.HTTPError, ValueError) as exc:
        return None, type(exc).__name__


class RetainedCandidates:
    def __init__(self, ttl_seconds: float = DEFAULT_RETENTION_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._rows: dict[str, tuple[base.StrictCandidate, float]] = {}

    def refresh(self, candidates: list[base.StrictCandidate], now: float) -> None:
        for candidate in candidates:
            self._rows[candidate.key] = (candidate, now)
        expired = [
            key for key, (_, seen) in self._rows.items()
            if now - seen > self.ttl_seconds
        ]
        for key in expired:
            self._rows.pop(key, None)

    def values(self) -> list[base.StrictCandidate]:
        return [row for row, _ in self._rows.values()]


async def _series_multipliers(http: httpx.AsyncClient) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for series in sorted(set(FAMILY_SERIES.values())):
        body, error = await _optional_json(http, f"{base.KALSHI}/series/{series}")
        if body is None or error is not None:
            continue
        meta = body.get("series") or {}
        if str(meta.get("fee_type") or "") not in {"quadratic", "quadratic_with_maker_fees"}:
            continue
        multiplier = base._d(meta.get("fee_multiplier"))
        if multiplier is not None and Decimal("0") <= multiplier <= Decimal("100"):
            out[series] = multiplier
    return out


async def _discover(http: httpx.AsyncClient) -> list[base.StrictCandidate]:
    poly, kalshi = await asyncio.gather(
        fetch_current_poly_mlb_props(http, ttl_seconds=DEFAULT_DISCOVERY_SECONDS),
        base._fetch_kalshi_events(http),
    )
    return base.discover_strict_candidates(poly, kalshi)


async def _price_candidate(
    http: httpx.AsyncClient,
    candidate: base.StrictCandidate,
    observation: PropObservation,
    multipliers: dict[str, Decimal],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        (poly_body, poly_error), (kalshi_body, kalshi_error) = await asyncio.gather(
            _optional_json(http, f"{base.POLY}/v1/markets/{candidate.poly_slug}/book"),
            _optional_json(
                http,
                f"{base.KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
                params={"depth": 1},
            ),
        )
    pbook = base.poly_top_book(poly_body) if poly_body else None
    kbook = base.kalshi_top_book(kalshi_body) if kalshi_body else None
    multiplier = multipliers.get(FAMILY_SERIES[candidate.family])
    edges: list[dict[str, Any]] = []
    if pbook is not None and observation.outcome:
        edge = _known_winner_edge(venue="polymarket", outcome=observation.outcome, book=pbook)
        if edge:
            edges.append(edge)
    if kbook is not None and observation.outcome:
        edge = _known_winner_edge(
            venue="kalshi",
            outcome=observation.outcome,
            book=kbook,
            kalshi_multiplier=multiplier,
        )
        if edge:
            edges.append(edge)
    pair = None
    if pbook is not None and kbook is not None and multiplier is not None:
        pair = base.best_conservative_plan(pbook, kbook, kalshi_fee_multiplier=multiplier, max_quantity=1)
    return {
        "family": candidate.family,
        "player": candidate.player,
        "threshold": str(candidate.threshold),
        "game_date": candidate.game_date,
        "poly_slug": candidate.poly_slug,
        "kalshi_ticker": candidate.kalshi_ticker,
        "mlb_game_pk": observation.game_pk,
        "mlb_game_status": observation.game_status,
        "official_statistic": observation.statistic,
        "known_outcome": observation.outcome,
        "decision_state": observation.decision_state,
        "evidence_source": observation.source,
        "known_winner_edges": edges,
        "cross_venue_q1_pair_lock": pair,
        "poly_book_error": poly_error,
        "kalshi_book_error": kalshi_error,
        "execution_eligible": False,
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }


async def run_forever() -> None:
    retained = RetainedCandidates()
    tracker = StablePropTracker()
    last_discovery: float | None = None
    multipliers: dict[str, Decimal] = {}

    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            cycle_start = time.monotonic()
            now_ms = int(time.time() * 1000)
            discovery_errors: list[str] = []
            # An empty overlap is a valid discovery result. Do not hammer venue
            # discovery APIs every two seconds just because the retained set is empty.
            if last_discovery is None or cycle_start - last_discovery >= DEFAULT_DISCOVERY_SECONDS:
                try:
                    fresh = await _discover(http)
                    retained.refresh(fresh, cycle_start)
                    multipliers = await _series_multipliers(http)
                    last_discovery = cycle_start
                    logger.info(
                        "DECIDED_MLB_PROP_READY %s",
                        json.dumps(
                            {
                                "fresh_candidates": len(fresh),
                                "retained_candidates": len(retained.values()),
                                "retention_seconds": DEFAULT_RETENTION_SECONDS,
                                "discovery_seconds": DEFAULT_DISCOVERY_SECONDS,
                                "stable_ms": DEFAULT_STABLE_MS,
                                "stable_observations": DEFAULT_STABLE_OBSERVATIONS,
                                "read_only": True,
                                "order_entry": False,
                            },
                            separators=(",", ":"),
                        ),
                    )
                except Exception as exc:  # keep prior retained universe fail-safe
                    # Back off after a failed discovery too; stale retained candidates
                    # remain available and the next discovery happens on schedule.
                    last_discovery = cycle_start
                    discovery_errors.append(f"{type(exc).__name__}:{exc}")

            candidates = retained.values()
            games: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
            if candidates:
                try:
                    games = await fetch_game_boxscores(http, {c.game_date for c in candidates})
                except Exception as exc:
                    discovery_errors.append(f"MLB:{type(exc).__name__}:{exc}")

            stable_rows: list[tuple[base.StrictCandidate, PropObservation, dict[str, Any]]] = []
            observed_decided = 0
            for candidate in candidates:
                obs = prop_observation(
                    family=candidate.family,
                    player_name=candidate.player,
                    poly_slug=candidate.poly_slug,
                    threshold=candidate.threshold,
                    games=games,
                )
                if obs is None or obs.decision_state is None or obs.outcome is None:
                    continue
                observed_decided += 1
                if tracker.observe(candidate.key, obs, now_ms):
                    stable_rows.append((candidate, obs, tracker.status(candidate.key)))

            semaphore = asyncio.Semaphore(PRICE_CONCURRENCY)
            priced = await asyncio.gather(
                *(
                    _price_candidate(http, candidate, obs, multipliers, semaphore)
                    for candidate, obs, _ in stable_rows
                )
            ) if stable_rows else []
            status_by_key = {
                candidate.key: status for candidate, _, status in stable_rows
            }
            for row, (candidate, _, _) in zip(priced, stable_rows):
                row["stability"] = status_by_key[candidate.key]
                if row["known_winner_edges"] or row["cross_venue_q1_pair_lock"]:
                    logger.info("DECIDED_MLB_PROP_SIGNAL %s", json.dumps(row, separators=(",", ":")))

            summary = {
                "retained_candidates": len(candidates),
                "mlb_games_with_boxscores": len(games),
                "decided_observations": observed_decided,
                "stable_decided_candidates": len(stable_rows),
                "priced_stable_candidates": len(priced),
                "positive_known_winner_edges": sum(len(r["known_winner_edges"]) for r in priced),
                "positive_q1_pair_locks": sum(bool(r["cross_venue_q1_pair_lock"]) for r in priced),
                "discovery_errors": discovery_errors,
                "cycle_ms": round((time.monotonic() - cycle_start) * 1000, 1),
                "execution_eligible": False,
                "approved_for_execution": False,
                "read_only": True,
                "order_entry": False,
            }
            logger.info("DECIDED_MLB_PROP_SUMMARY %s", json.dumps(summary, separators=(",", ":")))
            await asyncio.sleep(max(0.1, DEFAULT_POLL_SECONDS - (time.monotonic() - cycle_start)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
