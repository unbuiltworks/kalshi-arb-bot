"""What the registry publishes for a second strategy, and where the
transfers read is, must be readable on the heartbeat. Reporting only."""
import datetime as dt

from src import l5_equity_baseline as EB
from src import l5_equity_feed as EF
from src import l5_pmus_live_runner as R
from tests.test_l5_equity_feed import SINCE, old_page, run, verdict
from tests.test_l5_venue_cost_attestation import Feed


def runner(basis, results, day="2026-11-20"):
    class Rn:
        _publication_summary = R.Runner._publication_summary

        def _lifetime_basis_line(self):
            if isinstance(basis, BaseException):
                raise basis
            return basis

        def _retired_results_line(self, d):
            return None if results is None else {"utc_day": d,
                                                 "results": results}

        def day_realized_line(self):
            return {"utc_day": day}
    return Rn()


BASIS = {"cash_read_utc": "2026-11-16T00:11:52+00:00",
         "rebased_at_utc": "2026-11-16T00:20:00+00:00",
         "results_since_rebase": {"m1": "-0.0114", "m2": "-0.0151"},
         "rows": 2, "complete": True, "reconciled": True,
         "lifetime_realized_usd": "-39.4945", "labels": "x"}


def test_it_reports_the_verdicts_and_never_the_rows():
    got = runner(BASIS, {"m1": "-0.0114"})._publication_summary()
    assert got["lifetime_basis"] == {
        "reconciled": True, "complete": True, "rows": 2,
        "rebased_at_utc": "2026-11-16T00:20:00+00:00",
        "cash_read_utc": "2026-11-16T00:11:52+00:00",
        "lifetime_realized_usd": "-39.4945"}
    assert got["retired_results_utc_day"] == {"utc_day": "2026-11-20",
                                              "markets": 1}
    assert "results_since_rebase" not in str(got)


def test_an_unreconciled_basis_is_shown_as_unreconciled():
    got = runner(dict(BASIS, reconciled=False), {})._publication_summary()
    assert got["lifetime_basis"]["reconciled"] is False


def test_an_unknown_basis_is_none_not_a_pass():
    got = runner(None, None)._publication_summary()
    assert got == {"lifetime_basis": None, "retired_results_utc_day": None}


def test_it_never_raises():
    got = runner(RuntimeError("x"), {})._publication_summary()
    assert got["lifetime_basis"] is None and got["error"] == "RuntimeError"


def test_the_real_runner_carries_it_on_the_heartbeat_source():
    src = open(R.__file__).read()
    assert '"account_risk_publication": self._publication_summary()' in src


# ------------------------------------------------- the transfers read

def test_progress_before_during_and_after_a_read():
    w = EF.TransferWatch()
    assert w.progress(0.0) == {"walking": False, "pages_this_walk": None,
                               "reads_completed": 0, "backing_off_s": 0.0,
                               "last_error": None}
    feed = Feed([old_page(h) for h in range(1, 9)])
    w, now = run(feed, steps=1, w=w)                # two pages a step
    p = w.progress(now)
    assert p["walking"] is True and p["pages_this_walk"] == 2
    assert verdict(w, now)["net_usd"] is None        # still refuses
    w, now = run(feed, steps=20, w=w)
    p = w.progress(now)
    assert p["walking"] is False and p["reads_completed"] == 1


def test_progress_does_not_change_the_verdict():
    feed = Feed([old_page(h) for h in range(1, 5)])
    a, now = run(feed, steps=20)
    before = verdict(a, now)
    a.progress(now)
    assert verdict(a, now) == before
