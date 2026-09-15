"""DesiredInventoryAction -- the one venue-neutral thing the L5 engine emits.

WHY THIS TYPE EXISTS. Until now "what L5 wants" and "what a venue would let us
do" were computed in the same breath, which makes both unfalsifiable: a bad
intent looks like a venue problem and a bad venue looks like a bad model. An
intent is frozen FIRST, with an id, and every adapter is then scored against
that exact frozen object.

WHAT IT DELIBERATELY DOES NOT CONTAIN:

  * no venue, symbol or token id -- market mapping is the ADAPTER's job and is
    allowed to fail (a market may have no equivalent on a venue);
  * no fee, rebate or spread -- those are venue facts;
  * no submission price -- `max_acceptable_price` is an ECONOMIC CEILING, the
    worst price at which this action is still worth doing. What to actually
    submit, and whether that rests or crosses, is the adapter's decision;
  * no fill assumption of any kind.

`max_acceptable_price` is the load-bearing field. It is what lets a router
answer "neither venue" honestly: if no venue can acquire the desired inventory
at or under the ceiling, the correct action is not to trade.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
from typing import Any, Mapping, Optional

SCHEMA = "l5_desired_inventory_action/v1"

# The four things the behavioural model can want. COMPLETE buys the opposite
# side to close a pair; SWITCH changes which side is being accumulated.
ACTIONS = ("OPEN", "EXTEND", "COMPLETE", "SWITCH")


def _s(v: Any) -> Optional[str]:
    return None if v is None else str(v)


@dataclass(frozen=True)
class DesiredInventoryAction:
    """Frozen. An intent that can be edited after the fact scores nothing."""

    # --- identity of the state this came from
    cohort: str                      # BTC15m, BTC5m, ...
    market_key: str                  # venue-neutral market identity
    source_state_id: str             # the exact observed state that produced it
    t_utc: float                     # when the engine formed the intent
    tte_s: float                     # seconds to expiry at that moment

    # --- what the engine wants
    action: str                      # one of ACTIONS
    desired_outcome: str             # "Up" / "Down" -- the side to hold
    desired_inventory: str           # target holding in that outcome, shares
    incremental_qty: str             # how much MORE to acquire now
    max_acceptable_price: str        # economic ceiling, per share, 0 < p < 1

    # --- the inventory this is relative to
    current_matched: str             # min(up, dn) already held
    residual: str                    # signed: + is long Up, - is long Down

    # --- provenance
    model_version: str
    confidence: Optional[float] = None
    horizon_s: Optional[float] = None
    notes: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}: {self.action}")
        p = D(self.max_acceptable_price)
        if not (D("0") < p < D("1")):
            raise ValueError(
                f"max_acceptable_price must be a probability strictly between "
                f"0 and 1, got {p}. A ceiling of 0 or 1 is not an economic "
                f"bound, it is the absence of one.")
        if D(self.incremental_qty) <= 0:
            raise ValueError("incremental_qty must be positive; an intent to "
                             "do nothing is not an intent")

    @property
    def id(self) -> str:
        return intent_id(self)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        d["intent_id"] = self.id
        return d


def intent_id(a: "DesiredInventoryAction") -> str:
    """Content hash over the economically meaningful fields.

    `notes` and `extra` are excluded on purpose: annotating an intent must not
    change its identity, or the same decision acquires two ids and an adapter
    can appear to have scored an intent it never saw.
    """
    body = {k: v for k, v in asdict(a).items() if k not in ("notes", "extra")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return "int-" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def from_fidelity_state(state: Mapping[str, Any], prediction: Mapping[str, Any],
                        *, cohort: str, market_key: str, source_state_id: str,
                        max_acceptable_price: Any,
                        incremental_qty: Any = "1") -> DesiredInventoryAction:
    """Build an intent from a frozen FIDELITY row.

    The ceiling is supplied by the CALLER rather than derived here. Pricing
    policy is a risk decision, and burying it in a constructor is how a
    behavioural model quietly acquires one.
    """
    up, dn = D(str(state.get("inv_up") or 0)), D(str(state.get("inv_dn") or 0))
    resid = up - dn
    outcome = str(prediction.get("predicted_outcome") or "Up")
    # residual already long the predicted side -> EXTEND; against it -> SWITCH;
    # flat -> OPEN. COMPLETE is chosen by the caller, which knows the pair rule.
    if resid == 0:
        action = "OPEN"
    elif (resid > 0) == (outcome == "Up"):
        action = "EXTEND"
    else:
        action = "SWITCH"
    inc = D(str(incremental_qty))
    held = up if outcome == "Up" else dn
    return DesiredInventoryAction(
        cohort=cohort, market_key=market_key, source_state_id=source_state_id,
        t_utc=float(state.get("anchor_t") or 0.0),
        tte_s=float(state.get("tte_s") or 0.0),
        action=action, desired_outcome=outcome,
        desired_inventory=_s(held + inc), incremental_qty=_s(inc),
        max_acceptable_price=_s(max_acceptable_price),
        current_matched=_s(min(up, dn)), residual=_s(resid),
        model_version=str(prediction.get("model_version") or "unknown"),
        confidence=(prediction.get("p_outcome_up")
                    if outcome == "Up" else
                    (None if prediction.get("p_outcome_up") is None
                     else 1.0 - float(prediction["p_outcome_up"]))),
        horizon_s=None,
    )
