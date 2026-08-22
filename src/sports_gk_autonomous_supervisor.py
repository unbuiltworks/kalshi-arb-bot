"""Persistent bounded Gemini/Kalshi MLB live-probe supervisor.

Consolidates Hits, Home Runs, Total Bases and Pitcher Strikeouts into one executor.
The existing one-shot canary remains the order engine; this supervisor refreshes its
inventory by launching bounded one-shot epochs with unique run IDs.

Risk remains deliberately tiny:
- cumulative sports live-probe capital ceiling is $5 until explicitly changed;
- durable Command Center reservation is required before every order attempt;
- fresh cached Gemini AND Kalshi balance capacity is required before the hot path;
- any ledger outage or stale venue-capital snapshot fails closed;
- any previous ambiguous/one-leg outcome halts future submissions;
- one contract per leg, >=3c after-fee edge, <=50ms skew, <=100ms books and 50ms
  exact-price survival are inherited from the proven canary;
- Kalshi prop discovery uses bounded REST series snapshots when the account WS slot
  is unavailable, with an exact depth=1 REST refresh for the second live revalidation;
- no auto-reset of the $5 ceiling by day/date/restart.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from src import mlb_gk_multifamily_discovery_patch as discovery_patch
from src import mlb_gk_prop_live_multifamily_rule_risk_canary as canary
from src import sports_trade_reporting as reporting
from src.kalshi_prop_rest_books import KalshiPropRestBooks
from src.mlb_live_participation import candidate_game, participation_proof
from src.sports_live_budget_guard import (
    BudgetExhausted,
    CUMULATIVE_BUDGET,
    budget_used_from_trades,
    install as install_budget,
)
from src.sports_trade_reporting import install as install_reporting
from src.sports_venue_capital_allocator import (
    CachedVenueCapitalAllocator,
    VenueCapitalUnavailable,
    install as install_venue_capital,
)

logger = logging.getLogger("kalshi_arb.sports_gk_autonomous_supervisor")
D = Decimal

HITS = "baseball_player_hits"
HOME_RUNS = "baseball_player_home_runs"
TOTAL_BASES = "baseball_player_total_bases"
STRIKEOUTS = "baseball_player_strikeouts"
ALL_FAMILIES = frozenset({HITS, HOME_RUNS, TOTAL_BASES, STRIKEOUTS})
SELECTED_SERIES = ("KXMLBHIT", "KXMLBHR", "KXMLBTB", "KXMLBKS")
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
NOT_BEFORE_ENV = "SPORTS_SUPERVISOR_NOT_BEFORE_UTC"
EMPTY_INVENTORY_ERROR = "no eligible HR/TB/K direct-orientation Gemini/Kalshi props"


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _not_before() -> datetime:
    raw = os.getenv(NOT_BEFORE_ENV, "").strip()
    return _parse_time(raw) if raw else datetime.now(timezone.utc)


def _is_empty_inventory_error(exc: BaseException) -> bool:
    """Only the known no-current-market condition gets the slow idle retry."""
    return isinstance(exc, RuntimeError) and str(exc).strip() == EMPTY_INVENTORY_ERROR


def _d(value: Any) -> D:
    try:
        return D(str(value))
    except Exception:
        return D("0")


def _budget_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return non-sensitive rows contributing to the cumulative sports budget."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        if not str(row.get("strategy") or "").startswith("sports_gk_"):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        actual = _d(row.get("capital_filled"))
        reserved = _d(metadata.get("reserved_usd"))
        status = str(row.get("status") or "")
        effective_reserved = D("0") if status == "CAPITAL_RELEASED" else reserved
        contributing = max(actual, effective_reserved)
        if contributing <= 0:
            continue
        out.append({
            "trade_id": str(row.get("trade_id") or ""),
            "status": status,
            "market": str(row.get("market") or ""),
            "capital_filled": str(actual),
            "reserved_usd": str(reserved),
            "budget_contribution_usd": str(contributing),
        })
    return out


def _install_unified_families() -> None:
    discovery_patch.SELECTED_SERIES = SELECTED_SERIES
    discovery_patch.install()
    canary.SUPPORTED_FAMILIES = ALL_FAMILIES
    canary.EventingKalshiCrossBooks = KalshiPropRestBooks

    def participation_ok(row: Any, games: dict[Any, Any]):
        game_key = candidate_game(row.game_slug)
        game = games.get(game_key) if game_key is not None else None
        proof = participation_proof(
            family=row.family,
            player_name=row.player,
            poly_slug=row.game_slug,
            games=games,
        )
        status = str((game or {}).get("status") or "")
        if row.family == STRIKEOUTS:
            proven = bool(proof.starting_pitcher and proof.batter_faced_recorded and proof.source)
        elif row.family in {HITS, HOME_RUNS, TOTAL_BASES}:
            proven = bool(proof.started and proof.plate_appearance_recorded and proof.source)
        else:
            proven = False
        return bool(status.lower() == "live" and proven), proof, status or None

    canary._participation_ok = participation_ok


async def _trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [x for x in body if isinstance(x, dict)]


def _hard_halt_reason(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        if not str(row.get("strategy") or "").startswith("sports_gk_"):
            continue
        status = str(row.get("status") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        if status in {"AMBIGUOUS_SUBMISSION", "ONE_LEG_FILLED"} or bool(execution.get("hard_halt_required")):
            return f"trade_id={row.get('trade_id')} status={status or execution.get('outcome')}"
    return None


def _run_id(epoch: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%m%d-%H%M%S")
    return f"sup-{stamp}-{epoch % 100:02d}"


async def _wait_until_activation() -> None:
    not_before = _not_before()
    while True:
        remaining = (not_before - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        logger.info("SPORTS_SUPERVISOR_WAITING not_before=%s remaining_seconds=%.1f", not_before.isoformat(), remaining)
        await asyncio.sleep(min(30.0, remaining))


async def run_forever(*, epoch_seconds: float, cooldown_seconds: float) -> None:
    _install_unified_families()
    allocator = CachedVenueCapitalAllocator(
        refresh_seconds=float(os.getenv("SPORTS_CAPITAL_REFRESH_SECONDS", "2")),
    )
    install_reporting(
        canary,
        strategy="sports_gk_autonomous_rule_risk",
        run_id_env="MLB_GK_MULTI_RUN_ID",
    )
    install_budget(
        canary,
        strategy="sports_gk_autonomous_rule_risk",
        run_id_env="MLB_GK_MULTI_RUN_ID",
    )
    install_venue_capital(
        canary,
        allocator,
        run_id_env="MLB_GK_MULTI_RUN_ID",
    )

    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "").strip().upper() != "YES":
        raise RuntimeError("global live probe gate is not enabled")
    if os.getenv("MLB_GK_MULTI_LIVE_RULE_RISK", "").strip().upper() != "YES":
        raise RuntimeError("sports supervisor live rule-risk gate is not enabled")

    await _wait_until_activation()
    initial_capital = await allocator.refresh_once()
    logger.warning(
        "SPORTS_SUPERVISOR_ACTIVE families=%s cumulative_budget_usd=%s epoch_seconds=%s "
        "gemini_available=%s kalshi_available=%s venue_free_buffer=%s capital_snapshot_max_age_ms=%s",
        sorted(ALL_FAMILIES),
        CUMULATIVE_BUDGET,
        epoch_seconds,
        initial_capital.gemini_available,
        initial_capital.kalshi_available,
        allocator.free_buffer,
        allocator.max_snapshot_age_ms,
    )
    capital_task = asyncio.create_task(allocator.run())

    epoch = 0
    budget_notice_sent = False
    halt_notice_sent = False
    empty_retry_seconds = max(30.0, float(os.getenv("SPORTS_EMPTY_INVENTORY_RETRY_SECONDS", "120")))
    try:
        while True:
            epoch += 1
            try:
                rows = await _trades()
                halt = _hard_halt_reason(rows)
                if halt:
                    if not halt_notice_sent:
                        await reporting._push(
                            "SPORTS AUTO-TRADING HALTED",
                            f"A prior live probe requires manual review.\n{halt}\nNo new sports orders will be submitted.",
                        )
                        halt_notice_sent = True
                    logger.error("SPORTS_SUPERVISOR_HARD_HALT %s", halt)
                    await asyncio.sleep(300.0)
                    continue

                used = budget_used_from_trades(rows)
                breakdown = _budget_breakdown(rows)
                if breakdown:
                    logger.warning(
                        "SPORTS_SUPERVISOR_BUDGET_BREAKDOWN used=%s ceiling=%s contributors=%s",
                        used,
                        CUMULATIVE_BUDGET,
                        breakdown,
                    )
                if used >= CUMULATIVE_BUDGET:
                    if not budget_notice_sent:
                        await reporting._push(
                            "Sports $5 probe budget reached",
                            f"Cumulative deployed/reserved sports live-probe capital: ${used}.\nCeiling: ${CUMULATIVE_BUDGET}.\nScanning may continue, but no more live submissions are authorized.",
                        )
                        budget_notice_sent = True
                    logger.info("SPORTS_SUPERVISOR_BUDGET_EXHAUSTED used=%s ceiling=%s", used, CUMULATIVE_BUDGET)
                    await asyncio.sleep(300.0)
                    continue

                run_id = _run_id(epoch)
                arm_path = Path(f"/tmp/{run_id}.arm")
                try:
                    arm_path.unlink()
                except FileNotFoundError:
                    pass
                os.environ["MLB_GK_MULTI_RUN_ID"] = run_id
                os.environ["LIVE_PROBE_ARM_TOKEN_FILE"] = str(arm_path)
                os.environ["MLB_GK_MULTI_FAMILIES"] = ",".join(sorted(ALL_FAMILIES))
                os.environ["MLB_GK_MULTI_ARM_NOT_AFTER_UTC"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=max(60.0, epoch_seconds + 30.0))
                ).isoformat()

                logger.info(
                    "SPORTS_SUPERVISOR_EPOCH_START epoch=%s run_id=%s used=%s remaining=%s capital_snapshot_age_ms=%s",
                    epoch,
                    run_id,
                    used,
                    CUMULATIVE_BUDGET - used,
                    allocator.snapshot.age_ms() if allocator.snapshot else None,
                )
                rc = await canary.run(live_requested=True, watch_seconds=epoch_seconds)
                logger.info("SPORTS_SUPERVISOR_EPOCH_END epoch=%s run_id=%s rc=%s", epoch, run_id, rc)
                if rc == 70:
                    logger.error("SPORTS_SUPERVISOR_CHILD_HARD_HALT run_id=%s", run_id)
                    halt_notice_sent = False
                    await asyncio.sleep(1.0)
                    continue
            except BudgetExhausted as exc:
                logger.warning("SPORTS_SUPERVISOR_BUDGET_BLOCK %s", exc)
                budget_notice_sent = False
                await asyncio.sleep(60.0)
                continue
            except VenueCapitalUnavailable as exc:
                logger.warning("SPORTS_SUPERVISOR_VENUE_CAPITAL_BLOCK %s", exc)
                await asyncio.sleep(30.0)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if _is_empty_inventory_error(exc):
                    logger.info(
                        "SPORTS_SUPERVISOR_IDLE_NO_INVENTORY retry_s=%.1f used=%s remaining=%s",
                        empty_retry_seconds,
                        used if "used" in locals() else None,
                        CUMULATIVE_BUDGET - used if "used" in locals() else None,
                    )
                    await asyncio.sleep(empty_retry_seconds)
                    continue
                logger.exception("SPORTS_SUPERVISOR_EPOCH_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(max(5.0, cooldown_seconds))
    finally:
        capital_task.cancel()
        await asyncio.gather(capital_task, return_exceptions=True)
        await allocator.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoch-seconds", type=float, default=600.0)
    parser.add_argument("--cooldown-seconds", type=float, default=15.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_forever(epoch_seconds=max(30.0, args.epoch_seconds), cooldown_seconds=max(5.0, args.cooldown_seconds)))


if __name__ == "__main__":
    main()
