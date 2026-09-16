"""R1 probe source lineage: what the image runs, and how it differs from 6adcc4c4.

THE ACCEPTANCE CONDITION, machine-checked rather than asserted: every file the
R1 image COPIes must be either byte-identical to the reviewed artifact
6adcc4c43e67, or on the explicit allow-list below with a reason. Anything else
is an UNDISCLOSED delta and fails.

WHY THIS EXISTS AS CODE. I originally disclosed ONE differing file. Diffing the
image's actual COPY set found FOUR. The other three were benign -- and I only
knew that because I looked. A lineage claim that is not recomputable is a
statement about what someone remembered, so this recomputes it.

SCOPE IS THE COPY SET, NOT THE BRANCH. The branch carries thousands of lines of
analysis and tests that never enter the image. What matters for a probe is what
the probe runs.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict, List

LABEL = "R1_SOURCE_LINEAGE"
REVIEWED = "6adcc4c4"
DOCKERFILE = "deploy/l5_r1_probe/Dockerfile"

# Each entry: why this file may differ, and the class it must fall in.
# EXECUTION / DECISION / SIZING / SUBMISSION are never acceptable classes here.
ALLOWED: Dict[str, Dict[str, str]] = {
    "src/pmus_spring_l5_session.py": {
        "class": "OBSERVABILITY",
        "why": ("one hunk inside a block commented 'never let observability "
                "trade': publishes tte_s as null instead of the 1e9 "
                "SPORTS_TTE_DEFAULT_S sentinel when a market has no settlement "
                "clock. The decision path still buckets on the sentinel; only "
                "the emitted observation changes."),
    },
    "src/l5_decision_observability.py": {
        "class": "OBSERVABILITY",
        "why": ("adds two reason constants and two emitted keys "
                "(tte_s_reason, book_implied_pair_cost_reason). Output only -- "
                "this module has no decision path."),
    },
    "src/pmus_incentive_audit.py": {
        "class": "ACCOUNTING_REPORTING",
        "why": ("rows_detail truncation cap raised 50 -> 2000 and any residual "
                "truncation is now REPORTED. A payout that cannot be attributed "
                "to its markets is the same failure as a fabricated zero. No "
                "fee or rebate arithmetic changes."),
    },
    "analysis/l5/venue_maintenance_windows.json": {
        "class": "DOCUMENTATION",
        "why": ("descriptive fields added to one incident record (resolution, "
                "orders_count_correction 10->14, withdrawn_evidence, "
                "observed_recovery_*, rejection_signature_recurred_after_recovery). "
                "The loader reads ONLY windows[].start_utc / end_utc / "
                "revisions[].end_utc_revised / revised_by / id / venue, and the "
                "diff touches none of them, so gating behaviour is unchanged."),
    },
}

FORBIDDEN_CLASSES = ("EXECUTION", "DECISION", "SIZING", "SUBMISSION")


def copy_sources(dockerfile: str = DOCKERFILE) -> List[str]:
    out = []
    for line in open(dockerfile):
        if line.startswith("COPY "):
            out.append(line.split()[1])
    return sorted(set(out))


def _blob(rev: str, path: str):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def verify(head: str = "HEAD", reviewed: str = REVIEWED) -> Dict[str, Any]:
    """Recompute the delta over the image's COPY set and classify every file."""
    differing, absent, identical = [], [], []
    for src in copy_sources():
        a, b = _blob(reviewed, src), _blob(head, src)
        if a is None:
            absent.append(src)
        elif a == b:
            identical.append(src)
        else:
            differing.append(src)
    undisclosed = [f for f in differing if f not in ALLOWED]
    bad_class = [f for f in differing
                 if f in ALLOWED and ALLOWED[f]["class"] in FORBIDDEN_CLASSES]
    return {
        "label": LABEL, "reviewed_artifact": reviewed,
        "copy_sources": len(identical) + len(differing) + len(absent),
        "identical": len(identical),
        "differing": differing,
        "absent_from_reviewed": absent,
        "undisclosed": undisclosed,
        "forbidden_class": bad_class,
        "deltas": {f: ALLOWED[f] for f in differing if f in ALLOWED},
        "ACCEPTANCE": (not undisclosed and not bad_class and not absent),
        "why": ("every differing file is on the allow-list with a non-execution "
                "class, and no COPYed file is missing from the reviewed artifact"
                if (not undisclosed and not bad_class and not absent)
                else "an undisclosed or execution-class delta is present"),
    }
