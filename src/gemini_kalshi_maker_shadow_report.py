"""Summarize maker-shadow JSONL into policy/target performance metrics."""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from statistics import median
from typing import Any

D = Decimal


def _key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    asset = str(row.get("asset") or "NA")
    key = str(row.get("key") or "")
    parts = key.split("|")
    direction = str(row.get("direction") or (parts[0] if len(parts) > 0 else "NA"))
    policy = str(row.get("policy") or (parts[1] if len(parts) > 1 else "NA"))
    target = str(row.get("target_profit") or (parts[2] if len(parts) > 2 else "NA"))
    return asset, direction, policy, target


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * 0.95)))
    return ordered[idx]


def summarize(paths: list[Path]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "quotes_opened": 0,
            "reprices": 0,
            "cancels": 0,
            "fills": 0,
            "hedge_unavailable": 0,
            "profit": D("0"),
            "quote_ages_ms": [],
            "hedge_ages_ms": [],
        }
    )
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                bucket = buckets[_key(row)]
                event = str(row.get("event") or "")
                if event == "OPEN":
                    bucket["quotes_opened"] += 1
                elif event == "REPRICE":
                    bucket["reprices"] += 1
                elif event == "CANCEL":
                    bucket["cancels"] += 1
                elif event == "SIMULATED_FILL":
                    bucket["fills"] += 1
                    bucket["profit"] += D(str(row.get("simulated_fill_profit") or "0"))
                    if row.get("quote_age_ms") is not None:
                        bucket["quote_ages_ms"].append(int(row["quote_age_ms"]))
                    if row.get("hedge_book_age_ms") is not None:
                        bucket["hedge_ages_ms"].append(int(row["hedge_book_age_ms"]))
                elif event == "FILL_HEDGE_UNAVAILABLE":
                    bucket["hedge_unavailable"] += 1

    out: list[dict[str, Any]] = []
    for (asset, direction, policy, target), b in buckets.items():
        fill_attempts = b["fills"] + b["hedge_unavailable"]
        quotes = b["quotes_opened"]
        out.append({
            "asset": asset,
            "direction": direction,
            "policy": policy,
            "target_profit": target,
            "quotes_opened": quotes,
            "fills": b["fills"],
            "hedge_unavailable": b["hedge_unavailable"],
            "fill_attempts": fill_attempts,
            "fill_rate_per_quote_pct": (b["fills"] / quotes * 100) if quotes else 0.0,
            "hedge_success_pct": (b["fills"] / fill_attempts * 100) if fill_attempts else 0.0,
            "simulated_net_profit": str(b["profit"]),
            "avg_profit_per_fill": str((b["profit"] / b["fills"]) if b["fills"] else D("0")),
            "median_quote_age_ms": int(median(b["quote_ages_ms"])) if b["quote_ages_ms"] else None,
            "p95_quote_age_ms": _p95(b["quote_ages_ms"]),
            "median_hedge_age_ms": int(median(b["hedge_ages_ms"])) if b["hedge_ages_ms"] else None,
            "reprices": b["reprices"],
            "cancels": b["cancels"],
        })
    return sorted(out, key=lambda r: D(r["simulated_net_profit"]), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--dir", type=Path, default=Path("/tmp/maker-shadow"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = args.paths or sorted(args.dir.glob("maker_shadow_*.jsonl"))
    rows = summarize(paths)
    if args.json:
        print(json.dumps(rows, separators=(",", ":")))
        return
    if not rows:
        print("No maker-shadow events found.")
        return
    print("asset direction policy target fills hedge_ok% profit avg/fill p50_quote_ms")
    for r in rows:
        print(
            f"{r['asset']} {r['direction']} {r['policy']} {r['target_profit']} "
            f"{r['fills']} {r['hedge_success_pct']:.1f}% ${r['simulated_net_profit']} "
            f"${r['avg_profit_per_fill']} {r['median_quote_age_ms']}"
        )


if __name__ == "__main__":
    main()
