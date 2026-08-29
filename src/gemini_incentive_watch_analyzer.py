"""Analyze bounded Gemini incentive-watch snapshots without order entry.

Reads JSONL lines emitted by gemini_incentive_live_scan_summary and aggregates
reward-eligible persistence by ticker. It deliberately does NOT estimate a realized
liquidity-reward payout because Gemini's final denominator is only known after
scoring. It reports conservative one-sided capital-at-risk and the minimum reward
needed to offset a worst-case losing unmatched fill.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

PREFIX = "GEMINI_INCENTIVE_LIVE_SCAN="


def dec(v, default="0") -> D:
    try:
        return D(str(v))
    except Exception:
        return D(default)


def analyze(path: Path) -> dict:
    per = defaultdict(list)
    scans = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if PREFIX not in line:
            continue
        try:
            payload = json.loads(line.split(PREFIX, 1)[1])
        except Exception:
            continue
        scans += 1
        observed = payload.get("observed_at")
        for row in payload.get("top_candidates", []):
            r = dict(row)
            r["observed_at"] = observed
            per[str(r.get("ticker"))].append(r)

    rows = []
    for ticker, obs in per.items():
        if not ticker or ticker == "None":
            continue
        zero = [r for r in obs if r.get("reported_qualifying_maker_count") == 0]
        edges = [dec(r.get("trade_only_locked_edge")) for r in obs]
        pair_edges = [dec(r.get("trade_only_locked_edge_per_pair")) for r in obs]
        # q=10. Worst-case unmatched loss is conservatively the full premium paid
        # on the more expensive side, before any settlement payout/rebate.
        unmatched_costs = []
        for r in obs:
            y = dec(r.get("yes_bid"))
            n = dec(r.get("no_bid"))
            unmatched_costs.append(max(y, n) * D("10"))
        pool = max((dec(r.get("liquidity_reward_daily_pool_usd")) for r in obs), default=D("0"))
        min_break_even = max(unmatched_costs, default=D("0"))
        rows.append({
            "ticker": ticker,
            "asset": obs[-1].get("asset"),
            "horizon": obs[-1].get("horizon"),
            "eligible_observations": len(obs),
            "zero_competition_observations": len(zero),
            "zero_competition_fraction_of_eligible_observations": float(D(len(zero)) / D(len(obs))) if obs else 0.0,
            "first_observed_at": obs[0].get("observed_at"),
            "last_observed_at": obs[-1].get("observed_at"),
            "daily_pool_usd_reported": str(pool),
            "min_trade_only_locked_edge_q10": str(min(edges) if edges else D("0")),
            "medianish_trade_only_edge_per_pair": str(sorted(pair_edges)[len(pair_edges)//2] if pair_edges else D("0")),
            "max_trade_only_locked_edge_q10": str(max(edges) if edges else D("0")),
            "max_one_sided_premium_at_risk_q10": str(min_break_even),
            "reward_needed_to_cover_full_premium_loss_usd": str(min_break_even),
            "break_even_reward_as_fraction_of_reported_daily_pool": str((min_break_even / pool) if pool > 0 else D("0")),
            "classification": "READ_ONLY_PERSISTENCE_AND_RISK_ESTIMATE_NOT_REALIZED",
        })

    rows.sort(key=lambda r: (
        -r["zero_competition_fraction_of_eligible_observations"],
        dec(r["break_even_reward_as_fraction_of_reported_daily_pool"]),
        -r["eligible_observations"],
    ))
    return {
        "classification": "READ_ONLY_GEMINI_INCENTIVE_WATCH_ANALYSIS_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "scan_count_parsed": scans,
        "markets_with_reward_eligible_observations": len(rows),
        "top_markets": rows[:20],
        "warning": "Liquidity reward payout is unknown until Gemini computes final normalized score and total qualifying score; reported pool is not counted as PnL.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    args = ap.parse_args()
    print(json.dumps(analyze(args.path), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
