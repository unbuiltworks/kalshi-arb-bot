"""Continuous read-only supervisor for production-quality nested auto signals.

This is the graduation layer above kalshi_nested_auto_once.  It deliberately has
NO order-entry path.  It repeatedly rebuilds the near-term candidate universe,
consumes live Kalshi order books, applies the same production economics/spec
gates used by the one-shot live-capable runner, and records bounded paper
triggers through AutoRiskGovernor.

Once real one-shot execution has independently been proven, this governor and
journal can be placed above a separately authorized executor.  Keeping this
service paper-only lets us validate repeated-operation behavior first.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from decimal import Decimal
import json
import logging
from pathlib import Path
import time
from typing import Any

import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_auto_governor import AutoRiskGovernor, GovernorLimits, ET
from src.kalshi_nested_auto_once import (
    MAX_AUTO_MAKER_IMPROVEMENT,
    MAX_AUTO_SEED_COST,
    MAX_AUTO_TIME_TO_CLOSE_SECONDS,
    MIN_AUTO_TARGET,
    candidate_to_spec,
    fresh_fee_metadata,
)
from src.kalshi_nested_live_probe_guard import D
from src.kalshi_nested_probe_quality_watch import probe_quality_gate
from src.kalshi_nested_rolling_shadow import Candidate, build_candidates, desired_shadow_quote
from src.kalshi_nested_trade_through_shadow import KALSHI_WS, KALSHI_WS_SIGN_PATH, _ws_header_kw

logger = logging.getLogger("kalshi_arb.kalshi_nested_auto_supervisor")
WORST_CASE_SEED_CASH_RISK = D("0.02")


def pair_key(candidate: Candidate) -> str:
    legs = sorted([
        f"{candidate.maker_ticker}:{str(candidate.maker_side).lower()}",
        f"{candidate.hedge_ticker}:{str(candidate.hedge_side).lower()}",
    ])
    return f"{candidate.event_ticker}|{'|'.join(legs)}"


def append_journal(path: Path | None, row: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


async def paper_supervisor(
    *,
    seconds: float,
    refresh_seconds: float,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    maker_rest_seconds: int,
    journal_path: Path | None,
    limits: GovernorLimits,
) -> dict[str, Any]:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    if D(target_profit) < MIN_AUTO_TARGET:
        raise ValueError("supervisor target cannot be below 3c")
    if seconds <= 0 or refresh_seconds <= 0:
        raise ValueError("seconds and refresh_seconds must be positive")

    governor = AutoRiskGovernor(limits)
    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    deadline = time.monotonic() + float(seconds)
    stats: dict[str, Any] = {
        "generations": 0,
        "candidate_rows": 0,
        "quality_go_rows": 0,
        "paper_triggers": 0,
        "governor_blocks": {},
        "actual_orders_placed": 0,
    }
    block_log_keys: set[tuple[str, str]] = set()

    start_row = {
        "status": "PAPER_SUPERVISOR_START",
        "read_only": True,
        "order_entry": False,
        "actual_order_placed": False,
        "series": list(series),
        "target_profit": str(target_profit),
        "max_maker_improvement": str(MAX_AUTO_MAKER_IMPROVEMENT),
        "max_seed_cost": str(MAX_AUTO_SEED_COST),
        "max_time_to_close_seconds": MAX_AUTO_TIME_TO_CLOSE_SECONDS,
        "refresh_seconds": refresh_seconds,
        "governor": governor.snapshot(),
    }
    logger.info("NESTED_AUTO_SUPERVISOR_START %s", json.dumps(start_row, separators=(",", ":"), default=str))
    append_journal(journal_path, start_row)

    try:
        while time.monotonic() < deadline:
            if governor.locked_out:
                break
            if governor.triggers_today >= governor.limits.max_triggers_per_day:
                break

            stats["generations"] += 1
            candidates = await build_candidates(
                series=series,
                target_profit=D(target_profit),
                max_candidates=max_candidates,
                max_time_to_close_seconds=MAX_AUTO_TIME_TO_CLOSE_SECONDS,
            )
            stats["candidate_rows"] += len(candidates)
            generation_deadline = min(deadline, time.monotonic() + float(refresh_seconds))
            if not candidates:
                await asyncio.sleep(min(1.0, max(0.0, generation_deadline - time.monotonic())))
                continue

            tickers = sorted({t for c in candidates for t in (c.maker_ticker, c.hedge_ticker)})
            books = {ticker: KalshiOrderBook(ticker) for ticker in tickers}
            snapshots: set[str] = set()
            by_market: dict[str, list[Candidate]] = {}
            for candidate in candidates:
                by_market.setdefault(candidate.maker_ticker, []).append(candidate)
                by_market.setdefault(candidate.hedge_ticker, []).append(candidate)

            async def evaluate(candidate: Candidate) -> None:
                if candidate.maker_ticker not in snapshots or candidate.hedge_ticker not in snapshots:
                    return

                quote, economics = desired_shadow_quote(
                    candidate=candidate,
                    maker_book=books[candidate.maker_ticker],
                    hedge_book=books[candidate.hedge_ticker],
                )
                ok, _, _ = probe_quality_gate(
                    quote=quote,
                    economics=economics,
                    target_profit=D(target_profit),
                    max_maker_improvement=MAX_AUTO_MAKER_IMPROVEMENT,
                    max_seed_cost=MAX_AUTO_SEED_COST,
                )
                if not ok or quote is None:
                    return

                # Same final boundary as auto-once: refresh fee metadata, then
                # recompute from the live books before accepting the GO.
                meta = await fresh_fee_metadata(candidate.series)
                quote2, economics2 = desired_shadow_quote(
                    candidate=candidate,
                    maker_book=books[candidate.maker_ticker],
                    hedge_book=books[candidate.hedge_ticker],
                )
                ok2, _, detail = probe_quality_gate(
                    quote=quote2,
                    economics=economics2,
                    target_profit=D(target_profit),
                    max_maker_improvement=MAX_AUTO_MAKER_IMPROVEMENT,
                    max_seed_cost=MAX_AUTO_SEED_COST,
                )
                if not ok2 or quote2 is None:
                    return

                try:
                    spec = candidate_to_spec(
                        candidate=candidate,
                        quote=D(quote2),
                        economics=economics2,
                        fee_type=str(meta.fee_type),
                        fee_multiplier=D(meta.fee_multiplier),
                        scheduled_fee_changes=tuple(meta.scheduled_changes or []),
                        maker_rest_seconds=maker_rest_seconds,
                    )
                except ValueError:
                    return

                stats["quality_go_rows"] += 1
                key = pair_key(candidate)
                now = datetime.now(ET)
                mono = time.monotonic()
                decision = governor.decision(
                    event_ticker=candidate.event_ticker,
                    pair_key=key,
                    quantity=spec.quantity,
                    seed_cash_risk=WORST_CASE_SEED_CASH_RISK,
                    now=now,
                    monotonic_now=mono,
                )
                if not decision.allowed:
                    blocks = stats["governor_blocks"]
                    blocks[decision.reason] = int(blocks.get(decision.reason, 0)) + 1
                    log_key = (key, decision.reason)
                    if log_key not in block_log_keys:
                        block_log_keys.add(log_key)
                        logger.info("NESTED_AUTO_SUPERVISOR_BLOCK %s", json.dumps({
                            "event_ticker": candidate.event_ticker,
                            "pair_key": key,
                            "reason": decision.reason,
                            "actual_order_placed": False,
                            "read_only": True,
                        }, separators=(",", ":")))
                    return

                governor.record_trigger(
                    event_ticker=candidate.event_ticker,
                    pair_key=key,
                    seed_cash_risk=WORST_CASE_SEED_CASH_RISK,
                    now=now,
                    monotonic_now=mono,
                )
                stats["paper_triggers"] += 1
                row = {
                    "status": "PAPER_TRIGGER",
                    "timestamp_et": now.isoformat(),
                    "event_ticker": candidate.event_ticker,
                    "pair_key": key,
                    "maker_ticker": candidate.maker_ticker,
                    "maker_side": candidate.maker_side,
                    "maker_quote": str(quote2),
                    "hedge_ticker": candidate.hedge_ticker,
                    "hedge_side": candidate.hedge_side,
                    "locked_profit": str(economics2.get("locked_profit")),
                    "would_execute_seed_first": True,
                    "worst_case_seed_cash_risk": str(WORST_CASE_SEED_CASH_RISK),
                    **detail,
                    "governor": governor.snapshot(),
                    "actual_order_placed": False,
                    "read_only": True,
                    "order_entry": False,
                }
                logger.info("NESTED_AUTO_SUPERVISOR_PAPER_TRIGGER %s", json.dumps(row, separators=(",", ":"), default=str))
                append_journal(journal_path, row)

            async with websockets.connect(KALSHI_WS, open_timeout=5.0, **_ws_header_kw(headers)) as ws:
                await ws.send(json.dumps({
                    "id": stats["generations"],
                    "cmd": "subscribe",
                    "params": {"channels": ["orderbook_delta"], "market_tickers": tickers},
                }))
                while time.monotonic() < generation_deadline:
                    if governor.locked_out or governor.triggers_today >= governor.limits.max_triggers_per_day:
                        break
                    timeout = min(0.5, max(0.01, generation_deadline - time.monotonic()))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    payload = json.loads(raw)
                    if payload.get("type") == "error":
                        raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
                    if payload.get("type") not in {"orderbook_snapshot", "orderbook_delta"}:
                        continue
                    msg = payload.get("msg") or {}
                    ticker = str(msg.get("market_ticker") or "")
                    book = books.get(ticker)
                    if book is None:
                        continue
                    book.apply(payload)
                    if payload.get("type") == "orderbook_snapshot":
                        snapshots.add(ticker)
                    for candidate in by_market.get(ticker, []):
                        await evaluate(candidate)

    except Exception as exc:
        governor.lockout(f"{type(exc).__name__}:{str(exc)[:180]}")
        row = {
            "status": "PAPER_SUPERVISOR_LOCKOUT",
            "reason": governor.lockout_reason,
            "governor": governor.snapshot(),
            "stats": stats,
            "actual_order_placed": False,
            "read_only": True,
            "order_entry": False,
        }
        logger.exception("NESTED_AUTO_SUPERVISOR_LOCKOUT %s", json.dumps(row, separators=(",", ":"), default=str))
        append_journal(journal_path, row)
        return row

    status = "PAPER_SUPERVISOR_DONE"
    if governor.locked_out:
        status = "PAPER_SUPERVISOR_LOCKED_OUT"
    elif governor.triggers_today >= governor.limits.max_triggers_per_day:
        status = "PAPER_DAILY_LIMIT_REACHED"
    row = {
        "status": status,
        "governor": governor.snapshot(),
        "stats": stats,
        "actual_order_placed": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("NESTED_AUTO_SUPERVISOR_DONE %s", json.dumps(row, separators=(",", ":"), default=str))
    append_journal(journal_path, row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=21600.0)
    parser.add_argument("--refresh-seconds", type=float, default=300.0)
    parser.add_argument("--series", nargs="+", default=["KXBTCD", "KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=MIN_AUTO_TARGET)
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument("--maker-rest-seconds", type=int, default=10)
    parser.add_argument("--max-triggers-per-day", type=int, default=5)
    parser.add_argument("--cooldown-seconds", type=float, default=60.0)
    parser.add_argument("--journal-path", type=Path, default=Path("/tmp/kalshi-nested-auto-paper.jsonl"))
    args = parser.parse_args()

    limits = GovernorLimits(
        max_quantity=D("1"),
        max_triggers_per_day=args.max_triggers_per_day,
        max_triggers_per_event=1,
        cooldown_seconds=args.cooldown_seconds,
        max_seed_cash_risk_per_attempt=D("0.02"),
        max_seed_cash_risk_per_day=D("0.10"),
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(paper_supervisor(
        seconds=args.seconds,
        refresh_seconds=args.refresh_seconds,
        series=[s.upper() for s in args.series],
        target_profit=args.target_profit,
        max_candidates=args.max_candidates,
        maker_rest_seconds=args.maker_rest_seconds,
        journal_path=args.journal_path,
        limits=limits,
    ))
    print(json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
