"""The bounded probe envelope, in one place, with its worst case computed.

WHY THIS MODULE EXISTS. `SafetyLimits` defaults to max_total_exposure_usd
$10, so a probe built on the defaults would carry a worst case of $10 while
every document around it said $5.1738. The envelope the owner authorised is
therefore pinned HERE, explicitly, and the worst case is derived from the
venue's own fee implementation rather than restated as a number someone once
computed.

    principal   $5.00   every share can settle worthless, so the committed
                        cap IS the money at risk; this venue has no leverage
    taker fee   $0.1738 the venue formula is maximised at p = 0.5, where $5
                        buys 10 contracts
    -------------------
    worst case  $5.1738

Nothing here can place an order. It states bounds; `l5_live_execution`
enforces them and `l5_pmus_transport` refuses to transmit until the owner
arms it.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict

from src import pmus_fees as fees
from src.l5_live_execution import SafetyLimits

#: the market class the probe is scoped to; one market, one at a time
SERIES = "btc-updown-15m"

#: the frozen L5-relative sizing normalization: 1/median(l5_size), so the
#: median L5 action of 22.13 shares becomes 1.00 share. Never tuned.
LAMBDA = D("0.04518752824220514525")

#: the fee coefficient is maximised at this price
FEE_MAX_PRICE = D("0.5")

#: PRODUCTION'S OWN MECHANISM, read here rather than kept as a second number.
MAX_LIVE_ORDERS_ENV = "L5_MAX_LIVE_ORDERS"


def max_live_orders() -> int:
    """How many orders may rest at once -- ONE, unless a deployment says more.

    TWO DEFAULTS FOR ONE ACCOUNT IS NOT A POLICY. Probe 07 raised this
    envelope's count to 8 on replay evidence: 19 orders rested
    simultaneously for $4.9983, inside $5, so the dollar engine and not the
    count was the boundary. The CONTINUOUS runner was later tightened the
    other way -- `l5_pmus_live_runner.max_live_orders()` defaults to 1,
    because the bounded pilot's "one outstanding order" restriction was
    otherwise unenforceable at all: at a 3c quote eight clips rest for under
    a dollar. Both were right about their own question and they disagreed
    about the account, which is how an 8 ended up facing a 1.

    So this envelope now carries the runner's answer, read the runner's way:
    the default is 1, an unreadable value is the tightest one, and Probe 07's
    8 remains available to a deployment that says it out loud. The two
    functions are pinned to each other by test, so they cannot drift again.
    """
    raw = (os.getenv(MAX_LIVE_ORDERS_ENV) or "").strip()
    try:
        n = int(raw) if raw else 1
    except ValueError:
        n = 1                      # an unreadable limit is the tightest one
    return max(1, n)               # zero or negative would refuse everything


def probe_limits() -> SafetyLimits:
    """The authorised envelope. Every bound stated, none left to a default."""
    return SafetyLimits(
        max_usd_per_market=D("5"),
        max_total_exposure_usd=D("5"),
        max_abs_residual=D("10"),
        max_concurrent_markets=1,
        # ONE by default, 8 only when a deployment says so: see
        # `max_live_orders` above for why Probe 07's 8 and the runner's 1
        # both existed, and why the runner's answer wins. Every monetary
        # limit below is UNCHANGED either way -- the count has never been
        # what bounds the money.
        max_live_orders=max_live_orders(),
        daily_realized_loss_halt_usd=D("5"),
        band=None,          # a band is behavioural; the faithful engine has none
    )


def worst_case(limits: SafetyLimits = None) -> Dict[str, Any]:
    """The most this configuration can lose, from the real fee code."""
    lim = limits or probe_limits()
    principal = lim.worst_case_loss_usd()
    contracts = principal / FEE_MAX_PRICE
    fee = fees.taker_fee(FEE_MAX_PRICE, contracts)
    f = lambda d: format(D(str(d)), "f")
    return {
        "principal_usd": f(principal),
        "fee_maximising_price": f(FEE_MAX_PRICE),
        "contracts_at_that_price": f(contracts),
        "max_taker_fee_usd": f(fee),
        "worst_case_total_usd": f(principal + fee),
        "basis": ("principal is the committed cap because a share can settle "
                  "worthless; no leverage exists on this venue. Fee is the "
                  "venue's own taker formula at its maximum."),
    }


def manifest(market: str) -> Dict[str, Any]:
    lim = probe_limits()
    return {
        "market": market,
        "series": SERIES,
        "lambda": str(LAMBDA),
        "limits": lim.as_dict(),
        "worst_case": worst_case(lim),
        "armed": False,
        #: Probe 08 changes no number above. Its one treatment is a REFUSAL
        #: in the overlay -- while the realized residual is non-zero, an
        #: order that would deepen it is declined -- so it can only ever
        #: reduce exposure, never raise it. Named here so the envelope and
        #: the treatment are read from one place.
        "treatment": {
            "name": "IMBALANCE_INCREASE_SUPPRESSED",
            "env": "L5_IMBALANCE_SUPPRESS",
            "default": "OFF (Probe 07 is the control)",
            "permits": ["FLAT_OPEN", "REDUCE_RESIDUAL"],
            "refuses": ["INCREASE_RESIDUAL"],
            "basis": "REALIZED residual only",
            "changes_no_limit": True,
        },
        "labels": ("DISARMED. These are account-protection bounds only and "
                   "say nothing about L5 behaviour or PMUS capability."),
    }
