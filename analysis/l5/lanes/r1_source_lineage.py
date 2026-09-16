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
#: OWNER AUTHORIZATION 2026-09-16 "A2". The baseline advanced from the R1
#: review to the CP1 candidate artifact, which contains ONLY the explicitly
#: authorized Option A changes: the CP1 rung, the image's rung and market
#: allowance, and the read-only halted_envelope disclosure.
#:
#: THIS IS NOT A WEAKENING. FORBIDDEN_CLASSES is unchanged and still contains
#: SIZING. Disclosure is unchanged. A future sizing change still fails this
#: gate and still needs its own review. What moved is only WHICH artifact the
#: COPY set is compared against.
#:
#: WHY IT HAD TO MOVE: src/pmus_l5_ramp_ladder.py is in the image COPY set and
#: the authorized change raises a per-market cap, so its honest class is
#: SIZING -- a forbidden one. The other routes were to mislabel it, or to drop
#: SIZING from FORBIDDEN_CLASSES and weaken the gate permanently for every
#: future change. The owner reviewed the artifact and advanced the baseline.
REVIEWED = "f3247262"

#: The superseded baseline, kept so the provenance is legible HERE and not only
#: in git history. Every delta it covered is preserved verbatim in PRIOR_DELTAS
#: below; those files are now PART of REVIEWED and no longer differ. The
#: mechanism's own self-tests run against this baseline, so advancing the
#: production one does not leave the gate untested.
PREVIOUS_REVIEWED = "6adcc4c4"
DOCKERFILE = "deploy/l5_r1_probe/Dockerfile"

# Each entry: why this file may differ, the class it must fall in, and the
# EXACT blob the review covered.
#
# THE `sha` FIELD IS WHAT STOPS THIS BECOMING A RUBBER STAMP (OWNER 2026-09-16).
# A filename allow-list says "this file may differ" and then accepts ANY future
# content in it -- one reviewed delta silently licenses every later one. Pinning
# the reviewed blob means the accepted executable COPY set is identical except
# for deltas someone actually looked at: edit an allow-listed file again and
# verify() fails as CONTENT_DRIFT until the pin is refreshed with a new reason.
#
# EXECUTION / DECISION / SIZING / SUBMISSION are never acceptable classes here.
#: PROVENANCE ONLY -- the deltas reviewed against PREVIOUS_REVIEWED. They are
#: now inside REVIEWED and no longer differ from it. Kept verbatim, with
#: their reviewed blob ids, so the record of what was accepted and why does
#: not vanish when the baseline moves.
PRIOR_DELTAS: Dict[str, Dict[str, str]] = {
    "src/pmus_l5_continuous_session.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "758ba95b16648594760e629e018e1ac281c58a92",
        "why": ("CHANGED 2026-09-16 after the R1 canary traded 11+ markets on "
                "a one-market authorization. Four deltas, all restricting: "
                "(a) arming now REQUIRES a valid unexpired mandate naming this "
                "service, and refuses with NO_MANDATE otherwise; (b) the "
                "transport is wrapped by the mandate guard, so every "
                "POST /v1/orders is admitted against the envelope at the wire "
                "rather than at a call site; (c) approved_continuation refuses "
                "any approval that does not POSTDATE the active mandate, which "
                "is what let three 2026-09-06 approvals continue a 2026-09-16 "
                "campaign, and an undated approval is refused; (d) the halt "
                "latch and the mandate's expiry are re-read on EVERY campaign "
                "pass, not once at boot, so an unattended process stops itself "
                "when its authorization runs out. No order verb, size, price, "
                "cap, band or admission threshold is loosened by any of it."),
    },
    "src/pmus_live_mandate.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "959de8e7c7f5cc5990e1dd7f142b91ee7008c438",
        "why": ("ADDED 2026-09-16 after the R1 canary spent $19.3295 against a "
                "$0.05 authorization across 11+ markets. The immutable "
                "per-authorization mandate and the transport guard that "
                "enforces it. It can only REFUSE: no order verb, no transport "
                "of its own, no sizing, no pricing -- it wraps a transport and "
                "raises MandateRefusal before POST /v1/orders. Enforcement "
                "moved to the wire precisely because the old rule was an "
                "allow-list of stage names that forgot spring_complete_*."),
    },
    "analysis/l5/authorization/": {
        "class": "DOCUMENTATION",
        "sha": "e5ddef9792e0f53425e7857f6e35496a5760623d",
        "why": ("ADDED 2026-09-16. The committed HALT latch and the (currently "
                "ABSENT) live_mandate.json. Data and prose, never code: HALT's "
                "presence halts, live_mandate.json's absence is the safe "
                "state. Shipped as a directory so granting or revoking an "
                "authorization is a reviewed commit rather than a Dockerfile "
                "edit. The R1 probe ships HALTED with no mandate."),
    },
    "src/pmus_spring_l5_session.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "621ffeeb80e81a744ad58f4e97efca008f3745a6",
        "why": ("TWO deltas, and the class names the more consequential one. "
                "(a) OBSERVABILITY: inside a block commented 'never let "
                "observability trade', publishes tte_s as null instead of the "
                "1e9 SPORTS_TTE_DEFAULT_S sentinel; the decision path still "
                "buckets on the sentinel. (b) RISK_BOUND_TIGHTENING (OWNER "
                "2026-09-16 P0): the complement-acceptance probe now counts "
                "against the one per-market notional envelope and is tested "
                "against it BEFORE submission (counts_against_market_cap, "
                "cap_admits), and its ledger rows changed to the canonical "
                "submitted/order_terminal/filled shapes so a partial fill "
                "reconciles and ledger_qty_total can see the position. "
                "This GATES a submission, which is why it is not filed as "
                "OBSERVABILITY -- but it can only ever REFUSE one. It computes "
                "no price, no size and no side, contains no order verb, and "
                "leaves held_inventory_from_ledger untouched so no L5 decision "
                "input moves. Pinned by tests/test_market_cap_envelope.py."),
    },
    "src/l5_decision_observability.py": {
        "sha": "2f977868027299fe532ff05c99a561604df99079",
        "class": "OBSERVABILITY",
        "why": ("adds two reason constants and two emitted keys "
                "(tte_s_reason, book_implied_pair_cost_reason). Output only -- "
                "this module has no decision path."),
    },
    "src/pmus_incentive_audit.py": {
        "sha": "74354635ef6f97c36748770412a567adeccaebe4",
        "class": "ACCOUNTING_REPORTING",
        "why": ("rows_detail truncation cap raised 50 -> 2000 and any residual "
                "truncation is now REPORTED. A payout that cannot be attributed "
                "to its markets is the same failure as a fabricated zero. No "
                "fee or rebate arithmetic changes."),
    },
    "analysis/l5/venue_maintenance_windows.json": {
        "sha": "f7204c60fe5d7890380c2cb43a81394686cf95d4",
        "class": "DOCUMENTATION",
        "why": ("descriptive fields added to one incident record (resolution, "
                "orders_count_correction 10->14, withdrawn_evidence, "
                "observed_recovery_*, rejection_signature_recurred_after_recovery). "
                "The loader reads ONLY windows[].start_utc / end_utc / "
                "revisions[].end_utc_revised / revised_by / id / venue, and the "
                "diff touches none of them, so gating behaviour is unchanged."),
    },
}

#: NOTHING in the image COPY set may differ from REVIEWED except what is
#: disclosed here. The authorization directory is the one legitimate mover:
#: granting or revoking an authorization IS a change to it, by design, and the
#: pin is re-taken each time so it cannot become a rubber stamp.
ALLOWED: Dict[str, Dict[str, str]] = {
    "analysis/l5/authorization/": {
        "class": "DOCUMENTATION",
        "sha": "4048d4d769b6ef5e1bf04ee79b969a3fe4e8b202",
        "why": ("The committed HALT latch, the live mandate when one exists, "
                "and the arming records. Data and prose, never code: HALT's "
                "presence halts and live_mandate.json's absence is the safe "
                "state. Shipped as a directory so granting or revoking an "
                "authorization is a reviewed commit rather than a Dockerfile "
                "edit."),
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


def _blob_sha(rev: str, path: str) -> str:
    """Git's own object id for the file at that revision. Cheap, stable, and
    it is the same identity git uses, so nothing here has to hash bytes."""
    r = subprocess.run(["git", "rev-parse", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def pin_shas(head: str = "HEAD") -> Dict[str, str]:
    """The blob ids to paste into ALLOWED[...]['sha'] after a review.

    Deliberately NOT called by verify(): a pin that refreshes itself is not a
    pin. Refreshing one is a human act, and the reason string is expected to
    change with it."""
    return {f: _blob_sha(head, f) for f in ALLOWED}


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
    # A FILE ADDED TO THE COPY SET IS A DELTA TOO. It is absent from the
    # reviewed artifact, so it cannot be compared -- which makes disclosure
    # MORE important, not less. An addition is accepted on exactly the same
    # terms as a change: named in ALLOWED, given a non-execution class, and
    # pinned to the blob that was reviewed. Anything else still fails.
    added = [f for f in absent if f in ALLOWED]
    absent = [f for f in absent if f not in ALLOWED]
    undisclosed = [f for f in differing if f not in ALLOWED]
    bad_class = [f for f in differing + added
                 if f in ALLOWED and ALLOWED[f]["class"] in FORBIDDEN_CLASSES]
    # CONTENT, not filename. An allow-listed file whose blob is not the one the
    # review covered is drift, and drift fails exactly like an undisclosed
    # delta -- otherwise one accepted change licenses every later change to
    # the same file, which is the rubber stamp this is here to prevent.
    drift = []
    for f in differing + added:
        pinned = str((ALLOWED.get(f) or {}).get("sha") or "")
        actual = _blob_sha(head, f)
        if not pinned or not actual or pinned != actual:
            drift.append({"file": f, "pinned": pinned or None,
                          "actual": actual or None,
                          "reason": ("no reviewed blob pinned" if not pinned
                                     else "content differs from the reviewed "
                                          "blob")})
    ok = not undisclosed and not bad_class and not absent and not drift
    return {
        "label": LABEL, "reviewed_artifact": reviewed, "head": head,
        "copy_sources": len(identical) + len(differing) + len(absent),
        "identical": len(identical),
        "differing": differing,
        "absent_from_reviewed": absent,
        "added_and_disclosed": added,
        "undisclosed": undisclosed,
        "forbidden_class": bad_class,
        "content_drift": drift,
        "deltas": {f: ALLOWED[f] for f in differing + added if f in ALLOWED},
        "ACCEPTANCE": ok,
        "why": ("every differing file is on the allow-list with a "
                "non-execution class AND its blob is the exact one reviewed; "
                "every COPYed file absent from the reviewed artifact is a "
                "disclosed, classified, pinned ADDITION" if ok
                else "an undisclosed, execution-class or drifted delta is "
                     "present"),
    }
