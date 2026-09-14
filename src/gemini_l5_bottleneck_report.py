"""Decompose canonical Gemini L5 replay into behavior, shell, and execution bottlenecks.

Consumes the historical-backfill row artifact.  This does not alter strategy or
risk.  It separates three questions that must not be conflated:

1. SHARED L5 BEHAVIOR: what action did the canonical model recommend/sample?
2. VENUE-SPECIFIC EXECUTION: what did the shell actually act, and did maker
   orders strict-trade-through?
3. VENUE-SPECIFIC ECONOMICS/SETTLEMENT: what matched/residual economics resulted?

All historical fills remain shadow evidence, never venue FILLED/SETTLED/REALIZED.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
ROWS = ROOT / "analysis/l5/gemini_l5_canonical_backfill_rows.jsonl"
OUT = ROOT / "analysis/l5/gemini_l5_bottleneck_report.json"


def _gain(d: Mapping[str, Any]) -> float | None:
    """Held-side maker mark minus running average; explanatory only."""
    inv = d.get("inventory_before") or {}
    book = d.get("book") or {}
    try:
        residual = float(inv.get("residual") or 0)
        if residual > 0:
            return float(book["yes_bid"]) - float(inv["avg_up"])
        if residual < 0:
            return float(book["no_bid"]) - float(inv["avg_dn"])
    except (TypeError, ValueError, KeyError):
        return None
    return None


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 2) if d else None


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 4) if xs else None


def _partition(rows: list[dict]) -> dict:
    decisions = [d for r in rows for d in (r.get("canonical_decisions") or [])]
    post = [d for d in decisions if abs(float((d.get("inventory_before") or {}).get("residual") or 0)) > 0]
    orders = [o for r in rows for o in (r.get("canonical_orders") or [])]
    rec = Counter(str(d.get("v2_recommended") or "unknown") for d in post)
    acted = Counter(str(d.get("acted") or "unknown") for d in post)
    refusals = Counter((str(d.get("refusal") or "none").split(":", 1)[0]) for d in post)
    gains = [(d, _gain(d)) for d in post]
    gains = [(d, g) for d, g in gains if g is not None]

    def rel(rel: str) -> dict:
        ds = [d for d in post if d.get("v2_recommended") == rel]
        acted_ds = [d for d in post if d.get("acted") == rel]
        os = [o for o in orders if o.get("relation") == rel]
        fs = [o for o in os if o.get("filled")]
        gs = [g for d, g in gains if d.get("v2_recommended") == rel]
        return {
            "recommended_decisions": len(ds),
            "recommended_share_post_fill_pct": _pct(len(ds), len(post)),
            "acted_decisions": len(acted_ds),
            "orders": len(os),
            "strict_shadow_fills": len(fs),
            "order_to_strict_fill_pct": _pct(len(fs), len(os)),
            "gain_per_unit_p50": _median(gs),
            "positive_gain_decisions": sum(g > 0 for g in gs),
            "positive_gain_share_pct": _pct(sum(g > 0 for g in gs), len(gs)),
        }

    matched_windows = sum(1 for r in rows if int(r.get("matched_increments") or 0) > 0)
    fill_windows = sum(1 for r in rows if int(r.get("fills") or 0) > 0)
    positive = [g for _, g in gains if g > 0]
    return {
        "windows": len(rows),
        "windows_with_any_strict_shadow_fill": fill_windows,
        "windows_with_matched_increment": matched_windows,
        "decisions": len(decisions),
        "post_fill_decisions": len(post),
        "recommended_counts": dict(rec),
        "acted_counts": dict(acted),
        "shell_refusal_counts": dict(refusals),
        "complete": rel("complete"),
        "extend": rel("extend"),
        "gain_per_unit": {
            "measured_decisions": len(gains),
            "positive_decisions": len(positive),
            "positive_share_pct": _pct(len(positive), len(gains)),
            "p50": _median([g for _, g in gains]),
            "min": round(min((g for _, g in gains), default=0.0), 4) if gains else None,
            "max": round(max((g for _, g in gains), default=0.0), 4) if gains else None,
            "meaning": "held-side maker mark minus running average; explanatory state, never a COMPLETE gate",
        },
        "bottleneck_decomposition": {
            "shared_behavior": "frequency of canonical COMPLETE vs EXTEND recommendations after inventory exists",
            "venue_shell": "recommended actions converted to HOLD/refusal before an order exists",
            "venue_execution": "acted maker orders converted to strict trade-through shadow fills",
            "economics": "matched pair and residual settlement P&L remain separate in canonical backfill",
        },
    }


def run() -> dict:
    rows = [json.loads(x) for x in ROWS.read_text(encoding="utf-8").splitlines() if x.strip()]
    by = {h: _partition([r for r in rows if r.get("partition") == h]) for h in ("5m", "15m")}
    report = {
        "status": "HISTORICAL_REPLAY_DIAGNOSTIC",
        "labels": "READ-ONLY; strict trade-through is hypothetical maker-fill evidence; no realized P&L",
        "finding_classification": {
            "behavior": "SHARED L5 BEHAVIOR",
            "shell_and_fill_conversion": "VENUE-SPECIFIC EXECUTION",
            "fees_rebates_settlement": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
        },
        "by_horizon": by,
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_BOTTLENECK " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
