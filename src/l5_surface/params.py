"""The surface's parameters, and the refusal that keeps them honest.

THE FAILURE THIS FILE EXISTS TO PREVENT. A parameterised surface with sensible
defaults is indistinguishable, three weeks later, from a surface whose
parameters were measured. The default becomes "what L5 does" by being written
down next to things that were actually observed. So a parameter here does not
have a value and a comment; it has a value and a STATUS, and an UNRESOLVED
parameter has no value at all -- reading one raises rather than returning a
guess.

    OBSERVED_PROSPECTIVE   established by the frozen prospective n=3
    OBSERVED_RETROSPECTIVE established by a frozen historical study, named
    INFERRED               reasoned from something observed, not measured
    UNRESOLVED             unknown. No value exists. Reading it raises.
    OURS_ADAPTED           OUR risk or economic choice. Never L5 evidence,
                           never promotable to an observation about him.

`prior_evidence` carries what the retrospective work already showed WITHOUT
promoting it to a value. A 100-share mode in 1,142 historical parents is a
real finding and still not a measured `base_parent_qty` for a prospective
surface, so it is recorded as the prior it is and the parameter stays
UNRESOLVED until the prospective sample speaks.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

SCHEMA = "l5_passive_surface_params/v1"

OBSERVED_PROSPECTIVE = "OBSERVED_PROSPECTIVE"
OBSERVED_RETROSPECTIVE = "OBSERVED_RETROSPECTIVE"
INFERRED = "INFERRED"
UNRESOLVED = "UNRESOLVED"
OURS_ADAPTED = "OURS_ADAPTED"

STATUSES = (OBSERVED_PROSPECTIVE, OBSERVED_RETROSPECTIVE, INFERRED,
            UNRESOLVED, OURS_ADAPTED)

#: statuses that may be read as a statement about L5's own behaviour
L5_EVIDENCE = (OBSERVED_PROSPECTIVE, OBSERVED_RETROSPECTIVE)


class Unresolved(Exception):
    """Raised when something asks an UNRESOLVED parameter for a value.

    This is the whole mechanism. The engine cannot quietly proceed on a
    default, because there is no default to proceed on."""


@dataclass(frozen=True)
class Param:
    name: str
    status: str
    value: Any = None
    evidence: Optional[str] = None
    prior_evidence: Optional[str] = None
    pending_source: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError("unknown status %r" % (self.status,))
        if self.status == UNRESOLVED and self.value is not None:
            raise ValueError(
                "%s is UNRESOLVED but carries a value; an UNRESOLVED parameter "
                "with a value is exactly the mislabelled default this type "
                "exists to prevent" % self.name)
        if self.status != UNRESOLVED and self.value is None:
            raise ValueError("%s claims %s but has no value"
                             % (self.name, self.status))
        if self.status in L5_EVIDENCE and not self.evidence:
            raise ValueError("%s claims to be observed about L5 and cites "
                             "nothing" % self.name)

    def get(self) -> Any:
        if self.status == UNRESOLVED:
            raise Unresolved(
                "%s is UNRESOLVED%s. No default is substituted." % (
                    self.name,
                    (" pending " + self.pending_source)
                    if self.pending_source else ""))
        return self.value

    def is_l5_evidence(self) -> bool:
        return self.status in L5_EVIDENCE


#: every parameter the engine can consult. Named here so a set that omits one
#: is a detectable error rather than a silent None.
FIELDS: Tuple[str, ...] = (
    "rung_count",
    "tick_offsets",
    "base_parent_qty",
    "secondary_rung_qty",
    "both_sides_policy",
    "inventory_price_skew",
    "inventory_size_skew",
    "refresh_policy",
    "replenishment_policy",
    "minimum_rest_time",
    "withdrawal_policy",
    "TTE_policy",
    "max_residual",
)


@dataclass(frozen=True)
class SurfaceParams:
    label: str
    kind: str                     # "FIDELITY" or "ADAPTED"
    params: Dict[str, Param]
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        missing = [f for f in FIELDS if f not in self.params]
        if missing:
            raise ValueError("parameter set is incomplete: %s" % missing)
        extra = [k for k in self.params if k not in FIELDS]
        if extra:
            raise ValueError("unknown parameters: %s" % extra)
        if self.kind not in ("FIDELITY", "ADAPTED"):
            raise ValueError("kind must be FIDELITY or ADAPTED")
        if self.kind == "FIDELITY":
            ours = [k for k, p in self.params.items()
                    if p.status == OURS_ADAPTED]
            if ours:
                raise ValueError(
                    "a FIDELITY set cannot carry OUR choices: %s. That is what "
                    "ADAPTED is for, and mixing them is how a risk decision "
                    "becomes a claim about L5." % ours)

    def get(self, name: str) -> Any:
        return self.params[name].get()

    def unresolved(self) -> List[str]:
        return [f for f in FIELDS if self.params[f].status == UNRESOLVED]

    def resolved(self) -> List[str]:
        return [f for f in FIELDS if self.params[f].status != UNRESOLVED]

    def l5_evidence_fields(self) -> List[str]:
        return [f for f in FIELDS if self.params[f].is_l5_evidence()]

    def spec_sha256(self) -> str:
        """Identity of this parameter set, for freezing.

        Covers the statuses and the citations as well as the values: a set
        whose numbers are unchanged but whose evidence was rewritten is NOT
        the same spec."""
        blob = json.dumps(
            {"label": self.label, "kind": self.kind,
             "params": {k: asdict(self.params[k]) for k in FIELDS}},
            sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def report(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA, "label": self.label, "kind": self.kind,
            "spec_sha256": self.spec_sha256(),
            "fields": {k: asdict(self.params[k]) for k in FIELDS},
            "unresolved": self.unresolved(),
            "n_unresolved": len(self.unresolved()),
            "l5_evidence_fields": self.l5_evidence_fields(),
            "notes": self.notes,
            "labels": [
                "OURS_ADAPTED is never evidence about L5.",
                "prior_evidence records a retrospective finding WITHOUT "
                "promoting it to a value.",
            ],
        }


def _u(name: str, *, prior: Optional[str] = None,
       pending: str = "L5_PASSIVE_SURFACE_PROSPECTIVE_V1") -> Param:
    return Param(name=name, status=UNRESOLVED, prior_evidence=prior,
                 pending_source=pending)


# --------------------------------------------------------------------------
# FIDELITY_V0 -- the closest evidence-supported reconstruction of L5, which is
# currently NO surface at all. Every field is UNRESOLVED pending the frozen
# prospective n=3, and the retrospective findings are attached as priors
# rather than written in as values.
#
# This set cannot produce a surface. That is correct: nothing has yet measured
# these quantities prospectively, and a set that produced one anyway would be
# producing it from numbers somebody chose.
# --------------------------------------------------------------------------
FIDELITY_V0 = SurfaceParams(
    label="L5_PASSIVE_SURFACE_FIDELITY_V0",
    kind="FIDELITY",
    notes=("Deliberately unusable. Populate ONLY from "
           "L5_PASSIVE_SURFACE_PROSPECTIVE_V1, field by field, and only where "
           "that study actually establishes the value."),
    params={
        "rung_count": _u(
            "rung_count",
            prior="L5_PASSIVE_SURFACE_SPEC_V1 draft used a 3-rung ladder at "
                  "ask-1/-2/-3; that was a HYPOTHESIS used to generate "
                  "predictions, never a measurement of L5's rung count."),
        "tick_offsets": _u(
            "tick_offsets",
            prior="worked case btc15m_1789665300: two simultaneous same-outcome "
                  "parents were ONE TICK apart (Up 0.90/0.89, Down 0.08/0.09). "
                  "n=2 instances, which shows adjacency exists and does not "
                  "establish an offset set."),
        "base_parent_qty": _u(
            "base_parent_qty",
            prior="18.9% of 1,142 chain-proven parents were exactly 100.00, "
                  "and 30/10/40 @7c collapsed to ONE parent of exactly 80.00. "
                  "A mode is not a rule: 81.1% were not 100."),
        "secondary_rung_qty": _u(
            "secondary_rung_qty",
            prior="the two adjacent-tick instances paired a 100-lot with a "
                  "small lot (33.38, 2.00). n=2."),
        "both_sides_policy": _u(
            "both_sides_policy",
            prior="up/down overlap within 5s was 0.0000 and within 60s 0.8919, "
                  "median 26s (worked case): two-sided on a ~30s cadence, NOT "
                  "simultaneous placement -- which is a statement about FILLS, "
                  "not about what was resting."),
        "inventory_price_skew": _u("inventory_price_skew"),
        "inventory_size_skew": _u(
            "inventory_size_skew",
            prior="corr(total inventory, qty) = -0.0029 and corr(|residual|, "
                  "qty) = -0.1296 over the worked case: size did NOT track "
                  "inventory there. Evidence AGAINST a skew, not a value for "
                  "one."),
        "refresh_policy": _u("refresh_policy"),
        "replenishment_policy": _u("replenishment_policy"),
        "minimum_rest_time": _u("minimum_rest_time"),
        "withdrawal_policy": _u(
            "withdrawal_policy",
            prior="UNOBSERVABLE from public data: cancellations and unfilled "
                  "orders appear in no feed we have. A prospective study of "
                  "fills cannot establish this field."),
        "TTE_policy": _u("TTE_policy"),
        "max_residual": _u(
            "max_residual",
            prior="L5 ran 84.3% FLIPPED windows and 1 flat window in 5,546: he "
                  "tolerates large residual. That is a description of him, and "
                  "OUR bound is a risk choice belonging to ADAPTED."),
    },
)


# --------------------------------------------------------------------------
# ADAPTED_V0 -- OUR deployment, which is a different thing and says so.
#
# Only the fields that are genuinely ours are filled: a residual bound and a
# minimum rest time are risk decisions that need no observation of L5 to
# justify. Everything describing the SHAPE of the surface stays UNRESOLVED,
# because ADAPTED is a modification of FIDELITY and cannot modify what is not
# yet known.
# --------------------------------------------------------------------------
ADAPTED_V0 = SurfaceParams(
    label="L5_PASSIVE_SURFACE_ADAPTED_V0",
    kind="ADAPTED",
    notes=("Our risk envelope over whatever FIDELITY turns out to be. The "
           "OURS_ADAPTED fields are decisions, not findings, and must never "
           "be cited as evidence about L5."),
    params={
        **{k: v for k, v in FIDELITY_V0.params.items()
           if k not in ("max_residual", "minimum_rest_time",
                        "inventory_price_skew", "withdrawal_policy",
                        "base_parent_qty", "inventory_size_skew",
                        "replenishment_policy")},
        "max_residual": Param(
            "max_residual", OURS_ADAPTED, value="3.00",
            evidence=None,
            prior_evidence="V2E matched-preservation was the leading ADAPTED "
                           "hypothesis and is NOT promoted to live.",
        ),
        # OUR ORDER SIZE IS OURS. The prospective sample shows a 100 mode at
        # ~26.5% of parents -- a mode, not a rule, so it does not establish a
        # base lot. But the size WE rest is a deployment decision in any case.
        # 100 mirrors his observed mode so our surface stays comparable to his
        # parents in shadow. A LIVE PROBE MUST REPLACE THIS with a bounded
        # size; it is not a probe quantity.
        "base_parent_qty": Param(
            "base_parent_qty", OURS_ADAPTED, value="100",
            prior_evidence="OURS. Mirrors the 26.5% exact-100 mode observed "
                           "over 238 chain-collapsed parents so the shadow is "
                           "comparable to his parents. NOT a live probe size.",
        ),
        "inventory_size_skew": Param(
            "inventory_size_skew", OURS_ADAPTED,
            value={"widening_multiplier": 1, "reducing_multiplier": 1},
            prior_evidence="OURS. No skew, chosen because nothing established "
                           "one; measured as an axis rather than assumed.",
        ),
        "replenishment_policy": Param(
            "replenishment_policy", OURS_ADAPTED,
            value={"mode": "REPLACE_AFTER_FILL"},
            prior_evidence="OURS. Replace a filled rung, because a surface "
                           "that does not replace is not a surface. The "
                           "prospective +5s statistic did not resolve it.",
        ),
        # TWO FIELDS NO STUDY OF PUBLIC DATA CAN EVER REACH, so if we want a
        # runnable surface at all, WE supply them and say plainly that they
        # are ours. A prospective study scores whether a level was resting; it
        # cannot see a level SHIFT with inventory, and it cannot see a
        # cancellation at all. Leaving them UNRESOLVED would make FIDELITY
        # permanently unrunnable -- which is the honest verdict for FIDELITY
        # and would be an absurd reason never to deploy anything.
        "inventory_price_skew": Param(
            "inventory_price_skew", OURS_ADAPTED,
            value={"widening_ticks": 0, "reducing_ticks": 0},
            prior_evidence="NO skew, chosen because nothing established one. "
                           "This is our default, not a finding about L5.",
        ),
        "withdrawal_policy": Param(
            "withdrawal_policy", OURS_ADAPTED,
            value={"mode": "WITHDRAW_ON_TTE_FLOOR", "tte_floor_s": 0},
            prior_evidence="OURS. Cancellations appear in no public feed, so "
                           "L5's withdrawal behaviour is unobservable and this "
                           "field can never be evidence about him.",
        ),
        "minimum_rest_time": Param(
            "minimum_rest_time", OURS_ADAPTED, value=30.0,
            evidence=None,
            prior_evidence="DEFAULT_VALID_FOR_S=30.0 in l5_intent: at 30s only "
                           "17.1% of L5's own intents were still authorized by "
                           "his next action, so 30s is a CEILING on validity "
                           "and not a comfortable one.",
        ),
    },
)

FROZEN_DIR = "artifacts/l5_surface"


def to_json(sp: SurfaceParams) -> Dict[str, Any]:
    return {"label": sp.label, "kind": sp.kind, "notes": sp.notes,
            "spec_sha256": sp.spec_sha256(),
            "params": {k: asdict(sp.params[k]) for k in FIELDS}}


def from_json(blob: Mapping[str, Any]) -> SurfaceParams:
    """Rebuild a frozen set, and REFUSE it if its hash moved.

    A spec whose stored sha no longer matches the set it describes has been
    edited since it was frozen, and loading it would silently run something
    other than what was reviewed."""
    sp = SurfaceParams(
        label=blob["label"], kind=blob["kind"], notes=blob.get("notes"),
        params={k: Param(**v) for k, v in blob["params"].items()})
    want = blob.get("spec_sha256")
    if want and want != sp.spec_sha256():
        raise ValueError(
            "%s was frozen at %s but now hashes to %s: it has been edited "
            "since it was frozen" % (sp.label, want[:16],
                                     sp.spec_sha256()[:16]))
    return sp


def load_frozen(directory: str = FROZEN_DIR) -> Dict[str, SurfaceParams]:
    import glob as _g
    out: Dict[str, SurfaceParams] = {}
    for f in sorted(_g.glob(os.path.join(directory, "*.json"))):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                sp = from_json(json.load(fh))
        except (OSError, KeyError, TypeError, ValueError):
            continue
        out[sp.label] = sp
    return out


SETS = {s.label: s for s in (FIDELITY_V0, ADAPTED_V0)}
SETS.update(load_frozen())
