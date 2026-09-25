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


def probe_limits() -> SafetyLimits:
    """The authorised envelope. Every bound stated, none left to a default."""
    return SafetyLimits(
        max_usd_per_market=D("5"),
        max_total_exposure_usd=D("5"),
        max_abs_residual=D("10"),
        max_concurrent_markets=1,
        # EIGHT, not one, and this is a CONCURRENCY change rather than any
        # additional risk. Replaying the preregistered maker lifecycle over
        # 12 clean authenticated windows: under one-live-order, 143 of 217
        # maker intents were lost to the COUNT and ZERO to money, and
        # concurrent premium never exceeded $4.4198. Remove the count and 19
        # orders rest simultaneously for $4.9983 -- still inside $5.
        #
        # So the dollar engine, not the order count, is the hard boundary.
        # Eight is deliberately below the 19 the replay reached, so the
        # aggregate risk bound is what refuses an order and the count never
        # becomes the reason. Every monetary limit below is UNCHANGED.
        max_live_orders=8,
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
        "labels": ("DISARMED. These are account-protection bounds only and "
                   "say nothing about L5 behaviour or PMUS capability."),
    }
