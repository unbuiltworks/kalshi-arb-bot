"""Which window captures may count toward the prospective sample, and which may not.

THE RULE THIS ENFORCES. The prospective sample is THREE COMPLETE WINDOWS, not
three attempts. A capture that was interrupted anywhere between its -120s lead
and the completion of its +300s follow is
`TRUNCATED_NOT_SCORABLE_AS_A_COMPLETE_WINDOW`: useful diagnostic evidence,
never one of the three.

WHY IT IS A SEPARATE MODULE. `src/l5_window_forensics.py` is FROZEN. It cannot
learn to judge its own completeness without becoming a different experiment, so
completeness is decided here, afterwards, from what the tape itself shows:

    lead      the first row must be at or before window_start - lead_s
    close     a `window_end` row must exist
    follow    the last row must be at or after window_end + follow_s

A tape that stops early fails `close` and `follow` no matter how much data it
holds. A tape written by a process that started INSIDE its own lead window
fails `lead` -- which is the case no interruption produces and nothing in the
tape would otherwise flag, because such a capture looks perfectly healthy from
its first row onward.

DECLARED INELIGIBILITY. Two windows are ineligible by owner sequencing rather
than by anything in their tape, and they are named here so the decision is
readable rather than implicit:

  1789668000  the 18:00Z window, killed with the session container at
              18:10:56Z. Preserved exactly as
              WINDOW_1_TRUNCATED_NOT_SCORABLE_AS_A_COMPLETE_WINDOW.
  1789669800  the 18:30Z window. The durable deployment could not be verified
              and started in time to give it complete preregistered coverage,
              so rather than compromise the experiment to save one window it
              is ineligible and the sample begins at the next complete one.

Both remain diagnostic evidence. Neither counts toward n=3, and neither may be
used to change the frozen predictions before the replacement n=3 completes.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

LABEL = "L5_WINDOW_FORENSICS_V1"
COMPLETE = "COMPLETE"
TRUNCATED = "TRUNCATED_NOT_SCORABLE_AS_A_COMPLETE_WINDOW"
N_REQUIRED = 3

#: window_start -> why it cannot count, whatever its tape shows
DECLARED_INELIGIBLE: Dict[int, str] = {
    1789668000: ("WINDOW_1_TRUNCATED_NOT_SCORABLE_AS_A_COMPLETE_WINDOW: "
                 "killed with the session container at 18:10:56Z, 11 minutes "
                 "into a 20-minute capture"),
    1789669800: ("INELIGIBLE_BY_OWNER_SEQUENCING: the durable deployment could "
                 "not be verified and started with complete preregistered "
                 "coverage for this window, and the experiment is not "
                 "compromised to save one window"),
}

#: how much clock slop to forgive at each edge. Small on purpose: this is for
#: sampler jitter, not for a missing minute.
TOL_S = 3.0

#: A capture counts toward the prospective sample only if the SUPERVISED
#: DURABLE stream produced it, which its path proves: the supervisor gives
#: every start its own `seg_<stamp>_<n>` directory on /state/l5-research, and
#: nothing else writes there.
#:
#: This is not bookkeeping fussiness. The same frozen module can be run by
#: hand in the session container -- it was, three times today, and the
#: container killed it -- and those tapes are indistinguishable from durable
#: ones by content alone. Counting one would put a window of the prospective
#: sample back inside the failure domain the migration exists to leave.
DURABLE_SEGMENT = "seg_"


def is_durable(path: str) -> bool:
    """Did the supervised durable stream write this tape?"""
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return parent.startswith(DURABLE_SEGMENT)


def _rows(path: str) -> List[dict]:
    out: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue                      # a torn tail is not a row
    return out


def _clock(r: dict) -> Optional[float]:
    for k in ("L5_OBSERVATION_CLOCK", "t"):
        v = r.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def classify(path: str) -> Dict[str, Any]:
    """One tape -> COMPLETE or TRUNCATED, with the reasons it failed."""
    rows = _rows(path)
    pre = next((r for r in rows if r.get("kind") == "preregistration"), None)
    ts = [t for t in (_clock(r) for r in rows) if t is not None]
    out: Dict[str, Any] = {
        "path": path, "rows": len(rows),
        "window_start": pre.get("window_start") if pre else None,
        "window_end": pre.get("window_end") if pre else None,
        "first_row_utc": min(ts) if ts else None,
        "last_row_utc": max(ts) if ts else None,
        "has_preregistration": pre is not None,
        "has_window_end_row": any(r.get("kind") == "window_end" for r in rows),
    }
    if pre is None:
        out.update(verdict=TRUNCATED, failed=["no preregistration row"])
        return out

    proc = pre.get("procedure") or {}
    lead = float(proc.get("lead_s") or 0.0)
    follow = float(proc.get("follow_s") or 0.0)
    start = float(pre["window_start"])
    end = float(pre["window_end"])
    out["lead_s"], out["follow_s"] = lead, follow

    failed: List[str] = []
    if out["first_row_utc"] is None or out["first_row_utc"] > start - lead + TOL_S:
        failed.append(
            "lead: capture began at %s, which is after window_start - %ds"
            % (out["first_row_utc"], int(lead)))
    if not out["has_window_end_row"]:
        failed.append("close: no window_end row -- the capture never reached "
                      "the end of its own window")
    if out["last_row_utc"] is None or out["last_row_utc"] < end + follow - TOL_S:
        failed.append(
            "follow: last row at %s, short of window_end + %ds by %.1fs"
            % (out["last_row_utc"], int(follow),
               (end + follow) - (out["last_row_utc"] or end)))

    out["durable"] = is_durable(path)
    if not out["durable"]:
        failed.insert(0, "origin: not written by the supervised durable "
                           "stream (no seg_* segment in its path), so it is "
                           "diagnostic evidence and cannot be one of the three")
    declared = DECLARED_INELIGIBLE.get(int(start))
    if declared:
        failed.insert(0, declared)
    out["verdict"] = TRUNCATED if failed else COMPLETE
    out["failed"] = failed
    out["declared_ineligible"] = bool(declared)
    return out


def tapes(*dirs: str) -> List[str]:
    """Every window tape under these roots, including inside per-start
    segments. A segment directory is not a window; the files inside it are."""
    seen: List[str] = []
    for d in dirs:
        for pat in ("window_*.jsonl", "seg_*/window_*.jsonl"):
            seen.extend(sorted(glob.glob(os.path.join(d, pat))))
    return sorted(set(seen))


def report(*dirs: str) -> Dict[str, Any]:
    """The prospective standing: how many COMPLETE windows exist, and what
    every other capture is."""
    rows = [classify(p) for p in tapes(*dirs)]
    complete = [r for r in rows if r["verdict"] == COMPLETE]
    diagnostic = [r for r in rows if not r.get("durable")]
    # a window can be captured twice (a restart that targets the same boundary
    # writes into its own segment). Count WINDOWS, not tapes.
    by_window = sorted({int(r["window_start"]) for r in complete
                        if r["window_start"] is not None})
    return {
        "label": LABEL,
        "n_required": N_REQUIRED,
        "complete_windows": by_window,
        "n_complete": len(by_window),
        "satisfied": len(by_window) >= N_REQUIRED,
        "diagnostic_captures": len(diagnostic),
        "captures": rows,
        "labels": [
            "n_complete counts distinct WINDOWS whose capture ran from "
            "window_start - lead_s through window_end + follow_s. Two tapes "
            "of one window are one window.",
            "A TRUNCATED capture is diagnostic evidence. It is never one of "
            "the three, and it may not be used to change the frozen "
            "predictions before the replacement sample completes.",
            "Nothing here splices two partial captures into a complete one. "
            "Each tape is judged whole or not at all.",
            "Only the supervised durable stream's tapes count. A hand-run "
            "capture in an ephemeral container can be structurally perfect "
            "and is still diagnostic -- otherwise a window of the sample "
            "would sit back inside the failure domain the migration left.",
        ],
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*", default=["data/l5_window_forensics"])
    a = ap.parse_args(argv)
    print(json.dumps(report(*(a.dirs or ["data/l5_window_forensics"])),
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
