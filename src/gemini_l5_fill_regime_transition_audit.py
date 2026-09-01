"""Measure regime movement around strict trade-through Gemini l5 shadow fills.

Joins the raw shadow JSONL with a settlement-scored replay. For each strict
trade-through fill, reports the prior observation (when the virtual maker order
was resting) and the fill observation (when the market traded through it), then
classifies whether the favored regime persisted, disappeared, or flipped.

Read-only research only. No order entry or realized P&L claims.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any, Mapping


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("ticker"):
                rows.append(value)
    return rows


def load_score(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("venue") != "gemini":
        raise ValueError("expected Gemini settlement-score JSON")
    return value


def _dt(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


def _fav(row: Mapping[str, Any]) -> str | None:
    value = str(row.get("favored_side") or "").upper()
    return value if value in {"YES", "NO"} else None


def _prob(row: Mapping[str, Any]) -> D | None:
    value = row.get("favored_probability")
    return D(str(value)) if value not in {None, ""} else None


def audit(raw_rows: list[dict[str, Any]], score: Mapping[str, Any]) -> dict[str, Any]:
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        by_ticker.setdefault(str(row["ticker"]), []).append(row)
    for rows in by_ticker.values():
        rows.sort(key=lambda row: _dt(row["observed_at"]))

    details: list[dict[str, Any]] = []
    for market in score.get("settled_detail", []):
        if not isinstance(market, Mapping):
            continue
        ticker = str(market.get("ticker") or "")
        rows = by_ticker.get(ticker, [])
        if not rows:
            continue
        for fill in market.get("fill_chronology", []) or []:
            if not isinstance(fill, Mapping) or not fill.get("observed_at"):
                continue
            fill_time = _dt(fill["observed_at"])
            index = min(range(len(rows)), key=lambda i: abs((_dt(rows[i]["observed_at"]) - fill_time).total_seconds()))
            fill_row = rows[index]
            prior_row = rows[index - 1] if index > 0 else None
            fill_side = str(fill.get("side") or "").upper()
            prior_fav = _fav(prior_row or {})
            fill_fav = _fav(fill_row)
            if prior_fav == fill_side and fill_fav in {"YES", "NO"} and fill_fav != fill_side:
                transition = "FLIPPED_AGAINST_FILL_SIDE"
            elif prior_fav == fill_side and fill_fav is None:
                transition = "FAVORED_REGIME_DISAPPEARED"
            elif fill_fav == fill_side:
                transition = "FILL_SIDE_STILL_FAVORED"
            else:
                transition = "OTHER"
            details.append({
                "ticker": ticker,
                "fill_side": fill_side,
                "fill_price": str(fill.get("price")),
                "fill_quantity": str(fill.get("quantity")),
                "fill_observed_at": str(fill.get("observed_at")),
                "seconds_to_expiry_at_fill": str(fill_row.get("seconds_to_expiry")),
                "prior_favored_side": prior_fav,
                "prior_favored_probability": str(_prob(prior_row or {})) if _prob(prior_row or {}) is not None else None,
                "fill_observation_favored_side": fill_fav,
                "fill_observation_favored_probability": str(_prob(fill_row)) if _prob(fill_row) is not None else None,
                "transition": transition,
                "resolution_side": market.get("resolution_side"),
                "settled_shadow_trading_pnl": market.get("settled_shadow_trading_pnl"),
                "classification": "SHADOW_SETTLED_NOT_REALIZED",
            })

    against = [row for row in details if row["transition"] in {"FLIPPED_AGAINST_FILL_SIDE", "FAVORED_REGIME_DISAPPEARED"}]
    flips = [row for row in details if row["transition"] == "FLIPPED_AGAINST_FILL_SIDE"]
    persists = [row for row in details if row["transition"] == "FILL_SIDE_STILL_FAVORED"]
    return {
        "type": "GEMINI_L5_FILL_REGIME_TRANSITION_AUDIT",
        "strict_fills": len(details),
        "adverse_regime_transition_fills": len(against),
        "flipped_against_fill_side": len(flips),
        "fill_side_still_favored": len(persists),
        "adverse_transition_rate": str(D(len(against)) / D(len(details))) if details else None,
        "classification": "SHADOW_SETTLED_NOT_REALIZED",
        "detail": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("score", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(load_jsonl(args.raw), load_score(args.score))
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
