"""The placement band: submission only between T+5:00 and T+7:30.

WHY THIS EXISTS. On 2026-10-19 the 0330z attempt reached the submit at
T+3:50 -- seventy seconds BEFORE the placement window the owner had
authorized. Nothing reached the venue only because the submission claim was
already spent. The cause was structural: every gate was about the ORDER and
none was about the CLOCK, so the mechanism submitted the instant arming
completed, at whatever time the deployment schedule happened to produce.

WHAT IS PINNED HERE: the band is measured from the VENUE's own confirmed
window open and never from our slug grid or from process start; early is a
WAIT and late is TERMINAL; both edges are inclusive; a restart cannot reset
the band; and every way of NOT establishing the elapsed time -- no bounds, an
unconfirmed window, a non-venue source, unreadable bounds, bounds belonging
to another window, a read that raises, a window too short to contain the band
-- holds the order back instead of letting it through.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import l5_cancel_mechanism as M                       # noqa: E402
from src import l5_live_authorization as AUTH                  # noqa: E402
from test_l5_cancel_mechanism_leg import (                     # noqa: E402
    MARKET, WS, Clock, Store, Transport, mech, venue_bounds)

EARLIEST = 300.0          # T+5:00
LATEST = 450.0            # T+7:30 on a 900s window


def at(monkeypatch, elapsed, *, bounds=venue_bounds, transport=None,
       store=None):
    """A mechanism whose clock sits `elapsed` seconds into the venue window."""
    return mech(monkeypatch, transport=transport or Transport(),
                store=store or Store(), clock=Clock(float(WS) + elapsed),
                window_bounds=bounds)


def bounds_with(**over):
    def _f():
        b = dict(venue_bounds())
        b.update(over)
        return b
    return _f


# ===================================================== the band's own numbers

def test_the_band_opens_five_minutes_into_the_window():
    assert M.PLACEMENT_EARLIEST_S == EARLIEST


def test_the_upper_edge_is_derived_from_the_watchdog_requirement(monkeypatch):
    """Not a second declared number: span - MIN_WINDOW_REMAINING_S. On a 900s
    window that is T+7:30, so the band can never permit a placement that
    `window_has_room_for_the_watchdog` would then have to refuse."""
    band = at(monkeypatch, EARLIEST).placement_band()
    assert band["latest_s"] == LATEST
    assert band["latest_s"] == 900.0 - M.MIN_WINDOW_REMAINING_S


def test_venue_source_matches_the_authorization_module():
    """`VENUE_SOURCE` is a local mirror so a per-tick gate never depends on
    an import succeeding. Drift must fail here, not in a live window."""
    assert M.VENUE_SOURCE == AUTH.WINDOW_SOURCE_VENUE


def test_the_band_fits_inside_the_window_it_is_measured_in():
    assert EARLIEST < LATEST < 900.0
    # the watchdog must still get its 300s plus slack after the LATEST placement
    assert LATEST + M.SELF_CANCEL_AFTER_S <= 900.0


# ===================================================================== EARLY

def test_too_early_waits_and_sends_nothing(monkeypatch):
    t = Transport()
    rep = at(monkeypatch, 200.0, transport=t).step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert "placement_band_reached" in rep["waiting_on"]
    assert t.submits == []


def test_too_early_is_not_a_refusal(monkeypatch):
    """A boot at T+1:00 must live to place at T+5:00, not die."""
    m = at(monkeypatch, 60.0)
    m.step()
    assert m.state == M.Mechanism.INIT
    assert m.finished is False
    assert m.refusal is None


def test_the_early_wait_spends_neither_the_marker_nor_the_claim(monkeypatch):
    st, t = Store(), Transport()
    at(monkeypatch, 100.0, transport=t, store=st).step()
    assert M.MARKER_KEY not in st.state
    assert t.submits == []


def test_early_then_inside_the_band_submits(monkeypatch):
    """THE BEHAVIOURAL FIX, end to end: the 0330z sequence now waits and
    then places inside the band instead of placing early."""
    t = Transport()
    m = at(monkeypatch, 230.0, transport=t)
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    assert t.submits == []
    m.clock.t = float(WS) + 320.0
    assert m.step()["submitted"] == 1
    assert len(t.submits) == 1


# ===================================================================== VALID

@pytest.mark.parametrize("elapsed", [300.0, 301.0, 375.0, 449.0, 450.0])
def test_inside_the_band_submits(monkeypatch, elapsed):
    t = Transport()
    assert at(monkeypatch, elapsed, transport=t).step()["submitted"] == 1
    assert len(t.submits) == 1


# ====================================================================== LATE

def test_past_the_band_refuses_terminally(monkeypatch):
    t = Transport()
    m = at(monkeypatch, 451.0, transport=t)
    rep = m.step()
    assert rep["refused"] == "MECHANISM_PRECONDITIONS_NOT_MET"
    assert "placement_band_not_expired" in rep["failed_checks"]
    assert m.finished is True
    assert t.submits == []


def test_late_is_never_waited_out(monkeypatch):
    """Unlike early, late does not cure itself, so it must not be a WAIT."""
    assert "placement_band_not_expired" not in M.WAIT_CHECKS
    m = at(monkeypatch, 600.0)
    m.step()
    assert m.state == M.Mechanism.REFUSED


def test_a_wait_that_runs_past_the_band_ends_in_a_refusal(monkeypatch):
    """The wait is bounded by the band, with no timer."""
    t = Transport(armed=False)
    m = at(monkeypatch, 310.0, transport=t)
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    m.clock.t = float(WS) + 470.0
    assert m.step()["LABEL"] == "L5_MECH_REFUSED"
    assert t.submits == []


# ========================================================== CLOCK BOUNDARIES

@pytest.mark.parametrize("elapsed,reached", [
    (299.9, False), (299.999, False),
    (300.0, True),                       # inclusive: the band is [300, 450]
    (450.0, True),
    (450.1, True),                       # reached, but expired below
])
def test_the_lower_edge_is_inclusive(monkeypatch, elapsed, reached):
    band = at(monkeypatch, elapsed).placement_band()
    got = float(band["elapsed_s"]) >= float(band["earliest_s"])
    assert got is reached, band


@pytest.mark.parametrize("elapsed,expired", [
    (449.9, False), (450.0, False),      # inclusive
    (450.1, True), (451.0, True),
])
def test_the_upper_edge_is_inclusive(monkeypatch, elapsed, expired):
    band = at(monkeypatch, elapsed).placement_band()
    assert (float(band["elapsed_s"]) > float(band["latest_s"])) is expired


@pytest.mark.parametrize("elapsed,submits", [
    (299.9, 0), (300.0, 1), (450.0, 1), (450.1, 0)])
def test_the_edges_decide_submission(monkeypatch, elapsed, submits):
    t = Transport()
    at(monkeypatch, elapsed, transport=t).step()
    assert len(t.submits) == submits, elapsed


@pytest.mark.parametrize("elapsed", [299.96, 299.999, 299.9999])
def test_rounding_cannot_open_the_band_early(monkeypatch, elapsed):
    """REGRESSION. Rounded to a tenth, 299.999 became 300.0 and the band
    opened 1ms early -- a value rounded for the report was deciding the gate.
    The recorded number and the gate's number are now the same one."""
    band = at(monkeypatch, elapsed).placement_band()
    assert float(band["elapsed_s"]) < EARLIEST, band
    t = Transport()
    at(monkeypatch, elapsed, transport=t).step()
    assert t.submits == []


def test_the_gate_reads_exactly_what_the_record_shows(monkeypatch):
    """No second, unrounded number behind the reported one."""
    for elapsed in (299.9, 300.0, 374.5, 450.0, 450.1):
        m = at(monkeypatch, elapsed, transport=Transport())
        band = m.placement_band()
        permitted = (float(band["elapsed_s"]) >= float(band["earliest_s"])
                     and float(band["elapsed_s"]) <= float(band["latest_s"]))
        got = [c for c in m.checks(m.plan())
               if c["check"] in ("placement_band_reached",
                                 "placement_band_not_expired")]
        assert all(c["ok"] for c in got) is permitted, (elapsed, band)


# =================================================================== RESTART

def test_a_restart_inside_the_band_can_still_place(monkeypatch):
    """The band is wall-clock from the venue open, so a process that starts
    at T+5:20 is inside it."""
    t = Transport()
    assert at(monkeypatch, 320.0, transport=t).step()["submitted"] == 1


def test_a_restart_cannot_reset_the_band(monkeypatch):
    """A brand-new object at T+8:20 is LATE. Nothing about the band is
    measured from process start, so restarting does not buy a new band."""
    t = Transport()
    m = at(monkeypatch, 500.0, transport=t)
    assert m.waits == 0 and m.state == M.Mechanism.INIT   # genuinely fresh
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_REFUSED"
    assert "placement_band_not_expired" in rep["failed_checks"]
    assert t.submits == []


def test_a_restart_before_the_band_still_waits(monkeypatch):
    t = Transport()
    rep = at(monkeypatch, 10.0, transport=t).step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert t.submits == []


# =============================================== FAIL CLOSED: elapsed unknown

@pytest.mark.parametrize("bounds,why", [
    (None, "NO_BOUNDS_SUPPLIED"),
    (lambda: None, "WINDOW_NOT_VENUE_CONFIRMED"),
    (lambda: {}, "WINDOW_NOT_VENUE_CONFIRMED"),
    (bounds_with(source="CLOCK"), "WINDOW_BOUNDS_NOT_VENUE_SOURCED"),
    (bounds_with(source=""), "WINDOW_BOUNDS_NOT_VENUE_SOURCED"),
    (bounds_with(start="not-a-time"), "WINDOW_BOUNDS_UNREADABLE"),
    (bounds_with(start=None), "WINDOW_BOUNDS_UNREADABLE"),
])
def test_an_unestablished_band_never_passes(monkeypatch, bounds, why):
    t = Transport()
    m = at(monkeypatch, 375.0, transport=t, bounds=bounds)
    band = m.placement_band()
    assert band["elapsed_s"] is None
    assert band["why"] == why, band
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert "placement_band_reached" in rep["waiting_on"]
    assert t.submits == []


def test_bounds_for_another_window_fail_closed(monkeypatch):
    """The runner's bounds answer for ITS current window and cache per
    window; this object carries its own market. A stale pairing would measure
    the band from the wrong open, which is the exact error being fixed."""
    other = dt.datetime.fromtimestamp(WS - 900, dt.timezone.utc)
    t = Transport()
    m = at(monkeypatch, 375.0, transport=t,
           bounds=bounds_with(start=other.isoformat(),
                              end=(other + dt.timedelta(seconds=900)).isoformat()))
    band = m.placement_band()
    assert band["why"] == "VENUE_BOUNDS_ARE_NOT_THIS_MARKETS"
    assert band["elapsed_s"] is None
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    assert t.submits == []


def test_a_bounds_read_that_raises_fails_closed_without_escaping(monkeypatch):
    def boom():
        raise RuntimeError("venue read failed")
    t = Transport()
    m = at(monkeypatch, 375.0, transport=t, bounds=boom)
    band = m.placement_band()
    assert band["why"] == "BOUNDS_READ_FAILED:RuntimeError"
    assert band["elapsed_s"] is None
    assert m.step()["LABEL"] == "L5_MECH_WAIT"      # did not raise
    assert t.submits == []


def test_a_window_too_short_to_contain_the_band_fails_closed(monkeypatch):
    """span 600 leaves 150s of band, which is less than the 300s opening
    edge. Not silently narrowed to fit -- held closed."""
    start = dt.datetime.fromtimestamp(WS, dt.timezone.utc)
    t = Transport()
    m = at(monkeypatch, 160.0, transport=t,
           bounds=bounds_with(end=(start + dt.timedelta(seconds=600)).isoformat()))
    band = m.placement_band()
    assert band["why"] == "WINDOW_TOO_SHORT_FOR_THE_BAND"
    assert band["elapsed_s"] is None
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    assert t.submits == []


def test_an_inverted_window_fails_closed(monkeypatch):
    start = dt.datetime.fromtimestamp(WS, dt.timezone.utc)
    m = at(monkeypatch, 375.0,
           bounds=bounds_with(end=(start - dt.timedelta(seconds=60)).isoformat()))
    assert m.placement_band()["why"] == "WINDOW_BOUNDS_UNREADABLE"


def test_an_unconfirmed_window_cannot_wait_forever(monkeypatch):
    """Unconfirmed caches as None for the whole window, so the WAIT must be
    ended by a terminal gate rather than held open to the close."""
    t = Transport()
    m = at(monkeypatch, 375.0, transport=t, bounds=lambda: None)
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    m.clock.t = float(WS) + 470.0
    assert m.step()["LABEL"] == "L5_MECH_REFUSED"
    assert t.submits == []


# ====================================================== nothing else moved

def test_no_existing_gate_was_removed(monkeypatch):
    """The band ADDS two checks. Every pre-existing gate must still be run,
    HALT and the watchdog bound among them."""
    names = [c["check"] for c in
             at(monkeypatch, 375.0).checks(at(monkeypatch, 375.0).plan())]
    for gate in ("mode_enabled", "one_shot_not_already_used",
                 "book_two_sided_authenticated", "price_readable",
                 "price_positive", "price_below_one",
                 "price_is_nonmarketable", "post_only",
                 "qty_is_venue_minimum",
                 "worst_case_total_within_authorization",
                 "window_has_room_for_the_watchdog", "transport_armed",
                 "book_is_flat_before_the_test", "no_other_live_order",
                 "authorization_window_open", "halt_not_engaged"):
        assert gate in names, gate
    assert "placement_band_reached" in names
    assert "placement_band_not_expired" in names


def test_the_band_cannot_override_halt_or_the_watchdog_bound(monkeypatch):
    """Being inside the band is permission for the CLOCK only."""
    t = Transport()
    m = at(monkeypatch, 375.0, transport=t)
    m._halted_fn = lambda: True
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_REFUSED"
    assert "halt_not_engaged" in rep["failed_checks"]
    assert t.submits == []


def test_the_band_is_reported_for_the_audit(monkeypatch):
    band = at(monkeypatch, 375.0).placement_band()
    assert band["source"] == "VENUE"
    assert band["market"] == MARKET
    assert band["venue_start_utc"]
    assert band["why"] == "OK"
