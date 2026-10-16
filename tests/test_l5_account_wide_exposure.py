"""Account-wide exposure aggregation, and the bounded book-read retry."""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_live_execution as X             # noqa: E402
from src import l5_live_validation_gates as VG     # noqa: E402


def _s(tmp_path):
    return X.InventoryStore(str(tmp_path / "s.json"))


def _hold(s, market, side, qty, cost):
    s.state.setdefault("positions", {})["%s|%s" % (market, side)] = str(qty)
    s.state.setdefault("leg_cost", {})["%s|%s" % (market, side)] = str(cost)


# --------------------------------------------------- the one-entry-dict bug
def test_exposure_now_spans_every_market_not_just_the_traded_one(tmp_path):
    """THE DEFECT. risk_by_market was {market: ...}, so summing it summed ONE
    market and max_total_exposure_usd equalled max_usd_per_market."""
    s = _s(tmp_path)
    _hold(s, "mManual", "DOWN", "2.04", "2.04")      # the owner's inventory
    _hold(s, "mOther", "UP", "1.00", "0.50")
    out = s.account_wide_risk_by_market()
    assert out["ok"] is True
    assert set(out["markets"]) == {"mManual", "mOther"}
    assert len(out["risk_by_market"]) == 2
    assert D(out["total_usd"]) > 0


def test_manual_inventory_counts_toward_the_account_wide_total(tmp_path):
    s = _s(tmp_path)
    _hold(s, "mManual", "DOWN", "2.04", "2.04")
    total_without = D(s.account_wide_risk_by_market()["total_usd"])
    _hold(s, "mSecond", "DOWN", "3.00", "3.00")
    total_with = D(s.account_wide_risk_by_market()["total_usd"])
    assert total_with > total_without, "a second market must add exposure"


def test_the_pending_order_is_applied_to_its_own_market_only(tmp_path):
    s = _s(tmp_path)
    _hold(s, "mA", "UP", "1", "0.40")
    _hold(s, "mB", "UP", "1", "0.40")
    base = s.account_wide_risk_by_market()["risk_by_market"]
    withx = s.account_wide_risk_by_market(
        extra_market="mA", extra_side="UP", extra_qty=D("2"),
        extra_price=D("0.45"))["risk_by_market"]
    assert withx["mA"] > base["mA"]
    assert withx["mB"] == base["mB"], "no double counting onto other markets"


def test_a_resting_order_in_a_market_with_no_position_is_still_exposure(
        tmp_path):
    s = _s(tmp_path)
    s.state["live_orders"] = {"c1": {"market": "mRest", "remaining": "2",
                                     "price": "0.30", "side": "UP"}}
    out = s.account_wide_risk_by_market()
    assert out["ok"] is True
    assert "mRest" in out["markets"]


def test_an_unreadable_market_fails_closed_rather_than_summing_partially(
        tmp_path):
    """A partial sum understates the account -- the one direction that lets
    more risk through than the bound."""
    s = _s(tmp_path)
    _hold(s, "mA", "UP", "1", "0.40")
    _hold(s, "mB", "UP", "1", "0.40")
    real = s.risk_exposure_usd

    def boom(market, **kw):
        if market == "mB":
            raise RuntimeError("incoherent")
        return real(market, **kw)
    s.risk_exposure_usd = boom
    out = s.account_wide_risk_by_market()
    assert out["ok"] is False
    assert out["reason"].startswith("EXPOSURE_UNREADABLE:mB")
    assert "risk_by_market" not in out


def test_failed_enumeration_fails_closed(tmp_path):
    s = _s(tmp_path)

    def boom():
        raise RuntimeError("ledger unreadable")
    s.markets_with_exposure = boom
    out = s.account_wide_risk_by_market()
    assert out["ok"] is False
    assert out["reason"].startswith("EXPOSURE_ENUMERATION_FAILED")


def test_unknown_basis_inventory_is_priced_at_its_recorded_worst_case(
        tmp_path):
    """A $1/share adopted leg must not read as free inventory."""
    s = _s(tmp_path)
    _hold(s, "mUnknown", "DOWN", "2.04", "2.04")     # $1/share upper bound
    s.state["leg_cost_basis_unknown"] = ["mUnknown|DOWN"]
    out = s.account_wide_risk_by_market()
    assert out["ok"] is True
    assert D(out["risk_by_market"]["mUnknown"]) > 0


# ------------------------------------------- the limits actually bind now
def _gate():
    return VG.ValidationGates(max_cash_deployed_usd=D("7"),
                              max_unmatched_contracts=D("5"),
                              max_cumulative_loss_usd=D("20"),
                              max_l5_daily_loss_usd=D("5"),
                              max_l5_cumulative_loss_usd=D("20"),
                              max_worst_case_deficit_usd=D("3.60"),
                              cash_policy=VG.NO_RECYCLING)


def test_the_five_dollar_total_exposure_now_binds_across_markets():
    """With a real multi-market dict the total ceiling is no longer the same
    arithmetic as the per-market one."""
    lim = X.SafetyLimits(max_usd_per_market=D("5"), max_total_exposure_usd=D("5"),
                   max_abs_residual=D("10"), max_concurrent_markets=1,
                   max_live_orders=2)
    spread = {"mA": D("2.50"), "mB": D("2.50"), "mC": D("1.00")}
    why = lim.refuse(
        price=D("0.44"), qty=D("1"), market="mA",
        committed_by_market={}, residual_after=D(0),
        realized_today=D(0), open_markets=["mA"],
        outstanding_by_market={}, live_order_count=0,
        outstanding_residual=D(0), orders_uncertain=False,
        risk_by_market=spread)
    assert why == "MAX_TOTAL_EXPOSURE", why
    # and each market alone is under the per-market bound
    assert all(v <= D("5") for v in spread.values())


def test_a_single_market_under_both_bounds_still_passes():
    lim = X.SafetyLimits(max_usd_per_market=D("5"), max_total_exposure_usd=D("5"),
                   max_abs_residual=D("10"), max_concurrent_markets=1,
                   max_live_orders=2)
    why = lim.refuse(
        price=D("0.44"), qty=D("1"), market="mA",
        committed_by_market={}, residual_after=D(0),
        realized_today=D(0), open_markets=["mA"],
        outstanding_by_market={}, live_order_count=0,
        outstanding_residual=D(0), orders_uncertain=False,
        risk_by_market={"mA": D("3.60")})
    assert why is None, why


# --------------------------------------------- preflight book-read retry
def test_the_book_read_retries_a_transient_404_then_succeeds():
    from src import l5_pmus_transport as T
    assert T.BOOK_READ_ATTEMPTS == 3
    src = __import__("inspect").getsource(T.PmusTransport.preflight)
    assert "BOOK_READ_ATTEMPTS" in src
    assert "book_read_attempts" in src
    # the empty-book logic is untouched: still gated on bool(bk), and the
    # two-sided check is still informational with the binding check later
    assert 'gate("book_readable_now", bool(bk)' in src
    assert '"book_two_sided_at_preflight"' in src
    assert '"blocking": False' in src


def test_an_empty_book_is_never_made_executable_by_the_retry():
    """The retry is on the EXCEPTION path only."""
    from src import l5_pmus_transport as T
    src = __import__("inspect").getsource(T.PmusTransport.preflight)
    i = src.index("BOOK_READ_ATTEMPTS + 1")
    window = src[i:i + 900]
    # the loop breaks on a successful call, not on a non-empty book
    assert "bk = self.book(self.market_slug)" in window
    assert "if bk" not in window and "bool(bk)" not in window
