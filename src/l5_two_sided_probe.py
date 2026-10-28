"""One-window two-sided BTC15m probe. Experiment-gated, inert by default.

THE QUESTION IT EXISTS TO ANSWER. Can we acquire actual filled inventory --
not displayed prices -- whose settlement pays positive net on BOTH outcomes?
A book holding `a` UP and `b` DOWN for executed cost `C` pays `a` if UP
settles and `b` if DOWN settles, so that question is exactly `C < min(a, b)`
after fees. Unequal quantities are allowed: extra contracts on whichever side
is currently the MINIMUM lift `min(a, b)` for less than a dollar a share.

WHY A SEPARATE MODULE AND NOT A CHANGE TO THE ENGINE. `FaithfulExecutor`
mirrors L5: `FT.l5_native(row)` takes side, price and size from the L5 row and
there is no injection point. The ordinary path can HOLD and GATE this shape
but cannot generate it. This module generates two intents and nothing else --
it owns no transport, no ledger, no cancellation and no gate. Everything that
can move money or lose track of an order stays where it already is.

STRUCTURAL FACT THIS MODULE CANNOT CHANGE. Resting post-only on both sides,
the pair cost is forced by the spread: bid_UP + bid_DOWN = 1 - spread. On a
one-tick book that is 0.99, whose 1-cent gross edge is less than the 3.44-cent
worst-case taker fee at p~0.45. So a probe resting AT the touch is negative on
both outcomes by construction, and `MIN_EDGE` refuses it. Only resting behind
the touch clears the bar -- which is a bet on fill probability, and
`min_completion_probability()` below says how big a bet.

NOTHING HERE SUBMITS, CANCELS OR SETTLES ANYTHING.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import os
from decimal import Decimal as D, ROUND_CEILING, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

from src import l5_faithful_translation as FT

EXPERIMENT_ENV = "L5_TS_EXPERIMENT_ID"
MARKET_ENV = "L5_TS_MARKET"

#: worst-case fee coefficient. The maker rebate is a credit we are never
#: entitled to assume, so the probe is priced as if both legs paid taker.
TAKER_THETA = D("0.0695")
#: the rebate, used ONLY to report the upside, never to justify an order
MAKER_THETA = D("-0.0125")

#: both legs post-only. A crossing order is a different experiment.
MODE = FT.MAKER

#: the two sides, named rather than spelled out at each comparison. Same
#: strings the rest of this module and `AUTH.PROBE_LEGS` already use.
UP, DOWN = "UP", "DOWN"

#: the SMALLEST gross edge ever acceptable. A pair at or above $1.00 is
#: never placed whatever the fee schedule says.
MIN_GROSS_EDGE = D("0.01")

#: the DEAREST pair this module will ever quote.
#:
#: WHY A CAP AND NOT JUST AN EDGE FLOOR. `MIN_GROSS_EDGE` admits a pair at
#: 0.99, and `fee_floor` then calls it profitable -- because it judges
#: POST-ONLY execution, where the maker credit carries it. On that pair
#: `net_if_taker` is NEGATIVE and `both_positive_if_taker` is False, so its
#: profitability depends entirely on a rebate, and L5_OPERATING_MODEL is
#: explicit that an uncredited rebate is never permission to place a trade.
#:
#: What stood between us and such a pair was one environment variable:
#: `L5_TS_DEPTH_TICKS=2` happens to make the pair at most `1 - spread - 0.04`.
#: At 1 tick a one-cent book gives 0.97 and at 0 it gives 0.99, both sendable.
#: 0.96 is the spring target in L5_OPERATING_MODEL, so the discipline is now
#: a gate rather than a side effect of a setting.
MAX_PAIR_COST = D("0.96")
MAX_PAIR_COST_ENV = "L5_TS_MAX_PAIR_COST"

# ===================================== THE FIRST LEG'S OWN-SIDE PRICE FLOOR
#
# SEVEN SETTLED WINDOWS, and the separation is NOT clean. The 2026-10-23
# campaign:
#
#   2145z  DOWN  own 0.35  ->  complement 0.56  pair 0.91   +$0.0927
#   2200z  DOWN  own 0.29  ->  complement 0.66  pair 0.95   +$0.0495
#   2215z  DOWN  own 0.33  ->  complement 0.43  pair 0.76   +$0.2424
#   2300z  DOWN  own 0.36  ->  complement 0.38  pair 0.74   +$0.2678
#   2230z  UP    own 0.12  ->  never reachable  unmatched   -$0.1764
#   2245z  UP    own 0.06  ->  never reachable  unmatched   -$0.0930
#
# AND THE COUNTER-EXAMPLE, which must not be left out: 1215z on 2026-10-20
# opened at own 0.06 -- below this floor -- and DID complete, at a pair of
# exactly 0.96, for an attested +$0.0984. Across all three settled windows
# that opened below 0.20 the record is ONE WIN and two losses, netting
# -$0.1710. The subset is negative, which is why the floor is worth having;
# it is not a clean rule, and this floor declines winners of that shape too.
#
# SEVEN WINDOWS IS PRELIMINARY EVIDENCE AND 0.20 IS NOT CLAIMED TO BE OPTIMAL.
# Any threshold in 0.13..0.28 fits this sample equally well; 0.20 is chosen
# for margin on both sides of the observed gap, not because the data picks
# it. What makes the rule worth having is the MECHANISM, which does not rest
# on those two losses:
#
# Feasibility AT PLAN TIME is governed by the spread, not the price level --
# `pair ~= 1 - spread - 2 * depth_ticks * tick` -- which is why all six
# planned at a pair of exactly 0.95. What differs is what happens AFTER the
# fill. A maker bid two ticks under the touch fills BECAUSE the price came
# down to it, so every first-leg fill is mildly adverse by construction. At
# an own price of 0.06-0.12 the market sits near a boundary and that move is
# usually trend: three cents of continuation puts the complement
# permanently out of reach, because the complement costs 1 - wire_ask - 2
# ticks and rises as the cheap side gets cheaper. At 0.29-0.36 the same move
# is more often noise and the complement stays reachable.
#
# AND THE ECONOMIC POINT NEEDS NEITHER LOSS. A first leg that cannot complete
# contributes an EV-NEUTRAL fair lottery instead of a pair edge. Both
# unmatched legs above were correctly priced bets -- UP at 0.12 pays
# 0.12 * 1.47 * 0.88 - 0.88 * 0.1764 ~= 0 -- so holding them was not a
# mistake and completing them as taker would have been worse. They simply
# are not what this strategy is for: opening below the floor swaps a +0.05
# pair edge for a coin flip and adds variance.
#: HOW MANY SEQUENTIAL CYCLES THE CASH ENVELOPE IS SPLIT ACROSS.
#:
#: WHY THIS EXISTS. Every plan the 20261024a campaign produced was
#: `sized_by: CASH_ENVELOPE` with a cash-gate obligation of 1.6366 to 1.6480
#: against the $1.65 envelope -- 99.2% to 99.9% of it spent by round 0. The
#: round cap was never the limit (`ACC_MAX_ROUNDS_DEFAULT` is 8 and every plan
#: reported 8) and neither was the campaign budget (headroom stayed above
#: $20.38 of $29.30). And `cash_policy` is NO_RECYCLING: matching releases
#: nothing, only settlement does, which was observed at about T+16 minutes --
#: after the 15-minute window had closed. A second cycle was arithmetically
#: impossible, not policy-refused.
#:
#: IT ONLY EVER SHRINKS A ROUND. The divisor applies to the cash envelope
#: alone, which is the constraint that bound; the loss ceiling and the
#: campaign budget are untouched because they were never binding and
#: tightening them would refuse plans for the wrong reason. A divisor of N
#: makes every round at most 1/N of the clip it is today, so each individual
#: order is strictly smaller and strictly safer than the campaign's were.
#:
#: IT MINTS NO HEADROOM. Aggregate exposure is enforced account-wide on every
#: order by the $2 ceiling, the 3-contract residual cap and both loss halts,
#: each of which counts filled inventory plus still-fillable outstanding
#: orders. Splitting the envelope cannot raise any of them.
#:
#: DEFAULT 1 IS CURRENT BEHAVIOUR, byte for byte.
#: THE OPENING LEG MAY NEVER EXCEED THE APPROVED RESIDUAL CEILING.
#:
#: WHY IT HAD TO BE EXPLICIT. The residual cap in `l5_live_execution` refuses
#: only when the PROSPECTIVE BOOK IS NOT FLAT -- `exempt_open` -- because it
#: was written to stop accumulation onto existing inventory after the 1130z
#: burst reached 3.24 against a cap of 1.80. An OPENING leg from a flat book
#: was therefore never bounded by it, and the opening clip is set by cash
#: instead: qty = cash / (1 + up_px). At the $5 envelope that produced a
#: 4.09-contract fill at 13:31:45Z on 2026-10-24, which is a 4.09-contract
#: residual if it strands -- above the 3 the owner authorized.
#:
#: AND LOWERING CASH DOES NOT FIX IT. qty = cash / (1 + up_px) grows as up_px
#: falls, so no cash figure the owner approved bounds the clip across eligible
#: prices: at $3.70 a 0.02 leg still sizes 3.62, and only cash <= 3 x 1.01 =
#: $3.03 would guarantee it. A quantity cap is price-independent and is the
#: only honest implementation of a CONTRACT ceiling.
#:
#: IT BOUNDS THE PLAN, WHICH IS THE OPENING PAIR. Completing inventory already
#: held is sized against that inventory elsewhere, so this cannot block a
#: risk-reducing complement for a position that is already open.
#:
#: Configurable DOWNWARD only; anything malformed or larger reads as 3.
OPENING_LEG_MAX_CONTRACTS = D("3")
OPENING_LEG_MAX_ENV = "L5_TS_OPENING_LEG_MAX_CONTRACTS"

ROUND_ALLOCATIONS = 1
ROUND_ALLOCATIONS_ENV = "L5_TS_ROUND_ALLOCATIONS"
#: a divisor beyond this would size rounds below anything the venue grid can
#: express at our clip, so it is refused rather than silently floored to zero.
ROUND_ALLOCATIONS_MAX = 4

MIN_FIRST_LEG_OWN_PRICE = D("0.20")
MIN_FIRST_LEG_ENV = "L5_TS_MIN_FIRST_LEG_PRICE"

#: THE WORST-CASE OPENING NOTIONAL, IN DOLLARS. The replay's answer to the
#: one window that lost money.
#:
#: 0130z opened DOWN 1.87 at own 0.62, the dearer leg of a 0.95 plan. Its
#: complement never filled and it settled worthless: -$1.1594, which is 90.2%
#: of everything the seven completed pairs earned. Nothing was broken -- the
#: pair cap, the TTL and the drift recovery all behaved as specified. The leg
#: was simply too large for the loss it could take.
#:
#: WHY NOTIONAL AND NOT PRICE. A maximum opening PRICE was the obvious
#: alternative and the replay rejected it: it is non-monotonic in its own
#: threshold (+$1.2506 at 0.60 but -$0.0344 at 0.65, worse than doing
#: nothing), only one of eight episodes sat above 0.60 so a single
#: observation would set it, and the completion-rate curve behind it could
#: not be validated on PMUS -- the public tape holds 2 windows and the other
#: tapes are a different venue. A notional cap improved net settlement at
#: EVERY budget tried ($0.50 .. $1.00), has no cliff, and protects whatever
#: the opening price turns out to be. See L5_FOUR_POLICY_REPLAY.md.
#:
#: IT BOUNDS THE DEARER LEG, which is the one whose solo fill is the strand.
#: `qty * dear <= budget`, so the worst single-leg loss is the budget by
#: construction rather than by luck.
#:
#: IT BOUNDS OPENING PLANS ONLY. Completing inventory already held is sized
#: against that inventory elsewhere, so this can never throttle a
#: risk-reducing complement -- throttling one is what stranded 0130z.
#:
#: UNSET IS INERT, so the deployed default is byte-for-byte today's sizing.
#: UNREADABLE IS ZERO, which refuses rather than guesses: a budget we cannot
#: count is not a budget we may assume exists. Clamped to the approved
#: instantaneous exposure, so a typo cannot raise it.
OPENING_NOTIONAL_ENV = "L5_TS_OPENING_NOTIONAL_BUDGET_USD"
OPENING_NOTIONAL_MAX_USD = D("5")


def _sized_by(*, by_loss: D, by_cash: D, by_budget: Optional[D],
              by_opening_cap: D, by_notional: Optional[D] = None) -> str:
    """Which bound actually sized the plan: the argmin, with deterministic
    tie-breaking toward the TIGHTER-sounding constraint so a tie never hides
    a cap behind a dollar figure."""
    cands = [("OPENING_NOTIONAL_CAP", by_notional),
             ("OPENING_LEG_CONTRACT_CAP", by_opening_cap),
             ("CASH_ENVELOPE", by_cash),
             ("LOSS_CEILING", by_loss)]
    cands = [(n, v) for n, v in cands if v is not None]
    if by_budget is not None:
        cands.append(("CAMPAIGN_BUDGET", by_budget))
    lo = min(v for _n, v in cands)
    for name, v in cands:
        if v == lo:
            return name
    return "LOSS_CEILING"


def opening_leg_max_contracts() -> D:
    """The approved residual ceiling, as a hard cap on an opening clip.

    Configurable DOWNWARD only. A malformed, non-finite, non-positive or
    LARGER value reads as the approved 3: a ceiling that a typo can raise is
    not a ceiling.
    """
    raw = (os.getenv(OPENING_LEG_MAX_ENV) or "").strip()
    if not raw:
        return OPENING_LEG_MAX_CONTRACTS
    try:
        v = D(raw)
    except (InvalidOperation, ValueError, TypeError):
        return OPENING_LEG_MAX_CONTRACTS
    if not v.is_finite() or v <= 0:
        return OPENING_LEG_MAX_CONTRACTS
    return min(v, OPENING_LEG_MAX_CONTRACTS)


def round_allocations() -> int:
    """How many sequential cycles to split the cash envelope across.

    UNPARSEABLE OR ABSENT IS 1, which is today's behaviour. Anything outside
    1..ROUND_ALLOCATIONS_MAX is also 1: a divisor is a sizing decision and the
    safe reading of a malformed one is "do what we already do", never "guess".
    """
    raw = (os.getenv(ROUND_ALLOCATIONS_ENV) or "").strip()
    if not raw:
        return ROUND_ALLOCATIONS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return ROUND_ALLOCATIONS
    if n < 1 or n > ROUND_ALLOCATIONS_MAX:
        return ROUND_ALLOCATIONS
    return n


def opening_notional_budget_usd() -> Optional[D]:
    """Dollars of worst-case loss an OPENING leg may carry. None is inert.

    Absent means inert, which keeps the deployed default identical to
    today. Anything present but unreadable, non-finite or negative reads as
    ZERO and therefore refuses every opening plan: a bound we cannot parse
    must not silently become no bound. A value above the approved
    instantaneous exposure is clamped down to it.
    """
    raw = (os.getenv(OPENING_NOTIONAL_ENV) or "").strip()
    if not raw:
        return None
    try:
        v = D(raw)
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return D(0)
    if not v.is_finite() or v < 0:
        return D(0)
    return min(v, OPENING_NOTIONAL_MAX_USD)


def min_first_leg_own_price() -> D:
    """The own-side floor for OPENING a leg. Never for completing one.

    Configurable, because 0.20 is preliminary: a value in 0..0.50 is
    honoured, including 0, which DISABLES the rule and is an explicit owner
    act. Anything unreadable, non-finite or outside that range falls back to
    0.20 -- a typo must not be able to re-admit the regime this bounds.
    """
    raw = (os.getenv(MIN_FIRST_LEG_ENV) or "").strip()
    if not raw:
        return MIN_FIRST_LEG_OWN_PRICE
    try:
        v = D(raw)
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return MIN_FIRST_LEG_OWN_PRICE
    if not v.is_finite() or v < 0 or v > D("0.50"):
        return MIN_FIRST_LEG_OWN_PRICE
    return v


# ================== TWO MAKER ORDERS, ONE BOUNDED PAIR
#
# WHY. With one live-order slot the complement can never rest beside the
# first leg: it is refused MAX_LIVE_ORDERS -- 331 times across the 8-window
# campaign of 2026-10-23 -- and can only be placed once the first leg leaves
# the book, which is exactly the interval in which the book walks away and
# the pair breaches the 0.96 cap. Three of eight windows never completed and
# cost -$0.6684 against +$0.8020 from the five that did.
#
# OFF BY DEFAULT, and that is deliberate: the owner approved the mechanism
# and withheld activation until the lifecycle and risk accounting are
# verified. With the flag unset this module behaves exactly as it did.
#
# WHAT IT IS NOT. It is not `max_live_orders = 2`. That would permit two
# independent FIRST legs -- two openings sharing one cash envelope, which is
# the 1215z failure mode the STRATEGY_EXCLUSIVE gates exist to stop. The
# second slot is granted only to an order that is demonstrably the OTHER
# side of the SAME round in the SAME market as the one order already live.
TWO_ORDER_PAIR_ENV = "L5_TS_TWO_ORDER_PAIR"


def two_order_pair_enabled() -> bool:
    """Is the second maker slot active? Default NO.

    Only the exact strings "1", "YES" and "TRUE" (case-insensitively) enable
    it. Anything else -- unset, empty, "0", "no", a typo -- leaves the single
    slot in place, because a second resting order is money at the venue and
    must not be switchable on by accident.
    """
    raw = (os.getenv(TWO_ORDER_PAIR_ENV) or "").strip().upper()
    return raw in ("1", "YES", "TRUE")


def second_leg_slot_refusal(*, market: Any, side: str,
                            live_orders: Optional[Mapping[str, Any]] = None,
                            legs_claimed: Optional[Mapping[str, Any]] = None
                            ) -> Optional[str]:
    """None if this order may take the SECOND maker slot, else why not.

    Returning None does not place anything and grants no other allowance:
    the caller still runs every cash, exposure, residual, per-market and
    loss gate, each of which already prices an outstanding order as though
    it fills in full. This decides one thing only -- whether the
    MAX_LIVE_ORDERS ceiling may be one higher for THIS order.

    EVERY CONDITION MUST HOLD, and each is a named refusal:

      * the flag is on;
      * exactly ONE order is live. Zero means this is the first leg and the
        ordinary slot covers it; two or more means the pair is already
        resting and a third order is never part of one bounded pair;
      * that live order is in THIS market. A second market is excluded by
        max_concurrent_markets as well, and this refuses it independently;
      * it is on the OPPOSITE side. A same-side order is an EXTEND, which
        is a separate decision with its own economics and is not authorized
        to use this slot;
      * it belongs to the SAME round. `live_orders` rows carry no round --
        `record_submit` stores t, market, side, price, qty, session_id, mode,
        remaining and state and nothing else -- so the round is established
        from `legs_claimed`, which the caller reads per round with
        `probe_legs_claimed(market, rnd)`. The resting side must be CLAIMED
        in this round, which is what makes it this plan's first leg rather
        than an older order;
      * and THIS side must not be claimed in this round, which is what stops
        a replacement of an already-claimed leg from taking the slot.

    FAIL CLOSED: an absent claim map, an unreadable live-order map or a row
    whose side or market cannot be read all refuse.
    """
    if not two_order_pair_enabled():
        return "TWO_ORDER_PAIR_DISABLED"
    if live_orders is None:
        return "LIVE_ORDERS_UNREADABLE"
    try:
        rows = list(live_orders.values())
    except BaseException:                                      # noqa: BLE001
        return "LIVE_ORDERS_UNREADABLE"
    if len(rows) == 0:
        return "NO_FIRST_LEG_RESTING"
    if len(rows) > 1:
        return "PAIR_ALREADY_RESTING"
    row = rows[0]
    try:
        r_market = str(row.get("market") or "")
        r_side = str(row.get("side") or "").strip().upper()
    except BaseException:                                      # noqa: BLE001
        return "LIVE_ORDER_ROW_UNREADABLE"
    if not r_market or r_market != str(market):
        return "RESTING_ORDER_IS_ANOTHER_MARKET"
    if r_side not in (UP, DOWN):
        return "RESTING_ORDER_SIDE_UNREADABLE"
    if r_side == str(side).strip().upper():
        return "RESTING_ORDER_IS_THE_SAME_SIDE"
    if not legs_claimed:
        return "CLAIM_STATE_UNREADABLE"
    if not bool(legs_claimed.get(r_side)):
        return "RESTING_ORDER_IS_ANOTHER_ROUND"
    if bool(legs_claimed.get(str(side).strip().upper())):
        return "THIS_SIDE_ALREADY_CLAIMED"
    return None


def first_leg_entry_refusal(*, side: str, own_px: Any,
                            legs_claimed: Optional[Mapping[str, Any]] = None,
                            held_up: Any = 0,
                            held_down: Any = 0) -> Optional[Dict[str, Any]]:
    """None to permit this leg, else the named refusal. Read-only.

    SYMMETRIC BY CONSTRUCTION. It reads `own_px` -- the leg's own-outcome
    economic price, the same number `build_orders` puts in `legs[].price` and
    `l5_order_body.build` converts to the wire -- and nothing about the side
    beyond reporting it. UP at 0.12 and DOWN at 0.12 are the same decision.

    IT CAN ONLY REFUSE AN OPENING. Two independent facts make a leg a
    completion rather than a first leg, and EITHER one permits it
    unconditionally:

      * a leg of this round is already claimed, so the other side is being
        matched;
      * the market already holds filled quantity on either side, so this is
        inventory being managed.

    That ordering matters: the floor is consulted LAST, so no amount of
    configuration can turn this rule into something that strands a position
    it cannot complete.

    FAIL CLOSED on an unreadable price, which is the one case where refusing
    is the safe direction: an opening we cannot price is one we cannot judge.
    """
    if legs_claimed and any(bool(v) for v in legs_claimed.values()):
        return None                     # completing the other side
    up, dn = _d(held_up), _d(held_down)
    if up is None or dn is None:
        return None                     # unreadable inventory is not ours
        # to adjudicate: the residual and uncertainty gates own that case
    if up != dn:
        return None                     # a real imbalance: this is managing it
        # KEYED ON THE IMBALANCE, NOT ON HOLDING ANYTHING.
        #
        # This read `up != 0 or dn != 0` -- "the market already holds filled
        # quantity on either side" -- and that waived the floor for the rest
        # of the window as soon as ANY inventory existed. After a pair
        # completes, `up` and `dn` are both non-zero AND EQUAL: there is no
        # imbalance to manage, so a further leg is a fresh OPENING and must
        # face the same floor as the first one. Under the old predicate it
        # did not.
        #
        # 2026-10-26 2115z is the case. The window paired 2.18 at 0.95,
        # leaving up == dn == 2.18, and the engine then opened DOWN at own
        # 0.17 -- below the live 0.20 floor, which was waived by this line.
        # It stranded and settled -$0.2856.
        #
        # `up == dn` covers flat (0 == 0) too, which is the first leg of a
        # window and has always faced the floor. Completion and genuine
        # inventory management are untouched: an imbalance still returns
        # None here unconditionally, so this can never strand a position it
        # cannot complete.
    floor = min_first_leg_own_price()
    if floor <= 0:
        return None                     # explicitly disabled by the owner
    # `_d` returns Decimal("NaN") for "nan", which is NOT None and which
    # RAISES InvalidOperation on comparison -- a crash in the decision path
    # rather than a refusal. Non-finite is unreadable.
    p = _d(own_px)
    if p is None or not p.is_finite():
        return {"reason": "FIRST_LEG_PRICE_UNREADABLE", "side": str(side),
                "own_px": str(own_px), "min_own_px": str(floor)}
    if p < floor:
        return {"reason": "FIRST_LEG_BELOW_MIN_PRICE", "side": str(side),
                "own_px": str(p), "min_own_px": str(floor),
                "env": MIN_FIRST_LEG_ENV,
                "note": ("an opening leg this cheap has, on the evidence of "
                         "2230z and 2245z, a complement that the 0.96 pair "
                         "cap cannot reach once the cheap side gets cheaper. "
                         "Holding it unmatched is an EV-neutral lottery "
                         "rather than a pair edge. This refuses OPENING "
                         "only; completion and inventory management are "
                         "never blocked by it.")}
    return None


def max_pair_cost() -> D:
    """The pair ceiling. Configurable CHEAPER only; unreadable is not wider."""
    raw = (os.getenv(MAX_PAIR_COST_ENV) or "").strip()
    if not raw:
        return MAX_PAIR_COST
    try:
        v = D(raw)
    except (InvalidOperation, ValueError, ArithmeticError):
        return MAX_PAIR_COST
    if v <= 0:
        return MAX_PAIR_COST
    return min(MAX_PAIR_COST, v)
#: retained for callers that still read it; the binding test is now
#: `fee_floor()`, computed from the ACTUAL proposed quote prices.
MIN_EDGE = MIN_GROSS_EDGE


def fee_floor(up_px: D, dn_px: D) -> Dict[str, Any]:
    """Is this pair profitable on both outcomes, and under which execution?

    WHY THIS IS NOT A CONSTANT. The old flat 0.04 floor was the worst-case
    TAKER fee at p ~ 0.45, which is the most expensive point on the curve:
    the fee is Theta * p * (1-p), so at 0.12/0.88 it is less than half what
    it is at 50/50. A single constant therefore refused configurations that
    are comfortably profitable at the extremes, and it refused resting AT
    THE TOUCH everywhere -- the configuration with by far the best measured
    fill rate.

    BOTH LEGS ARE POST-ONLY BY CONSTRUCTION, so the maker coefficient is the
    one that applies: MODE is FT.MAKER, which is what l5_order_body turns
    into participateDontInitiate, and a crossing order is a different
    experiment. `requires_post_only` is reported so no caller can read this
    verdict as applying to a taker fill.

    THE TAKER FIGURE IS STILL COMPUTED AND REPORTED, including when it is
    negative, because it is the honest worst case if the rebate is not
    credited on a given order.
    """
    edge = D(1) - (up_px + dn_px)
    taker_cost = (TAKER_THETA * up_px * (D(1) - up_px)
                  + TAKER_THETA * dn_px * (D(1) - dn_px))
    maker_credit = -(MAKER_THETA * up_px * (D(1) - up_px)
                     + MAKER_THETA * dn_px * (D(1) - dn_px))
    net_maker = edge + maker_credit
    net_taker = edge - taker_cost
    return {"edge": edge,
            "taker_fee_per_contract": taker_cost,
            "maker_credit_per_contract": maker_credit,
            "net_if_maker": net_maker,
            "net_if_taker": net_taker,
            "ok": bool(edge >= MIN_GROSS_EDGE and net_maker > 0),
            "requires_post_only": True,
            "labels": ("the verdict is for POST-ONLY execution on both "
                       "legs, which is how this probe quotes. net_if_taker "
                       "is the worst case should the rebate not be credited "
                       "and is reported even when negative.")}
#: price sanity, same band the queue measurement uses
MIN_PRICE = D("0.05")
MAX_PRICE = D("0.95")

WINDOW_S = 900
#: the hard terminal deadline: nothing rests past this
CANCEL_MARGIN_S = 180.0
#: the margin may be WIDENED (cancel EARLIER), never narrowed, exactly as
#: `l5_live_authorization.close_margin_s` works.
#:
#: WHY IT NEEDS A KNOB AT ALL. The independent watchdog treats an order older
#: than `L5_WATCHDOG_REPORT_MAX_AGE_S` (300s in production) as overdue, and it
#: ASSERTS HALT BEFORE it cancels. With the default margin a leg placed at
#: T+2:00 and held to the T+12:00 deadline rests 600s, so the watchdog would
#: latch UNMANAGED_RESTING_ORDER, stop the executor and kill the complement
#: mid-window. That is safe but it destroys the measurement. Widening the
#: margin cancels EARLIER and is therefore a tightening in both senses.
CANCEL_MARGIN_ENV = "L5_TS_CANCEL_MARGIN_S"


def cancel_margin_s() -> float:
    """The effective terminal margin. Wider only; unreadable is not wider."""
    raw = (os.getenv(CANCEL_MARGIN_ENV) or "").strip()
    try:
        v = float(raw) if raw else CANCEL_MARGIN_S
    except ValueError:
        v = CANCEL_MARGIN_S
    return max(CANCEL_MARGIN_S, v)
#: no leg may be PLACED after this
PLACE_OFFSET_S = 480.0
MIN_REST_S = 60.0
#: the EARLIEST offset into a window at which a leg may be placed. Zero keeps
#: the deployed behaviour byte-for-byte.
#:
#: WHY IT EXISTS. `may_place` allowed placement from T+0, and the terminal
#: deadline is fixed relative to the window CLOSE, so the rest of a leg was
#: bounded only by whoever chose the moment the shadow lock came off. That
#: worked for one hand-timed window and does not survive a MANDATE, where the
#: lock stays open across consecutive windows: a leg placed at T+0:05 and held
#: to a T+10:00 deadline rests 595s against the watchdog's 300s max order age,
#: and the watchdog ASSERTS HALT BEFORE it cancels. A floor makes the bound
#: structural instead of operational.
#:
#: RAISE-ONLY, like `cancel_margin_s` widens only: a later floor is a shorter
#: rest, so only tightening is accepted and an unreadable value is not
#: permission.
PLACE_FLOOR_S = 0.0
PLACE_FLOOR_ENV = "L5_TS_PLACE_FLOOR_S"


def place_floor_s() -> float:
    """The effective placement floor, as an offset from the window start."""
    raw = (os.getenv(PLACE_FLOOR_ENV) or "").strip()
    try:
        v = float(raw) if raw else PLACE_FLOOR_S
    except ValueError:
        v = PLACE_FLOOR_S
    return max(PLACE_FLOOR_S, v)
MAX_BOOK_AGE_S = 2.0


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def experiment_id() -> str:
    return str(os.getenv(EXPERIMENT_ENV, "")).strip()


def named_market() -> str:
    return str(os.getenv(MARKET_ENV, "")).strip()


def market_patterns() -> tuple:
    """The authorized market patterns, in order, from `L5_TS_MARKET`.

    Comma-separated. An entry with no `*` is an EXACT slug and matches only
    itself, exactly as before.
    """
    raw = named_market()
    return tuple(p for p in (x.strip() for x in raw.split(",")) if p)


def market_authorized(market: str) -> bool:
    """Does this slug match an authorized pattern?

    WHY A PATTERN AT ALL. The runner rebuilds its venue client against a
    NEW slug every fifteen minutes, so an exact-slug authorization
    expires the moment its window rolls and the engine goes inert until a
    human edits an environment variable. That is the second half of the
    one-shot restriction: continuous accumulation across successive markets
    is impossible while the authorization names one slug.

    A pattern is still a deliberate, bounded authorization -- it names an
    instrument family (`cpc-btc-updown-15m-*`), not "any market" -- and an
    exact slug remains exact, so nothing already configured widens.
    """
    m = str(market or "").strip()
    if not m:
        return False
    for pat in market_patterns():
        if "*" in pat:
            if fnmatch.fnmatchcase(m, pat):
                return True
        elif m == pat:
            return True
    return False


def active(market: Optional[str] = None) -> bool:
    """False unless BOTH an experiment and a market are named AND it matches.

    INERT IS THE DEFAULT AND THERE IS NO PARTIAL ARMING. A missing experiment
    id, a missing market, or a market that no authorized pattern covers all
    return False, so an incomplete configuration cannot half-enable the
    probe.
    """
    exp = experiment_id()
    if not exp or not market_patterns():
        return False
    if market is None:
        return True
    return market_authorized(market)


# --------------------------------------------------------------- timing

#: THE TWO ECONOMIC CUTOFFS, replacing one arbitrary placement band.
#:
#: WHY TWO. A single `place_cutoff` conflated two decisions with opposite
#: risk. ENTERING a first leg late is the dangerous one: if the window cannot
#: still produce a complement at a price that clears the fee floor, that leg
#: is a directional bet the engine never intended. COMPLETING is the safe one
#: -- it REMOVES exposure -- so it must stay available later than opening.
#: Collapsing them meant the engine stopped reducing risk at the same instant
#: it stopped taking it.
#:
#: Both are offsets from the window START and are RAISE-ONLY in the safe
#: direction: `open` may only move EARLIER (stop entering sooner) and
#: `complete` may only move EARLIER too, so neither knob can extend trading.
ENTRY_CUTOFF_S = 600.0
COMPLETE_CUTOFF_S = 720.0
ENTRY_CUTOFF_ENV = "L5_TS_ENTRY_CUTOFF_S"
COMPLETE_CUTOFF_ENV = "L5_TS_COMPLETE_CUTOFF_S"


def _earlier_only(env: str, default: float) -> float:
    """Configured value, accepted only when it is EARLIER than the default."""
    raw = (os.getenv(env) or "").strip()
    try:
        v = float(raw) if raw else default
    except ValueError:
        return default                      # unreadable is not permission
    return min(default, v)


def entry_cutoff_s() -> float:
    return _earlier_only(ENTRY_CUTOFF_ENV, ENTRY_CUTOFF_S)


def complete_cutoff_s() -> float:
    return _earlier_only(COMPLETE_CUTOFF_ENV, COMPLETE_CUTOFF_S)


#: HOW LONG ONE ORDER MAY REST, from its own submission. This is what makes
#: the watchdog's 300s a PER-ORDER backstop instead of the strategy's clock:
#: with a per-order deadline, an order's life stops being the window's life,
#: so the engine can quote from the window's open and still never hold
#: anything near the watchdog's limit.
#:
#: Measured from SUBMIT rather than from the venue acknowledgement, which is
#: the conservative direction: submit is always at or before the ack, so the
#: deadline fires sooner than an ack-based one would.
#:
#: SHORTER ONLY. A longer value and an unreadable value are both refused.
ORDER_TTL_S = 120.0
ORDER_TTL_ENV = "L5_TS_ORDER_TTL_S"


def order_ttl_s() -> float:
    """The per-order rest bound. Configurable shorter, never longer."""
    raw = (os.getenv(ORDER_TTL_ENV) or "").strip()
    try:
        v = float(raw) if raw else ORDER_TTL_S
    except ValueError:
        return ORDER_TTL_S
    if v <= 0:
        return ORDER_TTL_S
    return min(ORDER_TTL_S, v)


def order_due(now: float, submitted_t: float, t_start: float,
              t_end: float) -> bool:
    """Is this order past its own deadline, or past the terminal?

    `min(submit + ttl, terminal)`, the same shape as the queue
    measurement's `min(T_ack + 120, T_end - 180)`.
    """
    return (float(now) >= float(submitted_t) + order_ttl_s()
            or float(now) >= cancel_cutoff(t_end))


def may_enter(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow a NEW first leg, else the refusal reason.

    Bounded by the open cutoff AND by the terminal deadline, so a leg can
    never be opened that the terminal could not withdraw.
    """
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    floor = float(t_start) + place_floor_s()
    if now < floor:
        return "BEFORE_PLACE_FLOOR"
    if now >= float(t_start) + entry_cutoff_s():
        return "PAST_ENTRY_CUTOFF"
    if now >= cancel_cutoff(t_end) - MIN_REST_S:
        return "TOO_LATE_TO_WITHDRAW"
    return None


#: THE MINIMUM USABLE ENTRY RUNWAY a window must still offer to be worth
#: CLAIMING. A claim is irreversible -- `WindowLedger.reserve` is not
#: refundable and `retire_settled_market` is the only thing that frees a
#: campaign -- so claiming a window that cannot realistically enter spends
#: the authorization for nothing.
#:
#: MEASURED, NOT ASSUMED. On 2026-10-23 the executor armed window 0000z at
#: T+441s. `entry_cutoff_s` is 600, so arithmetic said 159s of runway
#: remained; `may_enter` actually began refusing TOO_LATE_TO_WITHDRAW at
#: T+565s, because `t_end` comes from the VENUE (`bounded_by: VENUE_CLOSE`)
#: and the venue's close can be earlier than start+WINDOW_S implies. The
#: window produced zero submissions and the single-window campaign was spent.
#: That is why `usable_entry_runway_s` asks `may_enter` instead of
#: recomputing the bound: the only trustworthy deadline is the one the gate
#: itself enforces, against the venue's own t_end.
#:
#: RAISE-ONLY. A LARGER runway requirement is strictly more conservative, so
#: only increases are accepted and an unreadable value is not permission.
MIN_ENTRY_RUNWAY_S = 300.0
MIN_ENTRY_RUNWAY_ENV = "L5_TS_MIN_ENTRY_RUNWAY_S"


def min_entry_runway_s() -> float:
    raw = (os.getenv(MIN_ENTRY_RUNWAY_ENV) or "").strip()
    try:
        v = float(raw) if raw else MIN_ENTRY_RUNWAY_S
    except ValueError:
        return MIN_ENTRY_RUNWAY_S           # unreadable is not permission
    return max(MIN_ENTRY_RUNWAY_S, v)


def usable_entry_runway_s(now: float, t_start: float, t_end: float) -> float:
    """Seconds of ENTRY opportunity this window still offers, from `now`.

    Derived by asking `may_enter` where it stops allowing entry, rather than
    re-deriving the bound from constants -- `t_end` is the venue's close and
    only the gate knows the composite deadline. Zero when entry is already
    closed; zero also before the window opens, because a window that has not
    started has no runway to spend yet and `window_eligible` reports that
    case separately.

    Resolution is one second, which is far finer than the 300s threshold it
    feeds and costs at most 900 evaluations of a pure predicate.
    """
    n = float(now); s = float(t_start); e = float(t_end)
    if n < s:
        return 0.0
    if may_enter(n, s, e) is not None:
        # already refused at `now`; BEFORE_PLACE_FLOOR is the one refusal
        # that resolves on its own, so it is not treated as exhausted here.
        if may_enter(n, s, e) != "BEFORE_PLACE_FLOOR":
            return 0.0
    t = n
    limit = s + float(WINDOW_S)
    while t <= limit:
        why = may_enter(t, s, e)
        if why is not None and why != "BEFORE_PLACE_FLOOR":
            break
        t += 1.0
    return max(0.0, t - n)


def window_eligible(now: float, t_start: float, t_end: float
                    ) -> Dict[str, Any]:
    """Whether this window is worth an IRREVERSIBLE claim. Read-only.

    SEPARATED FROM THE CLAIM ON PURPOSE. An eligibility question must be
    answerable without spending anything, so this touches no ledger, writes
    nothing, and may be called on every tick. The caller claims only after
    this says ok.

    `WINDOW_NOT_OPEN` is reported as not-eligible-yet rather than as a
    failure: the correct response is to wait for the next window start, which
    is exactly the behaviour that was missing when a mid-window deployment
    claimed an aging window.
    """
    n = float(now); s = float(t_start); e = float(t_end)
    if n < s:
        return {"ok": False, "reason": "WINDOW_NOT_OPEN_YET",
                "seconds_until_open": s - n,
                "runway_s": 0.0, "required_s": min_entry_runway_s(),
                "labels": ("not a refusal -- wait for this window to open "
                           "and claim it from the start")}
    runway = usable_entry_runway_s(n, s, e)
    need = min_entry_runway_s()
    if runway < need:
        return {"ok": False, "reason": "INSUFFICIENT_ENTRY_RUNWAY",
                "runway_s": runway, "required_s": need,
                "elapsed_s": n - s,
                "labels": ("the claim is irreversible and this window cannot "
                           "use it; wait for the next window rather than "
                           "spending the campaign on a stub")}
    return {"ok": True, "reason": None, "runway_s": runway,
            "required_s": need, "elapsed_s": n - s}


def may_complete(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow a COMPLEMENT against existing inventory.

    Deliberately later than `may_enter`: completing removes risk, so the
    engine keeps doing it after it has stopped opening. Still bounded by the
    terminal, which nothing may rest past.
    """
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    if now >= float(t_start) + complete_cutoff_s():
        return "PAST_COMPLETE_CUTOFF"
    if now >= cancel_cutoff(t_end):
        return "PAST_TERMINAL"
    return None


def cancel_cutoff(t_end: float) -> float:
    """The hard terminal deadline. Nothing may rest past it."""
    return float(t_end) - cancel_margin_s()


def complement_retire_from_start(t_start: float,
                                 defensive_runway_s: float = 0.0) -> float:
    """The terminal deadline, pulled EARLIER by the defensive runway.

    THE 0300z COORDINATION DEFECT LIVES HERE. `maybe_ts_deadline` retired a
    resting leg AT `cancel_cutoff_from_start` -- the binding terminal, T+600 at
    the deployed margin. `l5_defensive_completion` needs
    `DEFENSIVE_MIN_EXECUTION_S` (15s) BEFORE that terminal to run its sequence
    at all: cancel the complement, re-read the venue to confirm it is gone,
    then submit one order. So a complement still resting at T+600 was retired
    at the exact moment the defensive exit's own window had already shut, and
    the position went to settlement with no exit attempted. That is 0300z.

    Subtracting the runway makes the ordering structural:

        complement retired   <=  terminal - runway   <   terminal

    RETIRING EARLIER IS A TIGHTENING. It shortens how long a leg may rest, in
    the same direction `cancel_margin_s` may only widen and `place_floor_s`
    may only rise, so a runway that cannot be read is simply zero and the
    deadline falls back to the deployed one rather than becoming later.

    The runway is a PARAMETER, not an import: this module owns the window
    arithmetic and `l5_defensive_completion` owns the constant, and neither
    should have to import the other to agree.
    """
    try:
        r = float(defensive_runway_s)
    except (TypeError, ValueError):
        r = 0.0
    return cancel_cutoff_from_start(t_start) - max(0.0, r)


def window_end(t_start: float) -> float:
    """Window END from its START. BTC15m slugs name the start."""
    return float(t_start) + WINDOW_S


def cancel_cutoff_from_start(t_start: float) -> float:
    """The terminal deadline, taken from the WINDOW START.

    WHY THIS EXISTS RATHER THAN CALLERS DOING THE ARITHMETIC. `cancel_cutoff`
    takes the window END, but every slug and every book row names the window
    START, so a caller who passes what it has gets `start - 180` -- a
    deadline 180 seconds BEFORE the window opens, i.e. already expired on the
    first tick, which cancels the probe's legs the moment they rest. That is
    exactly the bug an independent review found in Runner.maybe_ts_deadline.
    Callers that hold a start must use this and never do the addition
    themselves.
    """
    return cancel_cutoff(window_end(t_start))


def place_cutoff(t_start: float, t_end: float) -> float:
    return min(float(t_start) + PLACE_OFFSET_S,
               cancel_cutoff(t_end) - MIN_REST_S)


def may_place(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow, else the refusal reason."""
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    floor = float(t_start) + place_floor_s()
    cutoff = place_cutoff(t_start, t_end)
    if floor >= cutoff:
        # a floor past the cutoff leaves no placement band at all. Say so
        # rather than silently never placing, which reads like a quiet market.
        return "PLACE_FLOOR_LEAVES_NO_BAND"
    if now < floor:
        return "BEFORE_PLACE_FLOOR"
    if now >= cutoff:
        return "PAST_PLACE_CUTOFF"
    return None


# ----------------------------------------------------------------- fees

def fee(theta: D, qty: D, price: D) -> D:
    """Theta * C * p * (1-p), rounded AWAY from zero to the cent.

    The venue bills commission in whole cents -- 107 of our own orders show
    not one sub-cent value -- so a cost is rounded UP and a credit is rounded
    DOWN in magnitude. Both are the conservative direction.
    """
    q = _d(qty) or D(0)
    p = _d(price) or D(0)
    raw = theta * q * p * (D(1) - p)
    if raw > 0:
        return raw.quantize(D("0.01"), rounding=ROUND_CEILING)
    if raw < 0:                      # a credit: keep the SMALLER magnitude
        return -((-raw).quantize(D("0.01")))
    return D(0)


def worst_fee(qty: D, price: D) -> D:
    return fee(TAKER_THETA, qty, price)


# ------------------------------------------------------- the economics

def net_settlement(*, up_qty: Any, dn_qty: Any,
                   up_price: Any, dn_price: Any,
                   theta: D = TAKER_THETA) -> Dict[str, Any]:
    """Net P&L on BOTH outcomes for an arbitrary, possibly ASYMMETRIC book.

    payout is `up_qty` if UP settles and `dn_qty` if DOWN settles, against one
    total executed cost. `both_positive` is the probe's whole objective.
    """
    a = _d(up_qty) or D(0); b = _d(dn_qty) or D(0)
    pu = _d(up_price) or D(0); pd = _d(dn_price) or D(0)
    cost = a * pu + b * pd + fee(theta, a, pu) + fee(theta, b, pd)
    up_pnl = a - cost
    dn_pnl = b - cost
    return {"cost": cost, "up_pnl": up_pnl, "dn_pnl": dn_pnl,
            "min_payout": min(a, b),
            "worst_outcome": min(up_pnl, dn_pnl),
            "both_positive": bool(up_pnl > 0 and dn_pnl > 0),
            "basis": ("payout is up_qty if UP settles and dn_qty if DOWN "
                      "settles; both_positive iff cost < min(up_qty, dn_qty)")}


def expected_value(*, matched_edge: Any, residual_alpha: Any,
                   p_first: Any, p_complete: Any) -> Dict[str, Any]:
    """EV per intended contract, over the four fill outcomes.

    `residual_alpha` is the measured LOSS per unmatched contract and is passed
    as a POSITIVE number. It is not variance: in L5's own BTC15m record the
    unmatched side won 39.5% of windows, so an unmatched leg is adversely
    selected, and a probe resting BEHIND the touch is selected harder still --
    it fills precisely when the market moves against it.
    """
    e = _d(matched_edge) or D(0)
    al = _d(residual_alpha) or D(0)
    pa = _d(p_first) or D(0)
    pb = _d(p_complete) or D(0)
    both = pa * pb
    one = pa * (D(1) - pb) + (D(1) - pa) * pb
    ev = both * e - one * al
    return {"ev": ev, "p_both": both, "p_one_leg": one,
            "from_matched": both * e, "from_residual": -(one * al)}


def min_completion_probability(*, matched_edge: Any,
                               residual_alpha: Any) -> Optional[D]:
    """The symmetric fill probability at which the probe breaks even.

    With p on both legs, EV = p^2 * e - 2p(1-p) * alpha, so break-even is
    p = 2*alpha / (e + 2*alpha). None when no probability suffices.
    """
    e = _d(matched_edge) or D(0)
    al = _d(residual_alpha) or D(0)
    if al <= 0:
        return D(0)
    denom = e + D(2) * al
    if denom <= 0:
        return None
    p = (D(2) * al) / denom
    return p if p <= 1 else None


# ------------------------------------------------- the two intents

def build_orders(*, market: str, bid: Any, ask: Any,
                 bid_depth: Any, ask_depth: Any,
                 tick: Any, min_qty: Any,
                 depth_ticks: int,
                 cash_envelope: Any,
                 max_worst_case_loss: Any,
                 budget_headroom: Any = None,
                 book_age_s: Any = None,
                 gap_suspected: bool = False) -> Dict[str, Any]:
    """Exactly two post-only intents, or a terminal refusal.

    NO FALLBACK. Every failure returns ok False and nothing is submitted. The
    module never degrades into a single leg, a crossing order, or the
    ordinary lambda-sized L5 decision -- a fallback would turn a measurement
    into a trade under an authorization granted for a measurement.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if not market or not active(market):
        return no("NOT_THE_AUTHORIZED_MARKET")
    if gap_suspected:
        return no("GAP_SUSPECTED")
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        return no("BOOK_STALE")
    b = _d(bid); a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return no("BOOK_NOT_TWO_SIDED")
    if a <= b:
        return no("BOOK_CROSSED_OR_LOCKED")
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    if depth_ticks is None or int(depth_ticks) < 0:
        return no("DEPTH_TICKS_UNKNOWN")
    env = _d(cash_envelope); cap = _d(max_worst_case_loss)
    if env is None or env <= 0:
        return no("CASH_ENVELOPE_UNKNOWN")
    if cap is None or cap <= 0:
        return no("LOSS_CEILING_UNKNOWN")
    # THE CAMPAIGN BUDGET IS A THIRD BINDING CONSTRAINT, and it has to be
    # applied HERE for the same reason the cash basis already is -- see the
    # `oblig_per_contract` comment below, which records this module learning
    # the identical lesson one gate earlier. Sizing against the cash gate
    # alone built a 3.36-contract plan whose $4.6368 obligation fit the
    # $4.65 cash envelope and then had to be refused by the $11.10 campaign
    # budget at 13.5356 > 11.10, because $8.8988 of account-wide realized
    # loss already stood against it. The planner must not emit a plan its
    # own gates must reject; a refusal that the planner could have avoided
    # spends a window reservation for nothing.
    #
    # None means NOT SUPPLIED and is passed through unconstrained, so a
    # caller that does not run a campaign is unaffected. A supplied value of
    # zero or less is EXHAUSTED and refuses outright rather than sizing to
    # nothing -- "no budget" and "a tiny budget" are different answers.
    head = _d(budget_headroom)
    if budget_headroom is not None and (head is None or head <= 0):
        return no("CAMPAIGN_BUDGET_EXHAUSTED",
                  budget_headroom_usd=(None if head is None
                                       else str(head)))

    dsteps = D(int(depth_ticks)) * tk
    up_px = b - dsteps                     # rest BEHIND the UP bid
    dn_px = (D(1) - a) - dsteps            # and behind the DOWN bid
    for px in (up_px, dn_px):
        if px < MIN_PRICE or px > MAX_PRICE:
            return no("PRICE_OUT_OF_BAND", up_price=str(up_px),
                      dn_price=str(dn_px))
        if (px / tk) != (px / tk).to_integral_value():
            return no("PRICE_NOT_ON_TICK", price=str(px))

    pair = up_px + dn_px
    cap_px = max_pair_cost()
    if pair > cap_px:
        return no("PAIR_COST_ABOVE_CAP", pair_cost=str(pair),
                  max_pair_cost=str(cap_px))
    floor = fee_floor(up_px, dn_px)
    edge = floor["edge"]
    if not floor["ok"]:
        return no("EDGE_BELOW_FEE_FLOOR", pair_cost=str(pair),
                  edge=str(edge),
                  net_if_maker=str(floor["net_if_maker"]),
                  net_if_taker=str(floor["net_if_taker"]),
                  required="gross edge >= %s and net_if_maker > 0"
                           % MIN_GROSS_EDGE)

    # size: the WORST single-leg fill must stay inside the loss ceiling, and
    # the gross cash must stay inside the envelope. The dearer leg binds.
    dear = max(up_px, dn_px)
    by_loss = cap / dear
    # SIZE AGAINST THE GATE'S OWN CASH BASIS, not the pair cost. The cash
    # gate prices a resting DOWN leg (a BUY_SHORT) at a DOLLAR A SHARE, not
    # at its limit price, because a short owes $1 if YES settles 1. Sizing on
    # `pair` would therefore build a plan the gate then refuses -- which is
    # exactly what happened the first time this was wired. The conservative
    # accounting is preserved and respected, not worked around.
    oblig_per_contract = D(1) + up_px
    # SPLIT THE ENVELOPE ACROSS CYCLES, if configured. Default 1 leaves this
    # line arithmetically identical to `env / oblig_per_contract`. The divisor
    # touches the CASH envelope only -- the constraint that actually bound
    # every plan of the 20261024a campaign -- and never the loss ceiling or
    # the campaign budget, which had headroom to spare and would then refuse
    # plans for the wrong reason.
    allocs = round_allocations()
    env_per_round = env / allocs
    by_cash = env_per_round / oblig_per_contract
    # THE OPENING-LEG CONTRACT CEILING, intersected with the dollar bounds.
    # Price-independent, because the dollar bounds are not: qty grows as
    # up_px falls and no approved cash figure bounds it across eligible
    # prices. See OPENING_LEG_MAX_CONTRACTS.
    by_opening_cap = opening_leg_max_contracts()
    qty = min(by_loss, by_cash, by_opening_cap)
    # THE WORST-CASE OPENING NOTIONAL, on the dearer leg -- the one whose
    # solo fill is the strand. Inert when unconfigured. A budget that cannot
    # fund even one grid step is refused HERE and by name, rather than
    # surfacing later as a generic "no legal quantity".
    notional_budget = opening_notional_budget_usd()
    by_notional = None
    if notional_budget is not None:
        if mq * dear > notional_budget:
            return no("OPENING_NOTIONAL_CAP_BELOW_VENUE_MINIMUM",
                      opening_notional_budget_usd=str(notional_budget),
                      min_qty=str(mq), dearer_leg_own_px=str(dear),
                      min_notional_usd=str(mq * dear),
                      labels=("the venue minimum is one grid step; a "
                              "worst-case opening budget that cannot fund "
                              "even that cannot open a leg at this price, "
                              "and says so rather than sizing to zero"))
        by_notional = notional_budget / dear
        qty = min(qty, by_notional)
    # and the campaign budget, on that SAME obligation basis, because
    # `ValidationGates` gate 3b charges the order exactly as the cash gate
    # above does -- a resting BUY_SHORT at a dollar a share.
    by_budget = None
    if head is not None:
        by_budget = head / oblig_per_contract
        qty = min(qty, by_budget)
    # quantise DOWN to the venue grid, never up
    qty = (qty / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    # THEN SHRINK UNTIL THE FEE FITS TOO. Sizing on price alone overshoots,
    # because the worst single-leg fill costs qty*price PLUS its fee, and a
    # loss ceiling that the fee can step over is not a ceiling. One grid
    # step per iteration, bounded, and the refusal stands if nothing fits.
    for _ in range(64):
        if qty < mq:
            break
        if (qty * dear + worst_fee(qty, dear) <= cap
                and qty * oblig_per_contract <= env_per_round
                and (notional_budget is None or qty * dear <= notional_budget)
                and (head is None or qty * oblig_per_contract <= head)):
            break
        qty -= mq
    if qty < mq:
        return no("NO_LEGAL_QUANTITY", by_loss=str(by_loss),
                  by_cash=str(by_cash),
                  by_budget=(None if by_budget is None else str(by_budget)),
                  budget_headroom_usd=(None if head is None else str(head)),
                  min_qty=str(mq),
                  obligation_per_contract=str(oblig_per_contract),
                  round_allocations=allocs,
                  opening_leg_max_contracts=str(by_opening_cap),
                  opening_notional_budget_usd=(
                      None if notional_budget is None else str(notional_budget)),
                  by_notional=(None if by_notional is None else str(by_notional)),
                  cash_envelope_total_usd=str(env),
                  cash_envelope_per_round_usd=str(env_per_round),
                  binding=("CAMPAIGN_BUDGET"
                           if by_budget is not None
                           and by_budget <= min(by_loss, by_cash)
                           else "CASH_ENVELOPE"
                           if by_cash <= by_loss else "LOSS_CEILING"),
                  labels=("the venue minimum is one grid step; a budget "
                          "that cannot fund even that is reported as the "
                          "binding constraint rather than as a book "
                          "problem"))
    if qty * dear + worst_fee(qty, dear) > cap:
        return no("LOSS_CEILING_UNREACHABLE", qty=str(qty))
    bd = _d(bid_depth); ad = _d(ask_depth)
    if bd is None or ad is None:
        return no("NO_DISPLAYED_DEPTH")

    # ACCEPTANCE IS ON THE EXECUTION THIS PROBE ACTUALLY USES. Both legs are
    # post-only, so the maker coefficient applies here exactly as it does in
    # fee_floor; evaluating acceptance under taker fees while the floor used
    # maker fees would have contradicted itself, and did.
    econ = net_settlement(up_qty=qty, dn_qty=qty, up_price=up_px,
                          dn_price=dn_px, theta=MAKER_THETA)
    # the worst case is still COMPUTED and reported, never hidden
    econ_taker = net_settlement(up_qty=qty, dn_qty=qty, up_price=up_px,
                                dn_price=dn_px, theta=TAKER_THETA)
    if not econ["both_positive"]:
        return no("NOT_POSITIVE_ON_BOTH_OUTCOMES",
                  up_pnl=str(econ["up_pnl"]), dn_pnl=str(econ["dn_pnl"]))

    def leg(side: str, px: D) -> Dict[str, Any]:
        # DEPTH IS RECORDED ON THE LEG, not left to be re-derived later. The
        # depth comparison read 7,310 order records off the production tape
        # and attributed NOT ONE of them, because nothing wrote down how far
        # behind the touch each order rested and the touch at placement was
        # not recoverable from the record. A measurement whose input is not
        # recorded cannot be taken.
        return {"side": side, "action": "BUY", "price": str(px),
                "qty": str(qty), "mode": MODE, "post_only": True,
                "depth_ticks": int(depth_ticks),
                "touch_at_placement": str(b if side == "UP" else D(1) - a),
                "worst_case_fee_usd": str(worst_fee(qty, px)),
                "alone_cost_usd": str(qty * px + worst_fee(qty, px))}

    return {"ok": True, "market": market,
            "depth_ticks": int(depth_ticks),
            "legs": [leg("UP", up_px), leg("DOWN", dn_px)],
            "pair_cost": str(pair), "edge_per_contract": str(edge),
            "fee_floor": {k: (str(v) if isinstance(v, D) else v)
                          for k, v in floor.items()},
            "qty_per_leg": str(qty),
            "gross_cash_usd": str(econ["cost"]),
            "cash_gate_obligation_usd": str(qty * oblig_per_contract),
            "round_allocations": allocs,
            "opening_leg_max_contracts": str(by_opening_cap),
            "opening_leg_cap_binds": bool(by_opening_cap <= min(by_loss,
                                                                by_cash)),
            "opening_notional_budget_usd": (
                None if notional_budget is None else str(notional_budget)),
            "opening_notional_contracts": (
                None if by_notional is None else str(by_notional)),
            "opening_notional_binds": bool(
                by_notional is not None
                and by_notional <= min(by_loss, by_cash, by_opening_cap)),
            "worst_case_opening_notional_usd": str(qty * dear),
            "opening_notional_basis": (
                "the dearer leg is the one whose solo fill is the strand, so "
                "the cap is qty * dear and the worst single-leg loss is the "
                "budget by construction. Opening plans only: completing held "
                "inventory is sized elsewhere and is never throttled here."),
            "cash_envelope_total_usd": str(env),
            "cash_envelope_per_round_usd": str(env_per_round),
            "round_allocation_basis": (
                "the cash envelope is split across this many sequential "
                "cycles. 1 is the historical behaviour. It only ever shrinks "
                "a round: aggregate exposure stays bounded account-wide by "
                "the $2 ceiling, the residual cap and both loss halts, each "
                "counting filled inventory plus still-fillable outstanding "
                "orders. Matched inventory does NOT release cash before "
                "settlement."),
            "cash_basis": ("a resting DOWN leg is a BUY_SHORT and the cash "
                           "gate charges it a dollar a share; sizing uses "
                           "that basis so the plan the gate sees is the "
                           "plan this built"),
            "budget_headroom_usd": (None if head is None else str(head)),
            # THE LABEL NAMES THE ACTUAL SMALLEST BOUND, by argmin rather
            # than by a fixed precedence. The precedence version reported
            # CAMPAIGN_BUDGET on a $100 envelope while the opening-leg cap was
            # the term that actually sized the plan -- a label that names the
            # wrong constraint is worse than no label, because Priority 4 of
            # the owner's brief is exactly "report which constraint actually
            # bound, not merely which configuration limit exists".
            "sized_by": _sized_by(by_loss=by_loss, by_cash=by_cash,
                                  by_budget=by_budget,
                                  by_opening_cap=by_opening_cap,
                                  by_notional=by_notional),
            "budget_basis": ("the campaign budget is charged on the same "
                             "dollar-a-share obligation as the cash gate, "
                             "so the smaller of the two binds and the plan "
                             "this emits is one BOTH gates accept"),
            "up_pnl": str(econ["up_pnl"]), "dn_pnl": str(econ["dn_pnl"]),
            "up_pnl_if_taker": str(econ_taker["up_pnl"]),
            "dn_pnl_if_taker": str(econ_taker["dn_pnl"]),
            "both_positive_if_taker": bool(econ_taker["both_positive"]),
            "worst_single_leg_loss_usd": str(
                -(qty * dear + worst_fee(qty, dear))),
            "rebate_upside_usd": str(
                -(fee(MAKER_THETA, qty, up_px)
                  + fee(MAKER_THETA, qty, dn_px))),
            "labels": ("displayed prices are NOT fills. Both legs may rest "
                       "unfilled, one may fill alone, and either may fill "
                       "partially; the single-leg loss above is the bound "
                       "that matters.")}


def top_up(*, up_qty: Any, dn_qty: Any, up_price: Any, dn_price: Any,
           extra_side: str, extra_qty: Any, extra_price: Any
           ) -> Dict[str, Any]:
    """Would extra contracts on one side improve the WORST outcome?

    Allowed only when it does. Extra on the side that is already the maximum
    adds cost without lifting `min(a, b)`, which is strictly worse -- so this
    is the asymmetric case stated as a predicate rather than a hope.
    """
    a = _d(up_qty) or D(0); b = _d(dn_qty) or D(0)
    before = net_settlement(up_qty=a, dn_qty=b,
                            up_price=up_price, dn_price=dn_price)
    side = str(extra_side).upper()
    if side not in ("UP", "DOWN"):
        return {"ok": False, "reason": "EXTRA_SIDE_UNKNOWN"}
    eq = _d(extra_qty) or D(0)
    ep = _d(extra_price)
    if eq <= 0 or ep is None or ep <= 0 or ep >= D(1):
        return {"ok": False, "reason": "EXTRA_UNREADABLE"}
    # blended cost on the topped-up side
    if side == "UP":
        na = a + eq
        nc_u = (a * (_d(up_price) or D(0)) + eq * ep) / na if na else D(0)
        after = net_settlement(up_qty=na, dn_qty=b,
                               up_price=nc_u, dn_price=dn_price)
    else:
        nb = b + eq
        nc_d = (b * (_d(dn_price) or D(0)) + eq * ep) / nb if nb else D(0)
        after = net_settlement(up_qty=a, dn_qty=nb,
                               up_price=up_price, dn_price=nc_d)
    better = after["worst_outcome"] > before["worst_outcome"]
    return {"ok": bool(better),
            "reason": None if better else "TOP_UP_WORSENS_WORST_OUTCOME",
            "worst_before": before["worst_outcome"],
            "worst_after": after["worst_outcome"],
            "up_pnl_after": after["up_pnl"], "dn_pnl_after": after["dn_pnl"],
            "both_positive_after": after["both_positive"]}


# ------------------------------------------- patient opposite-side completion

#: how close to the terminal deadline completion may be CONSIDERED. Before
#: this the maker leg is still resting and still the better price, so there is
#: nothing to decide; crossing early throws away the edge the maker quote was
#: placed for.
COMPLETION_WINDOW_S = 120.0


def completion_phase(now: float, t_start: float) -> str:
    """MAKER while there is time, COMPLETION near the deadline, else LATE.

    Three named phases rather than a boolean, because "near the deadline" and
    "past the deadline" need different answers: at the cutoff nothing may
    rest, so a completion that has not been decided by then is not decided at
    all.
    """
    cut = cancel_cutoff_from_start(float(t_start))
    if float(now) >= cut:
        return "LATE"
    if float(now) >= cut - COMPLETION_WINDOW_S:
        return "COMPLETION"
    return "MAKER"


def completion_decision(*, up_qty: Any, dn_qty: Any,
                        up_cost_px: Any, dn_cost_px: Any,
                        taker_price: Any, available_depth: Any,
                        tick: Any, min_qty: Any,
                        cash_headroom: Any, worst_case_ceiling: Any,
                        book_age_s: Any = None,
                        gap_suspected: bool = False) -> Dict[str, Any]:
    """Should we CROSS to complete the deficient side? Economics only.

    THE DEADLINE IS NOT A REASON. This never returns ok True because time is
    short. It returns ok True only when crossing at the price actually
    executable, paying the actual taker fee, STRICTLY IMPROVES the worst of
    the two settlement outcomes, and fits inside the cash headroom and the
    worst-case ceiling we are already authorized for. Every other case is a
    refusal with its reason, and the caller's answer to a refusal is to
    cancel the unfilled order and carry the residual under the existing
    controls -- not to cross anyway.

    WHY IT CAN BEAT HOLDING. A one-sided book bought at `c` loses `c` on the
    wrong outcome. Completing at `q` makes the pair cost `c + q`, which pays
    `min(a, b)` on BOTH outcomes; whenever `c + q + fee < min(a, b)` after the
    completion that is a smaller worst case than holding, even though `q` is
    a crossing price and strictly worse than the maker quote we wanted.

    PARTIAL COMPLETION IS ALLOWED AND IS THE NORMAL CASE. Displayed depth
    rarely covers the whole deficit. A partial fill of the deficient side
    still lifts `min(a, b)`, so the size is clipped to the depth, the cash and
    the ceiling, and judged on what that clipped size actually does.

    PRICES ARE OWN-OUTCOME. `taker_price` is what a share of the DEFICIENT
    side costs to buy right now -- for DOWN that is `1 - up_bid`, not the UP
    ask -- and the caller owns that arithmetic because only the caller has the
    book.

    NOTHING HERE SUBMITS, CANCELS OR SETTLES ANYTHING.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason, "cross": False}, **extra)

    a = _d(up_qty); b = _d(dn_qty)
    if a is None or b is None or a < 0 or b < 0:
        return no("INVENTORY_UNREADABLE")
    if a == 0 and b == 0:
        return no("NO_INVENTORY_TO_COMPLETE")
    if a == b:
        return no("ALREADY_MATCHED", matched=str(a))
    side = "DOWN" if a > b else "UP"
    deficit = abs(a - b)

    if gap_suspected:
        return no("GAP_SUSPECTED", side=side, deficit=str(deficit))
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        # A STALE BOOK IS NOT AN EXECUTABLE PRICE. Crossing on a price we
        # cannot date is how a "completion" becomes a worse one-sided book.
        return no("BOOK_STALE", side=side, book_age_s=None if age is None
                  else str(age))
    q = _d(taker_price)
    if q is None or q <= 0 or q >= D(1):
        return no("COMPLETION_PRICE_UNREADABLE", side=side)
    if q < MIN_PRICE or q > MAX_PRICE:
        return no("COMPLETION_PRICE_OUT_OF_BAND", side=side, price=str(q))
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    if (q / tk) != (q / tk).to_integral_value():
        return no("COMPLETION_PRICE_NOT_ON_TICK", side=side, price=str(q))
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    depth = _d(available_depth)
    if depth is None:
        # NO DISPLAYED DEPTH IS NOT INFINITE DEPTH.
        return no("NO_DISPLAYED_DEPTH", side=side)
    cash = _d(cash_headroom)
    cap = _d(worst_case_ceiling)
    if cash is None or cash < 0:
        return no("CASH_HEADROOM_UNKNOWN", side=side)
    if cap is None or cap <= 0:
        return no("WORST_CASE_CEILING_UNKNOWN", side=side)

    # SIZE: the deficit, clipped by displayed depth, by the cash this costs,
    # and then shrunk on the venue grid until the fee fits too.
    qty = min(deficit, depth, cash / q if q > 0 else D(0))
    qty = (qty / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    for _ in range(64):
        if qty < mq:
            break
        if qty * q + worst_fee(qty, q) <= cash:
            break
        qty -= mq
    if qty < mq:
        return no("NO_LEGAL_COMPLETION_QUANTITY", side=side,
                  deficit=str(deficit), depth=str(depth),
                  cash_headroom=str(cash))

    # THE DECISION, through the EXISTING predicate. top_up already answers
    # "does extra on this side improve the worst outcome", at taker fees, so
    # the completion is judged by the same arithmetic as any other top-up
    # rather than by a second opinion written for the deadline.
    tu = top_up(up_qty=a, dn_qty=b, up_price=up_cost_px, dn_price=dn_cost_px,
                extra_side=side, extra_qty=qty, extra_price=q)
    if not tu.get("ok"):
        return no(str(tu.get("reason") or "TOP_UP_WORSENS_WORST_OUTCOME"),
                  side=side, qty=str(qty), price=str(q),
                  worst_before=str(tu.get("worst_before")),
                  worst_after=str(tu.get("worst_after")),
                  labels=("crossing would NOT improve the worst settlement "
                          "outcome, so the deadline is not a reason to do "
                          "it. Cancel the unfilled order and carry the "
                          "residual under the existing controls."))

    # AND THE CROSS MUST BUY MORE THAN IT COSTS.
    #
    # WHY THIS SECOND BAR IS NEEDED, measured rather than assumed. "Improves
    # the worst outcome" alone is very nearly VACUOUS once a book is
    # one-sided: holding loses the leg's whole cost on the wrong outcome, so
    # every completion priced below a dollar reduces that. On a 5-share UP
    # leg at 0.59 the predicate passes at 0.35, 0.45, 0.60, 0.80, 0.90 AND
    # 0.95 -- so a rule written only on worst-case improvement would cross at
    # essentially any price the book showed, which is not an economic
    # decision.
    #
    # WHAT COMPLETION ACTUALLY COSTS. At the market's own price the cross is
    # fair value, so in expectation it costs exactly the TAKER FEE and buys
    # the removal of all settlement variance. This bar says that purchase
    # must be worth its price: the worst case must improve by MORE than the
    # fee paid for the improvement.
    taker_fee = worst_fee(qty, q)
    improvement = tu["worst_after"] - tu["worst_before"]
    if improvement <= taker_fee:
        return no("IMPROVEMENT_BELOW_ITS_OWN_FEE", side=side, qty=str(qty),
                  price=str(q), improvement_usd=str(improvement),
                  taker_fee_usd=str(taker_fee),
                  labels=("the cross would cost more in fee than it buys in "
                          "worst-case improvement. In expectation a "
                          "completion at the market's own price costs "
                          "exactly the taker fee and buys the removal of "
                          "settlement variance; this one does not pay for "
                          "itself."))

    # AND IT MUST STILL FIT THE CEILING WE ARE AUTHORIZED FOR. An improvement
    # that breaches the worst-case bound is not permission to breach it.
    worst_after = tu["worst_after"]
    if worst_after < -cap:
        return no("COMPLETION_EXCEEDS_WORST_CASE_CEILING", side=side,
                  qty=str(qty), price=str(q), worst_after=str(worst_after),
                  ceiling=str(cap))

    partial = bool(qty < deficit)
    return {"ok": True, "cross": True, "side": side, "action": "BUY",
            "qty": str(qty), "price": str(q), "mode": FT.TAKER,
            "post_only": False,
            "deficit": str(deficit), "partial": partial,
            "displayed_depth": str(depth),
            "taker_fee_usd": str(taker_fee),
            "cash_cost_usd": str(qty * q + worst_fee(qty, q)),
            "worst_before": str(tu["worst_before"]),
            "worst_after": str(worst_after),
            "worst_improvement_usd": str(worst_after - tu["worst_before"]),
            "up_pnl_after": str(tu["up_pnl_after"]),
            "dn_pnl_after": str(tu["dn_pnl_after"]),
            "both_positive_after": bool(tu["both_positive_after"]),
            "labels": ("a CROSSING order, priced at the displayed executable "
                       "price and charged the TAKER fee. It is proposed only "
                       "because it improves the worst settlement outcome, "
                       "never because the deadline arrived. Displayed depth "
                       "is not a fill: the size may fill partially or not at "
                       "all, and a refusal to fill leaves the residual the "
                       "existing controls already bound.")}


# ===================================================== the execution lifecycle
#
# WHAT THE 0330z WINDOW SHOWED. The plan is frozen per round -- rightly:
# re-planning on every tick produced legs of DIFFERENT sizes (1.89 against
# 0.98 in test), which is not a matched pair at all. But a frozen plan was
# never RETIRED either, so when the book left it behind the engine proposed
# the same unreachable pair 32 times in one window, and the complement's price
# came from planning time however far the market had moved.
#
# These two functions are the whole remedy and they are PURE: one says whether
# a frozen plan is still executable against the book in front of us, the other
# prices a complement against that book and the cost we ACTUALLY paid. Neither
# reads a file, a clock or an environment variable beyond the tick knobs, and
# neither can submit, cancel or claim anything.

#: how far the book may move away from a frozen leg before its round is
#: retired, in ticks. Measured per leg, against where that leg would be
#: priced right now.
#:
#: WHY A DRIFT TEST AND NOT ONLY A CROSSING TEST. A crossing leg is rejected
#: by the venue outright, so it is unambiguously dead. A leg that has merely
#: been left behind is still a legal resting order -- it simply cannot fill,
#: because the price has to travel back to it, and `cross_price_depth` is
#: cleared by WITHDRAWAL, which trade volume cannot measure. Holding it is not
#: unsafe; it just spends the window. Six ticks is wider than the 2-tick
#: placement offset and well inside the 51-cent move that exposed this.
STALE_DRIFT_TICKS = 6
STALE_DRIFT_ENV = "L5_TS_STALE_DRIFT_TICKS"

# ===================================== RECOVERING A RESTING FIRST LEG
#
# THE GAP THIS CLOSES, measured live. `plan_executable` detects a stale plan,
# but `retire_blocked_by` refuses to retire a round while ANY order is live --
# correctly, because an order we may still own must be cancelled by its own
# id and proved terminal first. So while a first leg RESTS with zero fills,
# nothing but the 120-second age deadline can rescue it, however far the book
# has travelled.
#
# 2200z on 2026-10-23 is the case. A DOWN first leg was placed at own 0.2100
# against an own-side touch of 0.2300 -- correctly two ticks behind. The book
# then left it:
#
#     22:01:45  wire 0.7600/0.7700   own touch 0.23    2 ticks behind
#     22:02:17  wire 0.5600/0.5700   own touch 0.43   22 ticks behind
#     22:02:50  wire 0.7400/0.7500   own touch 0.25    4 ticks behind
#     22:03:23  wire 0.7000/0.7100   own touch 0.29    8 ticks behind
#
# It rested unfillable for 56 seconds, was cancelled at zero fill by the age
# deadline at 22:03:46, re-quoted two ticks from the NEW touch at own 0.2900,
# and filled in 15 seconds. The 56 seconds were pure loss out of a 540-second
# entry budget.
#
# WHY RE-QUOTING IS NOT CHASING. The replacement is priced by the same
# `quote_own_price` rule against a fresh book -- two ticks BEHIND the current
# touch, post-only, pair cap re-tested. It never moves to the touch and never
# crosses. The one live precedent is 2145z, where a superseded complement was
# re-derived from the current book at own 0.5600 instead of the frozen 0.6000:
# the pair improved from 0.95 to 0.91 and settled +$0.0927. Re-pricing against
# a current book has been MORE profitable, not less.
#
# FOUR TICKS, owner-approved 2026-10-23. Wider than the 2-tick placement
# offset, so an order is never recovered for sitting where it was put, and
# narrower than STALE_DRIFT_TICKS, which governs retiring a plan nothing is
# resting on.
STALE_RECOVER_DRIFT_TICKS = 4
STALE_RECOVER_DRIFT_ENV = "L5_TS_RECOVER_DRIFT_TICKS"

#: and the floor on how long a leg must have rested before drift may cancel
#: it. A book that flickers one tick for a moment must not be able to churn
#: an order that is about to fill: every live first-leg fill so far landed in
#: 9 to 15 seconds, so anything inside that is still working.
STALE_RECOVER_MIN_REST_S = 15.0
STALE_RECOVER_MIN_REST_ENV = "L5_TS_RECOVER_MIN_REST_S"

#: and the churn bound: how many drift recoveries one market may spend.
#:
#: ONE, lowered from 3 on review of 2cc57bed against the real OPEN budget.
#: The 8-window campaign of 2026-10-23 spent 8 OPEN submissions against a
#: ceiling of 32, so a cap of 3 would have permitted 8 * (1 + 3) = 32 --
#: exactly the whole budget, leaving nothing for a legitimate opening in a
#: later window. The code declines a recovery when OPEN has nothing left, so
#: that fails safe rather than overspending, but "fails safe by refusing to
#: trade" is a window lost. At 1 the worst case is 8 * 2 = 16 of 32.
#:
#: One is also what the evidence asks for: 2200z needed exactly one
#: recovery, and each one costs an OPEN submission and surrenders queue
#: priority. This is a hard ceiling, not a target.
STALE_RECOVER_MAX_PER_MARKET = 1
STALE_RECOVER_MAX_ENV = "L5_TS_RECOVER_MAX_PER_MARKET"

#: the book backing a recovery must be THIS fresh. `snapshot()` already
#: returns None unless the socket is connected and two-sided; this bounds how
#: old the last frame may be, because cancelling a live order against a stale
#: book is how a recovery becomes a mistake.
STALE_RECOVER_MAX_BOOK_AGE_S = 5.0


def recover_drift_ticks() -> int:
    """The drift that justifies recovering a RESTING leg.

    Configurable LAZIER only. A LOWER number recovers sooner and churns more,
    so an unreadable or smaller value falls back to the approved 4 rather
    than becoming more aggressive than the owner authorized.
    """
    raw = (os.getenv(STALE_RECOVER_DRIFT_ENV) or "").strip()
    try:
        v = int(raw) if raw else STALE_RECOVER_DRIFT_TICKS
    except ValueError:
        return STALE_RECOVER_DRIFT_TICKS
    return max(STALE_RECOVER_DRIFT_TICKS, v)


def recover_min_rest_s() -> float:
    """Configurable LONGER only, for the same reason."""
    raw = (os.getenv(STALE_RECOVER_MIN_REST_ENV) or "").strip()
    try:
        v = float(raw) if raw else STALE_RECOVER_MIN_REST_S
    except ValueError:
        return STALE_RECOVER_MIN_REST_S
    if v != v or v in (float("inf"), float("-inf")):
        return STALE_RECOVER_MIN_REST_S
    return max(STALE_RECOVER_MIN_REST_S, v)


def recover_max_per_market() -> int:
    """Configurable SMALLER only: a wider churn budget is the unsafe way."""
    raw = (os.getenv(STALE_RECOVER_MAX_ENV) or "").strip()
    try:
        v = int(raw) if raw else STALE_RECOVER_MAX_PER_MARKET
    except ValueError:
        return STALE_RECOVER_MAX_PER_MARKET
    return max(0, min(STALE_RECOVER_MAX_PER_MARKET, v))


def depth_ticks() -> int:
    """How far behind its touch a maker leg is quoted, in ticks.

    THE SAME READ the two money-path call sites make inline --
    `int(os.getenv("L5_TS_DEPTH_TICKS", "2"))` in l5_live_execution -- so
    this is one name for the convention they already use, not a second
    policy. It differs in one way, deliberately: an unparseable value falls
    back to 2 instead of raising, because this accessor is consulted from a
    recovery path that must never be able to take down the deadline that
    bounds real exposure.
    """
    raw = (os.getenv("L5_TS_DEPTH_TICKS") or "").strip()
    try:
        v = int(raw) if raw else 2
    except ValueError:
        return 2
    return v if v >= 0 else 2


def row_is_zero_fill(row: Optional[Mapping[str, Any]]) -> bool:
    """Does this durable live-order row prove NOTHING has filled on it?

    True only for a row that is present, in the LIVE state, and still
    carrying its full submitted quantity. `apply_order_progress` reduces
    `remaining` and sets PARTIALLY_FILLED on any fill, and a fully filled row
    is popped entirely -- so an ABSENT row is not zero-filled, it is terminal
    and possibly filled.

    Every other case is False, including an unreadable quantity: an unknown
    is never a zero when the question is whether money has moved.
    """
    if not row:
        return False
    if str(row.get("state") or "").strip().upper() != "LIVE":
        return False
    q, rem = _d(row.get("qty")), _d(row.get("remaining"))
    return q is not None and rem is not None and q > 0 and rem == q


def recover_stale_leg(*, side: str, own_px: Any, qty: Any, remaining: Any,
                      state: Any, submitted_t: Any, now: Any,
                      bid: Any, ask: Any, tick: Any, depth_ticks: int,
                      book_age_s: Any,
                      recoveries_used: int,
                      open_budget_remaining: Any = None) -> Dict[str, Any]:
    """Should this RESTING leg be cancelled now and re-quoted? Read-only.

    Returns {"recover": bool, "reason": str, ...}. `recover` is True only
    when every condition below holds; each failure is named, so the logs say
    which one declined rather than going quiet.

    PRICES ARE OWN-SIDE THROUGHOUT. `own_px` is the order's own-terms limit
    -- which is what `record_submit` stores, because `l5_order_body.build`
    takes own terms and converts with `venue_price`. The comparison is
    against `quote_own_price`, also own-terms, so no wire conversion happens
    here and none is needed.

    THE SIGN MATTERS, and it is the whole of requirement five. Drift is
    `quote_own_price(now) - own_px`. POSITIVE means the fair quote has moved
    ABOVE where we rest: the touch walked away and we are stranded behind it.
    NEGATIVE OR ZERO means we are already at or better than where we would
    quote today -- our order became MORE competitive -- and that is never a
    reason to cancel. This holds for both sides because everything is in own
    terms: for DOWN, a falling own price IS a receding touch.

    IT DECIDES NOTHING ABOUT SAFETY. The caller must still poll the venue
    first, confirm zero cumulative fill on the authoritative read, cancel by
    exact venue order id, and prove the cancel terminal before replacing.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, recover=False, reason=reason)

    # 1. ZERO CONFIRMED FILLS, from the durable row. `apply_order_progress`
    #    reduces `remaining` and sets PARTIALLY_FILLED on any fill, so a row
    #    still carrying its full quantity in the LIVE state is the only one
    #    that can be abandoned without stranding inventory. Unreadable is
    #    NOT zero.
    q, rem = _d(qty), _d(remaining)
    if q is None or rem is None:
        return no("FILL_STATE_UNREADABLE", qty=str(qty), remaining=str(remaining))
    if str(state or "").strip().upper() != "LIVE" or rem != q:
        return no("NOT_ZERO_FILL", state=str(state), qty=str(q),
                  remaining=str(rem))

    # 2. A BOOK WORTH ACTING ON. A missing, crossed or locked book judges
    #    nothing, and a stale one judges it wrongly.
    tk, b, a = _d(tick), _d(bid), _d(ask)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    if b is None or a is None or b <= 0 or a <= 0 or a <= b:
        return no("BOOK_UNUSABLE", bid=str(bid), ask=str(ask))
    age = _d(book_age_s)
    if age is None or age < 0 or age > _d(STALE_RECOVER_MAX_BOOK_AGE_S):
        return no("BOOK_STALE", book_age_s=str(book_age_s),
                  max_book_age_s=str(STALE_RECOVER_MAX_BOOK_AGE_S))

    # 3. IT MUST HAVE HAD ITS CHANCE. Every live first-leg fill so far
    #    arrived within 15 seconds; cancelling inside that is churning an
    #    order that is still working, and it throws away queue priority.
    t_now, t_sub = _d(now), _d(submitted_t)
    if t_now is None or t_sub is None:
        return no("REST_TIME_UNREADABLE")
    rest = t_now - t_sub
    if rest < _d(recover_min_rest_s()):
        return no("TOO_YOUNG", rest_s=str(rest),
                  min_rest_s=str(recover_min_rest_s()))

    own = _d(own_px)
    if own is None:
        return no("LEG_PRICE_UNREADABLE")
    now_px = quote_own_price(side, bid=b, ask=a, tick=tk,
                             depth_ticks=depth_ticks)
    if now_px is None:
        return no("TOUCH_UNREADABLE")

    # 4. AWAY, NOT TOWARDS.
    drift = now_px - own
    if drift <= 0:
        return no("NOT_DRIFTED_AWAY", own_px=str(own),
                  would_quote_now=str(now_px), drift_ticks=str(drift / tk),
                  note=("the resting price is at or better than today's "
                        "quote, so the order became more competitive; that "
                        "is never a reason to cancel it"))
    ticks = drift / tk
    allow = _d(recover_drift_ticks())
    if ticks <= allow:
        return no("WITHIN_TOLERANCE", own_px=str(own),
                  would_quote_now=str(now_px), drift_ticks=str(ticks),
                  allowed_ticks=str(allow))

    # 5. CHURN AND BUDGET. Cancelling without the capacity to re-place is
    #    strictly worse than leaving the order resting, so an exhausted OPEN
    #    budget declines the recovery rather than spending the cancel.
    if int(recoveries_used) >= recover_max_per_market():
        # THE REFUSAL CARRIES THE STATE THAT QUALIFIED IT. Every other test
        # has already passed by this line -- zero fill, readable tick, usable
        # and fresh book, rest past the floor, drift past tolerance -- so a
        # leg refused here is one the budget ALONE declined. Reporting only
        # `used` made that indistinguishable from "nothing drifted", and the
        # count of legs that would have qualified is exactly the quantity
        # needed to judge whether the cap of 1 is the right number. These are
        # the values already computed above; nothing new is measured and no
        # decision changes.
        return no("RECOVERY_BUDGET_SPENT", used=int(recoveries_used),
                  max_per_market=recover_max_per_market(),
                  side=str(side), own_px=str(own),
                  would_quote_now=str(now_px), drift_ticks=str(ticks),
                  allowed_ticks=str(allow), rest_s=str(rest),
                  book_age_s=str(age), qualified=True)
    obr = open_budget_remaining
    if obr is not None:
        try:
            if int(obr) < 1:
                return no("OPEN_BUDGET_EXHAUSTED", open_remaining=int(obr))
        except (TypeError, ValueError):
            return no("OPEN_BUDGET_UNREADABLE", open_remaining=str(obr))

    return {"recover": True, "reason": None, "side": str(side),
            "own_px": str(own), "would_quote_now": str(now_px),
            "drift_ticks": str(ticks), "allowed_ticks": str(allow),
            "rest_s": str(rest), "book_age_s": str(age),
            "recoveries_used": int(recoveries_used),
            "max_per_market": recover_max_per_market(),
            "note": ("stale by distance, zero filled. The replacement is "
                     "priced by the same rule two ticks BEHIND the current "
                     "touch and re-tested against the pair cap; it never "
                     "moves to the touch and never crosses.")}


def stale_drift_ticks() -> int:
    """The drift tolerance. Configurable TIGHTER only -- a wider tolerance
    keeps unreachable legs alive longer, which is the unsafe direction for
    the measurement even though it risks no money."""
    raw = (os.getenv(STALE_DRIFT_ENV) or "").strip()
    try:
        v = int(raw) if raw else STALE_DRIFT_TICKS
    except ValueError:
        return STALE_DRIFT_TICKS
    if v < 0:
        return STALE_DRIFT_TICKS
    return min(STALE_DRIFT_TICKS, v)


def wire_price(side: str, own_px: Any) -> Optional[D]:
    """The price the VENUE sees for a leg quoted at `own_px` in own terms.

    THE TRANSFORMATION, IN ONE PLACE. A DOWN buy at own cost `q` IS a sell of
    YES at `1 - q`, and it rests as an ASK; an UP buy at `p` rests as a BID at
    `p`. So the 0330z plan's DOWN leg at 0.5400 and its queue row's wire price
    of 0.4600 are the same order, and `0.54 + 0.46 == 1` is the check that
    says so. Behind-the-touch for DOWN therefore means a LOWER own price and a
    HIGHER wire price: at ask 0.44 and 2 ticks, own 0.54 and wire 0.46, which
    is two ticks ABOVE the ask -- correctly away from the touch, exactly as
    `up_px = bid - 2 ticks` is.
    """
    p = _d(own_px)
    if p is None:
        return None
    return p if side == UP else (D(1) - p)


def resting_side(side: str) -> str:
    """BID for UP, ASK for DOWN -- see `wire_price`."""
    return "BID" if side == UP else "ASK"


def leg_crosses(side: str, own_px: Any, *, bid: Any, ask: Any) -> bool:
    """Would this leg cross the book, so post-only rejects it?

    A BID at or above the ask crosses; an ASK at or below the bid crosses.
    Unknowable prices are NOT called crossing -- the caller fails closed on a
    missing book long before this.
    """
    w = wire_price(side, own_px)
    b = _d(bid)
    a = _d(ask)
    if w is None:
        return False
    if side == UP:
        return a is not None and w >= a
    return b is not None and w <= b


def touch_own_price(side: str, *, bid: Any, ask: Any) -> Optional[D]:
    """Where this side's TOUCH sits in own terms.

    UP's touch is the YES bid. DOWN's is `1 - ask`, because buying DOWN means
    selling YES and the best resting sell is the ask.
    """
    b = _d(bid)
    a = _d(ask)
    if side == UP:
        return b
    return None if a is None else (D(1) - a)


def quote_own_price(side: str, *, bid: Any, ask: Any, tick: Any,
                    depth_ticks: int) -> Optional[D]:
    """Where this side would be quoted RIGHT NOW: `depth_ticks` behind its
    touch, in own terms. The same arithmetic `build_orders` uses, so a
    complement derived here and a leg planned there agree by construction."""
    t = touch_own_price(side, bid=bid, ask=ask)
    tk = _d(tick)
    if t is None or tk is None or tk <= 0:
        return None
    return t - (D(int(depth_ticks)) * tk)


def plan_executable(plan: Mapping[str, Any], *, bid: Any, ask: Any,
                    tick: Any, depth_ticks: int,
                    drift_ticks: Optional[int] = None) -> Optional[str]:
    """None if this frozen plan is still executable, else WHY it is not.

    Says nothing about whether retiring the round is SAFE -- that is the
    caller's business, and it must confirm no order is live and no leg holds a
    fill before acting on this. This function only reads prices.
    """
    tk = _d(tick)
    if tk is None or tk <= 0:
        return "TICK_UNKNOWN"
    b = _d(bid)
    a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return None            # no book to judge against: judge nothing
    if a <= b:
        return None            # crossed or locked: not the plan's fault
    legs = list(plan.get("legs") or ())
    if len(legs) != 2:
        return "PLAN_NOT_TWO_LEGS"
    allow = stale_drift_ticks() if drift_ticks is None else min(
        stale_drift_ticks(), int(drift_ticks))
    priced = []
    for leg in legs:
        side = str(leg.get("side"))
        own = _d(leg.get("price"))
        if own is None:
            return "LEG_PRICE_UNREADABLE"
        priced.append((side, own))
    # CROSSING FIRST, BOTH LEGS, BEFORE ANY DRIFT. A crossing leg is rejected
    # by the venue outright; a leg left behind is merely unable to fill. When
    # both are true -- which a large move usually makes true, since on a
    # two-cent book one side crosses long before the other drifts six ticks
    # -- the harder fact is the one worth reporting.
    for side, own in priced:
        if leg_crosses(side, own, bid=b, ask=a):
            return "LEG_WOULD_CROSS:%s" % side
    # THE DRIFT CASE IS THE SPREAD WIDENING. With a narrow book and a 2-tick
    # offset a leg cannot drift six ticks without the other crossing, so this
    # fires when the spread opens up and the complement's fair price walks
    # away while nothing crosses -- which is exactly when completing stops
    # paying.
    for side, own in priced:
        now_px = quote_own_price(side, bid=b, ask=a, tick=tk,
                                 depth_ticks=depth_ticks)
        if now_px is None:
            return None
        drift = (now_px - own) / tk
        if drift < 0:
            drift = -drift
        if drift > D(allow):
            return "PRICE_LEFT_BEHIND:%s:%s_ticks" % (
                side, drift.to_integral_value(rounding=ROUND_CEILING))
    return None


def resting_depth(side: str, *, bid_shares: Any, ask_shares: Any) -> Optional[D]:
    """The displayed size on the side this MAKER leg would rest on.

    NOT the side a taker would consume, and the distinction is the whole
    point. `resting_side` is BID for UP and ASK for DOWN: buying UP rests a
    YES bid, buying DOWN rests a YES sell on the ask. The defensive unwind
    is a TAKER and so reads the OPPOSITE side -- buying DOWN sells into the
    bid and consumes bid depth -- which is why that path derives its
    `displayed_depth` the other way round. Reading one for the other would
    pass a plausible number that describes the wrong side of the book.
    """
    return _d(bid_shares if side == UP else ask_shares)


#: THE CEILINGS A RISK-REDUCING COMPLETION ANSWERS TO.
#
# `pair_cap` is the OPENING discipline: it asks whether a pair is worth
# starting, and it belongs on a first leg. `positive_both` asks the same
# question in a stronger form. Neither is the question a completion faces,
# which is whether completing beats carrying the residual to settlement --
# and on the four windows of 2026-10-28 `pair_cap` bound in every single
# case while `improves` permitted the whole price grid. 1245z stranded 2.27
# UP for -$0.7264 while a 0.97 pair at the touch would have left the book
# near flat.
#
# `improves` is a WORST-CASE test and not an expected-value one: completing
# surrenders the outcome in which the residual wins. That is the intended
# trade under the operating model's rule that residual directional exposure
# is not assumed to carry predictive skill, while a matched pair redeems
# exactly $1.00. `within_budget` keeps the completed worst case inside the
# residual risk the owner actually approved, so the pair of them is bounded
# by an owner number rather than by this module's opinion.
COMPLETION_CEILINGS_IN_FORCE = ("improves", "within_budget")


def completion_ceiling(**kw: Any) -> Optional[D]:
    """The dearest own price at which completing is permissible, or None.

    The minimum of `COMPLETION_CEILINGS_IN_FORCE` as `max_completion_price`
    computes them. None whenever the policy cannot be computed at all or any
    ceiling in force is unsatisfiable anywhere on the grid -- the caller then
    falls back to the opening cap, which is the stricter rule, so an
    unreadable input can never buy a dearer completion.
    """
    try:
        rep = max_completion_price(**kw)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    if not rep.get("ok"):
        return None
    ceilings = rep.get("ceilings") or {}
    vals = []
    for name in COMPLETION_CEILINGS_IN_FORCE:
        v = ceilings.get(name)
        if v is None:
            return None
        d = _d(v)
        if d is None:
            return None
        vals.append(d)
    return min(vals) if vals else None


def complement_order(*, side: str, qty: Any, cost_px: Any, bid: Any, ask: Any,
                     tick: Any, min_qty: Any, depth_ticks: int,
                     book_age_s: Any = None,
                     gap_suspected: bool = False,
                     market: Any = None, held_side: Any = None,
                     bid_shares: Any = None,
                     ask_shares: Any = None,
                     held_qty: Any = None, held_cost_usd: Any = None,
                     deficient_qty: Any = None,
                     deficient_cost_usd: Any = None,
                     residual_budget_usd: Any = None) -> Dict[str, Any]:
    """Price the SECOND leg against the book in front of us and the cost we
    ACTUALLY paid for the first.

    WHY THE FROZEN PRICE CANNOT BE USED. With one live-order slot the
    complement can never rest beside the first leg -- it is refused
    MAX_LIVE_ORDERS until that leg leaves the book -- so it is ALWAYS placed
    later, against a book that has moved. In 0330z the frozen complement's
    wire price ended 45 cents BELOW the bid: post-only, the venue rejects it
    for crossing, so it never rests and the filled leg is left unhedged.

    WHAT IS PRESERVED. The QUANTITY, because matching the filled quantity is
    what makes the pair a pair -- never more than was filled, and quantised
    DOWN to the venue grid. And the economics, judged on the REALISED cost of
    leg one plus this quote: `cost_px` is what we paid, not what we hoped to
    pay, so the floor this clears is the real one. A complement that no longer
    clears it is REFUSED rather than repriced into a loss -- the residual is
    then carried under the existing controls, which is a decision about
    economics and not about the deadline.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if side not in (UP, DOWN):
        return no("COMPLEMENT_SIDE_UNKNOWN", side=str(side))
    # THE SIDE MUST BE THE ONE WE DO NOT HOLD. The caller derives both from
    # one variable, so this cannot currently disagree -- which is exactly
    # why it is asserted here rather than assumed: a complement priced on
    # the side we already hold would double the exposure it exists to
    # remove, and nothing downstream would notice. Unstated is permitted,
    # because the deployed caller does not state it.
    if held_side is not None:
        want = DOWN if str(held_side) == UP else UP
        if str(held_side) not in (UP, DOWN):
            return no("COMPLEMENT_HELD_SIDE_UNKNOWN", held_side=str(held_side))
        if side != want:
            return no("COMPLEMENT_NOT_OPPOSITE_SIDE",
                      side=str(side), held_side=str(held_side))
    if gap_suspected:
        return no("GAP_SUSPECTED")
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        return no("BOOK_STALE")
    b = _d(bid)
    a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return no("BOOK_NOT_TWO_SIDED")
    if a <= b:
        return no("BOOK_CROSSED_OR_LOCKED")
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    need = _d(qty)
    if need is None or need <= 0:
        return no("NOTHING_TO_MATCH", qty=str(qty))
    paid = _d(cost_px)
    if paid is None or paid <= 0:
        return no("FIRST_LEG_COST_UNKNOWN", cost_px=str(cost_px))

    # ============ THE COMPLETION CEILING, AND QUOTING AT THE TOUCH
    #
    # Two separate defects cost the 2026-10-28 completions, and both live
    # here. The cap refused dear-but-improving prices (1245z), and the
    # `depth_ticks` offset rested the quote where the price had to come BACK
    # to reach it (1215z at own 0.47 against a 0.49 touch, 56s, no fill;
    # 1245z at 0.63 against 0.65, 21s, no fill, with the queue telemetry
    # reporting `requires_price_to_reach_us` true).
    #
    # So when the caller supplies the inventory it actually holds, the quote
    # starts AT the touch -- the dearest price and the one most likely to
    # fill -- and steps cheaper only while the ceiling demands it. Stepping
    # cheaper moves AWAY from the touch, which is the conservative direction
    # in price and the costly one in fill probability; it is taken because
    # the ceiling says so and never to chase.
    #
    # NO CONTEXT, NO NEW RULE. Without the inventory, or with a ceiling that
    # cannot be computed, `ceil` is None and everything below is exactly
    # today's behaviour at today's `depth_ticks`, gated by the opening cap.
    # That is the stricter path, so an unreadable input cannot buy a dearer
    # completion.
    ceil = None
    if held_qty is not None and residual_budget_usd is not None:
        ceil = completion_ceiling(
            held_side=(str(held_side) if held_side is not None
                       else (DOWN if side == UP else UP)),
            held_qty=held_qty, held_cost_usd=held_cost_usd,
            deficient_qty=(0 if deficient_qty is None else deficient_qty),
            deficient_cost_usd=(0 if deficient_cost_usd is None
                                else deficient_cost_usd),
            complete_qty=qty, residual_budget_usd=residual_budget_usd,
            tick=tk)
    # THE OPENING PRICE IS STILL THE FIRST ANSWER. `ceil` only ever
    # RESCUES a quote the opening discipline refuses; it never replaces it.
    # Deciding the other way round made every ordinary second leg a
    # "completion" and quoted it at the touch, which pushed routine pairs
    # past 0.96 and -- where the ceiling was tighter than the touch --
    # refused the second leg outright, stopping accumulation dead. The
    # opening cap stays the rule for any pair that can satisfy it.
    px = quote_own_price(side, bid=b, ask=a, tick=tk,
                         depth_ticks=depth_ticks)
    if px is None:
        return no("PRICE_UNDERIVABLE")
    if px < MIN_PRICE or px > MAX_PRICE:
        return no("PRICE_OUT_OF_BAND", price=str(px))
    if (px / tk) != (px / tk).to_integral_value():
        return no("PRICE_NOT_ON_TICK", price=str(px))
    if leg_crosses(side, px, bid=b, ask=a):
        # cannot happen from `quote_own_price` at depth_ticks >= 0, and is
        # asserted rather than assumed because post-only would reject it
        return no("COMPLEMENT_WOULD_CROSS", price=str(px))

    # match no more than was filled, on the venue's grid, rounded DOWN
    q = (need / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    if q < mq:
        return no("NOTHING_TO_MATCH_ON_GRID", need=str(need), min_qty=str(mq))

    # A DISPLAYED QUOTE IS NOT A FILL, AND A PRICE WITH NOTHING BEHIND IT
    # IS NOT A MARKET. The price above comes from the touch, which one
    # resting contract is enough to set; completing against it would hand
    # the realised cost of leg one to a quote that cannot absorb leg two.
    # So the side we would REST on must display at least the quantity we
    # are about to place. MEASURED on the ten preserved BTC15m windows:
    # displayed depth on the resting side ran 96x to 4097x the complement
    # quantity across all seven accepted completions, so this refuses none
    # of them -- it binds at a size we do not yet trade, which is when it
    # is wanted. Unreadable depth is NOT a refusal: the deployed caller
    # passes none, and failing closed there would strand inventory the
    # engine completes today.
    depth = resting_depth(side, bid_shares=bid_shares, ask_shares=ask_shares)
    if depth is not None and depth < q:
        return no("COMPLEMENT_DEPTH_INSUFFICIENT", price=str(px),
                  qty=str(q), resting_side=resting_side(side),
                  displayed_depth=str(depth))

    up_px, dn_px = ((paid, px) if side == DOWN else (px, paid))
    floor = fee_floor(up_px, dn_px)
    pair = up_px + dn_px
    cap_px = max_pair_cost()
    econ = net_settlement(up_qty=q, dn_qty=q, up_price=up_px,
                          dn_price=dn_px, theta=MAKER_THETA)
    # THE OPENING DISCIPLINE, FIRST AND UNCHANGED. Three tests, all asking
    # whether the PAIR is worth having: the cap on the realised first leg
    # plus this quote, the fee floor, and both-outcomes-positive.
    opening_no: Optional[Dict[str, Any]] = None
    if pair > cap_px:
        # the REALISED first leg plus this quote is what must clear the cap,
        # so a leg that filled dearer than planned tightens the complement
        opening_no = no("COMPLEMENT_PAIR_ABOVE_CAP", pair_cost=str(pair),
                        max_pair_cost=str(cap_px), first_leg_cost=str(paid),
                        complement_price=str(px))
    elif not floor["ok"]:
        opening_no = no("COMPLEMENT_BELOW_FEE_FLOOR", pair_cost=str(pair),
                        edge=str(floor["edge"]),
                        net_if_maker=str(floor["net_if_maker"]),
                        net_if_taker=str(floor["net_if_taker"]),
                        first_leg_cost=str(paid), complement_price=str(px))
    elif not econ["both_positive"]:
        opening_no = no("COMPLEMENT_NOT_POSITIVE_ON_BOTH_OUTCOMES",
                        up_pnl=str(econ["up_pnl"]),
                        dn_pnl=str(econ["dn_pnl"]), pair_cost=str(pair))
    # ... AND ONLY THEN THE COMPLETION RESCUE. Each opening test asks
    # whether this pair is worth STARTING; this inventory is already held,
    # so a refusal there is a decision to strand it. 1245z lost the whole of
    # its -$0.7264 that way. Where the held inventory and the owner's
    # residual budget are both known, a refused completion gets one more
    # question -- does completing at this price beat carrying the residual
    # to settlement, inside the approved budget -- and is permitted on that
    # answer alone.
    #
    # STRICTLY ADDITIVE. Anything the opening discipline accepts is returned
    # exactly as before, at the same price; this can only ever convert a
    # refusal into a permitted completion, never the reverse. The fee is
    # inside the ceiling at the TAKER coefficient, the worst case should no
    # maker rebate be credited.
    judged = "OPENING"
    if opening_no is not None:
        if ceil is None or px > ceil:
            return dict(opening_no,
                        completion_ceiling=(None if ceil is None
                                            else str(ceil)),
                        completion_rescue_available=False)
        judged = "COMPLETION"
    econ_taker = net_settlement(up_qty=q, dn_qty=q, up_price=up_px,
                                dn_price=dn_px, theta=TAKER_THETA)
    return {"ok": True, "provenance": "DERIVED_FROM_CURRENT_BOOK",
            # THE MARKET THIS WAS PRICED FROM, carried on the leg so the
            # adoption site can refuse a complement priced from another
            # market's book instead of trusting that it was not. None when
            # the caller does not name one, which is today's behaviour.
            "market": (str(market) if market is not None else None),
            "held_side": (str(held_side) if held_side is not None else None),
            "resting_depth": (str(depth) if depth is not None else None),
            "leg": {"side": side, "action": "BUY", "price": str(px),
                    "qty": str(q), "mode": MODE, "post_only": True,
                    "depth_ticks": int(depth_ticks),
                    "touch_at_placement": str(
                        touch_own_price(side, bid=b, ask=a)),
                    "wire_price": str(wire_price(side, px)),
                    "resting_side": resting_side(side)},
            "first_leg_cost": str(paid), "pair_cost": str(pair),
            # WHICH QUESTION THIS WAS JUDGED ON, stated rather than inferred
            # from which fields are present. A COMPLETION answered to
            # `completion_ceiling`; an OPENING answered to the pair cap, the
            # fee floor and both-outcomes-positive. The three opening figures
            # are reported either way, so a completion that would ALSO have
            # cleared the opening tests is visible as such.
            "judged_as": judged,
            "completion_ceiling": (None if ceil is None else str(ceil)),
            "clears_opening_pair_cap": bool(pair <= cap_px),
            "edge_per_contract": str(floor["edge"]),
            "fee_floor": floor,
            "up_pnl": str(econ["up_pnl"]), "dn_pnl": str(econ["dn_pnl"]),
            "up_pnl_if_taker": str(econ_taker["up_pnl"]),
            "dn_pnl_if_taker": str(econ_taker["dn_pnl"]),
            "both_positive_if_taker": bool(econ_taker["both_positive"]),
            "labels": ("priced against the CURRENT book and the REALISED cost "
                       "of the first leg. A displayed price is not a fill: "
                       "this leg may rest unfilled or fill partially, and the "
                       "residual is then carried under the existing limits.")}


# ============================================ B5: the completion-price ceiling
#
# WHAT THIS IS FOR. `completion_decision` already answers "should we cross
# right now, at the one price the book is showing?". It does not answer the
# question an authorization needs answered first: "how dear a completion is
# permissible AT ALL, given what we actually paid, the fee we would actually
# owe, the inventory we actually hold and the residual risk the owner has
# actually approved?" That ceiling is a POLICY, and a policy has to be stated
# as a number before it can be authorized. So this computes it and reports
# every binding constraint separately. It enables nothing.
#
# FOUR CEILINGS, AND THEY ARE DIFFERENT QUESTIONS.
#
#   positive_both  the completed book pays on BOTH outcomes. The strongest
#                  ceiling, and the only one that makes a completion a
#                  profit rather than a smaller loss.
#   improves       completing beats HOLDING the one-sided book. This can be
#                  satisfied where `positive_both` is not: a stranded leg
#                  bought at 0.79 is already a $3.21 worst case, and a dear
#                  completion that leaves a $0.40 worst case is better,
#                  though still a loss.
#   within_budget  the worst outcome after completing stays inside the
#                  residual-risk budget the owner approved.
#   pair_cap       the effective cost per MATCHED share stays under the
#                  spring target. This is the maker-pair discipline applied
#                  to a crossing completion; it may be the binding one.
#
# The dearest permissible price is the MINIMUM of the ceilings in force, and
# which ceilings are in force is the owner's decision, not this function's.
#
# PRICES ARE OWN-OUTCOME, on the venue tick grid, and the fee is charged at
# the TAKER coefficient because crossing is what this prices. Costs already
# paid are taken as given and NOT re-charged a fee: they are sunk, and the
# fees on them were charged when they filled.
#
# NOTHING HERE SUBMITS, CANCELS OR AUTHORIZES ANYTHING.

def max_completion_price(*, held_side: str, held_qty: Any, held_cost_usd: Any,
                         deficient_qty: Any = 0,
                         deficient_cost_usd: Any = 0,
                         complete_qty: Any,
                         residual_budget_usd: Any,
                         tick: Any = "0.01",
                         pair_cap: Any = None,
                         theta: Any = None) -> Dict[str, Any]:
    """The dearest price at which completing is permissible, and why.

    `held_*` is the side that FILLED -- exchange-confirmed quantity and the
    cash actually paid for it, fees included. `deficient_*` is whatever the
    other side already holds, which is usually nothing. `complete_qty` is how
    much of the deficit the caller intends to buy, already clipped to the
    displayed depth by the caller, because only the caller has the book.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if held_side not in (UP, DOWN):
        return no("HELD_SIDE_UNKNOWN", held_side=str(held_side))
    h = _d(held_qty)
    ch = _d(held_cost_usd)
    d0 = _d(deficient_qty)
    cd0 = _d(deficient_cost_usd)
    d = _d(complete_qty)
    budget = _d(residual_budget_usd)
    tk = _d(tick)
    if h is None or h <= 0:
        return no("NO_CONFIRMED_INVENTORY", held_qty=str(held_qty))
    if ch is None or ch < 0:
        return no("HELD_COST_UNKNOWN", held_cost_usd=str(held_cost_usd))
    if d0 is None or d0 < 0 or cd0 is None or cd0 < 0:
        return no("DEFICIENT_SIDE_UNREADABLE")
    if d is None or d <= 0:
        return no("NOTHING_TO_COMPLETE", complete_qty=str(complete_qty))
    if budget is None or budget < 0:
        # a budget we cannot read is NOT an unlimited one
        return no("RESIDUAL_BUDGET_UNKNOWN",
                  residual_budget_usd=str(residual_budget_usd))
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    cap = max_pair_cost() if pair_cap is None else _d(pair_cap)
    if cap is None or cap <= 0:
        return no("PAIR_CAP_UNKNOWN")
    th = TAKER_THETA if theta is None else _d(theta)
    if th is None:
        return no("THETA_UNKNOWN")

    sunk = ch + cd0
    # the worst outcome if we DO NOTHING and carry the one-sided book
    held_worst = min(h, d0) - sunk

    ceilings: Dict[str, Optional[D]] = {"positive_both": None,
                                        "improves": None,
                                        "within_budget": None,
                                        "pair_cap": None}
    rows = []
    q = MAX_PRICE
    # descending the grid: every constraint is monotone in q (a cheaper
    # completion is never worse), so the FIRST q that satisfies one is the
    # dearest price that satisfies it.
    while q >= MIN_PRICE:
        f = fee(th, d, q)
        cost = sunk + q * d + f
        up_after = (h + D(0)) if held_side == UP else (d0 + d)
        dn_after = (d0 + d) if held_side == UP else (h + D(0))
        worst = min(up_after - cost, dn_after - cost)
        matched = min(up_after, dn_after)
        eff_pair = (cost / matched) if matched > 0 else None
        tests = {
            "positive_both": worst > 0,
            "improves": worst > held_worst,
            "within_budget": worst >= -budget,
            "pair_cap": eff_pair is not None and eff_pair <= cap,
        }
        for name, passed in tests.items():
            if passed and ceilings[name] is None:
                ceilings[name] = q
        if all(tests.values()):
            rows.append(q)
        q -= tk

    present = {k: v for k, v in ceilings.items() if v is not None}
    dearest_all = max(rows) if rows else None
    binding = None
    if present:
        binding = min(present, key=lambda k: present[k])

    return {
        "ok": dearest_all is not None,
        "reason": None if dearest_all is not None else "NO_PERMISSIBLE_PRICE",
        "held_side": held_side, "deficient_side": (DOWN if held_side == UP
                                                   else UP),
        "held_qty": str(h), "held_cost_usd": str(sunk),
        "avg_cost_px": str((sunk / h).quantize(D("0.0001"))),
        "complete_qty": str(d),
        "residual_budget_usd": str(budget),
        "pair_cap": str(cap), "theta": str(th),
        "worst_outcome_if_held_usd": str(held_worst),
        "ceilings": {k: (None if v is None else str(v))
                     for k, v in ceilings.items()},
        "max_permissible_price": (None if dearest_all is None
                                  else str(dearest_all)),
        "binding_ceiling": binding,
        "labels": ("own-outcome prices on the venue grid, TAKER fee, costs "
                   "already paid treated as SUNK and not re-charged. A null "
                   "ceiling means NO price on the grid satisfies that "
                   "constraint. This computes a policy and authorizes "
                   "nothing: which ceilings are in force is the owner's "
                   "decision, and `max_permissible_price` is the minimum of "
                   "ALL FOUR, which is the most conservative reading."),
    }
