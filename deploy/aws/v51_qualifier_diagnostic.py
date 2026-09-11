"""Read-only Gemini BTC5 q10 V5.1 qualifier diagnostic.

Purpose
-------
Answer *why* a V5.1 canary would or would not reach order entry without placing
orders. Every poll is classified as one of:

- MARKET_GATE: no suitable current reward market / competition / expiry window
- ECONOMIC_GATE: price, reward distance, or pair-edge economics fail
- SAFETY_GATE: the standing <= $0.30 rescue invariant fails
- VENUE_DATA_ERROR: Gemini event/reward/depth transport or payload failure
- QUALIFIED: the current book would pass the pre-entry V5.1 gates

This program is deliberately credential-free and order-entry-free. It is intended
to run inside the exact frozen V5.1 release image so it exercises the same market
discovery, fee model, and rescue policy as the live candidate.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal as D
import json
import os
import time
from typing import Any, Mapping

import httpx

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_rescue_policy as rescue

WAIT_SECONDS = float(os.getenv("V51_DIAG_WAIT_SECONDS", "360"))
POLL_SECONDS = float(os.getenv("V51_DIAG_POLL_SECONDS", "5"))
DEPTH_ATTEMPTS = int(os.getenv("V51_DIAG_DEPTH_ATTEMPTS", "3"))
DEPTH_TIMEOUT = float(os.getenv("V51_DIAG_DEPTH_TIMEOUT_SECONDS", "4"))
DEPTH_RETRY_DELAY = float(os.getenv("V51_DIAG_DEPTH_RETRY_DELAY_SECONDS", "0.25"))
Q = D("10")
MAX_RESCUE_LOSS = D("0.30")
MIN_PAIR_EDGE = D("0.50")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
# The frozen V5.1 qualifier still enforces the probe's one-sided premium cap
# (V5.1's install() replaces only the executor entry gate, not probe._candidate),
# so the diagnostic must apply it too or it can report QUALIFIED where the live
# qualifier would return NO_CANDIDATE. Read it from the probe to stay in lockstep.
MAX_ONE_SIDED_PREMIUM = D(str(getattr(probe, "MAX_ONE_SIDED_PREMIUM", "6.50")))
RELEASE_COMMIT = os.getenv("V51_DIAG_RELEASE_COMMIT", "unknown")


def clean_error(exc: BaseException) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:240],
    }


async def reward_detail(ticker: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": "arb-v51-readonly-diagnostic/1.0"},
        ) as http:
            cfg_r, ev_r = await asyncio.gather(
                http.get(f"{probe.REST}/v1/prediction-markets/liquidity-rewards/config"),
                http.get(
                    f"{probe.REST}/v1/prediction-markets/liquidity-rewards/events",
                    params={"category": "Crypto", "limit": 100, "offset": 0},
                ),
            )
    except Exception as exc:
        return None, {
            "reason": "REWARD_TRANSPORT_ERROR",
            **clean_error(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    detail: dict[str, Any] = {
        "config_status": cfg_r.status_code,
        "events_status": ev_r.status_code,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if cfg_r.status_code != 200 or ev_r.status_code != 200:
        return None, {"reason": "REWARD_HTTP_STATUS", **detail}

    try:
        cfg = cfg_r.json() if cfg_r.content else {}
        payload = ev_r.json() if ev_r.content else {}
    except Exception as exc:
        return None, {"reason": "REWARD_JSON_ERROR", **clean_error(exc), **detail}

    rows = payload.get("data") or payload.get("events") or payload.get("liquidity_reward_events") or []
    target = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_ticker = str(row.get("event_ticker") or row.get("eventTicker") or "")
        if event_ticker.upper() == ticker.upper():
            target = dict(row)
            break

    detail.update(
        {
            "program_enabled": bool(cfg.get("enabled")),
            "reward_rows": len(rows),
            "event_found": target is not None,
        }
    )
    if not bool(cfg.get("enabled")):
        return None, {"reason": "REWARD_PROGRAM_DISABLED", **detail}
    if target is None:
        return None, {"reason": "REWARD_EVENT_MISSING", **detail}
    return {"config": cfg, "event": target}, detail


async def reliable_top(symbol: str) -> tuple[tuple[D, D, D, D] | None, dict[str, Any]]:
    """Retry only fresh independent websocket subscriptions; never use stale fallback."""
    failures: list[dict[str, Any]] = []
    for attempt in range(1, DEPTH_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            top = await probe.base.fresh_top(symbol, timeout=DEPTH_TIMEOUT)
            return top, {
                "attempt": attempt,
                "failures": failures,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        except (RuntimeError, asyncio.TimeoutError, OSError) as exc:
            failures.append(
                {
                    "attempt": attempt,
                    **clean_error(exc),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            if attempt < DEPTH_ATTEMPTS:
                await asyncio.sleep(DEPTH_RETRY_DELAY)
    return None, {"attempt": DEPTH_ATTEMPTS, "failures": failures}


async def evaluate_once() -> tuple[tuple[Any, dict[str, Any], dict[str, D]] | None, dict[str, Any]]:
    started = time.perf_counter()
    try:
        event = await probe.base.discover_btc5m()
    except Exception as exc:
        return None, {
            "classification": "VENUE_DATA_ERROR",
            "reason": "EVENT_DISCOVERY_ERROR",
            **clean_error(exc),
        }

    if event is None:
        return None, {"classification": "MARKET_GATE", "reason": "NO_ACTIVE_BTC5M_EVENT"}

    common = {
        "ticker": event.ticker,
        "symbol": event.symbol,
        "seconds_to_expiry": round(event.seconds_to_expiry, 3),
        "price_increment": str(event.price_increment),
    }
    if not (probe.MIN_SECONDS_TO_EXPIRY <= event.seconds_to_expiry <= probe.MAX_SECONDS_TO_EXPIRY):
        return None, {"classification": "MARKET_GATE", "reason": "EXPIRY_WINDOW", **common}

    reward, reward_meta = await reward_detail(event.ticker)
    if reward is None:
        venue_reasons = {"REWARD_TRANSPORT_ERROR", "REWARD_HTTP_STATUS", "REWARD_JSON_ERROR"}
        classification = "VENUE_DATA_ERROR" if reward_meta["reason"] in venue_reasons else "MARKET_GATE"
        return None, {"classification": classification, **common, **reward_meta}

    row = reward["event"]
    makers_raw = row.get("qualifying_maker_count") if "qualifying_maker_count" in row else row.get("qualifyingMakerCount")
    try:
        makers = int(makers_raw)
    except Exception as exc:
        return None, {
            "classification": "VENUE_DATA_ERROR",
            "reason": "MAKER_COUNT_UNREADABLE",
            "maker_count_raw": str(makers_raw),
            **clean_error(exc),
            **common,
            **reward_meta,
        }
    if makers != 0:
        return None, {
            "classification": "MARKET_GATE",
            "reason": "COMPETITION_PRESENT",
            "qualifying_maker_count": makers,
            **common,
            **reward_meta,
        }

    top, depth_meta = await reliable_top(event.symbol)
    if top is None:
        return None, {
            "classification": "VENUE_DATA_ERROR",
            "reason": "DEPTH_UNAVAILABLE_AFTER_RETRIES",
            **common,
            "depth": depth_meta,
            **reward_meta,
        }

    bid, bid_size, ask, ask_size = top
    model = probe._model(bid, ask)
    cfg = reward["config"]
    max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")
    metrics = {
        "bid": str(bid),
        "bid_size": str(bid_size),
        "ask": str(ask),
        "ask_size": str(ask_size),
        "yes_quote": str(model["yes_quote"]),
        "no_quote": str(model["no_quote"]),
        "locked_edge": str(model["locked_edge"]),
        "yes_distance": str(model["yes_distance"]),
        "no_distance": str(model["no_distance"]),
        "max_distance": str(max_distance),
        "max_one_sided_premium": str(model["max_one_sided_premium"]),
    }

    if model["yes_distance"] > max_distance or model["no_distance"] > max_distance:
        return None, {
            "classification": "ECONOMIC_GATE",
            "reason": "REWARD_DISTANCE_GATE",
            **common,
            **metrics,
            "depth": depth_meta,
            **reward_meta,
        }
    if not (MIN_PRICE <= model["yes_quote"] <= MAX_PRICE and MIN_PRICE <= model["no_quote"] <= MAX_PRICE):
        return None, {
            "classification": "ECONOMIC_GATE",
            "reason": "PRICE_BAND_GATE",
            **common,
            **metrics,
            "depth": depth_meta,
            **reward_meta,
        }
    if model["locked_edge"] < MIN_PAIR_EDGE:
        return None, {
            "classification": "ECONOMIC_GATE",
            "reason": "PAIR_EDGE_GATE",
            **common,
            **metrics,
            "depth": depth_meta,
            **reward_meta,
        }
    if model["max_one_sided_premium"] > MAX_ONE_SIDED_PREMIUM:
        return None, {
            "classification": "ECONOMIC_GATE",
            "reason": "ONE_SIDED_PREMIUM_GATE",
            "max_one_sided_premium_cap": str(MAX_ONE_SIDED_PREMIUM),
            **common,
            **metrics,
            "depth": depth_meta,
            **reward_meta,
        }

    standing = rescue.standing_rescue_check(
        yes_quote=model["yes_quote"],
        no_quote=model["no_quote"],
        quantity=Q,
        yes_bid=bid,
        yes_bid_size=bid_size,
        yes_ask=ask,
        yes_ask_size=ask_size,
        max_loss=MAX_RESCUE_LOSS,
        increment=event.price_increment,
    )
    if not standing.safe:
        return None, {
            "classification": "SAFETY_GATE",
            "reason": "V5_RESCUE_GATE",
            **common,
            **metrics,
            "rescue": standing.as_dict(),
            "depth": depth_meta,
            **reward_meta,
        }

    report = {
        "classification": "QUALIFIED",
        "reason": "QUALIFIED_V5_1_ENTRY",
        "qualified": True,
        **common,
        **metrics,
        "rescue": standing.as_dict(),
        "depth": depth_meta,
        **reward_meta,
        "evaluation_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    return (event, reward, model), report


async def run_diagnostic() -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    counts: Counter[str] = Counter()
    samples: dict[str, dict[str, Any]] = {}
    class_counts: Counter[str] = Counter()
    polls = 0
    candidate = None

    while time.monotonic() < deadline:
        polls += 1
        try:
            found, report = await evaluate_once()
        except Exception as exc:
            found = None
            report = {
                "classification": "DIAGNOSTIC_INTERNAL_ERROR",
                "reason": "UNEXPECTED_DIAGNOSTIC_ERROR",
                **clean_error(exc),
            }
        reason = str(report.get("reason") or "UNKNOWN")
        classification = str(report.get("classification") or "UNKNOWN")
        counts[reason] += 1
        class_counts[classification] += 1
        samples.setdefault(reason, report)
        if found is not None:
            candidate = report
            break
        await asyncio.sleep(POLL_SECONDS)

    return {
        "classification": "READ_ONLY_NO_ORDER_ENTRY",
        "release_commit": RELEASE_COMMIT,
        "polls": polls,
        "classification_counts": dict(class_counts),
        "reason_counts": dict(counts),
        "reason_samples": samples,
        "qualified_candidate": candidate,
        "order_entry": False,
        "realized_pnl": 0,
    }


def main() -> None:
    result = asyncio.run(run_diagnostic())
    print("V5_1_QUALIFIER_DIAG=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    print("V5_1_QUALIFIER_DIAG_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
