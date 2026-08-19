"""Read-only v2 MLB terminal-total shadow with close-state retention and doubleheader-safe matching.

This wraps the original terminal shadow without weakening any execution gate. It keeps
recently discovered contracts for a bounded TTL after a venue drops them from active
cold discovery, and it matches MLB Final evidence by both participants and scheduled
start time so same-day doubleheaders do not become ambiguous or cross-wired.

No authenticated venue endpoint, order transport, approval mutation, or live execution
code is imported here. Every signal remains research-only.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

import httpx

from src.mlb_terminal_safe_state import (
    FinalGameEvidence,
    TerminalFinalTracker,
    TerminalProof,
    fetch_mlb_schedule,
    final_games_from_schedule,
    parse_terminal_total_key,
    terminal_shadow_annotation,
)
from src.mlb_terminal_total_shadow import (
    TerminalContract,
    _priced_for_contract,
    _target_books,
    _winner_edges,
    discover_terminal_universe,
)
from src.universal_prediction_quant import find_cross_venue_locks

logger = logging.getLogger("kalshi_arb.mlb_terminal_total_shadow_v2")
D = Decimal
DEFAULT_RETENTION_SECONDS = 6 * 60 * 60
DEFAULT_START_TOLERANCE_SECONDS = 20 * 60


@dataclass(frozen=True)
class RetainedContract:
    contract: TerminalContract
    last_seen_ms: int


def retain_contracts(
    retained: Mapping[str, RetainedContract],
    fresh: Mapping[str, TerminalContract],
    *,
    now_ms: int,
    retention_ms: int,
) -> dict[str, RetainedContract]:
    """Merge fresh discovery into a bounded cache and expire old missing contracts."""
    if retention_ms < 0:
        raise ValueError("retention_ms must be non-negative")
    out: dict[str, RetainedContract] = {}
    for key, row in retained.items():
        age = now_ms - int(row.last_seen_ms)
        if 0 <= age <= retention_ms:
            out[key] = row
    for key, contract in fresh.items():
        out[key] = RetainedContract(contract=contract, last_seen_ms=now_ms)
    return out


def _parse_game_date_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def game_start_by_pk(payload: Any) -> dict[int, int]:
    """Extract MLB gamePk -> scheduled gameDate milliseconds from a schedule payload."""
    if not isinstance(payload, Mapping):
        return {}
    out: dict[int, int] = {}
    for date_row in payload.get("dates") or []:
        if not isinstance(date_row, Mapping):
            continue
        for game in date_row.get("games") or []:
            if not isinstance(game, Mapping):
                continue
            try:
                game_pk = int(game.get("gamePk"))
            except (TypeError, ValueError):
                continue
            start_ms = _parse_game_date_ms(game.get("gameDate"))
            if start_ms is not None:
                out[game_pk] = start_ms
    return out


def match_final_game_by_start(
    games: list[FinalGameEvidence],
    start_by_pk: Mapping[int, int],
    settlement_key: str,
    scheduled_start_ms: int,
    *,
    tolerance_ms: int = DEFAULT_START_TOLERANCE_SECONDS * 1000,
) -> FinalGameEvidence | None:
    """Match participants plus scheduled start; fail closed on ties or missing start proof."""
    if tolerance_ms < 0:
        raise ValueError("tolerance_ms must be non-negative")
    spec = parse_terminal_total_key(settlement_key)
    candidates: list[tuple[int, FinalGameEvidence]] = []
    for row in games:
        if row.participants != spec.participants:
            continue
        mlb_start = start_by_pk.get(row.game_pk)
        if mlb_start is None:
            continue
        skew = abs(int(mlb_start) - int(scheduled_start_ms))
        if skew <= tolerance_ms:
            candidates.append((skew, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1]


async def _schedule_snapshot(dates: tuple[str, ...]) -> tuple[list[FinalGameEvidence], dict[int, int], list[dict[str, str]]]:
    if not dates:
        return [], {}, []
    results = await asyncio.gather(*(fetch_mlb_schedule(date) for date in dates), return_exceptions=True)
    finals: list[FinalGameEvidence] = []
    starts: dict[int, int] = {}
    errors: list[dict[str, str]] = []
    for date, result in zip(dates, results):
        if isinstance(result, Exception):
            errors.append({"date": date, "error": f"{type(result).__name__}:{str(result)[:160]}"})
            continue
        finals.extend(final_games_from_schedule(result))
        starts.update(game_start_by_pk(result))
    return finals, starts, errors


def _dates(contracts: Mapping[str, RetainedContract]) -> tuple[str, ...]:
    return tuple(sorted({row.contract.game_date_et for row in contracts.values()}))


async def run(
    *,
    discovery_seconds: float,
    poll_seconds: float,
    min_stable_seconds: float,
    min_observations: int,
    retention_seconds: float,
    start_tolerance_seconds: float,
    once: bool,
) -> None:
    trackers: dict[str, TerminalFinalTracker] = {}
    retained: dict[str, RetainedContract] = {}
    retention_ms = int(retention_seconds * 1000)
    tolerance_ms = int(start_tolerance_seconds * 1000)

    async with httpx.AsyncClient(timeout=10.0) as http:
        fresh = await discover_terminal_universe(http)
        now_ms = int(time.time() * 1000)
        retained = retain_contracts(retained, fresh.contracts, now_ms=now_ms, retention_ms=retention_ms)
        next_discovery = time.monotonic() + discovery_seconds
        logger.info("TERMINAL_MLB_TOTAL_V2_READY %s", json.dumps({
            **fresh.diagnostics,
            "retained_contracts": len(retained),
            "retention_seconds": retention_seconds,
            "start_tolerance_seconds": start_tolerance_seconds,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str))

        while True:
            cycle_start = time.monotonic()
            if time.monotonic() >= next_discovery:
                try:
                    fresh = await discover_terminal_universe(http)
                    now_ms = int(time.time() * 1000)
                    retained = retain_contracts(retained, fresh.contracts, now_ms=now_ms, retention_ms=retention_ms)
                    logger.info("TERMINAL_MLB_TOTAL_V2_DISCOVERY %s", json.dumps({
                        **fresh.diagnostics,
                        "retained_contracts": len(retained),
                    }, separators=(",", ":"), default=str))
                except Exception as exc:
                    logger.warning("terminal v2 discovery refresh failed: %s", exc)
                next_discovery = time.monotonic() + discovery_seconds

            # Expire cache even between discovery refreshes.
            now_ms = int(time.time() * 1000)
            retained = retain_contracts(retained, {}, now_ms=now_ms, retention_ms=retention_ms)
            finals, start_by_pk, schedule_errors = await _schedule_snapshot(_dates(retained))
            final_contracts: list[TerminalContract] = []
            proofs: dict[str, TerminalProof] = {}
            for key, retained_row in retained.items():
                contract = retained_row.contract
                final = match_final_game_by_start(
                    finals,
                    start_by_pk,
                    key,
                    contract.scheduled_start_ms,
                    tolerance_ms=tolerance_ms,
                )
                if final is None:
                    continue
                tracker = trackers.setdefault(
                    key,
                    TerminalFinalTracker(
                        min_stable_ms=int(min_stable_seconds * 1000),
                        min_observations=min_observations,
                    ),
                )
                proof = tracker.observe(key, final, now_ms=now_ms)
                proofs[key] = proof
                final_contracts.append(contract)

            positive_pair_locks = 0
            positive_winner_edges = 0
            stable_final_keys = sum(1 for proof in proofs.values() if proof.stable)
            book_errors: list[dict[str, str]] = []
            if final_contracts:
                pbooks, kbooks, gcontracts, gemini_received, book_errors = await _target_books(http, final_contracts)
                for contract in final_contracts:
                    proof = proofs[contract.key]
                    rows = _priced_for_contract(
                        contract,
                        pbooks=pbooks,
                        kbooks=kbooks,
                        gcontracts=gcontracts,
                        gemini_received=gemini_received,
                    )
                    locks = find_cross_venue_locks(
                        [row.quote for row in rows],
                        min_unit_profit=D("0"),
                        max_quote_skew_ms=None,
                        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
                    ) if len({row.quote.venue for row in rows}) >= 2 else []
                    locks = [lock for lock in locks if D(str(lock.unit_profit)) > 0]
                    winner_edges = _winner_edges(rows, proof)
                    positive_pair_locks += len(locks)
                    positive_winner_edges += len(winner_edges)
                    if not locks and not winner_edges:
                        continue
                    logger.info("TERMINAL_MLB_TOTAL_V2_SIGNAL %s", json.dumps({
                        "ts_ms": now_ms,
                        "settlement_key": contract.key,
                        "venues": list(contract.venues),
                        "scheduled_start_ms": contract.scheduled_start_ms,
                        "terminal_proof": terminal_shadow_annotation(proof),
                        "quotes": [row.as_dict() for row in rows],
                        "pair_locks": [lock.as_dict() for lock in locks[:10]],
                        "known_winner_edges": winner_edges[:10],
                        "stable_terminal_candidate": proof.stable,
                        "execution_eligible": False,
                        "approved_for_execution": False,
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":"), default=str))

            logger.info("TERMINAL_MLB_TOTAL_V2_SUMMARY %s", json.dumps({
                "fresh_contracts": len(fresh.contracts),
                "retained_contracts": len(retained),
                "mlb_final_games": len(finals),
                "final_contract_keys": len(final_contracts),
                "stable_final_keys": stable_final_keys,
                "positive_pair_locks": positive_pair_locks,
                "positive_known_winner_edges": positive_winner_edges,
                "schedule_errors": schedule_errors[:5],
                "book_errors": book_errors[:10],
                "cycle_ms": round((time.monotonic() - cycle_start) * 1000, 1),
                "min_stable_seconds": min_stable_seconds,
                "min_observations": min_observations,
                "retention_seconds": retention_seconds,
                "start_tolerance_seconds": start_tolerance_seconds,
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
            if once:
                return
            elapsed = time.monotonic() - cycle_start
            await asyncio.sleep(max(0.05, poll_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-seconds", type=float, default=60.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--min-stable-seconds", type=float, default=60.0)
    parser.add_argument("--min-observations", type=int, default=3)
    parser.add_argument("--retention-seconds", type=float, default=float(DEFAULT_RETENTION_SECONDS))
    parser.add_argument("--start-tolerance-seconds", type=float, default=float(DEFAULT_START_TOLERANCE_SECONDS))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(
        discovery_seconds=max(10.0, args.discovery_seconds),
        poll_seconds=max(0.5, args.poll_seconds),
        min_stable_seconds=max(0.0, args.min_stable_seconds),
        min_observations=max(1, args.min_observations),
        retention_seconds=max(0.0, args.retention_seconds),
        start_tolerance_seconds=max(0.0, args.start_tolerance_seconds),
        once=bool(args.once),
    ))


if __name__ == "__main__":
    main()
