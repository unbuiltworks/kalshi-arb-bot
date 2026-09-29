"""Out-of-sample test of the q20->q25 knee. It does not survive.

THE SAMPLE. `data/pmus_btc15m_tape/raw_railway_logs/2026-09-28/` holds 13
hourly Railway log slices the collector echoed to stdout, preserved verbatim
because Railway retention destroys them. Its README says parsing them and
running the completeness rule is "a separate, separately authorized step";
the owner authorized it with "expand the out-of-sample replay as far as our
recorded history allows".

Parsed into `data/pmus_btc15m_tape_oos/oos_tape.jsonl` -- a SEPARATE file.
The frozen CHECKPOINT25 tape is not touched, and nothing here is admitted to
USABLE. Admission is by `pmus_completeness_rule.evaluate` applied
mechanically to every window; none is admitted by hand. 49 windows pass,
0245z->1445z on 2026-09-28, with ZERO overlap with the frozen 25.

SETTLEMENT IS NOT IN THE TAPE. The rows carry `last`, not settlementPx, and
the frozen sample's own notes record a window where settlementPx 1.0000 !=
lastTradePx 0.9900 -- so inferring the winner from `last` is exactly the
inference the accounting rules forbid. Net is therefore BRACKETED by running
both outcomes, and the settlement-dependent numbers are labelled as bounds
until a read-only venue sweep supplies real winners.

Everything else is settlement-independent by construction: decisions never
see the winner (`settle(winner)` runs once, at the end), so completion rate,
fully-completed rate, unmatched time, matched-pair contribution and peak cash
are exact.

No parameter is tuned to this data. Clip is the only thing varied.
"""
from __future__ import annotations

import json
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List

import scripts.pmus_completeness_rule as CR
from scripts.audit_economic_complete import USABLE
from scripts.r7_q10_readiness import walk

OOS_TAPE = "data/pmus_btc15m_tape_oos/oos_tape.jsonl"
CLIPS = (10, 20, 25, 30)
#: 4-hour buckets, to see whether the result is stable or one regime's.
PERIODS = ("02-06z", "06-10z", "10-15z")


def period_of(window: str) -> str:
    hh = int(window[:2])
    return PERIODS[0] if hh < 6 else (PERIODS[1] if hh < 10 else PERIODS[2])


def admitted(path: str = OOS_TAPE):
    by = CR.load_tape(path)
    slugs = [s for s, rows in sorted(by.items())
             if CR.evaluate(s, rows).get("tape_test_passes") and s not in USABLE]
    return by, slugs


def run(clip: int) -> Dict[str, Any]:
    by, slugs = admitted()
    up = [walk(by[s], winner="up", clip=D(clip)) for s in slugs]
    dn = [walk(by[s], winner="down", clip=D(clip)) for s in slugs]
    for r, s in zip(up, slugs):
        r["window"] = s[-5:]
        r["period"] = period_of(r["window"])

    tr = [r for r in up if r["traded"]]
    comp = [r["completion_rate"] for r in tr if r["completion_rate"] is not None]
    um = sorted(r["unmatched_frac"] for r in tr
                if r["unmatched_frac"] is not None)
    pc = sorted(float(r["peak_cash"]) for r in up)
    nu = [float(r["net"]) for r in up]
    nd = [float(r["net"]) for r in dn]

    per_period = {}
    for p in PERIODS:
        rs = [r for r in tr if r["period"] == p]
        if not rs:
            continue
        c = [r["completion_rate"] for r in rs if r["completion_rate"] is not None]
        u = [r["unmatched_frac"] for r in rs if r["unmatched_frac"] is not None]
        per_period[p] = {
            "n": len(rs),
            "completion_rate": (sum(c) / len(c)) if c else None,
            "fully_completed": sum(1 for r in rs if r["fully_completed"]),
            "unmatched_frac_median": statistics.median(u) if u else None,
        }

    n = len(up) or 1
    return {
        "clip": clip, "windows": len(up), "windows_that_traded": len(tr),
        "completion_rate_mean": (sum(comp) / len(comp)) if comp else None,
        "fully_completed": sum(1 for r in tr if r["fully_completed"]),
        "unmatched_frac_median": statistics.median(um) if um else None,
        "peak_cash_median": statistics.median(pc), "peak_cash_max": max(pc),
        "matched_pnl_mean": float(sum((r["matched_pnl"] for r in up), D(0)) / n),
        "net_mean_bracket": [statistics.mean(nu), statistics.mean(nd)],
        "net_median_bracket": [statistics.median(nu), statistics.median(nd)],
        "resid_pnl_bracket": [
            float(sum((r["resid_pnl"] for r in up), D(0)) / n),
            float(sum((r["resid_pnl"] for r in dn), D(0)) / n)],
        "by_period": per_period,
        "labels": ("net and resid are BRACKETS over both outcomes, not "
                   "realized; everything else is settlement-independent"),
        "rebates_assumed": "NONE",
    }


if __name__ == "__main__":
    print(json.dumps({str(c): run(c) for c in CLIPS}, indent=2, default=str))
