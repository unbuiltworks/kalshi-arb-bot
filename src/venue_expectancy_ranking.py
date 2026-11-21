"""One comparable metric per venue:

    net expectancy / $ turnover
      = pair economics + residual economics - fees + CONFIRMED incentives

READ-ONLY. No orders anywhere, no PMUS restart, no cap/model/flag change.
Every Kalshi call in this work was a public GET.

=========================================================================
THE HEADLINE: ONLY ONE ROW HAS BOTH TERMS MEASURED, AND IT IS NEGATIVE
=========================================================================

  venue / category                trading/$    confirmed inc/$   total/$
  PMUS sports (OURS, live)        -0.06458        +0.00384       -0.06074
  Kalshi KXBTC15M/KXETH15M         UNKNOWN         0.00000        UNKNOWN
  Kalshi KXCRYPTOLEAD15M           UNKNOWN        UNVERIFIED      UNKNOWN
  Kalshi 15m commodities           UNKNOWN        UNVERIFIED      UNKNOWN
  Gemini BTC/ETH                   UNKNOWN         0.00000        UNKNOWN
  L5 Polymarket crypto (NOT us)   -0.00037   +0.00275..+0.01323  +0.00901

Only PMUS has venue-confirmed numbers on both terms. Every other row is
missing at least one, and the missing term is never the cheap one to guess.

=========================================================================
KALSHI, VERIFIED LIVE TODAY (2026-09-08T23:32Z, 5,000 programs, 25 pages)
=========================================================================
The checked-in census is dated 2026-08-31 and its forward-looking rows have
since expired, so this was re-fetched rather than trusted.

1. THE SERIES OUR SHADOW COLLECTS PAY NOTHING. KXBTC15M and KXETH15M have
   ZERO active and ZERO upcoming programs. Confirmed live, not inferred.

   AND THE SUBSIDY WAS WITHDRAWN, WHICH IS WORSE THAN NEVER HAVING EXISTED:
   the census holds 9,781 KXBTC15M and 8,299 KXETH15M programs, ALL of type
   `volume`, ALL ended 2026-05-12. Kalshi ran volume incentives on exactly
   our series for months and stopped. A programme that was switched off once
   can be switched off again, and any plan that depends on one needs that
   risk priced in.

2. THE SERIES THAT DO PAY ARE SERIES WE HAVE NO EVIDENCE FOR.
   KXCRYPTOLEAD15M   5 active, 565 upcoming, liquidity, target_size 1000
   KXGOLD/WTI/SILVER/NATGAS/COPPER 15M  1 active + 17 upcoming each,
                                        liquidity, target_size 300
   plus a long tail of daily/weekly series (gas prices, mentions, elections,
   share prices) at period_reward 800,000-2,850,000.

   `liquidity` programs score RESTING orders -- no fill required to earn --
   which is structurally the L5 shape. But we hold no tape, no shadow and no
   fills on any of them, so their trading cost to us is UNKNOWN.

   THE MISMATCH IS THE FINDING: the Kalshi series that pay are not the
   series we have evidence for, and the series we have evidence for do not
   pay.

3. REWARD UNITS: STILL NOT VERIFIED, but the live distribution narrows it.
   docs.kalshi.com is blocked by this environment's egress proxy, so the
   documentation could not be read. What the data itself says:
   period_reward clusters at 200,000 / 800,000 / 1,000,000 / 1,100,000 /
   1,200,000 / 1,250,000 / 1,350,000 / 1,450,000 / 1,600,000 / 2,000,000 /
   2,300,000 / 2,850,000.
     * under CENTI-CENTS (1e-4 USD) a 1,000,000 daily-series program is
       $100/day -- mid-range against Kalshi's advertised $1-$1,000 per
       market per day, and the 15-minute series at 200,000 is $20 per
       market instance.
     * under MICRO-DOLLARS (1e-6 USD) every daily programme in the tail
       would be $1.00-$2.85/day, i.e. every single one pinned to the very
       bottom of the advertised band.
   The centi-cents reading is the more consistent of the two, and it is an
   INFERENCE FROM PLAUSIBILITY, not a verification. The 100x ambiguity is
   the difference between a viable programme and a rounding error, so it
   must be settled before any commitment. Two things would settle it: the
   published formula (readable from a normal browser), or one credited
   payout on our own account.

=========================================================================
WHY THE KALSHI SHADOW'S "+5.8c/$" IS NOT AN ENTRY IN THIS TABLE
=========================================================================
The lane reports `shadow_only: true, order_entry: false, realized_pnl:
null`. It has never sent an order, so it has no venue-confirmed economics
at all and its per-dollar figure is SIMULATED. This session has already
shown twice what happens when a simulated or outcome-selected number is
treated as measured, so it is excluded from the ranking rather than
discounted within it.

What the live shadow DOES show is not encouraging: its most recent status
reports theoretical pair costs of 1.0625, 1.0725, 1.0725, 1.0367 -- every
one ABOVE par -- alongside `no_settlement_metadata` and repeated
`stale_book` blockers. Completing a pair at those costs loses 4-7 cents
before any incentive.

=========================================================================
WHAT THIS MEANS FOR THE DECISION
=========================================================================
The owner's framing is right and the data sharpens it: a 1% reward does not
save a strategy bleeding 6%. Our PMUS trading drag is 174.5x L5's, and the
credited rebate rate we already earn (0.384%/$) is inside L5's own range.
The payment was never the binding constraint.

So Kalshi is NOT a solution on this evidence -- it is an UNMEASURED
ALTERNATIVE. Moving there today would replace a venue where we know we lose
6.5% per dollar with one where we do not know what we lose, on series whose
reward units we cannot read, having never sent it an order.

THE CHEAP NEXT STEP, and it costs nothing: our Kalshi collector already runs
read-only against KXBTC15M/KXETH15M. Repointing or extending it to
KXCRYPTOLEAD15M and the 15-minute commodity series would build a tape on the
series that actually pay, so the UNKNOWN trading-cost cell can be filled
from observation instead of assumption. That is a collector change, not an
order path, and it does not touch the stopped PMUS lane.

REJECT-OR-CONTINUE, per the owner's rule: Kalshi cannot be accepted or
rejected yet, because both of its cells are empty. It can be MEASURED for
free. If a KXCRYPTOLEAD15M tape shows the same above-par pair costs the
KXBTC15M shadow currently reports, that is a rejection on evidence rather
than another $110 spent proving negative expectancy.
"""
from __future__ import annotations

from decimal import Decimal as D

FETCHED_UTC = "2026-09-08T23:32:28Z"
PROGRAMS_FETCHED = 5000
CENSUS_ON_DISK_DATE = "2026-08-31"

UNKNOWN = "UNKNOWN"
UNVERIFIED = "UNVERIFIED"

# venue -> (trading per $, confirmed incentive per $, total per $)
RANKING = {
    "pmus_sports_ours": (D("-0.06458"), D("0.00384"), D("-0.06074")),
    "kalshi_KXBTC15M_KXETH15M": (UNKNOWN, D("0.00000"), UNKNOWN),
    "kalshi_KXCRYPTOLEAD15M": (UNKNOWN, UNVERIFIED, UNKNOWN),
    "kalshi_15m_commodities": (UNKNOWN, UNVERIFIED, UNKNOWN),
    "gemini_btc_eth": (UNKNOWN, D("0.00000"), UNKNOWN),
    "l5_polymarket_crypto_not_us": (D("-0.00037"), D("0.01323"), D("0.00901")),
}

# Kalshi, verified live
OUR_SERIES_ACTIVE_PROGRAMS = 0
OUR_SERIES_UPCOMING_PROGRAMS = 0
OUR_SERIES_WITHDRAWN = {"KXBTC15M": 9781, "KXETH15M": 8299,
                        "type": "volume", "ended": "2026-05-12"}
PAYING_SERIES = {
    "KXCRYPTOLEAD15M": {"active": 5, "upcoming": 565, "target_size": D("1000"),
                        "period_reward": 200000, "type": "liquidity"},
    "KXGOLD15M": {"active": 1, "upcoming": 17, "target_size": D("300"),
                  "period_reward": 200000, "type": "liquidity"},
}
WE_HAVE_TAPE_ON_PAYING_SERIES = False

PERIOD_REWARD_VALUES = (200000, 800000, 1000000, 1100000, 1200000, 1250000,
                        1350000, 1450000, 1600000, 2000000, 2300000, 2850000)
UNIT_STATUS = "NOT VERIFIED - docs.kalshi.com is egress-blocked here"
UNIT_INFERENCE = "centi-cents is the more consistent reading; NOT a verification"
UNIT_AMBIGUITY_FACTOR = 100
UNIT_RESOLUTION_PATHS = ("Kalshi's published scoring formula, readable from a "
                         "normal browser",
                         "one credited payout on our own account")

# the Kalshi shadow
KALSHI_SHADOW = {"shadow_only": True, "order_entry": False,
                 "realized_pnl": None,
                 "recent_theoretical_pair_costs": (D("1.0625"), D("1.0725"),
                                                   D("1.0725"), D("1.0367")),
                 "blockers": ("no_settlement_metadata", "stale_book")}
SHADOW_FIGURE_EXCLUDED = "+5.8c/$ is SIMULATED; excluded from the ranking"

VERDICT = "KALSHI_IS_UNMEASURED_NOT_PROVEN"
CHEAP_NEXT_STEP = ("extend the read-only Kalshi collector to KXCRYPTOLEAD15M "
                   "and the 15m commodity series to build a tape where the "
                   "programs actually are; collector only, no order path")
AUTHORISES_ORDERS = ()
AUTHORISES_RESTART = ()


def measured_rows() -> tuple:
    return tuple(k for k, v in RANKING.items()
                 if not any(x in (UNKNOWN, UNVERIFIED) for x in v))


def only_measured_own_venue_is_negative() -> bool:
    t = RANKING["pmus_sports_ours"][2]
    return t < 0


def paying_series_have_no_evidence() -> bool:
    return WE_HAVE_TAPE_ON_PAYING_SERIES is False


def our_series_pay_nothing() -> bool:
    return (OUR_SERIES_ACTIVE_PROGRAMS == 0
            and OUR_SERIES_UPCOMING_PROGRAMS == 0)


def shadow_pair_costs_are_above_par() -> bool:
    return all(c > 1 for c in KALSHI_SHADOW["recent_theoretical_pair_costs"])
