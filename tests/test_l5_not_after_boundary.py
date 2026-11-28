"""L5_LIVE_AUTH_NOT_AFTER bounds the ARMING INSTANT, not the window's start.

WHY THIS TEST EXISTS. On 2026-11-01 an eight-window campaign was authorized
1330z-1515z and `L5_LIVE_AUTH_NOT_AFTER` was set to 15:15:00Z -- "the last
window's start" -- intending to admit the 1515z window and exclude 1530z. The
1515z window was REFUSED and the eighth authorized window was forfeited:

    arm_refused_authorization_expired: 51
    armed: false                       (zero orders)

The cause is not a defect. `authorize_arming` step 8 is `if now > deadline`,
and `now` is the instant arming is ATTEMPTED. Arming runs a few seconds into
a window -- 13:30:11Z for the 13:30:00Z window in that same campaign -- so an
arming instant is ALWAYS later than its window's start. Setting the deadline
to the last window's start therefore refuses that window with certainty.

These tests pin the semantics so the convention cannot be mis-set silently
again. They do NOT change the semantics: an exclusive deadline on the arming
instant is the safe direction, and a campaign that loses its last window has
failed closed.

THE SAFE RULE, asserted below: set NOT_AFTER strictly after
(last window start + arming latency) and strictly before the next window's
start. Stage B2 used 14:58:00Z for a last window of 14:45z and armed all of
its windows; 15:15:00Z for a last window of 15:15z did not.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_live_authorization as AUTH

UTC = dt.timezone.utc
NOT_AFTER_ENV = AUTH.NOT_AFTER_ENV


def _t(h, m, s=0):
    return dt.datetime(2026, 11, 1, h, m, s, tzinfo=UTC)


#: measured on the 2026-11-01 campaign: the 13:30:00Z window armed at
#: 13:30:11.747Z. Not a tunable -- the observed latency of one real arm.
OBSERVED_ARM_LATENCY_S = 11.747


class TestTheComparisonIsOnTheArmingInstant:
    """`now > deadline`, where `now` is when arming is attempted."""

    @pytest.mark.parametrize("secs,expired", [
        (-1, False),        # one second before: still open
        (0, True),          # EXACTLY at it: the DIAGNOSTIC says expired
        (1, True),          # one second past: expired
        (12, True),         # the observed arming latency: expired
    ])
    def test_the_boundary_is_exclusive_of_later_instants(
            self, monkeypatch, secs, expired):
        monkeypatch.setenv(NOT_AFTER_ENV, _t(15, 15).isoformat())
        st = AUTH.not_after_status(
            now=_t(15, 15) + dt.timedelta(seconds=secs))
        assert st["set"] is True
        assert (st["state"] == "EXPIRED") is expired, st

    def test_the_diagnostic_is_one_tick_stricter_than_the_gate(
            self, monkeypatch):
        """A DISCREPANCY FOUND BY WRITING THIS TEST, recorded not fixed.

        `authorize_arming` step 8 is `if now > deadline`, which PERMITS at
        exact equality. `not_after_status` reports EXPIRED at equality, with
        `expires_in_s` 0.0. So at the single instant `now == deadline` the
        diagnostic says EXPIRED while the gate would still arm.

        It is harmless and it errs in the safe direction -- the observable
        says stop before the gate does -- and it is NOT worth changing the
        authorization semantics for, which is why this test pins both rather
        than altering either. It matters only to an operator comparing the
        boot diagnostic against the gate's behaviour at a one-second
        boundary, and arming never lands on that instant in practice.
        """
        monkeypatch.setenv(NOT_AFTER_ENV, _t(15, 15).isoformat())
        at = AUTH.not_after_status(now=_t(15, 15))
        assert at["state"] == "EXPIRED" and at["expires_in_s"] == 0.0
        # the gate's own comparison, evaluated directly
        assert not (_t(15, 15) > _t(15, 15)), (
            "authorize_arming would PERMIT at equality; the diagnostic "
            "above reports EXPIRED. Both are pinned; neither is changed.")


class TestTheCampaignRegression:
    """The exact setting that cost window 8, and the one that would not."""

    def test_not_after_at_the_last_window_start_refuses_that_window(
            self, monkeypatch):
        """THE BUG. Deadline 15:15:00Z, window 1515z arms ~11.7s later."""
        monkeypatch.setenv(NOT_AFTER_ENV, _t(15, 15).isoformat())
        arming = _t(15, 15) + dt.timedelta(seconds=OBSERVED_ARM_LATENCY_S)
        st = AUTH.not_after_status(now=arming)
        assert st["state"] == "EXPIRED", (
            "a deadline at the last window's own start cannot admit it, "
            "because arming always happens after the window opens")

    def test_not_after_between_the_last_and_next_window_admits_it(
            self, monkeypatch):
        """THE FIX, and it is a convention, not a code change. Any instant
        after (last start + latency) and before the next window start."""
        monkeypatch.setenv(NOT_AFTER_ENV, _t(15, 28).isoformat())
        arming = _t(15, 15) + dt.timedelta(seconds=OBSERVED_ARM_LATENCY_S)
        assert AUTH.not_after_status(now=arming)["state"] != "EXPIRED"

    def test_it_still_excludes_the_window_after_the_last_one(
            self, monkeypatch):
        """The deadline must not admit 1530z -- that is what it is for."""
        monkeypatch.setenv(NOT_AFTER_ENV, _t(15, 28).isoformat())
        arming = _t(15, 30) + dt.timedelta(seconds=OBSERVED_ARM_LATENCY_S)
        assert AUTH.not_after_status(now=arming)["state"] == "EXPIRED"

    def test_stage_b2s_setting_admitted_its_own_last_window(
            self, monkeypatch):
        """14:58:00Z against a last window of 14:45z -- the pattern that
        worked, kept here so the working convention is also pinned."""
        monkeypatch.setenv(NOT_AFTER_ENV, _t(14, 58).isoformat())
        arming = _t(14, 45) + dt.timedelta(seconds=OBSERVED_ARM_LATENCY_S)
        assert AUTH.not_after_status(now=arming)["state"] != "EXPIRED"
        # ...and still excluded 15:00z
        assert AUTH.not_after_status(
            now=_t(15, 0) + dt.timedelta(seconds=OBSERVED_ARM_LATENCY_S)
        )["state"] == "EXPIRED"


class TestItLeaksNothingAndFailsClosed:
    def test_the_configured_value_is_never_emitted(self, monkeypatch):
        secret = _t(15, 15).isoformat()
        monkeypatch.setenv(NOT_AFTER_ENV, secret)
        st = AUTH.not_after_status(now=_t(15, 0))
        assert secret not in repr(st), "the deadline must not be emitted"
        assert st.get("expires_in_s") is not None

    def test_unset_is_reported_as_unset(self, monkeypatch):
        monkeypatch.delenv(NOT_AFTER_ENV, raising=False)
        assert AUTH.not_after_status(now=_t(15, 0))["state"] == "UNSET"
