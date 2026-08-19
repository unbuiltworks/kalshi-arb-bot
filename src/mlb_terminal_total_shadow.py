"""Read-only post-final MLB game-total shadow across Kalshi/Gemini/Polymarket US.

The normal hot scanner has already shown real pregame price locks, but exceptional
settlement tails differ by venue. This service explores a narrower state: MLB has
repeatedly reported the exact game Final with an unchanged score, while at least
two matching full-game half-run total contracts are still quoteable.

It keeps discovery slow and price checks targeted. No authenticated endpoints,
approval writes, order clients, or execution code are imported.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx

from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import NormalizedSide, normalize_gemini_mlb, normalize_polymarket_mlb
from src.mlb_terminal_safe_state import (
    TerminalFinalTracker,
    TerminalProof,
    final_games_from_schedule,
    fetch_mlb_schedule,
    match_final_game,
    terminal_shadow_annotation,
)
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import KALSHI, POLY, kalshi_top_book, poly_top_book
from src.three_venue_mlb_price_scan import _gemini_contracts, _kalshi_series
from src.three_venue_mlb_snapshot_pricing import _series_multipliers
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import GEMINI, fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.mlb_terminal_total_shadow")
D = Decimal
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TerminalContract:
    key: str
    sides: dict[str, NormalizedSide]
    game_date_et: str
    scheduled_start_ms: int
    kalshi_fee_multiplier: Decimal | None

    @property
    def venues(self) -> tuple[str, ...]:
        return tuple(sorted(self.sides))


@dataclass(frozen=True)
class TerminalUniverse:
    contracts: dict[str, TerminalContract]
    diagnostics: dict[str, Any]

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(sorted({row.game_date_et for row in self.contracts.values()}))


async def _get_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    started = int(time.time() * 1000)
    try:
        response = await http.get(url, params=params)
        received = int(time.time() * 1000)
        if response.status_code != 200:
            return None, received, f"http_{response.status_code}"
        body = response.json()
        return (dict(body) if isinstance(body, Mapping) else None), received, ""
    except Exception as exc:
        return None, int(time.time() * 1000), f"{type(exc).__name__}:{str(exc)[:120]}:{started}"


def _game_date_et(start_ms: int) -> str:
    return datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone(ET).date().isoformat()


def _build_grouped(rows: list[NormalizedSide]) -> dict[str, dict[str, list[NormalizedSide]]]:
    grouped: dict[str, dict[str, list[NormalizedSide]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.identity.market_family != "game_total":
            continue
        grouped[row.contract_key][row.identity.venue].append(row)
    return grouped


async def discover_terminal_universe(http: httpx.AsyncClient) -> TerminalUniverse:
    gemini, poly_raw, kalshi = await asyncio.gather(
        fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http)
    )
    poly = full_game_only_events(poly_raw)
    rows = normalize_gemini_mlb(gemini) + normalize_polymarket_mlb(poly) + normalize_kalshi_mlb_eastern(kalshi)
    grouped = _build_grouped(rows)
    ktoseries = _kalshi_series(kalshi)

    provisional: list[tuple[str, dict[str, NormalizedSide], int]] = []
    ambiguous = 0
    for key, by_venue in grouped.items():
        chosen: dict[str, NormalizedSide] = {}
        bad = False
        for venue, candidates in by_venue.items():
            unique = {(row.venue_market_id, row.side): row for row in candidates}
            if len(unique) != 1:
                bad = True
                break
            chosen[venue] = next(iter(unique.values()))
        if bad or len(chosen) < 2:
            if bad:
                ambiguous += 1
            continue
        starts = [row.identity.event.scheduled_start_ms for row in chosen.values()]
        if max(starts) - min(starts) > 5 * 60 * 1000:
            ambiguous += 1
            continue
        provisional.append((key, chosen, min(starts)))

    series = sorted({
        ktoseries.get(side.venue_market_id, "")
        for _, chosen, _ in provisional
        for venue, side in chosen.items()
        if venue == "kalshi" and ktoseries.get(side.venue_market_id, "")
    })
    fee_errors: list[dict[str, str]] = []
    multipliers = await _series_multipliers(http, asyncio.Semaphore(8), series, fee_errors)

    contracts: dict[str, TerminalContract] = {}
    fee_blocked = 0
    for key, chosen, start_ms in provisional:
        multiplier = None
        kside = chosen.get("kalshi")
        if kside is not None:
            multiplier = multipliers.get(ktoseries.get(kside.venue_market_id, ""))
            if multiplier is None:
                # K/G/P combinations that do not use Kalshi remain useful; only
                # remove Kalshi from this contract when its fee metadata is absent.
                chosen = dict(chosen)
                chosen.pop("kalshi", None)
                fee_blocked += 1
                if len(chosen) < 2:
                    continue
        contracts[key] = TerminalContract(
            key=key,
            sides=chosen,
            game_date_et=_game_date_et(start_ms),
            scheduled_start_ms=start_ms,
            kalshi_fee_multiplier=multiplier,
        )

    diagnostics = {
        "raw_events": {"gemini": len(gemini), "polymarket": len(poly_raw), "kalshi": len(kalshi)},
        "terminal_game_total_contracts": len(contracts),
        "ambiguous_rejected": ambiguous,
        "kalshi_fee_blocked": fee_blocked,
        "dates": sorted({row.game_date_et for row in contracts.values()}),
        "fee_errors": fee_errors[:10],
        "read_only": True,
        "order_entry": False,
    }
    return TerminalUniverse(contracts=contracts, diagnostics=diagnostics)


async def _fetch_current_gemini(http: httpx.AsyncClient) -> tuple[dict[str, Mapping[str, Any]], int, str]:
    body, received, error = await _get_json(
        http,
        f"{GEMINI}/v1/prediction-markets/events",
        params={"status": "active", "sport": "baseball", "limit": 500, "offset": 0},
    )
    return _gemini_contracts([dict(row) for row in (body or {}).get("data") or (body or {}).get("events") or [] if isinstance(row, Mapping)]), received, error


async def _target_books(
    http: httpx.AsyncClient,
    contracts: list[TerminalContract],
) -> tuple[
    dict[str, tuple[Any, int]],
    dict[str, tuple[Any, int]],
    dict[str, Mapping[str, Any]],
    int,
    list[dict[str, str]],
]:
    poly_ids = sorted({side.venue_market_id for c in contracts for venue, side in c.sides.items() if venue == "polymarket"})
    kalshi_ids = sorted({side.venue_market_id for c in contracts for venue, side in c.sides.items() if venue == "kalshi"})
    g_task = _fetch_current_gemini(http)
    p_tasks = [_get_json(http, f"{POLY}/v1/markets/{market_id}/book") for market_id in poly_ids]
    k_tasks = [_get_json(http, f"{KALSHI}/markets/{ticker}/orderbook", params={"depth": 1}) for ticker in kalshi_ids]
    g_result, p_results, k_results = await asyncio.gather(
        g_task,
        asyncio.gather(*p_tasks),
        asyncio.gather(*k_tasks),
    )
    gcontracts, gemini_received, gemini_error = g_result
    errors: list[dict[str, str]] = []
    if gemini_error:
        errors.append({"venue": "gemini", "market_id": "active_events", "error": gemini_error})

    pbooks: dict[str, tuple[Any, int]] = {}
    for market_id, (body, received, error) in zip(poly_ids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "polymarket", "market_id": market_id, "error": error or "book_unparseable"})
        else:
            pbooks[market_id] = (book, received)

    kbooks: dict[str, tuple[Any, int]] = {}
    for market_id, (body, received, error) in zip(kalshi_ids, k_results):
        book = kalshi_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "kalshi", "market_id": market_id, "error": error or "book_unparseable"})
        else:
            kbooks[market_id] = (book, received)
    return pbooks, kbooks, gcontracts, gemini_received, errors


def _priced_for_contract(
    contract: TerminalContract,
    *,
    pbooks: dict[str, tuple[Any, int]],
    kbooks: dict[str, tuple[Any, int]],
    gcontracts: dict[str, Mapping[str, Any]],
    gemini_received: int,
) -> list[PricedQuote]:
    out: list[PricedQuote] = []
    for venue, side in contract.sides.items():
        row: PricedQuote | None = None
        if venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is not None:
                row = make_priced_quote(
                    side,
                    book,
                    received_ms=gemini_received,
                    depth_source="gemini_active_events_terminal_shadow",
                    category="sports_mlb_game_terminal",
                )
        elif venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]
            row = make_priced_quote(
                side,
                book,
                received_ms=received,
                depth_source="polymarket_public_book_terminal_shadow",
                category="sports_mlb_game_terminal",
            )
        elif venue == "kalshi" and side.venue_market_id in kbooks and contract.kalshi_fee_multiplier is not None:
            book, received = kbooks[side.venue_market_id]
            row = make_priced_quote(
                side,
                book,
                received_ms=received,
                kalshi_fee_multiplier=contract.kalshi_fee_multiplier,
                depth_source="kalshi_public_orderbook_terminal_shadow",
                category="sports_mlb_game_terminal",
            )
        if row is not None:
            out.append(row)
    return out


def _winner_edges(rows: list[PricedQuote], proof: TerminalProof) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if proof.normalized_winning_outcome == "YES":
            cost = D(row.quote.yes_cost)
            raw = row.raw_yes_ask
            fee = row.yes_fee
        else:
            cost = D(row.quote.no_cost)
            raw = row.raw_no_ask
            fee = row.no_fee
        edge = D("1") - cost
        if edge <= 0:
            continue
        out.append({
            "venue": row.quote.venue,
            "market_id": row.quote.market_id,
            "known_winning_outcome": proof.normalized_winning_outcome,
            "raw_price": str(raw),
            "fee": str(fee),
            "all_in_cost": str(cost),
            "known_payout_if_terminal_proof_holds": "1",
            "gross_edge": str(edge),
            "depth": str(row.quote.yes_depth if proof.normalized_winning_outcome == "YES" else row.quote.no_depth),
            "received_ms": row.quote.received_ms,
            "depth_source": row.depth_source,
            "execution_eligible": False,
        })
    out.sort(key=lambda x: D(x["gross_edge"]), reverse=True)
    return out


async def _schedule_finals(dates: tuple[str, ...]) -> tuple[list[Any], list[dict[str, str]]]:
    if not dates:
        return [], []
    tasks = [fetch_mlb_schedule(date) for date in dates]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    finals: list[Any] = []
    errors: list[dict[str, str]] = []
    for date, result in zip(dates, results):
        if isinstance(result, Exception):
            errors.append({"date": date, "error": f"{type(result).__name__}:{str(result)[:160]}"})
        else:
            finals.extend(final_games_from_schedule(result))
    return finals, errors


async def run(
    *,
    discovery_seconds: float,
    poll_seconds: float,
    min_stable_seconds: float,
    min_observations: int,
    once: bool,
) -> None:
    trackers: dict[str, TerminalFinalTracker] = {}
    async with httpx.AsyncClient(timeout=10.0) as http:
        universe = await discover_terminal_universe(http)
        next_discovery = time.monotonic() + discovery_seconds
        logger.info("TERMINAL_MLB_TOTAL_READY %s", json.dumps(universe.diagnostics, separators=(",", ":"), default=str))

        while True:
            cycle_start = time.monotonic()
            if time.monotonic() >= next_discovery:
                try:
                    universe = await discover_terminal_universe(http)
                    logger.info("TERMINAL_MLB_TOTAL_DISCOVERY %s", json.dumps(universe.diagnostics, separators=(",", ":"), default=str))
                except Exception as exc:
                    logger.warning("terminal discovery refresh failed: %s", exc)
                next_discovery = time.monotonic() + discovery_seconds

            finals, schedule_errors = await _schedule_finals(universe.dates)
            now_ms = int(time.time() * 1000)
            final_contracts: list[TerminalContract] = []
            proofs: dict[str, TerminalProof] = {}
            for key, contract in universe.contracts.items():
                final = match_final_game(finals, key)
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
                    payload = {
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
                    }
                    logger.info("TERMINAL_MLB_TOTAL_SIGNAL %s", json.dumps(payload, separators=(",", ":"), default=str))

            summary = {
                "universe_contracts": len(universe.contracts),
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
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }
            logger.info("TERMINAL_MLB_TOTAL_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
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
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(
        discovery_seconds=max(10.0, args.discovery_seconds),
        poll_seconds=max(0.5, args.poll_seconds),
        min_stable_seconds=max(0.0, args.min_stable_seconds),
        min_observations=max(1, args.min_observations),
        once=bool(args.once),
    ))


if __name__ == "__main__":
    main()
