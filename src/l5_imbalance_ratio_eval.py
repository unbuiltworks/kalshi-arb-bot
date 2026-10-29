"""OWNER GO: the shadow-only imbalance-ratio augmentation, tested against the
bar frozen in l5_imbalance_ratio_bar (commit 18cc8aa5) BEFORE any number
existed -- plus the economic question today's result raised.

READ-ONLY. Production computes and acts from the frozen key, unchanged. No
cap, gate, threshold, model, re-quote flag or maker-only rule is touched.

=========================================================================
HEADLINE: THE AUGMENTATION FAILS ITS OWN PRE-REGISTERED BAR
=========================================================================
Temporal split, canonical L5 sequence, systematic regime onward: 69,646
decisions, first 70% of windows train (47,930) and last 30% test (21,716).
Identical back-off ladder for both representations.

    ALL test decisions      frozen 0.70879   aug 0.71124   -0.35%
    ratio >= 0.75           frozen 0.70024   aug 0.68465   +2.23%
    post-fill only          frozen 0.69136   aug 0.69382   -0.36%

    by ratio stratum
      r<25     n=16918   -0.28%
      r25-50   n= 2379   -0.75%
      r50-75   n=  756   -6.36%
      r75-95   n=  260   -3.96%
      r95-100  n= 1403   +3.37%      <- the only stratum that improves

    [FAIL] 1. overall log-loss gain -0.35%, needed >= +1%
    [PASS] 2. high-imbalance gain +2.23%, needed >= +1%
    [PASS] 3. extra back-off +0.0pp, allowed <= 5pp
    [FAIL] 4. strata improved 1, needed >= 3

Two of four fail, so the augmentation FAILS. Condition 4 was written for
exactly this shape: the high-imbalance "win" is not a win across high
imbalance, it is one bucket. r75-95 -- also high imbalance -- gets WORSE by
3.96%. The +2.23% on "ratio >= 0.75" is r95-100 (n=1403) outvoting r75-95
(n=260) inside a pooled subset.

Everywhere else the augmentation is neutral to worse, which is the ordinary
signature of splitting states without adding signal: more parameters, same
information, slightly worse generalisation.

THE BAR'S ON_FAIL INSTRUCTION IS BEING FOLLOWED. It says report the
negative result and stop, and specifically not to retune buckets and re-run,
"which would be fitting the test half". A binary one-sided/not flag would
very likely pass -- and I have now seen the test half, so I cannot honestly
evaluate it here. If the owner wants that variant it needs a fresh
pre-registration and evidence this test did not touch. Forward live shadow
data is exactly such evidence.

=========================================================================
AND IT WOULD HAVE INCREASED EXPOSURE, NOT REDUCED IT
=========================================================================
Applied to our 1,769 observed live decisions with a computable ratio,
comparing the frozen state's L5 relation mix to the ratio-aware state's:

    stratum   our n   frozen P(EXTEND)   ratio-aware   delta
    r<25        636        40.1%            37.5%      -2.5pp
    r25-50      345        47.3%            45.4%      -1.9pp
    r50-75      222        54.3%            42.8%     -11.5pp
    r95-100     566        50.7%            70.0%     +19.3pp

    weighted mean change in P(EXTEND): +3.44%

The ratio-aware model would have asked us to GROW positions MORE OFTEN,
concentrated in exactly the fully-one-sided states where every loss lives.
That is the correct L5-fidelity answer -- L5 really does extend at 70% there
-- and it is the opposite of a risk improvement. Anyone reading "add the
imbalance ratio" as a safety measure has it backwards, and a test pins that.

OUR LANE IS NOT L5's LANE ON THIS AXIS. 32.0% of our live decisions sit at
ratio >= 95% against 6.5% of L5's test decisions: we spend five times as
much of our life fully one-sided. We also have literally ZERO decisions in
r75-95 -- our positions are either substantially paired or completely
unpaired, never in between.

=========================================================================
THE ECONOMIC QUESTION, AND IT IS THE REAL FINDING
=========================================================================
The premise was: L5 persists into adverse and expensive states, yet makes
money, so what property of his markets makes that viable? Derived from his
own public fills over the canonical tape (2,216 windows, 165.5 days):

    pair contribution        +$18,912.86
    residual contribution    -$19,963.43
    ------------------------------------
    net on fills              -$1,050.58

L5's trading, measured on the only evidence we have of it, is roughly
BREAK-EVEN TO SLIGHTLY NEGATIVE. His pair profit is almost exactly cancelled
by his residual losses. Supporting detail:

    matched units 648,022    residual units 224,899   residual share 25.8%
    per matched unit  +$0.02919      per residual unit  -$0.08877
    stranded-side win rate 49.9%  -- a coin flip, as it must be
    median matched pair cost 0.9705, and 38.8% of pairs cost >= 1.00
    only 23 of 2,216 windows (1.0%) end genuinely flat
    13.4 windows/day, 5- or 15-minute windows, median 6 fills, median
    spend $92.90 per window

SO THE ANSWER TO "WHAT MAKES IT VIABLE" IS NOT VISIBLE IN THE FILLS. If L5
is profitable overall, the profit is not in this tape; it is in liquidity
rewards and incentives, which this data does not contain. That reframes the
clone thesis: copying L5's decisions without his incentive stream copies the
half that loses money.

STATED AS UNKNOWN, because it is:
  - whether the public activity feed captures ALL of L5's fills
  - the size, terms and eligibility of any Polymarket reward program he
    was earning on these up/down markets
  - his fees, which are not in the feed
  - whether he ran offsetting inventory outside these windows
None of those can be resolved from the sources we have, and the -$1,050.58
must not be quoted as "L5 loses money" -- it is "L5's PUBLIC FILLS net
slightly negative, so his edge, if any, is elsewhere".

CROSS-ENVIRONMENT COMPARISON, every line source-supported:

                          L5 crypto up/down        our PMUS sports
    time per window       5 or 15 minutes          hours (event length)
    turnover              13.4 windows/day         ~1 market at a time
    residual share        25.8% of units           far higher (32% of
                                                   decisions fully one-sided)
    per residual unit     -$0.08877                -$0.331449 (3.7x worse)
    per matched unit      +$0.02919                +$0.057230 (2.0x better)
    median pair cost      0.9705 (38.8% >= 1.00)   above par on 66.7% of
                                                   replayed completions
    fill evidence         UNAVAILABLE -- his tape  measured: price-level
                          has fills, never orders  driven, 33.3% at 0.20-
                                                   0.30 down to 6.8% at 0.80+
    incentives            UNKNOWN, and the likely   credited maker rebates
                          location of his edge      $2.1300 confirmed to date

The structural difference that matters: L5 gets 13.4 fresh chances a day in
a market that reprices continuously and mean-reverts within minutes, so an
adverse position is frequently rescued by the window itself. A sports market
trends toward one outcome and stays there, so an adverse position is
rescued by nothing. Persisting into adversity is cheap when the clock is
five minutes long and expensive when the clock is a tennis match. That is a
mechanism argument supported by the duration and residual-cost numbers
above; it is not a measurement of mean reversion, which we have not made.

=========================================================================
AUTHENTICATED BOOK CAPTURE: NO COVERAGE YET
=========================================================================
Zero order rows carry book_capture. The capture shipped at 21:17Z and the
lane hit the daily stop at 21:20:06Z, about two minutes later. 704 rows
carry recorder_book from the older rejection and #93 control paths, which
are DIFFERENT populations and must not be pooled with it. Characterising
PMUS execution by price level, spread and depth cannot begin until the lane
runs again. Reported as no-coverage rather than substituted.
"""
from __future__ import annotations

from decimal import Decimal as D

# --- the OOS result ---------------------------------------------------------
COHORT_DECISIONS = 69646
TRAIN_N = 47930
TEST_N = 21716
LOGLOSS = {"all": (D("0.70879"), D("0.71124")),
           "high_imbalance": (D("0.70024"), D("0.68465")),
           "post_fill": (D("0.69136"), D("0.69382"))}
BY_STRATUM = (("r<25", 16918, D("-0.0028")), ("r25-50", 2379, D("-0.0075")),
              ("r50-75", 756, D("-0.0636")), ("r75-95", 260, D("-0.0396")),
              ("r95-100", 1403, D("+0.0337")))
BAR_CONDITIONS = {"1_overall_gain": False, "2_high_imbalance_gain": True,
                  "3_backoff_within_5pp": True, "4_three_strata_improved": False}
VERDICT = "FAIL"
STRATA_IMPROVED = 1


def gain(key: str) -> D:
    f, a = LOGLOSS[key]
    return ((f - a) / f).quantize(D("0.0001"))


def passes_bar() -> bool:
    return all(BAR_CONDITIONS.values())


def improvement_is_one_bucket_not_high_imbalance() -> bool:
    """r75-95 is also high imbalance and gets WORSE; only r95-100 gains."""
    by = dict((k, g) for k, _, g in BY_STRATUM)
    return by["r75-95"] < 0 and by["r95-100"] > 0


# --- exposure impact on decisions we already made ---------------------------
LIVE_DECISIONS_WITH_RATIO = 1769
EXPOSURE = (("r<25", 636, D("0.401"), D("0.375")),
            ("r25-50", 345, D("0.473"), D("0.454")),
            ("r50-75", 222, D("0.543"), D("0.428")),
            ("r95-100", 566, D("0.507"), D("0.700")))
MEAN_EXTEND_CHANGE = D("+0.0344")
OUR_RATIO_MIX = {"r<25": D("0.360"), "r25-50": D("0.195"), "r50-75": D("0.125"),
                 "r75-95": D("0.000"), "r95-100": D("0.320")}
L5_TEST_R95_SHARE = D("0.065")


def augmentation_would_increase_exposure() -> bool:
    return MEAN_EXTEND_CHANGE > 0


def our_one_sided_share_vs_l5() -> D:
    return (OUR_RATIO_MIX["r95-100"] / L5_TEST_R95_SHARE).quantize(D("0.01"))


# --- L5's own economics, derived from public fills only ---------------------
L5_WINDOWS = 2216
L5_DAYS = D("165.5")
L5_PAIR_CONTRIBUTION = D("18912.86")
L5_RESIDUAL_CONTRIBUTION = D("-19963.43")
L5_MATCHED_UNITS = 648022
L5_RESIDUAL_UNITS = 224899
L5_PER_MATCHED_UNIT = D("0.02919")
L5_PER_RESIDUAL_UNIT = D("-0.08877")
L5_STRANDED_WIN_RATE = D("0.499")
L5_MEDIAN_PAIR_COST = D("0.9705")
L5_PAIRS_ABOVE_PAR = D("0.388")
L5_FLAT_AT_CLOSE = (23, 2216)
L5_WINDOWS_PER_DAY = D("13.4")
OUR_PER_MATCHED_UNIT = D("0.057230")
OUR_PER_RESIDUAL_UNIT = D("-0.331449")

L5_UNKNOWNS = (
    "whether the public activity feed captures all of L5's fills",
    "the size, terms and eligibility of any liquidity reward program",
    "his fees, which the feed does not carry",
    "whether he held offsetting inventory outside these windows",
)


def l5_net_on_public_fills() -> D:
    return (L5_PAIR_CONTRIBUTION + L5_RESIDUAL_CONTRIBUTION).quantize(D("0.01"))


def l5_edge_is_not_in_the_fills() -> bool:
    return l5_net_on_public_fills() < 0


def our_residual_cost_ratio_vs_l5() -> D:
    return (OUR_PER_RESIDUAL_UNIT / L5_PER_RESIDUAL_UNIT).quantize(D("0.01"))


# --- book capture -----------------------------------------------------------
BOOK_CAPTURE_ROWS = 0
RECORDER_BOOK_ROWS_OTHER_POPULATIONS = 704
BOOK_CAPTURE_BLOCKED_BY = ("the lane stopped 2 minutes after the capture "
                           "deployed; no coverage exists yet")

# --- what this module authorises --------------------------------------------
RECOMMENDATION = "DO_NOT_PROMOTE_TO_A_LIVE_MODEL_TREATMENT"
AUTHORISES_MODEL_CHANGE = ()
AUTHORISES_RESTART = ()
SHADOW_ONLY_STILL_DEFENSIBLE = True   # logging costs nothing and gathers the
                                      # fresh evidence a re-test would need
