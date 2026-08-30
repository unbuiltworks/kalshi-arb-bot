"""Read-only multi-market Gemini liquidity-reward shadow allocator.

Watches the nearest BTC/ETH/SOL/XRP 5m and 15m reward-enabled contracts, samples
public books on a minute cadence, applies conservative quote/risk gates, and asks
`gemini_low_volume_reward_allocator` where q=10 capital would be deployed.

No credentials are read and no orders/cancels are possible from this module.
Liquidity-reward pool values and the internal scarcity score are ranking inputs,
not expected or realized payout. Realized rewards must come from Gemini account
records.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from decimal import Decimal as D
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable, Mapping

from src import gemini_low_volume_market_scan as scan
from src import gemini_low_volume_reward_allocator as allocator

ZERO = D("0")
ONE = D("1")
DEFAULT_AVAILABLE_USD = D("57.1455")
DEFAULT_SAFETY_CASH = D("12")
DEFAULT_ONE_SIDE_CAP = D("15")
DEFAULT_DURATION_S = 3 * 60 * 60
DEFAULT_INTERVAL_S = 60
MIN_TTE_S = D("90")
MAX_TTE_S = D("1000")
MAX_MID_MOVE_CENTS = D("3.0")
MIN_PAIR_PROFIT_AFTER_FEES = ZERO
TARGET_WINDOWS = (5, 15)


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def _truthy(value: Any) -> bool:
    return bool(value is True or str(value).strip().lower() in {"1", "true", "yes", "on"})


@dataclass
class EventStats:
    ticker: str
    symbol: str
    asset: str
    window_minutes: int
    observed_snapshots: int = 0
    safe_quote_snapshots: int = 0
    selected_quote_snapshots: int = 0
    toxic_snapshots: int = 0
    modeled_pair_profit_sum: D = ZERO
    selected_pair_profit_sum: D = ZERO

    @property
    def shadow_uptime(self) -> D:
        if self.observed_snapshots <= 0:
            return ZERO
        return D(self.selected_quote_snapshots) / D(self.observed_snapshots)

    def out(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "modeled_pair_profit_sum": str(self.modeled_pair_profit_sum),
            "selected_pair_profit_sum": str(self.selected_pair_profit_sum),
            "shadow_uptime": str(self.shadow_uptime),
            "shadow_uptime_meets_50pct": bool(self.shadow_uptime >= D("0.50")),
        }


def nearest_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Choose the nearest live event for every asset/window pair."""
    best: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        asset = str(row.get("asset") or "OTHER")
        window = int(row.get("window_minutes") or 0)
        tte = _d(row.get("seconds_to_expiry"), "999999")
        if asset not in scan.ASSETS or window not in TARGET_WINDOWS:
            continue
        if not (MIN_TTE_S <= tte <= MAX_TTE_S):
            continue
        key = (asset, window)
        if key not in best or tte < _d(best[key].get("seconds_to_expiry"), "999999"):
            best[key] = row
    return sorted(best.values(), key=lambda r: (str(r.get("asset")), int(r.get("window_minutes") or 0)))


def evaluate_sample(sampled: Mapping[str, Any], max_spread_cents: D,
                    previous_mid: D | None = None) -> dict[str, Any]:
    """Apply conservative public-data-only quote gates to one sampled book."""
    row = dict(sampled)
    reasons: list[str] = []
    if not _truthy(row.get("book_ok")):
        return {"safe": False, "reasons": ["BOOK_UNAVAILABLE"], "mid": None,
                "mid_move_cents": None, "candidate": None}

    model = row.get("model") if isinstance(row.get("model"), Mapping) else {}
    yes = _d(model.get("yes_quote")); no = _d(model.get("no_quote"))
    pair_profit = _d(model.get("modeled_pair_profit_after_fees"))
    bid = _d(row.get("bid")); ask = _d(row.get("ask"))
    mid = (bid + ask) / D("2") if bid > ZERO and ask > bid else None
    move_cents = ZERO if previous_mid is None or mid is None else abs(mid - previous_mid) * D("100")
    spread_cents = (ask - bid) * D("100") if mid is not None else D("999")
    tte = _d(row.get("seconds_to_expiry"))

    # Top-of-book YES bid and complementary NO bid are each half the YES spread
    # from the binary midpoint. Gemini's public max_spread_cents is treated as the
    # maximum midpoint distance for this shadow.
    if spread_cents / D("2") > max_spread_cents:
        reasons.append("OUTSIDE_LR_MAX_MIDPOINT_DISTANCE")
    if not (D("0.20") <= yes <= D("0.80") and D("0.20") <= no <= D("0.80")):
        reasons.append("OUTSIDE_MAKER_REBATE_BAND")
    if pair_profit < MIN_PAIR_PROFIT_AFTER_FEES:
        reasons.append("NEGATIVE_MODELED_PAIR_PROFIT")
    if tte < MIN_TTE_S:
        reasons.append("TOO_CLOSE_TO_EXPIRY")
    if move_cents >= MAX_MID_MOVE_CENTS:
        reasons.append("TOXIC_MIDPOINT_MOVE")
    makers = int(row.get("qualifying_maker_count") if row.get("qualifying_maker_count") is not None else -1)
    if makers < 0:
        reasons.append("UNKNOWN_QUALIFYING_MAKER_COUNT")

    candidate = None
    if not reasons:
        candidate = allocator.Candidate(
            ticker=str(row.get("ticker") or ""),
            symbol=str(row.get("symbol") or ""),
            asset=str(row.get("asset") or "OTHER"),
            window_minutes=int(row.get("window_minutes") or 0),
            yes_quote=yes,
            no_quote=no,
            daily_pool_usd=_d(row.get("daily_pool_usd")),
            qualifying_makers=makers,
            max_spread_cents=max_spread_cents,
            planned_uptime=D("0.60"),
            toxicity_penalty=ZERO,
            modeled_pair_profit=pair_profit,
            seconds_to_expiry=tte,
        )
    return {
        "safe": not reasons,
        "reasons": reasons,
        "mid": str(mid) if mid is not None else None,
        "mid_move_cents": str(move_cents),
        "spread_cents": str(spread_cents),
        "candidate": candidate,
    }


def summarize_stats(stats: Mapping[str, EventStats]) -> dict[str, Any]:
    rows = [s.out() for s in stats.values()]
    rows.sort(key=lambda r: (r["asset"], r["window_minutes"], r["ticker"]))
    observed = sum(int(r["observed_snapshots"]) for r in rows)
    selected = sum(int(r["selected_quote_snapshots"]) for r in rows)
    safe = sum(int(r["safe_quote_snapshots"]) for r in rows)
    return {
        "events_seen": len(rows),
        "observed_snapshots": observed,
        "safe_quote_snapshots": safe,
        "selected_quote_snapshots": selected,
        "portfolio_shadow_uptime": str(D(selected) / D(observed)) if observed else "0",
        "events": rows,
    }


async def take_snapshot(previous_mids: dict[str, D], stats: dict[str, EventStats],
                        available_usd: D, safety_cash: D, one_side_cap: D) -> dict[str, Any]:
    cfg, rows = await scan.active_reward_catalog()
    chosen = nearest_rows(rows)
    sampled = await asyncio.gather(*(scan._sample(r) for r in chosen))
    max_spread = _d(cfg.get("max_spread_cents") or cfg.get("maxSpreadCents") or "10", "10")

    candidates: list[allocator.Candidate] = []
    evaluations: list[dict[str, Any]] = []
    by_ticker: dict[str, allocator.Candidate] = {}
    for row in sampled:
        symbol = str(row.get("symbol") or "")
        ticker = str(row.get("ticker") or "")
        stat = stats.setdefault(symbol, EventStats(
            ticker=ticker, symbol=symbol, asset=str(row.get("asset") or "OTHER"),
            window_minutes=int(row.get("window_minutes") or 0),
        ))
        stat.observed_snapshots += 1
        evaluation = evaluate_sample(row, max_spread, previous_mids.get(symbol))
        if evaluation.get("mid") is not None:
            previous_mids[symbol] = D(str(evaluation["mid"]))
        if "TOXIC_MIDPOINT_MOVE" in evaluation.get("reasons", []):
            stat.toxic_snapshots += 1
        candidate = evaluation.pop("candidate", None)
        if candidate is not None:
            stat.safe_quote_snapshots += 1
            stat.modeled_pair_profit_sum += candidate.modeled_pair_profit
            candidates.append(candidate)
            by_ticker[candidate.ticker] = candidate
        evaluations.append({
            "ticker": ticker,
            "symbol": symbol,
            "asset": row.get("asset"),
            "window_minutes": row.get("window_minutes"),
            "seconds_to_expiry": row.get("seconds_to_expiry"),
            "daily_pool_usd": row.get("daily_pool_usd"),
            "qualifying_maker_count": row.get("qualifying_maker_count"),
            "book_ok": row.get("book_ok", False),
            "bid": row.get("bid"), "bid_qty": row.get("bid_qty"),
            "ask": row.get("ask"), "ask_qty": row.get("ask_qty"),
            "model": row.get("model"),
            **evaluation,
        })

    allocation = allocator.allocate(
        candidates,
        available_usd=available_usd,
        safety_cash=safety_cash,
        max_same_asset_windows=1,
        max_portfolio_one_side_usd=one_side_cap,
    )
    selected = {str(r.get("ticker")) for r in allocation.get("selected", [])}
    for ticker in selected:
        c = by_ticker.get(ticker)
        if c is None:
            continue
        stat = stats[c.symbol]
        stat.selected_quote_snapshots += 1
        stat.selected_pair_profit_sum += c.modeled_pair_profit

    return {
        "classification": "READ_ONLY_MULTI_MARKET_REWARD_SHADOW_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "observed_at_unix": time.time(),
        "available_usd_assumption": str(available_usd),
        "safety_cash_usd": str(safety_cash),
        "max_portfolio_one_side_usd": str(one_side_cap),
        "reward_enabled": bool(cfg.get("enabled")),
        "max_spread_cents": str(max_spread),
        "evaluations": evaluations,
        "allocation": allocation,
        "stats": summarize_stats(stats),
        "liquidity_reward_pool_values_are_not_expected_or_realized_payouts": True,
        "modeled_pair_profit_is_not_realized_without_both_fills": True,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")
    tmp.replace(path)


async def run() -> None:
    available = _d(os.getenv("GEMINI_SHADOW_AVAILABLE_USD", str(DEFAULT_AVAILABLE_USD)), str(DEFAULT_AVAILABLE_USD))
    safety = _d(os.getenv("GEMINI_SHADOW_SAFETY_CASH", str(DEFAULT_SAFETY_CASH)), str(DEFAULT_SAFETY_CASH))
    one_side_cap = _d(os.getenv("GEMINI_SHADOW_ONE_SIDE_CAP", str(DEFAULT_ONE_SIDE_CAP)), str(DEFAULT_ONE_SIDE_CAP))
    duration_s = max(60, int(os.getenv("GEMINI_SHADOW_DURATION_S", str(DEFAULT_DURATION_S))))
    interval_s = max(15, int(os.getenv("GEMINI_SHADOW_INTERVAL_S", str(DEFAULT_INTERVAL_S))))
    state_dir = Path(os.getenv("GEMINI_SHADOW_STATE_DIR", "/state"))
    state_dir.mkdir(parents=True, exist_ok=True)

    previous_mids: dict[str, D] = {}
    stats: dict[str, EventStats] = {}
    deadline = time.monotonic() + duration_s
    samples = 0
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            payload = await take_snapshot(previous_mids, stats, available, safety, one_side_cap)
            samples += 1
            payload["sample_number"] = samples
            payload["duration_seconds"] = duration_s
            payload["interval_seconds"] = interval_s
            _write_json(state_dir / "current.json", payload)
            with (state_dir / "snapshots.jsonl").open("a") as fh:
                fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            print("GEMINI_MULTI_MARKET_REWARD_SHADOW=" + json.dumps({
                "sample": samples,
                "selected": [r.get("ticker") for r in payload["allocation"].get("selected", [])],
                "reserved_usd": payload["allocation"].get("reserved_usd"),
                "one_side_risk_usd": payload["allocation"].get("portfolio_one_side_risk_usd"),
                "portfolio_shadow_uptime": payload["stats"].get("portfolio_shadow_uptime"),
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
        except Exception as exc:
            error = {
                "classification": "READ_ONLY_MULTI_MARKET_REWARD_SHADOW_ERROR",
                "order_entry": False,
                "credentials_loaded": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
                "sample_number": samples + 1,
                "observed_at_unix": time.time(),
            }
            _write_json(state_dir / "last_error.json", error)
            print("GEMINI_MULTI_MARKET_REWARD_SHADOW_ERROR=" + json.dumps(error, separators=(",", ":")), flush=True)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, min(interval_s - elapsed, deadline - time.monotonic())))

    final = {
        "classification": "READ_ONLY_MULTI_MARKET_REWARD_SHADOW_FINAL_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "samples": samples,
        "available_usd_assumption": str(available),
        "stats": summarize_stats(stats),
        "liquidity_reward_pool_values_are_not_expected_or_realized_payouts": True,
    }
    _write_json(state_dir / "final.json", final)
    print("GEMINI_MULTI_MARKET_REWARD_SHADOW_FINAL=" + json.dumps(final, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
