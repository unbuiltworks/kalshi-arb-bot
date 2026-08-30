"""Read-only allocator for thin Gemini Prediction Markets reward opportunities.

The purpose of this module is not to invent volume. It ranks reward-enabled crypto
markets by reward scarcity, capital efficiency, fill economics, and risk so a
future executor can spend limited q=10 capital where being a resting maker is most
valuable. It never reads credentials and never submits/cancels orders.

Important: `reward_proxy_usd` is a ranking proxy, NOT a predicted or realized
payout. Gemini's exact liquidity-reward scoring curve and other makers' future
scores are not known here. Realized rewards must come from Gemini account records.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from decimal import Decimal as D
import json
from typing import Any, Iterable, Mapping

import httpx

REST = "https://api.gemini.com"
Q = D("10")
ZERO = D("0")
ONE = D("1")
MIN_PLANNED_UPTIME = D("0.50")
DEFAULT_SAFETY_CASH = D("12")
MAX_SAME_ASSET_WINDOWS = 1


@dataclass(frozen=True)
class Candidate:
    ticker: str
    symbol: str
    asset: str
    window_minutes: int
    yes_quote: D
    no_quote: D
    daily_pool_usd: D
    qualifying_makers: int
    max_spread_cents: D = D("10")
    planned_uptime: D = D("0.60")
    toxicity_penalty: D = ZERO
    modeled_pair_profit: D = ZERO
    seconds_to_expiry: D = D("300")

    @property
    def reserve_usd(self) -> D:
        # Both q10 orders can fill. Reserve the full acquisition principal rather
        # than assuming only one side fills.
        return Q * (self.yes_quote + self.no_quote)

    @property
    def max_one_side_usd(self) -> D:
        return Q * max(self.yes_quote, self.no_quote)

    @property
    def midpoint_distance_cents(self) -> D:
        # YES+NO quotes are expressed on their own outcomes. For a symmetric
        # binary book, total acquisition below $1 is also a useful conservative
        # proxy for how aggressively we sit toward the inside.
        pair_discount = max(ZERO, ONE - self.yes_quote - self.no_quote)
        return pair_discount * D("50")


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def eligible(c: Candidate) -> tuple[bool, str]:
    if c.planned_uptime < MIN_PLANNED_UPTIME:
        return False, "UPTIME_BELOW_50_PERCENT"
    if c.qualifying_makers < 0 or c.daily_pool_usd <= ZERO:
        return False, "INVALID_REWARD_POOL"
    if not (D("0.20") <= c.yes_quote <= D("0.80") and D("0.20") <= c.no_quote <= D("0.80")):
        return False, "OUTSIDE_MAKER_REBATE_PRICE_BAND"
    if c.reserve_usd <= ZERO:
        return False, "INVALID_RESERVE"
    if c.seconds_to_expiry < D("60"):
        return False, "TOO_CLOSE_TO_EXPIRY"
    return True, "ELIGIBLE"


def reward_proxy_usd(c: Candidate) -> D:
    """Conservative scarcity-weighted ranking proxy; never label as expected P&L."""
    # Treat ourselves as one additional maker and haircut by planned uptime.
    # Proximity is intentionally coarse because the exact production LR curve is
    # not reproduced here.  Quotes with positive pair discount get a modest boost,
    # capped so this proxy cannot dominate pool scarcity.
    scarcity_share = c.daily_pool_usd / D(max(1, c.qualifying_makers + 1))
    proximity = min(D("1"), D("0.50") + c.midpoint_distance_cents / D("20"))
    return scarcity_share * c.planned_uptime * proximity


def risk_adjusted_score(c: Candidate) -> D:
    ok, _ = eligible(c)
    if not ok:
        return D("-999999")
    # Favor scarce reward dollars and positive locked pair economics, penalize
    # toxicity and capital tied up. Pair profit is an observed/model input, not a
    # promise that both sides fill.
    value = reward_proxy_usd(c) + max(ZERO, c.modeled_pair_profit) - c.toxicity_penalty
    return value / max(D("1"), c.reserve_usd)


def allocate(candidates: Iterable[Candidate], available_usd: D,
             safety_cash: D = DEFAULT_SAFETY_CASH,
             max_same_asset_windows: int = MAX_SAME_ASSET_WINDOWS) -> dict[str, Any]:
    budget = max(ZERO, D(str(available_usd)) - D(str(safety_cash)))
    ranked = sorted(candidates, key=lambda c: (risk_adjusted_score(c), reward_proxy_usd(c)), reverse=True)
    selected: list[Candidate] = []
    rejected: list[dict[str, Any]] = []
    used = ZERO
    asset_counts: dict[str, int] = {}
    for c in ranked:
        ok, reason = eligible(c)
        if not ok:
            rejected.append({"ticker": c.ticker, "reason": reason})
            continue
        if asset_counts.get(c.asset, 0) >= max_same_asset_windows:
            rejected.append({"ticker": c.ticker, "reason": "CORRELATED_ASSET_WINDOW_CAP"})
            continue
        if used + c.reserve_usd > budget:
            rejected.append({"ticker": c.ticker, "reason": "INSUFFICIENT_FREE_BUDGET"})
            continue
        selected.append(c)
        used += c.reserve_usd
        asset_counts[c.asset] = asset_counts.get(c.asset, 0) + 1

    return {
        "classification": "READ_ONLY_REWARD_ALLOCATION_NOT_REALIZED",
        "available_usd": str(available_usd),
        "safety_cash_usd": str(safety_cash),
        "allocatable_budget_usd": str(budget),
        "reserved_usd": str(used),
        "unreserved_allocatable_usd": str(max(ZERO, budget-used)),
        "selected": [dict(asdict(c), reserve_usd=str(c.reserve_usd),
                          reward_proxy_usd=str(reward_proxy_usd(c)),
                          risk_adjusted_score=str(risk_adjusted_score(c)),
                          reward_proxy_is_realized=False) for c in selected],
        "rejected": rejected,
        "reward_proxy_is_realized": False,
    }


def _asset_and_window(ticker: str) -> tuple[str, int]:
    t = ticker.upper()
    asset = next((a for a in ("BTC", "ETH", "SOL", "XRP") if a in t), "OTHER")
    window = 5 if "05M" in t or "5M" in t else 15 if "15M" in t else 0
    return asset, window


async def public_reward_rows() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch current public reward config/events only; no auth and no order calls."""
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True,
                                 headers={"User-Agent":"arb-low-volume-reward-allocator/1.0"}) as http:
        cfg_r, ev_r = await asyncio.gather(
            http.get(f"{REST}/v1/prediction-markets/liquidity-rewards/config"),
            http.get(f"{REST}/v1/prediction-markets/liquidity-rewards/events",
                     params={"category":"Crypto","limit":100,"offset":0}),
        )
    cfg_r.raise_for_status(); ev_r.raise_for_status()
    cfg = cfg_r.json() if cfg_r.content else {}
    payload = ev_r.json() if ev_r.content else {}
    rows = payload.get("data") or payload.get("events") or payload.get("liquidity_reward_events") or []
    return cfg, [dict(r) for r in rows if isinstance(r, Mapping)]


def summarize_public_rows(cfg: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out = []
    for r in rows:
        ticker = str(r.get("event_ticker") or r.get("eventTicker") or "")
        asset, window = _asset_and_window(ticker)
        makers = r.get("qualifying_maker_count") if "qualifying_maker_count" in r else r.get("qualifyingMakerCount")
        pool = r.get("daily_pool_usd") if "daily_pool_usd" in r else r.get("dailyPoolUsd")
        try:
            makers_i = int(makers)
        except Exception:
            makers_i = -1
        out.append({
            "ticker": ticker,
            "asset": asset,
            "window_minutes": window,
            "daily_pool_usd": str(pool or ""),
            "qualifying_maker_count": makers_i,
            "scarcity_pool_per_current_maker_plus_us": str(_d(pool) / D(max(1, makers_i + 1))) if makers_i >= 0 else None,
        })
    out.sort(key=lambda x: (_d(x.get("scarcity_pool_per_current_maker_plus_us")), _d(x.get("daily_pool_usd"))), reverse=True)
    return {
        "classification":"READ_ONLY_PUBLIC_REWARD_CENSUS_NOT_REALIZED",
        "enabled": bool(cfg.get("enabled")),
        "max_spread_cents": str(cfg.get("max_spread_cents") or cfg.get("maxSpreadCents") or ""),
        "minimum_order_size": str(cfg.get("minimum_order_size") or cfg.get("minimumOrderSize") or ""),
        "event_count": len(out),
        "events": out,
        "reward_proxy_is_realized": False,
    }


async def main() -> None:
    cfg, rows = await public_reward_rows()
    print("GEMINI_LOW_VOLUME_REWARD_CENSUS=" + json.dumps(summarize_public_rows(cfg, rows), separators=(",",":")))


if __name__ == "__main__":
    asyncio.run(main())
