"""Per-UTC-day allowances: rollover, restart, concurrency, non-reserving."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from src import l5_daily_action_ledger as L

D0 = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
MID_BEFORE = dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=dt.timezone.utc)
MID_AFTER = dt.datetime(2026, 10, 24, 0, 0, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def led(tmp_path):
    return L.DailyActionLedger(root=str(tmp_path))


class TestUtcDayKey:

    def test_the_key_is_the_utc_date(self):
        assert L.utc_day(D0) == "2026-10-23"

    def test_a_naive_datetime_is_refused_not_assumed_utc(self):
        with pytest.raises(L.DailyLedgerUnavailable):
            L.utc_day(dt.datetime(2026, 10, 23, 12, 0))

    def test_a_non_utc_zone_is_converted_not_truncated(self):
        tz = dt.timezone(dt.timedelta(hours=-5))
        # 2026-10-23 21:00 -05:00 is 2026-10-24 02:00 UTC
        assert L.utc_day(dt.datetime(2026, 10, 23, 21, 0, tzinfo=tz)) \
            == "2026-10-24"


class TestWindowAllowance:

    def test_a_window_is_granted_and_counted(self, led):
        assert led.reserve_window("m", "w1", max_windows_per_day=2,
                                  now=D0)["ok"]
        assert led.used("m", now=D0)["windows_used"] == 1

    def test_the_cap_binds(self, led):
        for i in range(2):
            led.reserve_window("m", "w%d" % i, max_windows_per_day=2, now=D0)
        r = led.reserve_window("m", "w9", max_windows_per_day=2, now=D0)
        assert r["reason"] == "DAILY_WINDOW_BUDGET_SPENT"
        assert r["windows_used"] == 2

    def test_re_reserving_the_same_slug_resumes_rather_than_respends(self, led):
        """A restart that re-arms the same window must not cost a second one
        -- the 2026-10-20 1600z loss."""
        led.reserve_window("m", "w1", max_windows_per_day=1, now=D0)
        r = led.reserve_window("m", "w1", max_windows_per_day=1, now=D0)
        assert r["ok"] and r["resumed"] is True
        assert led.used("m", now=D0)["windows_used"] == 1

    def test_a_cap_below_one_refuses(self, led):
        assert led.reserve_window("m", "w", max_windows_per_day=0,
                                  now=D0)["reason"] \
            == "DAILY_WINDOW_CAP_BELOW_ONE"

    def test_an_unreadable_cap_refuses(self, led):
        assert led.reserve_window("m", "w", max_windows_per_day="four",
                                  now=D0)["reason"] \
            == "DAILY_WINDOW_CAP_UNREADABLE"

    def test_mandates_do_not_share_an_allowance(self, led):
        led.reserve_window("m1", "w", max_windows_per_day=1, now=D0)
        assert led.reserve_window("m2", "w", max_windows_per_day=1,
                                  now=D0)["ok"]


class TestNonReservingVerification:

    def test_would_reserve_writes_nothing(self, led):
        assert led.would_reserve_window("m", "w", max_windows_per_day=1,
                                        now=D0)["ok"]
        assert led.used("m", now=D0)["windows_used"] == 0

    def test_would_reserve_still_reports_the_cap_being_spent(self, led):
        led.reserve_window("m", "a", max_windows_per_day=1, now=D0)
        r = led.would_reserve_window("m", "b", max_windows_per_day=1, now=D0)
        assert r["reason"] == "DAILY_WINDOW_BUDGET_SPENT"

    def test_repeated_verification_never_accumulates(self, led):
        for _ in range(10):
            led.would_reserve_window("m", "w", max_windows_per_day=1, now=D0)
        assert led.used("m", now=D0)["windows_used"] == 0


class TestMidnightRollover:

    def test_the_allowance_rolls_at_utc_midnight(self, led):
        led.reserve_window("m", "w1", max_windows_per_day=1,
                           now=MID_BEFORE)
        assert led.reserve_window("m", "w1", max_windows_per_day=1,
                                  now=MID_BEFORE)["resumed"] is True
        # one second later it is a new day and a fresh allowance
        r = led.reserve_window("m", "w2", max_windows_per_day=1,
                               now=MID_AFTER)
        assert r["ok"] and r["resumed"] is False
        assert r["utc_day"] == "2026-10-24"

    def test_yesterdays_counts_are_not_destroyed_by_the_rollover(self, led):
        led.reserve_window("m", "w1", max_windows_per_day=1, now=MID_BEFORE)
        led.reserve_window("m", "w2", max_windows_per_day=1, now=MID_AFTER)
        assert led.used("m", now=MID_BEFORE)["windows_used"] == 1
        assert led.used("m", now=MID_AFTER)["windows_used"] == 1

    def test_the_same_slug_on_a_new_day_costs_a_new_window(self, led):
        """Not a resume: a different UTC day is a different allowance."""
        led.reserve_window("m", "same", max_windows_per_day=1, now=MID_BEFORE)
        r = led.reserve_window("m", "same", max_windows_per_day=1,
                               now=MID_AFTER)
        assert r["resumed"] is False


class TestRestartPersistence:

    def test_counts_survive_a_new_ledger_over_the_same_root(self, tmp_path):
        a = L.DailyActionLedger(root=str(tmp_path))
        a.reserve_window("m", "w1", max_windows_per_day=2, now=D0)
        b = L.DailyActionLedger(root=str(tmp_path))
        assert b.used("m", now=D0)["windows_used"] == 1
        assert b.reserve_window("m", "w1", max_windows_per_day=2,
                                now=D0)["resumed"] is True


class TestConcurrency:

    def test_the_second_claimer_of_a_name_loses(self, led):
        """O_EXCL decides the race: two workers cannot both win one claim."""
        r1 = led.claim_submission("m", "w", 1, max_submissions_per_day=5,
                                  now=D0)
        assert r1["ok"]
        r2 = led.claim_submission("m", "w", 1, max_submissions_per_day=5,
                                  now=D0)
        assert r2["ok"] is False
        assert r2["reason"] == "SUBMISSION_ORDINAL_ALREADY_CLAIMED"

    def test_distinct_ordinals_are_distinct_claims(self, led):
        for i in range(3):
            assert led.claim_submission("m", "w", i,
                                        max_submissions_per_day=3,
                                        now=D0)["ok"]
        assert led.used("m", now=D0)["submissions_used"] == 3

    def test_the_submission_cap_binds(self, led):
        led.claim_submission("m", "w", 0, max_submissions_per_day=1, now=D0)
        assert led.claim_submission("m", "w", 1, max_submissions_per_day=1,
                                    now=D0)["reason"] \
            == "DAILY_SUBMISSION_BUDGET_SPENT"

    def test_a_claim_whose_body_failed_is_still_spent(self, led, monkeypatch):
        """Removing it on a write failure would hand the same claim twice."""
        import src.l5_daily_action_ledger as mod

        real = mod.os.fdopen

        def boom(fd, mode):
            os.close(fd)
            raise OSError("disk full")
        monkeypatch.setattr(mod.os, "fdopen", boom)
        r = led.reserve_window("m", "w", max_windows_per_day=2, now=D0)
        assert r["ok"] is False
        assert r["reason"] == "DAILY_CLAIM_BODY_FAILED"
        monkeypatch.setattr(mod.os, "fdopen", real)
        # the claim file exists, so the allowance was consumed
        assert led.used("m", now=D0)["windows_used"] == 1


class TestFailClosed:

    def test_an_unreadable_day_directory_raises(self, led, monkeypatch):
        def boom(_):
            raise PermissionError("nope")
        monkeypatch.setattr(os, "listdir", boom)
        with pytest.raises(L.DailyLedgerUnavailable):
            led.used("m", now=D0)

    def test_an_unreadable_ledger_refuses_rather_than_granting(
            self, led, monkeypatch):
        def boom(_):
            raise PermissionError("nope")
        monkeypatch.setattr(os, "listdir", boom)
        r = led.reserve_window("m", "w", max_windows_per_day=4, now=D0)
        assert r["ok"] is False
        assert r["reason"] == "DAILY_LEDGER_UNAVAILABLE"

    def test_a_day_with_no_claims_is_zero_not_an_error(self, led):
        assert led.used("m", now=D0)["windows_used"] == 0


class TestWhatMustNotReset:
    """Requirement: daily ACTION counters may reset; losses, inventory and
    mandate-wide capital limits must not."""

    def test_the_only_write_path_is_the_o_excl_claim(self):
        """Structural, not a word search: the docstring legitimately names
        the things that must not reset, so grepping for them is useless."""
        import ast, inspect
        src = inspect.getsource(L)
        tree = ast.parse(src)
        opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "open"]
        # exactly one builtin open(), inside _claim's fdopen wrapper path
        assert src.count("os.O_CREAT | os.O_EXCL") == 1
        assert src.count("os.fdopen") == 1
        assert len(opens) == 0, "no direct open(); claims go through O_EXCL"
        writers = [n.name for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name in ("_claim",)]
        assert writers == ["_claim"]

    def test_the_public_surface_cannot_decrement_or_clear_anything(self):
        pub = sorted(n for n in vars(L.DailyActionLedger)
                     if not n.startswith("_"))
        assert pub == ["claim_submission", "day_dir", "reserve_window",
                       "used", "would_reserve_window"]
        for n in pub:
            for bad in ("reset", "clear", "delete", "release", "refund"):
                assert bad not in n

    def test_it_imports_no_store_that_owns_money(self):
        import ast, inspect
        imported = set()
        for n in ast.walk(ast.parse(inspect.getsource(L))):
            if isinstance(n, ast.Import):
                imported.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                imported.add(n.module or "")
        assert not any(m.startswith("src.") or m.startswith("l5_")
                       for m in imported), imported

    def test_only_two_allowances_exist_and_both_are_rates(self, led):
        u = led.used("m", now=D0)
        assert set(u) == {"utc_day", "mandate_id", "windows_used",
                          "submissions_used", "windows"}
