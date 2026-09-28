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


#: NON-MONETARY OPERATIONAL CONTROLS ONLY (OWNER 2026-09-28).
#:
#: These carry NO monetary authority and must never grow any. They are separated
#: from `probe_limits()` because that function's dollars became a second sizing
#: authority on the Rung-A path: it reported a $5 per-market ceiling out of the
#: probe's own observe run while the approved envelope was $1.00.
#:
#: `max_live_orders` is a CONCURRENCY bound, not a dollar bound. Replaying the
#: preregistered maker lifecycle over 12 clean authenticated windows, 143 of 217
#: maker intents were lost to a one-live-order count and ZERO to money; removing
#: the count let 19 orders rest simultaneously for $4.9983, still inside the
#: then-$5 dollar bound. So the dollar engine is the hard boundary and the count
#: never is -- which is precisely why the count may live here and dollars may not.
#:
#: `band` is a behavioural price preference, not account protection. The faithful
#: engine imposes none.
OPERATIONAL_MAX_LIVE_ORDERS = 8
OPERATIONAL_BAND = None


#: THE OBSERVE COUNTERFACTUAL ENVELOPE. Not authority -- see
#: l5_live_execution.SafetyLimits.measurement_only and
#: refuse_measurement_authority, which make a POST-capable path fail closed on it.
#:
#: These are the values the OBSERVE diagnostic has always used, kept so the
#: counterfactual stays comparable with every previous observe run. They are
#: DELIBERATELY not sourced from `probe_limits()`: that function is the legacy
#: monetary authority being retired, and a measurement envelope must not be one
#: import away from the thing it is not.
MEASUREMENT_ENVELOPE: Dict[str, Any] = {
    "max_usd_per_market": D("5"),
    "max_total_exposure_usd": D("5"),
    "max_abs_residual": D("10"),
    "max_concurrent_markets": 1,
    "daily_realized_loss_halt_usd": D("5"),
}


def measurement_envelope() -> Dict[str, Any]:
    """The OBSERVE counterfactual bounds plus the operational controls."""
    return {**MEASUREMENT_ENVELOPE, **operational_controls()}


def operational_controls() -> Dict[str, Any]:
    """The non-monetary controls, and nothing that costs money."""
    return {"max_live_orders": OPERATIONAL_MAX_LIVE_ORDERS,
            "band": OPERATIONAL_BAND}


#: LEGACY. Carries hardcoded dollars and is NOT on the Rung-A path: the one-shot
#: entrypoint no longer calls it, and a structural test fails if it does. Retained
#: for the dry-run, realtime-probe, preview and manifest tooling that predates the
#: mandate. Do not reintroduce it into the money path -- derive from the mandate
#: with SafetyLimits.from_mandate instead.
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
