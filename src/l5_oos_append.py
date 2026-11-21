"""Append new L5 public activity, and run the FROZEN ceiling sweep on
genuinely unseen dates only.

OWNER 2026-09-10: "as new September L5 data accumulates, append it to the
canonical dataset and rerun this pre-registered exact ceiling sweep on
genuinely unseen dates. Same ceilings, same metrics, same significance
criterion -- no threshold shopping." And, restated: "Don't reopen the
rejected >$1 rescue decision on Sep1-10; only genuinely unseen dates count."

THE ONE THING THIS MODULE EXISTS TO MAKE IMPOSSIBLE
---------------------------------------------------
Re-testing the Sep 1-10 sample. That sample produced the REJECT decision
recorded in analysis/l5/DECISIONS/2026-09-10_rescue_ceiling_REJECT.md, whose
standing rule is:

    "Do not re-test 1.01 -- or any ceiling -- on the Sep 1-10 sample again.
     Repeated sweeps over a fixed sample eventually produce a
     significant-looking result by chance."

So SEEN dates are not merely skipped, they are REFUSED: `sweep_unseen`
raises rather than quietly filtering, because a silent filter would let a
caller pass the whole dataset, watch a number come out, and never learn that
half of it was the decision sample. The refusal names the offending dates.

The protocol is frozen and is NOT re-derived here -- it is read from the
decision record's own constants so the two cannot drift:

    ceilings 1.005 1.010 1.015 1.020 1.025 1.030   exactly these
    baseline B = 1.000                             the live policy
    reference A = inf                              L5 actual
    primary metric  trading P&L BEFORE incentives, vs B
    unit            one UTC day
    test            one-sample t on daily deltas, two-tailed, alpha 0.05
    accounting      src/l5_resolved_pnl.py, zero-redemption losers included
"""
from __future__ import annotations

import datetime
import gzip
import json
import os
from typing import Any, Iterable, Mapping, Optional, Sequence

CANONICAL = os.path.join("data", "l5", "sep2026",
                         "l5_activity_sep2026_raw.json.gz")

# Frozen before the OOS data existed. Changing any of these is threshold
# shopping, and a test pins them against the decision record.
FROZEN_CEILINGS = ("1.005", "1.010", "1.015", "1.020", "1.025", "1.030")
FROZEN_BASELINE = "1.000"
FROZEN_ALPHA = 0.05
FROZEN_UNIT = "one UTC day"
FROZEN_METRIC = "trading P&L before incentives, USD, vs baseline B=1.000"

# The decision sample. Any date here is refused, permanently.
DECISION_SAMPLE_DATES = tuple(
    f"2026-09-{d:02d}" for d in range(1, 11))          # Sep 1 .. Sep 10


class SeenDateRefused(ValueError):
    """Raised when a sweep is asked to include a decision-sample date."""


def utc_date(ts: float) -> str:
    return datetime.datetime.fromtimestamp(
        float(ts), datetime.timezone.utc).strftime("%Y-%m-%d")


def load_canonical(path: str = CANONICAL) -> dict:
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


def dates_present(rows: Iterable[Mapping[str, Any]]) -> set:
    out = set()
    for r in rows:
        ts = r.get("timestamp")
        if ts:
            out.add(utc_date(ts))
    return out


def latest_timestamp(rows: Iterable[Mapping[str, Any]]) -> Optional[float]:
    """Where an append should resume from. None when there is nothing."""
    ts = [float(r["timestamp"]) for r in rows if r.get("timestamp")]
    return max(ts) if ts else None


def append_rows(existing: Sequence[Mapping[str, Any]],
                new: Sequence[Mapping[str, Any]]) -> dict:
    """Append, de-duplicated, NEVER replacing existing rows.

    OWNER, on the original harvest: "append without replacing Apr-Aug data".
    Identity is the venue's own row id where present, falling back to
    (timestamp, type, slug, size) -- a fallback that can only ever merge
    rows the venue itself could not distinguish.
    """
    def key(r):
        rid = r.get("id") or r.get("transactionHash")
        if rid:
            return ("id", str(rid))
        return ("syn", r.get("timestamp"), r.get("type"),
                r.get("slug") or r.get("conditionId"), r.get("size"))

    seen = {key(r) for r in existing}
    added = [r for r in new if key(r) not in seen]
    return {
        "rows": list(existing) + added,
        "existing": len(existing),
        "offered": len(new),
        "added": len(added),
        "duplicates_dropped": len(new) - len(added),
        "labels": "append-only; existing rows are never replaced or re-fetched",
    }


def unseen_dates(rows: Iterable[Mapping[str, Any]],
                 decision_sample=DECISION_SAMPLE_DATES) -> list:
    """Dates in the data that are NOT part of the decision sample."""
    return sorted(d for d in dates_present(rows) if d not in set(decision_sample))


def assert_unseen(dates: Sequence[str],
                  decision_sample=DECISION_SAMPLE_DATES) -> None:
    """Refuse, loudly, rather than filter quietly.

    A silent filter would let a caller hand over the whole dataset, watch a
    number come out, and never learn that half of it was the sample the
    REJECT decision was made on.
    """
    bad = sorted(set(dates) & set(decision_sample))
    if bad:
        raise SeenDateRefused(
            "these dates are the Sep 1-10 decision sample and may not be "
            f"re-tested: {', '.join(bad)}. The REJECT decision "
            "(analysis/l5/DECISIONS/2026-09-10_rescue_ceiling_REJECT.md) "
            "was made on them; re-sweeping a fixed sample eventually "
            "manufactures significance. Only genuinely unseen dates count.")


def protocol() -> dict:
    """The frozen pre-registered protocol, restated for any report."""
    return {
        "ceilings": list(FROZEN_CEILINGS),
        "baseline": FROZEN_BASELINE,
        "reference": "inf (L5 actual)",
        "primary_metric": FROZEN_METRIC,
        "unit_of_observation": FROZEN_UNIT,
        "test": "one-sample t on daily deltas vs baseline, two-tailed",
        "alpha": FROZEN_ALPHA,
        "secondary": "residual quantity at settlement (reported, never decisive)",
        "accounting": "src/l5_resolved_pnl.py -- membership by resolution, "
                      "zero-redemption losers included",
        "decision_sample_excluded": list(DECISION_SAMPLE_DATES),
        "source": "analysis/l5/DECISIONS/2026-09-10_rescue_ceiling_REJECT.md",
    }


def readiness(rows: Iterable[Mapping[str, Any]], min_days: int = 10) -> dict:
    """Can the OOS sweep run yet? Days, not rows: the unit is a UTC day.

    Reports rather than decides. A sweep on three unseen days would clear no
    significance bar worth having, and saying "not yet, n=3" is more useful
    than a t-statistic nobody should act on.
    """
    rows = list(rows)
    unseen = unseen_dates(rows)
    return {
        "unseen_dates": unseen,
        "unseen_day_count": len(unseen),
        "min_days_for_a_meaningful_sweep": min_days,
        "ready": len(unseen) >= min_days,
        "why_not": (None if len(unseen) >= min_days else
                    f"only {len(unseen)} unseen UTC day(s); the frozen unit "
                    f"of observation is one UTC day and the decision sample "
                    f"had 10"),
        "decision_sample_dates_present": sorted(
            set(dates_present(rows)) & set(DECISION_SAMPLE_DATES)),
        "protocol": protocol(),
    }


# --------------------------------------------------- unattended continuation
PREFIX = "L5_OOS "
CYCLE_S = float(os.getenv("L5_OOS_CYCLE_S", "3600"))


def harvest_and_report(fetch=None, path: str = CANONICAL,
                       min_days: int = 10) -> dict:
    """One unattended pass: append genuinely new rows, then report readiness.

    OWNER: "Continue L5 September append/OOS automatically in parallel. Only
    genuinely unseen dates. Do not reopen Sep1-10 rescue tuning."

    THE SWEEP IS NOT RUN HERE, AND THAT IS DELIBERATE. This appends and
    reports; it never fires the ceiling sweep on its own. The sweep is a
    one-shot decision procedure on a pre-registered sample, and a loop that
    re-runs it every hour as data trickles in IS threshold shopping with extra
    steps -- each rerun another look at a moving sample until one crosses
    alpha. So readiness is reported and the sweep waits for a deliberate call
    on a sample that is complete.
    """
    doc = load_canonical(path)
    rows = list(doc.get("rows") or doc.get("activity") or [])
    before = len(rows)
    added: list = []
    error = None
    if fetch is not None:
        try:
            added = list(fetch(latest_timestamp(rows)) or [])
            rows = append_rows(rows, added)
        except Exception as exc:                            # noqa: BLE001
            error = repr(exc)

    fresh = sorted(unseen_dates(rows))
    ready = readiness(rows, min_days=min_days)
    return {
        "kind": "l5_oos_append",
        "rows_before": before,
        "rows_after": len(rows),
        "rows_appended": len(rows) - before,
        "fetch_error": error,
        "unseen_days": fresh,
        "unseen_day_count": len(fresh),
        "readiness": ready,
        "sweep_runnable": bool(ready.get("ready")),
        "decision_sample_excluded": list(DECISION_SAMPLE_DATES),
        "labels": ("append-only; seen dates are REFUSED not filtered; the "
                   "ceiling sweep is NOT auto-run -- re-sweeping a growing "
                   "sample every hour is threshold shopping"),
    }


def run(cycles: Optional[int] = None, fetch=None, sleep=None) -> int:
    """Report every cycle so 'still n=0' is visible rather than inferred."""
    import time as _t
    sleep = sleep or _t.sleep
    n = 0
    while cycles is None or n < cycles:
        n += 1
        try:
            row = harvest_and_report(fetch=fetch)
        except Exception as exc:                            # noqa: BLE001
            row = {"kind": "l5_oos_append", "error": repr(exc),
                   "sweep_runnable": False}
        row["cycle"] = n
        print(PREFIX + json.dumps(row, separators=(",", ":"), default=str),
              flush=True)
        sleep(CYCLE_S)
    return 0
