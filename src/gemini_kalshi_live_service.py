"""Bounded Gemini/Kalshi BTC execution service.

The service is safe-by-default:
- BTC 15-minute only.
- Exactly one contract per venue.
- Requires >= $0.03 exact modeled after-fee edge.
- Requires <= 50 ms quote skew and >= 10 ms persistence.
- Persistent authenticated Gemini trading session is opened before candidate watch.
- Kalshi executor is pinned to production and connection-prewarmed.
- Live order entry additionally requires ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES.
- Default live-attempt cap is 1. Increase only after reviewing realized probes.
- Any confirmed one-leg fill or unresolved submission ambiguity hard-halts the service.

When disarmed, this process remains a read-only candidate watcher and logs what it
would have attempted. It never silently transitions from disarmed to armed.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from typing import Any

from src.gemini_kalshi_btc_probe_candidate import watch
from src.gemini_kalshi_live_probe_guard import live_probe_authorized
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor, submit_dual_probe
from src.gemini_prediction_auth import private_rest_request

D = Decimal


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name, str(default)).strip()
    value = float(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _write_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
        fh.flush()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


async def _gemini_available_usd() -> Decimal:
    response = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("unexpected Gemini balances response")
    for row in rows:
        if str(row.get("currency") or "").upper() == "USD":
            return D(str(row.get("available") or "0"))
    return D("0")


async def _gemini_terms_ok() -> bool:
    response = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
    response.raise_for_status()
    body = response.json()
    return bool(body.get("hasAcceptedLatest"))


async def _preflight(kalshi: KalshiV2Executor, *, live_requested: bool) -> dict[str, Any]:
    status = await kalshi.client.get("/exchange/status")
    status.raise_for_status()
    balance = await kalshi.client.get("/portfolio/balance")
    balance.raise_for_status()
    kalshi_available = D(str(balance.json().get("balance") or "0")) / D("100")
    terms_ok = await _gemini_terms_ok()
    gemini_available = await _gemini_available_usd()
    report = {
        "mode": "LIVE_SERVICE_PREFLIGHT",
        "live_requested": bool(live_requested),
        "armed": live_probe_authorized(bool(live_requested)),
        "gemini_terms_accepted": terms_ok,
        "gemini_usd_available": str(gemini_available),
        "kalshi_usd_available": str(kalshi_available),
        "kalshi_production_authenticated": True,
    }
    if not terms_ok:
        raise RuntimeError("Gemini Prediction Markets terms are not accepted")
    if live_probe_authorized(bool(live_requested)):
        if gemini_available < D("1"):
            raise RuntimeError("Gemini available USD is below $1")
        if kalshi_available < D("1"):
            raise RuntimeError("Kalshi available balance is below $1")
    return report


async def run(*, live_requested: bool, log_path: Path) -> int:
    max_attempts = _env_int("LIVE_PROBE_MAX_ATTEMPTS", 1, minimum=1)
    persistence_ms = _env_int("LIVE_PROBE_MIN_PERSISTENCE_MS", 10, minimum=0)
    watch_seconds = _env_float("LIVE_PROBE_WATCH_SECONDS", 900.0, minimum=1.0)
    cooldown_s = _env_float("LIVE_PROBE_COOLDOWN_SECONDS", 30.0, minimum=0.0)
    min_expiry_remaining_s = _env_float("LIVE_PROBE_MIN_EXPIRY_REMAINING_SECONDS", 0.5, minimum=0.0)
    max_candidate_age_ms = _env_int("LIVE_PROBE_MAX_CANDIDATE_AGE_MS", 100, minimum=1)

    gemini = GeminiTradingSession()
    kalshi = KalshiV2Executor()
    attempts = 0
    try:
        # Establish both private transports before watching so candidate-to-send time
        # does not include an authentication/TLS connection setup round-trip.
        await gemini.connect()
        preflight = await _preflight(kalshi, live_requested=live_requested)
        _write_jsonl(log_path, {"ts": time.time(), **preflight})
        # Prewarm Kalshi's persistent HTTP connection.
        warm = await kalshi.client.get("/exchange/status")
        warm.raise_for_status()

        while attempts < max_attempts:
            candidate_started_ns = time.time_ns()
            candidate = await watch(seconds=watch_seconds, persistence_ms=persistence_ms)
            candidate_returned_ns = time.time_ns()
            if not candidate.get("qualified"):
                _write_jsonl(log_path, {
                    "ts": time.time(),
                    "mode": "NO_QUALIFIED_CANDIDATE",
                    "candidate": candidate,
                    "attempts": attempts,
                })
                continue

            # The watcher returns immediately after the qualifying event. This age
            # ceiling prevents a scheduler/network stall from turning an old quote
            # into an order.
            observed_ns = int(candidate.get("candidate_observed_ns") or candidate_started_ns)
            candidate_age_ms = (candidate_returned_ns - observed_ns) / 1_000_000
            expiry = _parse_dt(str(candidate["window_end_utc"]))
            seconds_to_expiry = (expiry - datetime.now(timezone.utc)).total_seconds()
            stale_reason = None
            if candidate_age_ms > max_candidate_age_ms:
                stale_reason = "candidate_age_exceeded"
            elif seconds_to_expiry < min_expiry_remaining_s:
                stale_reason = "too_close_to_expiry"

            if stale_reason:
                _write_jsonl(log_path, {
                    "ts": time.time(),
                    "mode": "STALE_CANDIDATE_SKIPPED",
                    "reason": stale_reason,
                    "candidate_age_ms": candidate_age_ms,
                    "seconds_to_expiry": seconds_to_expiry,
                    "candidate": candidate,
                    "attempts": attempts,
                })
                continue

            if not live_probe_authorized(live_requested):
                _write_jsonl(log_path, {
                    "ts": time.time(),
                    "mode": "DISARMED_QUALIFIED_CANDIDATE",
                    "candidate_age_ms": candidate_age_ms,
                    "seconds_to_expiry": seconds_to_expiry,
                    "candidate": candidate,
                    "attempts": attempts,
                    "order_entry": False,
                })
                await asyncio.sleep(min(5.0, cooldown_s if cooldown_s > 0 else 5.0))
                continue

            execution = await submit_dual_probe(
                gemini,
                kalshi,
                candidate["gemini_payload"],
                candidate["kalshi_payload"],
                modeled_locked_profit=D(str(candidate["modeled_locked_profit_dollars"])),
                kalshi_fee_multiplier=D(str(candidate["kalshi_fee_multiplier"])),
                quote_skew_ms=int(candidate["quote_skew_ms"]),
                cli_live=live_requested,
            )
            attempts += 1
            _write_jsonl(log_path, {
                "ts": time.time(),
                "mode": "LIVE_PROBE_RESULT",
                "attempt": attempts,
                "candidate_age_ms": candidate_age_ms,
                "seconds_to_expiry_at_send": seconds_to_expiry,
                "candidate": candidate,
                "execution": execution,
            })

            if execution.get("hard_halt_required"):
                _write_jsonl(log_path, {
                    "ts": time.time(),
                    "mode": "HARD_HALT",
                    "reason": execution.get("outcome"),
                    "attempt": attempts,
                })
                return 70

            if attempts < max_attempts and cooldown_s > 0:
                await asyncio.sleep(cooldown_s)

        _write_jsonl(log_path, {
            "ts": time.time(),
            "mode": "PROBE_CAP_REACHED",
            "attempts": attempts,
            "max_attempts": max_attempts,
        })
        return 0
    finally:
        await gemini.close()
        await kalshi.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="request live mode; env gate is still separately required")
    parser.add_argument("--log", type=Path, default=Path("logs/gemini_kalshi_live_service.jsonl"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(live_requested=args.live, log_path=args.log)))


if __name__ == "__main__":
    main()
