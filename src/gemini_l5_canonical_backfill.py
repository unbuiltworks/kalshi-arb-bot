"""Accelerated historical backfill for the canonical cross-venue L5 engine.

Replays the SAME Gemini continuous-inventory research path used by the live
queue shadow across retained BTC 5m and BTC 15m tapes. This is deliberately
not a new Gemini strategy: behavioral state/action/accounting comes from the
shared L5 model and venue adapter; only execution/economics are Gemini-specific.

Outputs are SHADOW/THEORETICAL. Historical strict trade-through is not a venue
fill and can never be promoted to FILLED/SETTLED/REALIZED.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import gemini_l5_inventory_cf as invcf
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/transfer_backtest"
OUT = ROOT / "analysis/l5/gemini_l5_canonical_backfill.json"
ROWS_OUT = ROOT / "analysis/l5/gemini_l5_canonical_backfill_rows.jsonl"

PARTITIONS = {
    "5m": {
        "bbo": DATA / "gemini_bbo_btc05m.jsonl.gz",
        "prints": DATA / "gemini_prints_btc05m.jsonl.gz",
    },
    "15m": {
        "bbo": DATA / "gemini_bbo_btc15m.jsonl.gz",
        "prints": DATA / "gemini_prints_15m_tail.jsonl.gz",
    },
}


def _run_partition(label: str, bbo_path: Path, prints_path: Path,
                   model: dict, art: dict) -> tuple[list[dict], dict[str, Any]]:
    bbo = tb.load_bbo(bbo_path)
    prints = tb.load_prints(prints_path)
    symbols = sorted(set(bbo) & set(prints))
    rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
            for s in symbols]
    summary = invcf.summarize(rows)
    summary.update({
        "partition": label,
        "symbols_with_bbo": len(bbo),
        "symbols_with_prints": len(prints),
        "symbols_replayed": len(symbols),
        "bbo_file": bbo_path.name,
        "prints_file": prints_path.name,
    })
    return rows, summary


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    parts: dict[str, Any] = {}
    all_rows: list[dict] = []
    for label, cfg in PARTITIONS.items():
        rows, summary = _run_partition(label, cfg["bbo"], cfg["prints"], model, art)
        parts[label] = summary
        for r in rows:
            all_rows.append({"partition": label, **r})

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model_sha": model.get("_sha256_16"),
        "status": "HISTORICAL_REPLAY",
        "finding_classification": {
            "behavior": "SHARED L5 BEHAVIOR",
            "execution": "VENUE-SPECIFIC EXECUTION",
            "economics": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
        },
        "semantics": {
            "strict_trade_through": "hypothetical maker-fill evidence; not a venue fill",
            "shadow_pnl": "theoretical only",
            "realized": None,
        },
        "partitions": parts,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    with ROWS_OUT.open("w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return report


def main() -> int:
    report = run()
    # One machine-readable line makes CI logs immediately usable without
    # downloading the artifact.
    print("GEMINI_L5_CANONICAL_BACKFILL " + json.dumps(report, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
