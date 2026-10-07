"""The fee-schedule correction, pinned.

Three things must not drift: the frozen fingerprint, the effective-dating
itself (a scalar cannot price a straddling corpus), and the fact that the
MAKER coefficient did not move -- which is what makes this a taker-only
correction and leaves every maker-rebate figure where it was.
"""
import datetime as dt
from decimal import Decimal as D

import pytest

from src import pmus_fee_schedule as S
from src import pmus_incentive_audit as ia


BEFORE = S.parse_ts("2026-09-10T17:14:00Z")      # our verbatim doc capture
AFTER = S.parse_ts("2026-10-07T00:00:00Z")       # today


def test_the_schedule_is_frozen_against_a_hardcoded_literal():
    S.assert_frozen()
    assert S.FINGERPRINT == \
        "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629"
    assert len(S.FINGERPRINT) == 64


def test_the_assertion_is_not_vacuous():
    """A mutated schedule must fail. If assert_frozen recomputed its own
    expectation this test would pass trivially and prove nothing."""
    import copy
    bad = copy.deepcopy(S.SCHEDULE)
    bad["entries"][-1]["taker_theta"] = "0.07"
    with pytest.raises(AssertionError):
        S.assert_frozen(bad)


def test_the_taker_coefficient_is_effective_dated_not_a_scalar():
    assert S.taker_theta(BEFORE) == D("0.06")
    assert S.taker_theta(AFTER) == D("0.0695")
    # a fill is priced by the schedule at ITS OWN timestamp
    assert S.price_fill("0.45", "100", fill_ts="2026-09-10T00:00:00Z",
                        is_taker=True)["theta"] == "0.06"
    assert S.price_fill("0.45", "100", fill_ts="2026-10-05T00:00:00Z",
                        is_taker=True)["theta"] == "0.0695"


def test_the_maker_coefficient_did_not_move_so_no_rebate_figure_can():
    assert S.maker_theta(BEFORE) == S.maker_theta(AFTER) == D("0.0125")
    for e in S.SCHEDULE["entries"]:
        assert e["maker_rebate_theta"] == "0.0125"
    assert S.SCHEDULE["maker_coefficient_moved"] is False
    # the figures the audit publishes from the maker coefficient
    assert ia.MAKER_REBATE_RATE == D("0.0125")
    assert ia.min_qty_for_nonzero_rebate(D("0.45")) == "1.62"
    assert ia.rebate_at(D("10"), D("0.45"))[
        "credited_after_rounding_usd"] == "0.03"
    assert S.maker_rebate(D("0.50"), D("100"), when=BEFORE, cents=True) == \
        S.maker_rebate(D("0.50"), D("100"), when=AFTER, cents=True) == \
        D("0.31")                      # published max rebate per 100 contracts


def test_the_published_max_taker_fee_reproduces_on_both_sides_of_the_change():
    # docs.polymarket.us/fees: max taker $1.50/100 at 0.06, $1.74 at 0.0695
    assert S.taker_fee(D("0.50"), D("100"), when=BEFORE, cents=True) == D("1.50")
    assert S.taker_fee(D("0.50"), D("100"), when=AFTER, cents=True) == D("1.74")


def test_the_stale_scalar_is_gone_from_the_audit_module():
    assert not hasattr(ia, "TAKER_RATE")
    import inspect
    # no module-level assignment of it survives (the only mentions left are
    # the docstring/comment that RECORDS the correction)
    assert not [ln for ln in inspect.getsource(ia).splitlines()
                if ln.startswith("TAKER_RATE")]


def test_the_date_conflict_is_recorded_and_flagged_not_resolved_silently():
    d = S.SCHEDULE["discrepancies"][0]
    assert "2026-09-17" in d["primary"] and "2026-10-01" in d["secondary"]
    assert S.date_conflict_window(S.parse_ts("2026-09-20T00:00:00Z")) is True
    assert S.date_conflict_window(S.parse_ts("2026-09-10T00:00:00Z")) is False
    assert S.date_conflict_window(S.parse_ts("2026-10-05T00:00:00Z")) is False
    assert S.price_fill("0.45", "100", fill_ts="2026-09-20T00:00:00Z",
                        is_taker=True)["effective_date_ambiguous"] is True


def test_it_does_not_disagree_with_the_execution_paths_own_schedule():
    S.assert_consistent_with_pmus_fees()


def test_the_rebate_is_returned_as_a_positive_credit_never_a_negative_fee():
    assert S.maker_rebate(D("0.45"), D("10")) > 0
    out = S.price_fill("0.45", "10", fill_ts="2026-09-07T00:00:00Z")
    assert out["sign"] == "credited_to_us"
    assert "not a credit" in out["label"]       # estimate, not income


def test_nanosecond_venue_timestamps_parse():
    assert S.parse_ts("2026-09-14T22:39:20.206118633Z") == \
        dt.datetime(2026, 9, 14, 22, 39, 20, 206118, tzinfo=dt.timezone.utc)


def test_the_audit_publishes_both_eras_rather_than_one_number():
    by = {e["effective_from_utc"]: e for e in S.SCHEDULE["entries"]}
    assert set(by) == {"2026-07-01T04:00:00+00:00", "2026-09-17T04:00:00+00:00"}
    assert S.taker_rebate_pct(0) == D("0")       # below the first tier
    assert S.taker_rebate_pct(300000) == D("0.10")
