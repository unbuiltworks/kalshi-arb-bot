"""The cash gate must receive the venue's own buying power, and a failed or
absent read must never read as unlimited."""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_pmus_live_runner as RUN
from src import l5_pmus_transport as T
from src import l5_live_validation_gates as VG

BAL = {"balances": [{"currentBalance": 243.15505, "currency": "USD",
                     "buyingPower": 243.15505, "unsettledFunds": 0,
                     "openOrders": 0, "marginRequirement": 0}]}


class FakeT:
    market_slug = "cpc-btc-updown-15m-2026-10-12-1800z"

    def __init__(self, bal=None, raises=None):
        self._b, self._raises = bal, raises
        self.calls = 0

    def account(self):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._b


class FakeExec:
    available_cash_usd = "SENTINEL"


class Runner:
    """Only the fields refresh_available_cash touches."""
    now_t = 1000.0

    def __init__(self, ex=None):
        self.available_cash_usd = None
        self.available_cash_t = None
        self.executor = ex

    refresh_available_cash = RUN.LiveRunner.refresh_available_cash \
        if hasattr(RUN, "LiveRunner") else None


def _runner(ex=None):
    r = Runner(ex)
    # bind the real method regardless of the runner class name
    import types
    for name in dir(RUN):
        obj = getattr(RUN, name)
        if isinstance(obj, type) and hasattr(obj, "refresh_available_cash"):
            r.refresh_available_cash = types.MethodType(
                obj.refresh_available_cash, r)
            # the two hooks the refresh calls since 2026-11-11. Both are
            # inert here: this executor has no cash policy, so nothing is
            # recycled and nothing is confirmed.
            for hook in ("recycle_wants_fresh_cash", "confirm_recycled_cash"):
                setattr(r, hook, types.MethodType(getattr(obj, hook), r))
            return r
    raise AssertionError("refresh_available_cash not found on any runner class")


# ------------------------------------------------- the venue's own number

def test_the_buying_power_reaches_the_executor():
    ex = FakeExec()
    r = _runner(ex)
    r.refresh_available_cash(FakeT(BAL), 1000.0)
    assert r.available_cash_usd == D("243.15505")
    assert ex.available_cash_usd == D("243.15505")


def test_the_extractor_reads_the_real_list_shaped_response():
    assert T.PmusTransport.buying_power(BAL) == D("243.15505")


def test_freshness_is_recorded():
    r = _runner(FakeExec())
    r.refresh_available_cash(FakeT(BAL), 1234.0)
    assert r.available_cash_t == 1234.0


# ---------------------------------- a failed read is None, never unlimited

def test_a_raising_read_installs_none_not_a_stale_number():
    ex = FakeExec()
    r = _runner(ex)
    r.refresh_available_cash(FakeT(BAL), 1000.0)
    assert ex.available_cash_usd == D("243.15505")
    r.refresh_available_cash(FakeT(raises=OSError("down")), 1100.0)
    assert r.available_cash_usd is None
    assert ex.available_cash_usd is None, "a stale number must not survive"
    assert r.available_cash_t is None


def test_an_empty_response_installs_none():
    r = _runner(FakeExec())
    r.refresh_available_cash(FakeT({}), 1000.0)
    assert r.available_cash_usd is None


def test_none_is_not_unlimited_in_the_gate():
    """The gate must still bind on its ceiling with no reading."""
    g = VG.ValidationGates()
    legs = [{"cash_out": D("2.45"), "settled": False} for _ in range(4)]
    r = g.refuse(order_qty=D(5), order_price=D("0.49"),
                 order_intent="ORDER_INTENT_BUY_LONG",
                 filled_legs=legs, available_cash_usd=None)
    assert r and r.startswith("CASH_COMMITMENT")


def test_none_still_refuses_to_arm():
    assert VG.ValidationGates().require_cash_evidence(None) == \
        "NO_AUTHORITATIVE_CASH_READING"


# ------------------------------- a number can only ADD a refusal

def test_a_known_balance_only_ever_adds_a_refusal():
    g = VG.ValidationGates()
    kw = dict(order_qty=D(5), order_price=D("0.49"),
              order_intent="ORDER_INTENT_BUY_LONG")
    assert g.refuse(**kw, available_cash_usd=None) is None
    assert g.refuse(**kw, available_cash_usd=D("243.15505")) is None
    tight = g.refuse(**kw, available_cash_usd=D("1.00"))
    assert tight and tight.startswith("INSUFFICIENT_AVAILABLE_CASH")


def test_a_large_balance_never_relaxes_the_configured_ceiling():
    g = VG.ValidationGates()
    legs = [{"cash_out": D("2.45"), "settled": False} for _ in range(4)]
    r = g.refuse(order_qty=D(5), order_price=D("0.49"),
                 order_intent="ORDER_INTENT_BUY_LONG",
                 filled_legs=legs, available_cash_usd=D("1000000"))
    assert r and r.startswith("CASH_COMMITMENT"), \
        "a fat balance must not buy headroom past the $10 ceiling"


# ----------------------------------------------------- read-only and loud

def test_the_read_is_a_plain_get_on_the_balances_route():
    src = (__import__("pathlib").Path(__file__).resolve().parent.parent
           / "src" / "l5_pmus_transport.py").read_text()
    body = src.split("    def account(self)")[1].split("\n    def ")[0]
    assert '_get("/v1/account/balances")' in body


def test_the_refresh_emits_its_outcome(capsys):
    r = _runner(FakeExec())
    r.refresh_available_cash(FakeT(BAL), 1000.0)
    out = capsys.readouterr().out
    assert "L5_LIVE_CASH" in out
    doc = json.loads(out.split("L5_LIVE_CASH ", 1)[1].splitlines()[0])
    assert doc["read_ok"] is True
    assert doc["available_cash_usd"] == "243.15505"


def test_a_failed_refresh_reports_the_error_not_silence(capsys):
    r = _runner(FakeExec())
    r.refresh_available_cash(FakeT(raises=OSError("boom")), 1000.0)
    doc = json.loads(capsys.readouterr().out.split(
        "L5_LIVE_CASH ", 1)[1].splitlines()[0])
    assert doc["read_ok"] is False and doc["error"]
    assert doc["available_cash_usd"] is None


# ------------------------------------------------- freshness is a cadence,
# not a one-shot. reconcile_if_needed short-circuits on a confirmed book, so
# without a time cadence a reading taken at startup is published for the
# whole run as though it were current.

def test_a_fresh_reading_is_not_re_read():
    t = FakeT(BAL)
    r = _runner(FakeExec())
    r.refresh_available_cash(t, 1000.0)
    r.refresh_available_cash(t, 1000.0 + RUN.CASH_REFRESH_S - 1)
    assert t.calls == 1, "spent a venue request on a reading still in hand"


def test_a_stale_reading_is_re_read():
    t = FakeT(BAL)
    r = _runner(FakeExec())
    r.refresh_available_cash(t, 1000.0)
    r.refresh_available_cash(t, 1000.0 + RUN.CASH_REFRESH_S)
    assert t.calls == 2
    assert r.available_cash_t == 1000.0 + RUN.CASH_REFRESH_S


def test_a_failed_read_is_retried_at_once_not_after_the_interval():
    """available_cash_t stays None on failure, so the next tick tries again
    rather than waiting out an interval it never earned."""
    t = FakeT(raises=RuntimeError("502"))
    r = _runner(FakeExec())
    r.refresh_available_cash(t, 1000.0)
    r.refresh_available_cash(t, 1000.5)
    assert t.calls == 2
    assert r.available_cash_usd is None
    assert r.available_cash_t is None


def test_a_failure_after_a_success_does_not_leave_the_old_number_standing():
    """The dangerous case: a number that is no longer supported by any read
    must be withdrawn, never kept because it is the last thing we saw."""
    ex = FakeExec()
    r = _runner(ex)
    r.refresh_available_cash(FakeT(BAL), 1000.0)
    assert r.available_cash_usd == D("243.15505")
    r.refresh_available_cash(FakeT(raises=RuntimeError("502")),
                             1000.0 + RUN.CASH_REFRESH_S)
    assert r.available_cash_usd is None
    assert ex.available_cash_usd is None
    assert r.available_cash_t is None


def test_the_refresh_interval_is_cheap_against_the_venue_budget():
    """One GET per interval must stay far under the transport's rps budget,
    or a read-only convenience becomes a rate-limit incident."""
    assert RUN.CASH_REFRESH_S >= 30.0
    assert (1.0 / RUN.CASH_REFRESH_S) < 0.1


# ------------------------------------------------- where it is NOT called
# The first version of this change called the cash read from inside
# reconcile_if_needed. That coupled book reconciliation to a balance read --
# a read that errors would have taken the book's confirmation down with it --
# and it broke 31 tests across three suites that build a Runner with
# __new__ and so do not have the new attributes. Both are the same defect:
# the reconcile path must not depend on this read.

def test_reconcile_does_not_perform_the_cash_read():
    import inspect
    src = inspect.getsource(RUN.Runner.reconcile_if_needed)
    assert "refresh_available_cash" not in src, (
        "the cash read is back inside reconcile_if_needed; a failing "
        "balance read must never break book reconciliation")


def test_the_cash_read_runs_on_the_main_loop_under_its_own_error_guard():
    import inspect
    src = inspect.getsource(RUN.main)
    assert "refresh_available_cash" in src, "no cadence drives the read"
    i = src.index("refresh_available_cash")
    window = src[max(0, i - 400):i + 400]
    assert "try:" in window and "except" in window, (
        "the read is unguarded on the tick loop; a venue error would kill "
        "the loop that reconciles the book")
    assert "cash_refresh_errors" in window, "failures are not counted"


def test_the_read_is_a_plain_get_with_no_order_path():
    import inspect
    src = inspect.getsource(RUN.Runner.refresh_available_cash)
    for forbidden in ("submit", "arm(", "cancel", "place"):
        assert forbidden not in src, (
            "refresh_available_cash touches %r; it is read-only" % forbidden)
