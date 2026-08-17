"""Autonomous, persistent, read-only Gemini/Kalshi crypto basis research.

This process combines three existing/proven pieces without adding order entry:
1. the event-driven Gemini/Kalshi HOT scanner;
2. the no-lookahead Gemini-spot/Kaiko-strike basis annotation;
3. a durable post-settlement map using each venue's published settlement value.

Only economically positive episode *entries* are persisted to /data.  Raw market-data
rows stay on ephemeral storage.  When a matched window settles, persisted candidates
are retrospectively marked to actual 0x/1x/2x payout so apparent quote-lock P&L can be
compared with settlement-adjusted P&L.

Nothing in this module can authorize or place an order.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import tempfile
import time
from typing import Any

from src import gemini_kalshi_basis_annotated_shadow as basis
from src import gemini_kalshi_basis_settlement_map as settlement_map

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_autonomous")
CLASSIFICATION = "BASIS_RESEARCH_ONLY"


def _dec(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
        fh.flush()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, separators=(",", ":"), default=str)
        tmp.write("\n")
        tmp.flush()
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _candidate_key(asset: str, pair, direction: str, start_ms: int) -> str:
    return "|".join((
        asset,
        pair.window_end.isoformat(),
        pair.gemini_symbol,
        pair.kalshi_ticker,
        direction,
        str(start_ms),
    ))


def install_candidate_journal(candidate_path: Path) -> None:
    """Persist one record at the start of each threshold-qualified episode."""
    basis.install()
    hot = basis.hot
    original = hot._update_episodes
    if getattr(original, "_basis_autonomous_journal_wrapper", False):
        return

    def journal_update(*, active, qualifying, now_ms, asset, pair, trigger_venue) -> None:
        for direction, (q, candidate) in qualifying.items():
            if direction in active:
                continue
            context = basis.basis_snapshot(asset, int(now_ms))
            payload = {
                "candidate_key": _candidate_key(asset, pair, direction, int(now_ms)),
                "asset": asset,
                "window_end_utc": pair.window_end.isoformat(),
                "candidate_receive_ms": int(now_ms),
                "trigger_venue": trigger_venue,
                "gemini_symbol": pair.gemini_symbol,
                "kalshi_ticker": pair.kalshi_ticker,
                "direction": direction,
                "q": int(q),
                "capital": str(candidate.net_cost),
                "apparent_locked_profit": str(candidate.locked_profit),
                "fees": str(candidate.fees),
                "roi": str(candidate.roi_on_cost),
                "quote_skew_ms": candidate.quote_skew_ms,
                "basis_context_available": context is not None,
                **(context or {}),
                "classification": CLASSIFICATION,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }
            _append_jsonl(candidate_path, payload)
            logger.info("CRYPTO_BASIS_PERSISTED_CANDIDATE %s", json.dumps(payload, separators=(",", ":"), default=str))
        original(
            active=active,
            qualifying=qualifying,
            now_ms=now_ms,
            asset=asset,
            pair=pair,
            trigger_venue=trigger_venue,
        )

    journal_update._basis_autonomous_journal_wrapper = True  # type: ignore[attr-defined]
    hot._update_episodes = journal_update


def _payout_wins(direction: str, gemini_outcome: str, kalshi_outcome: str) -> int:
    g = gemini_outcome.lower()
    k = kalshi_outcome.lower()
    if g not in {"yes", "no"} or k not in {"yes", "no"}:
        raise ValueError("unsupported settlement outcome")
    if direction == "GEMINI_YES_KALSHI_NO":
        return int(g == "yes") + int(k == "no")
    if direction == "KALSHI_YES_GEMINI_NO":
        return int(k == "yes") + int(g == "no")
    raise ValueError(f"unsupported direction={direction!r}")


def retro_score_candidates(
    *,
    candidate_path: Path,
    settlement_path: Path,
    score_path: Path,
) -> dict[str, Any]:
    """Score each not-yet-scored candidate against actual paired settlements."""
    candidates = _load_jsonl(candidate_path)
    settlements = settlement_map.load_rows(settlement_path)
    scores = _load_jsonl(score_path)
    scored_keys = {str(row.get("candidate_key")) for row in scores if row.get("candidate_key")}
    by_kalshi = {str(row.get("kalshi_ticker")): row for row in settlements if row.get("kalshi_ticker")}

    new_scores = 0
    for candidate in candidates:
        key = str(candidate.get("candidate_key") or "")
        if not key or key in scored_keys:
            continue
        agreement = by_kalshi.get(str(candidate.get("kalshi_ticker") or ""))
        if agreement is None:
            continue
        q = _dec(candidate.get("q"))
        capital = _dec(candidate.get("capital"))
        apparent = _dec(candidate.get("apparent_locked_profit"))
        if q is None or capital is None or apparent is None or q <= 0:
            continue
        direction = str(candidate.get("direction") or "")
        wins = _payout_wins(
            direction,
            str(agreement.get("gemini_outcome") or ""),
            str(agreement.get("kalshi_outcome") or ""),
        )
        payout = q * D(wins)
        actual = payout - capital
        score = {
            "candidate_key": key,
            "asset": candidate.get("asset"),
            "window_end_utc": candidate.get("window_end_utc"),
            "gemini_symbol": candidate.get("gemini_symbol"),
            "kalshi_ticker": candidate.get("kalshi_ticker"),
            "direction": direction,
            "q": str(q),
            "capital": str(capital),
            "apparent_locked_profit": str(apparent),
            "gemini_outcome": agreement.get("gemini_outcome"),
            "kalshi_outcome": agreement.get("kalshi_outcome"),
            "outcomes_agree": agreement.get("outcomes_agree"),
            "settlement_winning_legs": wins,
            "settlement_payout": str(payout),
            "settlement_adjusted_pnl": str(actual),
            "basis_effect_vs_apparent": str(actual - apparent),
            "signed_basis_bps": agreement.get("signed_basis_bps"),
            "abs_basis_bps": agreement.get("abs_basis_bps"),
            "classification": CLASSIFICATION,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        _append_jsonl(score_path, score)
        scored_keys.add(key)
        scores.append(score)
        new_scores += 1

    apparent_total = D("0")
    actual_total = D("0")
    zero_leg = one_leg = two_leg = 0
    by_asset: dict[str, dict[str, Any]] = {}
    for row in scores:
        apparent = _dec(row.get("apparent_locked_profit"))
        actual = _dec(row.get("settlement_adjusted_pnl"))
        if apparent is not None:
            apparent_total += apparent
        if actual is not None:
            actual_total += actual
        wins = int(row.get("settlement_winning_legs") or 0)
        if wins == 0:
            zero_leg += 1
        elif wins == 1:
            one_leg += 1
        elif wins == 2:
            two_leg += 1
        asset = str(row.get("asset") or "UNKNOWN")
        bucket = by_asset.setdefault(asset, {
            "scored_candidates": 0,
            "apparent_locked_profit": D("0"),
            "settlement_adjusted_pnl": D("0"),
            "zero_leg_settlements": 0,
            "two_leg_settlements": 0,
        })
        bucket["scored_candidates"] += 1
        if apparent is not None:
            bucket["apparent_locked_profit"] += apparent
        if actual is not None:
            bucket["settlement_adjusted_pnl"] += actual
        if wins == 0:
            bucket["zero_leg_settlements"] += 1
        if wins == 2:
            bucket["two_leg_settlements"] += 1

    serial_by_asset: dict[str, Any] = {}
    for asset, bucket in sorted(by_asset.items()):
        serial_by_asset[asset] = {
            **bucket,
            "apparent_locked_profit": str(bucket["apparent_locked_profit"]),
            "settlement_adjusted_pnl": str(bucket["settlement_adjusted_pnl"]),
        }
    return {
        "candidate_episode_entries": len(candidates),
        "scored_candidates": len(scores),
        "new_scores_this_refresh": new_scores,
        "unsettled_or_unmatched_candidates": max(0, len(candidates) - len(scores)),
        "apparent_locked_profit": str(apparent_total),
        "settlement_adjusted_pnl": str(actual_total),
        "basis_effect": str(actual_total - apparent_total),
        "zero_leg_settlements": zero_leg,
        "one_leg_settlements": one_leg,
        "two_leg_settlements": two_leg,
        "by_asset": serial_by_asset,
        "accounting_scope": "ONE_SCORE_PER_THRESHOLD_QUALIFIED_DIRECTIONAL_EPISODE_ENTRY",
        "classification": CLASSIFICATION,
        "read_only": True,
        "trade_authorized": False,
        "order_entry": False,
    }


async def settlement_loop(
    *,
    candidate_path: Path,
    settlement_path: Path,
    score_path: Path,
    summary_path: Path,
    refresh_seconds: float,
    initial_hours: int,
    refresh_hours: int,
    gemini_limit: int,
) -> None:
    first = True
    while True:
        hours = initial_hours if first else refresh_hours
        first = False
        try:
            summary = await settlement_map.refresh(
                journal_path=settlement_path,
                summary_path=summary_path,
                hours=hours,
                gemini_limit=gemini_limit,
            )
            retro = retro_score_candidates(
                candidate_path=candidate_path,
                settlement_path=settlement_path,
                score_path=score_path,
            )
            summary["retrospective_candidate_scoring"] = retro
            summary["last_refresh_epoch_ms"] = int(time.time() * 1000)
            _atomic_json(summary_path, summary)
            logger.info("CRYPTO_BASIS_AUTONOMOUS_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "CRYPTO_BASIS_AUTONOMOUS_REFRESH_ERROR type=%s error=%s read_only=true order_entry=false",
                type(exc).__name__,
                str(exc)[:300],
            )
        await asyncio.sleep(max(60.0, refresh_seconds))


async def run(args) -> None:
    args.data_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = args.data_dir / "crypto-basis-candidates.jsonl"
    settlement_path = args.data_dir / "crypto-basis-settlements.jsonl"
    score_path = args.data_dir / "crypto-basis-retro-scores.jsonl"
    summary_path = args.data_dir / "crypto-basis-summary.json"
    install_candidate_journal(candidate_path)

    hot = basis.hot
    logger.info(
        "CRYPTO_BASIS_AUTONOMOUS_START %s",
        json.dumps({
            "assets": args.assets,
            "candidate_path": str(candidate_path),
            "settlement_path": str(settlement_path),
            "score_path": str(score_path),
            "summary_path": str(summary_path),
            "classification": CLASSIFICATION,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )

    settle_task = asyncio.create_task(settlement_loop(
        candidate_path=candidate_path,
        settlement_path=settlement_path,
        score_path=score_path,
        summary_path=summary_path,
        refresh_seconds=args.settlement_refresh_seconds,
        initial_hours=args.initial_history_hours,
        refresh_hours=args.refresh_history_hours,
        gemini_limit=args.gemini_limit,
    ), name="crypto-basis-settlement-loop")
    try:
        await hot.collect_all(
            assets=args.assets,
            seconds=0.0,
            output_dir=args.hot_output_dir,
            max_quote_skew_ms=args.max_quote_skew_ms,
            capital_limit=args.capital_limit,
            min_profit=args.min_profit,
        )
    finally:
        settle_task.cancel()
        await asyncio.gather(settle_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--max-quote-skew-ms", type=int, default=100)
    parser.add_argument("--capital-limit", type=Decimal, default=D("5"))
    parser.add_argument("--min-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--hot-output-dir", type=Path, default=Path("/tmp/crypto-hot-basis"))
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--settlement-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--initial-history-hours", type=int, default=168)
    parser.add_argument("--refresh-history-hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    if args.capital_limit <= 0 or args.min_profit < 0:
        raise SystemExit("invalid economics thresholds")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
