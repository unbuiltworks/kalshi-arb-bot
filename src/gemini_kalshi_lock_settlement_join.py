"""Join fee-positive Gemini/Kalshi shadow windows to eventual settlements.

Read-only research only. This answers whether apparent post-fee lock windows are
more likely than baseline matched windows to disagree because Gemini and Kalshi
settle on different indexes. It never certifies risk-free arbitrage.

For each unique asset/window, the best observed both-fee-positive candidate is
also marked to the eventual pair of venue outcomes. If the venues agree, one leg
pays and the original locked-profit math holds. If they disagree, both legs can
win or both can lose depending on direction, so the settlement-adjusted P&L can
be dramatically different from the apparent lock profit.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import httpx

from src.gemini_kalshi_execution_shadow import evaluate_pair, gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_settled
from src.gemini_kalshi_settlement_probe import (
    _kalshi_settled_paced,
    match_settled_true_windows,
)
from src.gemini_kalshi_watch_analyze import _book, load_rows

D = Decimal
BASIS_MARGIN_THRESHOLDS_BPS = (D("0"), D("0.25"), D("0.5"), D("1"), D("2"), D("5"), D("10"))


async def _kalshi_settled(http: httpx.AsyncClient, hours: int) -> list[dict]:
    """Rate-limit-safe settled-market fetch used by the lock/settlement join."""
    return await _kalshi_settled_paced(http, hours)


def match_settled(
    gemini: list[dict],
    kalshi: list[dict],
    *,
    max_expiry_skew_s: float,
) -> list[AgreementRow]:
    """Match Gemini ticker endpoint to Kalshi close_time, not expected resolution."""
    return match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)


def _kalshi_fee(multiplier: Decimal):
    def fee(price: Decimal, quantity: Decimal) -> Decimal:
        return kalshi_quadratic_taker_fee(price, quantity, fee_multiplier=multiplier)
    return fee


def fee_positive_windows(
    rows: list[dict[str, Any]],
    *,
    fee_multiplier: Decimal,
    max_quote_skew_ms: int = 500,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return unique asset/window pairs with the best both-fee-positive sample."""
    kalshi_fee = _kalshi_fee(fee_multiplier)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not row.get("fresh", True):
            continue
        try:
            g = _book(row, "gemini")
            k = _book(row, "kalshi")
        except (KeyError, TypeError, ValueError):
            continue
        candidates = evaluate_pair(
            g,
            k,
            gemini_fee=gemini_predictions_taker_fee,
            kalshi_fee=kalshi_fee,
            max_quote_skew_ms=max_quote_skew_ms,
        )
        if not candidates:
            continue
        key = (str(row["asset"]), str(row["window_end_utc"]))
        best = max(candidates, key=lambda c: c.locked_profit)
        existing = out.get(key)
        payload = {
            "asset": key[0],
            "window_end_utc": key[1],
            "gemini_symbol": str(row.get("gemini_symbol") or ""),
            "kalshi_ticker": str(row.get("kalshi_ticker") or ""),
            "best_locked_profit_dollars": str(best.locked_profit),
            "best_net_cost_dollars": str(best.net_cost),
            "best_quantity": str(best.quantity),
            "best_roi": str(best.roi_on_cost),
            "direction": best.direction,
        }
        if existing is None or D(payload["best_locked_profit_dollars"]) > D(existing["best_locked_profit_dollars"]):
            out[key] = payload
    return out


def _settlement_payout(
    *,
    direction: str,
    quantity: Decimal,
    gemini_outcome: str,
    kalshi_outcome: str,
) -> Decimal:
    """Return total payout across both complementary legs for observed outcomes."""
    g = gemini_outcome.lower()
    k = kalshi_outcome.lower()
    if g not in {"yes", "no"} or k not in {"yes", "no"}:
        raise ValueError("unsupported settlement outcome")

    if direction == "GEMINI_YES_KALSHI_NO":
        wins = int(g == "yes") + int(k == "no")
    elif direction == "KALSHI_YES_GEMINI_NO":
        wins = int(k == "yes") + int(g == "no")
    else:
        raise ValueError(f"unsupported direction={direction!r}")
    return quantity * D(wins)


def _basis_margin_sensitivity(joined: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Show how a Gemini close-margin gate changes observed basis risk and P&L.

    This is retrospective sensitivity analysis, not a live-trading rule. Rows with
    unknown Gemini margin are excluded from gated cohorts so the analysis fails
    closed rather than assuming they are safely far from the boundary.
    """
    out: dict[str, dict[str, Any]] = {}
    for threshold in BASIS_MARGIN_THRESHOLDS_BPS:
        eligible = [
            row for row in joined
            if row.get("gemini_margin_bps") is not None
            and abs(D(str(row["gemini_margin_bps"]))) >= threshold
        ]
        disagreements = [row for row in eligible if not row["agrees"]]
        apparent = sum((D(str(row["best_locked_profit_dollars"])) for row in eligible), D("0"))
        settlement = sum((D(str(row["settlement_adjusted_pnl_dollars"])) for row in eligible), D("0"))
        key = str(threshold.normalize()) if threshold != 0 else "0"
        out[key] = {
            "min_abs_gemini_margin_bps": str(threshold),
            "eligible_windows": len(eligible),
            "disagreements": len(disagreements),
            "disagreement_rate": (len(disagreements) / len(eligible)) if eligible else None,
            "apparent_locked_profit_dollars": str(apparent),
            "settlement_adjusted_pnl_dollars": str(settlement),
            "basis_effect_dollars": str(settlement - apparent),
        }
    return out


def summarize_join(
    lock_windows: dict[tuple[str, str], dict[str, Any]],
    agreements: list[AgreementRow],
) -> dict[str, Any]:
    by_ticker = {row.kalshi_ticker: row for row in agreements}
    joined: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for payload in lock_windows.values():
        agreement = by_ticker.get(str(payload.get("kalshi_ticker") or ""))
        if agreement is None:
            unresolved.append(payload)
            continue
        quantity = D(str(payload["best_quantity"]))
        net_cost = D(str(payload["best_net_cost_dollars"]))
        payout = _settlement_payout(
            direction=str(payload["direction"]),
            quantity=quantity,
            gemini_outcome=agreement.gemini_outcome,
            kalshi_outcome=agreement.kalshi_outcome,
        )
        settlement_pnl = payout - net_cost
        row = dict(payload)
        row.update({
            "gemini_outcome": agreement.gemini_outcome,
            "kalshi_outcome": agreement.kalshi_outcome,
            "agrees": agreement.agrees,
            "gemini_margin_bps": agreement.gemini_margin_bps,
            "settlement_payout_dollars": str(payout),
            "settlement_adjusted_pnl_dollars": str(settlement_pnl),
            "basis_effect_vs_locked_profit_dollars": str(
                settlement_pnl - D(str(payload["best_locked_profit_dollars"]))
            ),
        })
        joined.append(row)

    baseline_disagreements = sum(1 for row in agreements if not row.agrees)
    lock_disagreements = sum(1 for row in joined if not row["agrees"])
    by_asset: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "lock_windows": 0,
        "settled_joined": 0,
        "disagreements": 0,
        "apparent_locked_profit_dollars": D("0"),
        "settlement_adjusted_pnl_dollars": D("0"),
    })
    for payload in lock_windows.values():
        by_asset[str(payload["asset"])]["lock_windows"] += 1
    for row in joined:
        bucket = by_asset[str(row["asset"])]
        bucket["settled_joined"] += 1
        bucket["apparent_locked_profit_dollars"] += D(str(row["best_locked_profit_dollars"]))
        bucket["settlement_adjusted_pnl_dollars"] += D(str(row["settlement_adjusted_pnl_dollars"]))
        if not row["agrees"]:
            bucket["disagreements"] += 1
    for bucket in by_asset.values():
        n = bucket["settled_joined"]
        bucket["disagreement_rate"] = (bucket["disagreements"] / n) if n else None
        bucket["apparent_locked_profit_dollars"] = str(bucket["apparent_locked_profit_dollars"])
        bucket["settlement_adjusted_pnl_dollars"] = str(bucket["settlement_adjusted_pnl_dollars"])

    apparent_total = sum((D(str(row["best_locked_profit_dollars"])) for row in joined), D("0"))
    settlement_total = sum((D(str(row["settlement_adjusted_pnl_dollars"])) for row in joined), D("0"))
    disagreement_rows = [row for row in joined if not row["agrees"]]
    adverse = [row for row in disagreement_rows if D(str(row["settlement_adjusted_pnl_dollars"])) < 0]
    favorable = [row for row in disagreement_rows if D(str(row["settlement_adjusted_pnl_dollars"])) > D(str(row["best_locked_profit_dollars"]))]

    return {
        "fee_positive_lock_windows": len(lock_windows),
        "settled_lock_windows_joined": len(joined),
        "unresolved_lock_windows": len(unresolved),
        "lock_window_disagreements": lock_disagreements,
        "lock_window_disagreement_rate": (lock_disagreements / len(joined)) if joined else None,
        "baseline_matched_windows": len(agreements),
        "baseline_disagreements": baseline_disagreements,
        "baseline_disagreement_rate": (baseline_disagreements / len(agreements)) if agreements else None,
        "best_sample_one_execution_per_window_apparent_locked_profit_dollars": str(apparent_total),
        "best_sample_one_execution_per_window_settlement_adjusted_pnl_dollars": str(settlement_total),
        "basis_effect_total_dollars": str(settlement_total - apparent_total),
        "adverse_mismatch_windows": len(adverse),
        "favorable_mismatch_windows": len(favorable),
        "basis_margin_sensitivity": _basis_margin_sensitivity(joined),
        "by_asset": dict(sorted(by_asset.items())),
        "disagreement_rows": disagreement_rows,
        "unresolved_rows": unresolved,
        "accounting_scope": "BEST_SAMPLE_ONE_EXECUTION_PER_ASSET_WINDOW_NOT_EPISODE_SUM",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


async def run(
    paths: list[Path],
    *,
    fee_multiplier: Decimal,
    hours: int,
    gemini_limit: int,
    max_quote_skew_ms: int,
    max_expiry_skew_s: float,
) -> dict[str, Any]:
    rows = load_rows(paths)
    locks = fee_positive_windows(
        rows,
        fee_multiplier=fee_multiplier,
        max_quote_skew_ms=max_quote_skew_ms,
    )
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled(http, hours),
        )
    agreements = match_settled(gemini, kalshi, max_expiry_skew_s=max_expiry_skew_s)
    return summarize_join(locks, agreements)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--kalshi-fee-multiplier", type=Decimal, required=True)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=10000)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    if args.kalshi_fee_multiplier < 0:
        raise SystemExit("--kalshi-fee-multiplier must be non-negative")
    result = asyncio.run(run(
        args.paths,
        fee_multiplier=args.kalshi_fee_multiplier,
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        max_quote_skew_ms=args.max_quote_skew_ms,
        max_expiry_skew_s=args.max_expiry_skew_s,
    ))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
