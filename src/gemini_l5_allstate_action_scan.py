"""Evaluate the frozen canonical L5 model at every retained Gemini BBO while inventory is held.

This is a cadence diagnostic only.  It asks whether COMPLETE-capable model states
exist between the sparse action timestamps that the current execution shell
samples.  It does NOT create extra hypothetical orders and does not alter risk.

Inventory/model inputs are inherited from the first canonical post-fill decision
for each held interval; only market price/TTE evolve with the tape.  The frozen
model and lookup definitions remain unchanged.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/transfer_backtest"
ROWS = ROOT / "analysis/l5/gemini_l5_canonical_backfill_rows.jsonl"
OUT = ROOT / "analysis/l5/gemini_l5_allstate_action_scan.json"
PARTS = {
    "5m": DATA / "gemini_bbo_btc05m.jsonl.gz",
    "15m": DATA / "gemini_bbo_btc15m.jsonl.gz",
}


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 2) if d else None


def _p50(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 4) if xs else None


def _relation_prob(prop: dict, rel: str) -> float:
    for x in prop.get("relation_ranked") or []:
        if x.get("relation") == rel:
            try:
                return float(x.get("p") or 0)
            except Exception:
                return 0.0
    return 0.0


def _interval_contexts(row: dict) -> list[dict]:
    """Map each held fill interval to the first canonical post-fill decision.

    That decision contains the exact canonical last_rel/last_size/behavioral
    residual/last outcome price used by lookup v3.  Those inventory inputs stay
    fixed until the next fill; only TTE changes between book observations.
    """
    fills = sorted(row.get("inventory_path") or [], key=lambda x: float(x.get("t") or 0))
    decs = sorted(row.get("canonical_decisions") or [], key=lambda x: float(x.get("t") or 0))
    out = []
    for i, f in enumerate(fills):
        if float(f.get("residual") or 0) == 0:
            continue
        ft = float(f.get("t") or 0)
        next_ft = float(fills[i + 1].get("t") or 1e30) if i + 1 < len(fills) else 1e30
        # Canonical decision whose inventory_before reflects this fill and occurs
        # before any subsequent fill.
        d = next((d for d in decs
                  if float(d.get("t") or 0) >= ft
                  and float(d.get("t") or 0) < next_ft
                  and abs(float((d.get("inventory_before") or {}).get("residual") or 0)) > 0), None)
        if d is None:
            continue
        lk = d.get("lookup") or {}
        inputs = lk.get("state_inputs") or {}
        last_px = inputs.get("last_px_outcome")
        if last_px is None:
            continue
        out.append({
            "from_t": ft, "to_t": next_ft, "decision_t": float(d.get("t") or 0),
            "last_px_outcome": float(last_px),
            "residual_units": float(inputs.get("residual_units") or 0),
            "last_rel": str(lk.get("last_rel") or d.get("last_rel") or "open"),
            "last_size": str(lk.get("last_size_behavioral") or "q<5"),
            "actual_recommended": d.get("v2_recommended"),
            "actual_acted": d.get("acted"),
        })
    return out


def _scan(label: str, rows: list[dict], bbo: dict[str, list[dict]], model: dict) -> dict:
    sampled = 0
    complete_modal = 0
    extend_modal = 0
    complete_p_ge_25 = complete_p_ge_50 = 0
    complete_windows: set[str] = set()
    missed_complete_windows: set[str] = set()
    latencies: list[float] = []
    ttes: list[float] = []
    by_actual = Counter()
    examples: list[dict[str, Any]] = []

    for r in rows:
        sym = r.get("symbol")
        if sym not in bbo:
            continue
        expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(sym))
        if expiry is None:
            continue
        contexts = _interval_contexts(r)
        actual_complete = any(d.get("v2_recommended") == "complete"
                              for d in (r.get("canonical_decisions") or []))
        first_complete_t = None
        for ctx in contexts:
            for q in bbo[sym]:
                t = float(q.get("t") or 0)
                if t < ctx["from_t"] or t >= ctx["to_t"]:
                    continue
                tte = max(expiry - t, 0.0)
                state = state_key(ctx["last_px_outcome"], tte, ctx["residual_units"])
                prop = pv2.proposal(model, state, ctx["last_rel"], ctx["last_size"],
                                    residual=ctx["residual_units"])
                rel = prop.get("recommended_relation")
                pc = _relation_prob(prop, "complete")
                sampled += 1
                complete_modal += int(rel == "complete")
                extend_modal += int(rel == "extend")
                complete_p_ge_25 += int(pc >= 0.25)
                complete_p_ge_50 += int(pc >= 0.50)
                if rel == "complete":
                    complete_windows.add(sym)
                    if not actual_complete:
                        missed_complete_windows.add(sym)
                    if first_complete_t is None:
                        first_complete_t = t
                        latencies.append(t - ctx["from_t"])
                        ttes.append(tte)
                        if len(examples) < 12:
                            examples.append({
                                "symbol": sym, "t": t, "tte_s": round(tte, 2),
                                "seconds_after_fill": round(t - ctx["from_t"], 2),
                                "state": state, "complete_p": pc,
                                "actual_interval_decision_relation": ctx["actual_recommended"],
                                "actual_interval_acted": ctx["actual_acted"],
                            })
        by_actual["actual_complete_window"] += int(actual_complete)

    return {
        "windows": len(rows),
        "held_bbo_states_scored": sampled,
        "modal_complete_states": complete_modal,
        "modal_complete_state_pct": _pct(complete_modal, sampled),
        "modal_extend_states": extend_modal,
        "modal_extend_state_pct": _pct(extend_modal, sampled),
        "complete_probability_ge_25_states": complete_p_ge_25,
        "complete_probability_ge_25_pct": _pct(complete_p_ge_25, sampled),
        "complete_probability_ge_50_states": complete_p_ge_50,
        "complete_probability_ge_50_pct": _pct(complete_p_ge_50, sampled),
        "windows_with_any_between_decision_modal_complete": len(complete_windows),
        "windows_with_between_decision_complete_but_no_actual_complete_decision": len(missed_complete_windows),
        "first_modal_complete_latency_after_fill_s_p50": _p50(latencies),
        "first_modal_complete_tte_s_p50": _p50(ttes),
        "actual_windows_with_complete_recommendation": by_actual["actual_complete_window"],
        "examples": examples,
    }


def run() -> dict:
    model = pv2.load_v2()
    rows = [json.loads(x) for x in ROWS.read_text(encoding="utf-8").splitlines() if x.strip()]
    report = {
        "status": "HISTORICAL_REPLAY_CADENCE_DIAGNOSTIC",
        "classification": "SHARED L5 BEHAVIOR observed through VENUE-SPECIFIC EXECUTION cadence",
        "labels": "READ-ONLY; no extra orders; frozen model only; diagnostic states are not fills or realized P&L",
        "model_sha": model.get("_sha256_16"),
        "by_horizon": {},
    }
    for label, path in PARTS.items():
        report["by_horizon"][label] = _scan(
            label, [r for r in rows if r.get("partition") == label], tb.load_bbo(path), model)
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_ALLSTATE_SCAN " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
