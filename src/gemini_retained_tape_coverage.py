"""Parse retained Gemini public shadow tapes and report family coverage.

Read-only utility: consumes JSONL only, loads no credentials, performs no network
calls, and never mutates venue/account state. It exists to prevent brittle shell
pattern matching from being mistaken for evidence about trade coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any


def _family(row: dict[str, Any]) -> str:
    asset = str(row.get("asset") or "UNKNOWN").upper()
    horizon = str(row.get("horizon") or "unknown").lower()
    return f"{asset}{horizon}"


def summarize_file(path: Path) -> dict[str, Any]:
    rows = 0
    malformed = 0
    type_counts: Counter[str] = Counter()
    plan_family: Counter[str] = Counter()
    trade_family: Counter[str] = Counter()
    trade_tickers: dict[str, set[str]] = defaultdict(set)

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
            except Exception:
                malformed += 1
                continue
            row_type = str(row.get("type") or "UNKNOWN")
            type_counts[row_type] += 1
            if row_type == "SHADOW_PLAN":
                plan_family[_family(row)] += 1
            elif row_type == "PUBLIC_TRADE":
                fam = _family(row)
                trade_family[fam] += 1
                ticker = str(row.get("ticker") or "")
                if ticker:
                    trade_tickers[fam].add(ticker)

    run_id = path.parent.name
    return {
        "run_id": run_id,
        "path": str(path),
        "rows": rows,
        "malformed_rows": malformed,
        "type_counts": dict(sorted(type_counts.items())),
        "shadow_plan_family_counts": dict(sorted(plan_family.items())),
        "public_trade_family_counts": dict(sorted(trade_family.items())),
        "public_trade_market_counts": {k: len(v) for k, v in sorted(trade_tickers.items())},
        "public_trade_tickers": {k: sorted(v) for k, v in sorted(trade_tickers.items())},
        "eth5_public_trades": trade_family.get("ETH5m", 0),
        "eth5_trade_markets": len(trade_tickers.get("ETH5m", set())),
    }


def summarize_root(root: Path, max_runs: int = 20) -> dict[str, Any]:
    captures = list(root.glob("*/capture.jsonl"))
    captures.sort(key=lambda p: (p.stat().st_mtime, p.parent.name), reverse=True)
    if max_runs > 0:
        captures = captures[:max_runs]
    runs = [summarize_file(path) for path in captures]
    best = None
    if runs:
        best = max(
            runs,
            key=lambda r: (
                int(r["eth5_public_trades"]),
                int(r["eth5_trade_markets"]),
                int(r["type_counts"].get("PUBLIC_TRADE", 0)),
                r["run_id"],
            ),
        )
    return {
        "type": "GEMINI_RETAINED_TAPE_COVERAGE_V1",
        "classification": "READ_ONLY_PUBLIC_TAPE_ANALYSIS",
        "order_entry": False,
        "credentials_loaded": False,
        "root": str(root),
        "runs_scanned": len(runs),
        "best_eth5_run_id": None if best is None else best["run_id"],
        "best_eth5_public_trades": 0 if best is None else best["eth5_public_trades"],
        "best_eth5_trade_markets": 0 if best is None else best["eth5_trade_markets"],
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=Path)
    group.add_argument("--root", type=Path)
    parser.add_argument("--max-runs", type=int, default=20)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.input:
        result: dict[str, Any] = summarize_file(args.input)
        result.update({
            "type": "GEMINI_RETAINED_TAPE_COVERAGE_FILE_V1",
            "classification": "READ_ONLY_PUBLIC_TAPE_ANALYSIS",
            "order_entry": False,
            "credentials_loaded": False,
        })
    else:
        result = summarize_root(args.root, max_runs=args.max_runs)

    if args.compact and "runs" in result:
        result = {
            k: v for k, v in result.items() if k != "runs"
        } | {
            "runs": [
                {
                    "run_id": r["run_id"],
                    "public_trades": r["type_counts"].get("PUBLIC_TRADE", 0),
                    "eth5_public_trades": r["eth5_public_trades"],
                    "eth5_trade_markets": r["eth5_trade_markets"],
                    "public_trade_family_counts": r["public_trade_family_counts"],
                }
                for r in result["runs"]
            ]
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
