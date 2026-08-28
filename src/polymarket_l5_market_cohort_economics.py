"""Read-only settled-market cohort economics for l5 Polymarket short crypto.

Calendar-day wallet cashflow can be badly distorted when a short-duration market is
bought before UTC midnight and redeemed after midnight. This module instead assigns
a market to the UTC day containing the market's own window start, then joins every
observed trade for that condition to eventual public REDEEM activity.

A missing REDEEM row is ambiguous: it can mean delayed/unobserved redemption, but for
an aged binary market it can also mean the wallet only held losing outcome inventory
and therefore had a zero payout. To avoid upward bias, this report exposes both the
redeemed-only observed cashflow and a conservative scenario treating every no-redeem
market as zero payout.

Public MAKER_REBATE and TAKER_REBATE payments are reported separately by payment day.
They are not automatically attributed to the cohort's earning day because the public
activity row itself does not prove the earning period. No authentication or order
entry.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
import json
from typing import Any

from src.polymarket_l5_daily_economics import (
    WINDOW_RE,
    _utc_bounds,
    fetch_activity_type,
    is_short_crypto,
)
from src.polymarket_l5_reward_snapshot import DEFAULT_WALLET, REWARD_TYPES, dec, fetch_reward_rows


def market_window_start(row: dict[str, Any]) -> int | None:
    for key in ("slug", "eventSlug"):
        match = WINDOW_RE.match(str(row.get(key) or "").strip())
        if match:
            return int(match.group(3))
    return None


def market_key(row: dict[str, Any]) -> str:
    return str(row.get("conditionId") or row.get("slug") or row.get("eventSlug") or "").strip()


def market_family(row: dict[str, Any]) -> tuple[str, str]:
    for key in ("slug", "eventSlug"):
        match = WINDOW_RE.match(str(row.get(key) or "").strip())
        if match:
            return match.group(1).lower(), match.group(2).lower()
    return "unknown", "unknown"


def _family_template() -> dict[str, D | int]:
    return {
        "markets": 0,
        "buy_usdc": D("0"),
        "sell_usdc": D("0"),
        "redeem_usdc": D("0"),
        "cashflow": D("0"),
        "no_redeem_markets": 0,
        "no_redeem_buy_usdc": D("0"),
        "no_redeem_sell_usdc": D("0"),
    }


def reward_totals_by_type(rows: list[dict[str, Any]]) -> dict[str, str]:
    totals = {typ: D("0") for typ in REWARD_TYPES}
    for row in rows:
        typ = str(row.get("type") or "").strip().upper()
        if typ in totals:
            totals[typ] += dec(row.get("usdc_size"))
    return {typ: str(totals[typ]) for typ in REWARD_TYPES}


def reconcile_market_cohorts(
    trades: list[dict[str, Any]],
    redeems: list[dict[str, Any]],
    *,
    day_start: int,
    day_end: int,
) -> dict[str, Any]:
    trade_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    redeem_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in trades:
        start = market_window_start(row)
        key = market_key(row)
        if key and start is not None and day_start <= start < day_end and is_short_crypto(row):
            trade_groups[key].append(row)
    for row in redeems:
        key = market_key(row)
        if key:
            redeem_groups[key].append(row)

    settled_rows: list[dict[str, Any]] = []
    no_redeem_rows: list[dict[str, Any]] = []
    by_family: dict[str, dict[str, D | int]] = {}

    for key, rows in sorted(trade_groups.items()):
        rows = sorted(rows, key=lambda r: int(r.get("timestamp") or 0))
        first = rows[0]
        coin, horizon = market_family(first)
        buys = [r for r in rows if str(r.get("side") or "").upper() == "BUY"]
        sells = [r for r in rows if str(r.get("side") or "").upper() == "SELL"]
        buy_usdc = sum((dec(r.get("usdcSize")) for r in buys), D("0"))
        sell_usdc = sum((dec(r.get("usdcSize")) for r in sells), D("0"))
        redeem_usdc = sum((dec(r.get("usdcSize")) for r in redeem_groups.get(key, [])), D("0"))
        base = {
            "condition_id": key,
            "slug": str(first.get("slug") or first.get("eventSlug") or ""),
            "coin": coin,
            "horizon": horizon,
            "window_start": market_window_start(first),
            "trade_rows": len(rows),
            "buy_rows": len(buys),
            "sell_rows": len(sells),
            "buy_usdc": str(buy_usdc),
            "sell_usdc": str(sell_usdc),
            "redeem_usdc": str(redeem_usdc),
        }
        family = f"{coin}_{horizon}"
        agg = by_family.setdefault(family, _family_template())

        if redeem_usdc <= 0:
            no_redeem_rows.append(base)
            agg["no_redeem_markets"] = int(agg["no_redeem_markets"]) + 1
            agg["no_redeem_buy_usdc"] = D(str(agg["no_redeem_buy_usdc"])) + buy_usdc
            agg["no_redeem_sell_usdc"] = D(str(agg["no_redeem_sell_usdc"])) + sell_usdc
            continue

        lifecycle = sell_usdc + redeem_usdc - buy_usdc
        base["observed_lifecycle_cashflow"] = str(lifecycle)
        settled_rows.append(base)

        agg["markets"] = int(agg["markets"]) + 1
        agg["buy_usdc"] = D(str(agg["buy_usdc"])) + buy_usdc
        agg["sell_usdc"] = D(str(agg["sell_usdc"])) + sell_usdc
        agg["redeem_usdc"] = D(str(agg["redeem_usdc"])) + redeem_usdc
        agg["cashflow"] = D(str(agg["cashflow"])) + lifecycle

    settled_buy = sum((D(r["buy_usdc"]) for r in settled_rows), D("0"))
    settled_redeem = sum((D(r["redeem_usdc"]) for r in settled_rows), D("0"))
    settled_sell = sum((D(r["sell_usdc"]) for r in settled_rows), D("0"))
    settled_cashflow = settled_sell + settled_redeem - settled_buy

    no_redeem_buy = sum((D(r["buy_usdc"]) for r in no_redeem_rows), D("0"))
    no_redeem_sell = sum((D(r["sell_usdc"]) for r in no_redeem_rows), D("0"))
    conservative_cashflow = settled_cashflow + no_redeem_sell - no_redeem_buy

    positive = sum(1 for r in settled_rows if D(r["observed_lifecycle_cashflow"]) > 0)
    negative = sum(1 for r in settled_rows if D(r["observed_lifecycle_cashflow"]) < 0)
    flat = len(settled_rows) - positive - negative

    return {
        "classification": "PUBLIC_MARKET_COHORT_CASHFLOW_REWARDS_SEPARATE_ZERO_REDEEM_SCENARIO",
        "market_count": len(trade_groups),
        "settled_market_count": len(settled_rows),
        "unresolved_market_count": len(no_redeem_rows),
        "no_redeem_observed_market_count": len(no_redeem_rows),
        "positive_market_count": positive,
        "negative_market_count": negative,
        "flat_market_count": flat,
        "settled_buy_usdc": str(settled_buy),
        "settled_sell_usdc": str(settled_sell),
        "settled_redeem_usdc": str(settled_redeem),
        "observed_settled_lifecycle_cashflow": str(settled_cashflow),
        "no_redeem_observed_buy_usdc": str(no_redeem_buy),
        "no_redeem_observed_sell_usdc": str(no_redeem_sell),
        "conservative_cashflow_if_no_redeem_is_zero_payout": str(conservative_cashflow),
        "by_family": {
            key: {
                "markets": int(value["markets"]),
                "buy_usdc": str(value["buy_usdc"]),
                "sell_usdc": str(value["sell_usdc"]),
                "redeem_usdc": str(value["redeem_usdc"]),
                "cashflow": str(value["cashflow"]),
                "no_redeem_markets": int(value["no_redeem_markets"]),
                "no_redeem_buy_usdc": str(value["no_redeem_buy_usdc"]),
                "no_redeem_sell_usdc": str(value["no_redeem_sell_usdc"]),
                "conservative_cashflow_if_no_redeem_is_zero_payout": str(
                    D(str(value["cashflow"]))
                    + D(str(value["no_redeem_sell_usdc"]))
                    - D(str(value["no_redeem_buy_usdc"]))
                ),
            }
            for key, value in sorted(by_family.items())
        },
        "markets": settled_rows,
        "unresolved_markets": no_redeem_rows,
        "no_redeem_observed_markets": no_redeem_rows,
    }


def reconcile_day(wallet: str, day: date, *, redeem_horizon_days: int = 3) -> dict[str, Any]:
    start, end = _utc_bounds(day)
    trades = fetch_activity_type(wallet, "TRADE", start=max(0, start - 1800), end=end + 1800)
    trades = [row for row in trades if is_short_crypto(row)]
    redeems = fetch_activity_type(wallet, "REDEEM", start=max(0, start - 1800), end=end + 86400 * redeem_horizon_days)
    result = reconcile_market_cohorts(trades, redeems, day_start=start, day_end=end)

    same_rewards = fetch_reward_rows(wallet, start=start, end=end)
    next_rewards = fetch_reward_rows(wallet, start=end, end=end + 86400)
    same_by_type = reward_totals_by_type(same_rewards)
    next_by_type = reward_totals_by_type(next_rewards)
    result.update(
        {
            "wallet": wallet,
            "cohort_day_utc": day.isoformat(),
            "window_start": start,
            "window_end": end,
            "redeem_horizon_days": redeem_horizon_days,
            "order_entry": False,
            "reward_payments_not_attributed": {
                "same_utc_day_total": str(sum((dec(r.get("usdc_size")) for r in same_rewards), D("0"))),
                "same_utc_day_by_type": same_by_type,
                "next_utc_day_total": str(sum((dec(r.get("usdc_size")) for r in next_rewards), D("0"))),
                "next_utc_day_by_type": next_by_type,
                "earning_period_attribution": "UNPROVEN",
                "activity_types": list(REWARD_TYPES),
                "liquidity_rewards_included": False,
            },
            "warnings": [
                "Redeemed-only lifecycle cashflow can be upward-biased if losing positions produce no REDEEM row.",
                "The zero-redeem scenario is conservative and may be too negative if redemption occurred outside the search horizon or is missing from the public API.",
                "MAKER_REBATE and TAKER_REBATE payment rows are split by type but are not added to lifecycle cashflow until earning-period attribution is proven.",
                "Polymarket liquidity rewards are a separate program and are not represented by these MAKER_REBATE/TAKER_REBATE activity rows.",
                "Public Data API cashflow is observational and is not our realized P&L or tax characterization.",
            ],
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--date", required=True)
    parser.add_argument("--redeem-horizon-days", type=int, default=3)
    parser.add_argument("--output")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = reconcile_day(args.wallet, date.fromisoformat(args.date), redeem_horizon_days=args.redeem_horizon_days)
    text = json.dumps(result, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        from pathlib import Path
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
