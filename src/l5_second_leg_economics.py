"""Read-only counterfactual second-leg economics; never submits orders.

Each candidate is an executable *hypothesis*, not a fill. Prices, available
depth, fees and adverse-selection haircuts must come from timestamped venue
observations. Compare candidates at the SAME decision instant and inventory.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

D = Decimal


def dec(value):
    try:
        result = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid numeric input")
    if not result.is_finite():
        raise ValueError("non-finite numeric input")
    return result


@dataclass(frozen=True)
class Inventory:
    up_qty: Decimal
    down_qty: Decimal
    total_cost: Decimal

    def __post_init__(self):
        if min(self.up_qty, self.down_qty, self.total_cost) < 0:
            raise ValueError("negative inventory or cost")

    def outcome_net(self):
        return self.up_qty - self.total_cost, self.down_qty - self.total_cost


@dataclass(frozen=True)
class Candidate:
    action: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    depth: Decimal
    book_age_s: Decimal
    adverse_cost: Decimal = D("0")
    maker_fill_probability: Optional[Decimal] = None

    def __post_init__(self):
        if self.side not in ("UP", "DOWN"):
            raise ValueError("invalid side")
        if self.action not in ("MAKER", "REPRICE", "TAKER"):
            raise ValueError("invalid action")
        if self.quantity <= 0 or not (D("0") <= self.price <= D("1")):
            raise ValueError("invalid quantity or price")
        if min(self.fee, self.depth, self.book_age_s, self.adverse_cost) < 0:
            raise ValueError("negative cost, depth or book age")
        if self.maker_fill_probability is not None and not (
            D("0") <= self.maker_fill_probability <= D("1")
        ):
            raise ValueError("invalid probability")


def evaluate(inv: Inventory, candidate: Candidate, *, max_book_age_s=D("2"),
             cash_headroom=None, worst_case_loss_ceiling=None):
    """Return bounded hypothetical economics, not a trade instruction.

    For maker/reprice, a fill is NOT assumed. The output exposes both
    fill and no-fill branches, and refuses to rank them by expected value
    unless a validated fill probability was explicitly supplied.
    """
    u0, d0 = inv.outcome_net()
    hold_worst = min(u0, d0)
    result = {"action": candidate.action, "hold_up": str(u0),
              "hold_down": str(d0), "hold_worst": str(hold_worst),
              "eligible": False, "reason": None}
    if candidate.book_age_s > dec(max_book_age_s):
        result["reason"] = "STALE_BOOK"
        return result
    if candidate.depth < candidate.quantity:
        result["reason"] = "INSUFFICIENT_DEPTH"
        return result
    spend = candidate.quantity * candidate.price + candidate.fee + candidate.adverse_cost
    if cash_headroom is not None and spend > dec(cash_headroom):
        result["reason"] = "CASH_HEADROOM"
        return result
    u1 = u0 + (candidate.quantity if candidate.side == "UP" else D("0")) - spend
    d1 = d0 + (candidate.quantity if candidate.side == "DOWN" else D("0")) - spend
    worst = min(u1, d1)
    if worst_case_loss_ceiling is not None and worst < -dec(worst_case_loss_ceiling):
        result["reason"] = "WORST_CASE_LIMIT"
        return result
    result.update(eligible=True, spend=str(spend), fill_up=str(u1),
                  fill_down=str(d1), fill_worst=str(worst),
                  worst_improvement=str(worst - hold_worst),
                  no_fill_worst=str(hold_worst))
    if candidate.action == "TAKER":
        result["executable_worst"] = str(worst)
    elif candidate.maker_fill_probability is not None:
        p = candidate.maker_fill_probability
        result["model_expected_worst"] = str(p * worst + (D("1") - p) * hold_worst)
        result["model_only"] = True
    else:
        result["model_expected_worst"] = None
        result["reason"] = "FILL_PROBABILITY_UNVALIDATED"
    return result


def evaluate_paired_entry(*, up_price, down_price, quantity, up_fee="0",
                          down_fee="0", p_both=None, p_up_only=None,
                          p_down_only=None, p_neither=None,
                          p_up_settles_given_up_only=None,
                          p_up_settles_given_down_only=None, adverse_cost="0",
                          max_combined_cost=None, minimum_expected_net="0",\n                          max_residual_probability=None,\n                          max_single_leg_loss=None):
    """Read-only joint-fill paired-entry model; NEVER an order instruction.

    Probabilities must be externally calibrated from time-aligned order events.
    A joint distribution is mandatory: assuming independent maker fills would
    conceal trend-driven adverse selection. Settlement probability is conditional on which leg filled alone,
    because fill-side adverse selection affects the settlement outcome. No estimated fills are represented as executions.
    Returns a refusal when the necessary evidence is unavailable.
    """
    q = dec(quantity)
    u, d = dec(up_price), dec(down_price)
    uf, df, adverse = dec(up_fee), dec(down_fee), dec(adverse_cost)
    if q <= 0 or not (D("0") <= u <= D("1") and D("0") <= d <= D("1")):
        raise ValueError("invalid quote price or quantity")
    if min(uf, df, adverse) < 0:
        raise ValueError("negative fee or adverse cost")
    pair_cost = q * (u + d) + uf + df + adverse
    result = {"eligible": False, "pair_cost": str(pair_cost),
              "pair_net_if_both_fill": str(q - pair_cost),
              "reason": "JOINT_FILL_OR_SETTLEMENT_UNVALIDATED"}
    if max_combined_cost is not None and pair_cost > dec(max_combined_cost):
        result["reason"] = "PAIR_COST_LIMIT"
        return result
    probs = (p_both, p_up_only, p_down_only, p_neither,
             p_up_settles_given_up_only, p_up_settles_given_down_only)
    if any(p is None for p in probs):
        return result
    both, up_only, down_only, neither, settle_up_if_up, settle_up_if_down = map(dec, probs)
    if any(p < 0 or p > 1 for p in (both, up_only, down_only, neither, settle_up_if_up, settle_up_if_down)):
        raise ValueError("probability outside [0,1]")
    if sum((both, up_only, down_only, neither)) != 1:
        raise ValueError("joint fill probabilities must sum to one")
    # Costs are incurred only for filled legs; adverse cost is charged to
    # both-filled paths only. Each residual is marked through settlement.
    up_only_net = q * settle_up_if_up - (q * u + uf + adverse)
    down_only_net = q * (1 - settle_up_if_down) - (q * d + df + adverse)
    both_net = q - pair_cost
    expected = both * both_net + up_only * up_only_net + down_only * down_only_net
    threshold = dec(minimum_expected_net)\n    residual_probability = up_only + down_only\n    residual_worst = min(-(q * u + uf + adverse),\n                         -(q * d + df + adverse))\n    if max_residual_probability is not None:\n        cap = dec(max_residual_probability)\n        if not D("0") <= cap <= D("1"):\n            raise ValueError("invalid residual probability limit")\n        if residual_probability > cap:\n            result.update(reason="RESIDUAL_PROBABILITY_LIMIT",\n                          expected_net=str(expected),\n                          p_any_residual=str(residual_probability),\n                          model_only=True)\n            return result\n    if max_single_leg_loss is not None:\n        limit = dec(max_single_leg_loss)\n        if limit < 0:\n            raise ValueError("negative single-leg loss limit")\n        if residual_worst < -limit:\n            result.update(reason="SINGLE_LEG_LOSS_LIMIT",\n                          expected_net=str(expected),\n                          residual_worst=str(residual_worst),\n                          model_only=True)\n            return result
    result.update(eligible=(expected > threshold),
                  reason=None if expected > threshold else "NONPOSITIVE_EXPECTED_NET",
                  model_only=True,
                  expected_net=str(expected),
                  p_any_residual=str(residual_probability),\n                  residual_worst=str(residual_worst),
                  up_only_expected_net=str(up_only_net),
                  down_only_expected_net=str(down_only_net),
                  both_net=str(both_net),
                  no_fill_net="0")
    return result
