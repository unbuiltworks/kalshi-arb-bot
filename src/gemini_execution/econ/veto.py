"""THE HARD ECONOMIC COMPLETION VETO.

WHY THIS EXISTS, STATED AS THE EVIDENCE THAT FORCED IT. On 2026-09-20 at
11:44:33Z the deployed BTC15m arm completed a pair and reported

    pairs=1  basis=1.13  fee+=0

A matched pair pays exactly $1.00 at settlement: one leg settles at $1.00 and
the other at $0.00, whichever way the market goes. Paying 1.13 for that dollar
is a guaranteed loss of 13 cents per contract BEFORE fees, and the lane counted
it as a successful completion. It is not a queue defect and it is not bad luck.
It is the absence of this module.

WHAT WAS ACTUALLY MISSING. `Rung.max_acceptable_price` in
`src/l5_surface/engine.py` reads like an economic ceiling and is not one:

    px = bid - (offset + shift) * tick

That is a GEOMETRIC offset from the touch. It never consults the cost of the
leg already held, so it cannot express "I hold Up at 0.63, therefore Down is
worth at most 0.36 to me". Fee arithmetic existed (`ab_arms._fee`) but was
applied in REPORTING, after the pair had already been formed. Admission is the
only place a veto can work.

THE ARITHMETIC, SOLVED RATHER THAN SEARCHED. Holding `q` of one outcome at
average cost `c_held`, acquiring the complement at price `c`, both legs maker:

    net per pair = 1 - c_held - c - fee(c_held) - fee(c)
    fee(p)       = rate * p * (1 - p)          per contract, the venue formula

Requiring `net >= min_edge + slippage` and writing
`B = 1 - c_held - fee(c_held) - min_edge - slippage` for the budget left for
the complement leg INCLUSIVE of its own fee:

    c + rate*c*(1-c) <= B
    rate*c^2 - (1+rate)*c + B >= 0

which is an upward parabola in `c` with `f(0) = B`. So for `B >= 0` the
admissible region is `c <= ` the SMALLER root:

    ceiling = [ (1+rate) - sqrt((1+rate)^2 - 4*rate*B) ] / (2*rate)

Closed form, no search, no tolerance to tune. `B < 0` means the held leg is
already so expensive that NO complement price makes the pair whole -- the
honest answer there is that completion is impossible, not that it is expensive,
and this module says so rather than returning a price near zero.

REBATES ARE NOT IN THIS ARITHMETIC AND THAT IS DELIBERATE. `REBATE_APPLICABILITY_1.md`
establishes that the published maker rebate rounds DOWN to the cent at daily
payment, so at our probe size (0.01 contracts) a per-fill accrual is $0.00 --
the rebate cannot rescue a negative pair here BY ARITHMETIC, not by estimate.
More generally a projected rebate is not money: the ladder is
historical -> shadow -> submitted -> filled -> settled -> credited -> realized,
and only `credited` is a venue record. `pair_economics` therefore reports any
accrual in its own field, computed by `rebate_program`, and `admits` never
reads it. A test asserts that a pair which is negative on trading economics
stays REFUSED no matter how large an accrual is passed in.

FLOORED TO THE TICK, ALWAYS DOWNWARD. A ceiling rounded UP to a tick is not a
ceiling. `tick_floor` truncates toward zero so a quoted price can never exceed
the computed bound by a fraction of a tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from decimal import InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, Mapping, Optional

from src.gemini_execution import rebate_program as RP

SCHEMA = "gemini_econ_completion_veto/v1"

#: the accounting ladder. Only `credited` is a venue record; everything to its
#: left is a projection and is labelled as one wherever it is reported.
LADDER = ("historical", "shadow", "submitted", "filled", "settled",
          "credited", "realized")

LABELS = (
    "A matched pair pays exactly $1.00 at settlement. A basis above $1.00 is a "
    "loss before fees and is never a successful completion.",
    "Rebates are EXCLUDED from every admission test in this module. Any "
    "accrual reported beside a decision is ACCRUED FROM PUBLISHED RULES, not "
    "credited and not realized.",
)


@dataclass(frozen=True)
class FeeRegime:
    """The fee rates an admission test is allowed to assume.

    Carried as a value rather than read from a constant at the point of use, so
    a decision record can state WHICH regime produced it and a later regime
    change cannot silently re-price historical evidence.
    """

    maker_rate: D
    taker_rate: D
    source: str
    read_utc: str

    def rate_for(self, liquidity: str) -> D:
        if liquidity == "MAKER":
            return self.maker_rate
        if liquidity == "TAKER":
            return self.taker_rate
        raise ValueError("liquidity must be MAKER or TAKER, got %r" % liquidity)

    def report(self) -> Dict[str, Any]:
        return {"maker_rate": str(self.maker_rate),
                "taker_rate": str(self.taker_rate),
                "source": self.source, "read_utc": self.read_utc}


#: the published schedule effective 2026-03-18, taken from the module that
#: already reconciles it against seven venue-confirmed credits rather than
#: restated here as a fresh literal.
VERIFIED_REGIME = FeeRegime(
    maker_rate=RP.MAKER_RATE,
    taker_rate=RP.TAKER_RATE,
    source=("src.gemini_execution.rebate_program, reconciled in "
            "REBATE_ECONOMICS_1.md against POST /v1/transfers credits and "
            "POST /v1/tradevolume maker notional"),
    read_utc="2026-09-19",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def leg_fee(price: Any, qty: Any, *, rate: D) -> Optional[D]:
    """One leg's fee under the venue formula. EXACT -- never pre-rounded.

    Rounding is the venue's, applied at its own boundary; rounding here would
    accumulate a per-leg error into the very comparison this module exists to
    make.
    """
    p, q = _dec(price), _dec(qty)
    if p is None or q is None:
        return None
    return rate * q * p * (D("1") - p)


def tick_floor(price: D, tick: D) -> D:
    """Largest multiple of `tick` not exceeding `price`. A ceiling only ever
    rounds DOWN, or it is not a ceiling."""
    if tick <= 0:
        return price
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def max_complement_price(*, held_avg_cost: Any, min_edge: Any,
                         regime: FeeRegime = VERIFIED_REGIME,
                         complement_liquidity: str = "MAKER",
                         held_liquidity: str = "MAKER",
                         slippage: Any = "0",
                         tick: Any = "0.01") -> Dict[str, Any]:
    """The most we may pay for the complement and still clear `min_edge`.

    Returns a refusal rather than a number whenever no such price exists, and
    names which input made it impossible.
    """
    c_held = _dec(held_avg_cost)
    edge = _dec(min_edge)
    slip = _dec(slippage)
    tk = _dec(tick) or D("0.01")
    out: Dict[str, Any] = {
        "schema": SCHEMA, "regime": regime.report(),
        "held_avg_cost": None if c_held is None else str(c_held),
        "min_edge": None if edge is None else str(edge),
        "slippage": None if slip is None else str(slip),
        "complement_liquidity": complement_liquidity,
        "rebate_excluded": True,
    }
    if c_held is None or edge is None or slip is None:
        out.update(ok=False, ceiling=None,
                   why="UNREADABLE input: held_avg_cost/min_edge/slippage")
        return out
    if not (D("0") < c_held < D("1")):
        out.update(ok=False, ceiling=None,
                   why="held_avg_cost %s is not a price in (0,1)" % c_held)
        return out

    held_rate = regime.rate_for(held_liquidity)
    rate = regime.rate_for(complement_liquidity)
    fee_held = held_rate * c_held * (D("1") - c_held)
    budget = D("1") - c_held - fee_held - edge - slip
    out["fee_held_per_contract"] = str(fee_held)
    out["budget_for_complement_leg"] = str(budget)

    if budget <= 0:
        out.update(ok=False, ceiling=None,
                   why=("NO complement price can make this pair whole: the "
                        "held leg at %s plus its fee already consumes the "
                        "$1.00 settlement at the required edge %s"
                        % (c_held, edge)))
        return out

    one_plus = D("1") + rate
    disc = one_plus * one_plus - D("4") * rate * budget
    if disc < 0:                                    # unreachable for sane rates
        out.update(ok=False, ceiling=None,
                   why="no real root: discriminant %s < 0" % disc)
        return out
    raw = (one_plus - disc.sqrt()) / (D("2") * rate)
    ceiling = tick_floor(raw, tk)
    if ceiling <= 0:
        out.update(ok=False, ceiling=None, raw_ceiling=str(raw),
                   why=("the exact ceiling %s is below one tick (%s), so no "
                        "quotable price is economic" % (raw, tk)))
        return out
    out.update(ok=True, ceiling=str(ceiling), raw_ceiling=str(raw), why=None)
    return out


def pair_economics(*, held_qty: Any, held_avg_cost: Any,
                   complement_qty: Any, complement_price: Any,
                   regime: FeeRegime = VERIFIED_REGIME,
                   held_liquidity: str = "MAKER",
                   complement_liquidity: str = "MAKER",
                   credited_rebate: Any = None) -> Dict[str, Any]:
    """Full economics of a pair, with every component reported APART.

    `matched` is the quantity actually paired; anything beyond it is residual
    and is NEVER folded into pair economics -- a residual leg is a directional
    position whose outcome is unknown, and averaging it into a matched pair
    would report a guaranteed number and an unknown one as one figure.
    """
    qh, ch = _dec(held_qty), _dec(held_avg_cost)
    qc, cc = _dec(complement_qty), _dec(complement_price)
    out: Dict[str, Any] = {"schema": SCHEMA, "regime": regime.report(),
                           "labels": list(LABELS)}
    if None in (qh, ch, qc, cc):
        out.update(readable=False,
                   why="UNREADABLE input; economics not computed")
        return out

    matched = min(qh, qc)
    out["matched_qty"] = str(matched)
    out["residual_qty"] = str(abs(qh - qc))
    out["residual_side"] = ("HELD" if qh > qc else
                            ("COMPLEMENT" if qc > qh else None))
    out["pair_basis_before_fees"] = str(ch + cc)

    fee_h = leg_fee(ch, matched, rate=regime.rate_for(held_liquidity))
    fee_c = leg_fee(cc, matched, rate=regime.rate_for(complement_liquidity))
    fees = (fee_h or D("0")) + (fee_c or D("0"))
    gross = matched * (D("1") - ch - cc)
    out["fee_held"] = str(fee_h)
    out["fee_complement"] = str(fee_c)
    out["fees_total"] = str(fees)
    out["trading_pnl_before_fees"] = str(gross)
    out["net_pair_economics"] = str(gross - fees)
    out["fee_positive"] = bool(gross - fees > 0)
    out["basis_at_or_below_one"] = bool(ch + cc <= D("1"))

    # accrued from published rules, reported in its own field, NEVER added to
    # `net_pair_economics` and never consulted by `admits`.
    acc_h = RP.accrual(matched, ch) if complement_liquidity else None
    acc_c = RP.accrual(matched, cc)
    out["rebate_accrued_held"] = acc_h
    out["rebate_accrued_complement"] = acc_c
    out["rebate_credited"] = (None if credited_rebate is None
                              else str(_dec(credited_rebate)))
    out["rebate_note"] = RP.ACCRUAL_LABEL
    out["accounting_ladder"] = list(LADDER)
    out["readable"] = True
    return out


def admits(*, held_avg_cost: Any, proposed_price: Any, min_edge: Any,
           regime: FeeRegime = VERIFIED_REGIME,
           complement_liquidity: str = "MAKER",
           held_liquidity: str = "MAKER",
           slippage: Any = "0", tick: Any = "0.01",
           accrued_rebate: Any = None) -> Dict[str, Any]:
    """THE VETO. May we pay `proposed_price` for the complement?

    `accrued_rebate` is accepted so a caller cannot claim it was never offered
    the chance to include it, and is then IGNORED -- it appears in the record
    and never in the test. That asymmetry is the whole point of the parameter.
    """
    ceil = max_complement_price(
        held_avg_cost=held_avg_cost, min_edge=min_edge, regime=regime,
        complement_liquidity=complement_liquidity,
        held_liquidity=held_liquidity, slippage=slippage, tick=tick)
    px = _dec(proposed_price)
    rec: Dict[str, Any] = {"schema": SCHEMA, "ceiling": ceil,
                           "proposed_price": None if px is None else str(px),
                           "rebate_seen_and_ignored": (
                               None if accrued_rebate is None
                               else str(_dec(accrued_rebate))),
                           "labels": list(LABELS)}
    if px is None:
        rec.update(admitted=False, reason="UNREADABLE proposed_price")
        return rec
    if not ceil.get("ok"):
        rec.update(admitted=False,
                   reason="NO_ECONOMIC_CEILING: %s" % ceil.get("why"))
        return rec
    cap = D(ceil["ceiling"])
    if px > cap:
        rec.update(admitted=False,
                   reason=("PRICE_ABOVE_CEILING: %s > %s; completing here "
                           "would form a pair with negative expected net "
                           "economics" % (px, cap)))
        return rec
    rec.update(admitted=True, reason=None, headroom=str(cap - px))
    return rec


def tick_ceil(price: D, tick: D) -> D:
    """Smallest multiple of `tick` not below `price`. A FLOOR on an acceptable
    sale price only ever rounds UP, for the same reason a ceiling rounds
    down."""
    if tick <= 0:
        return price
    q = (price / tick).to_integral_value(rounding=ROUND_CEILING)
    return q * tick


def min_exit_price(*, held_avg_cost: Any, min_edge: Any,
                   regime: FeeRegime = VERIFIED_REGIME,
                   exit_liquidity: str = "MAKER",
                   slippage: Any = "0",
                   tick: Any = "0.01") -> Dict[str, Any]:
    """The least we may accept to SELL the held leg and still clear `min_edge`.

    THIS IS NOT THE MIRROR OF `max_complement_price` AND MUST NOT BE DERIVED
    FROM IT. Completing a pair and round-tripping out of the held leg are the
    same economics ONLY while the venue's complement identity holds
    (`no_bid == 1 - yes_ask`). `metadata.outcome_book` CHECKS that identity on
    every read rather than assuming it, precisely because a book that violates
    it makes the two paths pay differently -- so each path is priced from its
    own quoted side here.

    `min_edge` may be NEGATIVE. A bounded rescue exit is a decision to take a
    known loss rather than carry an unknown one, and expressing that as a
    negative required edge keeps it inside the same arithmetic instead of
    bypassing the test.

    Selling `q` at `s` having paid `c`:

        net = s - c - rate*s*(1-s)   >=  min_edge + slippage
        =>  rate*s^2 + (1-rate)*s - (c + min_edge + slippage)  >=  0

    an upward parabola whose value at s=0 is negative, so the admissible region
    is `s >=` the LARGER root.
    """
    c = _dec(held_avg_cost)
    edge = _dec(min_edge)
    slip = _dec(slippage)
    tk = _dec(tick) or D("0.01")
    out: Dict[str, Any] = {
        "schema": SCHEMA, "regime": regime.report(),
        "held_avg_cost": None if c is None else str(c),
        "min_edge": None if edge is None else str(edge),
        "exit_liquidity": exit_liquidity,
        "rebate_excluded": True,
    }
    if c is None or edge is None or slip is None:
        out.update(ok=False, floor=None,
                   why="UNREADABLE input: held_avg_cost/min_edge/slippage")
        return out
    rate = regime.rate_for(exit_liquidity)
    target = c + edge + slip
    one_minus = D("1") - rate
    disc = one_minus * one_minus + D("4") * rate * target
    if disc < 0:
        out.update(ok=False, floor=None,
                   why="no real root: discriminant %s < 0" % disc)
        return out
    raw = (-one_minus + disc.sqrt()) / (D("2") * rate)
    floor_px = tick_ceil(raw, tk)
    if floor_px >= D("1"):
        out.update(ok=False, floor=None, raw_floor=str(raw),
                   why=("the exact floor %s is at or above $1.00, so no "
                        "quotable sale price clears the required edge %s"
                        % (raw, edge)))
        return out
    out.update(ok=True, floor=str(floor_px), raw_floor=str(raw), why=None)
    return out


def roundtrip_economics(*, qty: Any, held_avg_cost: Any, exit_price: Any,
                        regime: FeeRegime = VERIFIED_REGIME,
                        held_liquidity: str = "MAKER",
                        exit_liquidity: str = "MAKER") -> Dict[str, Any]:
    """Economics of going FLAT on the held leg rather than pairing it.

    Reported in its own shape because a round trip is NOT a matched pair: it
    has no settlement leg, so it carries no settlement cashflow and no residual.
    Folding it into pair economics would put a realized-on-exit number and a
    settles-at-$1.00 number in the same column.
    """
    q, c, s = _dec(qty), _dec(held_avg_cost), _dec(exit_price)
    out: Dict[str, Any] = {"schema": SCHEMA, "regime": regime.report(),
                           "kind": "ROUNDTRIP", "labels": list(LABELS)}
    if None in (q, c, s):
        out.update(readable=False, why="UNREADABLE input")
        return out
    fee_in = leg_fee(c, q, rate=regime.rate_for(held_liquidity)) or D("0")
    fee_out = leg_fee(s, q, rate=regime.rate_for(exit_liquidity)) or D("0")
    gross = q * (s - c)
    out.update(readable=True, qty=str(q), entry_price=str(c),
               exit_price=str(s),
               trading_pnl_before_fees=str(gross),
               fee_entry=str(fee_in), fee_exit=str(fee_out),
               fees_total=str(fee_in + fee_out),
               net_roundtrip=str(gross - fee_in - fee_out),
               fee_positive=bool(gross - fee_in - fee_out > 0),
               settlement_cashflow="0 -- a round trip never reaches settlement",
               residual_qty="0",
               rebate_accrued_exit=RP.accrual(q, s),
               rebate_note=RP.ACCRUAL_LABEL,
               accounting_ladder=list(LADDER))
    return out
