"""Read-only synchronized cross-venue companion for decided MLB prop research.

This module intentionally reuses the strict discovery and official-MLB decision logic
from :mod:`src.mlb_prop_decided_shadow`, but hardens cross-venue pair evidence with
per-venue receive timestamps and explicit OPEN/MOVE/CLOSE episode transitions.

It has no authenticated venue client and no order-entry path. Single-venue known-
winner evidence stays in the original decided-prop lane; this companion exists only
to make cross-venue q=1 pair-lock timing/capacity accounting defensible.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any

import httpx

from src import mlb_prop_decided_shadow as decided
from src import strict_mlb_prop_snapshot as base
from src.strict_mlb_prop_adapters import FAMILY_SERIES

logger = logging.getLogger("kalshi_arb.mlb_prop_decided_pair_shadow_v2")

MAX_PAIR_RECEIVE_SKEW_MS = 500


@dataclass(frozen=True)
class TimedJson:
    body: dict[str, Any] | None
    error: str | None
    received_ms: int
    latency_ms: float


async def _timed_optional_json(
    http: httpx.AsyncClient,
    url: str,
    **kwargs: Any,
) -> TimedJson:
    started_ns = time.monotonic_ns()
    body, error = await decided._optional_json(http, url, **kwargs)
    received_ms = int(time.time() * 1000)
    latency_ms = max(0.0, (time.monotonic_ns() - started_ns) / 1_000_000)
    return TimedJson(body=body, error=error, received_ms=received_ms, latency_ms=latency_ms)


def strict_pair_identity(row: dict[str, Any]) -> str | None:
    family = str(row.get("family") or "").strip()
    poly_slug = str(row.get("poly_slug") or "").strip()
    kalshi_ticker = str(row.get("kalshi_ticker") or "").strip()
    pair = row.get("cross_venue_q1_pair_lock")
    if not isinstance(pair, dict):
        return None
    direction = str(pair.get("direction") or "").strip()
    if not all((family, poly_slug, kalshi_ticker, direction)):
        return None
    return "|".join((family, poly_slug, kalshi_ticker, direction))


def _pair_fingerprint(row: dict[str, Any]) -> str:
    pair = row.get("cross_venue_q1_pair_lock") or {}
    material = {
        "direction": pair.get("direction"),
        "quantity": pair.get("quantity"),
        "total_cost": pair.get("total_cost"),
        "profit": pair.get("profit"),
        "poly_price": pair.get("poly_price"),
        "kalshi_price": pair.get("kalshi_price"),
        "poly_depth": pair.get("poly_depth"),
        "kalshi_depth": pair.get("kalshi_depth"),
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)


class PairEpisodeTracker:
    """Emit deterministic OPEN/MOVE/CLOSE transitions for synchronized pair locks."""

    def __init__(self) -> None:
        self._active: dict[str, tuple[str, int, int]] = {}

    def transition(self, current_rows: list[dict[str, Any]], now_ms: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        seen: set[str] = set()
        annotated: list[dict[str, Any]] = []
        closes: list[dict[str, Any]] = []

        for row in current_rows:
            identity = strict_pair_identity(row)
            if identity is None:
                continue
            seen.add(identity)
            fingerprint = _pair_fingerprint(row)
            prior = self._active.get(identity)
            event = "OPEN" if prior is None else ("MOVE" if prior[0] != fingerprint else "RESTING")
            opened_ms = now_ms if prior is None else prior[1]
            self._active[identity] = (fingerprint, opened_ms, now_ms)
            enriched = dict(row)
            enriched["pair_episode"] = {
                "identity": identity,
                "event": event,
                "opened_ms": opened_ms,
                "observed_ms": now_ms,
                "age_ms": max(0, now_ms - opened_ms),
            }
            annotated.append(enriched)

        for identity, (_, opened_ms, last_seen_ms) in list(self._active.items()):
            if identity in seen:
                continue
            closes.append(
                {
                    "pair_episode": {
                        "identity": identity,
                        "event": "CLOSE",
                        "opened_ms": opened_ms,
                        "last_seen_ms": last_seen_ms,
                        "closed_observed_ms": now_ms,
                        "duration_lower_bound_ms": max(0, last_seen_ms - opened_ms),
                        "duration_upper_bound_ms": max(0, now_ms - opened_ms),
                        "interval_censored": True,
                    },
                    "execution_eligible": False,
                    "approved_for_execution": False,
                    "read_only": True,
                    "order_entry": False,
                }
            )
            self._active.pop(identity, None)
        return annotated, closes


async def _price_candidate_sync(
    http: httpx.AsyncClient,
    candidate: base.StrictCandidate,
    observation: decided.PropObservation,
    multipliers: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        poly_task = _timed_optional_json(http, f"{base.POLY}/v1/markets/{candidate.poly_slug}/book")
        kalshi_task = _timed_optional_json(
            http,
            f"{base.KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
            params={"depth": 1},
        )
        poly_result, kalshi_result = await asyncio.gather(poly_task, kalshi_task)

    pbook = base.poly_top_book(poly_result.body) if poly_result.body else None
    kbook = base.kalshi_top_book(kalshi_result.body) if kalshi_result.body else None
    multiplier = multipliers.get(FAMILY_SERIES[candidate.family])
    receive_skew_ms = abs(poly_result.received_ms - kalshi_result.received_ms)
    synchronized = receive_skew_ms <= MAX_PAIR_RECEIVE_SKEW_MS

    pair = None
    if synchronized and pbook is not None and kbook is not None and multiplier is not None:
        pair = base.best_conservative_plan(
            pbook,
            kbook,
            kalshi_fee_multiplier=multiplier,
            max_quantity=1,
        )

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
        "cross_venue_q1_pair_lock": pair,
        "poly_book_received_ms": poly_result.received_ms,
        "kalshi_book_received_ms": kalshi_result.received_ms,
        "quote_receive_skew_ms": receive_skew_ms,
        "poly_fetch_latency_ms": round(poly_result.latency_ms, 3),
        "kalshi_fetch_latency_ms": round(kalshi_result.latency_ms, 3),
        "synchronized_book_pair": synchronized,
        "synchronization_limit_ms": MAX_PAIR_RECEIVE_SKEW_MS,
        "poly_book_error": poly_result.error,
        "kalshi_book_error": kalshi_result.error,
        "execution_eligible": False,
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }


async def run_forever() -> None:
    retained = decided.RetainedCandidates()
    tracker = decided.StablePropTracker()
    episodes = PairEpisodeTracker()
    last_discovery: float | None = None
    multipliers: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            cycle_start = time.monotonic()
            now_ms = int(time.time() * 1000)
            errors: list[str] = []
            if last_discovery is None or cycle_start - last_discovery >= decided.DEFAULT_DISCOVERY_SECONDS:
                try:
                    fresh = await decided._discover(http)
                    retained.refresh(fresh, cycle_start)
                    multipliers = await decided._series_multipliers(http)
                    last_discovery = cycle_start
                except Exception as exc:
                    last_discovery = cycle_start
                    errors.append(f"DISCOVERY:{type(exc).__name__}:{exc}")

            candidates = retained.values()
            games: dict[Any, dict[str, Any]] = {}
            if candidates:
                try:
                    games = await decided.fetch_game_boxscores(http, {c.game_date for c in candidates})
                except Exception as exc:
                    errors.append(f"MLB:{type(exc).__name__}:{exc}")

            stable_rows: list[tuple[base.StrictCandidate, decided.PropObservation]] = []
            for candidate in candidates:
                obs = decided.prop_observation(
                    family=candidate.family,
                    player_name=candidate.player,
                    poly_slug=candidate.poly_slug,
                    threshold=candidate.threshold,
                    games=games,
                )
                if obs is None or obs.decision_state is None or obs.outcome is None:
                    continue
                if tracker.observe(candidate.key, obs, now_ms):
                    stable_rows.append((candidate, obs))

            semaphore = asyncio.Semaphore(decided.PRICE_CONCURRENCY)
            priced = await asyncio.gather(
                *(_price_candidate_sync(http, candidate, obs, multipliers, semaphore) for candidate, obs in stable_rows)
            ) if stable_rows else []

            pair_rows = [row for row in priced if row.get("cross_venue_q1_pair_lock")]
            annotated, closes = episodes.transition(pair_rows, now_ms)
            for row in annotated:
                logger.info("DECIDED_MLB_PROP_SYNC_PAIR %s", json.dumps(row, separators=(",", ":")))
            for row in closes:
                logger.info("DECIDED_MLB_PROP_SYNC_PAIR %s", json.dumps(row, separators=(",", ":")))

            logger.info(
                "DECIDED_MLB_PROP_SYNC_SUMMARY %s",
                json.dumps(
                    {
                        "retained_candidates": len(candidates),
                        "stable_decided_candidates": len(stable_rows),
                        "priced_stable_candidates": len(priced),
                        "synchronized_pair_locks": len(pair_rows),
                        "episode_rows": len(annotated),
                        "episode_closes": len(closes),
                        "max_receive_skew_ms": MAX_PAIR_RECEIVE_SKEW_MS,
                        "errors": errors,
                        "cycle_ms": round((time.monotonic() - cycle_start) * 1000, 1),
                        "execution_eligible": False,
                        "approved_for_execution": False,
                        "read_only": True,
                        "order_entry": False,
                    },
                    separators=(",", ":"),
                ),
            )
            await asyncio.sleep(max(0.1, decided.DEFAULT_POLL_SECONDS - (time.monotonic() - cycle_start)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "DECIDED_MLB_PROP_SYNC_READY max_receive_skew_ms=%s read_only=true order_entry=false",
        MAX_PAIR_RECEIVE_SKEW_MS,
    )
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
