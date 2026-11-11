"""L5_CANONICAL vs L5_ADAPTED: never silently call an adapted action "L5".

THE DISTINCTION THIS ENFORCES. Two policies are maintained side by side:

  L5_CANONICAL  reproduces L5's ESTIMATED action distribution as faithfully as
                public evidence permits. It is a replication artefact and is
                allowed to propose actions we would never execute.
  L5_ADAPTED    starts from the canonical action and applies independently
                supported executable/economic constraints -- the pair filter,
                our bounded risk envelope, venue rules.

Every divergence carries a REASON, so the replication experiment survives the
adaptation. An adapted action with no recorded reason is a bug, not a policy.

READ-ONLY. Nothing here submits, prices or cancels anything, and no gate,
cap or threshold is defined here -- the caller passes its own live values in,
so this module can never become a second source of truth for a limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

CANONICAL = "L5_CANONICAL"
ADAPTED = "L5_ADAPTED"

# Every reason an adapted action may differ from the canonical one.
PAIR_ECONOMICS = "PAIR_ECONOMICS"
RISK_CAP = "RISK_CAP"
VENUE_RULE = "VENUE_RULE"
DATA_UNKNOWN = "DATA_UNKNOWN"
EXECUTION_UNSUPPORTED = "EXECUTION_UNSUPPORTED"
OTHER = "OTHER"

REASONS = (PAIR_ECONOMICS, RISK_CAP, VENUE_RULE, DATA_UNKNOWN,
           EXECUTION_UNSUPPORTED, OTHER)


@dataclass
class Divergence:
    """One place where ADAPTED refused or altered what CANONICAL proposed."""
    reason: str
    canonical_action: Any
    adapted_action: Any
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise ValueError(f"unknown divergence reason: {self.reason!r}")

    def as_dict(self) -> dict:
        return {"reason": self.reason,
                "canonical_action": self.canonical_action,
                "adapted_action": self.adapted_action,
                "detail": self.detail, "evidence": dict(self.evidence)}


@dataclass
class Decision:
    """A canonical proposal plus whatever the adapted policy did with it."""
    canonical_action: Any
    adapted_action: Any
    divergences: List[Divergence] = field(default_factory=list)

    @property
    def diverged(self) -> bool:
        return self.canonical_action != self.adapted_action

    def as_dict(self) -> dict:
        d = {"policy_canonical": CANONICAL, "policy_adapted": ADAPTED,
             "canonical_action": self.canonical_action,
             "adapted_action": self.adapted_action,
             "diverged": self.diverged,
             "divergences": [x.as_dict() for x in self.divergences]}
        # A SILENT DIVERGENCE IS THE ONE THING THIS MODULE EXISTS TO PREVENT.
        if self.diverged and not self.divergences:
            d["INVARIANT_VIOLATION"] = (
                "adapted action differs from canonical with NO reason "
                "recorded; this must never be reported as L5 behaviour")
        return d


def adapt(canonical_action: Any, *,
          pair_cost: Optional[float] = None,
          pair_ceiling: Optional[float] = None,
          residual_units: Optional[float] = None,
          residual_cap: Optional[float] = None,
          venue_admissible: bool = True,
          venue_detail: str = "",
          data_known: bool = True,
          data_detail: str = "",
          hold_action: Any = "hold") -> Decision:
    """Apply the adapted policy's constraints to ONE canonical action.

    Every limit is passed IN by the caller from its own live configuration;
    this function invents none and changes none.
    """
    diverg: List[Divergence] = []
    action = canonical_action

    if not data_known:
        diverg.append(Divergence(
            DATA_UNKNOWN, canonical_action, hold_action,
            detail=data_detail or "a required input is UNKNOWN; refusing "
                                  "rather than imputing",
            evidence={"data_detail": data_detail}))
        action = hold_action

    if action != hold_action and not venue_admissible:
        diverg.append(Divergence(
            VENUE_RULE, canonical_action, hold_action,
            detail=venue_detail or "the venue would not admit this order",
            evidence={"venue_detail": venue_detail}))
        action = hold_action

    if (action != hold_action and pair_cost is not None
            and pair_ceiling is not None and pair_cost > pair_ceiling):
        diverg.append(Divergence(
            PAIR_ECONOMICS, canonical_action, hold_action,
            detail=f"pair cost {pair_cost} exceeds the ceiling {pair_ceiling}",
            evidence={"pair_cost": pair_cost, "pair_ceiling": pair_ceiling}))
        action = hold_action

    if (action != hold_action and residual_units is not None
            and residual_cap is not None
            and abs(residual_units) >= residual_cap):
        diverg.append(Divergence(
            RISK_CAP, canonical_action, hold_action,
            detail=f"residual {residual_units} at or beyond cap {residual_cap}",
            evidence={"residual_units": residual_units,
                      "residual_cap": residual_cap}))
        action = hold_action

    return Decision(canonical_action=canonical_action, adapted_action=action,
                    divergences=diverg)


def summarize(decisions: List[Decision]) -> dict:
    """Divergence census. Counts only -- no judgement about which is better."""
    import collections
    by = collections.Counter()
    for d in decisions:
        for x in d.divergences:
            by[x.reason] += 1
    n = len(decisions)
    div = sum(1 for d in decisions if d.diverged)
    silent = sum(1 for d in decisions if d.diverged and not d.divergences)
    return {"decisions": n, "diverged": div,
            "diverged_pct": (round(100.0 * div / n, 2) if n else None),
            "by_reason": dict(by.most_common()),
            "silent_divergences": silent,
            "labels": ("ADAPTED actions are NEVER 'what L5 would do'; only "
                       "canonical_action may be described that way")}
