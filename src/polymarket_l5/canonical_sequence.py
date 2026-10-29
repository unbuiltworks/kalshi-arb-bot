"""Canonical per-trade normalized L5 dataset (schema v1).

One row per public BUY/SELL fill, sequential within each window, carrying the
full consolidation schema:

  window/market identity, asset, 5m/15m duration, timestamp,
  seconds-to-settlement, Up/Down, price, quantity, maker/taker (where
  determinable), cumulative Up/Down inventory, matched inventory, residual
  directional inventory, effective matched-pair cost, settlement outcome,
  and source/provenance.

Built deterministically from the committed harvest artifacts:
  * analysis/l5/l5_all_trades.csv        (fills, full API backfill)
  * analysis/l5/l5_window_sequences.csv  (sequential inventory accounting)
  * analysis/l5/l5_windows_summary.csv   (settlement outcome + source)
  * data/l5/raw/trades_takeronly.jsonl   (taker-fill fingerprints)

Maker/taker honesty: Polymarket's public feeds do not label maker-ness on
/activity rows. The takerOnly trades feed positively identifies TAKER fills;
fills absent from it within the feed's covered span are labelled "maker";
fills outside the takerOnly coverage span are "unknown". Never treat
"unknown" as maker.

Output: analysis/l5/l5_canonical_sequence.csv.gz + a small provenance JSON.
Deterministic: same inputs -> byte-identical CSV (stable ordering and number
formatting), asserted by tests.

    python3 -m src.polymarket_l5.canonical_sequence
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import config
from .fill_toxicity import _fingerprint_of_row, load_taker_fingerprints

OUT_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT_META = config.OUTPUT_DIR / "l5_canonical_sequence.meta.json"

SCHEMA_VERSION = "1"

COLUMNS = (
    "schema_version", "window_id", "market_slug", "asset", "duration_minutes",
    "trade_seq", "timestamp", "datetime_utc", "seconds_from_window_start",
    "seconds_to_settlement", "outcome", "side", "price", "quantity", "dollars",
    "maker_taker", "cum_up_qty", "cum_up_spend", "cum_down_qty",
    "cum_down_spend", "matched_qty", "matched_pair_unit_cost",
    "residual_qty_signed", "residual_side", "settlement_outcome",
    "resolution_source", "source", "transaction_hash",
)


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        return f"{v:.10g}"
    return str(v)


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_taker_flags(trades_csv: Path, cache_root: Path) -> Tuple[Dict[tuple, str], Tuple[Optional[int], Optional[int]]]:
    """Map (tx, ts, outcome, price6, shares6) -> maker/taker/unknown.

    Coverage span of the takerOnly feed bounds where 'maker' can be asserted.
    """
    taker_fps = load_taker_fingerprints(cache_root)
    ts_values = [fp[1] for fp in taker_fps if fp[1] is not None]
    span = (min(ts_values), max(ts_values)) if ts_values else (None, None)

    flags: Dict[tuple, str] = {}
    with open(trades_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = int(float(row["timestamp"]))
            key = (
                row.get("transaction_hash", ""), ts, row.get("outcome", ""),
                round(_f(row.get("price")) or 0.0, 6),
                round(_f(row.get("shares")) or 0.0, 6),
            )
            if _fingerprint_of_row(row) in taker_fps:
                flags[key] = "taker"
            elif span[0] is not None and span[0] <= ts <= span[1]:
                flags[key] = "maker"
            else:
                flags[key] = "unknown"
    return flags, span


def build() -> Dict[str, Any]:
    sequences = config.OUTPUT_DIR / config.SEQUENCES_CSV
    summary_csv = config.OUTPUT_DIR / config.SUMMARY_CSV
    trades_csv = config.OUTPUT_DIR / config.TRADES_CSV

    winners: Dict[str, Tuple[str, str]] = {}
    with open(summary_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            winners[row["window_id"]] = (
                row.get("winning_outcome", "") or "",
                row.get("resolution_source", "") or "",
            )

    flags, span = load_taker_flags(trades_csv, config.RAW_DIR)

    counts = {"rows": 0, "taker": 0, "maker": 0, "unknown": 0,
              "settlement_labelled": 0}
    with open(sequences, newline="", encoding="utf-8") as fh, \
            gzip.open(OUT_CSV, "wt", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(COLUMNS)
        for row in csv.DictReader(fh):
            ts = int(float(row["timestamp"]))
            key = (
                row.get("transaction_hash", ""), ts, row.get("outcome", ""),
                round(_f(row.get("price")) or 0.0, 6),
                round(_f(row.get("shares")) or 0.0, 6),
            )
            mt = flags.get(key, "unknown")
            counts[mt] += 1
            winner, res_src = winners.get(row["window_id"], ("", ""))
            if winner:
                counts["settlement_labelled"] += 1
            excess = _f(row.get("excess_qty"))
            residual_side = ("Up" if excess and excess > 0 else
                             "Down" if excess and excess < 0 else "")
            writer.writerow([_fmt(v) for v in (
                SCHEMA_VERSION, row["window_id"], row.get("market_slug", ""),
                row.get("asset", ""), row.get("window_minutes", ""),
                row.get("trade_seq", ""), ts, row.get("datetime_utc", ""),
                row.get("seconds_from_window_start", ""),
                row.get("seconds_to_expiry", ""), row.get("outcome", ""),
                row.get("side", ""), _f(row.get("price")), _f(row.get("shares")),
                _f(row.get("dollars")), mt, _f(row.get("cum_up_qty")),
                _f(row.get("cum_up_spend")), _f(row.get("cum_down_qty")),
                _f(row.get("cum_down_spend")), _f(row.get("matched_qty")),
                _f(row.get("matched_pair_unit_cost")), excess, residual_side,
                winner, res_src, "polymarket_public_activity",
                row.get("transaction_hash", ""),
            )])
            counts["rows"] += 1

    digest = hashlib.sha256(gzip.open(OUT_CSV, "rb").read()).hexdigest()[:16]
    meta = {
        "schema_version": SCHEMA_VERSION,
        "columns": list(COLUMNS),
        "rows": counts["rows"],
        "maker_taker_counts": {k: counts[k] for k in ("taker", "maker", "unknown")},
        "takeronly_coverage_span_epoch": list(span),
        "settlement_labelled_rows": counts["settlement_labelled"],
        "content_sha256_16": digest,
        "sources": [str(sequences), str(summary_csv), str(trades_csv),
                    str(config.RAW_DIR / "trades_takeronly.jsonl")],
        "note": ("'maker' asserted only inside takerOnly feed coverage; "
                 "'unknown' outside it. Never treat unknown as maker."),
    }
    OUT_META.write_text(json.dumps(meta, indent=1))
    return meta


def main() -> int:
    meta = build()
    print(json.dumps({k: meta[k] for k in
                      ("rows", "maker_taker_counts", "settlement_labelled_rows",
                       "content_sha256_16")}, indent=1))
    print(f"wrote {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
