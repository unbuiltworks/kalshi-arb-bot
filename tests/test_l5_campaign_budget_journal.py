"""The ledger holds ONE document, so closing a campaign erased the campaign it
closed. These tests pin the append-only journal that fixes it.

WHAT WAS LOST, and why this is not hypothetical. On 2026-10-24T00:31:27Z
production handed the budget from camp1.l5-btc15m-8win-20261023 -- final
peak_usd 10.35520000 of 29.30, eight windows exhausted, +$0.1336 settled -- to
...20261024a. `open_or_resume` calls `_write`, which is an atomic replace, so
the outgoing document was destroyed in place. Its only surviving trace was a
heartbeat line in a log stream with a retention window.

THE JOURNAL IS SUBORDINATE TO THE LEDGER. It is written first, and any failure
writing it is swallowed: a ledger that refused to record its own budget because
an audit file was unwritable would be strictly worse than a missing history
line. These tests assert that subordination explicitly, because it is the part
a later change is most likely to get backwards.

Nothing here touches a venue, a credential or a limit.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src import l5_campaign_budget as CB

A, B = "camp1.test-a", "camp1.test-b"


@pytest.fixture()
def led(tmp_path):
    return CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="29.30")


def _doc(cid, closed=False, peak="0"):
    return {"campaign_id": cid, "closed": closed, "opened_utc": "t",
            "peak_usd": peak}


class TestOnlyLifecycleTransitionsAreJournalled:

    def test_a_peak_bump_is_not_a_lifecycle_event(self, led):
        """peak_usd rises many times per campaign. Recording those would bury
        the two lines that matter."""
        led.open_or_resume(A, now_iso="t0")
        for p in ("1", "5", "9", "10.35520000"):
            led._write(_doc(A, peak=p))
        assert led.history() == []

    def test_closing_journals_the_outgoing_document(self, led):
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="10.35520000"))
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        h = led.history()
        assert len(h) == 1
        assert h[0]["campaign_id"] == A
        assert h[0]["peak_usd"] == "10.35520000"     # the figure that was lost
        assert h[0]["transition"] == "CLOSED"

    def test_replacement_journals_the_closed_document_too(self, led):
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="10.35520000"))
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        led.open_or_resume(B, now_iso="t1")
        h = led.history()
        assert len(h) == 2
        assert h[1]["transition"] == "REPLACED"
        assert h[1]["superseded_by"] == B
        assert h[1]["peak_usd"] == "10.35520000"

    def test_the_empty_placeholder_is_not_news(self, led):
        """A first-ever open has nothing to supersede."""
        led.open_or_resume(A, now_iso="t0")
        assert led.history() == []

    def test_history_is_append_only_and_ordered_oldest_first(self, led):
        led.open_or_resume(A, now_iso="t0")
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        led.open_or_resume(B, now_iso="t1")
        led.release_stale("camp1.test-c", legs=(), outstanding=(),
                          now_iso="t2")
        h = led.history()
        assert [r["campaign_id"] for r in h] == [A, A, B]
        assert len(h) == 3


class TestTheJournalCanNeverBreakTheLedger:

    def test_an_unwritable_history_still_lets_the_budget_be_recorded(
            self, led, monkeypatch):
        """The ledger is the authority; the journal is evidence beside it."""
        def boom(*_a, **_k):
            raise OSError("read-only file system")
        monkeypatch.setattr(CB, "open", boom, raising=False)
        monkeypatch.setattr(led, "_journal",
                            lambda *_a, **_k: boom())
        with pytest.raises(OSError):
            led._journal({}, {})
        # and with the real journal, a failing write is swallowed
        monkeypatch.undo()
        orig = CB.json.dumps

        def bad_dumps(obj, **kw):
            if "transition" in (obj or {}):
                raise ValueError("cannot serialise")
            return orig(obj, **kw)
        monkeypatch.setattr(CB.json, "dumps", bad_dumps)
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="7"))
        out = led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        assert out["ok"] is True and out["released"] is True
        assert led.read()["closed"] is True

    def test_history_never_raises_on_a_corrupt_line(self, led):
        led.open_or_resume(A, now_iso="t0")
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        with open(led._history_path(), "a") as fh:
            fh.write("{not json at all\n\n")
        h = led.history()
        assert len(h) == 1 and h[0]["campaign_id"] == A

    def test_history_on_a_fresh_ledger_is_empty_not_an_error(self, led):
        assert led.history() == []

    def test_the_journal_is_written_before_the_document_is_replaced(self):
        """Ordering matters: a crash between the two must lose the NEW
        document, not the old one's only record."""
        import inspect
        src = inspect.getsource(CB.CampaignBudgetLedger._write)
        assert src.index("_journal") < src.index("os.replace")


class TestFailClosedBehaviourIsUnchanged:
    """The journal must not have relaxed any refusal."""

    def test_a_foreign_open_campaign_still_locks_the_budget(self, led):
        led.open_or_resume(A, now_iso="t0")
        out = led.open_or_resume(B, now_iso="t1")
        assert out["ok"] is False
        assert out["reason"].startswith("CAMPAIGN_BUDGET_LOCKED")

    def test_a_campaign_still_cannot_release_itself(self, led):
        led.open_or_resume(A, now_iso="t0")
        out = led.release_stale(A, legs=(), outstanding=(), now_iso="t1")
        assert out["ok"] is False
        assert out["reason"] == "CANNOT_RELEASE_OWN_OPEN_CAMPAIGN"

    def test_outstanding_orders_still_refuse_a_release(self, led):
        led.open_or_resume(A, now_iso="t0")
        out = led.release_stale(B, outstanding=[{"qty": "1", "price": "0.5"}],
                                now_iso="t1")
        assert out["ok"] is False
        assert out["reason"] == "CAMPAIGN_OUTSTANDING_ORDERS"

    def test_unsettled_exposure_still_refuses_a_release(self, led):
        led.open_or_resume(A, now_iso="t0")
        out = led.release_stale(B, legs=[{"market": "m", "settled": False}],
                                now_iso="t1")
        assert out["ok"] is False
        assert out["reason"].startswith("CAMPAIGN_UNSETTLED_EXPOSURE")

    def test_an_unreadable_ledger_still_refuses_arming(self, led,
                                                       monkeypatch):
        monkeypatch.setattr(led, "read", lambda: (_ for _ in ()).throw(
            CB.CampaignBudgetUnavailable("boom")))
        assert led.refuse_arming(campaign_id=A) == "CAMPAIGN_BUDGET_UNREADABLE"

    def test_an_exhausted_budget_still_refuses_arming(self, led):
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="29.30"))
        why = led.refuse_arming(campaign_id=A)
        assert why and why.startswith("CAMPAIGN_WORST_CASE_EXHAUSTED")


class TestTheNewLedgerStartsAtZeroCampaignUsage:
    """The audit question: a fresh campaign's own usage must start at zero,
    while account-wide losses stay cumulative OUTSIDE this file."""

    def test_a_fresh_claim_writes_peak_zero(self, led):
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="10.35520000"))
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        out = led.open_or_resume(B, now_iso="t1")
        assert out["peak_usd"] == "0"
        assert led.read()["peak_usd"] == "0"

    def test_the_ledger_stores_no_realized_loss_figure_at_all(self, led):
        """Which is why a release cannot erase one. Realized losses live in
        the executor's store and re-enter the gate on every evaluation."""
        led.open_or_resume(A, now_iso="t0")
        doc = led.read()
        assert set(doc) <= {"campaign_id", "closed", "opened_utc", "peak_usd",
                            "closed_utc", "released_for"}
        assert not any("realized" in k for k in doc)


class TestTheLifecycleRecordCarriesTheEmittersClock:
    """Item 2: without this, submit-to-terminal latency is unmeasurable from
    the log stream, because Railway's ingestion lag is bimodal (0.03-9.64 s
    across the eleven live orders of the 20261024a campaign) and applies
    independently at each end of an interval."""

    def test_the_emit_carries_t_utc_and_names_the_clock(self):
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.lifecycle)
        assert 't=self.now_t' in src
        assert 'utc=dt.datetime.now(dt.timezone.utc).isoformat()' in src
        assert 'clock="emitter"' in src

    def test_it_is_added_without_disturbing_market_identity(self):
        """The no-fallback market identity rule predates this and must stay."""
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.lifecycle)
        assert 'market=(m if m else None)' in src
        assert '"PRESENT" if m else "MISSING"' in src

    def test_nothing_in_the_execution_path_reads_the_new_field(self):
        """Telemetry only. If a gate ever keys off `clock`, that is a
        different change and needs its own review."""
        import inspect
        from src import l5_live_execution as LX
        from src import l5_live_validation_gates as VG
        for mod in (LX, VG):
            assert 'clock="emitter"' not in inspect.getsource(mod)


class TestAJournalFailureIsObservableNotSilent:
    """The owner's condition: journal write failures must be observable rather
    than silently disappearing, WITHOUT making the trading ledger unavailable.
    Those two requirements pull in opposite directions, so both halves are
    asserted on the same failure."""

    def _break_journal(self, monkeypatch):
        orig = CB.json.dumps

        def bad(obj, **kw):
            if isinstance(obj, dict) and "transition" in obj:
                raise OSError("read-only file system")
            return orig(obj, **kw)
        monkeypatch.setattr(CB.json, "dumps", bad)

    def test_a_fresh_ledger_reports_no_journal_errors(self, led):
        t = led.telemetry(campaign_id=A)
        assert t["journal_errors"] == 0
        assert t["journal_last_error"] is None

    def test_the_failure_is_counted_and_named(self, led, monkeypatch):
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="7"))
        self._break_journal(monkeypatch)
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        t = led.telemetry(campaign_id=B)
        assert t["journal_errors"] == 1
        assert "OSError" in t["journal_last_error"]
        assert "read-only" in t["journal_last_error"]

    def test_the_ledger_stays_available_through_that_failure(self, led,
                                                             monkeypatch):
        led.open_or_resume(A, now_iso="t0")
        led._write(_doc(A, peak="7"))
        self._break_journal(monkeypatch)
        out = led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        assert out["ok"] is True and out["released"] is True
        assert led.read()["closed"] is True            # the write happened
        monkeypatch.undo()
        assert led.open_or_resume(B, now_iso="t1")["ok"] is True

    def test_errors_accumulate_rather_than_overwrite_the_count(self, led,
                                                               monkeypatch):
        led.open_or_resume(A, now_iso="t0")
        self._break_journal(monkeypatch)
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        led._write(_doc(B, closed=False, peak="1"))
        assert led.telemetry(campaign_id=B)["journal_errors"] >= 2

    def test_entry_count_is_reported_so_a_working_journal_is_visible_too(
            self, led):
        led.open_or_resume(A, now_iso="t0")
        assert led.telemetry(campaign_id=A)["journal_entries"] == 0
        led.release_stale(B, legs=(), outstanding=(), now_iso="t1")
        assert led.telemetry(campaign_id=B)["journal_entries"] == 1

    def test_telemetry_never_raises_even_with_a_broken_history_file(
            self, led, monkeypatch):
        led.open_or_resume(A, now_iso="t0")
        monkeypatch.setattr(led, "history",
                            lambda: (_ for _ in ()).throw(OSError("gone")))
        with pytest.raises(OSError):
            led.history()
        # telemetry calls history(); it must not propagate
        try:
            t = led.telemetry(campaign_id=A)
        except OSError:
            pytest.fail("telemetry must never raise")
        assert t["readable"] is True


class TestIncrementalRoomIsReportingOnly:

    def test_it_is_budget_minus_account_wide_realized_loss(self, led):
        led.open_or_resume(A, now_iso="t0")
        t = led.telemetry(campaign_id=A, realized_usd="-7.9019")
        assert t["incremental_room_usd"] == "21.3981"

    def test_a_profit_does_not_mint_extra_room(self, led):
        led.open_or_resume(A, now_iso="t0")
        t = led.telemetry(campaign_id=A, realized_usd="+5")
        assert t["incremental_room_usd"] == "29.30"

    def test_omitting_it_omits_the_field_rather_than_guessing(self, led):
        led.open_or_resume(A, now_iso="t0")
        assert led.telemetry(campaign_id=A)["incremental_room_usd"] is None

    def test_no_gate_reads_it(self):
        import inspect
        from src import l5_campaign_budget as M
        for fn in (M.CampaignBudgetLedger.refuse_arming,
                   M.CampaignBudgetLedger.refuse_amount):
            assert "incremental_room" not in inspect.getsource(fn)
