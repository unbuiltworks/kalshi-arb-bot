"""Does executable flow exist in the (1.00, 1.03] band our cap forbids?

Implements analysis/l5/PREREG_RESIDUAL_RESCUE_BAND.md and adds nothing to it.

THE GUARD THAT MATTERS, same shape as pmus_drift_report: evaluate() does not
compute the lift at all when R1 is unmet. There is no ratio in the returned
object and none was ever calculated, so reading the tool early cannot leak a
result the prereg says we may not have yet.

THE CLAUSE THE DRIFT PREREG LACKED, AND A DEFECT IN MY FIRST VERSION OF IT.
Pre-entry drift died with a CI of 0.404-1.634 straddling its own 1.50
threshold: a bar that could never have separated the required effect from a
smaller one. The fix is that the interval must clear the DECISION THRESHOLD.

My first implementation required the CI to clear 1.00 instead, which is
vacuous here: the arms are nested, so lift = 1 + rescue_only/capped and the
lower bound exceeds 1.00 whenever a single band-only opportunity exists. A
mutation test caught it -- deleting the clause left the suite green, which is
what an inert guard looks like. R2 now requires the 95% CI lower bound to
exceed MIN_LIFT itself, so a sample too small to distinguish "meets the bar"
from "falls short of it" FAILS rather than passing on a point estimate.

PAIRED, NOT TWO COHORTS. Both arms are evaluated at the same polls of the same
windows, so rescue_opportunities is a superset of capped_opportunities by
construction and any increment is attributable to the band rather than to which
markets were sampled. A rescue count below its capped count is impossible and
is reported as a data defect rather than silently accepted.

UPPER BOUND, NEVER A FILL. A reachable price with a contemporaneous print is
not a fill; queue position and depth are unmeasured. No number here converts
into recovered completions.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Mapping

MIN_CAPPED_OPPORTUNITIES = 30    # R1
MIN_LIFT = 1.20                  # R2 point estimate
MIN_RESCUE_ONLY = 10             # R3
BAND = "(1.00, 1.03]"


def _rows(path: Any) -> List[dict]:
    out = []
    for line in open(path, "rt", encoding="utf-8", errors="replace"):
        line = line.strip()
        if "PMUS_PERSISTENCE_WINDOW" not in line:
            continue
        i = line.find("{")
        if i < 0:
            continue
        try:
            out.append(json.loads(line[i:]))
        except ValueError:
            continue
    return out


def tally(windows: Iterable[Mapping[str, Any]]) -> dict:
    obs = capped = rescue = rescue_only = 0
    used = dropped_no_field = defects = 0
    for w in windows:
        bs = (w or {}).get("buckets") or {}
        if not any("capped_opportunities" in (b or {}) for b in bs.values()):
            dropped_no_field += 1          # predates the instrument
            continue
        used += 1
        for b in bs.values():
            b = b or {}
            obs += int(b.get("observations") or 0)
            c = int(b.get("capped_opportunities") or 0)
            r = int(b.get("rescue_opportunities") or 0)
            o = int(b.get("rescue_only_opportunities") or 0)
            if r < c or (r - c) != o:
                defects += 1               # arms are nested by construction
                continue
            capped += c
            rescue += r
            rescue_only += o
    return {"windows_used": used, "dropped_no_rescue_field": dropped_no_field,
            "nesting_defects": defects, "observations": obs,
            "capped_opportunities": capped, "rescue_opportunities": rescue,
            "rescue_only_opportunities": rescue_only}


def evaluate(windows: Iterable[Mapping[str, Any]]) -> dict:
    t = tally(windows)
    out: Dict[str, Any] = {
        "prereg": "analysis/l5/PREREG_RESIDUAL_RESCUE_BAND.md",
        "band": BAND, **t,
        "r1_required_capped": MIN_CAPPED_OPPORTUNITIES,
        "upper_bound_not_a_fill": (
            "a reachable price with a contemporaneous print is not a fill; "
            "queue position and depth are unmeasured. No count here converts "
            "into recovered completions."),
    }

    if t["nesting_defects"]:
        out["verdict"] = "DATA_DEFECT"
        out["why"] = (
            f"{t['nesting_defects']} buckets violate the nesting the paired "
            "design guarantees (rescue >= capped, and rescue - capped == "
            "rescue_only). The arms are not comparable; no lift was computed.")
        return out

    c = t["capped_opportunities"]
    if c < MIN_CAPPED_OPPORTUNITIES:
        out["verdict"] = "UNDERPOWERED"
        out["deficit"] = MIN_CAPPED_OPPORTUNITIES - c
        out["why"] = (
            f"R1 not met: {c} of {MIN_CAPPED_OPPORTUNITIES} capped "
            "opportunities. Per the prereg an underpowered result reports the "
            "deficit and nothing else, so the lift was NOT computed.")
        return out

    r = t["rescue_only_opportunities"]
    lift = 1.0 + (r / c)
    if r == 0:
        lo = hi = 1.0
    else:
        lr = math.log(r / c)
        se = math.sqrt(1.0 / r + 1.0 / c)
        lo, hi = 1.0 + math.exp(lr - 1.96 * se), 1.0 + math.exp(lr + 1.96 * se)

    out.update({"lift": round(lift, 4),
                "lift_ci95": [round(lo, 4), round(hi, 4)],
                "r2_required_lift": MIN_LIFT,
                "r2_point_met": lift >= MIN_LIFT,
                # the interval must clear the DECISION THRESHOLD, not zero:
                # with nested arms, clearing 1.00 is automatic and proves
                # nothing about whether the sample can support the bar
                "r2_ci_clears_threshold": lo > MIN_LIFT,
                "r3_required_rescue_only": MIN_RESCUE_ONLY,
                "r3_met": r >= MIN_RESCUE_ONLY})

    if not out["r2_point_met"]:
        out["verdict"] = "FAIL"
        out["why"] = (f"lift {lift:.4f} is below the required {MIN_LIFT}. "
                      "The band does not demonstrably unlock flow on PMUS; "
                      "the candidate is dead.")
    elif not out["r2_ci_clears_threshold"]:
        out["verdict"] = "FAIL"
        out["why"] = (
            f"lift {lift:.4f} clears {MIN_LIFT} but its 95% CI lower bound "
            f"{lo:.4f} does not. The sample cannot distinguish meeting the "
            "bar from falling short of it, which is exactly how the "
            "pre-entry-drift test failed. FAIL, not a pass on a point "
            "estimate.")
    elif not out["r3_met"]:
        out["verdict"] = "FAIL"
        out["why"] = (
            f"only {r} of {MIN_RESCUE_ONLY} required opportunities fall "
            f"strictly inside {BAND}. The band is inert on PMUS whatever L5 "
            "does; that is a complete answer.")
    else:
        out["verdict"] = "PASS"
        out["why"] = (
            f"lift {lift:.4f} (CI {lo:.4f}-{hi:.4f}) clears both {MIN_LIFT} "
            f"and the null, on {r} band-only opportunities. Per the prereg "
            "this STAGES a bounded live probe design for owner approval; it "
            "does not authorise one.")
    return out


def run(path: Any) -> dict:
    rows = _rows(path)
    res = evaluate(rows)
    res["rows_seen"] = len(rows)
    return res


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    print(json.dumps(run(sys.argv[1]), indent=1, default=str))
