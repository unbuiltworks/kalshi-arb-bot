"""Bounded q=1 same-Gemini combo FOK executor using minimal guaranteed covers.

This is the live-capable successor to v1. It keeps every v1 arm/account/grid/depth/
recovery gate but changes conjunction coverage to the minimal exact identities:

  C(YES) + SUM(all required-leg complements) >= 1
  C(NO)  + any ONE canonical required leg    >= 1

For a NO combo fill, only the selected canonical required leg is hedged. That lowers
fees, capital use, and sequential execution surface; it does not make separate FOK
orders atomic. Resting combo orders remain forbidden because q=1 combo orders can
partially fill in 0.01 increments and cent-rounded fees break the >=3c/contract floor
for tiny partials.

A fully covered fill remains FILLED_NOT_SETTLED until authoritative settlement and
ledger reconciliation. This module is fail-closed and DISARMED unless the same fresh
campaign + arm-token + enable + deadline gates used by v1 all pass.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
import time
from typing import Any, Mapping, Sequence

import httpx

import src.gemini_combo_mm_fok_live_v1 as base
import src.gemini_extreme_combo_live_canary as c
from src.gemini_combo_mm_fok_preflight import _cover_directions
from src.gemini_kalshi_probe_transport import GeminiTradingSession

D = Decimal
QTY = D("1")
TARGET = base.TARGET
TAKER = base.TAKER
MAX_FINAL_SKEW_MS = base.MAX_FINAL_SKEW_MS
POLL_SECONDS = base.POLL_SECONDS
WATCH_SECONDS = base.WATCH_SECONDS
MAX_ATTEMPTS = base.MAX_ATTEMPTS
MAX_EXECUTED = base.MAX_EXECUTED
CATALOG_REFRESH_SECONDS = base.CATALOG_REFRESH_SECONDS
FINAL_BALANCE_BUFFER = base.FINAL_BALANCE_BUFFER

# These aliases are intentionally patchable by the nonce-safe outer entrypoint.
_parse_deadline = base._parse_deadline
_load_candidates = base._load_candidates
_active_orders_for_symbols = base._active_orders_for_symbols
_relevant_positions = base._relevant_positions
_available_usd = base._available_usd
_certify_dynamic_candidate = base._certify_dynamic_candidate
emit = base.emit


def _direction_hedges(spec: Mapping[str, Any], direction: str) -> tuple[str, list[dict[str, str]]]:
    covers = _cover_directions(list(spec.get("active_required_legs") or []))
    for cover in covers:
        if str(cover["name"]) == direction:
            return (
                str(cover["combo_outcome"]),
                [{"symbol": str(s), "outcome": str(o)} for s, o in cover["component_specs"]],
            )
    raise ValueError(f"unsupported minimal-cover direction {direction}")


async def _boot_preflight(campaign: str, candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    # v1 owns the audited account-state implementation; install the outer entrypoint's
    # nonce-safe active-order function before calling it.
    base._active_orders_for_symbols = _active_orders_for_symbols
    return await base._boot_preflight(campaign, candidates)


async def _fresh_economics(http: httpx.AsyncClient, spec: Mapping[str, Any], direction: str) -> dict[str, Any]:
    combo_outcome, hedge_specs = _direction_hedges(spec, direction)
    symbols = [str(spec["combo"]), *(x["symbol"] for x in hedge_specs)]
    rows = await asyncio.gather(*(base._book(http, sym) for sym in symbols))
    received = [x[2] for x in rows]
    skew_ms = D(max(received) - min(received)) / D("1000000") if received else D("999999")
    combo_http, combo_book, _ = rows[0]
    combo_top = c._outcome_ask(combo_book, combo_outcome)
    hedge_rows: list[dict[str, Any]] = []
    hedge_all_in = D("0")
    hedge_ok = True
    for hedge, (status, book, _) in zip(hedge_specs, rows[1:]):
        top = c._outcome_ask(book, hedge["outcome"])
        row: dict[str, Any] = {"symbol": hedge["symbol"], "outcome": hedge["outcome"], "http": status}
        if status != 200 or top is None:
            row["available"] = False
            hedge_ok = False
        else:
            p, depth = top
            fee = c._fee(TAKER, p, QTY)
            row.update({"available": True, "ask": str(p), "depth": str(depth), "fee": str(fee), "all_in": str(p + fee)})
            hedge_all_in += p + fee
            if depth < QTY:
                hedge_ok = False
        hedge_rows.append(row)

    out: dict[str, Any] = {
        "candidate": str(spec.get("name") or ""),
        "combo": str(spec["combo"]),
        "direction": direction,
        "combo_outcome": combo_outcome,
        "combo_http": combo_http,
        "quote_skew_ms": str(skew_ms),
        "hedges": hedge_rows,
        "hedge_all_in": str(hedge_all_in),
        "pass": False,
    }
    if combo_http != 200 or combo_top is None or not hedge_ok or skew_ms > MAX_FINAL_SKEW_MS:
        out["reason"] = "NO_FRESH_EXECUTABLE_COVER_BOOK"
        return out
    cp, cd = combo_top
    combo_fee = c._fee(TAKER, cp, QTY)
    all_in = cp + combo_fee + hedge_all_in
    locked = D("1") - all_in
    out.update(
        {
            "combo_ask": str(cp),
            "combo_depth": str(cd),
            "combo_fee": str(combo_fee),
            "all_in": str(all_in),
            "locked": str(locked),
            "pass": bool(cd >= QTY and locked >= TARGET),
            "reason": "EXECUTABLE_Q1_FOK_MINIMAL_COVER" if cd >= QTY and locked >= TARGET else "BELOW_DEPTH_OR_3C_FLOOR",
        }
    )
    return out


async def _execute_candidate(
    session: GeminiTradingSession,
    http: httpx.AsyncClient,
    spec: Mapping[str, Any],
    econ: Mapping[str, Any],
    *,
    campaign: str,
    attempt: int,
) -> dict[str, Any]:
    # v1 execution consumes the already-certified econ hedge list, so it is safe for
    # the one-leg NO cover. Synchronize its recovery reconciliation helper first.
    base._active_orders_for_symbols = _active_orders_for_symbols
    return await base._execute_candidate(session, http, spec, econ, campaign=campaign, attempt=attempt)


async def run() -> dict[str, Any]:
    campaign = os.getenv("GEMINI_COMBO_FOK_CAMPAIGN", "").strip()
    arm = os.getenv("GEMINI_COMBO_FOK_ARM_CAMPAIGN", "").strip()
    enabled = os.getenv("ENABLE_GEMINI_COMBO_FOK_LIVE", "").strip().upper() == "YES"
    deadline = _parse_deadline(os.getenv("GEMINI_COMBO_FOK_ARM_NOT_AFTER_UTC", ""))
    deadline_ok = bool(deadline and datetime.now(timezone.utc) <= deadline)
    armed = bool(enabled and campaign and arm == campaign and deadline_ok)

    async with httpx.AsyncClient(timeout=12.0) as public_http:
        catalog, candidates = await _load_candidates(public_http)
    preflight = await _boot_preflight(campaign, candidates)
    out: dict[str, Any] = {
        "status": "GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER",
        "campaign": campaign,
        "armed": armed,
        "deadline_ok": deadline_ok,
        "catalog": catalog,
        "preflight": preflight,
        "target_locked": str(TARGET),
        "max_final_skew_ms": str(MAX_FINAL_SKEW_MS),
        "max_attempts": MAX_ATTEMPTS,
        "max_executed_trades": MAX_EXECUTED,
        "candidate_count": len(candidates),
        "order_entry": False,
        "realized": False,
        "resting_combo_orders": False,
        "settlement_identities": ["C_YES+SUM(ALL_COMPLEMENTS)>=1", "C_NO+ANY_SINGLE_REQUIRED_LEG>=1"],
    }
    if not armed:
        out["classification"] = "DISARMED_PREFLIGHT_PASS" if preflight.get("pass") else "DISARMED_PREFLIGHT_BLOCKED"
        emit("GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER", out)
        return out
    if not preflight.get("pass"):
        out["classification"] = "LIVE_GATE_BLOCKED"
        emit("GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER", out)
        return out

    session = GeminiTradingSession()
    out["order_entry"] = True
    attempts = executed = 0
    started = time.monotonic()
    last_heartbeat = 0.0
    last_catalog_refresh = time.monotonic()
    try:
        await session.connect()
        async with httpx.AsyncClient(timeout=6.0) as http:
            while time.monotonic() - started < WATCH_SECONDS and attempts < MAX_ATTEMPTS and executed < MAX_EXECUTED:
                if time.monotonic() - last_catalog_refresh >= CATALOG_REFRESH_SECONDS:
                    catalog, candidates = await _load_candidates(http)
                    last_catalog_refresh = time.monotonic()

                potential: list[tuple[D, Mapping[str, Any], str]] = []
                combo_rows = await asyncio.gather(*(base._book(http, str(spec["combo"])) for spec in candidates))
                for spec, (status, book, _) in zip(candidates, combo_rows):
                    if status != 200:
                        continue
                    for cover in _cover_directions(list(spec.get("active_required_legs") or [])):
                        outcome = str(cover["combo_outcome"])
                        top = c._outcome_ask(book, outcome)
                        if top is not None and top[1] >= QTY:
                            potential.append((top[0], spec, str(cover["name"])))
                potential.sort(key=lambda x: x[0])

                for _combo_price, spec, direction in potential:
                    econ = await _fresh_economics(http, spec, direction)
                    if not econ.get("pass"):
                        continue
                    cert = await _certify_dynamic_candidate(http, spec)
                    if not cert.get("pass"):
                        emit(
                            "GEMINI_COMBO_MM_FOK_CERT_CHANGED",
                            {"candidate": spec.get("name"), "direction": direction, "certification": cert, "order_entry": False},
                        )
                        continue

                    # The chosen NO hedge must still be one exact canonical active required
                    # outcome after the metadata revalidation above. _direction_hedges derives
                    # it only from that unchanged spec; fail closed on any unexpected shape.
                    combo_outcome, chosen_hedges = _direction_hedges(spec, direction)
                    if combo_outcome == "NO" and len(chosen_hedges) != 1:
                        out["classification"] = "LIVE_HARD_HALT_INVALID_MINIMAL_NO_COVER"
                        emit("GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER", out)
                        return out

                    symbols = {str(spec["combo"]), *(str(x["symbol"]) for x in econ.get("hedges") or [])}
                    ph, pos = await _relevant_positions(symbols)
                    active_ok, active = await _active_orders_for_symbols(symbols)
                    bh, usd = await _available_usd()
                    required_cash = D(str(econ["all_in"])) + FINAL_BALANCE_BUFFER
                    if ph != 200 or pos or not active_ok or active or bh != 200 or usd < required_cash:
                        out["classification"] = "LIVE_HARD_HALT_ACCOUNT_OR_CAPITAL_STATE_CHANGED"
                        out["final_positions_count"] = len(pos)
                        out["final_active_orders_count"] = len(active)
                        out["final_balance_http"] = bh
                        out["final_available_usd"] = str(usd)
                        out["required_cash_with_buffer"] = str(required_cash)
                        emit("GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER", out)
                        return out

                    econ = await _fresh_economics(http, spec, direction)
                    if not econ.get("pass"):
                        continue

                    attempts += 1
                    emit(
                        "GEMINI_COMBO_MM_FOK_EXECUTABLE",
                        {
                            "campaign": campaign,
                            "attempt": attempts,
                            "candidate": spec.get("name"),
                            "direction": direction,
                            "economics": econ,
                            "classification": "EXECUTABLE",
                            "realized": False,
                        },
                    )
                    result = await _execute_candidate(session, http, spec, econ, campaign=campaign, attempt=attempts)
                    emit("GEMINI_COMBO_MM_FOK_ATTEMPT", {"campaign": campaign, "attempt": attempts, **result, "realized": False})
                    if result.get("classification") == "FILLED_NOT_SETTLED":
                        executed += 1
                        break
                    if str(result.get("classification") or "").startswith("LIVE_HARD_HALT"):
                        out["classification"] = result["classification"]
                        out["last_attempt"] = result
                        emit("GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER", out)
                        return out

                now = time.monotonic()
                if now - last_heartbeat >= 60:
                    emit(
                        "GEMINI_COMBO_MM_FOK_HEARTBEAT",
                        {
                            "campaign": campaign,
                            "seconds_alive": round(now - started, 1),
                            "candidate_count": len(candidates),
                            "potential_cover_sides": len(potential),
                            "attempts": attempts,
                            "executed": executed,
                            "order_entry": True,
                            "realized": False,
                        },
                    )
                    last_heartbeat = now
                if executed >= MAX_EXECUTED or attempts >= MAX_ATTEMPTS:
                    break
                await asyncio.sleep(POLL_SECONDS)
    finally:
        await session.close()

    out["attempts"] = attempts
    out["executed"] = executed
    out["classification"] = (
        "FILLED_NOT_SETTLED"
        if executed
        else ("WATCH_ATTEMPT_CAP_REACHED_NO_EXECUTION" if attempts >= MAX_ATTEMPTS else "WATCH_WINDOW_ENDED_NO_EXECUTION")
    )
    emit("GEMINI_COMBO_MM_FOK_LIVE_V3_MINIMAL_COVER", out)
    return out


if __name__ == "__main__":
    asyncio.run(run())
