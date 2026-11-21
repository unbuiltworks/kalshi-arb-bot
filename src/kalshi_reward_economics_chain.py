"""The Kalshi incentive chain, one link at a time, with the links we cannot
yet close left OPEN rather than estimated.

    headline reward $
        -> qualifying two-sided fraction
        -> observable competition / depth
        -> plausible obtainable reward per $ of capital
        -> hypothetical L5 trading drag
        -> net shadow economics

THREE STATUSES, NEVER MERGED. The whole point of this module is that these
are different kinds of number and only the first one is measured:

  HEADLINE POOL      what the venue advertises it will distribute in a
                     market-period, among ALL qualifying liquidity.
                     MEASURED, from /incentive_programs.
  ESTIMATED OBTAINABLE   what WE could plausibly capture of that pool.
                     NOT PRODUCED. See OBTAINABLE_BLOCKERS.
  CREDITED           what the venue actually paid us.
                     NOT PRODUCED, and it is exactly $0.00, because we have
                     placed zero orders on this venue. order_entry=false.

A reader who wants a revenue number will not find one here. That is the
deliverable, not a gap in it.

--- LINK 1: HEADLINE REWARD, MEASURED ---
period_reward is in centi-cents (see kalshi_l5_collector.REWARD_UNIT), so
200,000 = $20.00. The program rows carry their own period: start_date
2026-09-08T23:45:00Z, end_date 2026-09-09T00:00:00Z. That is FIFTEEN
MINUTES, matching the market's own life -- each 15-minute market is a
distinct ticker with a single program. So the pool is $20 per market, not
$20 per day, and the venue's advertised "$1-$1,000 per market/day" range
contains it comfortably.

--- LINK 2: THE QUALIFYING FRACTION, AND A CORRECTION ---
Kalshi excludes a snapshot unless BOTH sides meet Target Size, and excluded
snapshots proportionally reduce the distributed reward. Target Size is 1000
contracts on KXCRYPTOLEAD15M and 300 on the commodity series.

I first read this against the TOP OF BOOK, where our collector's
two_sided_depth_median for KXCRYPTOLEAD15M is 10-16 contracts, and was
about to report that the book misses the qualifying bar by roughly 100x.
THAT WAS WRONG, and it was wrong in the direction that would have killed
the venue on a measurement error. Target Size is a property of the resting
book, not of the touch. Read against the full book, every one of the ten
active programs qualifies with room to spare -- min-side depth 3,354 to
259,338 against targets of 300 to 1,000.

So the pools ARE being distributed. The binding constraint is not
qualification. It moves entirely to link 3.

--- LINK 3: COMPETITION, WHICH IS WHERE THIS ACTUALLY TURNS ---
Rewards are normalized against competing qualifying liquidity and scored on
size AND proximity to the Reference Price. Full-book depth therefore
overstates our competition, because most of it is parked far away. Our
proxy is depth within 5 cents of the implied mid (BAND_PROXY below).

Under that proxy the flagship series is genuinely thin near the mid: 0, 0,
10, 120 and 516 contracts of min-side band depth across the five
KXCRYPTOLEAD15M legs, against 91 to 8,767 on the commodities.

--- LINK 4: WHY WE STILL REFUSE TO DIVIDE ---
It is tempting here. At a pair cost near 1.01, our ~$111 bankroll supports
roughly 110 contracts resting two-sided, which set against a band depth of
120 would be a large share of a $20 pool every fifteen minutes. That
arithmetic produces an absurd number, and the correct response to an absurd
number is to distrust the input rather than to bank the output.

Three specific reasons it is not an estimate:
  1. We do not have Kalshi's scoring function. discount_factor_bps=5000 is
     in every row and we do not know what it multiplies.
  2. The 5-cent band is our invention, not the venue's Reference Price
     rule. If the real band is wider, competing liquidity is far larger.
  3. A market that paid this rate on this little capital would not stay
     un-arbitraged. The absurdity is itself evidence that the proxy
     understates competition.

So ESTIMATED_OBTAINABLE stays None.

--- LINK 5: THE TRADING DRAG, WHICH DOES NOT NEED THE REWARD ---
This link is measurable today and it is fee-independent. Buying both sides
at the observed pair cost to receive $1.00 at settlement loses
(pair_cost - 1) per pair, before any fee and before any reward. Across the
ten active programs that is -0.8% to -13.8% per turnover dollar.

Set against L5's own -0.037% per dollar of turnover, and our PMUS lane's
-6.458%: KXCRYPTOLEAD15M's wide legs are in OUR failure regime, not L5's.

--- LINK 6: NET, WHICH IS OPEN BY CONSTRUCTION ---
net = pair economics + residual economics - fees + confirmed incentives.
Two terms are open: fees are uncertified for all six observation series (no
established maker schedule), and confirmed incentives are $0.00. A net that
sums a measured negative against an unmeasured positive is not a net, so
NET_SHADOW_ECONOMICS is None and P0_VERDICT is UNDECIDED.

UNDECIDED IS NOT A STALL. It names one experiment that closes it, and the
experiment costs no money: read the credited-reward side of an account that
already rests liquidity. Until then the honest reading is that Kalshi's
incentivized series pay a real, verified pool into books whose drag we have
measured and whose share-of-score we have not.
"""

from decimal import Decimal as D
from typing import Any, Dict, Optional

# --------------------------------------------------------------- provenance
SOURCE = {
    "programs": "GET /incentive_programs (public), 2026-09-08T23:58Z-09-09T00:05Z",
    "books": ("GET /markets/{ticker}/orderbook?depth=100 (public), "
              "orderbook_fp.yes_dollars / no_dollars"),
    "structure": ("kalshi-l5-shadow deployment e63f3f58, KALSHI_L5_STRUCTURE "
                  "rows 23:48:23Z and 23:53:37Z"),
    "snapshot_artifact": "analysis/kalshi/qualifying_snapshot.json",
    "reward_unit": ("Kalshi Liquidity Incentive Program documentation via "
                    "the owner, 2026-09-08: period_reward is centi-cents"),
}

ORDER_ENTRY = False
SHADOW_ONLY = True
CREDITED_REWARD_USD = D("0.00")   # zero orders placed, so zero credited

# ------------------------------------------------------ LINK 1: headline $
REWARD_PERIOD_MINUTES = 15
REWARD_PERIOD_EVIDENCE = ("start_date 2026-09-08T23:45:00Z -> end_date "
                          "2026-09-09T00:00:00Z on every active row")

HEADLINE = {
    # series -> per-market period pool, target size, concurrent legs observed
    "KXCRYPTOLEAD15M": {"reward_raw": 200_000, "reward_usd": D("20.00"),
                        "target_size": 1000, "concurrent_markets": 5,
                        "incentive_type": "liquidity"},
    "KXGOLD15M":       {"reward_raw": 200_000, "reward_usd": D("20.00"),
                        "target_size": 300, "concurrent_markets": 1,
                        "incentive_type": "liquidity"},
    "KXWTI15M":        {"reward_raw": 200_000, "reward_usd": D("20.00"),
                        "target_size": 300, "concurrent_markets": 1,
                        "incentive_type": "liquidity"},
    "KXSILVER15M":     {"reward_raw": 200_000, "reward_usd": D("20.00"),
                        "target_size": 300, "concurrent_markets": 1,
                        "incentive_type": "liquidity"},
    "KXNATGAS15M":     {"reward_raw": 200_000, "reward_usd": D("20.00"),
                        "target_size": 300, "concurrent_markets": 1,
                        "incentive_type": "liquidity"},
    "KXCOPPER15M":     {"reward_raw": 200_000, "reward_usd": D("20.00"),
                        "target_size": 300, "concurrent_markets": 1,
                        "incentive_type": "liquidity"},
    # the control pair: programs WITHDRAWN, not absent
    "KXBTC15M":        {"reward_raw": None, "reward_usd": None,
                        "target_size": None, "concurrent_markets": 0,
                        "incentive_type": "WITHDRAWN 2026-05-12 (volume)"},
    "KXETH15M":        {"reward_raw": None, "reward_usd": None,
                        "target_size": None, "concurrent_markets": 0,
                        "incentive_type": "WITHDRAWN 2026-05-12 (volume)"},
}


def headline_usd_per_hour(series: str) -> Optional[D]:
    """Pool the venue distributes into this series per hour, across all its
    concurrent markets. HEADLINE ONLY -- not ours, not anyone's in
    particular."""
    h = HEADLINE.get(series)
    if not h or h["reward_usd"] is None:
        return None
    periods = D(60) / D(REWARD_PERIOD_MINUTES)
    return h["reward_usd"] * D(h["concurrent_markets"]) * periods


# --------------------------------------------- LINK 2: qualifying fraction
# min-side depth of the FULL resting book against Target Size, per active
# program, from analysis/kalshi/qualifying_snapshot.json.
QUALIFYING = {
    "KXCOPPER15M-26SEP082015-15":      {"target": 300,  "min_full": 15901},
    "KXCRYPTOLEAD15M-26SEP082015-BTC": {"target": 1000, "min_full": 3354},
    "KXCRYPTOLEAD15M-26SEP082015-ETH": {"target": 1000, "min_full": 3204},
    "KXCRYPTOLEAD15M-26SEP082015-HYPE": {"target": 1000, "min_full": 5603},
    "KXCRYPTOLEAD15M-26SEP082015-SOL": {"target": 1000, "min_full": 4964},
    "KXCRYPTOLEAD15M-26SEP082015-XRP": {"target": 1000, "min_full": 4604},
    "KXGOLD15M-26SEP082015-15":        {"target": 300,  "min_full": 259338},
    "KXNATGAS15M-26SEP082015-15":      {"target": 300,  "min_full": 14804},
    "KXSILVER15M-26SEP082015-15":      {"target": 300,  "min_full": 87952},
    "KXWTI15M-26SEP082015-15":         {"target": 300,  "min_full": 120047},
}

QUALIFYING_SAMPLE = ("ONE snapshot of 10 markets, not a distribution over "
                     "time. The collector is building the time series.")

TOUCH_READING_WAS_WRONG = (
    "An earlier reading tested Target Size against the TOUCH, where "
    "KXCRYPTOLEAD15M shows a two-sided depth median of 10-16 contracts, and "
    "concluded the book misses the bar by ~100x. Target Size is a property "
    "of the resting book, not of the best bid. Every active program "
    "qualifies on full-book depth. The error would have rejected the venue "
    "on a measurement mistake, so it is recorded here rather than deleted.")


def qualifying_fraction() -> D:
    """Fraction of observed active programs whose full book meets Target
    Size on BOTH sides."""
    n = len(QUALIFYING)
    ok = sum(1 for v in QUALIFYING.values() if v["min_full"] >= v["target"])
    return D(ok) / D(n)


# ------------------------------------------------- LINK 3: competition
BAND_PROXY = ("depth within 5 cents of the implied mid on each side, "
              "min of the two. A PROXY for Kalshi's proximity-to-Reference-"
              "Price scoring, which we do not have.")

BAND_MIN_SIDE = {
    "KXCRYPTOLEAD15M-26SEP082015-BTC": 516,
    "KXCRYPTOLEAD15M-26SEP082015-ETH": 120,
    "KXCRYPTOLEAD15M-26SEP082015-HYPE": 0,
    "KXCRYPTOLEAD15M-26SEP082015-SOL": 0,
    "KXCRYPTOLEAD15M-26SEP082015-XRP": 10,
    "KXCOPPER15M-26SEP082015-15": 3014,
    "KXGOLD15M-26SEP082015-15": 8767,
    "KXNATGAS15M-26SEP082015-15": 91,
    "KXSILVER15M-26SEP082015-15": 3426,
    "KXWTI15M-26SEP082015-15": 5374,
}

DISCOUNT_FACTOR_BPS = 5000
DISCOUNT_FACTOR_MEANING = "UNKNOWN - present in every row; we do not know what it multiplies"

# -------------------------------------- LINK 4: obtainable, NOT PRODUCED
ESTIMATED_OBTAINABLE_USD = None

OBTAINABLE_BLOCKERS = (
    "scoring_function_unknown: size x proximity weighting is not published "
    "to us, and discount_factor_bps=5000 is unexplained",
    "band_is_our_invention: the 5-cent window is a proxy for the Reference "
    "Price rule, and a wider real band means far more competing liquidity",
    "no_credited_receipts: we have never rested liquidity here, so there is "
    "no payment record to calibrate share-of-score against",
    "single_snapshot: ten markets at one instant is not a distribution",
)

WHY_NOT_JUST_DIVIDE = (
    "At pair cost ~1.01 a $111 bankroll rests ~110 contracts two-sided. "
    "Against a band depth of 120 that would be a large share of a $20 pool "
    "every 15 minutes, which annualizes to a number no venue leaves "
    "unclaimed. An absurd quotient is evidence the denominator is wrong, "
    "not evidence of an opportunity.")

# --------------------------------------------- LINK 5: trading drag, MEASURED
PAIR_COST = {
    "KXSILVER15M-26SEP082015-15": D("1.008"),
    "KXCOPPER15M-26SEP082015-15": D("1.010"),
    "KXCRYPTOLEAD15M-26SEP082015-BTC": D("1.010"),
    "KXCRYPTOLEAD15M-26SEP082015-ETH": D("1.010"),
    "KXGOLD15M-26SEP082015-15": D("1.010"),
    "KXNATGAS15M-26SEP082015-15": D("1.010"),
    "KXWTI15M-26SEP082015-15": D("1.010"),
    "KXCRYPTOLEAD15M-26SEP082015-XRP": D("1.040"),
    "KXCRYPTOLEAD15M-26SEP082015-SOL": D("1.110"),
    "KXCRYPTOLEAD15M-26SEP082015-HYPE": D("1.160"),
}

# reference points for the same quantity, from our own prior work
L5_TRADING_DRAG_PER_DOLLAR = D("-0.00037")     # -0.037%, full wallet
PMUS_OUR_TRADING_DRAG_PER_DOLLAR = D("-0.06458")  # -6.458%, our lane


def drag_per_turnover_dollar(ticker: str) -> Optional[D]:
    """Loss per dollar turned over to manufacture a pair at the observed
    book, receiving $1.00 at settlement. Fee-independent and
    incentive-independent: it is (pair_cost - 1) / pair_cost."""
    pc = PAIR_COST.get(ticker)
    if pc is None:
        return None
    return -(pc - D(1)) / pc


def drag_range() -> Dict[str, D]:
    vals = [drag_per_turnover_dollar(t) for t in PAIR_COST]
    return {"best": max(vals), "worst": min(vals)}


# ------------------------------------------------ LINK 6: net, OPEN
NET_SHADOW_ECONOMICS = None

NET_OPEN_TERMS = (
    "confirmed incentives: $0.00 credited, and estimated obtainable is "
    "refused above",
    "residual economics: UNMEASURED here -- we hold no fills on this venue "
    "-- and it is the term that actually sank PMUS",
)

FEE_TERM_CLOSED = (
    "The fee term is NO LONGER OPEN. GET /series/{ticker} declares "
    "fee_type and fee_multiplier, and all eight series return "
    "quadratic / 1, identical to the certified KXBTC15M/KXETH15M pair. "
    "See kalshi_leg_ranking.FEE_STATUS: venue-declared, not a paid "
    "invoice. This closes one of the two terms this module listed as open "
    "on 2026-09-09, and it was closed by reading the venue's own series "
    "metadata rather than by assuming the schedule carried over.")

SUPERSEDED_BY_LEG_ANALYSIS = (
    "The per-series drag figures in link 5 are SUPERSEDED for decisions by "
    "kalshi_leg_ranking, which reports per LEG. KXCRYPTOLEAD15M is one "
    "series carrying five assets whose pair costs run 1.01 to 1.16; its "
    "median describes none of them. This module's series view is kept for "
    "the chain narrative only.")

THE_TENSION_FOUND_AT_LEG_LEVEL = (
    "Drag and reward share pull in OPPOSITE directions across legs. The "
    "cheapest books carry the thickest near-mid competition -- they are "
    "cheap because makers are already standing there -- while the legs "
    "with almost no near-mid competition are wide. There is no single "
    "best leg, only a frontier, and which end of it you want depends on "
    "whether you are manufacturing pairs or providing liquidity.")

P0_VERDICT = "UNDECIDED"

P0_QUESTION = ("does an actively incentivized Kalshi market provide both "
               "(1) sufficiently cheap execution/trading drag and "
               "(2) sufficiently large verified payments to plausibly "
               "reproduce L5's positive total expectancy?")

P0_ANSWER = {
    "1_execution_drag": ("MEASURED and MIXED. The tight legs (silver, "
                         "copper, gold, WTI, natgas, and the BTC/ETH "
                         "KXCRYPTOLEAD15M legs) run -0.8% to -1.0% per "
                         "turnover dollar. The wide KXCRYPTOLEAD15M legs "
                         "(XRP -3.8%, SOL -9.9%, HYPE -13.8%) are worse "
                         "than our own failed PMUS lane."),
    "2_verified_payments": ("PARTIALLY. The POOL is verified: $20 per "
                            "15-minute market, liquidity type, target size "
                            "300-1000, and every observed active program's "
                            "book clears that target. OUR SHARE of it is "
                            "not measured and is not estimated here."),
    "verdict": ("UNDECIDED, and specifically not REJECTED. The earlier "
                "touch-based reading that would have rejected it was a "
                "measurement error. What is missing is share-of-score, "
                "which no amount of public book data supplies."),
    "next_step_costs_no_money": ("read the credited-reward side of an "
                                 "account that already rests liquidity; "
                                 "that single observation closes link 3-4 "
                                 "and the chain resolves without an order"),
}

# ----------------------------------- incidental, and flagged as incidental
VENUE_WIDE_LEAD = {
    "note": ("seen while an unbounded scan briefly ran past our eight "
             "series. NOT measured, NOT a recommendation, recorded only so "
             "the discovery lead is not lost."),
    "KXDEEPSHARE": {"period_reward_raw": 2_000_000, "reward_usd": D("200.00")},
    "KXDIESELD": {"period_reward_raw": 1_000_000, "reward_usd": D("100.00")},
    "caveat": "books unread, drag unmeasured, periods unchecked",
}


def chain() -> Dict[str, Any]:
    """The six links, with the open ones open."""
    return {
        "kind": "KALSHI_REWARD_ECONOMICS_CHAIN",
        "1_headline_pool_usd_per_market_period": {
            s: (None if h["reward_usd"] is None else str(h["reward_usd"]))
            for s, h in HEADLINE.items()},
        "1_period_minutes": REWARD_PERIOD_MINUTES,
        "2_qualifying_fraction_of_active_programs": str(qualifying_fraction()),
        "3_band_min_side_depth": dict(BAND_MIN_SIDE),
        "4_estimated_obtainable_usd": ESTIMATED_OBTAINABLE_USD,
        "4_blockers": list(OBTAINABLE_BLOCKERS),
        "5_drag_per_turnover_dollar": {k: str(v) for k, v
                                       in drag_range().items()},
        "6_net_shadow_economics": NET_SHADOW_ECONOMICS,
        "6_fee_term_closed": FEE_TERM_CLOSED,
        "superseded_by_leg_analysis": SUPERSEDED_BY_LEG_ANALYSIS,
        "credited_reward_usd": str(CREDITED_REWARD_USD),
        "verdict": P0_VERDICT,
        "shadow_only": SHADOW_ONLY,
        "order_entry": ORDER_ENTRY,
        "realized_pnl": None,
    }
