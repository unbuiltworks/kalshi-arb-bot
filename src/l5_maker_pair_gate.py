"""A SEPARATE pair-cost ceiling for completions that are genuinely maker.

WHY 0.96 IS THE WRONG NUMBER FOR A MAKER, AND WHY THAT IS NOT AN OPINION.

`MAKER_COMPLETE_CEILING = 0.96` is a TAKER-ERA bound. A taker pays
`theta_taker * C * p * (1-p)` on BOTH legs, and the live gates add a
conservative `$0.01`-per-order allowance on top; at the deployed $0.80
opening notional that round trip costs about 4 cents per contract, which is
exactly the margin 0.96 reserves.

A maker pays NO fee. The venue's published table has two columns -- what a
100-lot taker PAYS and what a 100-lot maker RECEIVES
(`analysis/pmus/pmus_published_fee_schedule.json`, all 99 rows reproduced in
`tests/test_pmus_fees.py`). There is no maker charge to reserve margin for,
so the break-even pair cost for a maker-both-legs completion is EXACTLY
1.0000, and 0.96 leaves four cents of margin that nothing consumes.

Measured against L5's own 66,581 matched-pair observations, that unspent
margin is not free: 0.96 admits 24.6% of his pairs, 0.99 admits 52.5%. The
band (0.96, 0.99] is 28.0% of everything he does.

WHAT THIS GATE DOES NOT RELY ON: THE REBATE.

The maker rebate is real, is NOT volume-tiered (unlike the taker rebate,
whose first tier is $250,000 of prior-month notional and which this account
has never reached), and has actually been credited to this account -- 25
prints, $0.42. But the venue credits it PER PRINT with banker's rounding to
the cent, and our own history records the floor biting: "earlier cohorts
(< 1.62 shares per print) were credited 0.00"
(`src/l5_profitability.py:observed_rebate_rate`).

At the deployed $0.80 opening notional and 2-contract leg cap, the credit is
$0.00 at every price except a narrow p in [0.30, 0.45] band. So this gate is
justified with the rebate valued at ZERO, and `CEILING` is set strictly
below the no-rebate break-even of 1.0000 rather than at the higher figure a
rebate would permit. An uncredited rebate is never margin.

WHAT THE CEILING IS ACTUALLY FOR.

It bounds what a completion MAY pay; it does not instruct the engine to pay
it. The engine completes at the best price the book offers. So the band
between 0.96 and this ceiling is reached only where 0.96 was never available
-- which is precisely the case where the alternative is STRANDING the first
leg. Stranding is not free: adverse selection on filled opening legs is
measured at -$0.0827 per contract over 956 queue-aware modelled fills, with
91.63% of fills adverse (`analysis/l5/ADVERSE_SELECTION_MEASUREMENT_
20261024.md`). A leg completed at 0.99 earns +$0.0100; the same leg stranded
is worth -$0.0827. The swing is $0.0927 per contract.

That is the same marginal argument `l5_defensive_completion` already makes
for its 1.20 unwind bound ("exiting is the bounded choice whenever
P < 1.00 + first_leg_cost"). This gate is the NORMAL-completion analogue,
and unlike that one it never pays above par.

SAFETY. This gate WIDENS a bound, so it is off unless explicitly enabled AND
the completion is genuinely post-only maker on both legs. A TAKER pair at
0.99 is net-NEGATIVE, so `effective_ceiling` refuses to return this ceiling
for anything that could cross.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

#: the deployed taker-era bound, unchanged and still the default everywhere.
TAKER_ERA_CEILING = D("0.96")

#: the proposed maker-only bound. Strictly below the 1.0000 no-rebate
#: break-even, so a completed pair is locked profit with the rebate at zero.
CEILING = D("0.99")

#: break-even pair cost for a maker-both-legs completion, rebate at ZERO.
NO_REBATE_BREAK_EVEN = D("1.00")

#: measured adverse selection on a filled opening leg that is NOT completed,
#: per contract. 956 queue-aware modelled fills, 91.63% adverse.
#: EVIDENCE GRADE: modelled-fill, not venue-confirmed (only 6 of those fills
#: were venue-confirmed). It is used here to ARGUE for completion, never to
#: book income.
STRAND_ADVERSE_SELECTION_PER_CONTRACT = D("0.0827")

#: THE FEATURE FLAG. Absent, empty, misspelled or unreadable is OFF, exactly
#: as `L5_DEFENSIVE_COMPLETION`. This gate is NOT authorized for live
#: trading; it ships disabled so it can be shadow-measured with live
#: behaviour byte-for-byte unchanged.
ENABLED_ENV = "L5_MAKER_PAIR_GATE"
_TRUE = ("1", "yes", "true", "on")

REFUSE_DISABLED = "MAKER_PAIR_GATE_DISABLED"
REFUSE_NOT_MAKER = "MAKER_PAIR_GATE_REQUIRES_POST_ONLY_BOTH_LEGS"
REFUSE_ABOVE_CEILING = "PAIR_ABOVE_MAKER_CEILING"
REFUSE_AT_OR_ABOVE_PAR = "PAIR_AT_OR_ABOVE_PAR"


def enabled() -> bool:
    try:
        return (os.getenv(ENABLED_ENV) or "").strip().lower() in _TRUE
    except BaseException:                                   # noqa: BLE001
        return False


def effective_ceiling(*, maker_both_legs: bool,
                      gate_enabled: Optional[bool] = None) -> D:
    """The ceiling a completion may use. NEVER above 0.96 unless the gate is
    explicitly enabled AND both legs are genuinely post-only maker.

    `gate_enabled=None` reads the environment; the parameter exists so tests
    and replays can be explicit without touching process state.
    """
    on = enabled() if gate_enabled is None else bool(gate_enabled)
    if on and maker_both_legs:
        return CEILING
    return TAKER_ERA_CEILING


def decide(*, first_leg_own_cost: Any, complement_own_cost: Any,
           maker_both_legs: bool,
           gate_enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Refuse-only completion decision. Returns the ceiling it applied and,
    where it refuses, the reason -- it never returns a price or a size.

    The rebate is reported as an ENTITLEMENT at the schedule, separately, and
    is NOT added to `locked_pnl_per_contract`.
    """
    a = _d(first_leg_own_cost)
    b = _d(complement_own_cost)
    on = enabled() if gate_enabled is None else bool(gate_enabled)
    ceil = effective_ceiling(maker_both_legs=maker_both_legs,
                             gate_enabled=on)
    out: Dict[str, Any] = {
        "gate": "L5_MAKER_PAIR_GATE",
        "gate_enabled": on,
        "maker_both_legs": bool(maker_both_legs),
        "ceiling_applied": str(ceil),
        "taker_era_ceiling": str(TAKER_ERA_CEILING),
        "rebate_valued_at": "0.00",
        "rebate_basis": ("schedule entitlement only; the per-print cent "
                         "floor credits 0.00 below ~1.62 shares and the "
                         "deployed caps sit under that at most prices"),
        "allowed": False,
        "refused_reason": None,
    }
    if a is None or b is None:
        out["refused_reason"] = "UNREADABLE_COSTS"
        return out
    pair = a + b
    out["pair"] = str(pair)
    out["locked_pnl_per_contract"] = str(D(1) - pair)
    # par is an absolute bound: this gate never pays at or above $1.00, which
    # is what separates it from the defensive unwind ceiling.
    if pair >= NO_REBATE_BREAK_EVEN:
        out["refused_reason"] = REFUSE_AT_OR_ABOVE_PAR
        return out
    if pair > ceil:
        out["refused_reason"] = (
            REFUSE_ABOVE_CEILING if (on and maker_both_legs)
            else (REFUSE_NOT_MAKER if on and not maker_both_legs
                  else REFUSE_DISABLED))
        out["would_allow_if_enabled"] = bool(
            maker_both_legs and pair <= CEILING)
        return out
    out["allowed"] = True
    return out


def strand_vs_complete(*, pair: Any) -> Dict[str, Any]:
    """The marginal comparison that justifies the band, per contract.

    Completing at `pair` locks (1 - pair). NOT completing leaves the leg to
    settle, measured at -$0.0827/contract. Reported for the record; the gate
    does not consult it, because it never pays at or above par regardless.
    """
    p = _d(pair)
    if p is None:
        return {"readable": False}
    comp = D(1) - p
    strand = -STRAND_ADVERSE_SELECTION_PER_CONTRACT
    return {
        "readable": True,
        "pair": str(p),
        "complete_per_contract": str(comp),
        "strand_per_contract": str(strand),
        "advantage_of_completing": str(comp - strand),
        "indifference_pair": str(
            D(1) + STRAND_ADVERSE_SELECTION_PER_CONTRACT),
        "evidence_grade": "MODELLED_FILL_NOT_VENUE_CONFIRMED",
    }


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except BaseException:                                   # noqa: BLE001
        return None


def provenance() -> Dict[str, Any]:
    return {
        "taker_era_ceiling": str(TAKER_ERA_CEILING),
        "maker_ceiling": str(CEILING),
        "no_rebate_break_even": str(NO_REBATE_BREAK_EVEN),
        "enabled_env": ENABLED_ENV,
        "enabled_now": enabled(),
        "authorized_for_live": False,
        "rebate_valued_at": "0.00",
        "maker_pays_fee": False,
        "l5_pair_share_at_0_96": "24.6%",
        "l5_pair_share_at_0_99": "52.5%",
        "l5_pair_share_in_band": "28.0%",
        "strand_adverse_selection_per_contract": str(
            STRAND_ADVERSE_SELECTION_PER_CONTRACT),
    }
