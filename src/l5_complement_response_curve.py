"""OWNER P0: the L5 Complement Response Curve, and the sequential-pair test.

PROVENANCE, FIRST AND EXPLICITLY. An outside X post by "0xSurferX" discusses
trader l5zn1bwom8etsk. I have NOT read it, I cannot access it, and I do not
know whether 0xSurferX is L5, an observer, a promoter or an imposter. NOTHING
he is reported to claim is treated as evidence here. His claims enter this
module ONLY as named hypotheses, and every one of them is adjudicated against
our own canonical reconstruction of L5's public fills. Where our data
contradicts the outside account, our data wins and the contradiction is
stated. No strategy of his is implemented and the frozen L5 model is not
altered.

READ-ONLY. Nothing here changes a cap, gate, threshold, model, flag or the
stopped live lane.

Cohort: canonical L5 sequence, systematic regime onward (>= 1774569600).

=========================================================================
HEADLINE: L5 IS NOT A SIMULTANEOUS YES+NO<$1 ARBITRAGEUR
=========================================================================
Hypothesis 2 -- that profitable pairs are formed SEQUENTIALLY after
probability movement rather than being simultaneously executable -- is
strongly supported, and the popular "buys YES and NO for under a dollar"
description is not what the tape shows.

Matching every buy against the opposite side FIFO within a window (an
ACCOUNTING convention, not a claim about L5's intent): 61,774 pair records
covering 647,805 matched units.

    SECONDS BETWEEN THE TWO LEGS      median 47.0     mean 66.0
        same second      1.3% of records
        1-5s             5.3%
        5-30s           28.9%
        30-120s         47.8%
        >120s           16.7%

Only 6.6% of pairs form within five seconds. Two thirds form more than
thirty seconds apart.

    PROFITABLE PAIRS (cost < 1.00): 34,859 of 61,774 = 56.4%
        median cost 0.9300, median leg gap 44s
        MOVEMENT-DOMINATED (leg-1 side rose by at least half the eventual
        profit between the legs)                 78.0%   276,183 units
        spread / simultaneous-looking            22.0%    77,333 units
        median favourable movement +0.1000 against median profit +0.0700

The movement is LARGER than the profit: he captures most of a ten-cent move
as a seven-cent pair discount.

AND PROFIT RISES WITH THE WAIT, which is the opposite of an arbitrage that
wants both legs at once:

        same second   median profit 0.02, movement-dominated 37.1%
        1-5s          0.02   72.9%
        5-30s         0.05   79.6%
        30-120s       0.08   78.1%
        >120s         0.11   81.1%

The leg-price pattern is exactly the shape the owner sketched:

        leg1 0.150 -> leg2 0.760   pair 0.9400   median gap 41s
        leg1 0.370 -> leg2 0.530   pair 0.9100   median gap 41s
        leg1 0.500 -> leg2 0.410   pair 0.9100   median gap 47s
        leg1 0.620 -> leg2 0.280   pair 0.9100   median gap 50s
        leg1 0.800 -> leg2 0.120   pair 0.9500   median gap 42s

WHAT IS UNVERIFIABLE AND IS LABELLED SO. We have L5's fills, never the book.
I cannot know what was quoted on the far side at leg-1 time, so SIMULTANEOUS
EXECUTABILITY IS UNVERIFIABLE. The 22% "spread/simultaneous-looking" bucket
is an UPPER BOUND on genuine simultaneous arbitrage, not a measurement of
it: some of it is movement my nearest-fill price series is too coarse to
resolve. The movement series itself is reconstructed from L5's own fill
prices, converted to a common P(Up) scale; between his fills we see nothing.

=========================================================================
THE COMPLEMENT RESPONSE CURVE
=========================================================================
71,354 inventory-changing fills with a defined next action. Conditioned on
how the price of the side he already holds moved by the next print:

    move of OUR side      n      SAME    OPP    median delay
        <= -10c         2356    52.3%  47.7%      18.0s
        -10..-5c        2983    60.0%  40.0%      10.0s
        -5..-2c         6503    66.7%  33.3%       4.0s
        -2..-1c         8393    61.2%  38.8%       1.0s
        -1..0c          7041    56.0%  44.0%       1.0s
        0..+1c         21129    51.3%  48.7%       0.0s
        +1..+2c         8710    44.2%  55.8%       1.0s
        +2..+5c         6736    32.3%  67.7%       4.0s
        +5..+10c        3087    36.5%  63.5%      10.0s
        >= +10c         2503    43.3%  56.7%      18.0s

THE RULE IS COHERENT AND IT IS A PRICE RULE. When the side he holds gets
CHEAPER he buys more of it; when it gets DEARER he buys the complement,
which has just become cheap. Both tails soften -- a move beyond ten cents
reduces both responses -- so it is not a naive momentum rule either.

Conditioned on the attainable pair cost right now, monotone across all six
buckets: pair <0.90 -> complement 59.5%; pair >1.20 -> complement 33.8%.

Conditioned on imbalance ratio it is NOT monotone (0-25% same-side 49.6%,
75-95% 42.0%, 95-100% 61.0%), which already hints that inventory is the
weakest of the three stories.

=========================================================================
A vs B vs C, CHRONOLOGICALLY OUT OF SAMPLE
=========================================================================
39,953 modelling rows, first 70% of windows train, last 30% test, identical
back-off. Target: is the next purchase the SAME side?

    model                    test logloss    gain vs baseline
    baseline                     0.62472           --
    A pair-cost only             0.61462         1.62%
    B imbalance only             0.62269         0.32%
    C movement only              0.55206        11.63%
    A+C                          0.54524        12.72%
    A+B+C                        0.54676        12.48%

    INCREMENTAL, adding one feature to the other two:
        add A to B+C     +0.33%
        add B to A+C     -0.28%     (it HURTS)
        add C to A+B    +10.79%

C WINS DECISIVELY. Probability movement after inventory acquisition carries
roughly thirty times the incremental predictive information of pair cost and
strictly more than nothing against inventory imbalance, which is worse than
useless once the other two are present.

THIS CONTRADICTS THE FRAME OUR CLONE USES. Our shell treats COMPLETE as an
inventory/cap event -- the spend cap converts EXTEND into COMPLETE. L5's own
history says the complement decision is a PRICE-MOVEMENT response and barely
an inventory response at all. That is a fidelity gap of a different kind
from the one we have been chasing, and it is measured, not proposed.

Caveat kept in view: pair cost and movement are mechanically related
(pair_now = avg_cost + 1 - price_now), so A and C share information by
construction. They are separable because A is a LEVEL that also depends on
the accumulated cost basis while C is a CHANGE, and the OOS test is what
separates them.

=========================================================================
PRICE-BAND DECOMPOSITION, FIVE POPULATIONS KEPT SEPARATE
=========================================================================
    band     first-leg    EXTEND   complement   residual   resid $/unit
    <10c         5,489   123,000       78,963     13,128       -0.0470
    10-25c       7,276   112,688      118,967     55,973       -0.1584
    25-45c      11,323    95,441      140,275     59,603       -0.2487
    45-55c      14,566    64,961       70,152     14,706       -0.2100
    55-75c      24,783   168,419      124,389     38,951       +0.0508
    75-90c      11,709   112,276       99,923     36,515       +0.1382
    >90c         4,337    53,579       77,781      5,812       +0.0694

Residual expectancy is reported per acquisition-price band and is NEVER
mixed with paired economics. The pattern is stark: cheap residuals lose
heavily and dear residuals win. Implied win rates run near 10% for the
25-45c band against a fair ~35%, and near 96% for 75-90c against a fair
~82%. His unpaired leg is systematically the side the market had already
decided -- adverse selection on what fails to pair, not on what he buys.

=========================================================================
THE QUEUE HYPOTHESIS: WHAT PUBLIC FILLS CAN AND CANNOT ESTABLISH
=========================================================================
The tape carries a maker/taker flag, so it does establish that L5 was
RESTING rather than crossing on 30,414 of 36,924 classified modelling
fills. It does NOT establish queue priority, resting duration, or that he
was positioned ahead of a move.

And the direction of the evidence is against the pre-positioning story:

    after a MAKER fill: the next print moved in his favour 43.2% of the
                        time, median move +0.0000
    after a TAKER fill: 52.8%, median move +0.0100

Passive fills are followed by adverse movement more often than favourable
-- the ordinary adverse-selection signature of resting orders. His crossing
fills do better on the immediate next print.

WHAT WOULD ACTUALLY BE NEEDED to test pre-positioning: order-level data --
placement and cancellation timestamps, order identifiers, and book
snapshots at those timestamps. None of it is derivable from a fill feed, and
no amount of fill analysis substitutes. Stated as a data requirement rather
than answered.

=========================================================================
HYPOTHESIS D, INCENTIVE INTENSITY: NOT SOURCEABLE
=========================================================================
The owner asked for incentive intensity "wherever historically sourceable".
For L5's crypto history it is NOT. Everything we hold on incentives is PMUS
and Kalshi and is OUR OWN venue credit history, not Polymarket crypto
reward-program terms over March-September 2026. No reward rate, eligibility
rule, or program date is in any checked-in artifact.

A maker-share time series COULD be computed and would be an unfalsifiable
proxy: maker share moves for many reasons and without program terms nothing
distinguishes an incentive response from a strategy change. It is therefore
NOT offered. To answer "does L5 extend harder when paid more" we would need
the program's historical rate schedule and eligibility windows, timestamp
aligned. Until then D is UNTESTED, not weakly supported.

=========================================================================
HYPOTHESIS E, EXTERNAL SIGNAL: UNTESTED, NO ALIGNED DATA
=========================================================================
No timestamp-aligned BTC/ETH spot or order-flow history is checked in, so
the lead-lag test was not run. It is not asserted either way, and nothing
about sports is inferred from it.

=========================================================================
VERDICT ON THE SIX OUTSIDE HYPOTHESES
=========================================================================
1 pre-positioning before a profitable pair exists   NOT ESTABLISHED; the
    maker/taker evidence points the other way, and fills cannot settle it
2 pairs formed sequentially after movement          STRONGLY SUPPORTED
    78% of profitable pairs, median 44s between legs, profit rising with
    the wait
3 behaviour/economics vary by fill-price band       SUPPORTED, and the
    residual expectancy split is large
4 movement after one side decides the next side     STRONGLY SUPPORTED and
    it is the single best predictor we have
5 external state leads repricing                    UNTESTED, no data
6 incentives affect EXTEND willingness              UNTESTED, no data
"""
from __future__ import annotations

from decimal import Decimal as D

PROVENANCE = ("0xSurferX is an outside, unverified hypothesis source; his "
              "claims are never evidence here and were not read by this "
              "module. The canonical L5 fill tape is the only evidence.")
COHORT_SINCE_EPOCH = 1774569600

# --- sequential pair formation ---------------------------------------------
PAIR_RECORDS = 61774
MATCHED_UNITS = 647805
LEG_GAP_MEDIAN_S = D("47.0")
LEG_GAP_MEAN_S = D("66.0")
GAP_SHARES = (("same second", D("0.013")), ("1-5s", D("0.053")),
              ("5-30s", D("0.289")), ("30-120s", D("0.478")),
              ("gt120s", D("0.167")))
PROFITABLE_PAIRS = 34859
PROFITABLE_SHARE = D("0.564")
PROFITABLE_MEDIAN_COST = D("0.9300")
PROFITABLE_MEDIAN_GAP_S = D("44.0")
MOVEMENT_DOMINATED_SHARE = D("0.780")
SPREAD_LOOKING_SHARE = D("0.220")
MEDIAN_FAVOURABLE_MOVE = D("0.1000")
MEDIAN_PAIR_PROFIT = D("0.0700")
PROFIT_BY_GAP = (("same second", D("0.02"), D("0.371")), ("1-5s", D("0.02"), D("0.729")),
                 ("5-30s", D("0.05"), D("0.796")), ("30-120s", D("0.08"), D("0.781")),
                 ("gt120s", D("0.11"), D("0.811")))
LEG_PATTERN = ((D("0.150"), D("0.760"), D("0.9400")), (D("0.370"), D("0.530"), D("0.9100")),
               (D("0.500"), D("0.410"), D("0.9100")), (D("0.620"), D("0.280"), D("0.9100")),
               (D("0.800"), D("0.120"), D("0.9500")))
SIMULTANEOUS_EXECUTABILITY = "UNVERIFIABLE"
SPREAD_BUCKET_IS_AN_UPPER_BOUND = True

# --- the response curve -----------------------------------------------------
RESPONSE_ROWS = 71354
RESPONSE_CURVE = (   # (label, n, P(same side), P(complement))
    ("<=-10c", 2356, D("0.523"), D("0.477")), ("-10..-5c", 2983, D("0.600"), D("0.400")),
    ("-5..-2c", 6503, D("0.667"), D("0.333")), ("-2..-1c", 8393, D("0.612"), D("0.388")),
    ("-1..0c", 7041, D("0.560"), D("0.440")), ("0..+1c", 21129, D("0.513"), D("0.487")),
    ("+1..+2c", 8710, D("0.442"), D("0.558")), ("+2..+5c", 6736, D("0.323"), D("0.677")),
    ("+5..+10c", 3087, D("0.365"), D("0.635")), (">=+10c", 2503, D("0.433"), D("0.567")),
)

# --- A/B/C out of sample ----------------------------------------------------
MODEL_ROWS = 39953
OOS_LOGLOSS = {"baseline": D("0.62472"), "A": D("0.61462"), "B": D("0.62269"),
               "C": D("0.55206"), "AC": D("0.54524"), "ABC": D("0.54676")}
INCREMENTAL = {"A_to_BC": D("0.0033"), "B_to_AC": D("-0.0028"), "C_to_AB": D("0.1079")}
WINNER = "C"

# --- price bands ------------------------------------------------------------
RESIDUAL_PNL_PER_UNIT = (("<10c", D("-0.0470")), ("10-25c", D("-0.1584")),
                         ("25-45c", D("-0.2487")), ("45-55c", D("-0.2100")),
                         ("55-75c", D("+0.0508")), ("75-90c", D("+0.1382")),
                         (">90c", D("+0.0694")))

# --- queue ------------------------------------------------------------------
MAKER_FILLS = 30414
TAKER_FILLS = 6510
MAKER_NEXT_PRINT_FAVOURABLE = D("0.432")
TAKER_NEXT_PRINT_FAVOURABLE = D("0.528")
QUEUE_EVIDENCE_REQUIRED = ("order placement and cancellation timestamps",
                           "order identifiers",
                           "book snapshots at those timestamps")

# --- untested hypotheses ----------------------------------------------------
D_INCENTIVE_STATUS = "UNTESTED_NOT_SOURCEABLE"
D_REQUIRED = ("Polymarket crypto reward-program historical rate schedule",
              "eligibility rules and program windows, timestamp aligned")
E_EXTERNAL_STATUS = "UNTESTED_NO_ALIGNED_DATA"

HYPOTHESIS_VERDICTS = {
    "1_pre_positioning": "NOT ESTABLISHED",
    "2_sequential_pairs": "STRONGLY SUPPORTED",
    "3_price_band_variation": "SUPPORTED",
    "4_movement_decides_next_side": "STRONGLY SUPPORTED",
    "5_external_signal": "UNTESTED",
    "6_incentives": "UNTESTED",
}
AUTHORISES_MODEL_CHANGE = ()
AUTHORISES_RESTART = ()
IMPLEMENTS_OUTSIDE_STRATEGY = False


def gain(model: str) -> D:
    b = OOS_LOGLOSS["baseline"]
    return ((b - OOS_LOGLOSS[model]) / b).quantize(D("0.0001"))


def movement_beats_pair_cost_and_inventory() -> bool:
    return (INCREMENTAL["C_to_AB"] > INCREMENTAL["A_to_BC"]
            and INCREMENTAL["C_to_AB"] > INCREMENTAL["B_to_AC"])


def inventory_adds_nothing() -> bool:
    return INCREMENTAL["B_to_AC"] <= 0


def pairs_are_predominantly_sequential() -> bool:
    same_second = dict(GAP_SHARES)["same second"]
    return MOVEMENT_DOMINATED_SHARE > D("0.5") and same_second < D("0.05")


def profit_rises_with_the_wait() -> bool:
    profits = [p for _, p, _ in PROFIT_BY_GAP]
    return profits == sorted(profits)


def maker_fills_are_adversely_selected() -> bool:
    return MAKER_NEXT_PRINT_FAVOURABLE < TAKER_NEXT_PRINT_FAVOURABLE
