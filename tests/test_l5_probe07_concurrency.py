"""Eight orders may rest at once. Five dollars is still the boundary.

The count changed because replaying 12 clean windows showed it excluded 143
of 217 maker intents while the dollar cap excluded none, and 19 orders rested
simultaneously for $4.9983. The dollar engine -- not the order count -- is
what must refuse, so these tests push on the money with several orders
outstanding.
"""
from __future__ import annotations

import tempfile
import pathlib
from decimal import Decimal as D

import pytest

import src.l5_live_execution as EX
from src import l5_probe_manifest as PM

MARKET = "cpc-btc-updown-15m-2026-09-25-1900z"
SID = "l5probe-20260925-btc15m-07"


def store():
    return EX.InventoryStore(str(pathlib.Path(tempfile.mkdtemp()) / "i.json"))


def rest(st, coid, side="UP", price="0.50", qty="1", session=SID,
         acked=True):
    st.record_submit(coid, {"market": MARKET, "side": side,
                            "price": D(price), "qty": D(qty)},
                     session_id=session, mode="MAKER")
    if acked:
        # a RESTING order is one the venue acknowledged, and the id it gave
        # back is what /v1/order/{id} is asked by later. An order with no
        # id was never ACKed and is a different case entirely.
        st.attach_venue_id(coid, "V-%s" % coid)


# --------------------------------------------------------- the envelope

def test_the_authorised_envelope_changed_only_the_count():
    lim = PM.probe_limits().as_dict()
    assert lim["max_live_orders"] == 8
    assert lim["max_usd_per_market"] == "5"
    assert lim["max_total_exposure_usd"] == "5"
    assert lim["max_abs_residual"] == "10"
    assert lim["max_concurrent_markets"] == 1
    assert lim["daily_realized_loss_halt_usd"] == "5"
    assert PM.worst_case()["worst_case_total_usd"] == "5.1738"


# ------------------------------------- every resting order consumes risk

def test_a_resting_order_consumes_risk_assuming_a_full_fill():
    st = store()
    rest(st, "a", price="0.40", qty="2")           # 0.80 of premium
    assert st.risk_exposure_usd(MARKET) == D("0.80")


def test_several_resting_orders_aggregate():
    st = store()
    for i, q in enumerate(("1", "2", "3")):
        rest(st, "o%d" % i, price="0.50", qty=q)
    assert st.risk_exposure_usd(MARKET) == D("3.00")


def test_the_candidate_order_is_counted_before_it_is_sent():
    st = store()
    rest(st, "a", price="0.50", qty="4")           # 2.00 resting
    with_extra = st.risk_exposure_usd(MARKET, extra_side="UP",
                                      extra_qty=D("2"), extra_price=D("0.5"))
    assert with_extra == D("3.00")


def test_a_partial_fill_counts_filled_plus_remaining_exactly_once():
    st = store()
    rest(st, "a", price="0.50", qty="4")           # 2.00 of premium
    assert st.risk_exposure_usd(MARKET) == D("2.00")
    st.apply_fill(MARKET, "UP", D("1"), D("0.50"), coid="a", basis=EX.OWN)
    # 1 share held at 0.50 + 3 still resting at 0.50 -- still 2.00, not 2.50
    assert st.risk_exposure_usd(MARKET) == D("2.00")
    assert st.live_orders()["a"]["remaining"] == "3"


def test_a_full_fill_moves_the_premium_and_does_not_double_count():
    st = store()
    rest(st, "a", price="0.50", qty="2")
    st.apply_fill(MARKET, "UP", D("2"), D("0.50"), coid="a", basis=EX.OWN)
    assert "a" not in st.live_orders()
    assert st.risk_exposure_usd(MARKET) == D("1.00")


def test_a_terminal_order_stops_consuming_risk():
    st = store()
    rest(st, "a", price="0.50", qty="4")
    st.apply_order_progress("a", {"cum_qty": D(0), "terminal": True,
                                  "state": "ORDER_STATE_CANCELED"},
                            market=MARKET, side="UP")
    assert st.risk_exposure_usd(MARKET) == D(0)


# ------------------------------- the dollar boundary, several outstanding

def limits():
    return PM.probe_limits()


def refuse(st, price, qty):
    return limits().refuse(
        price=D(price), qty=D(qty), market=MARKET,
        committed_by_market=st.committed(),
        residual_after=st.residual(MARKET), realized_today=D(0),
        open_markets=[MARKET],
        outstanding_by_market=st.outstanding_by_market(),
        live_order_count=st.live_order_count(),
        risk_by_market={MARKET: st.risk_exposure_usd(
            MARKET, extra_side="UP", extra_qty=D(qty),
            extra_price=D(price))})


def test_five_dollars_refuses_before_the_count_does():
    st = store()
    for i in range(4):
        rest(st, "o%d" % i, price="0.60", qty="2")     # 4 x 1.20 = 4.80
    assert st.live_order_count() == 4                  # well under 8
    assert refuse(st, "0.60", "2") == "MAX_USD_PER_MARKET"


def test_an_order_that_fits_inside_five_dollars_is_allowed():
    st = store()
    for i in range(4):
        rest(st, "o%d" % i, price="0.25", qty="1")     # 1.00 total
    assert refuse(st, "0.25", "1") is None


def test_the_count_still_binds_at_eight():
    st = store()
    for i in range(8):
        rest(st, "o%d" % i, price="0.01", qty="1")     # 0.08 total: cheap
    assert st.live_order_count() == 8
    assert refuse(st, "0.01", "1") == "MAX_LIVE_ORDERS"


def test_seven_cheap_orders_still_admit_an_eighth():
    st = store()
    for i in range(7):
        rest(st, "o%d" % i, price="0.01", qty="1")
    assert refuse(st, "0.01", "1") is None


def test_the_boundary_is_the_aggregate_not_the_largest_order():
    st = store()
    rest(st, "a", price="0.90", qty="2")               # 1.80
    rest(st, "b", price="0.90", qty="2")               # 3.60
    # a third 1.80 would make 5.40
    assert refuse(st, "0.90", "2") == "MAX_USD_PER_MARKET"


# ------------------------------------------------ run-scoped provenance

def test_orders_carry_the_session_that_placed_them():
    st = store()
    rest(st, "mine", session=SID)
    rest(st, "theirs", session="l5probe-20260925-btc15m-04")
    assert set(st.live_orders_for_session(SID)) == {"mine"}
    assert set(st.live_orders_other_sessions(SID)) == {"theirs"}


def test_a_prior_sessions_order_still_consumes_the_envelope():
    """It is not ours to cancel, but it is certainly ours to count."""
    st = store()
    rest(st, "theirs", price="0.50", qty="4",
         session="l5probe-20260925-btc15m-04")
    assert st.risk_exposure_usd(MARKET) == D("2.00")


def test_an_unstamped_order_is_not_claimed_by_this_session():
    st = store()
    st.state["live_orders"] = {"legacy": {"market": MARKET, "side": "UP",
                                          "price": "0.5", "qty": "1",
                                          "remaining": "1", "state": "LIVE"}}
    assert st.live_orders_for_session(SID) == {}
    assert set(st.live_orders_other_sessions(SID)) == {"legacy"}


# ------------------------------------------- cancellation race / restart

def test_a_fill_that_lands_during_cancellation_is_still_booked():
    """The race: we ask to cancel, the venue fills it first."""
    st = store()
    rest(st, "a", price="0.50", qty="2")
    # our cancel is in flight; the authoritative refresh says FILLED
    out = st.apply_order_progress("a", {"cum_qty": D("2"), "leaves": D(0),
                                        "terminal": True, "avg_px": D("0.50"),
                                        "state": "ORDER_STATE_FILLED"},
                                  market=MARKET, side="UP")
    assert out["released"] is True
    assert st.position(MARKET, "UP") == D("2")
    assert "a" not in st.live_orders()


def test_a_cancel_after_a_fill_books_nothing_twice():
    st = store()
    rest(st, "a", price="0.50", qty="2")
    prog = {"cum_qty": D("2"), "terminal": True, "avg_px": D("0.50"),
            "state": "ORDER_STATE_FILLED"}
    st.apply_order_progress("a", prog, market=MARKET, side="UP")
    again = st.apply_order_progress("a", prog, market=MARKET, side="UP")
    assert D(again["delta"]) == 0
    assert st.position(MARKET, "UP") == D("2")


def test_state_survives_a_restart_with_several_orders_resting():
    d = pathlib.Path(tempfile.mkdtemp()) / "i.json"
    st = EX.InventoryStore(str(d))
    for i in range(3):
        st.record_submit("o%d" % i, {"market": MARKET, "side": "UP",
                                     "price": D("0.50"), "qty": D("2")},
                         session_id=SID, mode="MAKER")
    again = EX.InventoryStore(str(d))
    assert again.live_order_count() == 3
    assert again.risk_exposure_usd(MARKET) == D("3.00")
    assert set(again.live_orders_for_session(SID)) == {"o0", "o1", "o2"}


def test_recovery_releases_every_order_the_venue_no_longer_lists():
    """SUPERSEDED 2026-09-26: all three leave the envelope, none is dropped.

    Probe 07's claim -- that recovery must not carry phantom orders against
    MAX_LIVE_ORDERS forever -- holds and is still asserted. What is no
    longer true is that an absent order is finished with: absent includes
    filled, and 2045z settled 2.25 DOWN shares at zero because an absent
    order was released without its fill being booked.
    """
    st = store()
    for i in range(3):
        rest(st, "o%d" % i)
    out = st.reconcile_from_venue([], [])
    assert sorted(out["live_orders_pending_terminal"]) == ["o0", "o1", "o2"]
    assert st.live_order_count() == 0
    assert st.risk_exposure_usd(MARKET) == D(0)
    # each one still owes a final fill before this market can settle
    assert sorted(st.pending_terminal()) == ["o0", "o1", "o2"]


def test_recovery_adopts_an_order_the_venue_lists_that_we_lost():
    st = store()
    out = st.reconcile_from_venue([], [{"id": "ghost", "market": MARKET,
                                        "side": "UP", "price": "0.5",
                                        "quantity": "2",
                                        "remainingQuantity": "2"}])
    assert out["live_orders_adopted"] == ["ghost"]
    assert st.live_order_count() == 1


# ------------------------------------------------------- maker only

def test_maker_only_refuses_a_taker_translation():
    ex = EX.FaithfulExecutor(store(), limits(), maker_only=True)
    assert ex.maker_only is True
    assert ex.maker_treatment is False


def test_the_treatment_flags_are_off_unless_asked_for():
    ex = EX.FaithfulExecutor(store(), limits())
    assert ex.maker_treatment is False
    assert ex.maker_only is False


# ------------------------------------------- the switches reach the engine

def test_the_treatment_flags_default_off(monkeypatch):
    import scripts.l5_pmus_live_oneshot as P
    monkeypatch.delenv("L5_MAKER_TREATMENT", raising=False)
    monkeypatch.delenv("L5_MAKER_ONLY", raising=False)
    assert P.maker_treatment_enabled() is False
    assert P.maker_only_enabled() is False


def test_the_flags_read_the_environment(monkeypatch):
    import scripts.l5_pmus_live_oneshot as P
    monkeypatch.setenv("L5_MAKER_TREATMENT", "YES")
    monkeypatch.setenv("L5_MAKER_ONLY", "yes")
    assert P.maker_treatment_enabled() is True
    assert P.maker_only_enabled() is True


def test_run_window_passes_the_flags_to_the_executor(monkeypatch):
    """The wiring, asserted rather than assumed."""
    import src.l5_pmus_live_wiring as W
    import src.l5_live_execution as EX
    seen = {}
    real = EX.FaithfulExecutor

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(W.EX, "FaithfulExecutor", spy)
    W.run_window([], [], market=MARKET, window="1945z", store=store(),
                 limits=PM.probe_limits(), transport=None, lam=D("1"),
                 maker_treatment=True, maker_only=True)
    assert seen.get("maker_treatment") is True
    assert seen.get("maker_only") is True
