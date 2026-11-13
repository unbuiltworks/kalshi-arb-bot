"""The OPEN budget and the approved window count are set in different
places, and until tonight nothing compared them.

Observed live 2026-11-12: an approval enumerated 8 windows; the 0.05-0.10
ladder spent 8 OPEN submissions a window; `L5_LIVE_AUTH_MAX_OPEN` was 48,
sized long ago for a one-entry pair strategy that spent two. 48 // 8 = 6.
Windows 1-6 traded, window 7 was refused for an exhausted budget, and
every report said 8 approved windows right up to the end.

`budget_reach` states the shortfall before the first window. It must
WIDEN NOTHING -- the budget stays the binding control -- and it must not
confuse "no budget configured" with "budget too small".
"""
from __future__ import annotations

import pytest

from src import l5_btc5m_runner as R
from src import l5_live_authorization as AUTH


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv(AUTH.CLASS_BUDGET_ENV["OPEN"], raising=False)


def set_open(monkeypatch, v):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], str(v))


class TestTheLiveCase:

    def test_tonights_exact_numbers(self, monkeypatch):
        set_open(monkeypatch, 48)
        got = R.budget_reach(8, 8)
        assert got["ok"] is False
        assert got["windows_payable"] == 6
        assert got["short_by"] == 2
        assert got["last_payable_window"] == 6
        assert got["why"] == "OPEN_BUDGET_COVERS_6_OF_8_WINDOWS"

    def test_the_single_rung_straddle_was_never_short(self, monkeypatch):
        #: 2 orders a window is what the budget was sized for
        set_open(monkeypatch, 48)
        assert R.budget_reach(8, 2)["ok"] is True

    def test_a_full_12_order_ladder_is_shorter_still(self, monkeypatch):
        set_open(monkeypatch, 48)
        got = R.budget_reach(8, 12)
        assert got["windows_payable"] == 4
        assert got["short_by"] == 4


class TestItReportsRatherThanRefuses:

    def test_exactly_enough_is_ok(self, monkeypatch):
        set_open(monkeypatch, 64)
        got = R.budget_reach(8, 8)
        assert got["ok"] is True and got["short_by"] is None

    def test_one_short_is_not_ok(self, monkeypatch):
        set_open(monkeypatch, 63)
        assert R.budget_reach(8, 8)["ok"] is False

    def test_plenty_is_ok(self, monkeypatch):
        set_open(monkeypatch, 500)
        got = R.budget_reach(8, 8)
        assert got["ok"] is True
        assert got["windows_payable"] == 62

    def test_it_echoes_what_it_compared(self, monkeypatch):
        set_open(monkeypatch, 48)
        got = R.budget_reach(8, 8)
        assert got["open_budget"] == 48
        assert got["orders_per_window"] == 8
        assert got["windows_approved"] == 8


class TestEdges:

    def test_unconfigured_is_not_a_shortfall(self):
        got = R.budget_reach(8, 8)
        #: the class simply does not bind; that is not the same as too small
        assert got["ok"] is None
        assert got["why"] == "OPEN_BUDGET_NOT_CONFIGURED"
        assert got["short_by"] is None

    def test_a_zero_budget_pays_for_nothing(self, monkeypatch):
        set_open(monkeypatch, 0)
        got = R.budget_reach(8, 8)
        assert got["windows_payable"] == 0
        assert got["ok"] is False

    def test_zero_orders_per_window_does_not_divide_by_zero(self, monkeypatch):
        set_open(monkeypatch, 48)
        got = R.budget_reach(8, 0)
        assert got["windows_payable"] == 48
        assert got["ok"] is True

    def test_an_unreadable_budget_says_so(self, monkeypatch):
        def boom(_k):
            raise OSError("nope")
        monkeypatch.setattr(AUTH, "max_of_class", boom)
        got = R.budget_reach(8, 8)
        assert got["why"] == "UNREADABLE:OSError"
        assert got["ok"] is None

    def test_it_never_raises(self, monkeypatch):
        def boom(_k):
            raise RuntimeError("nope")
        monkeypatch.setattr(AUTH, "max_of_class", boom)
        #: a diagnostic must not be able to stop the service from trading
        assert R.budget_reach(8, 8)["ok"] is None


class TestItGrantsNothing:

    def test_it_does_not_touch_the_budget(self, monkeypatch):
        set_open(monkeypatch, 48)
        before = AUTH.max_of_class("OPEN")
        R.budget_reach(8, 8)
        assert AUTH.max_of_class("OPEN") == before == 48

    def test_a_short_report_is_still_only_a_report(self, monkeypatch):
        set_open(monkeypatch, 48)
        got = R.budget_reach(8, 8)
        #: nothing in the result is a permission or a raised ceiling
        assert set(got) == {
            "open_budget", "orders_per_window", "windows_approved",
            "windows_payable", "short_by", "last_payable_window",
            "ok", "why"}
