"""L5-fidelity mode: behavioural policy separated from safety controls.

WHY THIS MODULE EXISTS. Several behavioural knobs are env-configurable but
their accessors clamp in the RESTRICTIVE direction only, so no configuration
can express the behaviour we are trying to replicate:

    entry_cutoff_s           _earlier_only(..., 600.0)  -> never later
    complete_cutoff_s        _earlier_only(..., 720.0)  -> never later
    opening_leg_max_contracts  downward only, ceiling 3 -> never larger
    order_ttl_s              min(120.0, v)              -> never longer
    maybe_controller_open    `if resting: return None`  -> never 2 openings

Measured against L5's own 214 profitable BTC15m windows, our engine would
attempt 12.0% of its actions and 1.00% of its contracts. The one-order
lockout is also strictly TIGHTER than the authorized `max_live_orders` of 2,
which it claims to mirror.

WHAT THIS MODULE IS. A registry of which ceilings may widen, by how much, and
an explicit statement of which controls may NEVER widen here. It is the
separation itself, made testable: `POLICY` and `SAFETY` are asserted disjoint
at import, and `ceiling()` returns today's constant whenever the mode is off,
so a deployment with the mode unset behaves EXACTLY as it does now.

WHAT THIS MODULE IS NOT. It does not arm anything, size anything, or decide
anything. Widening a policy ceiling only means a configured value is no longer
silently clamped away; every safety control below still binds afterwards, and
the order of those gates is unchanged.

FAIL CLOSED. The mode is on only for the exact token "ON". Absent, blank,
misspelled, "1", "true", "yes" -- anything else -- reads as OFF. A mode that a
typo can enable is not a mode, and the asymmetry is deliberate: the failure we
are protecting against is accidentally widening a live trading ceiling.
"""
from decimal import Decimal as D
import os
from typing import Any, Dict, Tuple

FIDELITY_ENV = "L5_FIDELITY_MODE"
#: the ONLY value that enables it. See FAIL CLOSED above.
ENABLE_TOKEN = "ON"

#: knob -> (today's ceiling, the ceiling in fidelity mode, what it bounds)
#:
#: Every fidelity ceiling is a bounded widening justified by a measured L5
#: behaviour, never "unlimited":
#:   entry/complete cutoff -- L5's median LAST trade is t=752s (early era),
#:     beyond BOTH current ceilings, so neither can currently be reached.
#:   clip -- bounded by the authorized cash, not by this number.
#:   order ttl -- a 120s TTL plus a one-order lockout caps openings at
#:     floor(entry_cutoff/ttl); that is what produced Campaign E's 4.
#:   concurrent openings -- 2 is the ALREADY AUTHORIZED max_live_orders. This
#:     raises nothing; it stops the controller refusing a slot it may use.
POLICY: Dict[str, Tuple[Any, Any, str]] = {
    "entry_cutoff_s":             (600.0, 840.0, "when a first leg may open"),
    "complete_cutoff_s":          (720.0, 870.0, "when a leg may complete"),
    "opening_leg_max_contracts":  (D("3"), D("10"), "contracts per opening"),
    "order_ttl_s":                (120.0, 300.0, "how long a quote rests"),
    "concurrent_opening_orders":  (1, 2, "controller opening slots"),
    # 4 -> 12 outside fidelity mode too (2026-11-11): the divisor only makes
    # clips SMALLER, and a value above the ceiling falls back to one
    # allocation -- the largest clip -- which is the unsafe direction.
    "round_allocations_max":      (12, 12, "openings per window"),
}

#: Controls that may NEVER be widened through this module. Listed by name so
#: the separation is explicit and testable rather than implied.
#:
#: `completion_pair_ceiling` is here ON PURPOSE and is not a policy choice:
#: paying more than redemption for a matched pair is a guaranteed loss by
#: no-arbitrage, and the L5 era that did it on 18% of its pairs is the era
#: that lost $1,507 over four days.
#:
#: `max_abs_residual` and `ABS_RESIDUAL_CAP_CONTRACTS` are here because
#: directional exposure is capital at risk, not behaviour.
SAFETY: Tuple[str, ...] = (
    "max_total_exposure_usd",
    "max_cash_deployed_usd",
    "max_usd_per_market",
    "max_abs_residual",
    "ABS_RESIDUAL_CAP_CONTRACTS",
    "max_unmatched_contracts",
    "max_worst_case_deficit_usd",
    "max_cumulative_loss_usd",
    "max_l5_cumulative_loss_usd",
    "daily_realized_loss_halt_usd",
    "campaign_additional_loss_usd",
    "max_concurrent_markets",
    "max_live_orders",
    "completion_pair_ceiling",
    "shadow_lock",
    "owner_arm_token",
    "halt_latch",
    "L5_LIVE_HALT",
    "reconciliation",
    "exact_order_cancellation",
    "watchdog_deadman",
    "cancel_margin_s",
    "authorization_expiry",
    "campaign_approval",
    "submission_class_budgets",
)

# the separation is a fact about this module, checked at import so it cannot
# drift: a knob can never be in both lists.
_overlap = sorted(set(POLICY) & set(SAFETY))
if _overlap:                                               # pragma: no cover
    raise AssertionError("knob is both policy and safety: %r" % (_overlap,))


def enabled() -> bool:
    """True only for the exact token. Anything else is OFF."""
    return (os.getenv(FIDELITY_ENV) or "").strip() == ENABLE_TOKEN


def ceiling(knob: str) -> Any:
    """The widest this knob may reach right now.

    Today's constant when the mode is off -- which is why an unset
    environment is bit-identical to current behaviour -- and the fidelity
    ceiling when it is on. An unknown knob raises: a silent None would read
    as "no ceiling" at exactly the wrong moment.
    """
    if knob in SAFETY:
        raise KeyError(
            "%s is a safety control and has no fidelity ceiling" % knob)
    try:
        today, fidelity, _ = POLICY[knob]
    except KeyError:
        raise KeyError("unknown policy knob %r" % knob) from None
    return fidelity if enabled() else today


def describe() -> Dict[str, Any]:
    """Reporting only: what the mode would change, for the config telemetry."""
    on = enabled()
    return {
        "label": "L5_FIDELITY_MODE",
        "enabled": on,
        "env": FIDELITY_ENV,
        "policy": {k: {"today": str(t), "fidelity": str(f),
                       "in_force": str(f if on else t), "bounds": w}
                   for k, (t, f, w) in sorted(POLICY.items())},
        "safety_never_widened_here": list(SAFETY),
        "labels": ("Policy ceilings only. Widening one means a configured "
                   "value is no longer clamped away; every safety control "
                   "still binds afterwards and the gate order is unchanged. "
                   "This module arms nothing and decides nothing."),
    }
