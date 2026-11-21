"""L5_ADAPTED_V1 -- residual management only. Offline. Native stays frozen.

L5's own Sep 23-25 decomposition is the reason this exists: the matched leg
earned +520.7, +411.1 and +382.7 bp of spend on those three days while the
RESIDUAL leg lost -719.8, -1,175.5 and -824.9. The pair economics were not
the hole; the directional remainder was. So the one thing this policy is
allowed to change is what happens to the residual.

It changes NOTHING else. Side selection, price, size, timing, lambda and the
frozen rule are Native's and stay Native's, so a difference between the arms
cannot be a difference in two things at once.

THE HARD BARRIER
----------------
No settlement-outcome information may enter a decision. Not the settlement
price, not the eventual winner, not "the market later resolved Up", not a
terminal market state, and not any field derived from them. `assert_blind`
is called on every decision input and raises on anything that looks like an
outcome, because a residual policy that knows the answer is not a policy --
it is a scoring function that flatters itself.

WHAT IT MAY USE
---------------
The state at decision time and nothing after it: current inventory, the age
of the residual, the book, the pair cost already locked, and time remaining
in the window. All are knowable at t.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: fields that carry, or trivially reveal, the outcome. A decision input
#: containing any of these is a bug, not a feature.
FORBIDDEN_KEYS = (
    "settlementpx", "settlement_px", "settlement", "settled", "resolution",
    "resolved", "winner", "outcome_final", "final_outcome", "redeem",
    "redemption", "payout", "realized_pnl", "market_state", "expired",
    "settlementprice", "won", "lost",
)


class OutcomeLeak(AssertionError):
    """A decision input carried settlement information."""


def assert_blind(state: Mapping[str, Any], *, where: str = "decision") -> None:
    """Refuse to decide on anything that knows how the market ended."""
    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                lk = str(k).strip().lower().replace("-", "_")
                if any(bad in lk for bad in FORBIDDEN_KEYS):
                    raise OutcomeLeak(
                        "%s input carries settlement information at %s.%s"
                        % (where, path, k))
                walk(v, "%s.%s" % (path, k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))
    walk(state, where)


class AdaptedV1:
    """Tighter residual bound, a residual age limit, complement-repair bias.

    Three levers, each off by default so an experiment can turn on exactly
    one and attribute the difference:

      max_residual_shares   a tighter cap than Native's 10
      max_residual_age_s    how long a residual may sit unrepaired
      repair_bias           prefer the side that REDUCES |residual| when the
                            frozen rule would otherwise be indifferent

    None of these invents a trade. `repair` returns what the policy WANTS;
    whether that is executable is the execution layer's problem, and whether
    it happened is the venue's answer.
    """

    def __init__(self, *, max_residual_shares: Optional[D] = None,
                 max_residual_age_s: Optional[float] = None,
                 repair_bias: bool = False) -> None:
        self.max_residual_shares = max_residual_shares
        self.max_residual_age_s = max_residual_age_s
        self.repair_bias = repair_bias

    def as_dict(self) -> Dict[str, Any]:
        return {"policy": "L5_ADAPTED_V1",
                "max_residual_shares": (None if self.max_residual_shares is None
                                        else str(self.max_residual_shares)),
                "max_residual_age_s": self.max_residual_age_s,
                "repair_bias": self.repair_bias,
                "changes": "residual management only"}

    # ------------------------------------------------------------ decision

    def wants_repair(self, state: Mapping[str, Any]) -> Dict[str, Any]:
        """Should the residual be reduced right now, and toward which side?

        `state` must be blind. It carries: residual (signed, + is long UP),
        residual_age_s, and time_remaining_s.
        """
        assert_blind(state, where="wants_repair")
        residual = D(str(state.get("residual") or 0))
        age = state.get("residual_age_s")
        size = residual if residual >= 0 else -residual
        reasons: List[str] = []
        if self.max_residual_shares is not None and size > self.max_residual_shares:
            reasons.append("RESIDUAL_ABOVE_CAP")
        if (self.max_residual_age_s is not None and age is not None
                and float(age) > self.max_residual_age_s and size > 0):
            reasons.append("RESIDUAL_TOO_OLD")
        if not reasons:
            return {"repair": False, "reasons": [], "toward": None,
                    "residual": str(residual)}
        # repairing a long residual means acquiring the complement
        return {"repair": True, "reasons": reasons,
                "toward": "DOWN" if residual > 0 else "UP",
                "shares": str(size), "residual": str(residual)}

    def prefers(self, state: Mapping[str, Any], side: str) -> bool:
        """With repair_bias on, does this side reduce the imbalance?"""
        assert_blind(state, where="prefers")
        residual = D(str(state.get("residual") or 0))
        if not self.repair_bias or residual == 0:
            return False
        return (side == "DOWN") if residual > 0 else (side == "UP")


# ------------------------------------------------------- comparison frame

#: the axes Native and Adapted are compared on. Fixed here so an experiment
#: cannot quietly report only the flattering ones.
COMPARISON_AXES = ("matched_pnl", "residual_pnl", "maker_economics",
                   "taker_fees", "incentives", "total", "max_residual",
                   "drawdown", "capital_utilization")


def compare(native: Mapping[str, Any],
            adapted: Mapping[str, Any]) -> Dict[str, Any]:
    """Native beside Adapted on every axis, with the delta. No selection."""
    out: Dict[str, Any] = {"axes": {}, "labels": (
        "every axis is reported. A policy that improves one axis by wrecking "
        "another has not improved anything.")}
    for axis in COMPARISON_AXES:
        n, a = native.get(axis), adapted.get(axis)
        row: Dict[str, Any] = {"native": None if n is None else str(n),
                               "adapted": None if a is None else str(a)}
        if n is not None and a is not None:
            try:
                row["delta"] = str(D(str(a)) - D(str(n)))
            except Exception:                                  # noqa: BLE001
                row["delta"] = None
        out["axes"][axis] = row
    return out
