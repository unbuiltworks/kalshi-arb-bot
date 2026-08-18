"""K/G/P micro-live entrypoint with fresh Gemini depth10 revalidation.

The base canary intentionally rejects Gemini's active-events discovery quote. This
entrypoint wraps each scanner cycle and upgrades only exact approved Gemini rows with
a fresh public ``depth10@100ms`` book before the base execution guard sees them.
A failed/one-sided depth fetch leaves the discovery source untouched, so execution
still fails closed.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.gemini_prediction_depth import fetch_depth_top
from src.sports_kgp_micro_guard import RuleApproval
from src.three_venue_pricing_core import gemini_one_contract_fee
from src import sports_kgp_micro_service as base

D = Decimal
ONE = D("1")


def _approved_gemini_symbols(approvals: dict[str, list[RuleApproval]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for key, rows in approvals.items():
        for approval in rows:
            if "gemini" not in approval.venues:
                continue
            out.setdefault(key, set()).add(approval.market_for("gemini"))
    return out


def _apply_gemini_top(row: dict[str, Any], top: Any) -> dict[str, Any]:
    """Replace one Gemini discovery row with canonical q=1 depth economics."""
    out = dict(row)
    source_side = str(row.get("source_side") or "")
    if source_side in {"YES", "CONTRACT"}:
        raw_yes = D(top.yes_ask)
        raw_no = ONE - D(top.yes_bid)
        yes_depth = D(top.yes_ask_size)
        no_depth = D(top.yes_bid_size)
    elif source_side == "NO":
        # Canonical YES is native NO when the source contract is the complement.
        raw_yes = ONE - D(top.yes_bid)
        raw_no = D(top.yes_ask)
        yes_depth = D(top.yes_bid_size)
        no_depth = D(top.yes_ask_size)
    else:
        return out
    yes_fee = gemini_one_contract_fee(raw_yes)
    no_fee = gemini_one_contract_fee(raw_no)
    out.update({
        "raw_yes_ask": str(raw_yes),
        "raw_no_ask": str(raw_no),
        "yes_fee": str(yes_fee),
        "no_fee": str(no_fee),
        "all_in_yes_cost": str(raw_yes + yes_fee),
        "all_in_no_cost": str(raw_no + no_fee),
        "yes_depth": str(min(ONE, yes_depth)),
        "no_depth": str(min(ONE, no_depth)),
        "received_ms": int(top.received_ms),
        "depth_source": "gemini_depth10_100ms",
    })
    return out


async def refresh_approved_gemini_depth(result: dict[str, Any], approvals: dict[str, list[RuleApproval]]) -> dict[str, Any]:
    approved = _approved_gemini_symbols(approvals)
    if not approved:
        return result
    output = deepcopy(result)
    rows = output.get("positive_quote_audit", [])
    requests: dict[tuple[str, str], asyncio.Task] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("venue") or "") != "gemini":
            continue
        key = str(row.get("settlement_key") or "")
        symbol = str(row.get("market_id") or "").split("#", 1)[0]
        if symbol and symbol in approved.get(key, set()):
            requests[(key, symbol)] = asyncio.create_task(fetch_depth_top(symbol, timeout=2.0))
    if not requests:
        return output
    fresh: dict[tuple[str, str], Any] = {}
    for marker, task in requests.items():
        try:
            fresh[marker] = await task
        except Exception:
            # Discovery-only row remains untouched and the execution guard rejects it.
            pass
    replaced = 0
    for idx, row in enumerate(rows):
        if not isinstance(row, dict) or str(row.get("venue") or "") != "gemini":
            continue
        key = str(row.get("settlement_key") or "")
        symbol = str(row.get("market_id") or "").split("#", 1)[0]
        top = fresh.get((key, symbol))
        if top is not None:
            rows[idx] = _apply_gemini_top(row, top)
            replaced += 1
    output.setdefault("execution_revalidation", {})["gemini_depth10_rows_replaced"] = replaced
    output["execution_revalidation"]["gemini_depth10_requests"] = len(requests)
    output["execution_revalidation"]["gemini_depth10_failures"] = len(requests) - len(fresh)
    return output


async def run_with_depth(
    *,
    live_requested: bool,
    once: bool,
    interval_seconds: float,
    approvals_path: Path,
    token_path: Path,
    log_path: Path,
) -> int:
    approvals = base.load_approvals(approvals_path)
    original_run_once = base.scan.run_once

    async def wrapped_run_once() -> dict[str, Any]:
        result = await original_run_once()
        return await refresh_approved_gemini_depth(result, approvals)

    base.scan.run_once = wrapped_run_once
    try:
        return await base.run(
            live_requested=live_requested,
            once=once,
            interval_seconds=interval_seconds,
            approvals_path=approvals_path,
            token_path=token_path,
            log_path=log_path,
        )
    finally:
        base.scan.run_once = original_run_once


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--approvals", type=Path, default=base.DEFAULT_APPROVALS)
    parser.add_argument("--arm-token", type=Path, default=base.DEFAULT_ARM_TOKEN)
    parser.add_argument("--log", type=Path, default=base.DEFAULT_LOG)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_with_depth(
        live_requested=args.live,
        once=args.once,
        interval_seconds=max(0.25, args.interval_seconds),
        approvals_path=args.approvals,
        token_path=args.arm_token,
        log_path=args.log,
    )))


if __name__ == "__main__":
    main()
