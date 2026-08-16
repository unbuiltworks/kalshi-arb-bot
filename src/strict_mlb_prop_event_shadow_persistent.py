"""Persistent, read-only MLB prop hot scanner.

This is the next step after the full-subscription hot scanner:

* discover exact cross-venue counterparts and fee metadata once per long session,
* subscribe the complete deterministic shard immediately (participation is not a
  subscription prerequisite),
* refresh MLB participation proofs independently in the background,
* evaluate only participation-cleared rows from fresh in-memory venue books,
* evaluate a newly-opened participation gate immediately from the current books,
* keep a quiet five-second market interval from recycling the session, and
* report trigger -> evaluation latency and gate coverage in heartbeat summaries.

There is no order manager or order transport in this module. HOT_READY is research
output only and never authorizes an order.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import strict_mlb_prop_event_shadow as hot

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_event_shadow_persistent")
PARTICIPATION_REFRESH_S = 15.0
MIN_SESSION_S = 1800.0


@dataclass
class WatchedCandidate:
    candidate: hot.base.StrictCandidate
    fee_multiplier: Decimal
    fee_type: str
    proof: dict[str, Any]
    eligible: bool = False

    @property
    def key(self) -> str:
        return self.candidate.key


class PersistentStats(hot.Stats):
    def __init__(self) -> None:
        super().__init__()
        self.gated_updates_ignored = 0
        self.gate_open_evaluations = 0
        self._decision_ms: deque[float] = deque(maxlen=5000)

    def observe_decision_ms(self, value: float) -> None:
        self._decision_ms.append(max(0.0, value))

    def payload(
        self,
        poly: hot.EventingPolyBookStore,
        kalshi: hot.EventingKalshiCrossBooks,
    ) -> dict[str, Any]:
        payload = super().payload(poly, kalshi)
        payload.update({
            "poly_stored_books": poly.quote_count,
            "gated_updates_ignored": self.gated_updates_ignored,
            "gate_open_evaluations": self.gate_open_evaluations,
        })
        if not self._decision_ms:
            payload.update({
                "trigger_to_eval_samples": 0,
                "trigger_to_eval_avg_ms": None,
                "trigger_to_eval_p95_ms": None,
                "trigger_to_eval_max_ms": None,
            })
            return payload
        values = sorted(self._decision_ms)
        n = len(values)
        p95 = values[min(n - 1, max(0, int(0.95 * n) - 1))]
        payload.update({
            "trigger_to_eval_samples": n,
            "trigger_to_eval_avg_ms": round(sum(values) / n, 3),
            "trigger_to_eval_p95_ms": round(p95, 3),
            "trigger_to_eval_max_ms": round(values[-1], 3),
        })
        return payload


async def _discover_watch(
    http: httpx.AsyncClient,
    shard_index: int,
    shard_count: int,
) -> tuple[list[WatchedCandidate], dict[str, Any]]:
    """Discover counterpart identity/fee metadata without consulting MLB game state."""
    poly_markets, kalshi_events = await asyncio.gather(
        hot.base._fetch_poly_mlb(http), hot.base._fetch_kalshi_events(http)
    )
    candidates = hot.base.discover_strict_candidates(poly_markets, kalshi_events)

    series_meta: dict[str, dict[str, Any]] = {}
    for series in sorted(set(hot.base.FAMILY_SERIES.values())):
        body, error = await hot.v3._fetch_optional(
            http, f"{hot.base.KALSHI}/series/{series}"
        )
        if body is not None and error is None:
            series_meta[series] = dict(body.get("series") or {})

    watched: list[WatchedCandidate] = []
    fee_blocked = 0
    for candidate in candidates:
        meta = series_meta.get(hot.base.FAMILY_SERIES[candidate.family]) or {}
        fee_type = str(meta.get("fee_type") or "")
        multiplier = hot.base._d(meta.get("fee_multiplier"))
        if (
            fee_type not in {"quadratic", "quadratic_with_maker_fees"}
            or multiplier is None
            or not (Decimal("0") <= multiplier <= Decimal("100"))
        ):
            fee_blocked += 1
            continue
        if hot._shard(candidate, shard_count) != shard_index:
            continue
        watched.append(
            WatchedCandidate(
                candidate=candidate,
                fee_multiplier=multiplier,
                fee_type=fee_type,
                proof={},
            )
        )

    watched.sort(key=lambda row: row.key)
    summary = {
        "strict_candidates": len(candidates),
        "fee_blocked": fee_blocked,
        "watched_shard_candidates": len(watched),
        "hot_shard_candidates": 0,
        "participation_cleared": 0,
        "participation_pending": len(watched),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "discovery_mode": "IDENTITY_AND_FEES_ONLY",
        "participation_refresh_s": PARTICIPATION_REFRESH_S,
    }
    return watched, summary


async def _refresh_participation_once(
    http: httpx.AsyncClient,
    rows: list[WatchedCandidate],
) -> tuple[int, list[WatchedCandidate]]:
    games = await hot.fetch_game_boxscores(
        http, {row.candidate.game_date for row in rows}
    )
    newly_cleared: list[WatchedCandidate] = []
    for row in rows:
        proof = hot.participation_proof(
            family=row.candidate.family,
            player_name=row.candidate.player,
            poly_slug=row.candidate.poly_slug,
            games=games,
        )
        assessment = hot.base.assess_rule_equivalence(row.candidate.family, proof)
        proof_payload = hot.proof_dict(proof)
        if assessment.settlement_equivalent_now:
            if not row.eligible:
                newly_cleared.append(row)
            row.eligible = True
            row.proof = proof_payload
        elif not row.eligible:
            # Never revoke a previously proven participation gate on a transient MLB
            # API failure. Participation itself is monotonic once proven.
            row.proof = proof_payload
    return len(games), newly_cleared


async def _participation_loop(
    rows: list[WatchedCandidate],
    poly: hot.EventingPolyBookStore,
    kalshi: hot.EventingKalshiCrossBooks,
    active: dict[str, hot.ActiveSignal],
    stats: PersistentStats,
    discovery: dict[str, Any],
    max_quote_age_ms: int,
) -> None:
    dates = sorted({row.candidate.game_date for row in rows})
    async with httpx.AsyncClient(timeout=7.0) as http:
        while True:
            started = time.perf_counter()
            try:
                game_count, newly_cleared = await _refresh_participation_once(http, rows)
                cleared = sum(1 for row in rows if row.eligible)
                discovery.update({
                    "hot_shard_candidates": cleared,
                    "participation_cleared": cleared,
                    "participation_pending": len(rows) - cleared,
                    "mlb_games_with_boxscores": game_count,
                    "participation_refresh_ok": True,
                    "last_participation_refresh_ms": int(time.time() * 1000),
                })
                if newly_cleared:
                    logger.info(
                        "MLB_PROP_HOT_GATE_OPEN %s",
                        json.dumps({
                            "newly_cleared": len(newly_cleared),
                            "participation_cleared": cleared,
                            "watched_shard_candidates": len(rows),
                            "dates": dates,
                        }, separators=(",", ":")),
                    )
                    # Do not wait for a price tick after the rule gate opens. Evaluate
                    # the current cached books immediately; stale/missing books fail
                    # closed inside _evaluate.
                    for row in newly_cleared:
                        trigger = hot.BookUpdate(
                            venue="participation",
                            market_id=row.candidate.poly_slug,
                            received_ms=int(time.time() * 1000),
                            mono_ns=time.perf_counter_ns(),
                            kind="gate_open",
                        )
                        stats.gate_open_evaluations += 1
                        await hot._evaluate(
                            row,
                            trigger,
                            poly,
                            kalshi,
                            max_quote_age_ms,
                            active,
                            stats,
                        )
                        stats.observe_decision_ms(
                            (time.perf_counter_ns() - trigger.mono_ns) / 1_000_000
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                discovery.update({
                    "participation_refresh_ok": False,
                    "last_participation_refresh_error": str(exc)[:240],
                })
                logger.warning("MLB_PROP_HOT_GATE_REFRESH_ERROR %s", exc)

            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(1.0, PARTICIPATION_REFRESH_S - elapsed))


def _task_failure(task: asyncio.Task[Any], name: str) -> RuntimeError:
    if task.cancelled():
        return RuntimeError(f"{name} market-data task was cancelled")
    exc = task.exception()
    if exc is not None:
        return RuntimeError(f"{name} market-data task exited: {exc}")
    return RuntimeError(f"{name} market-data task exited unexpectedly")


async def run_session(args) -> None:
    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        raise ValueError("shard-index must be in [0, shard-count)")

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("Polymarket market-data credentials are required")

    async with httpx.AsyncClient(timeout=20.0) as http:
        rows, discovery = await _discover_watch(
            http, args.shard_index, args.shard_count
        )
    if not rows:
        logger.warning(
            "MLB_PROP_HOT_EMPTY %s",
            json.dumps(discovery, separators=(",", ":")),
        )
        await asyncio.sleep(args.empty_sleep_s)
        return

    # No REST book crawl is required to enter the hot set. WebSocket market data is
    # the source of truth; a missing book simply fails closed until its first update.
    poly = hot.EventingPolyBookStore()
    slug_map = {row.candidate.poly_slug: row for row in rows}
    ticker_map = {row.candidate.kalshi_ticker: row for row in rows}
    kalshi = hot.EventingKalshiCrossBooks(list(ticker_map))
    poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
    poly_task = asyncio.create_task(
        hot.run_poly_ws(poly_client, poly, list(slug_map)), name="poly_ws"
    )
    kalshi_task = asyncio.create_task(kalshi.run(), name="kalshi_ws")

    deadline = time.monotonic() + args.startup_timeout_s
    while time.monotonic() < deadline:
        if poly_task.done() or kalshi_task.done():
            break
        if poly.connected and kalshi.initialized_count > 0:
            break
        await asyncio.sleep(0.025)

    if poly_task.done():
        failure = _task_failure(poly_task, "Polymarket")
        kalshi_task.cancel()
        await kalshi.close()
        try:
            poly_client.close()
        except Exception:
            pass
        await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
        raise failure
    if kalshi_task.done():
        failure = _task_failure(kalshi_task, "Kalshi")
        poly_task.cancel()
        await kalshi.close()
        try:
            poly_client.close()
        except Exception:
            pass
        await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
        raise failure
    if not poly.connected or kalshi.initialized_count == 0:
        poly_task.cancel()
        kalshi_task.cancel()
        await kalshi.close()
        try:
            poly_client.close()
        except Exception:
            pass
        await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)
        raise RuntimeError(
            f"persistent hot startup timeout poly_connected={poly.connected} "
            f"kalshi_initialized={kalshi.initialized_count}/{len(rows)}"
        )

    session_seconds = max(float(args.session_seconds), MIN_SESSION_S)
    discovery.update({
        "subscribed_hot_pairs": len(rows),
        "live_hot_pairs": len(rows),
        "seeded_poly_books": 0,
        "seed_coverage_pct": 0.0,
        "rest_seed_is_gate": False,
        "rest_seed_enabled": False,
        "kalshi_initialized_at_ready": kalshi.initialized_count,
        "max_quote_age_ms": args.max_quote_age_ms,
        "session_seconds": session_seconds,
        "persistent_hot_layer": True,
    })
    hot._drain(poly.updates)
    hot._drain(kalshi.updates)
    logger.info(
        "MLB_PROP_HOT_READY_FEED %s",
        json.dumps(
            {**discovery, "read_only": True, "order_entry": False},
            separators=(",", ":"),
        ),
    )

    stats = PersistentStats()
    active: dict[str, hot.ActiveSignal] = {}
    heartbeat = asyncio.create_task(
        hot._heartbeat(stats, poly, kalshi, active, discovery),
        name="hot_heartbeat",
    )
    participation = asyncio.create_task(
        _participation_loop(
            rows,
            poly,
            kalshi,
            active,
            stats,
            discovery,
            args.max_quote_age_ms,
        ),
        name="participation_refresh",
    )

    end_at = time.monotonic() + session_seconds
    try:
        while time.monotonic() < end_at:
            if poly_task.done():
                raise _task_failure(poly_task, "Polymarket")
            if kalshi_task.done():
                raise _task_failure(kalshi_task, "Kalshi")
            if participation.done():
                raise _task_failure(participation, "Participation")

            try:
                trigger = await asyncio.wait_for(
                    hot._next_update(poly, kalshi), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue

            stats.triggers += 1
            row = (
                slug_map.get(trigger.market_id)
                if trigger.venue == "polymarket"
                else ticker_map.get(trigger.market_id)
            )
            if row is None:
                continue
            if not row.eligible:
                stats.gated_updates_ignored += 1
                continue

            await hot._evaluate(
                row,
                trigger,
                poly,
                kalshi,
                args.max_quote_age_ms,
                active,
                stats,
            )
            stats.observe_decision_ms(
                (time.perf_counter_ns() - trigger.mono_ns) / 1_000_000
            )
    finally:
        heartbeat.cancel()
        participation.cancel()
        poly_task.cancel()
        kalshi_task.cancel()
        await kalshi.close()
        try:
            poly_client.close()
        except Exception:
            pass
        await asyncio.gather(
            heartbeat,
            participation,
            poly_task,
            kalshi_task,
            return_exceptions=True,
        )


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(message)s",
        stream=sys.stdout,
        force=True,
    )
    args = hot.parse_args()
    hot.run_session = run_session
    asyncio.run(hot.run(args))


if __name__ == "__main__":
    main()
