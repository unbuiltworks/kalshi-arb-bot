"""OWNER P0: what L5 does when inventory grows and price moves against him,
and what the 2.6x grow-vs-close fill asymmetry actually was.

READ-ONLY. No model, cap, gate, threshold or flag is changed.

=========================================================================
FIRST: TWO CORRECTIONS TO MY OWN NUMBERS. BOTH MATERIAL.
=========================================================================

(A) THE 2.6x ASYMMETRY WAS A SELECTION ARTIFACT. I measured 23.8% grow
    vs 9.2% close on the ELEVEN-MARKET LOSING COHORT -- a sample selected
    on the outcome. Markets are in that cohort precisely because their
    completes did not fill, so the close-side rate was guaranteed low. On
    the full harvest of 2,169 order rows the ordering REVERSES:

        open      9.8%   (n=887)
        extend   24.7%   (n=340)
        complete 18.2%   (n=919)

    Excluding venue rejections, which never rested and so are not a
    fill-rate observation at all: grow 18.5% (n=923), close 23.5%
    (n=711) -- the close side fills BETTER, ratio 0.79x, not 2.6x.

(B) EVEN THAT COMPARISON IS NOT IDENTIFIED. Grow and close orders barely
    share a price range: grow sits at p25-p75 = 0.37-0.45, close at
    0.56-0.74. Pooling them compares different parts of the book.

=========================================================================
1. L5 UNDER ADVERSE ONE-SIDED ACCUMULATION (41,372 states, 2,216 windows)
=========================================================================

Method: walk each window's fills in order; a state is any fill that leaves
a one-sided residual on the side just bought. Label the NEXT fill EXTEND
(same side), COMPLETE (other side) or HOLD (no further fill). "Adverse"
means the price of our own side FELL since the run began.

    ADVERSE     n=15984   EXTEND 72.5%  COMPLETE 24.6%  HOLD 2.9%
    FAVORABLE   n= 9328   EXTEND 59.4%  COMPLETE 36.2%  HOLD 4.4%
    FLAT        n=16060   EXTEND 61.3%  COMPLETE 35.7%  HOLD 3.0%

L5 EXTENDS MORE WHEN THE MARKET MOVES AGAINST HIM, NOT LESS. The premise
that EXTEND persistence "should" decay after adverse fills is not what his
own history does. Sequentially, under adverse movement:

  consecutive same-side fills   2: 62.9%  3: 65.2%  4-5: 70.3%
                                6-8: 76.5%  9-14: 81.0%  15+: 84.4%
      -> persistence ESCALATES monotonically with run length.

  adverse move since run start  -0.02..0: 71.1%   -0.05..-0.02: 72.7%
                                -0.10..-0.05: 76.5%  -0.20..-0.10: 74.4%
                                <= -0.20: 61.1% (and HOLD jumps to 12.2%)
      -> the only decay is at a LARGE move, 20 cents or more.

  inventory imbalance RATIO     0-25%: 75.8%  25-50%: 66.8%  50-75%: 62.3%
                                75-95%: 55.6%  95-100%: 58.7%
      -> monotone decay: THE ratio is the variable that carries it.

  effective pair cost now       <0.90: 67.1%  0.90-0.98: 64.9%
                                1.05-1.20: 75.3%  >1.20: 76.9%
      -> he extends MORE when completing is expensive.

  time to settlement            0-60s: 62.6% (HOLD 19.6%)  60-180s: 67.0%
                                180-420s: 75.6%  420s+: 74.2%

  time since run start          0-30s: 72.9%  30-90s: 73.3%
                                90-240s: 63.9%  240-600s: 51.4%

  failed complement attempts    UNMEASURABLE. L5's public tape records
                                fills, never orders, so his unfilled
                                complement attempts do not exist in any
                                data we have. Reported as unmeasurable
                                rather than proxied.

TRANSFER LIMIT, unchanged from the residual-state replay: this history is
BTC/ETH 5- and 15-minute windows. Our lane trades sports. The mechanism
transfers; the magnitudes are crypto measurements.

=========================================================================
2. kaique-annrog AGAINST THOSE TRAJECTORIES
=========================================================================
Our run: six consecutive sampled EXTENDs while the NO side cheapened
0.38 -> 0.31 (adverse), residual 4.09 -> 15.74, state constant at
p25-45c|tte>10m|imb10-50, model extend p = 0.6786.

L5's analogue at run length 6-8 under adverse movement extends at 76.5%,
and at 9-14 at 81.0%. Our model sat at 67.9% and we extended six times.
WE WERE, IF ANYTHING, LESS PERSISTENT THAN L5. There is no divergence to
correct on this axis: the model reproduced L5's adverse-sequence behaviour.

The divergence is on the axis the state cannot see -- see 3.

=========================================================================
3. THE STATE-MODEL ALIASING, AND WHY FIXING IT DOES NOT FIX THIS LOSS
=========================================================================
IMBALANCE_BUCKETS in action_model.py are ABSOLUTE CONTRACT COUNTS:

    flat <1 | imb1-10 | imb10-50 | imb>50     (contracts, not a ratio)

So 15.74 contracts that are 100% of a 15.74-contract book, and 15.74
contracts that are 10% of a 157-contract book, are THE SAME STATE. The
imbalance RATIO -- the one variable that carries L5's EXTEND decay -- is
invisible to the model.

Measured cost of that aliasing, inside the exact bucket kaique sat in
(p25-45c|tte>10m|imb10-50, n=1462 L5 states):

        ratio 0-25%    n=1014   EXTEND 47.9%
        ratio 25-50%   n= 203   EXTEND 49.3%
        ratio 50-75%   n=  64   EXTEND 42.2%
        ratio 95-100%  n= 159   EXTEND 70.4%      <- our sub-state

Across all 34 frozen buckets with n>=200 and both ratio halves populated,
the mean |EXTEND gap| between ratio<50% and ratio>=50% is 11.5% and the
maximum is 39.0% (p55-75c|tte>10m|imb1-10: 48.5% vs 87.5%). The signal is
real and it is being discarded.

AND IT WOULD NOT HAVE PREVENTED THIS LOSS. In our own sub-state L5 extends
at 70.4%, ABOVE the bucket average of 50.3% and above our model's 67.9%.
De-aliasing raises EXTEND here, it does not lower it. Anyone adopting this
correction expecting fewer losses has mis-read it; it is a FIDELITY fix,
and the sign of the gap varies by bucket.

=========================================================================
4. DECOMPOSING THE FILL DIFFERENCE
=========================================================================
Conditioned on price, on rested orders only:

    price band    grow n   grow      close n   close
    0.20-0.30         84   33.3%           8   --
    0.30-0.40        324   16.0%          18   --
    0.40-0.50        461   18.7%          50   42.0%
    0.50-0.60         10   --            179   35.8%
    0.60-0.70          8   --            209   17.7%
    0.70-0.80         24    0.0%         172   17.4%
    0.80-1.01          6   --             74    6.8%

In the ONLY band where both are populated, close fills BETTER (42.0% vs
18.7%). What actually drives fills is PRICE LEVEL, on both sides: 33.3%
down at 0.20-0.30 falling to 6.8% at 0.80+. PMUS sports books are thin
above roughly 0.70 and that is where completes have to sit, because by the
time a complement is needed the price has moved there.

    expected L5 behaviour        NO -- not testable; L5's tape has no
                                 unfilled orders, so his fill rate is not
                                 an observable quantity at all.
    PMUS sports market structure DOMINANT -- price-level liquidity,
                                 monotone on both sides.
    our quoting/execution        MINOR -- at equal price the two sides
                                 fill the same or better on the close side.
    state-model aliasing         NOT THIS -- aliasing changes which action
                                 is chosen, not whether it fills.
    combination                  yes, but structure dominates.

=========================================================================
5. THE THREE REJECTED EXTENDs (#93): ACCEPTING THEM WOULD HAVE COST MORE
=========================================================================
kaique's rejected non-crossing EXTENDs were NO 0.38 x4.09, NO 0.34 x3.84,
NO 0.39 x4.16 = $4.482 of additional losing side, at prices DEARER than
the fills that actually followed (0.33, 0.33, 0.31).

Had they been accepted and filled, the spend cap binds earlier: the path
1.5542 -> 3.108 -> 4.414 -> 6.036 trips `spent + 2.4949 > 7.50` at 6.036,
so total spend lands near $6.04 against the actual $5.3169. Loss
approximately -$6.04 instead of -$5.3169: about $0.72 WORSE, on a worse
average price, with the cap again bounding the damage.

Population-wide, EXTEND carries the HIGHEST venue rejection rate:

    open 20.1% (178/887)   extend 37.1% (126/340)   complete 22.6% (208/919)

So #93 is currently an accidental brake on the position-GROWING action.
Fixing rejection without fixing pairing would increase stranded residual.
That is not an argument against fixing #93 -- it is an argument against
expecting it to improve economics.

(Counterfactual, labelled: a rejected order never rested, so there is no
evidence it would have filled. The direction is robust; the size is not.)

=========================================================================
THE SMALLEST EVIDENCE-SUPPORTED CORRECTION
=========================================================================
Add the IMBALANCE RATIO (residual / total traded quantity) to the state
representation. It is one derived scalar from fields the session already
carries, it is the single variable that carries L5's EXTEND decay, and the
frozen bucket discards it at a mean cost of 11.5 EXTEND-percentage-points.

BOUNDED TEST, shadow-only, zero live behaviour change: compute BOTH state
keys on every live decision, act on the frozen key exactly as now, and log
the ratio-augmented key beside it with both predicted mixes. Promote only
if the augmented key's predicted relation mix tracks L5's measured mix
better on forward evidence. No model mutation, no cap change, and nothing
here is a stop-loss rule or a fix aimed at this one loss.
"""
from __future__ import annotations

from decimal import Decimal as D

# --- corrections to previously reported numbers ----------------------------
SUPERSEDED_ASYMMETRY = {"grow": D("0.238"), "close": D("0.092"),
                        "basis": "11-market losing cohort",
                        "defect": "sample selected on the outcome being explained"}
ALL_ORDERS_N = 2169
BY_RELATION = {"open": (87, 887), "extend": (84, 340), "complete": (167, 919)}
RESTED_GROW = (171, 923)      # filled, n  (venue rejections excluded)
RESTED_CLOSE = (167, 711)

# --- 1. L5 under adverse one-sided accumulation ----------------------------
L5_STATES_N = 41372
L5_WINDOWS_N = 2216
L5_ADVERSE = {"n": 15984, "extend": D("0.725"), "complete": D("0.246"), "hold": D("0.029")}
L5_FAVORABLE = {"n": 9328, "extend": D("0.594"), "complete": D("0.362"), "hold": D("0.044")}
L5_FLAT = {"n": 16060, "extend": D("0.613"), "complete": D("0.357"), "hold": D("0.030")}
# EXTEND rate under adverse movement, by consecutive same-side fills
L5_BY_RUN_LENGTH = ((2, 3080, D("0.629")), (3, 2471, D("0.652")),
                    (4, 3206, D("0.703")), (6, 2779, D("0.765")),
                    (9, 2678, D("0.810")), (15, 1770, D("0.844")))
# EXTEND rate under adverse movement, by imbalance RATIO
L5_BY_IMBALANCE_RATIO = ((D("0.00"), 11911, D("0.758")), (D("0.25"), 1929, D("0.668")),
                         (D("0.50"), 655, D("0.623")), (D("0.75"), 198, D("0.556")),
                         (D("0.95"), 1291, D("0.587")))
UNMEASURABLE_ON_L5 = ("failed complement attempts",)
TRANSFER_LIMIT = ("BTC/ETH 5m and 15m windows; our lane trades sports. "
                  "The mechanism transfers, the magnitudes do not.")

# --- 2. the case against those trajectories --------------------------------
CASE_RUN_LENGTH = 6
CASE_MODEL_EXTEND_P = D("0.6786")
L5_ANALOGUE_EXTEND_P = D("0.765")        # run length 6-8, adverse

# --- 3. the aliasing --------------------------------------------------------
IMBALANCE_BUCKETS_ARE_ABSOLUTE_CONTRACTS = True
TARGET_BUCKET = "p25-45c|tte>10m|imb10-50"
TARGET_BUCKET_N = 1462
TARGET_BUCKET_EXTEND = D("0.503")
TARGET_BY_RATIO = ((D("0.00"), 1014, D("0.479")), (D("0.25"), 203, D("0.493")),
                   (D("0.50"), 64, D("0.422")), (D("0.95"), 159, D("0.704")))
ALIASING_BUCKETS_TESTED = 34
ALIASING_MEAN_GAP = D("0.115")
ALIASING_MAX_GAP = D("0.390")
ALIASING_MAX_BUCKET = "p55-75c|tte>10m|imb1-10"

# --- 4. the fill decomposition ---------------------------------------------
# price band -> (grow filled, grow n, close filled, close n); None = n < 20
FILL_BY_PRICE = {
    "0.20-0.30": (28, 84, None, 8),
    "0.30-0.40": (52, 324, None, 18),
    "0.40-0.50": (86, 461, 21, 50),
    "0.50-0.60": (None, 10, 64, 179),
    "0.60-0.70": (None, 8, 37, 209),
    "0.70-0.80": (0, 24, 30, 172),
    "0.80-1.01": (None, 6, 5, 74),
}
GROW_PRICE_IQR = (D("0.37"), D("0.45"))
CLOSE_PRICE_IQR = (D("0.56"), D("0.74"))
ATTRIBUTION = {
    "expected_l5_behaviour": "NOT TESTABLE - L5's tape has no unfilled orders",
    "pmus_sports_market_structure": "DOMINANT",
    "our_quoting_execution": "MINOR",
    "state_model_aliasing": "NOT THIS - aliasing changes choice, not fill",
    "combination": "yes, structure dominates",
}

# --- 5. the #93 rejections --------------------------------------------------
REJECTED_EXTENDS = ((D("0.38"), D("4.09")), (D("0.34"), D("3.84")),
                    (D("0.39"), D("4.16")))
REJECTION_RATE_BY_RELATION = {"open": (178, 887), "extend": (126, 340),
                              "complete": (208, 919)}
CASE_SPEND_IF_REJECTS_HAD_FILLED = D("6.036")
CASE_SPEND_ACTUAL = D("5.3169")


def rejected_extend_notional() -> D:
    return sum((p * q for p, q in REJECTED_EXTENDS), D("0")).quantize(D("0.001"))


def accepting_rejects_would_have_cost() -> D:
    """Positive = the loss gets WORSE if #93 is fixed, on this market."""
    return (CASE_SPEND_IF_REJECTS_HAD_FILLED - CASE_SPEND_ACTUAL).quantize(D("0.0001"))


def rejection_rate(relation: str) -> D:
    n, d = REJECTION_RATE_BY_RELATION[relation]
    return (D(n) / D(d)).quantize(D("0.001"))


def extend_is_the_most_rejected_relation() -> bool:
    return rejection_rate("extend") > max(rejection_rate("open"),
                                          rejection_rate("complete"))


# --- helpers ----------------------------------------------------------------

def rate(pair: tuple) -> D:
    f, n = pair
    return (D(f) / D(n)).quantize(D("0.001"))


def l5_persistence_escalates_with_run_length() -> bool:
    rates = [r for _, _, r in L5_BY_RUN_LENGTH]
    return rates == sorted(rates)


def l5_persistence_decays_with_imbalance_ratio() -> bool:
    """Over the monotone part of the curve (the 95-100% bin turns up)."""
    rates = [r for _, _, r in L5_BY_IMBALANCE_RATIO[:4]]
    return rates == sorted(rates, reverse=True)


def l5_extends_more_under_adverse_movement() -> bool:
    return L5_ADVERSE["extend"] > L5_FAVORABLE["extend"]


def close_side_fills_at_least_as_well_at_equal_price() -> bool:
    gf, gn, cf, cn = FILL_BY_PRICE["0.40-0.50"]
    return (D(cf) / D(cn)) > (D(gf) / D(gn))


# --- the deliverable --------------------------------------------------------
CORRECTION = "add_imbalance_ratio_to_state_representation"
CORRECTION_IS_FIDELITY_NOT_PROFIT = True
CORRECTION_WOULD_HAVE_PREVENTED_THE_LOSS = False
CORRECTION_DIRECTION_IN_CASE_SUBSTATE = "raises EXTEND (70.4% vs 47.9%)"
BOUNDED_TEST = ("compute both state keys per live decision, ACT ON THE FROZEN "
                "ONE, log the ratio-augmented key and both predicted mixes; "
                "promote only if the augmented mix tracks L5 better forward")
AUTHORISES_MODEL_CHANGE = ()
NOT_A_STOP_LOSS = True
