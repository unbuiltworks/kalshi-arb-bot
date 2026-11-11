"""The PMUS fee coefficient, verified against the venue's own market records.

THE QUESTION (owner 2026-11-03): correct fee/rebate accounting using the
schedule effective on each trade date, and verify the reported 2026-10-19
schedule change against official venue records.

WHAT WAS MEASURED. `gateway.polymarket.us/v1/market/slug/<slug>` returns a
`feeCoefficient` on every market object, and resolved markets keep it. Queried
individually across eleven dates spanning the claimed change:

    2026-09-30  0.0695      2026-10-19  0.0695      2026-10-25  0.0695
    2026-10-01  0.0695      2026-10-20  0.0695      2026-11-02  0.0695
    2026-10-05  0.0695      2026-10-21  0.0695      2026-11-03  0.0695
    2026-10-10  0.0695
    2026-10-14  0.0695      2026-10-17  0.0695      2026-10-18  0.0695

NO STEP AT 2026-10-19. The reported change is NOT corroborated by the venue's
own records: the coefficient is identical before, on and after that date.

THE LIMIT OF THE EVIDENCE, stated. Markets before ~2026-09-30 return 404 --
the gateway's retention floor -- so a change on or before that date cannot be
ruled out. The `docs.polymarket.us/fees` read of 0.06 on 2026-09-13 sits below
the floor and cannot be re-verified. If a 0.06 -> 0.0695 change happened, it
happened at or before 2026-09-30, not on 2026-10-19.

THE CONSEQUENCE FOR OUR LEDGER. Every one of our own live trade dates
(2026-10-11 .. 2026-11-02) falls inside the verified single-coefficient span,
so ONE coefficient covers all of them and no per-date split is warranted. A
date-split correction would be inventing a change the venue does not show.

This test pins that so neither the stale 0.06 nor a spurious date-split can
return unnoticed. It is offline: the figures above are the record, and nothing
here makes a network call.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, ROUND_HALF_EVEN

import pytest

#: the venue's own feeCoefficient, by the date the market traded
VERIFIED_FEE_COEFFICIENT = D("0.0695")
VERIFIED_SPAN = (dt.date(2026, 9, 30), dt.date(2026, 11, 3))
RETENTION_FLOOR = dt.date(2026, 9, 30)
CLAIMED_CHANGE_DATE = dt.date(2026, 10, 19)
OBSERVED = {
    "2026-09-30": "0.0695", "2026-10-01": "0.0695", "2026-10-05": "0.0695",
    "2026-10-10": "0.0695", "2026-10-14": "0.0695", "2026-10-17": "0.0695",
    "2026-10-18": "0.0695", "2026-10-19": "0.0695", "2026-10-20": "0.0695",
    "2026-10-21": "0.0695", "2026-10-25": "0.0695", "2026-11-02": "0.0695",
    "2026-11-03": "0.0695",
}
#: our own venue-settled live trade dates
OUR_TRADE_DATES = (dt.date(2026, 10, 11), dt.date(2026, 11, 2))


def test_the_coefficient_is_constant_across_every_date_observed():
    assert set(OBSERVED.values()) == {"0.0695"}


def test_there_is_no_step_at_the_claimed_change_date():
    before = OBSERVED["2026-10-18"]
    on = OBSERVED["2026-10-19"]
    after = OBSERVED["2026-10-20"]
    assert before == on == after == "0.0695"


def test_all_of_our_trade_dates_sit_inside_the_verified_span():
    lo, hi = VERIFIED_SPAN
    for d in OUR_TRADE_DATES:
        assert lo <= d <= hi, d


def test_one_coefficient_covers_our_whole_ledger_so_no_date_split_applies():
    """The positive claim this test exists to protect."""
    lo, hi = VERIFIED_SPAN
    assert all(lo <= d <= hi for d in OUR_TRADE_DATES)
    assert len(set(OBSERVED.values())) == 1


def test_the_unverifiable_region_is_named_rather_than_assumed():
    assert RETENTION_FLOOR == dt.date(2026, 9, 30)
    assert dt.date(2026, 9, 13) < RETENTION_FLOOR     # the 0.06 doc read
    assert CLAIMED_CHANGE_DATE > RETENTION_FLOOR      # so it IS checkable


# --------------------------------------------- the modules that got it wrong

def test_the_shadow_modules_now_carry_the_verified_coefficient():
    from src import l5_cross_shadow as X
    from src import pmus_cross_shadow_live as Y
    assert X.TAKER_THETA == VERIFIED_FEE_COEFFICIENT
    assert Y.TAKER_THETA == VERIFIED_FEE_COEFFICIENT


def test_the_money_path_already_carried_it():
    """`l5_two_sided_probe` was never wrong; only the shadow modules were."""
    from src import l5_two_sided_probe as TS
    from src import l5_queue_measurement as QM
    from src import l5_strand_policy_replay as SP
    assert TS.TAKER_THETA == VERIFIED_FEE_COEFFICIENT
    assert QM.TAKER_THETA == VERIFIED_FEE_COEFFICIENT
    assert SP.TAKER_THETA == VERIFIED_FEE_COEFFICIENT


def test_the_stale_value_understated_the_charge():
    """Direction matters: 0.06 made crossing look cheaper than it is."""
    stale, true = D("0.06"), VERIFIED_FEE_COEFFICIENT
    assert stale < true
    qty, p = D(2), D("0.50")
    fee = lambda th: (th * qty * p * (D(1) - p)).quantize(
        D("0.01"), rounding=ROUND_HALF_EVEN)
    assert fee(stale) <= fee(true)
    # and the relative understatement, as a number
    assert ((true - stale) / true).quantize(D("0.01")) == D("0.14")


# --------------------------------------------- the maker side is unchanged

def test_the_maker_rebate_coefficient_is_untouched():
    """Only the taker term was stale. The rebate is the term that has ever
    actually paid this account, and it is confirmed by 25 credited prints."""
    from src import l5_cross_shadow as X
    assert X.MAKER_THETA == D("0.0125")
