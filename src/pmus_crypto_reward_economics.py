"""Pure research evaluator; no order authorization or network access."""
from decimal import Decimal as D, InvalidOperation

def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean")
    x = D(str(value))
    if not x.is_finite():
        raise ValueError("nonfinite")
    return x

def evaluate(*, pool, period_seconds, remaining_seconds, share_lower,
             qualifying_fraction_lower, allocation_lower,
             accrued_lower, incremental_cost_upper, payout_floor="1",
             scope_verified=False):
    """Conditional conservative scenario, never a confirmed payout.

    Allocation is a verified lower bound on the market's fraction of a
    shared pool, not an assumed equal split. Share and qualification must
    be independently estimated over the remaining period, not a snapshot.
    Costs include incremental inventory loss and execution costs.
    """
    refused = {"eligible": False, "incremental_reward": D(0),
               "net_lower": None}
    if scope_verified is not True:
        return dict(refused, reason="unknown_pool_scope")
    try:
        p,t,r,s,q,a,c,k,f = map(_number, (
            pool, period_seconds, remaining_seconds, share_lower,
            qualifying_fraction_lower, allocation_lower, accrued_lower,
            incremental_cost_upper, payout_floor))
        if not (p > 0 and t > 0 and 0 < r <= t and
                all(0 <= x <= 1 for x in (s,q,a)) and
                0 <= c <= p*a and k >= 0 and f >= 1):
            raise ValueError("range")
        projected = min(p*a-c, p*a*r/t*s*q)
        # Do not credit previously accrued rewards as new earnings.
        # If the floor is not reached, count no incremental reward.
        reward = projected if c+projected >= f else D(0)
        net = reward-k
        return {"eligible": reward > 0 and net > 0,
                "incremental_reward": reward, "net_lower": net,
                "reason": "positive_scenario" if reward > 0 and net > 0
                else "floor_or_cost", "label": "modelled_not_paid"}
    except (ValueError, TypeError, InvalidOperation, ArithmeticError):
        return dict(refused, reason="invalid_inputs")
