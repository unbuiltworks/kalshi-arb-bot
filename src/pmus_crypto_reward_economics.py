"""Pure research evaluator; no order authorization or network access.

EVIDENCE LEVELS (owner 2026-11-28: never substitute an assumed payout for
venue evidence). Every figure this module returns names one:

  PUBLISHED  read from a public programme row (/v1/incentives): pool,
             target size, discount factor, max spread, period label.
  INFERRED   depends on an allocation basis nobody has confirmed: how a
             `daily_event` pool is divided between contracts and over time.
  PAID       confirmed by authenticated earnings rows naming the market.
             None exist for a crypto market (28 Nov 2026).

A reward is CREDITED (counted in a net estimate) only when the allocation is
PAID-verified (`scope_verified=True`), or when the caller names one
allocation hypothesis explicitly (`probe_hypothesis`) -- the route for a
bounded probe whose purpose is to obtain PAID evidence. Otherwise the
evaluator fails closed: reward 0, reason `unknown_pool_scope`.

ALLOCATION HYPOTHESES for a crypto up/down programme row (period
"daily_event"; the docs call it "a per-day pool on an event ... midnight to
midnight Eastern Time"). All three are consistent with the published row:

  per_contract_window     the contract's pool is paid over the contract's
                          own trading window (1 h or 24 h)
  per_contract_et_day     each contract has its own pool for a 24 h day; a
                          contract earns only for the seconds it trades
  shared_program_et_day   one pool per programme id per 24 h day, divided
                          between the contracts carrying that id at once
                          (ETH, SOL and XRP share an id; BTC has its own)

For a DAILY market the first two are the same number. `allocation_scenarios`
lists them most conservative first; `conservative` combines per-hypothesis
results so a decision never rests on the optimistic one.
"""
from decimal import Decimal as D, InvalidOperation

EVIDENCE = ("PUBLISHED", "INFERRED", "PAID")
HYPOTHESES = ("shared_program_et_day", "per_contract_et_day",
              "per_contract_window")          # most conservative first
DAY_S = D(86400)
LABEL = "modelled_not_paid"


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean")
    x = D(str(value))
    if not x.is_finite():
        raise ValueError("nonfinite")
    return x


def allocation_scenarios(*, pool, window_seconds, sharing_contracts=1):
    """Every allocation basis consistent with one published programme row,
    most conservative first: [{"hypothesis", "period_seconds", "allocation",
    "usd_per_hour", "usd_per_24h", "evidence": "INFERRED"}]. `sharing_contracts` is how
    many contracts carry the programme id at the same time (3 for the alt
    programmes, 1 for BTC). Raises ValueError on unreadable input."""
    p, w = _number(pool), _number(window_seconds)
    n = int(sharing_contracts)
    if isinstance(sharing_contracts, bool) or n < 1 or p <= 0 or \
            not 0 < w <= DAY_S:
        raise ValueError("range")
    out = []
    for name, period, div in (
            ("shared_program_et_day", DAY_S, D(n)),
            ("per_contract_et_day", DAY_S, D(1)),
            ("per_contract_window", w, D(1))):
        out.append({"hypothesis": name, "period_seconds": period,
                    "allocation": D(1) / div,
                    # while the contract trades: its pool rate
                    "usd_per_hour": p * D(3600) / (period * div),
                    "usd_per_24h": p * DAY_S / (period * div),
                    "pool_evidence": "PUBLISHED", "evidence": "INFERRED"})
    return out


def evaluate(*, pool, period_seconds, remaining_seconds, share_lower,
             qualifying_fraction_lower, allocation_lower,
             accrued_lower, incremental_cost_upper, payout_floor="1",
             scope_verified=False, probe_hypothesis=None,
             paid_fraction_lower="1"):
    """Conditional conservative scenario, never a confirmed payout.

    Allocation is a verified lower bound on the market's fraction of a
    shared pool, not an assumed equal split. Share and qualification must
    be independently estimated over the remaining period, not a snapshot.
    Costs include incremental inventory loss and execution costs.

    `scope_verified=True` means the allocation is confirmed by PAID earnings
    rows. Without it a reward is credited only under an explicitly named
    `probe_hypothesis` (one of HYPOTHESES) and is then labelled INFERRED.
    `paid_fraction_lower` scales the credit by the share of resolved reward
    value the venue actually paid (PAID / (PAID + SKIPPED)).
    """
    refused = {"eligible": False, "incremental_reward": D(0),
               "net_lower": None, "label": LABEL, "evidence": "PUBLISHED",
               "credit_basis": None}
    if scope_verified is True:
        evidence, basis = "PAID", "paid_rows"
    elif isinstance(probe_hypothesis, str) and \
            probe_hypothesis in HYPOTHESES:
        evidence, basis = "INFERRED", "probe_hypothesis:" + probe_hypothesis
    else:
        return dict(refused, reason="unknown_pool_scope")
    try:
        p,t,r,s,q,a,c,k,f,pf = map(_number, (
            pool, period_seconds, remaining_seconds, share_lower,
            qualifying_fraction_lower, allocation_lower, accrued_lower,
            incremental_cost_upper, payout_floor, paid_fraction_lower))
        if not (p > 0 and t > 0 and 0 < r <= t and
                all(0 <= x <= 1 for x in (s,q,a,pf)) and
                0 <= c <= p*a and k >= 0 and f >= 1):
            raise ValueError("range")
        projected = min(p*a-c, p*a*r/t*s*q)
        # Do not credit previously accrued rewards as new earnings.
        # If the floor is not reached, count no incremental reward.
        reward = projected*pf if c+projected >= f else D(0)
        net = reward-k
        return {"eligible": reward > 0 and net > 0,
                "incremental_reward": reward, "net_lower": net,
                "reason": "positive_scenario" if reward > 0 and net > 0
                else "floor_or_cost", "label": LABEL,
                "evidence": evidence, "credit_basis": basis,
                "projected_before_floor": projected}
    except (ValueError, TypeError, InvalidOperation, ArithmeticError):
        return dict(refused, reason="invalid_inputs")


def evaluate_row(*, pool, window_seconds, sharing_contracts, hypothesis,
                 remaining_seconds, share_lower, qualifying_fraction_lower,
                 accrued_lower, incremental_cost_upper, payout_floor="1",
                 paid_verified_hypothesis=None, paid_fraction_lower="1"):
    """`evaluate` for one published programme row under ONE allocation
    hypothesis. `hypothesis` None (or unknown) is the fail-closed default:
    no credit. `paid_verified_hypothesis` is the basis PAID rows confirmed,
    if any; only that one is reported as PAID."""
    refused = {"eligible": False, "incremental_reward": D(0),
               "net_lower": None, "label": LABEL, "evidence": "PUBLISHED",
               "credit_basis": None, "hypothesis": hypothesis}
    if hypothesis not in HYPOTHESES:
        return dict(refused, reason="unknown_pool_scope")
    try:
        sc = {x["hypothesis"]: x for x in allocation_scenarios(
            pool=pool, window_seconds=window_seconds,
            sharing_contracts=sharing_contracts)}[hypothesis]
        rem = min(_number(remaining_seconds), sc["period_seconds"])
    except (ValueError, TypeError, InvalidOperation, ArithmeticError):
        return dict(refused, reason="invalid_inputs")
    out = evaluate(
        pool=pool, period_seconds=sc["period_seconds"],
        remaining_seconds=rem, share_lower=share_lower,
        qualifying_fraction_lower=qualifying_fraction_lower,
        allocation_lower=sc["allocation"], accrued_lower=accrued_lower,
        incremental_cost_upper=incremental_cost_upper,
        payout_floor=payout_floor,
        scope_verified=paid_verified_hypothesis == hypothesis,
        probe_hypothesis=hypothesis,
        paid_fraction_lower=paid_fraction_lower)
    out["hypothesis"] = hypothesis
    return out


def conservative(results):
    """Combine per-hypothesis results into the figure a decision may rest
    on: eligible only if EVERY hypothesis is; reward and net are the
    smallest. An empty or refused set is not eligible."""
    rs = [r for r in (results or []) if isinstance(r, dict)]
    if not rs or any(r.get("net_lower") is None for r in rs):
        return {"eligible": False, "incremental_reward": D(0),
                "net_lower": None, "label": LABEL,
                "evidence": "PUBLISHED", "reason": "unknown_pool_scope",
                "hypotheses": [r.get("hypothesis") for r in rs]}
    worst = min(rs, key=lambda r: r["net_lower"])
    paid = all(r.get("evidence") == "PAID" for r in rs)
    return {"eligible": all(r.get("eligible") for r in rs),
            "incremental_reward": min(r["incremental_reward"] for r in rs),
            "net_lower": worst["net_lower"], "label": LABEL,
            "evidence": "PAID" if paid else "INFERRED",
            "reason": worst.get("reason"),
            "binding_hypothesis": worst.get("hypothesis"),
            "hypotheses": [r.get("hypothesis") for r in rs]}
