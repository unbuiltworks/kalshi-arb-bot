"""Evaluate the pre-entry drift test against the frozen bars.

The rules are NOT restated here as prose to be followed by hand; they are
enforced in code, and the constants below must match
`analysis/l5/PREREG_PRE_ENTRY_DRIFT.md` §3. A test asserts they do.

WHY THE SPLIT IS NOT COMPUTED WHEN UNDERPOWERED
------------------------------------------------
The prereg says an UNDERPOWERED result reports "the exact deficit and nothing
else". That is easy to honour on the first read and hard on the fourth, when
the numbers are sitting right there and the sample is one opportunity short.
So `evaluate` does not compute the median split at all below P1 -- there is no
ratio in the returned object to be tempted by, and none was ever calculated.
Peeking is not forbidden by convention here; it is unavailable.
"""

import json
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

PREREG = "analysis/l5/PREREG_PRE_ENTRY_DRIFT.md"

# frozen in the prereg -- changing these without changing the prereg is a bug
MIN_OPPORTUNITIES = 30      # P1
MIN_RATE_RATIO = 1.50       # P2
MIN_LOW_SHARE = 0.55        # P3
PRIMARY_LOOKBACK_S = 60.0

TAG = "PMUS_PERSISTENCE_WINDOW"
FLOW_RANKED = "flow_ranked"

DROP_NOT_FLOW_RANKED = "not_flow_ranked"
DROP_UNRESOLVABLE_BUCKET = "unresolvable_bucket"
DROP_DRIFT_UNRESOLVABLE = "drift_unresolvable"
DROP_NO_DRIFT_FIELD = "no_drift_field"


def parse(lines: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for line in lines:
        i = line.find(TAG)
        if i < 0:
            continue
        try:
            row = json.loads(line[i + len(TAG):].strip())
        except ValueError:
            continue
        if isinstance(row, dict) and "buckets" in row:
            out.append(row)
    return out


def usable(row: Mapping[str, Any]):
    """(kept, reason). Unresolvable drift is EXCLUDED, never read as zero."""
    if row.get("selection") != FLOW_RANKED:
        return False, DROP_NOT_FLOW_RANKED
    if row.get("unresolvable_buckets"):
        return False, DROP_UNRESOLVABLE_BUCKET
    d = row.get("pre_entry_drift")
    if not isinstance(d, Mapping):
        return False, DROP_NO_DRIFT_FIELD
    if not d.get("resolvable") or d.get("drift_abs") is None:
        return False, DROP_DRIFT_UNRESOLVABLE
    return True, None


def _totals(rows: Sequence[Mapping[str, Any]]):
    obs = opp = 0
    for r in rows:
        for b in r["buckets"].values():
            obs += int(b["observations"])
            opp += int(b["opportunities"])
    return obs, opp


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def evaluate(lines: Sequence[str]) -> Dict[str, Any]:
    rows = parse(lines)
    kept, dropped = [], {}
    for r in rows:
        ok, why = usable(r)
        if ok:
            kept.append(r)
        else:
            dropped[why] = dropped.get(why, 0) + 1
    obs, opp = _totals(kept)
    out: Dict[str, Any] = {
        "prereg": PREREG, "primary_lookback_s": PRIMARY_LOOKBACK_S,
        "rows_seen": len(rows), "windows_used": len(kept),
        "dropped": dropped, "observations": obs, "opportunities": opp,
        "p1_required": MIN_OPPORTUNITIES,
    }
    if opp < MIN_OPPORTUNITIES:
        out["verdict"] = "UNDERPOWERED"
        out["deficit"] = MIN_OPPORTUNITIES - opp
        out["why"] = ("P1 not met: %d of %d opportunities. Per the prereg an "
                      "underpowered result reports the deficit and nothing "
                      "else, so the median split was NOT computed."
                      % (opp, MIN_OPPORTUNITIES))
        return out

    vals = [float(r["pre_entry_drift"]["drift_abs"]) for r in kept]
    med = _median(vals)
    low = [r for r in kept
           if float(r["pre_entry_drift"]["drift_abs"]) <= med]
    high = [r for r in kept
            if float(r["pre_entry_drift"]["drift_abs"]) > med]
    lo_obs, lo_opp = _totals(low)
    hi_obs, hi_opp = _totals(high)
    lo_rate = (lo_opp / lo_obs) if lo_obs else None
    hi_rate = (hi_opp / hi_obs) if hi_obs else None
    ratio = (lo_rate / hi_rate) if (lo_rate is not None and hi_rate) else None
    share = (lo_opp / opp) if opp else None
    p2 = ratio is not None and ratio >= MIN_RATE_RATIO
    p3 = share is not None and share >= MIN_LOW_SHARE
    out.update({
        "median_drift_abs": med,
        "low_drift": {"windows": len(low), "observations": lo_obs,
                      "opportunities": lo_opp, "rate": lo_rate},
        "high_drift": {"windows": len(high), "observations": hi_obs,
                       "opportunities": hi_opp, "rate": hi_rate},
        "p2_rate_ratio": ratio, "p2_required": MIN_RATE_RATIO, "p2_met": p2,
        "p3_low_share": share, "p3_required": MIN_LOW_SHARE, "p3_met": p3,
        "verdict": "PASS" if (p2 and p3) else "FAIL",
    })
    if not (p2 and p3):
        out["why"] = ("P1 met but %s failed -- the lever is dead under the "
                      "frozen rules; secondaries do not rescue it."
                      % (", ".join(x for x, ok in (("P2", p2), ("P3", p3))
                                   if not ok)))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    a = ap.parse_args(argv)
    src = open(a.path, encoding="utf-8") if a.path else sys.stdin
    with src:
        print(json.dumps(evaluate(src.readlines()), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
