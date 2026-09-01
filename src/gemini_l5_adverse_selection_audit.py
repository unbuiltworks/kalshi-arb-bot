"""Aggregate settlement-scored Gemini l5 shadow replays into fill-quality evidence.

Inputs are JSON artifacts emitted by :mod:`src.l5_shadow_settlement_score` for
Gemini. The audit intentionally ignores markets without strict trade-through
fills when calculating fill outcome statistics, while still reporting overall
coverage. Shadow economics remain SHADOW/NOT_REALIZED.
"""
from __future__ import annotations

import argparse
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    if value.get("venue") != "gemini":
        raise ValueError(f"{path}: expected venue=gemini")
    return value


def _filled(row: Mapping[str, Any]) -> bool:
    return D(str(row.get("yes_qty") or 0)) > 0 or D(str(row.get("no_qty") or 0)) > 0


def _asset_horizon(ticker: str) -> tuple[str, str]:
    upper = ticker.upper()
    asset = "BTC" if upper.startswith("BTC") else "ETH" if upper.startswith("ETH") else "OTHER"
    horizon = "5m" if "05M" in upper else "15m" if "15M" in upper else "other"
    return asset, horizon


def audit(payloads: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    payloads = list(payloads)
    settled_rows: list[Mapping[str, Any]] = []
    unresolved_rows: list[Mapping[str, Any]] = []
    markets_in_replay = 0
    for payload in payloads:
        markets_in_replay += int(payload.get("markets_in_replay") or 0)
        settled_rows.extend(row for row in payload.get("settled_detail", []) if isinstance(row, Mapping))
        unresolved_rows.extend(row for row in payload.get("unresolved_detail", []) if isinstance(row, Mapping))

    filled_settled = [row for row in settled_rows if _filled(row)]
    filled_unresolved = [row for row in unresolved_rows if _filled(row)]
    one_sided = [row for row in filled_settled if D(str(row.get("matched_qty") or 0)) == 0]
    both_sides = [row for row in filled_settled if D(str(row.get("matched_qty") or 0)) > 0]
    winners = [row for row in filled_settled if D(str(row.get("settled_shadow_trading_pnl") or 0)) > 0]
    losers = [row for row in filled_settled if D(str(row.get("settled_shadow_trading_pnl") or 0)) < 0]
    flats = [row for row in filled_settled if D(str(row.get("settled_shadow_trading_pnl") or 0)) == 0]

    total_pnl = sum((D(str(row.get("settled_shadow_trading_pnl") or 0)) for row in filled_settled), D("0"))
    total_modeled = sum((D(str(row.get("settled_shadow_modeled_pnl_with_maker_rebate") or 0)) for row in filled_settled), D("0"))

    groups: dict[str, dict[str, Any]] = {}
    for row in filled_settled:
        asset, horizon = _asset_horizon(str(row.get("ticker") or ""))
        key = f"{asset}_{horizon}"
        group = groups.setdefault(key, {"filled_settled": 0, "wins": 0, "losses": 0, "shadow_trading_pnl": D("0")})
        pnl = D(str(row.get("settled_shadow_trading_pnl") or 0))
        group["filled_settled"] += 1
        group["wins"] += int(pnl > 0)
        group["losses"] += int(pnl < 0)
        group["shadow_trading_pnl"] += pnl

    serial_groups = {
        key: {**value, "shadow_trading_pnl": str(value["shadow_trading_pnl"])}
        for key, value in sorted(groups.items())
    }
    detail = []
    for row in filled_settled:
        pnl = D(str(row.get("settled_shadow_trading_pnl") or 0))
        detail.append({
            "ticker": row.get("ticker"),
            "resolution_side": row.get("resolution_side"),
            "yes_qty": row.get("yes_qty"),
            "no_qty": row.get("no_qty"),
            "matched_qty": row.get("matched_qty"),
            "residual_side": row.get("residual_side"),
            "residual_qty": row.get("residual_qty"),
            "settled_shadow_trading_pnl": str(pnl),
            "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
        })

    return {
        "type": "GEMINI_L5_ADVERSE_SELECTION_AUDIT",
        "input_artifacts": len(payloads),
        "markets_in_replay": markets_in_replay,
        "settled_markets": len(settled_rows),
        "unresolved_markets": len(unresolved_rows),
        "filled_settled_markets": len(filled_settled),
        "filled_unresolved_markets": len(filled_unresolved),
        "one_sided_filled_settled_markets": len(one_sided),
        "both_sides_filled_settled_markets": len(both_sides),
        "fill_win_count": len(winners),
        "fill_loss_count": len(losers),
        "fill_flat_count": len(flats),
        "fill_win_rate": str(D(len(winners)) / D(len(filled_settled))) if filled_settled else None,
        "settled_shadow_trading_pnl": str(total_pnl),
        "settled_shadow_modeled_pnl_with_maker_rebate": str(total_modeled),
        "maker_rebate_status": "MODELED_NOT_REALIZED",
        "classification": "SHADOW_SETTLED_NOT_REALIZED",
        "groups": serial_groups,
        "filled_detail": detail,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(_load(path) for path in args.inputs)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
