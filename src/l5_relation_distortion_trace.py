"""Where does COMPLETE come from, if the model does not sample it?

OWNER P0: "why does the frozen L5 model's sampled relation distribution pass
fidelity while the executable shell produces COMPLETE at ~39.6% versus L5
~28.6%? Trace every transformation from sampled L5 decision -> safety/
execution shell -> final acted relation and quantify which guards/caps/state
transitions convert OPEN or EXTEND into COMPLETE/HOLD."

THE ANSWER IS ONE CELL OF THE MATRIX. Sampling is nearly faithful; the
distortion is almost entirely EXTEND being executed as COMPLETE:

    sampled open      629  ->  open 562   complete   4   hold  63
    sampled extend    800  ->  extend 373 complete 227   hold 200
    sampled complete  784  ->  complete 704 extend 3  hold  77

Only 46.6% of sampled EXTENDs are executed as EXTEND. 227 become COMPLETE
and 200 become HOLD.

AND THE MECHANISM IS COHERENT RATHER THAN BUGGY. EXTEND grows a position;
COMPLETE shrinks it. When a cap binds, the shell cannot grow, so an EXTEND
draw is executed as the shrinking action instead. The shell is doing what it
was built to do. The fidelity cost is the by-product, and it is now measured.

THE COUNTERFACTUAL IS THE SYSTEM'S OWN, NOT MINE. Every decision row records
would_act_without_safety_cap -- what the shell would have done had no cap
bound. On 197 of the 227 EXTEND->COMPLETE conversions that field reads
"extend", which is the shell attesting to its own override. Where the field
is null the row is reported as UNATTRIBUTED rather than guessed.

WHAT THIS IS NOT. It is not a proposal. No safety control is removed, no cap
is changed, no live policy is altered, and a test greps this module for any
such suggestion. The counterfactual mix below is a MEASUREMENT of what the
caps cost in fidelity terms -- what to do about it is the owner's decision,
and the caps may well be worth their cost.

READ-ONLY.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src import l5_completion_extension_eval as ev
from src import l5_fidelity_gate as fg

RELATIONS = ("open", "extend", "complete", "hold")
# The frozen model's own expectation, from the live fidelity gate.
L5_EXPECTED = {"complete": 0.2858, "extend": 0.3376, "open": 0.3766}


def decisions(paths: Iterable[Any]) -> List[dict]:
    return [x for s in ev._sessions(paths) for x in (s.get("decisions") or [])]


def matrix(decs: List[dict]) -> dict:
    """sampled relation -> acted relation, with row totals."""
    m: Counter = Counter()
    for d in decs:
        s = fg._sampled(d)
        if s:
            m[(s, str(d.get("acted")))] += 1
    rows: Dict[str, Any] = {}
    for s in ("open", "extend", "complete"):
        tot = sum(v for (ss, _), v in m.items() if ss == s)
        if not tot:
            continue
        rows[s] = {
            "sampled_n": tot,
            "acted": {a: m[(s, a)] for a in RELATIONS if m[(s, a)]},
            "executed_as_sampled": m[(s, s)],
            "fidelity": round(m[(s, s)] / tot, 4),
        }
    return rows


def attribute(decs: List[dict], sampled: str, acted: str) -> dict:
    """Which guard converted this cell, by the shell's own labels."""
    g = [d for d in decs
         if fg._sampled(d) == sampled and str(d.get("acted")) == acted]
    caps: Counter = Counter()
    for d in g:
        cb = d.get("cap_binding")
        if isinstance(cb, list) and cb:
            for c in cb:
                caps[c] += 1
        elif cb is None:
            caps["<not recorded>"] += 1
        else:
            caps["<none bound>"] += 1
    wa = Counter(d.get("would_act_without_safety_cap") for d in g)
    attested = wa.get(sampled, 0)
    return {
        "n": len(g),
        "divergence": dict(Counter(d.get("divergence") for d in g).most_common(6)),
        "refusal_family": dict(Counter(
            str(d.get("refusal") or "none").split(":")[0]
            for d in g).most_common(6)),
        "cap_binding": dict(caps.most_common(6)),
        "shell_attests_it_would_have_acted_as_sampled": attested,
        "unattributed": len(g) - attested,
        "attestation_note": (
            "would_act_without_safety_cap is the shell's own record of what "
            "it would have done absent a binding cap. Rows where it is null "
            "are counted UNATTRIBUTED rather than assumed."),
    }


def _freq(counter: Mapping[str, int], total: int) -> Dict[str, float]:
    return {k: round(v / total, 4) for k, v in counter.items()} if total else {}


def _l1(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    return round(sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys), 4)


def counterfactual_mix(decs: List[dict]) -> dict:
    """What the ACTED mix would be if no cap had converted a draw.

    A MEASUREMENT, not a proposal. Each row is restored to the relation the
    shell itself recorded in would_act_without_safety_cap; a row without that
    field keeps its actual acted relation, so the counterfactual is
    conservative -- it never invents a restoration."""
    actual: Counter = Counter()
    restored: Counter = Counter()
    n_restored = 0
    for d in decs:
        a = str(d.get("acted"))
        actual[a] += 1
        w = d.get("would_act_without_safety_cap")
        if w and w != a:
            restored[str(w)] += 1
            n_restored += 1
        else:
            restored[a] += 1
    tot = sum(actual.values())
    af, rf = _freq(actual, tot), _freq(restored, tot)
    # L5's expectation covers the three acting relations only
    def acting_only(f):
        s = sum(f.get(k, 0.0) for k in ("open", "extend", "complete"))
        return ({k: round(f.get(k, 0.0) / s, 4)
                 for k in ("open", "extend", "complete")} if s else {})
    aa, rr = acting_only(af), acting_only(rf)
    return {
        "decisions": tot,
        "rows_restored": n_restored,
        "actual_incl_hold": af,
        "counterfactual_incl_hold": rf,
        "actual_acting_only": aa,
        "counterfactual_acting_only": rr,
        "l5_expected": dict(L5_EXPECTED),
        "l1_actual_vs_l5": _l1(aa, L5_EXPECTED),
        "l1_counterfactual_vs_l5": _l1(rr, L5_EXPECTED),
        "complete_share_actual": aa.get("complete"),
        "complete_share_counterfactual": rr.get("complete"),
        "this_is_a_measurement_not_a_proposal": (
            "restoring these rows would mean not honouring a cap. Nothing "
            "here proposes that. The figure says what the caps cost in "
            "fidelity, so the trade-off can be seen; the caps may be worth "
            "it."),
    }


def report(paths: Iterable[Any]) -> dict:
    decs = decisions(paths)
    mx = matrix(decs)
    cells = [("extend", "complete"), ("extend", "hold"), ("open", "hold"),
             ("open", "complete"), ("complete", "hold")]
    attr = {f"{s}->{a}": attribute(decs, s, a) for s, a in cells}
    attr = {k: v for k, v in attr.items() if v["n"]}
    cf = counterfactual_mix(decs)
    lead = attr.get("extend->complete", {})
    return {
        "decisions": len(decs),
        "with_a_sampled_relation": sum(1 for d in decs if fg._sampled(d)),
        "transition_matrix": mx,
        "attribution": attr,
        "counterfactual_mix": cf,
        "the_finding": (
            "Sampling is faithful; execution is not, and the gap is one cell. "
            f"Only {mx.get('extend', {}).get('fidelity', 0):.1%} of sampled "
            "EXTENDs are executed as EXTEND: "
            f"{mx.get('extend', {}).get('acted', {}).get('complete', 0)} become "
            "COMPLETE and "
            f"{mx.get('extend', {}).get('acted', {}).get('hold', 0)} become "
            "HOLD. OPEN and COMPLETE are executed close to as sampled."),
        "why_it_is_coherent": (
            "EXTEND grows a position and COMPLETE shrinks it. When the spend "
            "or residual cap binds the shell cannot grow, so an EXTEND draw "
            "is executed as the shrinking action. The shell is doing what it "
            "was built to do; the fidelity cost is the by-product."),
        "dominant_guard": (
            f"{lead.get('cap_binding', {})} over "
            f"{lead.get('n', 0)} EXTEND->COMPLETE conversions, with the shell "
            f"attesting {lead.get('shell_attests_it_would_have_acted_as_sampled', 0)} "
            "of them would have been EXTEND but for a cap."),
        "no_change_proposed": (
            "No safety control is removed, no cap is changed and no live "
            "policy is altered by this module. It measures; it does not "
            "recommend."),
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    mx, cf = rep["transition_matrix"], rep["counterfactual_mix"]
    L = ["# Sampled → acted: where COMPLETE actually comes from", "",
         f"**{rep['the_finding']}**", "",
         "## The transition matrix", "",
         "| sampled | n | → open | → extend | → complete | → hold | executed "
         "as sampled |", "|---|---|---|---|---|---|---|"]
    for s, r in mx.items():
        a = r["acted"]
        L.append(f"| {s} | {r['sampled_n']} | {a.get('open', 0)} | "
                 f"{a.get('extend', 0)} | {a.get('complete', 0)} | "
                 f"{a.get('hold', 0)} | **{r['fidelity']:.1%}** |")
    L += ["", "> " + rep["why_it_is_coherent"], "",
          "## What converted each cell", ""]
    for k, v in rep["attribution"].items():
        L += [f"### {k} — n={v['n']}", "",
              f"- divergence: `{v['divergence']}`",
              f"- refusal family: `{v['refusal_family']}`",
              f"- cap binding: `{v['cap_binding']}`",
              f"- shell attests it would have acted as sampled: "
              f"**{v['shell_attests_it_would_have_acted_as_sampled']}**, "
              f"unattributed {v['unattributed']}", ""]
    L += ["> " + list(rep["attribution"].values())[0]["attestation_note"], "",
          "## What the caps cost in fidelity", "",
          "| | complete | extend | open | L1 vs L5 |", "|---|---|---|---|---|"]
    for label, mix, l1 in (("actual", cf["actual_acting_only"],
                            cf["l1_actual_vs_l5"]),
                           ("counterfactual", cf["counterfactual_acting_only"],
                            cf["l1_counterfactual_vs_l5"])):
        L.append(f"| {label} | {mix.get('complete', 0):.1%} | "
                 f"{mix.get('extend', 0):.1%} | {mix.get('open', 0):.1%} | "
                 f"{l1:.4f} |")
    e = cf["l5_expected"]
    L.append(f"| L5 expected | {e['complete']:.1%} | {e['extend']:.1%} | "
             f"{e['open']:.1%} | — |")
    L += ["", f"{cf['rows_restored']} rows restored from the shell's own "
              f"would_act_without_safety_cap field.", "",
          "> " + cf["this_is_a_measurement_not_a_proposal"], "",
          "## No change proposed", "", rep["no_change_proposed"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    paths = ([os.path.join(a.logs, f) for f in sorted(os.listdir(a.logs))]
             if os.path.isdir(a.logs) else [a.logs])
    rep = report(paths)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "RELATION_DISTORTION_TRACE.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "relation_distortion_trace.json"),
                  "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
