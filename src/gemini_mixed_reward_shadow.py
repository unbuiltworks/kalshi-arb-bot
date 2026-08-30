"""Read-only mixed Gemini reward allocator.

Extends the conservative two-sided q=10 allocation with one-sided *shadow* quotes
only when a safe market was rejected by the portfolio one-side cap and the cheaper
leg fits inside remaining capital/risk. This is a measurement policy, not order
entry. Reward score proxies are relative ranking signals, never expected or
realized USD.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable, Mapping

from src import gemini_low_volume_reward_allocator as allocator
from src import gemini_multi_market_reward_shadow as base

ZERO = D("0")
Q = D("10")
DEFAULT_DURATION_S = 60 * 60
DEFAULT_INTERVAL_S = 60


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def candidate_from_evaluation(row: Mapping[str, Any]) -> allocator.Candidate | None:
    if not bool(row.get("safe")):
        return None
    model = row.get("model") if isinstance(row.get("model"), Mapping) else {}
    yes = _d(model.get("yes_quote")); no = _d(model.get("no_quote"))
    if yes <= ZERO or no <= ZERO:
        return None
    return allocator.Candidate(
        ticker=str(row.get("ticker") or ""),
        symbol=str(row.get("symbol") or ""),
        asset=str(row.get("asset") or "OTHER"),
        window_minutes=int(row.get("window_minutes") or 0),
        yes_quote=yes,
        no_quote=no,
        daily_pool_usd=_d(row.get("daily_pool_usd")),
        qualifying_makers=int(row.get("qualifying_maker_count") if row.get("qualifying_maker_count") is not None else -1),
        max_spread_cents=D("10"),
        planned_uptime=D("0.60"),
        toxicity_penalty=ZERO,
        modeled_pair_profit=_d(model.get("modeled_pair_profit_after_fees")),
        seconds_to_expiry=_d(row.get("seconds_to_expiry")),
    )


def one_sided_fallback(c: allocator.Candidate) -> dict[str, Any]:
    side = "YES" if c.yes_quote <= c.no_quote else "NO"
    price = min(c.yes_quote, c.no_quote)
    # A relative score proxy only. The baseline two-sided allocation gets the
    # platform's two-sided advantage; a single side is intentionally discounted.
    relative_score = allocator.reward_proxy_usd(c) / D("3")
    return {
        "ticker": c.ticker,
        "symbol": c.symbol,
        "asset": c.asset,
        "window_minutes": c.window_minutes,
        "mode": "ONE_SIDED_FALLBACK",
        "side": side,
        "price": str(price),
        "quantity": "10",
        "reserve_usd": str(Q * price),
        "portfolio_one_side_risk_usd": str(Q * price),
        "relative_reward_score_proxy": str(relative_score),
        "reward_proxy_is_realized": False,
        "modeled_pair_profit_usd": "0",
    }


def extend_baseline_with_one_sided_fallback(
    candidates: Iterable[allocator.Candidate],
    baseline: Mapping[str, Any],
    *,
    available_usd: D,
    safety_cash: D,
    one_side_cap: D,
) -> dict[str, Any]:
    candidates = list(candidates)
    budget = max(ZERO, available_usd - safety_cash)
    used = _d(baseline.get("reserved_usd"))
    risk = _d(baseline.get("portfolio_one_side_risk_usd"))
    selected_assets = {str(x.get("asset")) for x in baseline.get("selected", [])}
    selected_tickers = {str(x.get("ticker")) for x in baseline.get("selected", [])}

    two_sided = []
    for x in baseline.get("selected", []):
        row = dict(x)
        row["mode"] = "TWO_SIDED_BASELINE"
        row["relative_reward_score_proxy"] = str(_d(row.get("reward_proxy_usd")))
        two_sided.append(row)

    fallbacks = []
    remaining = [c for c in candidates if c.ticker not in selected_tickers and c.asset not in selected_assets]
    # Prefer the most reward-scarce cheap-side coverage per dollar of incremental
    # residual risk. No pair-profit credit is given to a one-sided quote.
    remaining.sort(
        key=lambda c: (allocator.reward_proxy_usd(c) / max(D("0.01"), Q * min(c.yes_quote, c.no_quote))),
        reverse=True,
    )
    for c in remaining:
        action = one_sided_fallback(c)
        add_reserve = _d(action["reserve_usd"])
        add_risk = _d(action["portfolio_one_side_risk_usd"])
        if used + add_reserve > budget:
            continue
        if risk + add_risk > one_side_cap:
            continue
        fallbacks.append(action)
        used += add_reserve
        risk += add_risk
        selected_assets.add(c.asset)

    return {
        "classification": "READ_ONLY_MIXED_REWARD_ALLOCATION_NOT_REALIZED",
        "order_entry": False,
        "available_usd": str(available_usd),
        "safety_cash_usd": str(safety_cash),
        "allocatable_budget_usd": str(budget),
        "max_portfolio_one_side_usd": str(one_side_cap),
        "reserved_usd": str(used),
        "portfolio_one_side_risk_usd": str(risk),
        "two_sided": two_sided,
        "one_sided_fallbacks": fallbacks,
        "market_coverage_count": len(two_sided) + len(fallbacks),
        "baseline_market_coverage_count": len(two_sided),
        "reward_score_proxy_is_relative_not_usd": True,
        "modeled_pair_profit_is_not_realized_without_both_fills": True,
    }


async def take_snapshot(previous_mids: dict[str, D], stats: dict[str, base.EventStats],
                        available_usd: D, safety_cash: D, one_side_cap: D) -> dict[str, Any]:
    baseline_snapshot = await base.take_snapshot(previous_mids, stats, available_usd, safety_cash, one_side_cap)
    candidates = []
    for row in baseline_snapshot.get("evaluations", []):
        c = candidate_from_evaluation(row)
        if c is not None:
            candidates.append(c)
    mixed = extend_baseline_with_one_sided_fallback(
        candidates,
        baseline_snapshot.get("allocation", {}),
        available_usd=available_usd,
        safety_cash=safety_cash,
        one_side_cap=one_side_cap,
    )
    return {
        "classification": "READ_ONLY_MIXED_REWARD_SHADOW_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "observed_at_unix": time.time(),
        "baseline": baseline_snapshot,
        "mixed_allocation": mixed,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")
    tmp.replace(path)


async def run() -> None:
    available = _d(os.getenv("GEMINI_SHADOW_AVAILABLE_USD", "57.1455"))
    safety = _d(os.getenv("GEMINI_SHADOW_SAFETY_CASH", "12"))
    one_side_cap = _d(os.getenv("GEMINI_SHADOW_ONE_SIDE_CAP", "15"))
    duration_s = max(60, int(os.getenv("GEMINI_SHADOW_DURATION_S", str(DEFAULT_DURATION_S))))
    interval_s = max(15, int(os.getenv("GEMINI_SHADOW_INTERVAL_S", str(DEFAULT_INTERVAL_S))))
    state_dir = Path(os.getenv("GEMINI_SHADOW_STATE_DIR", "/state"))
    previous_mids: dict[str, D] = {}
    stats: dict[str, base.EventStats] = {}
    deadline = time.monotonic() + duration_s
    samples = 0
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            payload = await take_snapshot(previous_mids, stats, available, safety, one_side_cap)
            samples += 1
            payload["sample_number"] = samples
            _write_json(state_dir / "current.json", payload)
            with (state_dir / "snapshots.jsonl").open("a") as fh:
                fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            print("GEMINI_MIXED_REWARD_SHADOW=" + json.dumps({
                "sample": samples,
                "baseline_markets": payload["mixed_allocation"]["baseline_market_coverage_count"],
                "mixed_markets": payload["mixed_allocation"]["market_coverage_count"],
                "one_sided_fallbacks": len(payload["mixed_allocation"]["one_sided_fallbacks"]),
                "risk_usd": payload["mixed_allocation"]["portfolio_one_side_risk_usd"],
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
        except Exception as exc:
            _write_json(state_dir / "last_error.json", {
                "classification": "READ_ONLY_MIXED_REWARD_SHADOW_ERROR",
                "order_entry": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
                "sample_number": samples + 1,
            })
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, min(interval_s - elapsed, deadline - time.monotonic())))
    _write_json(state_dir / "final.json", {
        "classification": "READ_ONLY_MIXED_REWARD_SHADOW_FINAL_NOT_REALIZED",
        "order_entry": False,
        "samples": samples,
        "stats": base.summarize_stats(stats),
    })


if __name__ == "__main__":
    asyncio.run(run())
