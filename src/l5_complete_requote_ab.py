"""A bounded live A/B for COMPLETE re-quoting that cannot contaminate R5F.

OWNER: "If the offline evidence is strong, design an isolated bounded live
A/B that does not contaminate L5_COMPLETION_EXTENSION_GATE_v1."

The offline bar returned OFFLINE_EVIDENCE_STRONG, which authorises DESIGNING
this. It does not authorise running it. Arming is an owner decision and this
module never makes it.

THE CONTAMINATION PROBLEM, STATED EXACTLY. L5_COMPLETION_EXTENSION_GATE_v1 is
frozen and accumulating forward evidence on the completion surface: pairing
rate, complete fill and rejection shares, per session. COMPLETE re-quoting
changes those same numbers. Run both at once with no separation and the
extension's verdict stops being about the extension.

Three ways out, and why this is the one:

  SEQUENTIAL -- wait for the extension's verdict, then start. Perfectly
    clean, and costs 48 forward hours the owner asked not to spend.
  SEPARATE SERVICE -- forbidden; a duplicate Railway service is a standing
    prohibition on this project.
  LABELLED ARMS IN ONE LANE -- what this designs. Every session records the
    arm it ran under, and the extension evaluator counts CONTROL-arm sessions
    only. The extension's criteria, baselines and sample minimums are
    untouched; it simply accrues evidence at roughly half rate.

That last cost is real and is stated rather than hidden: at the observed
~3.8 residual-opening sessions per forward hour, halving the control arm
moves the extension's remaining 20 firings from about 5 forward hours to
about 10. The owner is trading that for an immediate read on re-quoting.

WHY IT IS SAFE TO RUN BOTH ON ONE ACCOUNT. Re-quoting does not change any
money limit. It changes when a resting order is cancelled and re-posted at
the price the model already returned. Exposure per action, per market, the
daily stop, concurrency, maker-only and post-only are all untouched, and the
crossing guard already refuses a re-quote that would cross.

WHAT THE OFFLINE EVIDENCE SAYS THIS SHOULD SHOW, so it can be wrong in
public: about 15 additional completions across 15 markets, removing ~54
residual units, at a locked pair P&L of -$1.71 -- that is, the pairs it makes
LOSE about 3.2 cents a unit and are worth making only because stranding has
measured -33 cents. The A/B's job is to test that comparison on venue
numbers, not to confirm the fill count.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

DESIGN = "L5_COMPLETE_REQUOTE_AB_v1"
ARM_CONTROL = "control_no_complete_requote"
ARM_TREAT = "treat_complete_requote"
ARM_FIELD = "requote_arm"          # written on every session_end row

# ------------------------------------------------------------- assignment
ASSIGNMENT = {
    "unit": "market slug",
    "rule": ("deterministic hash of the slug, so a market's arm never "
             "changes between sessions or across a restart -- a market that "
             "flips arms mid-residual would be neither arm"),
    "split": 0.5,
    "why_not_random_per_session": (
        "a residual can span sessions after a restart. Randomising per "
        "session would let the same held position be worked under both arms "
        "and belong to neither."),
    "why_not_alternate_by_time": (
        "sports liquidity has a time-of-day shape; alternating by clock "
        "would confound the arm with the slate."),
}


def arm_for(slug: str, *, salt: str = DESIGN) -> str:
    """Stable arm for a market. Same slug, same arm, forever."""
    h = hashlib.sha256(f"{salt}|{slug}".encode()).hexdigest()
    return ARM_TREAT if (int(h[:8], 16) % 2) else ARM_CONTROL


# ------------------------------------------------------------------ bounds
BOUNDS_UNCHANGED = (
    "~$2.50 per action", "$7.50 per market", "concurrency 1",
    "maker-only", "post-only", "the daily loss stop", "the entry band",
    "the frozen model sha", "the frozen lookup v3",
    "COMPLETE_REJECT_STREAK_MAX = 3", "the extended action budget",
)

NEW_BOUNDS_REQUIRED = {
    # Each re-quote is one more order on the #93 surface, and #93's
    # unexplained non-crossing rejections concentrate on COMPLETE. The
    # offline replay charged 72 extra orders and ~13.4 expected rejections.
    "max_complete_requotes_per_session": 4,
    "requotes_charge_the_action_budget": True,
    "rejection_breaker_covers_requotes": True,
    "why": (
        "a rejected complete at a non-crossing price revokes the complement "
        "proof, which is the deadlock that took the lane down at 13:09 on "
        "2026-09-08. Re-quoting must not buy fills by spending proofs."),
}

# ----------------------------------------------------- pre-registered read
PRIMARY_ENDPOINT = {
    "name": "realized dollars per capital-dollar-hour, treat minus control",
    "why_not_completion_rate": (
        "the offline replay says two thirds of these completions are ABOVE "
        "PAR. A completion-rate endpoint would score locked losses as wins, "
        "which is the exact failure this whole analysis exists to avoid."),
    "secondary": [
        "residual units carried to settlement per settled market",
        "pair cost distribution, and the share above par",
        "complete rejections per session, treated arm only",
        "complement-proof revocations per 100 completes",
    ],
}

SAMPLE_MINIMUMS = {
    "settled_venue_confirmed_markets_per_arm": 12,
    "sessions_that_opened_a_residual_per_arm": 30,
    "sessions_where_a_requote_fired": 20,
}

STOP_EARLY = {
    "complement_proof_revocations_in_treat_exceed_control_by": 3,
    "treat_arm_daily_loss_stop_hit": True,
    "any_bound_violation": True,
    "why": ("these are safety stops, not efficacy reads. Hitting one halts "
            "the treated arm and leaves control running."),
}

NON_CONTAMINATION = {
    "mechanism": (
        f"every session_end row carries {ARM_FIELD}. The extension evaluator "
        "counts CONTROL-arm sessions only, so "
        "L5_COMPLETION_EXTENSION_GATE_v1's baselines, criteria and sample "
        "minimums are untouched."),
    "cost": (
        "the extension accrues evidence at roughly half rate: its remaining "
        "~20 firings move from about 5 forward hours to about 10."),
    "what_would_break_it": (
        "an unlabelled session. A session_end row without an arm field must "
        "be counted in NEITHER arm and must be reported, never defaulted to "
        "control -- defaulting would silently feed treated sessions into the "
        "extension's evidence."),
    "the_gate_is_not_edited": (
        "L5_COMPLETION_EXTENSION_GATE_v1 keeps its frozen sha. This design "
        "changes what the evaluator COUNTS, never what the gate REQUIRES."),
}

AUTHORISATION = {
    "offline_bar": "L5_COMPLETE_REQUOTE_OFFLINE_BAR_v1",
    "offline_verdict_required": "OFFLINE_EVIDENCE_STRONG",
    "this_module_authorises": "nothing",
    "arming_requires": (
        "an explicit owner go, naming the arm split and the stop conditions. "
        "PMUS_SPRING_COMPLETE_REQUOTE defaults OFF and no code here sets it."),
}


def describe() -> Mapping[str, Any]:
    return {"design": DESIGN, "arms": [ARM_CONTROL, ARM_TREAT],
            "arm_field": ARM_FIELD, "assignment": ASSIGNMENT,
            "bounds_unchanged": list(BOUNDS_UNCHANGED),
            "new_bounds_required": NEW_BOUNDS_REQUIRED,
            "primary_endpoint": PRIMARY_ENDPOINT,
            "sample_minimums": SAMPLE_MINIMUMS, "stop_early": STOP_EARLY,
            "non_contamination": NON_CONTAMINATION,
            "authorisation": AUTHORISATION}


def balance_check(slugs) -> dict:
    """How evenly the deterministic split lands on a real market list."""
    arms = [arm_for(s) for s in slugs]
    t = sum(1 for a in arms if a == ARM_TREAT)
    n = len(arms)
    return {"markets": n, "treat": t, "control": n - t,
            "treat_share": round(t / n, 4) if n else None}


def render_markdown(d: Optional[Mapping[str, Any]] = None) -> str:
    d = d or describe()
    n, a = d["non_contamination"], d["assignment"]
    L = ["# " + d["design"] + " — bounded live A/B design", "",
         "**Authorises nothing.** " + d["authorisation"]["arming_requires"],
         "", "## Arms", "",
         f"- `{ARM_CONTROL}` / `{ARM_TREAT}`, assigned by {a['unit']}",
         f"- {a['rule']}",
         f"- not per session: {a['why_not_random_per_session']}",
         f"- not by clock: {a['why_not_alternate_by_time']}", "",
         "## Non-contamination of the frozen gate", "",
         n["mechanism"], "", f"**Cost.** {n['cost']}", "",
         f"**What would break it.** {n['what_would_break_it']}", "",
         n["the_gate_is_not_edited"], "",
         "## Primary endpoint", "",
         f"**{d['primary_endpoint']['name']}**", "",
         "> " + d["primary_endpoint"]["why_not_completion_rate"], "",
         "Secondary:", ""]
    L += [f"- {s}" for s in d["primary_endpoint"]["secondary"]]
    L += ["", "## Bounds", "", "Unchanged:", ""]
    L += [f"- {b}" for b in d["bounds_unchanged"]]
    L += ["", "New bounds this change requires:", ""]
    for k, v in d["new_bounds_required"].items():
        if k != "why":
            L.append(f"- `{k}` = {v}")
    L += ["", "> " + d["new_bounds_required"]["why"], "",
          "## Sample minimums", ""]
    for k, v in d["sample_minimums"].items():
        L.append(f"- {k}: **{v}**")
    L += ["", "## Early stops (safety, not efficacy)", ""]
    for k, v in d["stop_early"].items():
        if k != "why":
            L.append(f"- {k}: {v}")
    L += ["", "> " + d["stop_early"]["why"], ""]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    import argparse
    import os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    d = describe()
    md = render_markdown(d)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "COMPLETE_REQUOTE_AB_DESIGN.md"),
                  "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "complete_requote_ab_design.json"),
                  "w") as f:
            json.dump(d, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
