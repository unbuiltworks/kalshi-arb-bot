"""Strict BTC 15m micro-live executor, disarmed by default.

This is the only bridge from the conservative 20 bp readiness stack to the existing
one-contract dual-FOK transport.  It intentionally requires ALL of the following:

* the exact 20 bp double-boundary gate held for the whole quote-lock episode;
* the original one-contract limits are still marketable after 100 ms;
* both entry and current cross-venue quote skew are <= 50 ms;
* both books are post-activation and <= 250 ms old;
* the exact original-limit payloads independently recompute to >= $0.03 after fees;
* process launched with --live;
* ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES; and
* a single-use arm-token file exists at the instant of submission.

The Railway service is intended to run without --live and without the env gate until
explicitly armed.  In that state it is a pure read-only scanner and cannot submit.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import time
import uuid
from typing import Any

from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src.gemini_kalshi_live_probe_guard import (
    DEFAULT_MIN_LOCKED_PROFIT,
    MAX_QUOTE_SKEW_MS,
    live_probe_authorized,
    validate_exact_probe_payloads,
)
from src.gemini_kalshi_live_service import (
    _arm_token_path,
    _armed_once,
    _consume_arm_token,
    _preflight,
)
from src.gemini_kalshi_probe_transport import (
    GeminiTradingSession,
    KalshiV2Executor,
    submit_dual_probe,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_micro_live_executor_20bp")
exact = readiness.exact
basis = readiness.basis

_LIVE_REQUESTED = False
_ENTRY_SKEW_MS: dict[tuple[str, str, int], int] = {}
_FEE_MULTIPLIER: dict[str, Decimal] = {}
_GEMINI_SESSION: GeminiTradingSession | None = None
_KALSHI_EXECUTOR: KalshiV2Executor | None = None
_EXECUTION_ATTEMPTED = False
_HARD_HALTED = False


def strict_execution_gate(
    *,
    delay_ms: int,
    readiness_20bp: bool,
    quantity: Decimal,
    locked_profit: Decimal,
    entry_quote_skew_ms: int | None,
    current_quote_skew_ms: int | None,
) -> bool:
    """Pure fail-closed gate shared by runtime and tests."""
    return bool(
        int(delay_ms) == 100
        and readiness_20bp
        and D(quantity) == D("1")
        and D(locked_profit) >= DEFAULT_MIN_LOCKED_PROFIT
        and entry_quote_skew_ms is not None
        and int(entry_quote_skew_ms) <= MAX_QUOTE_SKEW_MS
        and current_quote_skew_ms is not None
        and int(current_quote_skew_ms) <= MAX_QUOTE_SKEW_MS
    )


def build_original_limit_payloads(plan) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build FOK payloads at the exact limits whose worst-case P&L was modeled."""
    client_id = f"gk-20bp-{uuid.uuid4().hex[:20]}"
    if plan.direction == "KALSHI_YES_GEMINI_NO":
        gemini_outcome = "NO"
        gemini_price = D(plan.gemini_limit)
        kalshi_side = "bid"
        kalshi_yes_price = D(plan.kalshi_limit)
    elif plan.direction == "GEMINI_YES_KALSHI_NO":
        gemini_outcome = "YES"
        gemini_price = D(plan.gemini_limit)
        kalshi_side = "ask"
        # plan.kalshi_limit is the NO ask/cost; Kalshi V2 ask is YES-space.
        kalshi_yes_price = D("1") - D(plan.kalshi_limit)
    else:
        raise ValueError(f"unsupported direction={plan.direction}")

    if not (D("0.01") <= gemini_price <= D("0.99")):
        raise ValueError("Gemini original limit outside supported prediction range")
    if not (D("0.01") <= kalshi_yes_price <= D("0.99")):
        raise ValueError("Kalshi original YES-space limit outside supported range")

    gemini = {
        "symbol": plan.gemini_symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(gemini_price),
        "quantity": "1",
        "eventOutcome": gemini_outcome,
        "clientOrderId": client_id + "-g",
    }
    kalshi = {
        "ticker": plan.kalshi_ticker,
        "client_order_id": client_id + "-k",
        "side": kalshi_side,
        "count": "1.00",
        "price": str(kalshi_yes_price),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return gemini, kalshi


def _capture_plan_from_books(original):
    def capture(**kwargs):
        plan = original(**kwargs)
        candidate = kwargs.get("candidate")
        skew = getattr(candidate, "quote_skew_ms", None)
        key = (plan.asset, plan.direction, int(plan.opened_ms))
        if skew is not None:
            _ENTRY_SKEW_MS[key] = int(skew)
        return plan
    return capture


async def _strict_probe(*, feed, plan, active_episodes, delays_ms=(50, 100)) -> None:
    """Revalidate the original plan and optionally execute only at the 100 ms gate."""
    global _EXECUTION_ATTEMPTED, _HARD_HALTED
    previous = 0
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry20 = bool(readiness._PLAN_ENTRY_20BP.get(key, False))
    entry_skew_ms = _ENTRY_SKEW_MS.get(key)
    try:
        for delay_ms in delays_ms:
            await asyncio.sleep(max(0, delay_ms - previous) / 1000)
            previous = delay_ms
            now_ms = int(time.time() * 1000)
            g, k = feed.gemini, feed.kalshi
            same_window = bool(
                feed.active
                and feed.pair.gemini_symbol == plan.gemini_symbol
                and feed.pair.kalshi_ticker == plan.kalshi_ticker
            )
            checks = {
                "gemini_price_ok": False,
                "gemini_depth_ok": False,
                "kalshi_price_ok": False,
                "kalshi_depth_ok": False,
                "both_marketable": False,
            }
            gemini_age_ms = None
            kalshi_age_ms = None
            book_skew_ms = None
            post_activation_books = False
            strict_fresh = False
            if same_window and g is not None and k is not None:
                checks = exact.same_limits_marketable(plan, g, k)
                gemini_age_ms = max(0, now_ms - int(g.ts_ms))
                kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
                book_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
                post_activation_books = bool(
                    int(g.ts_ms) >= int(feed.boundary_ms)
                    and int(k.ts_ms) >= int(feed.boundary_ms)
                )
                strict_fresh = bool(
                    post_activation_books
                    and max(gemini_age_ms, kalshi_age_ms) <= 250
                    and book_skew_ms <= MAX_QUOTE_SKEW_MS
                )

            state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
            same_episode = readiness.guarded.same_original_episode(plan, active_episodes, state)
            snapshot = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
            gate20_now = bool(snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms"))
            whole20 = bool(same_episode and state and getattr(state, "all_events_gate_20bp", False))
            readiness20 = bool(
                entry20
                and same_episode
                and whole20
                and gate20_now
                and strict_fresh
                and checks["both_marketable"]
            )
            executable = strict_execution_gate(
                delay_ms=delay_ms,
                readiness_20bp=readiness20,
                quantity=D(plan.quantity),
                locked_profit=D(plan.entry_locked_profit),
                entry_quote_skew_ms=entry_skew_ms,
                current_quote_skew_ms=book_skew_ms,
            )

            payload = {
                "asset": plan.asset,
                "direction": plan.direction,
                "opened_ms": int(plan.opened_ms),
                "delay_ms": int(delay_ms),
                "quantity": str(plan.quantity),
                "gemini_symbol": plan.gemini_symbol,
                "kalshi_ticker": plan.kalshi_ticker,
                "original_gemini_limit": str(plan.gemini_limit),
                "original_kalshi_limit": str(plan.kalshi_limit),
                "entry_locked_quote_profit": str(plan.entry_locked_profit),
                "entry_quote_skew_ms": entry_skew_ms,
                "current_quote_skew_ms": book_skew_ms,
                "entry_double_boundary_20bp": entry20,
                "same_window": same_window,
                "same_original_episode": same_episode,
                **checks,
                "post_activation_books": post_activation_books,
                "gemini_age_ms": gemini_age_ms,
                "kalshi_age_ms": kalshi_age_ms,
                "strict_fresh_250ms_and_50ms_skew": strict_fresh,
                "double_boundary_20bp_now": gate20_now,
                "double_boundary_20bp_held_through_episode": whole20,
                "strict_micro_live_executable_20bp": executable,
                "live_cli_requested": _LIVE_REQUESTED,
                "live_env_gate": live_probe_authorized(_LIVE_REQUESTED),
                "single_use_arm_token_present": _arm_token_path().is_file(),
                "execution_attempted": _EXECUTION_ATTEMPTED,
                "hard_halted": _HARD_HALTED,
            }
            logger.info("CRYPTO_MICRO_LIVE_EXECUTOR_GATE %s", json.dumps(payload, separators=(",", ":")))

            if not executable:
                continue

            fee_multiplier = _FEE_MULTIPLIER.get(plan.asset)
            if fee_multiplier is None:
                logger.error("CRYPTO_MICRO_LIVE_FAIL_CLOSED reason=missing_fee_multiplier asset=%s", plan.asset)
                continue
            gemini_payload, kalshi_payload = build_original_limit_payloads(plan)
            try:
                recomputed = validate_exact_probe_payloads(
                    gemini_payload,
                    kalshi_payload,
                    modeled_locked_profit=D(plan.entry_locked_profit),
                    kalshi_fee_multiplier=fee_multiplier,
                    quote_skew_ms=int(max(entry_skew_ms or 10**9, book_skew_ms or 10**9)),
                )
            except Exception as exc:
                logger.error(
                    "CRYPTO_MICRO_LIVE_FAIL_CLOSED reason=exact_payload_validation type=%s error=%s",
                    type(exc).__name__, str(exc)[:220],
                )
                continue

            armed = _armed_once(_LIVE_REQUESTED, _arm_token_path())
            logger.info("CRYPTO_MICRO_LIVE_ACTIONABLE %s", json.dumps({
                **payload,
                "recomputed_locked_profit_dollars": str(recomputed),
                "armed": armed,
                "trade_authorized": armed and not _EXECUTION_ATTEMPTED and not _HARD_HALTED,
                "order_entry": armed and not _EXECUTION_ATTEMPTED and not _HARD_HALTED,
            }, separators=(",", ":")))

            if not armed or _EXECUTION_ATTEMPTED or _HARD_HALTED:
                continue
            if _GEMINI_SESSION is None or _KALSHI_EXECUTOR is None:
                logger.error("CRYPTO_MICRO_LIVE_FAIL_CLOSED reason=execution_sessions_not_warmed")
                continue
            if not _consume_arm_token(_arm_token_path()):
                logger.error("CRYPTO_MICRO_LIVE_FAIL_CLOSED reason=arm_token_race")
                continue

            _EXECUTION_ATTEMPTED = True
            result = await submit_dual_probe(
                _GEMINI_SESSION,
                _KALSHI_EXECUTOR,
                gemini_payload,
                kalshi_payload,
                modeled_locked_profit=D(plan.entry_locked_profit),
                kalshi_fee_multiplier=fee_multiplier,
                quote_skew_ms=int(max(entry_skew_ms, book_skew_ms)),
                cli_live=_LIVE_REQUESTED,
            )
            logger.warning("CRYPTO_MICRO_LIVE_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
            if result.get("hard_halt_required"):
                _HARD_HALTED = True
                logger.critical("CRYPTO_MICRO_LIVE_HARD_HALT outcome=%s", result.get("outcome"))
    finally:
        _ENTRY_SKEW_MS.pop(key, None)


async def _with_execution_sessions(original_collect_all, **kwargs):
    """Warm private execution sessions only when both live software gates are on."""
    global _GEMINI_SESSION, _KALSHI_EXECUTOR
    if not live_probe_authorized(_LIVE_REQUESTED):
        logger.info(
            "CRYPTO_MICRO_LIVE_EXECUTOR_DISARMED live_cli=%s env_gate=false token_present=%s",
            _LIVE_REQUESTED, _arm_token_path().is_file(),
        )
        return await original_collect_all(**kwargs)

    gemini = GeminiTradingSession()
    kalshi = KalshiV2Executor()
    _GEMINI_SESSION = gemini
    _KALSHI_EXECUTOR = kalshi
    try:
        await gemini.connect()
        preflight = await _preflight(
            kalshi,
            live_requested=_LIVE_REQUESTED,
            token_path=_arm_token_path(),
        )
        logger.warning("CRYPTO_MICRO_LIVE_PREFLIGHT %s", json.dumps(preflight, separators=(",", ":")))
        return await original_collect_all(**kwargs)
    finally:
        _GEMINI_SESSION = None
        _KALSHI_EXECUTOR = None
        await gemini.close()
        await kalshi.close()


def install_executor() -> None:
    """Install strict 20 bp readiness first, then only strengthen its execution edge."""
    readiness.install()

    # Capture entry quote skew from the same candidate that produced the exact plan.
    original_plan = exact._plan_from_books
    exact._plan_from_books = _capture_plan_from_books(original_plan)
    exact._probe = _strict_probe

    # Capture the exact fee multiplier already supplied to the scanner's fee model.
    prewarmed_base = exact.safe.base
    original_collect_asset = prewarmed_base.collect_asset_prewarmed

    async def collect_asset_capture_fee(**kwargs):
        fee_meta = kwargs.get("fee_meta")
        asset = str(kwargs.get("asset") or "").upper()
        if fee_meta is not None and asset:
            _FEE_MULTIPLIER[asset] = D(str(fee_meta.fee_multiplier))
        return await original_collect_asset(**kwargs)

    prewarmed_base.collect_asset_prewarmed = collect_asset_capture_fee

    # Keep private trading sessions warm only in explicitly live software mode.
    hot = exact.hot
    original_collect_all = hot.collect_all

    async def collect_all_with_sessions(**kwargs):
        return await _with_execution_sessions(original_collect_all, **kwargs)

    hot.collect_all = collect_all_with_sessions


def main() -> None:
    global _LIVE_REQUESTED
    # Parse only the dangerous flag here; the proven readiness parser handles all
    # scanner arguments. Remove --live before handing argv to it.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live", action="store_true")
    live_args, remaining = parser.parse_known_args()
    _LIVE_REQUESTED = bool(live_args.live)
    sys.argv = [sys.argv[0], *remaining]

    install_executor()
    logger.info(
        "CRYPTO_MICRO_LIVE_EXECUTOR_ENABLED strict_delay_ms=100 min_double_boundary_bps=20 "
        "entry_and_current_max_skew_ms=50 quantity=1 min_profit=0.03 live_requested=%s env_gate=%s",
        _LIVE_REQUESTED,
        live_probe_authorized(_LIVE_REQUESTED),
    )
    # Do not call readiness.main(): it would reinstall the read-only probe over ours.
    readiness.guarded.main()


if __name__ == "__main__":
    main()
