"""The maker rebate: what the venue has actually CREDITED, and why our
model earns none of it.

THE EVIDENCE, from `analysis/l5/venue_rebate_credits.json`. 25 PMUS maker
fills, each a distinct venue fill id, each carrying a NEGATIVE
`commission` on the fill itself -- which is how "credited at the point of
trade" actually arrives. $0.42 over 119.49 contracts, $0.003515 a
contract. The markets are PMUS sports slugs from 2026-09-06..07; no
BTC15m credit has been observed.

THE ACCOUNTING UNIT IS THE FILL. Each row rounds to the cent on its own:
3.13 contracts credited $0.01, 5.15 contracts credited $0.02, and every
row's implied `p*(1-p)` band is consistent with `0.0125*C*p*(1-p)`. The
rebate is NOT aggregated per order, per market or per day before
rounding, and that is what creates the uplift below.

THE FLOOR IS PRICE-DEPENDENT, which is the executable part.
`0.0125*C*p*(1-p) > 0.005` gives `C > 0.4/(p*(1-p))`: 1.61 contracts at
the money, 2.09 at p=0.26, 4.45 at p=0.10. A clip chosen without
reference to its price can earn nothing at all -- a 2-contract fill at
p=0.26 is credited $0.00.

AND THE UPLIFT CANNOT RESCUE A LOSING STRATEGY. It is bounded by one
cent per fill, worth roughly 0.68% of cash deployed at our fill sizes,
against a measured trading leg of -22% a dollar in the current engine.
These tests pin the arithmetic, not a profit claim.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

from src import pmus_fees as FEES


def test_the_credited_rate_is_what_the_rows_actually_say():
    """Recompute the headline rate from the raw rows, not the summary."""
    d = json.load(open("analysis/l5/venue_rebate_credits.json"))
    rows = d["rows"]
    assert len(rows) == 25
    total = sum(-D(r["our_commission"]) for r in rows)
    qty = sum(D(r["qty"]) for r in rows)
    assert total == D("0.42")
    assert qty == D("119.49")
    # every row is a CREDIT (negative commission) on a MAKER fill
    for r in rows:
        assert D(r["our_commission"]) < 0, r["id"]
        assert r["our_side"] == "passiveExecution", r["id"]
        assert r["credited_to_us"] is True, r["id"]
        assert r["venue"] == "polymarket_us", r["id"]
    assert FEES.CREDITED_OBSERVED["usd_per_contract"] == D("0.003515")
    # the published figure agrees with the rows to the hundredth of a cent
    assert abs(total / qty - D("0.003515")) < D("0.00001")


#: THE REGIME THESE CREDITS WERE EARNED UNDER. The 25 rows are from
#: 2026-09-06/07, when a single maker coefficient of 0.0125 applied to
#: everyone. PMUS re-tiered the schedule on 2026-11-14 and this account is
#: Maker Tier 1, which receives NO rebate -- so `maker_theta()` is now 0
#: and every figure below would divide by it. Pricing this evidence at
#: today's coefficient would be re-rating history, which is the precise
#: thing this module is effective-dated to prevent.
FLAT = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


def test_this_account_is_paid_no_maker_rebate_today():
    """The counterpart to everything below: these credits are real and
    historical, and they are NOT evidence of income now."""
    assert FEES.maker_theta() == D("0")
    assert FEES.maker_rebate(D("0.50"), D("100")) == 0
    assert FEES.rebate_floor_contracts(D("0.50")) is None
    assert FEES.rebate_rounding_uplift(D("0.50"), D("100")) is None


def test_every_credited_row_is_consistent_with_the_published_formula():
    """Each cent must be reachable by `0.0125*C*p*(1-p)` at SOME legal p.

    The rows do not record the fill price, so the test asserts the
    credited cent is attainable -- i.e. the implied `p*(1-p)` band
    intersects the formula's range (0, 0.25]. A row that needed
    `p*(1-p) > 0.25` could not come from this schedule at all.
    """
    d = json.load(open("analysis/l5/venue_rebate_credits.json"))
    theta = FEES.maker_theta(FLAT)
    for r in d["rows"]:
        q = D(r["qty"]); c = -D(r["our_commission"])
        lo = (c - D("0.005")) / (theta * q)
        assert lo < D("0.25"), (r["id"], lo)       # attainable
        assert lo >= 0 or c == D("0.01"), (r["id"], lo)


def test_the_floor_is_price_dependent_and_a_unit_clip_never_clears_it():
    """1.61 contracts at the money, 2.09 at p=0.26, 4.45 at p=0.10."""
    assert FEES.rebate_floor_contracts(D("0.50"), FLAT) == D("1.6")
    f26 = FEES.rebate_floor_contracts(D("0.26"), FLAT)
    f10 = FEES.rebate_floor_contracts(D("0.10"), FLAT)
    assert D("2.07") < f26 < D("2.09"), f26
    assert D("4.44") < f10 < D("4.45"), f10
    # a unit clip is below the floor at EVERY legal price, so it is
    # credited nothing anywhere
    for cents_price in range(1, 100):
        p = D(cents_price) / D(100)
        assert FEES.maker_rebate(p, D(1), cents=True, when=FLAT) == \
            D("0.00"), p


def test_the_rounding_uplift_exists_and_is_bounded():
    """Just above the floor the venue pays about twice the accrual."""
    up = FEES.rebate_rounding_uplift(D("0.50"), D("1.61"), FLAT)
    assert D("1.9") < up < D("2.1"), up
    # and it is NOT free money at every size: below the floor it is zero,
    # and above it the whole cent gets diluted
    assert FEES.rebate_rounding_uplift(D("0.50"), D("1"), FLAT) == 0
    assert FEES.rebate_rounding_uplift(D("0.26"), D("2"), FLAT) == 0
    assert FEES.rebate_rounding_uplift(D("0.50"), D("10.49"), FLAT) < 1


def test_a_two_contract_clip_is_not_assumed_to_earn_a_penny():
    """The owner's exact caution, pinned.

    At the money a 2-contract fill does round up; at p=0.26 it does not.
    So 'two contracts earns a cent' is FALSE as a general claim, and the
    evidence floor stays at the smallest clip actually credited, 2.52.
    """
    assert FEES.maker_rebate(D("0.50"), D("2"), cents=True,
                             when=FLAT) == D("0.01")
    assert FEES.maker_rebate(D("0.26"), D("2"), cents=True,
                             when=FLAT) == D("0.00")
    #: and today the claim is false at EVERY price, because the rebate is
    #: zero rather than small
    assert FEES.maker_rebate(D("0.50"), D("2"), cents=True) == D("0.00")
    assert FEES.CREDITED_OBSERVED[
        "smallest_credited_clip_contracts"] == D("2.52")


def test_the_taker_rebate_stays_zero_for_this_account():
    """Tiers start at $250,000 of prior-month taker notional."""
    assert FEES.taker_rebate_pct(D("249999")) == 0
    assert FEES.provenance()[
        "taker_rebate_available_to_this_account"] is False


def test_the_shadow_reports_confirmed_and_modelled_apart():
    """And says WHY its own rebate is zero."""
    from src import l5_shadow_controller as SC
    acc = SC.ShadowController._rebate_accounting(SC.ShadowController)
    assert acc["confirmed_rate_usd_per_contract"] == "0.003515"
    assert acc["confirmed_rows"] == 25
    assert acc["confirmed_evidence_floor_contracts"] == "2.52"
    assert acc["credited_at_p050_usd"] == "0.00"
    assert acc["modelled_clip_contracts"] == "1"
    assert "PER FILL" in acc["accounting_unit"]
    assert "never summed" in acc["labels"]
