"""The BTC15m-native economic baseline, reconciled to cash.

WHY THIS EXISTS. `src/l5_breakeven.py` and `src/l5_capacity_curve.py` carry
unit economics measured over `analysis/l5/l5_r5f_cohort.json` -- 13 esports
(`aec`), 2 `atc` and 1 MLB (`tsc`) market, and ZERO BTC15m. One of its rows
carries `matched_pair_cost 1.110705`, impossible under the 0.96 BTC15m ceiling.
Those constants were used to compute a BTC15m breakeven of 84.52% and declare
the strategy negative. That was an instrument-cohort error.

THE ONE METRIC THAT RECONCILES is cash:

    net_cash = redemption_usd - gross_acquisition_usd

with cost taken from `usdcSize` (which already contains the fee), per
`analysis/l5/btc15m/decompose.py`. On L5's BTC15m book that is +$16,436.09.

THE MATCHED/RESIDUAL PARTITION DOES NOT SUM TO CASH. matched_pnl +162,256.93
plus residual_pnl -144,065.00 is +18,191.93, which overstates realized cash by
$1,755.84. Any per-unit figure derived from that partition -- pair profit per
pair, residual loss per unit, and therefore any completion-rate breakeven --
inherits the gap and is a DECOMPOSITION, not a cashflow.

AND THE BREAKEVEN FRAMEWORK FAILS VALIDATION ON L5'S OWN DATA. It reports him
clearing breakeven by +0.1219 in TRAIN and +0.0655 OOS, while his realized
return goes +52.03 bp -> +1.08 bp. A 48x swing in cash against a 2x change in
"margin". Completion-rate breakeven does not predict cash and is not used here
as a decision metric.

READ-ONLY. No network, no venue, no orders, no limit touched.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from analysis.l5.btc15m import decompose as DC

#: the cohort these figures are valid for. Reusing them for any other
#: instrument class is the error this module exists to prevent.
INSTRUMENT_CLASS = "BTC15m"

#: the contaminated cohort, named so the mistake is greppable
CONTAMINATED_COHORT = "analysis/l5/l5_r5f_cohort.json"
CONTAMINATED_REASON = ("13 aec esports + 2 atc + 1 tsc MLB, zero BTC15m; "
                       "carries matched_pair_cost 1.110705 which the 0.96 "
                       "BTC15m ceiling forbids")


def baseline(cohort: str = INSTRUMENT_CLASS) -> Dict[str, Any]:
    rows = DC.build(cohort)
    train, oos = DC.split(rows)
    out: Dict[str, Any] = {
        "instrument_class": cohort,
        "reconciles_to": "redemption_usd - gross_acquisition_usd",
        "cost_basis": "usdcSize (contains the fee)",
        "contaminated_cohort_not_used": CONTAMINATED_COHORT,
        "contaminated_reason": CONTAMINATED_REASON,
        "splits": {},
    }
    for name, r in (("all", rows), ("train", train), ("oos", oos)):
        a = DC.agg(r)
        g = [x for x in r if x["net_cash"] is not None]
        mq = sum(x["matched_qty"] for x in g)
        ru = sum(x["residual_abs"] for x in g)
        cash = a["redemption_usd"] - a["gross_acquisition_usd"]
        part = a["matched_pnl"] + a["residual_pnl"]
        fee_free = a["net_cash"] + a["fee_usd"]
        spend = a["gross_acquisition_usd"]
        ff = sorted((x["net_cash"] + x["fee_usd"] for x in g), reverse=True)
        tot = sum(ff) or 1.0
        k1, k10 = max(1, len(ff) // 100), max(1, len(ff) // 10)
        out["splits"][name] = {
            "n_windows": a["n"],
            # --- the metric that reconciles -------------------------------
            "net_cash_usd": round(a["net_cash"], 2),
            "cashflow_identity_usd": round(cash, 2),
            "reconciles": abs(cash - a["net_cash"]) < 0.01,
            "gross_acquisition_usd": round(spend, 2),
            "bp_per_dollar": round(a["bp_per_dollar"], 4),
            "fee_usd": round(a["fee_usd"], 2),
            "fee_bearing_fill_frac": round(a["fee_bearing_fill_frac"], 6),
            # --- our own fee regime, evidenced ----------------------------
            "fee_free_net_usd": round(fee_free, 2),
            "fee_free_bp_per_dollar": round(1e4 * fee_free / spend, 4),
            # --- the partition, flagged as NOT cash -----------------------
            "matched_pnl": round(a["matched_pnl"], 2),
            "residual_pnl": round(a["residual_pnl"], 2),
            "partition_sum": round(part, 2),
            "partition_overstates_cash_by": round(part - a["net_cash"], 2),
            "matched_pairs": round(mq, 2),
            "residual_units": round(ru, 2),
            # --- concentration --------------------------------------------
            "top_1pct_share_of_fee_free_net": round(sum(ff[:k1]) / tot, 4),
            "top_10pct_share_of_fee_free_net": round(sum(ff[:k10]) / tot, 4),
            "win_rate": round(a["win_rate"], 4),
        }
    return out


def main() -> int:
    rep = baseline()
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "native_baseline.json")
    with open(dst, "w") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    print("wrote", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
