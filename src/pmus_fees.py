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


def thetas(when: Optional[dt.datetime] = None) -> Tuple[D, D]:
    """(taker, maker) coefficients in force at `when`; now by default."""
    return THETA_FROM if _at(when) >= THETA_CHANGE_UTC else THETA_BEFORE


def taker_theta(when: Optional[dt.datetime] = None) -> D:
    return thetas(when)[0]


def maker_theta(when: Optional[dt.datetime] = None) -> D:
    return thetas(when)[1]


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
              when: Optional[dt.datetime] = None, cents: bool = False) -> D:
    """What a taker PAYS. Positive."""
    return _fee(taker_theta(when), contracts, price, cents=cents)


def maker_rebate(price: D, contracts: D = D(1), *,
                 when: Optional[dt.datetime] = None,
                 cents: bool = False) -> D:
    """What a maker RECEIVES, credited at the point of trade. Positive.

    NEVER net this against a cost to make an action look affordable. It is
    reported on its own line and it is income only once credited.
    """
    return _fee(maker_theta(when), contracts, price, cents=cents)


def taker_rebate_pct(prior_month_taker_notional_usd: D) -> D:
    """Share of the taker fee rebated, from the published tier table.

    Returns 0 below the first tier. This account is below the first tier, so
    every economics figure in this programme carries 0 here.
    """
    v = D(str(prior_month_taker_notional_usd))
    for floor, pct in TAKER_REBATE_TIERS:
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
        "verified_against": ("all 99 published price rows reproduce exactly; "
                             "see tests/test_pmus_fees.py"),
    }
