#!/usr/bin/env python3
"""Standard workup for a harvested Polymarket wallet.

Usage: python3 scripts/trader_workup.py <name>
Reads data/traders/<name>/raw, writes analysis/traders/<name>/ (CSVs + workup
JSON). Widens the classifier to BTC/ETH/SOL/XRP/DOGE and 5/15/60/240-minute
buckets at runtime; the L5 study's committed defaults are untouched.
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.polymarket_l5 import config, windows  # noqa: E402
from src.polymarket_l5.cache import RawCache  # noqa: E402
from src.polymarket_l5.export import build  # noqa: E402
from src.polymarket_l5.normalize import (  # noqa: E402
    fingerprint, load_non_trade_activity, normalize_trade_item,
)

ASSET_RES = {
    "BTC": re.compile(r"(?<![a-z0-9])(btc|xbt|bitcoin)(?![a-z0-9])", re.I),
    "ETH": re.compile(r"(?<![a-z0-9])(eth|ether|ethereum)(?![a-z0-9])", re.I),
    "SOL": re.compile(r"(?<![a-z0-9])(sol|solana)(?![a-z0-9])", re.I),
    "XRP": re.compile(r"(?<![a-z0-9])(xrp|ripple)(?![a-z0-9])", re.I),
    "DOGE": re.compile(r"(?<![a-z0-9])(doge|dogecoin)(?![a-z0-9])", re.I),
}


def widen_scope() -> None:
    config.ASSETS = tuple(ASSET_RES)
    config.WINDOW_BUCKETS_MIN = (5, 15, 60, 240)

    def classify_asset_wide(text):
        hits = [a for a, rx in ASSET_RES.items() if rx.search(text or "")]
        if len(hits) > 1:
            return "AMBIGUOUS", "text_conflict"
        return (hits[0], "text") if hits else ("", "none")

    windows.classify_asset = classify_asset_wide
    windows.MINUTE_RES[60] = re.compile(
        r"(?<![0-9])(?:60\s*-?\s*(?:min|minute)|1\s*-?\s*(?:hr|hour))", re.I)
    windows.MINUTE_RES[240] = re.compile(
        r"(?<![0-9])(?:240\s*-?\s*min|4\s*-?\s*(?:hr|hour)|4h)", re.I)


def _f(row, key):
    value = row.get(key, "")
    try:
        return None if value in ("", None) else float(value)
    except ValueError:
        return None


def workup(name: str) -> dict:
    widen_scope()
    raw = Path("data/traders") / name / "raw"
    out = Path("analysis/traders") / name
    cache = RawCache(raw)
    report = build(cache=cache, output_dir=out)

    trades = list(csv.DictReader(open(out / "l5_all_trades.csv")))
    summary = list(csv.DictReader(open(out / "l5_windows_summary.csv")))

    taker = set()
    for batch in cache.payload_batches("trades_takeronly"):
        for item in batch:
            rec = normalize_trade_item(item, "t")
            if rec:
                taker.add(fingerprint(rec))

    def fp(row):
        return fingerprint({
            "transaction_hash": row["transaction_hash"],
            "timestamp": int(float(row["timestamp"])),
            "condition_id": row["condition_id"], "token_id": row["token_id"],
            "side": row["side"], "price": _f(row, "price"), "shares": _f(row, "shares"),
        })

    dollars_all = sum(_f(r, "dollars") or 0 for r in trades)
    dollars_taker = sum(_f(r, "dollars") or 0 for r in trades if fp(r) in taker)

    non_trade = load_non_trade_activity(cache)
    income = Counter()
    for row in non_trade:
        income[row["activity_type"]] += row.get("usdc_size") or 0

    pair_costs = [x for x in (_f(r, "matched_pair_unit_cost") for r in summary) if x is not None]
    pnl = [x for x in (_f(r, "pnl_derived_resolution") for r in summary) if x is not None]
    ts = [float(r["timestamp"]) for r in trades if r.get("timestamp")]

    result = {
        "name": name,
        "fills_in_scope": report["fills_in_scope"],
        "fills_out_of_scope": report["fills_out_of_scope"],
        "windows": report["windows"],
        "coverage": {"first_ts": min(ts) if ts else None, "last_ts": max(ts) if ts else None},
        "by_asset_window": Counter((r["asset"], r["window_minutes"]) for r in trades).most_common(8),
        "sides": Counter(r["side"] for r in trades).most_common(),
        "dollars_in_scope": round(dollars_all, 2),
        "taker_share_dollars": round(dollars_taker / dollars_all, 4) if dollars_all else None,
        "median_pair_cost": round(statistics.median(pair_costs), 4) if pair_costs else None,
        "pair_cost_below_1_share": (
            round(sum(1 for x in pair_costs if x < 1) / len(pair_costs), 3) if pair_costs else None
        ),
        "derived_trading_pnl": round(sum(pnl), 2) if pnl else None,
        "incentives_usdc": {k: round(v, 2) for k, v in income.items()
                            if k in ("MAKER_REBATE", "TAKER_REBATE", "REWARD", "YIELD", "REFERRAL_REWARD")},
        "merge_events": int(sum(1 for r in non_trade if r["activity_type"] == "MERGE")),
        "merge_usdc": round(income.get("MERGE", 0), 2),
    }
    (out / "workup.json").write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    print(json.dumps(workup(sys.argv[1]), indent=1, default=str))
