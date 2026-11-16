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
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Optional, Tuple

SOURCE = "https://docs.polymarket.us/fees"

#: 12 AM ET, Thursday 2026-09-17. ET was UTC-4 (EDT) on that date.
THETA_CHANGE_UTC = dt.datetime(2026, 9, 17, 4, 0, tzinfo=dt.timezone.utc)

#: (taker theta, maker-rebate theta) before and from that instant. The maker
#: coefficient did not move; only the taker one did.
THETA_BEFORE: Tuple[D, D] = (D("0.06"), D("0.0125"))
THETA_FROM: Tuple[D, D] = (D("0.0695"), D("0.0125"))

#: THE TIERED SCHEDULE. "Effective exchange-wide from 10:00 PM ET,
#: Wednesday, October 28, 2026" (docs.polymarket.us/fees, fetched
#: 2026-11-12). ET was UTC-4 on that date. From then the taker coefficient
#: and the maker rebate are set by the ACCOUNT'S TIER, not by one number:
#:   taker, trailing 30 completed ET days of taker volume:
#:     Tier 1 < $10,000 -> 0.095 | Tier 2 < $50,000 -> 0.085 |
#:     Tier 3 < $250,000 -> 0.075 | Tier 4 >= $250,000 -> 0.0695
#:   maker rebate, qualifying maker volume (same window):
#:     Tier 1 < $10,000,000 -> NO MAKER REBATE | Tier 2 -> 0.0035 |
#:     Tier 3 ($50M+) -> 0.0070 | Tier 4 ($200M+) -> 0.0104
#: "Maker rebates use the same tier coefficient in every market, including
#: ... crypto." The coefficient is fixed when the ORDER IS INSERTED.
TIER_SCHEDULE_UTC = dt.datetime(2026, 10, 29, 2, 0, tzinfo=dt.timezone.utc)
TIER_SOURCE_FETCHED = "2026-11-12"
TAKER_TIER_THETA = {1: D("0.095"), 2: D("0.085"), 3: D("0.075"), 4: D("0.0695")}
MAKER_TIER_THETA = {1: D("0"), 2: D("0.0035"), 3: D("0.0070"), 4: D("0.0104")}
#: this account's tiers. Volume-derived: our 30-day maker volume is a tiny
#: fraction of $10M and our taker volume is below $10,000, so Tier 1 / Tier
#: 1. A venue override or accelerated placement would be set here by the
#: owner after it is confirmed; nothing infers a better tier.
TAKER_TIER_ENV = "PMUS_TAKER_TIER"
MAKER_TIER_ENV = "PMUS_MAKER_TIER"
#: taker rebate under the tiered schedule: % of taker fees, paid WEEKLY
TAKER_REBATE_TIERS_FROM_TIERED = ((D("50000000"), D("0.30")),
                                  (D("25000000"), D("0.15")),
                                  (D("10000000"), D("0.10")),
                                  (D("1000000"), D("0.05")),
                                  (D("250000"), D("0.03")))

#: prices the venue will accept, and the published domain of the formula
MIN_PRICE, MAX_PRICE = D("0.01"), D("0.99")

#: prior-calendar-month TAKER notional -> share of the taker fee rebated
TAKER_REBATE_TIERS = ((D("10000000"), D("0.50")),
                      (D("1000000"), D("0.25")),
                      (D("250000"), D("0.10")))

CENT = D("0.01")


def _at(when: Optional[dt.datetime]) -> dt.datetime:
    if when is None:
        return dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.timezone.utc)
    return when


def _tier(env: str, given: Optional[int]) -> int:
    """The account tier: an explicit argument, else the env, else Tier 1.
    Anything unreadable or out of range is Tier 1 -- the dearest taker fee
    and no maker rebate -- so an error can only make economics look worse."""
    raw = given if given is not None else os.environ.get(env, "")
    try:
        t = int(str(raw).strip() or 1)
    except (TypeError, ValueError):
        return 1
    return t if t in (1, 2, 3, 4) else 1


def account_tiers(taker_tier: Optional[int] = None,
                  maker_tier: Optional[int] = None) -> Tuple[int, int]:
    return _tier(TAKER_TIER_ENV, taker_tier), _tier(MAKER_TIER_ENV, maker_tier)


def thetas(when: Optional[dt.datetime] = None, *,
           taker_tier: Optional[int] = None,
           maker_tier: Optional[int] = None) -> Tuple[D, D]:
    """(taker, maker-rebate) coefficients in force at `when` (order
    insertion time; now by default) for this account's tiers."""
    t = _at(when)
    if t < THETA_CHANGE_UTC:
        return THETA_BEFORE
    if t < TIER_SCHEDULE_UTC:
        return THETA_FROM
    tt, mt = account_tiers(taker_tier, maker_tier)
    return TAKER_TIER_THETA[tt], MAKER_TIER_THETA[mt]


def taker_theta(when: Optional[dt.datetime] = None, **tiers) -> D:
    return thetas(when, **tiers)[0]


def maker_theta(when: Optional[dt.datetime] = None, **tiers) -> D:
    return thetas(when, **tiers)[1]


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
              taker_tier: Optional[int] = None) -> D:
    """What a taker PAYS. Positive."""
    return _fee(taker_theta(when, taker_tier=taker_tier), contracts, price,
                cents=cents)


def maker_rebate(price: D, contracts: D = D(1), *,
                 when: Optional[dt.datetime] = None,
                 cents: bool = False,
                 maker_tier: Optional[int] = None) -> D:
    """What a maker RECEIVES, credited at the point of trade. Positive.

    NEVER net this against a cost to make an action look affordable. It is
    reported on its own line and it is income only once credited.
    """
    return _fee(maker_theta(when, maker_tier=maker_tier), contracts, price,
                cents=cents)


def taker_rebate_pct(prior_month_taker_notional_usd: D,
                     when: Optional[dt.datetime] = None) -> D:
    """Share of the taker fee rebated, from the published tier table.

    Returns 0 below the first tier. This account is below the first tier, so
    every economics figure in this programme carries 0 here.
    """
    v = D(str(prior_month_taker_notional_usd))
    table = (TAKER_REBATE_TIERS_FROM_TIERED
             if _at(when) >= TIER_SCHEDULE_UTC else TAKER_REBATE_TIERS)
    for floor, pct in table:
        if v >= floor:
            return pct
    return D("0")


def provenance(when: Optional[dt.datetime] = None) -> dict:
    t, m = thetas(when)
    tiered = _at(when) >= TIER_SCHEDULE_UTC
    tt, mt = account_tiers()
    return {
        "schedule": "TIERED" if tiered else "FLAT",
        "taker_tier": tt if tiered else None,
        "maker_tier": mt if tiered else None,
        "maker_rebate_available_to_this_account": bool(m > 0),
        "tier_schedule_effective_utc": TIER_SCHEDULE_UTC.isoformat(),
        "tier_source_fetched": TIER_SOURCE_FETCHED,
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
        "verified_against": ("all 99 published price rows reproduce exactly; "
                             "see tests/test_pmus_fees.py"),
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
