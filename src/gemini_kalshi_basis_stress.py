"""Conservative dollar stress for Gemini/Kalshi BTC settlement-shadow summaries.

Transforms retrospective executable-shadow scenario output into a forward-looking
stress metric using the existing 95% Wilson upper bound on mismatch probability.
For each consumable paired contract, an adverse index mismatch can reduce the
complementary payout by at most $1 versus the synthetic 1x lock assumption. The
stress P&L therefore subtracts ``upper_mismatch_probability * contracts`` from
apparent locked P&L. Favorable 2x mismatches are intentionally ignored.

This is read-only research accounting, not a live entry rule or forecast.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

D = Decimal


def stress_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Add a one-sided Wilson-95 adverse-basis stress estimate to one scenario."""
    upper = scenario.get("consumable_mismatch_rate_wilson_upper_95")
    contracts = D(str(scenario.get("consumable_contracts", "0")))
    apparent = D(str(scenario.get("apparent_locked_profit_dollars", "0")))

    if contracts < 0:
        raise ValueError("consumable_contracts cannot be negative")
    if upper is None:
        stress_loss = None
        stress_pnl = None
    else:
        p = D(str(upper))
        if p < 0 or p > 1:
            raise ValueError("Wilson upper mismatch probability must be in [0,1]")
        # One adverse basis mismatch can move a paired contract from the assumed
        # 1x payout to 0x, a $1 swing per contract. Ignore favorable 2x outcomes.
        stress_loss = p * contracts
        stress_pnl = apparent - stress_loss

    out = dict(scenario)
    out.update({
        "wilson95_adverse_basis_stress_loss_dollars": (
            str(stress_loss) if stress_loss is not None else None
        ),
        "wilson95_adverse_basis_stress_pnl_dollars": (
            str(stress_pnl) if stress_pnl is not None else None
        ),
    })
    return out


def stress_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Stress entry/survival and every basis-margin sensitivity cohort."""
    out = dict(summary)
    if isinstance(summary.get("entry"), dict):
        out["entry"] = stress_scenario(summary["entry"])
    if isinstance(summary.get("survival"), dict):
        out["survival"] = {
            str(k): stress_scenario(v) for k, v in summary["survival"].items()
        }

    sensitivity: dict[str, Any] = {}
    for margin, cohort in (summary.get("basis_margin_sensitivity") or {}).items():
        c = dict(cohort)
        if isinstance(cohort.get("entry"), dict):
            c["entry"] = stress_scenario(cohort["entry"])
        if isinstance(cohort.get("survival"), dict):
            c["survival"] = {
                str(k): stress_scenario(v) for k, v in cohort["survival"].items()
            }
        sensitivity[str(margin)] = c
    if sensitivity:
        out["basis_margin_sensitivity"] = sensitivity

    out.update({
        "basis_stress_method": "WILSON95_UPPER_MISMATCH_PROBABILITY_X_$1_PER_CONSUMABLE_CONTRACT",
        "basis_stress_interpretation": "ONE_SIDED_ADVERSE_STRESS_NOT_EXPECTED_VALUE_OR_FORECAST",
        "favorable_basis_mismatch_credit": False,
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path, help="JSON output from gemini_kalshi_btc_ws_settlement")
    args = parser.parse_args()
    payload = json.loads(args.summary.read_text(encoding="utf-8"))
    print(json.dumps(stress_summary(payload), separators=(",", ":")))


if __name__ == "__main__":
    main()
