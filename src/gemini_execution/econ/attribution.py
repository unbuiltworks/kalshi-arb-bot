"""WHICH GATE IS ACTUALLY BLOCKING US, and would removing it change anything.

THE QUESTION THIS ANSWERS. `eligible=19, admitted=0` says the strategy refused
every economically eligible cycle. It does not say WHY, and "SPREAD appears in
the refusal list" is not an answer: a gate can appear in every refusal and still
be blocking nothing, because some OTHER gate was failing on every one of those
cycles too. Removing it would then change not a single decision.

So the unit of analysis is the MARGINAL BLOCKER:

    marginal   this cycle would have been ADMITTED if ONLY this gate were
               removed -- i.e. it is the sole failing gate
    co-blocked two or more gates failed, so no single removal admits the cycle
    passenger  the gate failed on cycles that were blocked by something else
               anyway, and removing it admits nothing

A gate that blocks 100% of cycles and is marginal on 0% is a passenger. A gate
that is marginal on 80% is the binding constraint. Those call for opposite
responses and the raw refusal counts cannot tell them apart.

ABLATIONS ARE COMPUTED, NOT DEPLOYED. Every recorded cycle carries its full gate
vector, so "what would the admission rate have been under gate set S" is
arithmetic over data already collected -- no re-run, no policy change, and
nothing fitted. The named ablations exist to answer one question: is
`max_spread` protecting us from bad inventory, or simply making participation
impossible? Attribution alone cannot settle that -- it says what is blocked, not
whether being blocked was good for us. That is `blocked_labeler`'s job, and the
two must be read together.

NOTHING HERE DECIDES ANYTHING. No threshold is read from these numbers and
applied in the same breath; a treatment proposed from this evidence is
preregistered separately before it runs.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "gemini_econ_gate_attribution/v1"

#: named ablations. Each is the SET OF GATES REMOVED.
ABLATIONS: Dict[str, Tuple[str, ...]] = {
    "current_gates": (),
    "no_spread_gate": ("SPREAD",),
    "flow_only": ("SPREAD", "TWO_SIDED_QUOTE", "COMPLEMENT_REACHABLE",
                  "ECONOMIC_FEASIBILITY", "REFERENCE_AGREEMENT", "HAZARD",
                  "INVENTORY_HEADROOM"),
    "quote_quality_only": ("FLOW_REVERSIBLE", "NOT_CONCENTRATED", "HAZARD",
                           "REFERENCE_AGREEMENT"),
    "flow_plus_economics": ("SPREAD", "REFERENCE_AGREEMENT", "HAZARD"),
    "economics_only": ("SPREAD", "FLOW_REVERSIBLE", "NOT_CONCENTRATED",
                       "TWO_SIDED_QUOTE", "REFERENCE_AGREEMENT", "HAZARD"),
}

LABELS = (
    "A gate that blocks every cycle but is MARGINAL on none is a passenger: "
    "removing it admits nothing. Refusal counts alone cannot show that.",
    "Ablations are arithmetic over recorded gate vectors. Nothing here is "
    "deployed, fitted, or read as a threshold.",
    "Attribution says WHAT is blocked, never whether being blocked was GOOD. "
    "Read it with blocked_labeler.",
)


def _verdicts(gates: Sequence[Mapping[str, Any]]) -> Dict[str, Optional[bool]]:
    return {str(g.get("gate")): g.get("pass") for g in gates or []}


def blocking_set(gates: Sequence[Mapping[str, Any]], *,
                 waived: Sequence[str] = ()) -> FrozenSet[str]:
    """Gates that refuse this cycle. UNKNOWN refuses unless it was waived."""
    w = set(waived or ())
    out = set()
    for name, v in _verdicts(gates).items():
        if v is False:
            out.add(name)
        elif v is None and name not in w:
            out.add(name)
    return frozenset(out)


def admitted_under(gates: Sequence[Mapping[str, Any]], *,
                   removed: Sequence[str] = (),
                   waived: Sequence[str] = ()) -> bool:
    """Would this cycle be admitted with `removed` gates deleted entirely?"""
    return not (blocking_set(gates, waived=waived) - set(removed or ()))


@dataclass
class GateAttribution:
    """Every eligible cycle's gate vector, and what it implies."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    max_rows: int = 5000

    def record(self, *, cohort: str, symbol: str, t: float, tte_s: Any,
               leg: Optional[str], price: Any, spread: Any,
               gates: Sequence[Mapping[str, Any]],
               waived: Sequence[str] = (),
               admitted: Optional[bool] = None) -> Dict[str, Any]:
        blk = blocking_set(gates, waived=waived)
        row = {
            "cohort": cohort, "symbol": symbol, "t": t,
            "tte_s": (None if tte_s is None else float(tte_s)),
            "leg": leg, "price": (None if price is None else str(price)),
            "spread": (None if spread is None else str(spread)),
            "verdicts": _verdicts(gates),
            "blocking": sorted(blk),
            "n_blocking": len(blk),
            # the marginal blocker: sole failing gate, or None
            "marginal_blocker": (sorted(blk)[0] if len(blk) == 1 else None),
            "admitted": (bool(admitted) if admitted is not None
                         else not blk),
        }
        self.rows.append(row)
        if len(self.rows) > self.max_rows:
            self.rows = self.rows[-(self.max_rows // 2):]
        return row

    # ------------------------------------------------------------ analysis
    def report(self, *, tte_buckets: bool = True) -> Dict[str, Any]:
        n = len(self.rows)
        if not n:
            return {"schema": SCHEMA, "cycles": 0, "labels": list(LABELS)}

        blocked_by = Counter()
        marginal_by = Counter()
        combos = Counter()
        for r in self.rows:
            for g in r["blocking"]:
                blocked_by[g] += 1
            if r["marginal_blocker"]:
                marginal_by[r["marginal_blocker"]] += 1
            combos[tuple(r["blocking"])] += 1

        gates = sorted(set(blocked_by) | set(marginal_by))
        per_gate = {}
        for g in gates:
            b, m = blocked_by[g], marginal_by[g]
            per_gate[g] = {
                "blocked_n": b, "blocked_pct": round(100.0 * b / n, 1),
                "marginal_n": m, "marginal_pct": round(100.0 * m / n, 1),
                # the number that matters: of the cycles this gate blocked,
                # on how many was it the ONLY thing blocking?
                "marginal_share_of_its_blocks": (round(m / b, 3) if b else None),
                "verdict": ("BINDING" if m > 0 else
                            ("PASSENGER" if b > 0 else "INACTIVE")),
            }

        abl = {}
        for name, removed in ABLATIONS.items():
            ok = sum(1 for r in self.rows
                     if not (set(r["blocking"]) - set(removed)))
            abl[name] = {"removed": list(removed), "admitted_n": ok,
                         "admitted_pct": round(100.0 * ok / n, 1),
                         "delta_vs_current": None}
        base = abl["current_gates"]["admitted_n"]
        for name, d in abl.items():
            d["delta_vs_current"] = d["admitted_n"] - base

        out = {
            "schema": SCHEMA, "cycles": n,
            "admitted_n": base,
            "admitted_pct": round(100.0 * base / n, 1),
            "per_gate": per_gate,
            "blocker_combinations": [
                {"gates": list(k), "n": v,
                 "pct": round(100.0 * v / n, 1)}
                for k, v in combos.most_common(8)],
            "ablations": abl,
            "labels": list(LABELS),
        }
        if tte_buckets:
            out["by_tte_and_leg"] = self._segmented()
        return out

    def _segmented(self) -> Dict[str, Any]:
        """BTC5m by TTE and by INVENTORY DIRECTION, never pooled.

        YES and NO toxicity are not assumed symmetric; pooling them would
        average away the asymmetry the sprint is looking for.
        """
        from src.gemini_execution.econ import spread_census as SC
        seg: Dict[str, Dict[str, Any]] = {}
        for r in self.rows:
            b = SC._bucket(r.get("tte_s"), SC.TTE_EDGES, SC.TTE_LABELS)
            key = "%s|%s|%s" % (r["cohort"], b, r.get("leg") or "?")
            s = seg.setdefault(key, {"n": 0, "admitted": 0,
                                     "blocked_by": Counter(),
                                     "marginal_by": Counter()})
            s["n"] += 1
            s["admitted"] += 1 if r["admitted"] else 0
            for g in r["blocking"]:
                s["blocked_by"][g] += 1
            if r["marginal_blocker"]:
                s["marginal_by"][r["marginal_blocker"]] += 1
        return {k: {"n": v["n"], "admitted": v["admitted"],
                    "blocked_by": dict(v["blocked_by"]),
                    "marginal_by": dict(v["marginal_by"])}
                for k, v in sorted(seg.items())}
