"""Venue-neutral shadow scoring (moved verbatim from gemini_l5_shadow_v2,
which re-exports these names): relation-frequency fidelity of a set of
decision rows against the frozen v2 conditional masses, the split of rows
by PROVEN lookup version, and the sampled -> acted funnel. Pure functions
over plain dict rows; nothing venue-specific, nothing that orders. The
PMUS recorder's read-only multi-market shadow scores its own rows with
exactly these functions."""
from __future__ import annotations

from typing import Any, Mapping

from src.l5_venue_adapter import LOOKUP_VERSION, SUPERSEDED_LOOKUP_VERSIONS


def split_rows_by_lookup(rows: list) -> dict:
    """OWNER 5558646019 #2/#3: decisions split by PROVEN lookup version.
    v2 = rows written with the canonical lookup (lookup_version 2);
    legacy = rows without a lookup record (UNVERIFIABLE_LEGACY, v1 keys);
    unknown = rows with a lookup record of another/no version. Each
    bucket is returned as row copies carrying only its decisions, so the
    existing scorers run unchanged on a provably homogeneous cohort."""
    def _sub(pred):
        out = []
        for r in rows:
            ds = [d for d in (r.get("decisions") or []) if pred(d)]
            if ds:
                out.append({**r, "decisions": ds})
        return out

    def ver(d):
        lk = d.get("lookup")
        if not isinstance(lk, Mapping):
            return None
        try:
            return int(lk.get("lookup_version") or 0)
        except (TypeError, ValueError):
            return 0
    v2 = _sub(lambda d: ver(d) == LOOKUP_VERSION)
    legacy = _sub(lambda d: ver(d) is None)
    unknown = _sub(lambda d: ver(d) not in (None, LOOKUP_VERSION))
    count = lambda rs: sum(len(r["decisions"]) for r in rs)      # noqa: E731
    superseded: dict = {}
    for r in unknown:
        for d in r["decisions"]:
            v = ver(d)
            superseded[str(v)] = superseded.get(str(v), 0) + 1
    return {"v2": v2, "legacy": legacy, "unknown": unknown,
            "provenance": {"lookup_version_gate": LOOKUP_VERSION,
                           "v2_decisions": count(v2), "legacy_decisions": count(legacy),
                           "unknown_decisions": count(unknown),
                           "superseded_versions": {v: {"decisions": n,
                                                       "label": SUPERSEDED_LOOKUP_VERSIONS.get(int(v), "unknown version")}
                                                   for v, n in superseded.items()},
                           "v2_windows": len({(r.get("symbol"), r.get("entry_t")) for r in v2}),
                           "legacy_label": "UNVERIFIABLE_LEGACY: v1 keys, no persisted "
                                           "lookup; diagnostic only, never a verdict input"}}


def action_scoring(rows: list) -> dict:
    """OWNER 5555481658 item 3: relation-frequency distance and calibration
    of an arm against the historical L5 conditional distribution, decision
    by decision, on the rows given (the caller restricts to untouched
    windows). Two views: SAMPLED/RECOMMENDED = the policy output before
    the safety shell (pure policy fidelity); ACTED = what the shell let
    through (executed fidelity; HOLD is not an L5 relation and is reported
    with its refusal reasons). Post-fill states are compared separately."""
    decs = [d for r in rows for d in (r.get("decisions") or [])
            if d.get("p_complete") is not None]
    if not decs:
        return {"scored_decisions": 0}

    def masses(d):
        pc, pe = float(d.get("p_complete") or 0), float(d.get("p_extend") or 0)
        po = max(0.0, 1.0 - pc - pe)
        return {"complete": pc, "extend": pe, "open": po}

    def freq(items, key):
        c: dict = {}
        for d in items:
            v = d.get(key)
            if v in ("complete", "extend", "open"):
                c[v] = c.get(v, 0) + 1
        n = sum(c.values()) or 1
        return {k: round(c.get(k, 0) / n, 4) for k in ("complete", "extend", "open")}, sum(c.values())

    def expected(items):
        n = len(items) or 1
        agg = {"complete": 0.0, "extend": 0.0, "open": 0.0}
        for d in items:
            for k, v in masses(d).items():
                agg[k] += v
        return {k: round(v / n, 4) for k, v in agg.items()}

    def l1(a, b):
        return round(sum(abs(a.get(k, 0) - b.get(k, 0)) for k in ("complete", "extend", "open")), 4)

    exp_all = expected(decs)
    samp, n_s = freq(decs, "sampled")
    acted, n_a = freq(decs, "acted")
    holds = [d for d in decs if d.get("acted") == "hold"]
    post = [d for d in decs if abs(float(d.get("residual_units") or 0)) >= 1]
    exp_post = expected(post) if post else None
    samp_post, _ = freq(post, "sampled") if post else ({}, 0)
    acted_post, _ = freq(post, "acted") if post else ({}, 0)
    # calibration: decisions binned by P(complete) vs observed sampled COMPLETE
    bins: dict = {}
    for d in decs:
        pc = float(d.get("p_complete") or 0)
        b = f"{int(pc * 10) / 10:.1f}"
        e = bins.setdefault(b, {"n": 0, "sampled_complete": 0, "acted_complete": 0})
        e["n"] += 1
        e["sampled_complete"] += int(d.get("sampled") == "complete")
        e["acted_complete"] += int(d.get("acted") == "complete")
    calib = {b: {"n": e["n"], "sampled_complete_rate": round(e["sampled_complete"] / e["n"], 3),
                 "acted_complete_rate": round(e["acted_complete"] / e["n"], 3)}
             for b, e in sorted(bins.items())}
    refusals: dict = {}
    for d in holds:
        k = str(d.get("refusal")).split(":")[0]
        refusals[k] = refusals.get(k, 0) + 1
    return {
        "scored_decisions": len(decs),
        "expected_relation_freq_l5": exp_all,
        "sampled_relation_freq": samp, "sampled_n": n_s,
        "l1_sampled_vs_l5": l1(samp, exp_all),
        "acted_relation_freq": acted, "acted_n": n_a,
        "l1_acted_vs_l5": l1(acted, exp_all),
        "hold_share_of_decisions": round(len(holds) / len(decs), 4),
        "hold_refusals": refusals,
        "post_fill": {
            "decisions": len(post),
            "expected_l5": exp_post, "sampled": samp_post, "acted": acted_post,
            "complete_freq_gap_pp_sampled": (round(100 * (samp_post.get("complete", 0) - exp_post["complete"]), 1)
                                             if post else None),
            "complete_freq_gap_pp_acted": (round(100 * (acted_post.get("complete", 0) - exp_post["complete"]), 1)
                                           if post else None),
        },
        "calibration_by_p_complete_bin": calib,
        "off_distribution_rate_pct": round(100.0 * sum(1 for d in decs if d.get("off_distribution")) / len(decs), 2),
        "labels": "distributional fidelity vs the frozen v2 conditional masses; THEORETICAL",
    }


def sampled_to_acted_funnel(rows: list) -> dict:
    """OWNER 5558167805 #3: the sampled -> attempted -> acted -> hold funnel
    by refusal reason, per sampled relation, so policy fidelity (the draw)
    is separated from execution-shell adaptation (what the shell let
    through)."""
    decs = [d for r in rows for d in (r.get("decisions") or [])
            if d.get("p_complete") is not None]
    per: dict = {}
    for d in decs:
        s = d.get("sampled") or d.get("v2_recommended") or "unknown"
        a = d.get("acted")
        e = per.setdefault(s, {"sampled_n": 0, "acted_as_sampled": 0,
                               "converted_to": {}, "held_by_reason": {}})
        e["sampled_n"] += 1
        if a == s:
            e["acted_as_sampled"] += 1
        elif a == "hold":
            k = str(d.get("refusal")).split(":")[0] if d.get("refusal") else "unnamed"
            e["held_by_reason"][k] = e["held_by_reason"].get(k, 0) + 1
        else:
            e["converted_to"][str(a)] = e["converted_to"].get(str(a), 0) + 1
    n = len(decs) or 1
    held = sum(sum(e["held_by_reason"].values()) for e in per.values())
    conv = sum(sum(e["converted_to"].values()) for e in per.values())
    same = sum(e["acted_as_sampled"] for e in per.values())
    reasons: dict = {}
    for e in per.values():
        for k, v in e["held_by_reason"].items():
            reasons[k] = reasons.get(k, 0) + v
    return {"scored_decisions": len(decs), "per_sampled_relation": per,
            "acted_as_sampled_share": round(same / n, 4),
            "held_share": round(held / n, 4), "converted_share": round(conv / n, 4),
            "held_by_reason": reasons,
            "reading": ("the draw is the policy; hold/conversion shares are the "
                        "execution shell's suppression of that draw, by reason")}
