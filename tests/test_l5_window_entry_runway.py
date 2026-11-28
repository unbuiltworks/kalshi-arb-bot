"""A window is only worth an IRREVERSIBLE claim if it can still be entered.

THE WASTED WINDOW, 2026-10-23, reconstructed from production. The executor
armed `cpc-btc-updown-15m-2026-10-23-0000z` at 00:07:21Z -- T+441s -- because
a deployment happened to finish then. The claim was written (`event: CLAIMED`,
`max_windows: 1`), the book then collapsed 0.0500/0.0600 -> 0.0100/0.0100, and
the window closed at 00:14:06Z with:

    submissions 0, fills 0, realized P&L $0.00

and `CAMPAIGN_WINDOWS_EXHAUSTED windows_spent: 1` refusing every later window.
The single authorization was spent on 159 seconds of unusable runway.

WHY ARITHMETIC WAS NOT ENOUGH, which is the part worth pinning. `entry_cutoff_s`
is 600, so T+441 looked like 159s of runway. `may_enter` actually began
refusing `TOO_LATE_TO_WITHDRAW` at T+565s, because `t_end` is the VENUE's close
(`bounded_by: "VENUE_CLOSE", source: "VENUE"`) and can be earlier than
start + WINDOW_S implies. So `usable_entry_runway_s` ASKS `may_enter` where it
stops, rather than recomputing the bound from constants: the only trustworthy
deadline is the one the gate itself enforces.

ELIGIBILITY IS SEPARATED FROM THE CLAIM. `window_eligible` writes nothing and
touches no ledger, so it can be asked on every tick without spending anything.
Nothing here releases, reuses or refunds a claim that was legitimately taken --
a claimed window stays spent whether or not it filled.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import pytest

from src import l5_two_sided_probe as TS

START = 0.0
END = 900.0            # the slug-implied close; the venue's may be earlier


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for v in (TS.MIN_ENTRY_RUNWAY_ENV, TS.ENTRY_CUTOFF_ENV,
              TS.PLACE_FLOOR_ENV, "L5_TS_CANCEL_MARGIN_S"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


class TestTheProductionWastedWindow:

    def test_the_exact_arming_moment_is_now_refused(self):
        """T+441s, the moment production armed 0000z and wasted it."""
        out = TS.window_eligible(441.0, START, END)
        assert out["ok"] is False
        assert out["reason"] == "INSUFFICIENT_ENTRY_RUNWAY"
        assert out["runway_s"] == 159.0
        assert out["elapsed_s"] == 441.0

    def test_a_window_claimed_from_its_start_is_eligible(self):
        out = TS.window_eligible(0.0, START, END)
        assert out["ok"] is True
        assert out["runway_s"] == 600.0

    def test_an_unopened_window_is_not_a_refusal_but_a_wait(self):
        """The correct response is to wait, not to look elsewhere.

        Reporting this as a failure is what let a mid-window deployment
        claim an aging window instead of waiting ~90s for a clean one.
        """
        out = TS.window_eligible(-30.0, START, END)
        assert out["ok"] is False
        assert out["reason"] == "WINDOW_NOT_OPEN_YET"
        assert out["seconds_until_open"] == 30.0


class TestRunwayTracksTheGateNotTheConstants:

    def test_runway_is_zero_once_may_enter_refuses(self):
        for t in (600.0, 660.0, 700.0, 899.0):
            assert TS.usable_entry_runway_s(t, START, END) == 0.0, t

    def test_runway_decreases_one_for_one_with_elapsed_time(self):
        a = TS.usable_entry_runway_s(100.0, START, END)
        b = TS.usable_entry_runway_s(200.0, START, END)
        assert a - b == 100.0

    def test_an_earlier_VENUE_close_shortens_the_runway(self):
        """The defect's root cause: t_end is the venue's, not the slug's.

        A venue closing early must reduce runway even though WINDOW_S and
        entry_cutoff_s are unchanged. Recomputing from constants would miss
        this entirely, which is why the gate is consulted instead.
        """
        full = TS.usable_entry_runway_s(0.0, START, END)
        early = TS.usable_entry_runway_s(0.0, START, END - 300.0)
        assert early < full, (early, full)

    def test_a_tighter_entry_cutoff_shortens_the_runway(self, clean):
        clean.setenv(TS.ENTRY_CUTOFF_ENV, "200")
        assert TS.usable_entry_runway_s(0.0, START, END) == 200.0

    def test_the_place_floor_does_not_read_as_exhausted(self, clean):
        """BEFORE_PLACE_FLOOR resolves by waiting; it is not a closed window.

        Treating it as exhausted would make a window with a configured floor
        permanently ineligible, which is the opposite of the intent.
        """
        clean.setenv(TS.PLACE_FLOOR_ENV, "120")
        assert TS.usable_entry_runway_s(0.0, START, END) > 0.0
        assert TS.window_eligible(0.0, START, END)["ok"] is True


class TestTheThresholdCannotBeLoosened:

    def test_the_default_requirement_is_five_minutes(self):
        assert TS.min_entry_runway_s() == 300.0

    def test_a_larger_requirement_is_accepted(self, clean):
        clean.setenv(TS.MIN_ENTRY_RUNWAY_ENV, "450")
        assert TS.min_entry_runway_s() == 450.0
        assert TS.window_eligible(0.0, START, END)["ok"] is True      # 600
        assert TS.window_eligible(200.0, START, END)["ok"] is False   # 400

    def test_a_smaller_requirement_is_REFUSED(self, clean):
        """Raise-only. Lowering it is how a wasted window comes back."""
        for v in ("0", "1", "60", "299", "-500"):
            clean.setenv(TS.MIN_ENTRY_RUNWAY_ENV, v)
            assert TS.min_entry_runway_s() == 300.0, v

    def test_an_unreadable_value_is_not_permission(self, clean):
        for v in ("", "   ", "soon", "null", "NaN-ish"):
            clean.setenv(TS.MIN_ENTRY_RUNWAY_ENV, v)
            assert TS.min_entry_runway_s() == 300.0, v


class TestEligibilityIsSideEffectFree:

    def test_it_can_be_asked_repeatedly_with_a_stable_answer(self):
        """It must be safe on every tick: no ledger, no claim, no spend."""
        first = TS.window_eligible(100.0, START, END)
        for _ in range(50):
            again = TS.window_eligible(100.0, START, END)
            assert again == first

    def test_it_reports_runway_and_requirement_either_way(self):
        for t in (0.0, 441.0, 800.0):
            out = TS.window_eligible(t, START, END)
            assert "runway_s" in out and "required_s" in out
            assert out["required_s"] == 300.0
