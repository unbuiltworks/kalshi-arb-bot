"""One-shot live Gemini<->Kalshi MLB hit-prop rule-risk canary.

This is intentionally NOT classified as guaranteed arbitrage. Gemini and Kalshi
have materially different exceptional-event rules for MLB hit props. The canary is
allowed only after official MLB game state proves the hitter was a starter and has
recorded >=1 plate appearance, which removes the non-participation mismatch but not
all suspension/postponement risk.

Safety:
- exact Gemini/Kalshi canonical identity from the audited direct-match scanner;
- current family must be baseball_player_hits;
- both venue contracts must map canonical YES to source YES;
- official MLB game state must be Live and prove starter + >=1 PA;
- exactly one contract per leg;
- >= $0.03 modeled after-fee q=1 lock;
- entry and revalidation quote skew <= 50 ms;
- both books <= 100 ms old;
- exact original limits/depth must remain marketable after 50 ms;
- exact outbound payload economics are independently recomputed;
- explicit --live plus ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES plus
  MLB_GK_PROP_LIVE_RULE_RISK=YES plus a one-use local arm token;
- deterministic client order ids derived from a required run id;
- one submission attempt per process; Railway service must use restartPolicy=NEVER;
- transport preserves the existing dual-FOK, ambiguity reconciliation, and hard
  halt/flatten behavior.

The experiment has a $5 hard service budget but the first canary uses only one
contract per leg. Settlement compatibility must be audited later before any result
is counted as realized arbitrage P&L.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx

from src.gemini_kalshi_execution_shadow import evaluate_pair, gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_kalshi_live_probe_guard import (
    build_leg_payloads,
    live_probe_authorized,
    validate_exact_probe_payloads,
)
from src.gemini_kalshi_live_service import _preflight
from src.gemini_kalshi_mlb_prop_hot_shadow import (
    GeminiPropBooks,
    _next_update,
    discover_hot,
    kalshi_tob,
    orient_tob,
)
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor, submit_dual_probe
from src.eventing_books import EventingKalshiCrossBooks
from src.mlb_live_participation import candidate_game, fetch_game_boxscores, participation_proof, proof_dict

D = Decimal
logger = logging.getLogger("kalshi_arb.mlb_gk_prop_live_rule_risk_canary")

FAMILY = "baseball_player_hits"
QUANTITY = D("1")
MIN_PROFIT = D("0.03")
MAX_BOOK_AGE_MS = 100
MAX_QUOTE_SKEW_MS = 50
REVALIDATE_MS = 50
HARD_BUDGET_USD = D("5")
ARM_ENV = "MLB_GK_PROP_LIVE_RULE_RISK"
RUN_ID_ENV = "MLB_GK_PROP_RUN_ID"
DEFAULT_ARM_PATH = "/tmp/mlb-gk-prop-live-rule-risk.arm"


def _run_id() -> str:
    raw = os.getenv(RUN_ID_ENV, "").strip()
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    if not value or len(value) > 28:
        raise ValueError("MLB_GK_PROP_RUN_ID must be 1-28 safe characters")
    return value


def _arm_path() -> Path:
    return Path(os.getenv("LIVE_PROBE_ARM_TOKEN_FILE", DEFAULT_ARM_PATH).strip())


def _software_authorized(live_requested: bool) -> bool:
    return bool(
        live_probe_authorized(live_requested)
        and os.getenv(ARM_ENV, "NO").strip().upper() == "YES"
    )


def _create_arm_once(live_requested: bool) -> Path:
    path = _arm_path()
    if not _software_authorized(live_requested):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path
    try:
        os.write(fd, (_run_id() + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _consume_arm(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _fresh(g: Any, k: Any, now_ms: int) -> bool:
    return bool(
        max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms)) <= MAX_BOOK_AGE_MS
        and abs(int(g.ts_ms) - int(k.ts_ms)) <= MAX_QUOTE_SKEW_MS
    )


def _same_original_limits(direction: str, entry_g: Any, entry_k: Any, now_g: Any, now_k: Any) -> bool:
    if direction == "GEMINI_YES_KALSHI_NO":
        return bool(
            now_g.yes_ask <= entry_g.yes_ask
            and now_g.yes_ask_size >= QUANTITY
            and now_k.no_ask <= entry_k.no_ask
            and now_k.no_ask_size >= QUANTITY
        )
    if direction == "KALSHI_YES_GEMINI_NO":
        return bool(
            now_k.yes_ask <= entry_k.yes_ask
            and now_k.yes_ask_size >= QUANTITY
            and now_g.no_ask <= entry_g.no_ask
            and now_g.no_ask_size >= QUANTITY
        )
    return False


def _payloads(row: Any, direction: str, g: Any, k: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    # Current Gemini MLB hit inventory maps canonical >=N YES directly to Gemini YES;
    # current Kalshi hit series does the same. Fail closed if that ever changes.
    if row.gemini_side.side not in {"YES", "CONTRACT"} or row.kalshi_side.side not in {"YES", "CONTRACT"}:
        raise ValueError("live hit canary only supports direct source-YES orientation")
    gp, kp = build_leg_payloads(
        direction=direction,
        gemini_symbol=row.gemini_side.venue_market_id,
        kalshi_ticker=row.kalshi_side.venue_market_id,
        gemini_yes_bid=D(g.yes_bid),
        gemini_yes_ask=D(g.yes_ask),
        kalshi_yes_bid=D(k.yes_bid),
        kalshi_yes_ask=D(k.yes_ask),
    )
    rid = _run_id()
    gp["clientOrderId"] = f"mlbgk-{rid}-g"
    kp["client_order_id"] = f"mlbgk-{rid}-k"
    return gp, kp


def _game_live_and_participated(row: Any, games: dict[Any, Any]) -> tuple[bool, Any, str | None]:
    game_key = candidate_game(row.game_slug)
    game = games.get(game_key) if game_key is not None else None
    proof = participation_proof(
        family=row.family,
        player_name=row.player,
        poly_slug=row.game_slug,
        games=games,
    )
    status = str((game or {}).get("status") or "")
    ok = bool(
        row.family == FAMILY
        and status.lower() == "live"
        and proof.started
        and proof.plate_appearance_recorded
        and proof.source
    )
    return ok, proof, status or None


async def run(*, live_requested: bool, watch_seconds: float) -> int:
    if not live_requested:
        raise RuntimeError("live canary requires --live")
    if not _software_authorized(live_requested):
        raise RuntimeError("live sports rule-risk software gates are not enabled")

    token = _create_arm_once(live_requested)
    if not token.is_file():
        raise RuntimeError("one-use sports arm token is absent")

    async with httpx.AsyncClient(timeout=30.0) as http:
        hot, market_to_keys, diagnostics = await discover_hot(http)
    hot = {
        key: row for key, row in hot.items()
        if row.family == FAMILY
        and row.gemini_side.side in {"YES", "CONTRACT"}
        and row.kalshi_side.side in {"YES", "CONTRACT"}
    }
    market_to_keys = {
        marker: tuple(key for key in keys if key in hot)
        for marker, keys in market_to_keys.items()
        if any(key in hot for key in keys)
    }
    if not hot:
        raise RuntimeError("no eligible direct-orientation Gemini/Kalshi MLB hit props")

    symbols = sorted({row.gemini_side.venue_market_id for row in hot.values()})
    tickers = sorted({row.kalshi_side.venue_market_id for row in hot.values()})
    dates = sorted({row.game_slug[-10:] for row in hot.values()})
    gemini_books = GeminiPropBooks(symbols)
    kalshi_books = EventingKalshiCrossBooks(tickers)
    gemini_trade = GeminiTradingSession()
    kalshi_trade = KalshiV2Executor()
    games: dict[Any, Any] = {}

    async def refresh_games() -> None:
        nonlocal games
        while True:
            try:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    games = await fetch_game_boxscores(http, dates)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MLB_GK_RULE_RISK_GAME_REFRESH_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(2.0)

    tasks: list[asyncio.Task] = []
    try:
        await gemini_trade.connect()
        preflight = await _preflight(kalshi_trade, live_requested=True, token_path=token)
        logger.warning("MLB_GK_RULE_RISK_PREFLIGHT %s", json.dumps({
            **preflight,
            "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
            "hard_budget_usd": str(HARD_BUDGET_USD),
            "quantity": str(QUANTITY),
        }, separators=(",", ":"), default=str))

        tasks = [
            asyncio.create_task(gemini_books.run()),
            asyncio.create_task(kalshi_books.run()),
            asyncio.create_task(refresh_games()),
        ]
        logger.warning("MLB_GK_RULE_RISK_ARMED %s", json.dumps({
            **diagnostics,
            "eligible_hit_keys": len(hot),
            "run_id": _run_id(),
            "quantity": "1",
            "hard_budget_usd": str(HARD_BUDGET_USD),
            "min_profit": str(MIN_PROFIT),
            "max_book_age_ms": MAX_BOOK_AGE_MS,
            "max_quote_skew_ms": MAX_QUOTE_SKEW_MS,
            "revalidate_ms": REVALIDATE_MS,
            "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
            "order_entry": True,
        }, separators=(",", ":"), default=str))

        deadline = time.monotonic() + max(1.0, watch_seconds)
        while time.monotonic() < deadline:
            try:
                trigger = await asyncio.wait_for(_next_update(gemini_books, kalshi_books), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            for key in market_to_keys.get((trigger.venue, trigger.market_id), ()):
                row = hot[key]
                eligible, proof, game_status = _game_live_and_participated(row, games)
                if not eligible:
                    continue
                g_raw = gemini_books.quote(row.gemini_side.venue_market_id, max_age_ms=MAX_BOOK_AGE_MS)
                k_raw = await kalshi_books.quote(row.kalshi_side.venue_market_id, max_age_ms=MAX_BOOK_AGE_MS)
                if g_raw is None or k_raw is None:
                    continue
                g = orient_tob(g_raw, row.gemini_side.side)
                k = orient_tob(kalshi_tob(k_raw, row.kalshi_side.venue_market_id), row.kalshi_side.side)
                now_ms = int(time.time() * 1000)
                if not _fresh(g, k, now_ms):
                    continue

                def kfee(price: Decimal, quantity: Decimal) -> Decimal:
                    return kalshi_quadratic_taker_fee(price, quantity, fee_multiplier=row.kalshi_fee_multiplier)

                candidates = evaluate_pair(
                    g, k,
                    gemini_fee=gemini_predictions_taker_fee,
                    kalshi_fee=kfee,
                    max_quote_skew_ms=MAX_QUOTE_SKEW_MS,
                    max_quantity=QUANTITY,
                )
                candidates = [c for c in candidates if c.locked_profit >= MIN_PROFIT and c.net_cost <= HARD_BUDGET_USD]
                if not candidates:
                    continue
                candidate = max(candidates, key=lambda c: c.locked_profit)
                entry_g, entry_k = g, k
                await asyncio.sleep(REVALIDATE_MS / 1000)

                g2_raw = gemini_books.quote(row.gemini_side.venue_market_id, max_age_ms=MAX_BOOK_AGE_MS)
                k2_raw = await kalshi_books.quote(row.kalshi_side.venue_market_id, max_age_ms=MAX_BOOK_AGE_MS)
                if g2_raw is None or k2_raw is None:
                    continue
                g2 = orient_tob(g2_raw, row.gemini_side.side)
                k2 = orient_tob(kalshi_tob(k2_raw, row.kalshi_side.venue_market_id), row.kalshi_side.side)
                if not _fresh(g2, k2, int(time.time() * 1000)):
                    continue
                if not _same_original_limits(candidate.direction, entry_g, entry_k, g2, k2):
                    continue

                still_eligible, proof2, status2 = _game_live_and_participated(row, games)
                if not still_eligible:
                    continue

                gp, kp = _payloads(row, candidate.direction, entry_g, entry_k)
                recomputed = validate_exact_probe_payloads(
                    gp, kp,
                    modeled_locked_profit=candidate.locked_profit,
                    kalshi_fee_multiplier=row.kalshi_fee_multiplier,
                    quote_skew_ms=candidate.quote_skew_ms,
                )
                if recomputed < MIN_PROFIT:
                    continue
                if not token.is_file() or not _consume_arm(token):
                    raise RuntimeError("sports one-use arm token race")

                logger.warning("MLB_GK_RULE_RISK_SUBMITTING %s", json.dumps({
                    "run_id": _run_id(),
                    "settlement_key": key,
                    "player": row.player,
                    "direction": candidate.direction,
                    "modeled_locked_profit": str(candidate.locked_profit),
                    "net_cost": str(candidate.net_cost),
                    "quote_skew_ms": candidate.quote_skew_ms,
                    "revalidation_ms": REVALIDATE_MS,
                    "game_status": status2 or game_status,
                    "participation_proof": proof_dict(proof2 or proof),
                    "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
                    "single_use_token_consumed": True,
                }, separators=(",", ":"), default=str))

                result = await submit_dual_probe(
                    gemini_trade,
                    kalshi_trade,
                    gp,
                    kp,
                    modeled_locked_profit=candidate.locked_profit,
                    kalshi_fee_multiplier=row.kalshi_fee_multiplier,
                    quote_skew_ms=candidate.quote_skew_ms,
                    cli_live=True,
                )
                logger.warning("MLB_GK_RULE_RISK_RESULT %s", json.dumps({
                    "run_id": _run_id(),
                    "settlement_key": key,
                    "player": row.player,
                    "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
                    "execution": result,
                }, separators=(",", ":"), default=str))
                return 70 if result.get("hard_halt_required") else 0

        logger.info("MLB_GK_RULE_RISK_NO_ATTEMPT watch_seconds=%s token_still_present=%s", watch_seconds, token.is_file())
        return 0
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await gemini_trade.close()
        await kalshi_trade.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--watch-seconds", type=float, default=14400.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(live_requested=args.live, watch_seconds=args.watch_seconds)))


if __name__ == "__main__":
    main()
