"""Every incentive program on Kalshi, enumerated, and where our candidates
sit in it.

The prior venue work looked at eight series chosen because our shadow
already watched two of them. That is not a search. This enumerates the
COMPLETE program universe -- 917 pages, 183,329 program rows, 2,790 active
across 300 distinct series at 2026-09-09T00:56Z -- and ranks it.

--- THE COMPARABLE METRIC ---
Headline pool per dollar of the capital needed to meet Target Size on BOTH
sides, per hour:

    density/hr = (reward_usd / target_size) * (60 / period_minutes)

Per hour matters because period length varies by three orders of magnitude
here, from 15 minutes to a week. A $100 pool over a week is not a $100
pool over 15 minutes, and comparing the raw pools would have ranked the
gas-price series above the commodity legs.

This is a HEADLINE metric and a capacity-normalised one. It is not revenue:
share of score is still unknown, and a leg's drag is a separate axis
entirely -- see kalshi_leg_ranking.

--- THE RESULT: OUR CANDIDATES ARE RANKS 1-5 OF 300 ---
    1-5  KXNATGAS15M / KXCOPPER15M / KXSILVER15M / KXWTI15M / KXGOLD15M
         0.2667 per capital dollar per hour, $20 against target 300, 15 min
    6    KXCRYPTOLEAD15M  0.0800   ($20 against target 1000)
    7    KXTTELITEMATCH   0.0444   ($20 / 300 / 90 min, 21 markets)
    8    KXAAAGASDFL      0.0215   ($100 / 1000 / 279 min, 47 markets)

Median across all 300 active series: 0.00060. The commodity legs are 444x
the venue median and 3.3x the next-best series.

--- AND THE SECOND AXIS KILLS THE BIG POOLS ---
The largest pools per hour are the gas-price series (KXAAAGASDFL at
$1,010/hr) and KXTRUMPMENTION ($319/hr). Their books are unusable:

    KXAAAGASDIL   pair cost 1.660   -> -40% per turnover dollar
    KXAAAGASDOH   pair cost 1.330   -> -25%
    KXAAAGASDFL   pair cost 1.250   -> -20%
    KXTRUMPMENTION pair cost 1.090  -> -8.3%
    KXTTELITEMATCH pair cost 1.080  -> -7.4%
    KXDIESELD      pair cost 1.060  -> -5.7%

Only two series outside the commodity legs combine a real pool with a
tradeable book -- KXAAAGASD at 1.020 and KXEARNINGSMENTIONCHWY at 1.020 --
and both sit at a sixteenth to a thirtieth of the commodity density.

So the search for a better current program is ANSWERED, and the answer is
that we already found it. That is a negative result about the venue's
opportunity set, not a positive result about our prospects: the binding
constraint remains share of score, which no program listing exposes.
"""

from decimal import Decimal as D

CENSUS = {
    "observed_utc": "2026-09-09T00:56Z",
    "pages": 917,
    "total_program_rows": 183_329,
    "active_now": 2_790,
    "active_series": 300,
    "exhaustive": True,
    "method": ("GET /incentive_programs paged to exhaustion; a prior run "
               "capped at 200 pages returned only 40,000 rows and was "
               "NOT venue-complete -- that cap was found and removed "
               "before any ranking claim was made"),
}

TOP = (
    ("KXNATGAS15M", D("0.2667"), D("20"), 300, 15),
    ("KXCOPPER15M", D("0.2667"), D("20"), 300, 15),
    ("KXSILVER15M", D("0.2667"), D("20"), 300, 15),
    ("KXWTI15M", D("0.2667"), D("20"), 300, 15),
    ("KXGOLD15M", D("0.2667"), D("20"), 300, 15),
    ("KXCRYPTOLEAD15M", D("0.0800"), D("20"), 1000, 15),
    ("KXTTELITEMATCH", D("0.0444"), D("20"), 300, 90),
    ("KXAAAGASDFL", D("0.0215"), D("100"), 1000, 279),
)

VENUE_MEDIAN_DENSITY_PER_HOUR = D("0.00060")

# the big pools, and why they are unusable -- measured, 6 markets each
BIG_POOLS_ARE_UNTRADEABLE = {
    "KXAAAGASDIL": D("1.660"),
    "KXAAAGASDOH": D("1.330"),
    "KXAAAGASDFL": D("1.250"),
    "KXTRUMPMENTION": D("1.090"),
    "KXTTELITEMATCH": D("1.080"),
    "KXDIESELD": D("1.060"),
    "KXEARNINGSMENTIONCHWY": D("1.020"),
    "KXAAAGASD": D("1.020"),
}

SEARCH_VERDICT = (
    "ANSWERED, and the answer is that we already had it. Nothing on Kalshi "
    "beats the 15-minute commodity legs on reward density, and the series "
    "that carry larger pools carry books at 1.06 to 1.66 that no maker can "
    "manufacture pairs in. This is a negative result about the venue's "
    "opportunity set, not a positive one about our prospects.")

STILL_BINDING = (
    "share of score. No program listing exposes it, the complete census "
    "does not change that, and the $1 payout floor makes it binary rather "
    "than approximate.")


def density_per_hour(reward_usd: D, target_size: int,
                     period_minutes: float) -> D:
    """Headline pool per capital dollar per hour. Capacity-normalised so
    periods of different length compare; NOT revenue."""
    if not target_size or not period_minutes:
        return D(0)
    return ((D(reward_usd) / D(target_size))
            * (D(60) / D(str(period_minutes))))
