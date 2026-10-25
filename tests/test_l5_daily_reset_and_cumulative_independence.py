"""Pre-authorization verification the owner required before any live campaign.

Two properties, tested on the REAL ISO stamp format both settlement write sites
produce (dt.datetime.now(dt.timezone.utc).isoformat(), e.g.
"2026-10-25T20:03:40.448171+00:00"):

  1. the UTC-DAY loss figure really RESETS at midnight UTC
  2. the CUMULATIVE figure is INDEPENDENT and keeps counting every historical loss

If (1) failed silently the daily ceiling would never bind. If (2) failed the
$20 the owner approved would quietly become a reset of history.
"""
import datetime as dt
import json
import os
import tempfile
from decimal import Decimal as D

from src.l5_live_execution import InventoryStore


def _store(rows, baseline="2026-10-01T00:00:00+00:00"):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    state = {"l5_baseline": {"t": baseline}, "retired_markets": rows}
    with open(path, "w") as fh:
        json.dump(state, fh)
    s = InventoryStore(path)
    s.state = state           # the cache this class documents itself as
    return s


def _row(stamp, pnl, attested=True):
    return {"t": stamp, "settlement_pnl": str(pnl),
            "l5_owner_attribution_verified": attested}


TODAY = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
YESTERDAY = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=1)).strftime("%Y-%m-%d")


def test_the_real_iso_stamp_format_is_matched_at_all():
    """Guards the failure that would make the daily ceiling never bind."""
    s = _store({"m1": _row(TODAY + "T12:00:00.123456+00:00", "-1.25")})
    assert s.l5_realized_today() == D("-1.25")


def test_todays_loss_counts_today():
    s = _store({"m1": _row(TODAY + "T01:00:00+00:00", "-2.00"),
                "m2": _row(TODAY + "T23:59:59+00:00", "-3.00")})
    assert s.l5_realized_today() == D("-5.00")


def test_YESTERDAYS_loss_does_NOT_count_today__the_daily_reset():
    s = _store({"m1": _row(YESTERDAY + "T23:00:00+00:00", "-9.00"),
                "m2": _row(TODAY + "T01:00:00+00:00", "-1.00")})
    assert s.l5_realized_today() == D("-1.00")          # resets
    assert s.l5_realized_since_baseline() == D("-10.00")  # remembers


def test_cumulative_is_INDEPENDENT_and_keeps_every_historical_loss():
    s = _store({"m1": _row(YESTERDAY + "T10:00:00+00:00", "-4.00"),
                "m2": _row(YESTERDAY + "T11:00:00+00:00", "-6.00")})
    assert s.l5_realized_today() == D("0")             # nothing today
    assert s.l5_realized_since_baseline() == D("-10.00")  # all of it remains


def test_a_day_with_no_settlement_reads_zero_not_the_cumulative_figure():
    s = _store({"m1": _row(YESTERDAY + "T10:00:00+00:00", "-85.00")})
    assert s.l5_realized_today() == D("0")
    assert s.l5_realized_since_baseline() == D("-85.00")


def test_unattested_rows_are_excluded_from_BOTH():
    s = _store({"m1": _row(TODAY + "T10:00:00+00:00", "-5.00", attested=False)})
    assert s.l5_realized_today() == D("0")
    assert s.l5_realized_since_baseline() == D("0")


def test_pre_baseline_rows_are_excluded_from_BOTH():
    s = _store({"m1": _row("2026-09-30T10:00:00+00:00", "-7.00")})
    assert s.l5_realized_today() == D("0")
    assert s.l5_realized_since_baseline() == D("0")


def test_no_baseline_returns_None_from_BOTH_rather_than_zero():
    s = _store({"m1": _row(TODAY + "T10:00:00+00:00", "-1.00")}, baseline=None)
    s.state["l5_baseline"] = None
    assert s.l5_realized_today() is None
    assert s.l5_realized_since_baseline() is None


def test_an_unreadable_pnl_makes_BOTH_unknown_rather_than_zero():
    s = _store({"m1": {"t": TODAY + "T10:00:00+00:00",
                       "settlement_pnl": "not-a-number",
                       "l5_owner_attribution_verified": True}})
    assert s.l5_realized_today() is None
    assert s.l5_realized_since_baseline() is None
