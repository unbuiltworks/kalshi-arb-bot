"""The PMUS fee schedule, once, with provenance and an effective date.

WHY A MODULE AND NOT A CONSTANT. `0.06` was the taker coefficient, and it was
copied into eight places. PMUS republished the schedule effective 12 AM ET on
Thursday 2026-09-17 with the taker coefficient at **0.0695** -- 15.8% higher --
and every one of those copies kept the old number. On the execution path that
is not a cosmetic error: the fee-safe complement bound is derived from the
taker fee, so understating it LOOSENS a risk bound. A number that decides
whether an order is safe belongs in one place that says where it came from.

WHY IT IS EFFECTIVE-DATED RATHER THAN SIMPLY UPDATED. Analyses of fills from
before 2026-09-17 are CORRECT at 0.06, and silently re-rating them at 0.0695
would corrupt evidence that was right when it was recorded. So the schedule is
a function of time: ask it what applied at an instant, and the execution path
asks it about now.

PROVENANCE, and how strong it is. `docs.polymarket.us/fees` publishes both the
formula and a 99-row price table of what a 100-lot taker pays and a 100-lot
maker receives at every price from $0.01 to $0.99. All 99 rows reproduce
EXACTLY from theta = 0.0695 / 0.0125 with banker's rounding to the cent
(`tests/test_pmus_fees.py` checks every one against the captured table in
`analysis/pmus/pmus_published_fee_schedule.json`). That is a far stronger
confirmation than reading a headline number, and it pins the rounding rule as
well as the coefficients.

THE MAKER REBATE IS A CREDIT, NOT A NEGATIVE FEE TO NET AGAINST EDGE. It is
returned here as a positive number by `maker_rebate`, and this module never
subtracts it from a cost. Strategy selection and pre-rebate P&L exclude it; an
accrual becomes income only when the venue credits it.

THE TAKER REBATE IS NOT OURS. Tiers start at $250,000 of prior-calendar-month
taker volume. `taker_rebate_pct` exists so the tier table is written down
rather than remembered, and it returns 0 for any volume below the first tier,
which is where this account is.

=========================================================================
THE SCHEDULE WAS RE-TIERED ON 2026-11-14, AND THE FLAT COEFFICIENTS ABOVE
ARE NOW THE TOP TIER ONLY
=========================================================================

`docs.polymarket.us/fees`, read 2026-11-17, is headed "Effective
exchange-wide from 12:00 AM ET, Saturday, November 14, 2026". It replaces
the single taker/maker pair with THREE tier tables. The old numbers did
not vanish -- 0.0695 is now **Taker Tier 4** and the maker coefficient
moved 0.0125 -> 0.0104 at **Maker Tier 4** -- so code that kept reading a
flat 0.0695 is now quoting the BEST tier in the schedule to an account
that qualifies for the WORST one.

This is the same failure this module was written for, one schedule later,
and it is worse in the same direction: the fee-safe complement bound is
derived from the taker fee, so understating it LOOSENS a risk bound.

WHAT THIS ACCOUNT ACTUALLY QUALIFIES FOR. Taker Tier 1 (0.095) and Maker
Tier 1 (NO REBATE AT ALL). Confirmed rather than assumed: the live market
object for a current BTC5m window returns `feeCoefficient: 0.095`, which
is exactly the Tier-1 taker coefficient, and the maker rebate needs
$10,000,000 of 30-day maker volume against this account's ~$38,880/month
of capacity under its ceilings -- 257x short.

THE DEFAULT NOW RETURNS WHAT THIS ACCOUNT QUALIFIES FOR, BY OWNER
INSTRUCTION 2026-11-17 ("correct the taker coefficient to the verified
account value, but leave taker execution disabled").

It MOVES RISK BOUNDS, deliberately and in the safe direction. The
preflight's worst case on the $5 probe ceiling goes from $5.1738 to
$5.2375, because the same principal now carries a larger taker fee. The
principal is unchanged -- it is the committed cap, and a share can settle
worthless whatever the fee is -- so nothing became riskier; a cost that
was always being paid is now being counted.

TAKER EXECUTION REMAINS DISABLED. Correcting what the gates BELIEVE a
taker fee costs is not the same as permitting a taker order, and this
change does not touch `taker_allowed` anywhere.

HISTORICAL ANALYSES ARE UNAFFECTED: the regime is still effective-dated,
so a date before 2026-11-14 resolves to the flat coefficients that
applied when the evidence was recorded. Callers that want the published
TOP tier must now ask for it explicitly, which is how the captured 99-row
table is still reproduced.

NOTE ON `CREDITED_OBSERVED` BELOW. Those 25 credits are from 2026-09-06/07,
inside the FLAT regime, and they reproduce at 0.0125. They are not
evidence that this account receives a maker rebate under the tier regime,
and must not be read as such.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Optional, Tuple

SOURCE = "https://docs.polymarket.us/fees"

#: 12 AM ET, Thursday 2026-09-17. ET was UTC-4 (EDT) on that date.
THETA_CHANGE_UTC = dt.datetime(2026, 9, 17, 4, 0, tzinfo=dt.timezone.utc)

#: (taker theta, maker-rebate theta) before and from that instant. The maker
#: coefficient did not move; only the taker one did.
THETA_BEFORE: Tuple[D, D] = (D("0.06"), D("0.0125"))
THETA_FROM: Tuple[D, D] = (D("0.0695"), D("0.0125"))

#: prices the venue will accept, and the published domain of the formula
MIN_PRICE, MAX_PRICE = D("0.01"), D("0.99")

#: prior-calendar-month TAKER notional -> share of the taker fee rebated.
#: The FLAT-REGIME table, kept for dates before the re-tier.
TAKER_REBATE_TIERS = ((D("10000000"), D("0.50")),
                      (D("1000000"), D("0.25")),
                      (D("250000"), D("0.10")))

#: 12:00 AM ET, Saturday 2026-11-14. ET was UTC-5 (EST) on that date.
TIER_REGIME_UTC = dt.datetime(2026, 11, 14, 5, 0, tzinfo=dt.timezone.utc)

#: FROM the re-tier. Each table is (volume floor, coefficient), highest
#: floor first, so the first match wins and anything below the last floor
#: falls through to Tier 1.
#: trailing-30-day TAKER volume -> taker coefficient
TAKER_FEE_TIERS = ((D("250000"), D("0.0695")),     # Tier 4
                   (D("50000"), D("0.075")),       # Tier 3
                   (D("10000"), D("0.085")),       # Tier 2
                   (D("0"), D("0.095")))           # Tier 1 <- this account
#: qualifying MAKER volume -> maker rebate coefficient. Tier 1 is ZERO:
#: not a small rebate, none at all.
MAKER_REBATE_TIERS = ((D("200000000"), D("0.0104")),   # Tier 4
                      (D("50000000"), D("0.0070")),    # Tier 3
                      (D("10000000"), D("0.0035")),    # Tier 2
                      (D("0"), D("0")))                # Tier 1 <- this account
#: qualifying TAKER volume -> share of taker fees rebated, paid weekly
TAKER_REBATE_TIERS_TIERED = ((D("50000000"), D("0.30")),
                             (D("25000000"), D("0.15")),
                             (D("10000000"), D("0.10")),
                             (D("1000000"), D("0.05")),
                             (D("250000"), D("0.03")),
                             (D("0"), D("0")))

#: what this account qualifies for under the tier regime, and the reason.
ACCOUNT_TAKER_TIER = 1
ACCOUNT_MAKER_TIER = 1
ACCOUNT_TIER_BASIS = ("live market object returns feeCoefficient 0.095, the "
                      "Tier-1 taker coefficient; maker Tier 2 needs "
                      "$10,000,000 of 30-day maker volume against ~$38,880 "
                      "of monthly capacity under this account's ceilings")


def _resolve(tiers, volume):
    for floor, coef in tiers:
        if volume >= floor:
            return coef
    return tiers[-1][1]

CENT = D("0.01")


def _at(when: Optional[dt.datetime]) -> dt.datetime:
    if when is None:
        return dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.timezone.utc)
    return when


#: this account's qualifying volumes. Both are effectively zero against
#: the schedule's thresholds; see ACCOUNT_TIER_BASIS.
ACCOUNT_TAKER_VOLUME_USD = D("0")
ACCOUNT_MAKER_VOLUME_USD = D("0")


def tiered(when: Optional[dt.datetime] = None) -> bool:
    """Whether the TIERED schedule is in force at `when`."""
    return _at(when) >= TIER_REGIME_UTC


def thetas(when: Optional[dt.datetime] = None, *,
           taker_volume_usd: Optional[D] = None,
           maker_volume_usd: Optional[D] = None) -> Tuple[D, D]:
    """(taker, maker) coefficients at `when`; now by default.

    With volumes given, the tier tables resolve them. With volumes
    OMITTED this returns the published top-tier/flat coefficients, which
    is exactly what it returned before tiers existed -- deliberately, so
    that no risk bound moves without approval. See the header, and use
    `account_thetas()` for what this account qualifies for.
    """
    at = _at(when)
    if at >= TIER_REGIME_UTC:
        #: an unspecified volume is THIS ACCOUNT's, which is Tier 1 on
        #: both tables. Guessing the top tier would understate the taker
        #: fee by 37% and credit a maker rebate we do not receive.
        tv = (ACCOUNT_TAKER_VOLUME_USD if taker_volume_usd is None
              else D(str(taker_volume_usd)))
        mv = (ACCOUNT_MAKER_VOLUME_USD if maker_volume_usd is None
              else D(str(maker_volume_usd)))
        return (_resolve(TAKER_FEE_TIERS, tv),
                _resolve(MAKER_REBATE_TIERS, mv))
    return THETA_FROM if at >= THETA_CHANGE_UTC else THETA_BEFORE


def account_thetas(when: Optional[dt.datetime] = None) -> Tuple[D, D]:
    """(taker, maker) coefficients THIS ACCOUNT actually qualifies for.

    Under the tier regime that is Taker Tier 1 (0.095) and Maker Tier 1
    (no rebate at all). Before it, the flat schedule applied to everyone
    and this equals `thetas`.

    Since 2026-11-17 this is also what `thetas()` returns by default.
    """
    return thetas(when, taker_volume_usd=ACCOUNT_TAKER_VOLUME_USD,
                  maker_volume_usd=ACCOUNT_MAKER_VOLUME_USD)


def taker_theta(when: Optional[dt.datetime] = None, *,
                taker_volume_usd: Optional[D] = None) -> D:
    return thetas(when, taker_volume_usd=taker_volume_usd)[0]


def maker_theta(when: Optional[dt.datetime] = None, *,
                maker_volume_usd: Optional[D] = None) -> D:
    return thetas(when, maker_volume_usd=maker_volume_usd)[1]


def _fee(theta: D, contracts: D, price: D, *, cents: bool) -> D:
    """theta * C * p * (1 - p).

    `cents=True` applies the venue's own presentation rule -- banker's
    rounding to the cent -- and is what reconciles against a statement.
    `cents=False` keeps four decimals and is what a per-contract bound wants,
    because rounding a one-contract fee to the cent would quantise a $0.0017
    charge to $0.00 and make a bound look safer than it is.
    """
    raw = theta * D(str(contracts)) * D(str(price)) * (D(1) - D(str(price)))
    if cents:
        return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)
    return raw.quantize(D("0.0001"), rounding=ROUND_HALF_EVEN)


def taker_fee(price: D, contracts: D = D(1), *,
              when: Optional[dt.datetime] = None, cents: bool = False,
              taker_volume_usd: Optional[D] = None) -> D:
    """What a taker PAYS. Positive.

    Under the tiered schedule an unspecified volume prices at the WORST
    tier; see `thetas`.
    """
    return _fee(taker_theta(when, taker_volume_usd=taker_volume_usd),
                contracts, price, cents=cents)


def maker_rebate(price: D, contracts: D = D(1), *,
                 when: Optional[dt.datetime] = None,
                 cents: bool = False,
                 maker_volume_usd: Optional[D] = None) -> D:
    """What a maker RECEIVES, credited at the point of trade. Positive.

    NEVER net this against a cost to make an action look affordable. It is
    reported on its own line and it is income only once credited.
    """
    return _fee(maker_theta(when, maker_volume_usd=maker_volume_usd),
                contracts, price, cents=cents)


def taker_rebate_pct(prior_month_taker_notional_usd: D, *,
                     when: Optional[dt.datetime] = None) -> D:
    """Share of the taker fee rebated, from the published tier table.

    Returns 0 below the first tier. This account is below the first tier, so
    every economics figure in this programme carries 0 here.
    """
    v = D(str(prior_month_taker_notional_usd))
    table = (TAKER_REBATE_TIERS_TIERED if tiered(when)
             else TAKER_REBATE_TIERS)
    for floor, pct in table:
        if v >= floor:
            return pct
    return D("0")


def provenance(when: Optional[dt.datetime] = None) -> dict:
    t, m = thetas(when)
    return {
        "source": SOURCE,
        "formula": "fee = theta * C * p * (1 - p)",
        "taker_theta": str(t),
        "maker_rebate_theta": str(m),
        "maker_rebate_timing": "applied at the point of trade",
        "rounding": "banker's rounding to the cent, as published",
        "effective_change_utc": THETA_CHANGE_UTC.isoformat(),
        "effective_change_label": "12 AM ET, Thursday September 17, 2026",
        "taker_rebate_available_to_this_account": False,
        "taker_rebate_reason": ("tiers start at $250,000 of prior-calendar-"
                                "month taker notional; this account is below "
                                "the first tier, so it is counted at zero"),
        "verified_against": ("all 99 published price rows reproduce exactly "
                             "at the FLAT-regime coefficients; see "
                             "tests/test_pmus_fees.py"),
        "tiered": tiered(when),
        "tier_regime_effective_utc": TIER_REGIME_UTC.isoformat(),
        "tier_regime_label": ("12:00 AM ET, Saturday November 14, 2026, "
                              "exchange-wide"),
        "account_taker_tier": ACCOUNT_TAKER_TIER if tiered(when) else None,
        "account_maker_tier": ACCOUNT_MAKER_TIER if tiered(when) else None,
        "account_tier_basis": ACCOUNT_TIER_BASIS if tiered(when) else None,
        "maker_rebate_available_to_this_account": (
            not tiered(when)),
        "account_thetas": [str(x) for x in account_thetas(when)],
        "default_is_this_accounts_tier": (
            "thetas() with no volumes returns this account's tier; the "
            "published top tier must be asked for explicitly"),
    }


# ============ WHAT THE VENUE HAS ACTUALLY CREDITED THIS ACCOUNT
#
# The rebate above is the PUBLISHED schedule. This block is the OBSERVED
# accounting, from `analysis/l5/venue_rebate_credits.json`: 25 PMUS maker
# fills, each a distinct venue fill id, each carrying a NEGATIVE
# `commission` on the fill itself -- which is how "credited at the point
# of trade" actually arrives. It is not a separate payout row.
#
# THE ACCOUNTING UNIT IS THE FILL, and that is the whole point of
# recording it here. Each of the 25 rows is rounded to the cent on its
# own: a 3.13-contract fill was credited $0.01 and a 5.15-contract fill
# $0.02, and every row's implied `p*(1-p)` band is consistent with
# `0.0125 * C * p * (1 - p)`. So the rebate is NOT aggregated per order,
# per market or per day before rounding -- which matters, because
# rounding per fill is what creates the uplift below.
#
# THE SMALLEST CREDIT WE HAVE EVER OBSERVED IS AT 2.52 CONTRACTS. Below
# that there is no observation, so `rebate_floor_contracts` is a
# PUBLISHED-SCHEDULE figure and anything under 2.52 is modelled, never
# confirmed. A 1-contract fill accrues $0.003125 at p=0.50 and rounds to
# $0.00: the engine earns nothing on unit clips, and that is a measured
# result rather than a missing one.
CREDITED_OBSERVED = {
    "source_file": "analysis/l5/venue_rebate_credits.json",
    "rows": 25,
    "total_usd": D("0.42"),
    "total_contracts": D("119.49"),
    "usd_per_contract": D("0.003515"),
    "smallest_credited_clip_contracts": D("2.52"),
    "arrival": ("a NEGATIVE `commission` on the maker fill itself "
                "(`commissionNotionalTotalCollected`), not a separate "
                "payout row"),
    "accounting_unit": "PER FILL, banker's-rounded to the cent",
    "markets": ("PMUS sports slugs (aec-/asc-/tsc-), 2026-09-06..07; no "
                "BTC15m credit has been observed yet"),
}

#: the smallest clip whose rebate reaches half a cent and so rounds UP.
#: Exactly half rounds to EVEN under the published rule, hence the strict
#: inequality: ask for a hair more than the boundary.
def rebate_floor_contracts(price: D,
                           when: Optional[dt.datetime] = None) -> Optional[D]:
    """Contracts needed before a maker rebate rounds to at least $0.01.

    PUBLISHED SCHEDULE, not observed. `0.0125 * C * p * (1 - p) > 0.005`
    gives `C > 0.4 / (p * (1 - p))`, so the floor is PRICE-DEPENDENT and
    is cheapest at the money: 1.61 contracts at p=0.50, 2.09 at p=0.26,
    4.45 at p=0.10. A clip chosen without reference to its price can
    therefore earn nothing at all.
    """
    p = D(str(price))
    if p <= 0 or p >= 1:
        return None
    _t, m = thetas(when)
    denom = m * p * (D(1) - p)
    if denom <= 0:
        return None
    return (CENT / 2) / denom


def rebate_rounding_uplift(price: D, contracts: D,
                           when: Optional[dt.datetime] = None) -> Optional[D]:
    """Credited rebate divided by the unrounded accrual, or None.

    THE UPLIFT IS REAL AND IT IS BOUNDED. Rounding per fill pays the whole
    cent, so a clip sitting just above the floor is paid about twice what
    it accrues -- the 5.15-contract fills were credited $0.02 against an
    accrual of $0.0161, which is +24%. It is a modifier on a quantity
    worth roughly 0.7% of cash deployed, so it cannot rescue a strategy
    whose trading leg loses materially more than that.
    """
    p = D(str(price)); c = D(str(contracts))
    if p <= 0 or p >= 1 or c <= 0:
        return None
    _t, m = thetas(when)
    raw = m * c * p * (D(1) - p)
    if raw <= 0:
        return None
    return maker_rebate(p, c, when=when, cents=True) / raw
