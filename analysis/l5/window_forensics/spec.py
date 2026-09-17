"""Turn the prospective report into L5_PASSIVE_SURFACE_SPEC_V1 -- only what it supports.

THE TEMPTATION THIS RESISTS. A spec with two fields filled looks unfinished,
and the pull is to complete it from judgement. But a field filled from
judgement and a field filled from measurement are indistinguishable once
written down side by side, and the first then gets cited as what L5 does. So
the rule for every field is declared HERE, as data, above the code that
applies it -- and a field whose rule is not met stays UNRESOLVED with the
statistic that failed it recorded.

`withdrawal_policy` can never be populated by this study at all: cancellations
and unfilled orders appear in no feed we have, so a sample of FILLS cannot
establish it however large it gets. It is marked permanently unobservable
rather than left looking merely pending.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from src.l5_surface import params as PR

LABEL = "L5_PASSIVE_SURFACE_SPEC_V1"

#: the two frozen sets this module produces.
#:
#: FIDELITY_OBSERVABLE_V1 is every field observable L5 evidence supports, with
#: the unobservable ones explicitly unresolved. It is not a failed spec: it is
#: the exact boundary of what public data can say about him, and that boundary
#: is a result.
#:
#: L5_ADAPTED_V1 is that set plus OUR deployment policies for the load-bearing
#: fields nothing can observe. Those policies exist to make the observable
#: mechanism EXECUTABLE so it can be tested prospectively -- never to claim L5
#: uses them.
FIDELITY_LABEL = "FIDELITY_OBSERVABLE_V1"
ADAPTED_LABEL = "L5_ADAPTED_V1"

# --------------------------------------------------------------------------
# WHAT COUNTS AS ESTABLISHED. Declared before the code that evaluates it.
# Each entry: the statistic, the threshold, and the minimum sample beneath
# which the question is not asked at all.
# --------------------------------------------------------------------------
RULES: Dict[str, Dict[str, Any]] = {
    "base_parent_qty": {
        "statistic": "qty.exact_100_share over pooled parents",
        "establishes_if": ">= 0.50",
        "min_n": 30,
        "else": "UNRESOLVED -- a mode is not a rule",
    },
    "secondary_rung_qty": {
        "statistic": "median qty of parents that are NOT the base lot",
        "establishes_if": "at least 10 non-base parents exist",
        "min_n": 10,
        "else": "UNRESOLVED",
    },
    "tick_offsets": {
        "statistic": ("modal ticks_from_touch at the -1s mark, over parents "
                      "with compatible liquidity present"),
        "establishes_if": ">= 0.60 of them share a contiguous offset set",
        "min_n": 30,
        "else": "UNRESOLVED",
    },
    "rung_count": {
        "statistic": "size of the established tick_offsets set",
        "establishes_if": "tick_offsets is established",
        "min_n": 0,
        "else": "UNRESOLVED",
    },
    "both_sides_policy": {
        "statistic": "both_sides.both_outcomes_share",
        "establishes_if": ">= 0.70 (both) or <= 0.30 (one at a time)",
        "min_n": 200,
        "else": "UNRESOLVED",
    },
    "inventory_size_skew": {
        "statistic": "qty_vs_inventory.corr_abs_residual_vs_qty",
        "establishes_if": "|corr| < 0.10, which establishes NO skew",
        "min_n": 30,
        "else": "UNRESOLVED",
    },
    "inventory_price_skew": {
        "statistic": "not measured by this study",
        "establishes_if": "never",
        "min_n": 0,
        "else": "UNRESOLVED -- the study scores levels, not level shifts",
    },
    "refresh_policy": {
        "statistic": "reversals.share_already_present",
        "establishes_if": (">= 0.70 gives STANDING; <= 0.30 gives "
                           "REFRESHED_AFTER_FILL"),
        "min_n": 8,
        "else": "UNRESOLVED",
    },
    "replenishment_policy": {
        "statistic": "replenishment at +5s present_share",
        "establishes_if": ">= 0.70 gives REPLACED; <= 0.30 gives NOT_REPLACED",
        "min_n": 30,
        "else": "UNRESOLVED",
    },
    "TTE_policy": {
        "statistic": "qty_vs_tte.corr_tte_vs_qty and bucket coverage",
        "establishes_if": "all four TTE buckets carry parents",
        "min_n": 30,
        "else": "UNRESOLVED",
    },
    "withdrawal_policy": {
        "statistic": "none",
        "establishes_if": "NEVER -- permanently unobservable from public data",
        "min_n": 0,
        "else": "UNRESOLVED_UNOBSERVABLE",
    },
    "minimum_rest_time": {
        "statistic": "ours",
        "establishes_if": "never from this study; it is a risk choice",
        "min_n": 0,
        "else": "UNRESOLVED in FIDELITY; OURS_ADAPTED in the adapted set",
    },
    "max_residual": {
        "statistic": "ours",
        "establishes_if": "never from this study; it is a risk bound",
        "min_n": 0,
        "else": "UNRESOLVED in FIDELITY; OURS_ADAPTED in the adapted set",
    },
}


def _u(name: str, why: str) -> PR.Param:
    return PR.Param(name, PR.UNRESOLVED, prior_evidence=why,
                    pending_source="L5_PASSIVE_SURFACE_PROSPECTIVE_V1")


def _obs(name: str, value: Any, evidence: str) -> PR.Param:
    return PR.Param(name, PR.OBSERVED_PROSPECTIVE, value=value,
                    evidence=evidence)


def _offsets(rep: Dict[str, Any]) -> Tuple[Optional[List[int]], str]:
    """Modal ticks-from-touch among parents whose level was already resting."""
    seen: Dict[int, int] = {}
    n = 0
    for w in rep.get("windows") or []:
        for r in w.get("scored_parents") or []:
            m = (r.get("marks") or {}).get("-1") or {}
            if not m.get("readable") or not m.get("present"):
                continue
            t = m.get("ticks_from_touch")
            if t is None:
                continue
            k = int(round(float(t)))
            seen[k] = seen.get(k, 0) + 1
            n += 1
    if n < RULES["tick_offsets"]["min_n"]:
        return None, "only %d parents had a readable, present -1s mark" % n
    ordered = sorted(seen.items(), key=lambda kv: -kv[1])
    chosen: List[int] = []
    got = 0
    for k, c in ordered:
        chosen.append(k)
        got += c
        if got / n >= 0.60:
            break
    if got / n < 0.60:
        return None, "no contiguous offset set reached 0.60 (n=%d)" % n
    chosen = sorted(chosen)
    if chosen != list(range(chosen[0], chosen[0] + len(chosen))):
        return None, ("the modal offsets %s are not contiguous, so they are "
                      "not a ladder" % chosen)
    return chosen, "%d of %d parents (%.2f) rested at offsets %s" % (
        got, n, got / n, chosen)


def build(rep: Dict[str, Any], *, kind: str = "FIDELITY") -> Dict[str, Any]:
    """Populate only what the report establishes. Everything else stays open."""
    mech = (rep.get("mechanism") or {}).get("verdict")
    integ = rep.get("integrity") or {}
    if not integ.get("sample_complete"):
        return {"built": False,
                "why": ("the prospective sample is incomplete (%s of %s), so "
                        "no field may be populated from it"
                        % (integ.get("eligible_n"), integ.get("n_required"))),
                "mechanism": mech}

    pooled = rep.get("pooled") or {}
    fields: Dict[str, PR.Param] = {}
    why: Dict[str, str] = {}

    qty = {}
    repl = {}
    qvi = {}
    qtte = {}
    n_par = 0
    for w in rep.get("windows") or []:
        n_par += (w.get("parents") or {}).get("n", 0)
        for src, dst in (("qty", qty), ("replenishment", repl),
                         ("qty_vs_inventory", qvi), ("qty_vs_tte", qtte)):
            for k, v in (w.get(src) or {}).items():
                if isinstance(v, (int, float)):
                    dst[k] = dst.get(k, 0) + v

    # base_parent_qty -- a mode is not a rule
    ex = sum((w.get("qty") or {}).get("classes", {}).get("EXACT_100", 0)
             for w in rep.get("windows") or [])
    share = (ex / n_par) if n_par else None
    if n_par >= RULES["base_parent_qty"]["min_n"] and share is not None \
            and share >= 0.50:
        fields["base_parent_qty"] = _obs(
            "base_parent_qty", "100",
            "%d of %d pooled parents (%.2f) were exactly 100.00"
            % (ex, n_par, share))
    else:
        why["base_parent_qty"] = (
            "exact-100 share %s over n=%d; the rule needs >= 0.50 and n >= %d"
            % (None if share is None else round(share, 4), n_par,
               RULES["base_parent_qty"]["min_n"]))
        fields["base_parent_qty"] = _u("base_parent_qty", why["base_parent_qty"])

    # tick_offsets and rung_count
    offs, off_why = _offsets(rep)
    if offs:
        fields["tick_offsets"] = _obs("tick_offsets", offs, off_why)
        fields["rung_count"] = _obs("rung_count", len(offs),
                                    "the established offset set has %d rungs"
                                    % len(offs))
    else:
        fields["tick_offsets"] = _u("tick_offsets", off_why)
        fields["rung_count"] = _u("rung_count",
                                  "tick_offsets is unresolved: " + off_why)

    # both_sides_policy
    bs = (pooled.get("both_sides") or {})
    share_b, n_b = bs.get("both_outcomes_share"), bs.get("readable_snapshots", 0)
    if n_b >= RULES["both_sides_policy"]["min_n"] and share_b is not None and (
            share_b >= 0.70 or share_b <= 0.30):
        fields["both_sides_policy"] = _obs(
            "both_sides_policy",
            {"outcomes": ["Up", "Down"]} if share_b >= 0.70
            else {"outcomes": "ONE_AT_A_TIME"},
            "both-outcome share %.4f over %d readable snapshots"
            % (share_b, n_b))
    else:
        why["both_sides_policy"] = (
            "both-outcome share %s over %d snapshots; the rule needs >= 0.70 "
            "or <= 0.30 with n >= %d"
            % (share_b, n_b, RULES["both_sides_policy"]["min_n"]))
        fields["both_sides_policy"] = _u("both_sides_policy",
                                         why["both_sides_policy"])

    # inventory_size_skew -- a near-zero correlation establishes NO skew
    corrs = [(w.get("qty_vs_inventory") or {}).get("corr_abs_residual_vs_qty")
             for w in rep.get("windows") or []]
    corrs = [c for c in corrs if isinstance(c, (int, float))]
    if n_par >= RULES["inventory_size_skew"]["min_n"] and corrs and all(
            abs(c) < 0.10 for c in corrs):
        fields["inventory_size_skew"] = _obs(
            "inventory_size_skew",
            {"widening_multiplier": 1, "reducing_multiplier": 1},
            "|corr(|residual|, qty)| < 0.10 in every window (%s) over n=%d: "
            "size did not track inventory" % (corrs, n_par))
    else:
        why["inventory_size_skew"] = (
            "per-window corr(|residual|, qty) = %s over n=%d" % (corrs, n_par))
        fields["inventory_size_skew"] = _u("inventory_size_skew",
                                           why["inventory_size_skew"])

    # refresh_policy from the causal reversal test
    rv = pooled.get("reversals") or {}
    already, resolved = rv.get("share_already_present"), rv.get("resolved", 0)
    if resolved >= RULES["refresh_policy"]["min_n"] and already is not None \
            and (already >= 0.70 or already <= 0.30):
        fields["refresh_policy"] = _obs(
            "refresh_policy",
            {"mode": "STANDING" if already >= 0.70 else "REFRESHED_AFTER_FILL"},
            "opposite-already-present share %.4f over %d resolved reversals"
            % (already, resolved))
    else:
        why["refresh_policy"] = (
            "already-present share %s over %d resolved reversals; the rule "
            "needs >= 0.70 or <= 0.30 with n >= %d"
            % (already, resolved, RULES["refresh_policy"]["min_n"]))
        fields["refresh_policy"] = _u("refresh_policy", why["refresh_policy"])

    # replenishment_policy
    r5 = [(w.get("replenishment") or {}).get("at_+5s") or {}
          for w in rep.get("windows") or []]
    read = sum(x.get("readable", 0) for x in r5)
    pres = sum(x.get("present", 0) for x in r5)
    sh = (pres / read) if read else None
    if read >= RULES["replenishment_policy"]["min_n"] and sh is not None and (
            sh >= 0.70 or sh <= 0.30):
        fields["replenishment_policy"] = _obs(
            "replenishment_policy",
            {"mode": "REPLACED" if sh >= 0.70 else "NOT_REPLACED"},
            "level present again at +5s in %d of %d readable cases (%.4f)"
            % (pres, read, sh))
    else:
        why["replenishment_policy"] = (
            "+5s present share %s over %d readable; the rule needs >= 0.70 or "
            "<= 0.30 with n >= %d"
            % (sh, read, RULES["replenishment_policy"]["min_n"]))
        fields["replenishment_policy"] = _u("replenishment_policy",
                                            why["replenishment_policy"])

    # TTE_policy -- only the observed span, never extrapolated
    buckets: Dict[str, int] = {}
    for w in rep.get("windows") or []:
        for k, v in ((w.get("qty_vs_tte") or {}).get("by_bucket") or {}).items():
            buckets[k] = buckets.get(k, 0) + v.get("n", 0)
    if n_par >= RULES["TTE_policy"]["min_n"] and len(buckets) >= 4 and all(
            v > 0 for v in buckets.values()):
        fields["TTE_policy"] = _obs(
            "TTE_policy", {"quote_between_tte_s": [0, 900]},
            "parents observed in every TTE bucket: %s" % buckets)
    else:
        why["TTE_policy"] = ("parents by TTE bucket %s over n=%d; the rule "
                             "needs all four populated" % (buckets, n_par))
        fields["TTE_policy"] = _u("TTE_policy", why["TTE_policy"])

    # secondary_rung_qty
    non_base = n_par - ex
    if non_base >= RULES["secondary_rung_qty"]["min_n"]:
        qs = sorted(r["qty"] for w in rep.get("windows") or []
                    for r in (w.get("scored_parents") or [])
                    if abs(r.get("qty", 0) - 100.0) > 1e-6)
        med = qs[len(qs) // 2] if qs else None
        fields["secondary_rung_qty"] = _obs(
            "secondary_rung_qty", str(med),
            "median of %d non-100 parents" % len(qs))
    else:
        why["secondary_rung_qty"] = ("only %d non-base parents; the rule needs "
                                     "%d" % (non_base,
                                             RULES["secondary_rung_qty"]["min_n"]))
        fields["secondary_rung_qty"] = _u("secondary_rung_qty",
                                          why["secondary_rung_qty"])

    fields["inventory_price_skew"] = _u(
        "inventory_price_skew",
        "this study scores whether a LEVEL was resting, not whether the level "
        "SHIFTED with inventory; it cannot establish a price skew")
    fields["withdrawal_policy"] = _u(
        "withdrawal_policy",
        "PERMANENTLY UNOBSERVABLE from public data: cancellations and unfilled "
        "orders appear in no feed we have, so no sample of fills establishes "
        "it, however large")

    if kind == "ADAPTED":
        # the two the study can never reach. FIDELITY leaves them open and is
        # therefore unrunnable, which is the honest answer for a
        # reconstruction of L5; ADAPTED supplies OUR choices and says so.
        for f in ("inventory_price_skew", "withdrawal_policy"):
            fields[f] = PR.ADAPTED_V0.params[f]
    if kind == "FIDELITY":
        fields["minimum_rest_time"] = _u(
            "minimum_rest_time", "a risk choice, not a finding; see ADAPTED")
        fields["max_residual"] = _u(
            "max_residual", "a risk bound, not a finding; see ADAPTED")
    else:
        fields["minimum_rest_time"] = PR.ADAPTED_V0.params["minimum_rest_time"]
        fields["max_residual"] = PR.ADAPTED_V0.params["max_residual"]

    sp = PR.SurfaceParams(
        label=(FIDELITY_LABEL if kind == "FIDELITY" else ADAPTED_LABEL),
        kind=kind, params=fields,
        notes=("populated from L5_PASSIVE_SURFACE_PROSPECTIVE_V1 by the rules "
               "frozen in analysis/l5/window_forensics/spec.py"))
    return {"built": True, "spec": sp, "report": sp.report(),
            "mechanism": mech, "rules": RULES, "unresolved_because": why,
            "pooled_parents": n_par,
            "labels": [
                "Only fields whose frozen rule was met are populated. A field "
                "that failed its rule records the statistic that failed it.",
                "withdrawal_policy can never be populated by a study of fills.",
            ]}
