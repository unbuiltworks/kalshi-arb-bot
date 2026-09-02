"""Driver: run the Gemini BTC-5m transfer backtest and emit the report.

Reads the extracted BBO artifact (tier 1) and the complete per-window
public prints, replays every covered window under both fill assumptions,
evaluates the pre-entry trailing two-sided-flow gates with no hindsight,
computes tier-2 flow ceilings over the full print universe, and writes
analysis/l5/transfer_backtest_gemini.json. THEORETICAL only.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.polymarket_l5 import transfer_backtest as tb  # noqa: E402

BBO = Path("data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz")
PRINTS = Path("data/transfer_backtest/gemini_prints_btc05m.jsonl.gz")
OUT = Path("analysis/l5/transfer_backtest_gemini.json")
TRAILS = (30.0, 60.0, 120.0)


def day_of(event: str) -> str:
    return event[len("BTC05M"):len("BTC05M") + 6]


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def summarize(rows: list[dict], bbo, prints_by_symbol) -> dict:
    settled = []
    for r in rows:
        if r["status"] == "ONE_SIDED_HOLD":
            settled.append(tb.settle_hold(r, bbo.get(f"GEMI-{r['event']}-UP", [])))
        else:
            settled.append(r)
    by = defaultdict(list)
    for r in settled:
        by[r["status"]].append(r)
    qualifying = [r for r in settled if r["status"] in
                  ("MATCHED", "ONE_SIDED_HOLD", "NO_FILL_TIMEOUT")]
    fills = [r for r in qualifying if r["status"] in ("MATCHED", "ONE_SIDED_HOLD")]
    matched = by.get("MATCHED", [])
    holds = by.get("ONE_SIDED_HOLD", [])
    days = {day_of(r["event"]) for r in qualifying} or {"-"}
    hold_pnls = [D(r["hold_pnl_theoretical"]) for r in holds
                 if r.get("hold_pnl_theoretical") is not None]
    pair_pnls = [D(r["pair_pnl_theoretical"]) for r in matched]
    out = {
        "windows_considered": len(settled),
        "v1_abstain": len(by.get("V1_ABSTAIN_FLAT", [])),
        "no_fee_safe": len(by.get("NO_FEE_SAFE_MAKER_PRICE", [])),
        "qualifying_campaigns": len(qualifying),
        "distinct_days": sorted(days),
        "qualifying_per_day": round(len(qualifying) / max(len(days), 1), 2),
        "first_leg_fill_rate": round(len(fills) / len(qualifying), 4) if qualifying else None,
        "p_complement_given_first_fill": round(len(matched) / len(fills), 4) if fills else None,
        "one_sided_hold_rate": round(len(holds) / len(qualifying), 4) if qualifying else None,
        "matched_completions": len(matched),
        "matched_per_day": round(len(matched) / max(len(days), 1), 2),
        "median_pair_cost_after_fees": (str(sorted(D(r["pair_cost_after_fees"]) for r in matched)[len(matched) // 2]) if matched else None),
        "mean_time_to_complement_s": round(mean(r["time_to_complement_s"] for r in matched), 1) if matched else None,
        "mean_residual_duration_s": round(mean(r.get("residual_s", 0.0) for r in holds), 1) if holds else None,
        "pair_pnl_theoretical_total": str(sum(pair_pnls)) if pair_pnls else "0",
        "hold_pnl_theoretical_total": str(sum(hold_pnls)) if hold_pnls else "0",
        "hold_pnl_ambiguous_outcomes_excluded": sum(1 for r in holds if r.get("hold_pnl_theoretical") is None),
        "holds_residual_won": sum(1 for r in holds if r.get("residual_won") is True),
        "holds_residual_lost": sum(1 for r in holds if r.get("residual_won") is False),
        "adverse_selection_by_side": {
            side: {"holds": len([r for r in holds if r["side"] == side]),
                   "won": len([r for r in holds if r["side"] == side and r.get("residual_won") is True])}
            for side in ("YES", "NO")},
        "net_theoretical_total": str((sum(pair_pnls) if pair_pnls else D(0)) + (sum(hold_pnls) if hold_pnls else D(0))),
    }
    return out


def gate_eval(rows: list[dict], prints_by_symbol) -> dict:
    """Trailing pre-entry two-sided-flow gates, no hindsight."""
    out = {}
    base_matched = [r for r in rows if r["status"] == "MATCHED"]
    base_holds = [r for r in rows if r["status"] == "ONE_SIDED_HOLD"]
    for trail in TRAILS:
        allowed, skipped = [], []
        for r in rows:
            if "entry_t" not in r:
                continue
            ok = tb.trailing_flow_two_sided(prints_by_symbol, r["entry_t"], trail)
            (allowed if ok else skipped).append(r)
        out[f"trail_{int(trail)}s"] = {
            "allowed": len(allowed), "skipped": len(skipped),
            "matched_kept": len([r for r in allowed if r["status"] == "MATCHED"]),
            "matched_lost": len([r for r in skipped if r["status"] == "MATCHED"]),
            "holds_kept": len([r for r in allowed if r["status"] == "ONE_SIDED_HOLD"]),
            "holds_avoided": len([r for r in skipped if r["status"] == "ONE_SIDED_HOLD"]),
            "holds_avoided_pct": round(100 * len([r for r in skipped if r["status"] == "ONE_SIDED_HOLD"]) / max(len(base_holds), 1), 1),
            "matched_lost_pct": round(100 * len([r for r in skipped if r["status"] == "MATCHED"]) / max(len(base_matched), 1), 1),
        }
    return out


def main() -> int:
    bbo = tb.load_bbo(BBO)
    prints = tb.load_prints(PRINTS)
    tier1_syms = []
    for sym, rows in bbo.items():
        exp = tb.ticker_expiry_epoch(tb.event_of_symbol(sym))
        if exp and any(tb.MIN_TTE_S <= exp - r["t"] <= tb.ENTRY_MAX_TTE_S
                       for r in rows):
            tier1_syms.append(sym)
    tier1_syms.sort()

    report: dict = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "labeling": "ALL P&L THEORETICAL — nothing realized; queue position unobservable, optimistic tier is an upper bound",
        "provenance": {
            "bbo": "collector tapes data/policy_d_port/tape_*.jsonl (branch claude/polymarket-l5-historical-analysis-2ih9ui), Gemini venue bbo rows",
            "prints": "GET https://api.gemini.com/v1/trades/{symbol} complete per settled window, fetched 2026-09-02",
            "mechanics_vendored_from": "feature/gemini-l5-dynamic-q1-v1policy @ 80f40c86",
            "v1_gate_model_sha": tb.load_gate()["source_model_sha256_16"],
        },
        "tier1_symbols": len(tier1_syms),
    }

    for mode in ("optimistic", "strict"):
        rows = [tb.replay_window(s, bbo[s], prints.get(s, []), mode)
                for s in tier1_syms]
        rows = [r for r in rows if r["status"] != "BAD_TICKER"]
        report[f"tier1_{mode}"] = summarize(rows, bbo, prints)
        report[f"tier1_{mode}_gates"] = gate_eval(rows, prints)
        if mode == "optimistic":
            report["tier1_rows_optimistic"] = rows

    # tier-2 flow ceiling over the full print universe
    daily = defaultdict(lambda: {"windows": 0, "two_sided": 0, "empty": 0,
                                 "prints": 0})
    for sym, rows in prints.items():
        exp = tb.ticker_expiry_epoch(tb.event_of_symbol(sym))
        if exp is None:
            continue
        flow = tb.window_flow(rows, exp)
        d = daily[day_of(tb.event_of_symbol(sym))]
        d["windows"] += 1
        d["two_sided"] += int(flow["two_sided"])
        d["empty"] += int(flow["span_prints"] == 0)
        d["prints"] += flow["span_prints"]
    report["tier2_flow_ceiling_by_day"] = {
        day: {**v, "two_sided_pct": round(100 * v["two_sided"] / v["windows"], 1)}
        for day, v in sorted(daily.items())}
    allw = sum(v["windows"] for v in daily.values())
    report["tier2_flow_ceiling_overall"] = {
        "windows": allw,
        "two_sided_pct": round(100 * sum(v["two_sided"] for v in daily.values()) / allw, 1) if allw else None,
        "empty_pct": round(100 * sum(v["empty"] for v in daily.values()) / allw, 1) if allw else None,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1, default=str))
    slim = {k: v for k, v in report.items() if k != "tier1_rows_optimistic"}
    print(json.dumps(slim, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
