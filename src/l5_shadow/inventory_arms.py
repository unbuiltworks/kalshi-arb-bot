"""PROSPECTIVE arm registry: frozen stochastic FIDELITY vs ADAPTED inventory gates.

OWNER 2026-09-17, after EXPLORATORY_V2 was frozen. Read-only. This module has
NO money-moving capability: it has no client, no submit path, and nothing here
can become an order.

WHAT IT IS FOR. EXPLORATORY_V2 ranked inventory gates on L5's own history. That
is retrospective and subtractive. This runs the surviving hypotheses FORWARD on
identical live observations so the comparison is prospective and the arms
differ only by policy.

TWO AXES, KEPT SEPARATELY ATTRIBUTABLE -- this is the point of the module:

    inventory_policy   when to allow the inventory to grow           (this file)
    placement_policy   where in the book to put the resulting quote  (frozen
                       PMUS sweep, combined only after its tape closes)

Every emitted row carries BOTH ids. A change in outcome can then be attributed
to the gate or to the placement, never to "the new arm" as one undivided thing.
The placement axis is deliberately pinned to one value until the PMUS tape
closes; the owner's instruction is to combine the winning behavioural shell
with the book-aware execution result only afterwards.

ONE DRAW, SHARED BY EVERY ARM. The stochastic relation is sampled ONCE per
decision and handed to all arms. Sampling per arm would let arms diverge
through RNG noise rather than through policy, and the whole comparison would
be measuring the seed.

NOTHING HERE IS A FILL. Arms emit CROSS / RESTABLE / EXECUTABLE / WAIT -- the
same vocabulary the live shell uses, which deliberately contains no FILLED.
Shadow inventory advances ONLY when an externally supplied, queue-evidenced
fill is applied through `apply_evidenced_fill`. A classification can never
advance it. There is no code path from a classification to an inventory change,
and `tests/test_inventory_arms.py` asserts that.

RECONCILIATION, not a second implementation. The gates are imported from the
frozen `analysis/l5/exploratory_v2/interventions.py`; the preregistered
run-cap arms are delegated to `src/l5_shadow/adapted_policy.py` unchanged.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from analysis.l5.exploratory_v2 import interventions as IV

LABEL = "PROSPECTIVE/inventory_arms_v1"

#: the only placement in use until the frozen PMUS book sweep closes. Combining
#: the two axes before then would confound the behavioural result with an
#: execution change, which is exactly what the owner asked to avoid.
PLACEMENT_PINNED = "FROZEN_CURRENT_SHELL"
PLACEMENT_PENDING = "PMUS_BOOK_AWARE_pending_tape_close"

CLASSIFICATIONS = ("CROSS", "RESTABLE", "EXECUTABLE", "WAIT")


# ---------------------------------------------------------------------------
# inventory policies

def _fidelity(_st, _f) -> bool:
    return True


#: arm id -> (gate, params, disposition). Gates come from the FROZEN module so
#: there is exactly one definition of each rule in the repository.
INVENTORY_POLICIES: Dict[str, Tuple[Any, dict, str]] = {
    "FIDELITY": (
        _fidelity, {},
        "CONTROL. Exact stochastic FIDELITY: no inventory gate at all."),
    "V2E_near10_sub50": (
        IV.v2e_matched_preservation(10.0, 50.0), {"near": 10, "substantial": 50},
        "LEADING ADAPTED HYPOTHESIS. Chronologically positive in both historical "
        "periods at 97.6-99.9% of matched P&L retained. NOT promoted to live."),
    "V2A_cap100": (
        IV.v2a_residual_cap(100.0), {"cap": 100},
        "SECONDARY CHALLENGER. Larger historical net gain, but retained only "
        "51%/21% of matched P&L."),
}

#: Preserved and NOT run by default. EXPLORATORY_V2 rejected the same-side run
#: cutoff as a CONTROL rule: its profitable cutoff moved from ~9 in training to
#: ~4 out of sample and every member was net-negative in training. The
#: preregistered cap5/cap8/cap13 family in `adapted_policy` is that rule, so it
#: is kept as evidence and is not part of the default comparison.
SUPERSEDED_AS_CONTROL = {
    "cap5": "src.l5_shadow.adapted_policy", "cap8": "src.l5_shadow.adapted_policy",
    "cap13": "src.l5_shadow.adapted_policy",
}

DEFAULT_ARMS: Tuple[str, ...] = ("FIDELITY", "V2E_near10_sub50", "V2A_cap100")


def arms(include: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    if include is None:
        return DEFAULT_ARMS
    unknown = [a for a in include if a not in INVENTORY_POLICIES]
    if unknown:
        raise KeyError("unknown inventory policy: %s" % unknown)
    return tuple(include)


# ---------------------------------------------------------------------------
# per-arm shadow state. SHADOW, and labelled as such everywhere it is read.

def new_state(arm: str) -> Dict[str, Any]:
    return {"arm": arm, "placement_policy": PLACEMENT_PINNED,
            "up": D("0"), "dn": D("0"), "run_side": None, "run_len": 0,
            "evidenced_fills": 0, "suppressed": 0, "allowed": 0,
            "basis": "SHADOW_INVENTORY: advances only on externally supplied "
                     "queue-evidenced fills; never on a classification"}


def residual(state: Mapping[str, Any]) -> D:
    return D(state["up"]) - D(state["dn"])


def apply_evidenced_fill(state: Dict[str, Any], *, side: str, qty: D,
                         evidence: str) -> Dict[str, Any]:
    """The ONLY way shadow inventory moves.

    `evidence` is the queue criterion the caller verified. Anything weaker than
    the strict criterion is rejected here rather than downgraded silently, so a
    theoretical fill cannot enter the inventory by being labelled optimistically
    upstream."""
    from analysis.l5.adapted import prospective as PR
    if evidence != PR.STRICT:
        raise ValueError(
            "refusing a non-strict fill into shadow inventory: %r (require %r)"
            % (evidence, PR.STRICT))
    if side not in ("Up", "Down"):
        raise ValueError("side must be Up or Down, got %r" % (side,))
    state["up" if side == "Up" else "dn"] += D(qty)
    state["run_len"] = (state["run_len"] + 1) if state["run_side"] == side else 1
    state["run_side"] = side
    state["evidenced_fills"] += 1
    return state


# ---------------------------------------------------------------------------
# one decision, fed identically to every arm

def decide(observation: Mapping[str, Any], states: Mapping[str, Dict[str, Any]],
           *, sampled_relation: str, proposal: Mapping[str, Any],
           classification: str) -> List[dict]:
    """Return one row per arm for a SINGLE shared observation and draw.

    `sampled_relation`, `proposal` and `classification` are computed ONCE by the
    caller and passed in unchanged. Arms may only decide whether to allow the
    proposal, never what it is -- otherwise the comparison would be measuring
    two changes at once.
    """
    if classification not in CLASSIFICATIONS:
        raise ValueError("classification must be one of %s, got %r"
                         % (CLASSIFICATIONS, classification))
    side = str(proposal.get("side") or "")
    qty = D(str(proposal.get("qty") or "0"))
    fill = (0, 0.0, side, float(qty), 0.0)
    rows: List[dict] = []
    for arm, st in states.items():
        gate, params, disposition = INVENTORY_POLICIES[arm]
        res = float(residual(st))
        s = IV.State(str(observation.get("slug") or ""),
                     int(observation.get("seq") or 0), res,
                     int(st["run_len"]), st["run_side"],
                     observation.get("abs_dp_60s"), observation.get("p_up"),
                     observation.get("tte_s"), st["evidenced_fills"],
                     float(st["up"]), float(st["dn"]))
        allowed = bool(gate(s, fill))
        st["allowed" if allowed else "suppressed"] += 1
        rows.append({
            "kind": "prospective_arm_decision", "label": LABEL,
            "slug": observation.get("slug"), "seq": observation.get("seq"),
            "t": observation.get("t"),
            # the two axes, always both present
            "inventory_policy": arm,
            "placement_policy": st["placement_policy"],
            "inventory_policy_disposition": disposition,
            "inventory_policy_params": params,
            # shared inputs, identical across arms by construction
            "sampled_relation": sampled_relation,
            "would_be_relation": IV._relation(res, side, float(qty)),
            "proposal": dict(proposal),
            "classification": classification,
            # the arm's only degree of freedom
            "allowed": allowed,
            "gate_reason": (None if allowed else
                            "%s suppressed %s at residual %.4f run %d" % (
                                arm, IV._relation(res, side, float(qty)),
                                res, st["run_len"])),
            "shadow_residual_before": str(residual(st)),
            "shadow_up": str(st["up"]), "shadow_dn": str(st["dn"]),
            "labels": ("SHADOW. `classification` is CROSS/RESTABLE/EXECUTABLE/"
                       "WAIT and is NEVER a fill. Inventory advances only via "
                       "apply_evidenced_fill under strict queue evidence."),
        })
    return rows


def divergence(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Where did the arms actually disagree on this decision?

    Reported per decision so the prospective record answers "which arm refused,
    and why" without re-deriving it later -- the same lesson as the live
    order-provenance row."""
    allowed = {r["inventory_policy"]: r["allowed"] for r in rows}
    ctrl = allowed.get("FIDELITY")
    return {
        "kind": "prospective_arm_divergence", "label": LABEL,
        "slug": rows[0]["slug"] if rows else None,
        "seq": rows[0]["seq"] if rows else None,
        "allowed_by_arm": allowed,
        "any_disagreement": len(set(allowed.values())) > 1,
        "diverged_from_fidelity": sorted(
            a for a, v in allowed.items() if ctrl is not None and v != ctrl),
        "placement_policy": (rows[0]["placement_policy"] if rows else None),
        "placement_held_constant": len(
            {r["placement_policy"] for r in rows}) == 1,
    }


def combine_with_placement(placement_policy: str, *, pmus_tape_closed: bool
                           ) -> str:
    """Switch the placement axis. Refuses before the PMUS tape has closed.

    The owner's sequencing: pick the behavioural shell first, then combine it
    with the frozen book-aware execution result -- and only afterwards, so the
    two changes never land together and confound each other."""
    if not pmus_tape_closed:
        raise RuntimeError(
            "placement axis is pinned to %r until the frozen PMUS book tape "
            "closes; combining earlier confounds inventory policy with "
            "execution placement" % PLACEMENT_PINNED)
    return placement_policy
