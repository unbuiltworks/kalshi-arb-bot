"""Four-arm prospective shadow: CONTROL + cap20 + cap35 + cap50, per decision.

SHADOW ONLY. No order path, no state written back to any engine, no arm can
cause or prevent a submission. Each arm is a bookkeeping replica that advances
ONLY on fills the venue actually gave us.

THE FILL STANDARD IS THE WHOLE GUARANTEE. An arm that would have refused an
action simply does not receive that fill. It can never receive a DIFFERENT
fill, at a different price, size or time, because no such fill exists in the
record. A refusal is therefore always a subtraction and never a substitution --
no theoretical fill can become a shadow fill.

PMUS ECONOMICS, NOT L5's. Costs here are the venue's own cash for OUR fills,
and the rebate is the venue's `commissionNotionalCollected` on our side. The
L5 crypto acquisition fee (0.07*p*(1-p)) is NOT applied: it was measured on
Polymarket crypto and our PMUS maker fills show no such wedge. Liquidity
rewards are tracked SEPARATELY and never enter profitability; estimated
incentives never enter anything.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src import l5_residual_saturation as sat

SCHEMA = "l5_saturation_shadow/v1"


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:            # noqa: BLE001
        return None


class Arm:
    """One arm's inventory replica. Advances only on source-supported fills."""

    def __init__(self, name: str):
        self.name = name
        self.up = D("0"); self.dn = D("0")
        self.cost_up = D("0"); self.cost_dn = D("0")
        self.rebate = D("0")          # venue-confirmed, negative commission
        self.refused = 0
        self.taken = 0
        self.worst_residual_shares = 0.0
        self.residual_shares = 0.0    # in L5 behavioral shares

    # --- read-only views -------------------------------------------------
    @property
    def matched(self) -> D:
        return min(self.up, self.dn)

    @property
    def residual(self) -> D:
        return self.up - self.dn

    @property
    def avg_up(self) -> Optional[D]:
        return (self.cost_up / self.up) if self.up else None

    @property
    def avg_dn(self) -> Optional[D]:
        return (self.cost_dn / self.dn) if self.dn else None

    def effective_pair(self) -> Optional[D]:
        a, b = self.avg_up, self.avg_dn
        return (a + b) if (a is not None and b is not None) else None

    def settle(self, won_up: bool) -> Dict[str, Optional[D]]:
        """Matched and residual P&L kept apart, never summed into one number."""
        m, r = self.matched, self.residual
        a, b = self.avg_up, self.avg_dn
        matched_pnl = (m * (D(1) - (a + b))
                       if (m > 0 and a is not None and b is not None) else D("0"))
        if r == 0:
            residual_pnl = D("0")
        else:
            r_up = r > 0
            r_avg = a if r_up else b
            payoff = abs(r) if (r_up == won_up) else D("0")
            residual_pnl = payoff - abs(r) * (r_avg or D("0"))
        return {"matched_pnl": matched_pnl, "residual_pnl": residual_pnl,
                "settlement_pnl": matched_pnl + residual_pnl,
                "stranded_residual": abs(r)}


def observe(*, arms: Dict[str, Arm], action: Optional[str], price: Any,
            size: Any, side_up: Optional[bool], add_shares: float,
            residual_shares: Optional[float], residual_units: Optional[float],
            unit_contracts: Any, filled: bool, fill_cost: Any = None,
            rebate: Any = None, maker_evidence: Any = None,
            rejection: Any = None) -> Dict[str, Any]:
    """One decision, scored on every arm at once. OBSERVATION ONLY."""
    rows: Dict[str, Any] = {}
    for name in sat.ARMS:
        arm = arms[name]
        verdict = sat.decide(arm=name, residual_shares=arm.residual_shares,
                             side_up=bool(side_up), add_shares=add_shares,
                             relation=action)
        allow = verdict["allow"]
        # an arm advances ONLY on a fill the venue actually gave us, and only
        # if this arm would have allowed the action that produced it
        if allow and filled and side_up is not None:
            q = _d(size) or D("0")
            c = _d(fill_cost)
            if c is None:
                px = _d(price)
                c = (px * q) if (px is not None) else D("0")
            if side_up:
                arm.up += q; arm.cost_up += c
            else:
                arm.dn += q; arm.cost_dn += c
            arm.rebate += (_d(rebate) or D("0"))
            arm.residual_shares += (add_shares if side_up else -add_shares)
            arm.worst_residual_shares = max(arm.worst_residual_shares,
                                            abs(arm.residual_shares))
            arm.taken += 1
        elif not allow:
            arm.refused += 1
        ep = arm.effective_pair()
        rows[name] = {
            "allow": allow, "reason": verdict["reason"],
            "taken": arm.taken, "refused": arm.refused,
            "inv_up": str(arm.up), "inv_dn": str(arm.dn),
            "matched_qty": str(arm.matched),
            "residual": str(arm.residual),
            "residual_shares": arm.residual_shares,
            "worst_residual_shares": arm.worst_residual_shares,
            "effective_pair_cost": (str(ep) if ep is not None else None),
            "effective_pair_cost_reason": (None if ep is not None else
                                           "only one leg held: pair undefined"),
            "rebate_confirmed_usd": str(arm.rebate),
        }
    return {
        "schema": SCHEMA,
        "action": action,
        "price": (str(_d(price)) if _d(price) is not None else None),
        "size": (str(_d(size)) if _d(size) is not None else None),
        "side_up": side_up,
        "filled": filled,
        "maker_fill_evidence": maker_evidence,
        "maker_fill_evidence_reason": (None if maker_evidence is not None else
                                       "venue served no maker/passive evidence"),
        "rejection": rejection,
        "conversion": sat.conversion_report(residual_units=residual_units,
                                            residual_shares=residual_shares,
                                            unit_contracts=unit_contracts),
        "arms": rows,
        "labels": ("SHADOW ONLY -- no order path. An arm advances only on a "
                   "venue-confirmed fill it would itself have allowed; a "
                   "refusal is a subtraction, never a substitution. Liquidity "
                   "rewards are tracked separately and never in profitability."),
    }


# ------------------------------------------------------------- the gate
GATE_CRITERIA = (
    "1_reduces_residual_vs_control",
    "2_retains_supported_complement_fills",
    "3_positive_fee_corrected_expectancy",
    "4_not_from_directional_residual",
    "5_acceptable_worst_case_stranded",
    "6_beats_control",
)


def advancement_gate(control: Dict[str, Any], arm: Dict[str, Any],
                     *, min_settled: int = 30) -> Dict[str, Any]:
    """The six criteria, each answered separately and with its sample size.

    A small sample FAILS the gate on sample size rather than being hidden
    behind an attractive bp/$.
    """
    n = int(arm.get("settled_campaigns") or 0)
    checks = {
        "1_reduces_residual_vs_control": (
            arm.get("worst_residual") is not None
            and control.get("worst_residual") is not None
            and arm["worst_residual"] < control["worst_residual"]),
        "2_retains_supported_complement_fills": (arm.get("complement_fills", 0) > 0),
        "3_positive_fee_corrected_expectancy": (arm.get("net", 0) > 0),
        "4_not_from_directional_residual": (arm.get("matched_pnl", 0) > 0
                                            and arm.get("matched_pnl", 0)
                                            >= arm.get("net", 0)),
        "5_acceptable_worst_case_stranded": (
            arm.get("worst_stranded") is not None
            and control.get("worst_stranded") is not None
            and arm["worst_stranded"] <= control["worst_stranded"]),
        "6_beats_control": (arm.get("net", 0) > control.get("net", 0)),
    }
    enough = n >= min_settled
    return {
        "schema": SCHEMA,
        "arm": arm.get("name"),
        "settled_campaigns": n,
        "sample_size_sufficient": enough,
        "min_settled_required": min_settled,
        "checks": checks,
        "passes": bool(enough and all(checks.values())),
        "labels": ("Sample size is reported first and gates the verdict: a "
                   "small n FAILS here rather than hiding behind bp/$. "
                   "Criterion 4 requires the matched leg to carry the result, "
                   "so an arm cannot pass on directional residual."),
    }
