"""The envelope must bound what CAN fill, not only what has filled.

On 2026-09-24 window 1630z the shadow session produced 99 would-orders whose
modelled notional was $44.65 against a $5 cap, and the overlay refused none
of them. It was not broken: it tested `store.committed()`, which is
incremented in `apply_fill` and nowhere else, so a resting unfilled order
counted as nothing at all. In shadow that is harmless because nothing rests.
Armed it is the whole bound: orders that have not yet filled are precisely
the ones that still can.

So every check here asks the same question in a different way -- can a
sequence of decisions leave MORE fillable dollars at the venue than the
envelope allows, before any acknowledgement has come back?
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import src.l5_live_execution as E
from src.l5_probe_manifest import probe_limits


def store(tmp_path, name="inv.json"):
    return E.InventoryStore(str(tmp_path / name))


def lim(**kw):
    base = dict(max_usd_per_market=D("5"), max_total_exposure_usd=D("5"),
                max_abs_residual=D("10"), max_concurrent_markets=1,
                max_live_orders=1, daily_realized_loss_halt_usd=D("5"))
    base.update(kw)
    return E.SafetyLimits(**base)


def ask(limits, st, *, price="0.50", qty="4", market="m"):
    return limits.refuse(
        price=D(price), qty=D(qty), market=market,
        committed_by_market=st.committed(),
        residual_after=st.residual(market) + D(qty),
        realized_today=st.realized_today(),
        open_markets=(),
        outstanding_by_market=st.outstanding_by_market(),
        live_order_count=st.live_order_count(),
        outstanding_residual=st.outstanding_residual(market),
        orders_uncertain=st.orders_uncertain())


def submit(st, coid, *, market="m", side="UP", price="0.50", qty="4"):
    st.record_submit(coid, {"market": market, "side": side,
                            "price": D(price), "qty": D(qty)})


# ------------------------------------------------- the bug, stated as a test

def test_an_unfilled_order_consumes_the_envelope(tmp_path):
    st = store(tmp_path)
    assert ask(lim(), st, price="0.50", qty="4") is None      # $2.00, fine
    submit(st, "a", price="0.50", qty="4")
    # $2.00 is now resting at the venue and nothing has filled. Under the
    # old accounting `committed` was still {} and a second $2 order passed.
    assert st.committed() == {}
    assert st.outstanding_by_market() == {"m": D("2.00")}
    # $2.00 resting + $4.00 more = $6.00 fillable against a $5 cap.
    assert ask(lim(max_live_orders=9), st, price="0.50",
               qty="8") == "MAX_USD_PER_MARKET"


def test_rapid_decisions_cannot_exceed_the_cap_before_any_ack(tmp_path):
    st = store(tmp_path)
    limits = lim(max_live_orders=99)        # isolate the DOLLAR bound
    placed = D(0)
    for i in range(20):
        why = ask(limits, st, price="0.50", qty="4")   # $2.00 each
        if why:
            assert why in ("MAX_USD_PER_MARKET", "MAX_TOTAL_EXPOSURE")
            break
        submit(st, "o%d" % i, price="0.50", qty="4")
        placed += D("2.00")
    else:                                              # pragma: no cover
        pytest.fail("the cap never bound")
    assert placed <= D("5")
    assert sum(st.outstanding_by_market().values(), D(0)) <= D("5")
    assert st.committed() == {}          # still not one acknowledgement


def test_the_one_live_order_maximum_is_enforced(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.10", qty="1")             # $0.10, far under cap
    assert ask(lim(), st, price="0.10", qty="1") == "MAX_LIVE_ORDERS"


def test_a_terminal_order_releases_the_slot_and_the_dollars(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")
    assert ask(lim(), st) == "MAX_LIVE_ORDERS"
    st.terminate_order("a", reason="CANCELLED")
    assert st.outstanding_by_market() == {}
    assert ask(lim(), st) is None


# --------------------------------------------------------------- partial fills

def test_a_partial_fill_moves_dollars_across_not_away(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")             # $2.00 outstanding
    st.apply_fill("m", "UP", D("1"), D("0.50"), coid="a")
    # 1 share filled: $0.50 committed, $1.50 still fillable. The total the
    # envelope sees must not drop just because part of it landed.
    assert st.committed()["m"] == D("0.50")
    assert st.outstanding_by_market()["m"] == D("1.50")
    total = st.committed()["m"] + st.outstanding_by_market()["m"]
    assert total == D("2.00")


def test_a_partially_filled_order_is_still_live(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")
    st.apply_fill("m", "UP", D("1"), D("0.50"), coid="a")
    assert st.live_order_count() == 1
    assert ask(lim(), st) == "MAX_LIVE_ORDERS"


def test_a_complete_fill_ends_the_order(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")
    st.apply_fill("m", "UP", D("4"), D("0.50"), coid="a")
    assert st.live_order_count() == 0
    assert st.outstanding_by_market() == {}
    assert st.committed()["m"] == D("2.00")


def test_partial_fills_still_cannot_breach_the_cap(tmp_path):
    st = store(tmp_path)
    limits = lim(max_live_orders=99)
    for i in range(20):
        why = ask(limits, st, price="0.50", qty="4")
        if why:
            break
        submit(st, "o%d" % i, price="0.50", qty="4")
        st.apply_fill("m", "UP", D("1"), D("0.50"), coid="o%d" % i)
    potential = (sum(st.committed().values(), D(0))
                 + sum(st.outstanding_by_market().values(), D(0)))
    assert potential <= D("5")


# ------------------------------------------------- uncertainty and reconciling

def test_an_unknown_submit_outcome_refuses_everything_after_it(tmp_path):
    st = store(tmp_path)
    st.mark_uncertain("submit raised TimeoutError for a")
    assert ask(lim(), st) == "ORDER_STATE_UNCERTAIN"


def test_only_the_venue_clears_uncertainty(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")
    st.mark_uncertain("timeout")
    st.terminate_order("a", reason="CANCELLED")      # our own belief
    assert st.orders_uncertain()                     # is not enough
    assert ask(lim(), st) == "ORDER_STATE_UNCERTAIN"
    st.reconcile_from_venue([], [])
    assert st.orders_uncertain() is None
    assert ask(lim(), st) is None


def test_the_venue_releases_an_order_we_think_is_live(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")
    out = st.reconcile_from_venue([], [])            # venue lists none open
    assert out["live_orders_released"] == ["a"]
    assert st.live_order_count() == 0
    assert ask(lim(), st) is None


def test_the_venue_can_tell_us_about_an_order_we_never_recorded(tmp_path):
    st = store(tmp_path)
    out = st.reconcile_from_venue([], [{"client_order_id": "ghost",
                                        "market": "m", "side": "UP",
                                        "price": "0.50", "qty": "4",
                                        "remaining_qty": "4"}])
    assert out["live_orders_adopted"] == ["ghost"]
    assert st.outstanding_by_market()["m"] == D("2.00")
    assert ask(lim(), st) == "MAX_LIVE_ORDERS"


def test_a_delayed_acknowledgement_does_not_free_the_envelope(tmp_path):
    # the ack simply has not arrived. Nothing about that makes the resting
    # order less fillable, so nothing about it may free the slot.
    st = store(tmp_path)
    submit(st, "a", price="1.00", qty="5")           # the whole $5
    assert ask(lim(max_live_orders=99), st, price="0.01", qty="1") \
        == "MAX_USD_PER_MARKET"


# ------------------------------------------------------------ what is preserved

def test_the_stated_envelope_is_unchanged(tmp_path):
    p = probe_limits()
    assert p.max_usd_per_market == D("5")
    assert p.max_total_exposure_usd == D("5")
    assert p.max_abs_residual == D("10")
    assert p.max_concurrent_markets == 1
    # the ONLY change: a concurrency bound. Every dollar limit above is
    # unchanged, which is what this test exists to hold.
    assert p.max_live_orders == 8
    assert p.daily_realized_loss_halt_usd == D("5")
    assert p.band is None


def test_residual_is_bounded_on_what_would_land(tmp_path):
    st = store(tmp_path)
    submit(st, "a", side="UP", price="0.01", qty="8")
    assert st.outstanding_residual("m") == D("8")
    why = lim(max_live_orders=99).refuse(
        price=D("0.01"), qty=D("5"), market="m",
        committed_by_market=st.committed(), residual_after=D("5"),
        realized_today=D(0), open_markets=(),
        outstanding_by_market=st.outstanding_by_market(),
        live_order_count=st.live_order_count(),
        outstanding_residual=st.outstanding_residual("m"),
        orders_uncertain=None)
    assert why == "MAX_ABS_RESIDUAL"          # 5 + 8 = 13 > 10


def test_the_daily_halt_still_bites(tmp_path):
    st = store(tmp_path)
    st.state["realized_today"] = "-5"
    assert ask(lim(), st) == "DAILY_REALIZED_LOSS_HALT"


def test_live_orders_survive_a_restart(tmp_path):
    st = store(tmp_path)
    submit(st, "a", price="0.50", qty="4")
    again = E.InventoryStore(str(tmp_path / "inv.json"))
    assert again.live_order_count() == 1
    assert again.outstanding_by_market()["m"] == D("2.00")


def test_the_overlay_cannot_be_called_without_the_new_evidence():
    # a caller that has not been taught about live orders must fail loudly,
    # not silently lose the bound it never knew it had.
    with pytest.raises(TypeError):
        lim().refuse(price=D("0.5"), qty=D("1"), market="m",
                     committed_by_market={}, residual_after=D(0),
                     realized_today=D(0), open_markets=())


# ------------------------------------------- the whole path, not just the rule

class NeverFills:
    """Armed, acknowledges, and leaves the order resting. The realistic
    maker case, and the one the old accounting could not see."""

    armed = True
    #: the venue's own quantity grid, as this market's preflight
    #: reported it on 2026-09-25. A double without it is not
    #: modelling the transport -- and an unfaithful double is how
    #: the unquantized quantity reached the wire in the first place.
    quantity_increment = D("0.01")
    name = "NEVER_FILLS"

    def __init__(self):
        self.sent = []

    def submit(self, intent):
        self.sent.append(dict(intent))
        return {"order_id": "v%d" % len(self.sent), "filled_qty": "0"}


def test_rapid_l5_decisions_are_bounded_by_the_dollars(tmp_path, monkeypatch):
    """The 1630z shape: many decisions, no acknowledgement, one envelope.

    This used to assert ONE order, because one live order was the binding
    constraint. Probe 07 allows eight concurrent, so the bound under test is
    now the money -- which is the point of the change: the risk engine, not
    the order count, is the hard boundary. 2 shares at 0.60 is $1.20, so four
    orders reach $4.80 and the fifth would breach $5.
    """
    import datetime as dt
    import tests.test_l5_live_execution as L

    t = NeverFills()
    ex = E.FaithfulExecutor(store(tmp_path), probe_limits(),
                            transport=t, now_fn=L.NOW)
    recs = [ex.handle(L.row(key="k%d" % i, size=2.0), {},
                      market="m", window="16:30")
            for i in range(25)]
    assert len(t.sent) == 4, "$4.80 resting; a fifth would breach $5"
    assert sum(1 for r in recs
               if r.get("refused_reason") == "MAX_USD_PER_MARKET") == 21
    # nothing ever acknowledged a fill, and nothing ran away
    assert all(r.get("refused_reason") != "MAX_LIVE_ORDERS" for r in recs)
    later = recs[4:]
    assert all(r["refused_by_layer"] == "SAFETY_OVERLAY" for r in later)
    assert {r["refused_reason"] for r in later} == {"MAX_USD_PER_MARKET"}
    # and everything that did go out is inside the envelope, in AGGREGATE
    total = sum(D(str(o["price"])) * D(str(o["qty"])) for o in t.sent)
    assert total <= D("5")
    assert total == D("4.80")


def test_the_dollar_cap_alone_also_stops_the_1630z_shape(tmp_path):
    """With the live-order slot removed, the dollars still bound it.

    Belt and braces on purpose: if a later probe raises max_live_orders, the
    envelope must not go with it.
    """
    import tests.test_l5_live_execution as L
    from src.l5_live_execution import SafetyLimits

    t = NeverFills()
    limits = SafetyLimits(max_usd_per_market=D("5"),
                          max_total_exposure_usd=D("5"),
                          max_abs_residual=D("10000"),
                          max_concurrent_markets=1, max_live_orders=9999,
                          daily_realized_loss_halt_usd=D("5"))
    st = store(tmp_path)
    ex = E.FaithfulExecutor(st, limits, transport=t, now_fn=L.NOW)
    for i in range(40):
        ex.handle(L.row(key="k%d" % i, size=2.0), {},
                  market="m", window="16:30")
    fillable = (sum(st.committed().values(), D(0))
                + sum(st.outstanding_by_market().values(), D(0)))
    assert fillable <= D("5"), fillable
    assert st.committed() == {}            # not one acknowledgement arrived
