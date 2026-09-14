"""Scan every retained BBO observation while canonical Gemini inventory is held.

Purpose: test whether favorable gain_per_unit / cheap-complement states exist in
the venue tape but are missed by the current action cadence.  gain_per_unit is
EXPLANATORY ONLY; this module does not turn gain into a trading rule.

Classification:
- gain/state prevalence: SHARED L5 BEHAVIOR research
- whether those states are observable/executable at Gemini touch: VENUE-SPECIFIC EXECUTION
- pair cost includes known Gemini maker fees: VENUE-SPECIFIC ECONOMICS

All fills are historical strict-trade-through shadow evidence.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from decimal import Decimal as D
from typing import Any

from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/transfer_backtest"
ROWS = ROOT / "analysis/l5/gemini_l5_canonical_backfill_rows.jsonl"
OUT = ROOT / "analysis/l5/gemini_l5_gain_state_scan.json"
ONE = D("1")

PARTS = {
    "5m": DATA / "gemini_bbo_btc05m.jsonl.gz",
    "15m": DATA / "gemini_bbo_btc15m.jsonl.gz",
}


def _d(x: Any) -> D:
    return D(str(x))


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 2) if d else None


def _p50(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 4) if xs else None


def _scan_partition(label: str, rows: list[dict], bbo: dict[str, list[dict]]) -> dict:
    obs = 0
    positive = 0
    pair99 = 0
    pair97 = 0
    windows_positive: set[str] = set()
    windows_pair99: set[str] = set()
    windows_pair97: set[str] = set()
    max_gain_by_window: list[float] = []
    first_positive_latency: list[float] = []
    windows_with_fills = 0
    decision_positive = 0
    decision_measured = 0

    for r in rows:
        symbol = r.get("symbol")
        path = r.get("inventory_path") or []
        if not path or symbol not in bbo:
            continue
        windows_with_fills += 1
        points = sorted(bbo[symbol], key=lambda x: float(x.get("t") or 0))
        fills = sorted(path, key=lambda x: float(x.get("t") or 0))
        local_gains: list[float] = []
        first_fill_t = float(fills[0]["t"])
        first_pos_t = None
        j = -1
        for q in points:
            t = float(q.get("t") or 0)
            while j + 1 < len(fills) and float(fills[j + 1]["t"]) <= t:
                j += 1
            if j < 0:
                continue
            inv = fills[j]
            residual = _d(inv.get("residual"))
            if residual == 0:
                continue
            try:
                if residual > 0:
                    avg = _d(inv["avg_up"])
                    mark = _d(q["bid"])
                else:
                    avg = _d(inv["avg_dn"])
                    mark = ONE - _d(q["ask"])
            except (KeyError, TypeError, ValueError):
                continue
            gain = mark - avg
            obs += 1
            local_gains.append(float(gain))
            if gain > 0:
                positive += 1
                windows_positive.add(symbol)
                if first_pos_t is None:
                    first_pos_t = t
            # maker-touch complement is the same mark on the opposite outcome
            # because NO bid = 1-YES ask and YES bid is explicit.
            comp = ((ONE - _d(q["ask"])) if residual > 0 else _d(q["bid"]))
            eff = avg + comp + tb.maker_fee(avg, D("1")) + tb.maker_fee(comp, D("1"))
            if eff <= D("0.99"):
                pair99 += 1; windows_pair99.add(symbol)
            if eff <= D("0.97"):
                pair97 += 1; windows_pair97.add(symbol)
        if local_gains:
            max_gain_by_window.append(max(local_gains))
        if first_pos_t is not None:
            first_positive_latency.append(first_pos_t - first_fill_t)

        for d in r.get("canonical_decisions") or []:
            inv = d.get("inventory_before") or {}
            try:
                residual = _d(inv.get("residual") or 0)
            except Exception:
                continue
            if residual == 0:
                continue
            book = d.get("book") or {}
            try:
                if residual > 0:
                    g = _d(book["yes_bid"]) - _d(inv["avg_up"])
                else:
                    g = _d(book["no_bid"]) - _d(inv["avg_dn"])
            except (KeyError, TypeError, ValueError):
                continue
            decision_measured += 1
            decision_positive += int(g > 0)

    return {
        "windows_with_fills": windows_with_fills,
        "held_bbo_observations": obs,
        "positive_gain_observations": positive,
        "positive_gain_observation_pct": _pct(positive, obs),
        "windows_with_positive_gain": len(windows_positive),
        "windows_with_positive_gain_pct": _pct(len(windows_positive), windows_with_fills),
        "max_gain_per_window_p50": _p50(max_gain_by_window),
        "first_positive_gain_latency_s_p50": _p50(first_positive_latency),
        "touch_pair_cost_le_099_observations": pair99,
        "windows_with_touch_pair_cost_le_099": len(windows_pair99),
        "touch_pair_cost_le_097_observations": pair97,
        "windows_with_touch_pair_cost_le_097": len(windows_pair97),
        "canonical_postfill_decisions_measured": decision_measured,
        "canonical_positive_gain_decisions": decision_positive,
        "canonical_positive_gain_decision_pct": _pct(decision_positive, decision_measured),
        "reading": "If BBO positive-gain prevalence materially exceeds decision-sampled prevalence, cadence/shell is hiding explanatory states; gain remains non-prescriptive.",
    }


def run() -> dict:
    rows = [json.loads(x) for x in ROWS.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = {
        "status": "HISTORICAL_REPLAY_STATE_SCAN",
        "labels": "READ-ONLY; no orders; strict historical shadow fills; gain is explanatory, not a gate",
        "classification": {
            "gain_state": "SHARED L5 BEHAVIOR",
            "state_observability": "VENUE-SPECIFIC EXECUTION",
            "fee_adjusted_pair_cost": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
        },
        "by_horizon": {},
    }
    for label, path in PARTS.items():
        bbo = tb.load_bbo(path)
        part_rows = [r for r in rows if r.get("partition") == label]
        out["by_horizon"][label] = _scan_partition(label, part_rows, bbo)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    return out


def main() -> int:
    print("GEMINI_L5_GAIN_SCAN " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
