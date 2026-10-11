"""The FROZEN BTC15m continuous-inventory policy, loaded -- never restated.

THERE IS ONE POLICY AND IT LIVES IN ONE FILE:
`analysis/gemini/info_edge/btc15m_shadow_policy.py`, the same bytes the
prospective EXTEND-vs-NO-EXTEND shadow runs. This module LOADS it and
publishes a FINGERPRINT of those bytes. Nothing here reimplements a
threshold, a gate or a decision.

WHY A FINGERPRINT. An authorization is granted against a policy, not against
a filename. If the policy file changes by one byte the fingerprint changes,
the authorization no longer matches, and the probe fails closed. That is what
makes "keep the frozen policy exactly as committed" an enforceable statement
rather than a promise.

WHY THE POLICY FILE IS SAFE TO IMPORT. It has no transport import, no signer
and no order path -- `test_ci15m_policy.py` asserts that by scanning its
module namespace. Importing it cannot reach a venue.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from decimal import Decimal as D
from typing import Any, Dict

#: the ONE policy file. Resolved from this module's location, not from cwd.
POLICY_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "analysis", "gemini", "info_edge", "btc15m_shadow_policy.py"))


class PolicyError(RuntimeError):
    """The frozen policy could not be loaded, or is not the frozen one."""


def policy_bytes() -> bytes:
    with open(POLICY_PATH, "rb") as fh:
        return fh.read()


def fingerprint() -> str:
    """sha256 of the policy file's exact bytes. The authorization binds here."""
    return hashlib.sha256(policy_bytes()).hexdigest()


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "btc15m_shadow_policy_frozen", POLICY_PATH)
    if spec is None or spec.loader is None:        # pragma: no cover
        raise PolicyError("cannot load the frozen policy at %s" % POLICY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MODULE = _load()

#: EVERY number and function the live probe may use, taken from the frozen
#: module. A value absent from here is a value the probe cannot reach.
POLICY: Dict[str, Any] = {
    "path": POLICY_PATH,
    "fingerprint": fingerprint(),

    # ---- the frozen thresholds, by reference
    "EXTEND_MIN_AVG_IMPROVEMENT": MODULE.EXTEND_MIN_AVG_IMPROVEMENT,
    "MAX_EXTENDS_BEFORE_COMPLEMENT": MODULE.MAX_EXTENDS_BEFORE_COMPLEMENT,
    "MAX_PAIR_COST": MODULE.MAX_PAIR_COST,
    "HARD_MAX_PRICE": MODULE.HARD_MAX_PRICE,
    "MAX_RESIDUAL_PRINCIPAL": MODULE.MAX_RESIDUAL_PRINCIPAL,
    "OPEN_TTE_LO": MODULE.OPEN_TTE_LO,
    "OPEN_TTE_HI": MODULE.OPEN_TTE_HI,

    # ---- the decision, and the measurements around it
    "decide_once": MODULE.decide_once,
    "locked": MODULE.locked,
    "coherent": MODULE.coherent,
    "risk_block": MODULE.risk_block,
    "want_extend_avg": MODULE.want_extend_avg,
    "want_complement": MODULE.want_complement,
    "required_complement_price": MODULE.required_complement_price,
    "maker_counterfactual": MODULE.maker_counterfactual,

    # ---- the accounting
    "Inventory": MODULE.Inventory,
    "make_fill": MODULE.make_fill,
}

#: the probe's q. Stated ONCE, here, and asserted all the way to the payload.
QUANTITY = D("1")


def summary() -> Dict[str, Any]:
    """What a preflight prints. Opens no mutation path."""
    return {
        "policy_path": POLICY_PATH,
        "policy_fingerprint": POLICY["fingerprint"],
        "quantity": str(QUANTITY),
        "extend_min_avg_improvement":
            str(POLICY["EXTEND_MIN_AVG_IMPROVEMENT"]),
        "max_extends_before_complement":
            str(POLICY["MAX_EXTENDS_BEFORE_COMPLEMENT"]),
        "max_pair_cost": str(POLICY["MAX_PAIR_COST"]),
        "hard_max_price": str(POLICY["HARD_MAX_PRICE"]),
        "open_tte_band": [POLICY["OPEN_TTE_LO"], POLICY["OPEN_TTE_HI"]],
        "single_source": ("the live probe and the prospective shadow call the "
                          "SAME decide_once in the SAME file; there is no "
                          "second copy of the policy."),
    }
