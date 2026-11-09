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
