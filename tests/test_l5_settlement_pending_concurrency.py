"""Settlement-pending markets must not occupy a concurrency slot.

An expired market still owes its committed capital, its inventory and its
reconciliation. What it CANNOT do is accept another order, so it has no
business holding the single MAX_CONCURRENT_MARKETS slot while the venue
takes 85-115s to post settlement.

Exactly one thing narrows: the concurrency count. Every capital and loss
gate keeps reading the full book, so unsettled funds are never recycled.
The feature is OFF unless a caller passes `settlement_pending`, and these
tests pin that default as byte-for-byte today's behaviour.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX

A = "cpc-btc-updown-15m-2026-10-27-1330z"     # expired, unsettled
B = "cpc-btc-updown-15m-2026-10-27-1345z"     # the new window


def limits(**kw):
    base = dict(max_usd_per_market=D("20"), max_abs_residual=D("10"),
                max_total_exposure_usd=D("20"), max_concurrent_markets=1,
                max_live_orders=8, daily_realized_loss_halt_usd=D("5"))
    base.update(kw)
    return EX.SafetyLimits(**base)


_USE_COMMITTED = object()


def refuse(lim, *, market, committed, pending=(), outstanding=None,
           live=0, uncertain=None, risk=_USE_COMMITTED,
           qty=D("1"), price=D("0.50")):
    # risk=None exercises the NOTIONAL FALLBACK branch, which is the one
    # that adds this order's cost on top of the book.
    return lim.refuse(
        price=price, qty=qty, market=market,
        committed_by_market=committed,
        residual_after=D(0), realized_today=D(0),
        open_markets=[m for m, v in committed.items() if v > 0],
        outstanding_by_market=(outstanding or {}),
        live_order_count=live, orders_uncertain=uncertain,
        risk_by_market=(committed if risk is _USE_COMMITTED else risk),
        settlement_pending=pending)


# ------------------------------------------------- the default is unchanged

def test_default_is_todays_behaviour_exactly():
    """No caller passes it -> the expired market still blocks, as now."""
    assert refuse(limits(), market=B, committed={A: D("2")}) \
        == "MAX_CONCURRENT_MARKETS"


# ------------------------------------- 1. expired market, unsettled inventory

def test_expired_unsettled_market_frees_the_slot():
    assert refuse(limits(), market=B, committed={A: D("2")},
                  pending=[A]) is None


def test_the_expired_market_keeps_its_capital_charged():
    """Freeing the SLOT must not free the MONEY. $19 committed on the
    expired market plus this order must still breach the $20 ceiling."""
    assert refuse(limits(), market=B, committed={A: D("19")}, risk=None,
                  pending=[A], qty=D("4"), price=D("0.60")) \
        == "MAX_TOTAL_EXPOSURE"


# -------------------------------------------------------- 2. unknown expiry

def test_unknown_expiry_is_not_pending(tmp_path):
    """The classifier, not the gate: `expired` missing reads as NOT expired."""
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["committed"] = {A: "2"}
    assert st.settlement_pending_markets(expired={}) == []
    assert st.settlement_pending_markets(expired={A: None}) == []
    assert st.settlement_pending_markets(expired={A: "yes"}) == []
    assert st.settlement_pending_markets(expired={A: True}) == [A]


# --------------------------------------------------- 3. resting at expiry

def test_any_live_order_suppresses_the_whole_list(tmp_path):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["committed"] = {A: "2"}
    st.state["live_orders"] = {"o1": {"market": B, "side": "UP",
                                      "remaining": "1"}}
    assert st.settlement_pending_markets(expired={A: True}) == []


def test_uncertain_orders_suppress_the_whole_list(tmp_path, monkeypatch):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["committed"] = {A: "2"}
    monkeypatch.setattr(type(st), "orders_uncertain",
                        lambda self: "SUBMIT_ACK_LOST")
    assert st.settlement_pending_markets(expired={A: True}) == []


def test_unreadable_order_state_suppresses_the_whole_list(tmp_path,
                                                          monkeypatch):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["committed"] = {A: "2"}
    def boom(self):
        raise RuntimeError("volume unreadable")
    monkeypatch.setattr(type(st), "live_order_count", boom)
    assert st.settlement_pending_markets(expired={A: True}) == []


def test_the_market_being_worked_is_never_pending(tmp_path):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["committed"] = {A: "2", B: "1"}
    assert st.settlement_pending_markets(
        expired={A: True, B: True}, exclude=B) == [A]


# --------------------------------------- 4. two SIMULTANEOUSLY tradable

def test_two_tradable_markets_are_still_refused():
    """The invariant is unchanged: at most one TRADABLE market. B is not
    pending, so A plus B is two live ones and the second is refused."""
    assert refuse(limits(), market=B, committed={A: D("2")},
                  pending=[]) == "MAX_CONCURRENT_MARKETS"


def test_pending_one_but_two_other_tradable_still_refused():
    C = "cpc-btc-updown-15m-2026-10-27-1400z"
    assert refuse(limits(), market=C,
                  committed={A: D("1"), B: D("1")}, pending=[A]) \
        == "MAX_CONCURRENT_MARKETS"


def test_an_outstanding_order_keeps_a_market_tradable():
    """outstanding_by_market feeds live_markets too; pending must not
    override a market that still has a fillable order against it."""
    assert refuse(limits(), market=B, committed={},
                  outstanding={A: D("2")}, pending=[]) \
        == "MAX_CONCURRENT_MARKETS"


# ------------------------------------------- 5. insufficient remaining cash

def test_remaining_cash_binds_against_the_unsettled_commitment():
    """$18 tied up in the expired market leaves $2: a $3 order breaches."""
    assert refuse(limits(), market=B, committed={A: D("18")}, risk=None,
                  pending=[A], qty=D("5"), price=D("0.60")) \
        == "MAX_TOTAL_EXPOSURE"


def test_within_remaining_cash_passes():
    assert refuse(limits(), market=B, committed={A: D("10")},
                  pending=[A], qty=D("1"), price=D("0.50")) is None


# ------------------------------------------ 6. restart with settlement pending

def test_pending_survives_restart(tmp_path):
    p = str(tmp_path / "inv.json")
    st = EX.InventoryStore(p)
    st.state["committed"] = {A: "2"}
    st.save()
    again = EX.InventoryStore(p)
    assert again.committed().get(A) == D("2"), "commitment must survive"
    assert again.settlement_pending_markets(expired={A: True}) == [A]


# ---------------------------------------- 7. late settlement / failure

def test_settlement_failure_keeps_the_capital_charged(tmp_path):
    """Retirement never runs -> committed stays -> the money gate still
    sees it. Only the slot was freed, and that is the whole point."""
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["committed"] = {A: "2"}
    assert st.settlement_pending_markets(expired={A: True}) == [A]
    assert st.committed().get(A) == D("2")
    assert A in st.markets_with_exposure()


# ------------------------- 8. loss and exposure limits span BOTH markets

def test_daily_loss_halt_is_unaffected_by_pending():
    assert refuse(limits(), market=B, committed={A: D("1")},
                  pending=[A]) is None
    lim = limits()
    assert lim.refuse(
        price=D("0.50"), qty=D("1"), market=B,
        committed_by_market={A: D("1")}, residual_after=D(0),
        realized_today=D("-5"), open_markets=[A],
        outstanding_by_market={}, live_order_count=0,
        settlement_pending=[A]) == "DAILY_REALIZED_LOSS_HALT"


def test_the_pending_market_still_counts_toward_total_exposure():
    """Its risk is summed even though it no longer holds a slot."""
    assert refuse(limits(), market=B, committed={A: D("25")},
                  pending=[A], risk={A: D("25")}) == "MAX_TOTAL_EXPOSURE"


def test_per_market_ceiling_still_applies_to_the_pending_market():
    """Placed INTO the expired market (a completion), its own ceiling holds."""
    assert refuse(limits(max_usd_per_market=D("5")), market=A,
                  committed={A: D("9")}, pending=[A],
                  risk={A: D("9")}) == "MAX_USD_PER_MARKET"
