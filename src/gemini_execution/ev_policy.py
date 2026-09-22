"""GEMINI_EV_POLICY_V1 -- "is another unit economically worth acquiring?"

THE QUESTION THIS ANSWERS is the L5 question stated exactly: at this market
state, with this inventory, is the next unit worth buying, and if we are
holding an unpaired leg, is the complement worth paying for now?

Every earlier arm in this programme answered that with a CONSTANT -- a pair
cap of 0.96, a residual floor of $0.50, a CAP4. A constant cannot be right,
because the answer depends on what the leg already held is worth. This module
replaces the constants with an expected-value comparison.

------------------------------------------------------------------------
THE ONE EMPIRICAL INPUT, and it is not fitted here.

A residual leg bought at price p does NOT win with probability p. L5's own
2,193-window history says it wins materially less when p is small and
slightly more when p is large, monotonically, and the pattern survives
BTC/ETH and 5m/15m splits:

      p band        return on cost      implied P(win) = p(1+r)
      <0.35            -79.4%
      0.35-0.50        -55.5%
      0.50-0.65        -15.5%
      0.65-0.80        +23.4%
      >0.80            +12.2%

`RESIDUAL_RETURN` is that table. It is a PRIOR measured on a different venue
and it is applied identically to every arm, so it can bias the LEVEL of every
result but not the RANKING between them. Nothing in this module is tuned on
Gemini data.

------------------------------------------------------------------------
THE DECISIONS, all as future cashflow from this instant, sunk cost ignored.

Holding an unpaired leg bought at p, complement available at q:

    COMPLETE   ->  1 - q            certain; the pair pays $1
    HOLD       ->  P_win(p)         the residual settles for $1 or $0

    complete iff   1 - q  >  P_win(p)   <=>   q  <  1 - P_win(p)

That single inequality is the whole completion rule, and it says what the
owner suspected: a 0.30 leg implies P_win 0.062, so completing is right up to
q = 0.938 -- a PAIR COST OF 1.238. Refusing a 0.98 pair to keep a 0.30
residual destroys value. Conversely a 0.70 leg implies P_win 0.864, so its
complement is only worth paying under 0.136.

Acquiring a fresh leg at p, when the market's own history says the complement
arrives with probability `p_complete` at expected price `q_hat`:

    ACQUIRE    ->  p_complete x (1 - p - q_hat)  +  (1 - p_complete) x (P_win(p) - p)
    SKIP       ->  0

Both terms matter and the second is usually negative. A cheap leg is only
worth taking if the complement is genuinely likely; a dear leg can be worth
taking on its own.

CAUSAL BY CONSTRUCTION: `p_complete` and `q_hat` are estimated from prints
STRICTLY BEFORE the decision. Nothing here may read the market's outcome, its
later prints, or its final print count.

NO REBATE TERM EXISTS IN THIS MODULE. Rebates are incremental income and are
deliberately absent from model selection; they are added, if at all, to a
frozen candidate afterwards.

OFFLINE AND PURE. No socket, no venue read, no clock.
"""
from __future__ import annotations

from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

LABEL = "GEMINI_EV_POLICY_V1"

YES, NO = "YES", "NO"
OPEN, EXTEND, COMPLETE, HOLD, CANCEL = "OPEN", "EXTEND", "COMPLETE", "HOLD", "CANCEL"

#: L5's measured residual return on cost, by the leg's purchase price.
#: MEASURED, NOT FITTED, and measured on a different venue.
RESIDUAL_RETURN: Tuple[Tuple[F, F, F], ...] = (
    (F(0),        F(35, 100),  F(-794, 1000)),
    (F(35, 100),  F(50, 100),  F(-555, 1000)),
    (F(50, 100),  F(65, 100),  F(-155, 1000)),
    (F(65, 100),  F(80, 100),  F(234, 1000)),
    (F(80, 100),  F(10),       F(122, 1000)),
)


def residual_return(p: F) -> F:
    for lo, hi, r in RESIDUAL_RETURN:
        if lo <= p < hi:
            return r
    return RESIDUAL_RETURN[-1][2]


#: price grid the monotone envelope is built on, one point per cent.
_GRID = tuple(F(k, 100) for k in range(0, 101))


def _raw_p_win(p: F) -> F:
    v = p * (F(1) + residual_return(p))
    return max(F(0), min(F(1), v))


def _envelope() -> Tuple[Tuple[F, F], ...]:
    """A MONOTONE NON-DECREASING envelope over the raw band estimate.

    WHY THIS IS NEEDED, and it is a coherence constraint rather than a fit.
    Applying a band's average return to a price at that band's EDGE produces
    an incoherent probability: L5's 0.65-0.80 band returns +23.4% and his
    >0.80 band returns +12.2%, so the raw estimate makes a 0.75 leg (0.9255)
    look MORE likely to win than a 0.80 leg (0.8976). A dearer contract
    cannot be less likely to settle in the money; the artifact comes from the
    bands being averages over a range, not from the measurement.

    The running maximum is the smallest change that restores monotonicity and
    it never lowers an estimate, so it cannot make acquisition look better
    than the raw data says.
    """
    out = []
    best = F(0)
    for g in _GRID:
        best = max(best, _raw_p_win(g))
        out.append((g, best))
    return tuple(out)


_ENVELOPE = _envelope()


def p_win(p: F) -> F:
    """Probability the leg settles in the money, given it cost p.

    NOT p. The price is a biased estimator and the bias is the whole finding.
    Monotone by construction -- see `_envelope`.
    """
    if p <= 0:
        return F(0)
    if p >= 1:
        return F(1)
    prev = F(0)
    for g, v in _ENVELOPE:
        if g > p:
            return prev
        prev = v
    return prev


# --------------------------------------------------------------------------
# causal state, built only from prints already seen
# --------------------------------------------------------------------------
class MarketState:
    """What is knowable at this instant about one contract.

    Fed print by print. It never sees a print it has not been given, which is
    what makes every estimate below causal rather than hindsight.
    """

    def __init__(self) -> None:
        self.n_yes = 0          # prints that made a YES buyable (sell prints)
        self.n_no = 0           # prints that made a NO buyable (buy prints)
        self.yes_px: List[F] = []
        self.no_px: List[F] = []
        self.last_t: Optional[int] = None
        self.first_t: Optional[int] = None

    def observe(self, t: int, side: str, px: F) -> None:
        if self.first_t is None:
            self.first_t = t
        self.last_t = t
        if side == "sell":
            self.n_yes += 1
            self.yes_px.append(px)
        elif side == "buy":
            self.n_no += 1
            self.no_px.append(F(1) - px)

    # -- estimates, all from history only ---------------------------------
    def p_complete(self, need: str, *, floor: F = F(1, 10)) -> F:
        """Chance the complement shows up at all, from the mix seen so far.

        The share of past prints that were on the needed side. With no
        history it returns `floor` rather than 0 or 1 -- an unknown market is
        neither hopeless nor a certainty, and assuming either is how a
        backtest invents an edge.
        """
        tot = self.n_yes + self.n_no
        if tot == 0:
            return floor
        k = self.n_yes if need == YES else self.n_no
        return max(floor, F(k, tot))

    def q_hat(self, need: str) -> Optional[F]:
        """Expected complement price: the mean of what that side has cost."""
        xs = self.yes_px if need == YES else self.no_px
        if not xs:
            return None
        return sum(xs, F(0)) / len(xs)


# --------------------------------------------------------------------------
# the decisions
# --------------------------------------------------------------------------
def complete_value(leg_cost: F, complement_price: F) -> Dict[str, Any]:
    """COMPLETE vs HOLD, as future cashflow from this instant."""
    hold = p_win(leg_cost)
    comp = F(1) - complement_price
    return {
        "action": COMPLETE if comp > hold else HOLD,
        "complete_value": comp,
        "hold_value": hold,
        "edge": comp - hold,
        "max_complement_price": F(1) - hold,
        "implied_pair_cost_limit": leg_cost + (F(1) - hold),
        "why": ("a matched pair pays $1 for certain; an unpaired leg bought at "
                "%s settles with probability %s" % (leg_cost, hold)),
    }


def acquire_value(price: F, state: MarketState, need_side: str,
                  *, assume_complete_at: Optional[F] = None) -> Dict[str, Any]:
    """ACQUIRE vs SKIP for a fresh leg, using only what the tape has shown."""
    pc = state.p_complete(need_side)
    q = assume_complete_at if assume_complete_at is not None else state.q_hat(need_side)
    standalone = p_win(price) - price
    if q is None:
        ev = standalone
        paired = None
    else:
        paired = F(1) - price - q
        ev = pc * paired + (F(1) - pc) * standalone
    return {
        "action": EXTEND if ev > 0 else HOLD,
        "ev": ev,
        "p_complete": pc,
        "q_hat": q,
        "paired_value": paired,
        "standalone_value": standalone,
        "why": ("acquiring is worth it only when the complement is likely "
                "enough, or the leg is dear enough to stand alone"),
    }


def decide(*, inventory: Mapping[str, Any], offered_side: str, price: F,
           state: MarketState) -> Dict[str, Any]:
    """The single entry point: what is this offered unit worth right now?

    `offered_side` is the leg the tape has just made buyable. `inventory`
    carries yes_qty / no_qty and the per-leg average costs.
    """
    y = int(inventory.get("yes_qty") or 0)
    n = int(inventory.get("no_qty") or 0)
    ay = inventory.get("avg_yes")
    an = inventory.get("avg_no")

    reduces = (offered_side == NO and y > n) or (offered_side == YES and n > y)
    if reduces:
        leg_cost = F(str(ay)) if offered_side == NO else F(str(an))
        out = complete_value(leg_cost, price)
        out["kind"] = COMPLETE
        return out

    out = acquire_value(price, state, offered_side)
    out["kind"] = OPEN if (y + n) == 0 else EXTEND
    return out
