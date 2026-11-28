"""Can the engine finish what it starts?

TWO DEPLOYED NUMBERS DECIDE DIFFERENT HALVES OF ONE CYCLE and nothing
made them agree: the OPENING clip sizes the first leg, and the TCP
per-cross cap bounds what retiring that leg by crossing may cost.
Completing C contracts by crossing costs up to `C * 0.99 + taker_fee`.

THE DEPLOYED CONFIGURATION FAILS IT. clip 3 -> worst cross outlay $2.97
against a $1.50 cap. So the live engine cannot retire its own opening leg
by crossing at any price it would ever need to.

MEASURED ON THE RECORDED TAPE, 10 terminal residuals: 7 refused on the
1.20 pair ceiling (pairs 1.23..1.38) and TWO refused purely on outlay at
pairs of 1.13 and 1.18 -- economically sound completions blocked by a
cash cap rather than by economics.

AND IT COLLIDES WITH THE REBATE FLOOR. The maker rebate rounds per fill
to the cent, so a clip below `0.4/(p*(1-p))` -- 1.61 contracts at the
money, and 2.52 is the smallest ever actually credited -- earns nothing,
while a clip above ~1.45 cannot be crossed out within a $1.50 cap. Both
cannot be satisfied. That is an owner decision, and this row states it
rather than patching either number.

OBSERVED, NEVER GRADED: a maker-only deployment that never crosses is
not defective for holding an uncompletable clip.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

from src import l5_runtime_config_verify as RCV
from src import pmus_fees as FEES


def test_the_row_states_the_arithmetic_and_the_verdict():
    s = RCV._clip_vs_cross_cap()
    assert "clip" in s and "worst cross outlay" in s and "cap" in s
    assert ("COMPLETABLE" in s) or ("UNKNOWN" in s)
    # it must name BOTH collision bounds, or it is not actionable
    assert "rebate floor" in s
    assert "confirmed floor" in s


def test_the_deployed_clip_is_not_completable_within_the_deployed_cap():
    """The finding, pinned. 3 contracts needs $2.97; the cap is $1.50."""
    s = RCV._clip_vs_cross_cap()
    assert "NOT_COMPLETABLE" in s, s
    assert "clip 3 " in s, s


def test_a_small_enough_clip_is_completable():
    """And the row flips, so it is measuring and not asserting."""
    from src import l5_two_sided_probe as TSP
    prev = os.environ.get(TSP.OPENING_LEG_MAX_ENV)
    os.environ[TSP.OPENING_LEG_MAX_ENV] = "1"
    try:
        s = RCV._clip_vs_cross_cap()
    finally:
        if prev is None:
            os.environ.pop(TSP.OPENING_LEG_MAX_ENV, None)
        else:
            os.environ[TSP.OPENING_LEG_MAX_ENV] = prev
    assert "COMPLETABLE" in s and "NOT_COMPLETABLE" not in s, s


def test_no_clip_earns_a_maker_rebate_at_this_accounts_tier():
    """The exclusivity below is now VACUOUS and that is the finding.

    PMUS re-tiered on 2026-11-14 and this account is Maker Tier 1, which
    is credited nothing, so there is no rebate floor to clear at any
    size. `rebate_floor_contracts` returns None rather than a number,
    which is the correct answer to "how big must a clip be to earn a
    rebate" when the answer is "no size will".
    """
    assert FEES.maker_theta() == D("0")
    assert FEES.rebate_floor_contracts(D("0.50")) is None


def test_the_two_bounds_are_genuinely_mutually_exclusive():
    """A clip cannot both earn the rebate and be crossable at $1.50.

    PRICED AT THE FLAT REGIME, when a rebate existed to be earned. Kept
    because it is the arithmetic that justified the clip ceiling, and
    that ceiling has not moved.
    """
    import datetime as _dt
    flat = _dt.datetime(2026, 10, 1, tzinfo=_dt.timezone.utc)
    cap = D("1.50")
    floor = FEES.rebate_floor_contracts(D("0.50"), flat)
    worst_at_floor = floor * D("0.99") + FEES.taker_fee(
        D("0.99"), floor, cents=True)
    assert worst_at_floor > cap, (floor, worst_at_floor)
    # and the largest crossable clip earns nothing
    crossable = D("1.45")
    assert crossable < floor
    assert FEES.maker_rebate(D("0.50"), crossable, cents=True) == D("0.00")


def test_it_reports_UNKNOWN_rather_than_guessing(monkeypatch):
    """An unreadable clip must not be reported as completable.

    The env route cannot produce this: `opening_leg_max_contracts` falls
    back to its code default rather than going unreadable, which is the
    safe behaviour and is why this patches the accessor instead.
    """
    from src import l5_two_sided_probe as TSP
    monkeypatch.setattr(TSP, "opening_leg_max_contracts", lambda: None)
    assert RCV._clip_vs_cross_cap().startswith("UNKNOWN")
    monkeypatch.setattr(TSP, "opening_leg_max_contracts",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert RCV._clip_vs_cross_cap().startswith("UNKNOWN")


def test_it_is_observed_and_not_graded():
    """It must never fail a deployment that runs maker-only."""
    out = RCV.verify()
    names = {r["name"] for r in out.get("observed_not_graded", [])}
    assert "opening_clip_completable_within_taker_cap" in names
    graded = {r["name"] for r in out.get("rows", [])}
    assert "opening_clip_completable_within_taker_cap" not in graded
