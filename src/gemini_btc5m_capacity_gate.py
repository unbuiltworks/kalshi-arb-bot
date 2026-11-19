"""CAN THIS VENUE PAY THE TARGET AT ALL? Asked before any strategy is built.

WHY THIS MODULE EXISTS. Every lane this programme has retired -- K<->P taker,
K<->P maker, G<->K sports, PMUS BTC5m -- was retired for the same reason, and
it was never the reason engineering time was spent on it. They were built,
instrumented, shadowed and probed, and then found to be arithmetically incapable
of the target. `ARB_STATUS.md` records the end state: "FINAL CERTIFIED REALISTIC
DEPLOYABLE CAPACITY: $0/day" across seven lanes. `analysis/MONEY_PATH_STATUS.md`
records the same conclusion reached about Gemini on 2026-09-23 -- "Gemini is a
mechanism laboratory. It is not, on current measurement, an income venue" --
after two live probes had already been prepared.

The missing step was never a better strategy. It was a CEILING: the most the
venue could pay even if the strategy were perfect, computed from the venue's own
measured tape BEFORE the strategy is written. That is this module, and it is
deliberately crude in our favour -- every term is the best case:

  * every matched pair captures the FULL touch spread;
  * our fills are assumed to arrive at the measured participation share;
  * the lone-leg drag is whatever the caller measured, not a worst case;
  * no latency, no queue position, no cancellation cost, no outage.

If the CEILING does not clear the target, nothing below it can, and no amount of
repricing, replenishment or selective completion changes that. A gate that only
ever says "no" to a venue that cannot pay is worth more than a strategy that
says "maybe" to one that can't.

THE ONE IDENTITY THAT MATTERS, and it is why the touch spread is the input
rather than the two prices. To be a maker on both sides of a single binary
Up/Down contract we rest

    buy UP   at the bid                 cost = bid
    buy DOWN at the complement of ask   cost = 1 - ask

so a matched pair costs `bid + (1 - ask) = 1 - (ask - bid)` and pays exactly
$1.00 at settlement. Therefore

    GROSS MARGIN OF A MATCHED TWO-SIDED MAKER PAIR == THE TOUCH SPREAD

per contract, with no reference to the level. A 15c-wide book pays 15c a pair
whether it is quoted 0.43/0.58 or 0.05/0.20. The level still matters, but only
through the fee and rebate formulae, which carry P(1-P).

WHAT KILLS SMALL SIZE, and it is not the fee RATE. Gemini rounds the maker fee
UP to the next cent (`gemini_execution/rebate_program.py`, read 2026-09-19):

    Maker Fee = ceil_cent(0.0175 x C x P x (1 - P))

At the venue minimum C = 0.01 and P = 0.43 that is ceil_cent($0.0000429) =
$0.01 -- a full cent of fee on $0.0043 of notional, against a gross margin of
0.01 x 0.15 = $0.0015 for the whole pair. The fee is an order of magnitude
larger than the trade. `min_viable_clip` solves for the smallest clip whose
gross spread capture clears the two-cent-per-pair fee floor, which is the single
most actionable sizing number on this venue and is NOT a tuning preference.

WHAT MAKES THE REBATE $0.00, separately from whether it is generous. The rebate
is rounded DOWN to the next cent and paid daily, so a day's whole accrual must
reach one cent before any cash moves. `rebate_program.py` already did this
arithmetic: at the crypto rate (0.30 x 0.07 = 0.021) the best possible price
P = 0.50 needs 1.905 contracts of daily maker volume to earn the first cent.
This module charges the same round-down against the day, not the fill, so a
programme doing real volume is credited and a probe doing 0.01-lots is not.

NOTHING HERE IS A REBATE, A FILL OR A DOLLAR. It is a ceiling computed from
measurements the caller supplies. Every rate that could manufacture the answer
-- participation share, match rate, lone-leg drag -- is a REQUIRED input with no
default, exactly as `l5_straddle_ev.entry_ev` refuses an unset probability. An
unmeasured ceiling is refused, not estimated.

PURE. No I/O, no venue calls, no order transport, no credentials.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Dict, Optional

D = Decimal
ZERO = D("0")
ONE = D("1")
CENT = D("0.01")

SCHEMA = "gemini_btc5m_capacity_gate/v1"

#: the published prediction-market fee schedule, effective 2026-03-18.
#: Source and provenance live in src/gemini_execution/rebate_program.py.
MAKER_RATE = D("0.0175")
TAKER_RATE = D("0.07")

#: crypto maker-rebate rate from 2026-06-11, and the eligible price band.
CRYPTO_REBATE_RATE = D("0.30")
REBATE_BAND_LOW = D("0.20")
REBATE_BAND_HIGH = D("0.80")

#: the rebate may not exceed this fraction of fill notional.
REBATE_NOTIONAL_CAP = D("0.05")

#: a 5-minute series opens a window every 300s.
BTC05M_WINDOWS_PER_DAY = 288


def _dec(x: Any) -> Optional[Decimal]:
    """Decimal or None. Bools and non-finite values are not numbers here."""
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def ceil_cent(x: Decimal) -> Decimal:
    """Round UP to the next cent, as the venue charges fees."""
    return x.quantize(CENT, rounding=ROUND_CEILING)


def floor_cent(x: Decimal) -> Decimal:
    """Round DOWN to the next cent, as the venue pays rebates."""
    return x.quantize(CENT, rounding=ROUND_FLOOR)


def maker_fee(*, contracts: Any, price: Any,
              waived: bool = False) -> Optional[Decimal]:
    """ceil_cent(0.0175 x C x P x (1-P)), or $0.00 when genuinely waived.

    `waived` is a measurement, not a hope. It must come from an authoritative
    account read -- `gemini_execution.maker_fee_cash_probe` asks whether money
    ever actually left -- and never from a venue field at fill time, which
    `maker_fee_cash_probe` documents as an assertion rather than a settlement.
    """
    c, p = _dec(contracts), _dec(price)
    if c is None or c <= ZERO:
        return None
    if p is None or not (ZERO < p < ONE):
        return None
    if waived:
        return ZERO
    return ceil_cent(MAKER_RATE * c * p * (ONE - p))


def maker_rebate_accrual(*, contracts: Any, price: Any,
                         rebate_rate: Any = CRYPTO_REBATE_RATE) -> Optional[Decimal]:
    """The UNROUNDED accrual of one maker fill. Outside the band it is zero.

    Returned unrounded on purpose: the venue's round-down applies to the day's
    total, not to each fill, so rounding here would understate a real book and
    `daily_rebate` is where the floor belongs.
    """
    c, p = _dec(contracts), _dec(price)
    rate = _dec(rebate_rate)
    if c is None or c <= ZERO:
        return None
    if p is None or not (ZERO < p < ONE):
        return None
    if rate is None or rate < ZERO:
        return None
    if not (REBATE_BAND_LOW <= p <= REBATE_BAND_HIGH):
        #: an out-of-band fill earns nothing at all, not a reduced amount.
        return ZERO
    accrual = rate * TAKER_RATE * c * p * (ONE - p)
    cap = REBATE_NOTIONAL_CAP * c * p
    return accrual if accrual <= cap else cap


def daily_rebate(accrued: Any) -> Optional[Decimal]:
    """What a day's accrual actually pays: floored to the cent."""
    a = _dec(accrued)
    if a is None or a < ZERO:
        return None
    return floor_cent(a)


def min_viable_clip(*, touch_spread: Any, price: Any,
                    maker_fee_waived: bool = False) -> Dict[str, Any]:
    """Smallest clip whose matched pair clears its own fees. THE sizing number.

    With the maker fee waived there is no floor to clear, so the venue minimum
    is viable and the answer says so rather than pretending to a bound.
    """
    s, p = _dec(touch_spread), _dec(price)
    if s is None or s <= ZERO:
        return {"ok": False, "reason": "TOUCH_SPREAD_UNREADABLE"}
    if p is None or not (ZERO < p < ONE):
        return {"ok": False, "reason": "PRICE_UNREADABLE"}
    if maker_fee_waived:
        return {"ok": True, "reason": None, "maker_fee_waived": True,
                "min_contracts": None,
                "note": ("no per-pair fee floor to clear while the waiver "
                         "holds; the venue quantity minimum binds instead")}
    #: both legs round up independently, so the floor is two cents a pair
    #: however small the clip is.
    fee_floor = D("2") * CENT
    need = fee_floor / s
    return {"ok": True, "reason": None, "maker_fee_waived": False,
            "min_contracts": need,
            "pair_fee_floor_usd": str(fee_floor),
            "gross_per_contract_usd": str(s),
            "note": ("a matched pair's gross margin is the touch spread, and "
                     "each leg's fee rounds UP independently, so no clip below "
                     "this can pay for its own two cents of fees")}


def venue_ceiling(*, tape_usd_per_window: Any, windows_per_day: Any,
                  traded_window_fraction: Any, touch_spread: Any,
                  quote_price: Any, participation_share: Any,
                  match_rate: Any, lone_leg_ev_per_contract: Any,
                  clip_contracts: Any, maker_fee_waived: bool = False,
                  rebate_rate: Any = CRYPTO_REBATE_RATE) -> Dict[str, Any]:
    """The most this venue can pay per day. Best case, and still a ceiling.

    Every input is required and unset ones refuse, because each of the three
    behavioural rates below can manufacture any answer at all:

      participation_share   our maker share of the flow that trades. MEASURED,
                            never assumed: the programme has 61 Gemini maker
                            fills in its entire history.
      match_rate            fraction of our fills that pair up rather than
                            stranding. Probe A filled 5 of 5 on ONE side
                            (p=0.0128), so a high value here needs evidence.
      lone_leg_ev_per_contract
                            expected dollars of an unmatched leg carried to
                            settlement. Negative on all measurement so far.
                            `l5_straddle_ev.entry_ev` is the per-share model.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason, schema=SCHEMA,
                    net_usd_per_day=None)

    tape = _dec(tape_usd_per_window)
    wpd = _dec(windows_per_day)
    frac = _dec(traded_window_fraction)
    spread = _dec(touch_spread)
    px = _dec(quote_price)
    share = _dec(participation_share)
    mrate = _dec(match_rate)
    lone = _dec(lone_leg_ev_per_contract)
    clip = _dec(clip_contracts)

    if tape is None or tape < ZERO:
        return no("TAPE_UNREADABLE")
    if wpd is None or wpd <= ZERO:
        return no("WINDOWS_PER_DAY_UNREADABLE")
    if frac is None or not (ZERO <= frac <= ONE):
        return no("TRADED_WINDOW_FRACTION_NOT_SET")
    if spread is None or not (ZERO < spread < ONE):
        return no("TOUCH_SPREAD_UNREADABLE")
    if px is None or not (ZERO < px < ONE):
        return no("QUOTE_PRICE_UNREADABLE")
    if share is None or not (ZERO <= share <= ONE):
        return no("PARTICIPATION_SHARE_NOT_SET")
    if mrate is None or not (ZERO <= mrate <= ONE):
        return no("MATCH_RATE_NOT_SET")
    if lone is None:
        #: NO DEFAULT. Assuming an unmatched leg is free is how a losing
        #: book reports a profit.
        return no("LONE_LEG_EV_NOT_SET")
    if clip is None or clip <= ZERO:
        return no("CLIP_UNREADABLE")

    pair_cost = ONE - spread
    if pair_cost <= ZERO:
        return no("SPREAD_EXCEEDS_PAYOUT", touch_spread=str(spread))

    #: the venue's whole tape, then the part of it we could ever touch.
    tape_per_day = tape * wpd * frac
    our_notional = share * tape_per_day

    #: a matched pair consumes `pair_cost` of notional across both legs.
    pairs_if_all_matched = our_notional / pair_cost
    matched_pairs = mrate * pairs_if_all_matched

    #: the rest strands as single legs, priced at the leg we quoted.
    unmatched_notional = (ONE - mrate) * our_notional
    unmatched_contracts = unmatched_notional / px

    fee_per_pair = ZERO
    if not maker_fee_waived:
        leg_a = maker_fee(contracts=clip, price=px, waived=False)
        leg_b = maker_fee(contracts=clip, price=ONE - px, waived=False)
        if leg_a is None or leg_b is None:
            return no("FEE_UNCOMPUTABLE")
        #: a pair is two legs of `clip` contracts, so the per-CONTRACT fee is
        #: the pair's fee spread over the clip. This is where ceil-cent bites.
        fee_per_pair = (leg_a + leg_b) / clip

    gross_per_pair = spread - fee_per_pair
    matched_usd = matched_pairs * gross_per_pair
    stranded_usd = unmatched_contracts * lone

    #: rebates accrue on every maker fill in the band, then floor to the cent
    #: once a day. Both legs of a pair are maker fills. A book that matched
    #: nothing accrues nothing -- that is an answer, not an unreadable input.
    if matched_pairs <= ZERO:
        accrued = ZERO
    else:
        accrued = maker_rebate_accrual(contracts=matched_pairs * D("2"),
                                       price=px, rebate_rate=rebate_rate)
        if accrued is None:
            return no("REBATE_UNCOMPUTABLE")
    rebate_usd = daily_rebate(accrued)
    if rebate_usd is None:
        return no("REBATE_UNCOMPUTABLE")

    net = matched_usd + stranded_usd + rebate_usd
    return {
        "ok": True, "reason": None, "schema": SCHEMA,
        "net_usd_per_day": net,
        "tape_usd_per_day": str(tape_per_day),
        "our_notional_usd_per_day": str(our_notional),
        "pair_cost": str(pair_cost),
        "gross_per_pair_usd": str(gross_per_pair),
        "fee_per_pair_usd": str(fee_per_pair),
        "maker_fee_waived": bool(maker_fee_waived),
        "matched_pairs_per_day": str(matched_pairs),
        "matched_usd_per_day": str(matched_usd),
        "unmatched_contracts_per_day": str(unmatched_contracts),
        "stranded_usd_per_day": str(stranded_usd),
        "rebate_accrued_usd_per_day": str(accrued),
        "rebate_paid_usd_per_day": str(rebate_usd),
        "note": ("a CEILING under best-case assumptions: full spread capture "
                 "on every matched pair, no latency, queue, cancellation or "
                 "outage cost. Nothing below it can exceed it."),
    }


def required_share_for_target(*, target_usd_per_day: Any,
                              tape_usd_per_window: Any, windows_per_day: Any,
                              traded_window_fraction: Any, touch_spread: Any,
                              match_rate: Any) -> Dict[str, Any]:
    """What share of the whole tape the target needs. Over 1.0 means refuse.

    The cleanest possible statement of infeasibility: if hitting the number
    requires being the maker on more than all of the venue's flow, no strategy
    exists. Fees and stranding are EXCLUDED here, which only makes the required
    share smaller -- so a share above 1.0 computed here is a hard impossibility,
    not a pessimistic estimate.
    """
    tgt = _dec(target_usd_per_day)
    tape = _dec(tape_usd_per_window)
    wpd = _dec(windows_per_day)
    frac = _dec(traded_window_fraction)
    spread = _dec(touch_spread)
    mrate = _dec(match_rate)
    if tgt is None or tgt <= ZERO:
        return {"ok": False, "reason": "TARGET_UNREADABLE"}
    if tape is None or tape <= ZERO:
        return {"ok": False, "reason": "TAPE_UNREADABLE"}
    if wpd is None or wpd <= ZERO:
        return {"ok": False, "reason": "WINDOWS_PER_DAY_UNREADABLE"}
    if frac is None or not (ZERO < frac <= ONE):
        return {"ok": False, "reason": "TRADED_WINDOW_FRACTION_NOT_SET"}
    if spread is None or not (ZERO < spread < ONE):
        return {"ok": False, "reason": "TOUCH_SPREAD_UNREADABLE"}
    if mrate is None or not (ZERO < mrate <= ONE):
        return {"ok": False, "reason": "MATCH_RATE_NOT_SET"}

    tape_per_day = tape * wpd * frac
    pair_cost = ONE - spread
    #: gross dollars per dollar of our maker notional, if matched
    margin_per_notional = mrate * (spread / pair_cost)
    if margin_per_notional <= ZERO:
        return {"ok": False, "reason": "NO_MARGIN"}
    needed_notional = tgt / margin_per_notional
    share = needed_notional / tape_per_day
    return {"ok": True, "reason": None,
            "required_share": share,
            "feasible": bool(share <= ONE),
            "tape_usd_per_day": str(tape_per_day),
            "needed_notional_usd_per_day": str(needed_notional),
            "margin_per_notional_dollar": str(margin_per_notional),
            "note": ("fees and stranded inventory are excluded, so a required "
                     "share above 1.0 is a hard impossibility rather than a "
                     "pessimistic estimate")}


def admits(ceiling: Any, *, target_usd_per_day: Any) -> Dict[str, Any]:
    """Whether the ceiling clears the target. Unknown is refused as a no.

    Mirrors `l5_straddle_ev.admits`: "we could not tell" is not permission.
    """
    if not isinstance(ceiling, dict) or not ceiling.get("ok"):
        reason = (str((ceiling or {}).get("reason"))
                  if isinstance(ceiling, dict) else "CEILING_UNAVAILABLE")
        return {"ok": False, "reason": reason}
    net = _dec(ceiling.get("net_usd_per_day"))
    tgt = _dec(target_usd_per_day)
    if net is None or tgt is None:
        return {"ok": False, "reason": "CEILING_UNREADABLE"}
    if net <= ZERO:
        return {"ok": False, "reason": "CEILING_NOT_POSITIVE",
                "net_usd_per_day": str(net)}
    if net < tgt:
        return {"ok": False, "reason": "CEILING_BELOW_TARGET",
                "net_usd_per_day": str(net),
                "target_usd_per_day": str(tgt),
                "shortfall_usd_per_day": str(tgt - net)}
    return {"ok": True, "reason": None, "net_usd_per_day": str(net),
            "target_usd_per_day": str(tgt)}
