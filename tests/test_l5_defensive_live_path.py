"""The LIVE defensive path: the mandate, the gates it relaxes, the sequence.

This is the money path, not a shadow. What it asserts:

  * the maker-only overlay and the 0.96 pair ceiling are unchanged for every
    order in the system, and are relaxed ONLY for the exact order a live
    defensive mandate names;
  * no mandate is issued until the venue's own re-read proves the resting
    complement is gone;
  * a fill booked while cancelling stops the tick instead of being hedged
    over the top of;
  * the loss budget is charged BEFORE the wire and survives a restart.

Prices are the 2315z position: UP 3.39 at own 0.3700, bid 0.20, so the
executable own complement is 0.80 and the unwind pair cost is 1.17 -- above
the 0.96 maker ceiling and inside the 1.20 defensive one.
"""
import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_defensive_completion as DC
from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-09-23-1615z"
COID = "l5ts-complement-1"
NOW_TS = dt.datetime(2026, 9, 23, 16, 15, 5,
                     tzinfo=dt.timezone.utc).timestamp()
# 545s into the window: inside [T+540, T+600) with 55s of runway left
WS = NOW_TS - 545.0


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, "1")


@pytest.fixture()
def ex(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "DEFENSIVE")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    t = H.FakeTransport()
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                           transport=t, now_fn=H.NOW, maker_only=True)
    return x, t, E


def kwargs(**kw):
    base = dict(
        now_t=NOW_TS, window_start=WS,
        complete_cutoff_s=720.0, cancel_cutoff_s=600.0,
        unmatched_qty=D("3.39"), first_leg_cost=D("0.37"),
        exit_price=D("0.80"), maker_complement_price=D("0.55"),
        buy_side="DOWN",
        displayed_depth=D("1831.25"),
        taker_fee_per_contract=TS.TAKER_THETA * D("0.80") * D("0.20"),
        defensive_spent_usd=D(0), window_settled=False)
    base.update(kw)
    return base


def rest(x, coid=COID, side="DOWN", vid="v1", remaining="3.39"):
    x.store.state.setdefault("live_orders", {})[coid] = {
        "market": SLUG, "side": side, "venue_order_id": vid,
        "client_order_id": coid, "remaining": remaining,
        "state": "WORKING", "price": "0.71"}
    x.store.save()


# ------------------------------------------- the overlay, for everyone else

def test_the_maker_only_overlay_is_unchanged_without_a_mandate(ex):
    x, t, E = ex
    assert x._defensive_mandate is None
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.80", "qty": "3.39"}) is None


def test_a_mandate_covers_only_the_exact_order_it_names(ex):
    x, t, E = ex
    x._defensive_mandate = {
        "market": SLUG, "side": "DOWN", "price": "0.80", "qty": "3.39",
        "expires_at": NOW_TS + 55.0}
    ok = {"side": "DOWN"}, {"price": "0.80", "qty": "3.39"}
    assert x.defensive_mandate_covers(SLUG, *ok)
    # every field is load-bearing
    assert x.defensive_mandate_covers("other-slug", *ok) is None
    assert x.defensive_mandate_covers(
        SLUG, {"side": "UP"}, {"price": "0.80", "qty": "3.39"}) is None
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.81", "qty": "3.39"}) is None
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.80", "qty": "3.40"}) is None
    # ... but 3.39 == 3.390: a mandate is matched on VALUE, not on spelling
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.800", "qty": "3.390"})


def test_a_mandate_expires_on_the_window_clock(ex):
    x, t, E = ex
    x._defensive_mandate = {
        "market": SLUG, "side": "DOWN", "price": "0.80", "qty": "3.39",
        "expires_at": NOW_TS - 1.0}
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.80", "qty": "3.39"}) is None


def test_a_mandate_is_inert_while_the_flag_is_off(ex, monkeypatch):
    """Belt and braces: the flag gates the issuing AND the honouring, so a
    mandate left in memory cannot outlive the flag that allowed it."""
    x, t, E = ex
    x._defensive_mandate = {
        "market": SLUG, "side": "DOWN", "price": "0.80", "qty": "3.39",
        "expires_at": NOW_TS + 55.0}
    monkeypatch.delenv(DC.DEFENSIVE_ENABLED_ENV, raising=False)
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.80", "qty": "3.39"}) is None


def test_an_unreadable_mandate_covers_nothing(ex):
    x, t, E = ex
    for bad in ("not a mapping", {"market": SLUG}, {}):
        x._defensive_mandate = bad
        assert x.defensive_mandate_covers(
            SLUG, {"side": "DOWN"}, {"price": "0.80", "qty": "3.39"}) is None


# ------------------------------------------------------ the pair ceiling

def test_the_096_ceiling_still_refuses_the_unwind_without_a_mandate(ex):
    """1.17 is a certain loss by the normal rule, and the normal rule is
    what applies to every order that no mandate names."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    bad = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                              {"price": "0.80", "qty": "3.39"})
    assert bad and bad["why"] == "PAIR_COST_ABOVE_CEILING"
    assert bad["ceiling"] == str(TS.max_pair_cost())
    assert bad["ceiling_is_defensive"] is False


def test_the_defensive_ceiling_admits_the_same_order_under_a_mandate(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    x._defensive_mandate = {
        "market": SLUG, "side": "DOWN", "price": "0.80", "qty": "3.39",
        "expires_at": NOW_TS + 55.0}
    assert x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                               {"price": "0.80", "qty": "3.39"}) is None


def test_the_defensive_ceiling_is_a_ceiling_and_not_an_exemption(ex):
    """1.20 is a bound, not permission. A 1.25 pair is refused under a
    mandate exactly as it is without one."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.45"), basis="OWN")
    x._defensive_mandate = {
        "market": SLUG, "side": "DOWN", "price": "0.80", "qty": "3.39",
        "expires_at": NOW_TS + 55.0}
    bad = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                              {"price": "0.80", "qty": "3.39"})
    assert bad and bad["ceiling"] == str(DC.DEFENSIVE_PAIR_CEILING)
    assert bad["ceiling_is_defensive"] is True


# ------------------------------------------------------- the sequence

def test_the_happy_path_cancels_then_mandates_one_crossing_order(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    rest(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[COID], decide_kwargs=kwargs(),
                              prov=None)
    assert out["ok"] is True, out
    # the complement was cancelled by VENUE id and proved by a re-read
    assert t.cancelled == ["v1"]
    assert out["confirmations"][0]["reason"] \
        == "CANCEL_CONFIRMED_BY_VENUE_REREAD"
    # one crossing order, at the executable price, for the unmatched size
    assert out["leg"]["mode"] == "TAKER"
    assert out["leg"]["post_only"] is False
    assert out["leg"]["price"] == "0.80"
    assert D(out["leg"]["qty"]) == D("3.39")
    assert out["leg"]["wire"]["intent"] == "ORDER_INTENT_BUY_SHORT"
    # and the mandate names exactly that order
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.80", "qty": "3.39"})


def test_the_budget_is_charged_before_the_wire_and_persists(ex, tmp_path):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    rest(x)
    assert x.store.defensive_spent() == D(0)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[COID], decide_kwargs=kwargs(),
                              prov=None)
    spent = x.store.defensive_spent()
    assert spent == D(out["verdict"]["exit_downside_usd"])
    assert spent > 0
    # a fresh store over the same file still sees it: a restart cannot
    # reset the budget
    again = E.InventoryStore(x.store.path)
    assert again.defensive_spent() == spent
    assert again.state["defensive_log"][0]["market"] == SLUG


def test_the_budget_binds_the_second_attempt(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    rest(x)
    x.store.defensive_charge(D("1.95"), {"market": SLUG, "note": "earlier"})
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[COID],
                              decide_kwargs=kwargs(
                                  defensive_spent_usd=x.store
                                  .defensive_spent()),
                              prov=None)
    assert out["ok"] is False
    assert out["reason"] == "DEFENSIVE_REFUSED_AFTER_CANCEL"
    assert out["verdict"]["reason"] == "DEFENSIVE_LOSS_BUDGET"
    # NO MANDATE from a refused verdict
    assert x._defensive_mandate is None


def test_an_unconfirmed_cancellation_issues_no_mandate(ex):
    """The dangerous case: the cancel returned and the order is still
    working. A defensive order beside it is how both fill."""
    x, t, E = ex

    class StillOpen(type(t)):
        def order(self, order_id):
            return {"id": order_id, "state": "ORDER_STATE_WORKING",
                    "cumQuantity": "0", "leavesQuantity": "3.39",
                    "avgPx": {"value": "0", "currency": "USD"},
                    "commissionNotionalTotalCollected": {"value": "0",
                                                         "currency": "USD"}}

    x.transport = StillOpen()
    x.transport.quantity_increment = D("0.01")
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    rest(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[COID], decide_kwargs=kwargs(),
                              prov=None)
    assert out["ok"] is False
    assert out["reason"] == "DEFENSIVE_CANCEL_NOT_CONFIRMED"
    assert x._defensive_mandate is None
    # and the book is marked uncertain, which refuses the next submission
    assert x.store.orders_uncertain()


def test_a_fill_booked_while_cancelling_stops_the_tick(ex):
    """Overhedging is refused, not resized in flight: the residual the
    complement existed to match has already shrunk."""
    x, t, E = ex

    class FilledOnCancel(type(t)):
        def order(self, order_id):
            return {"id": order_id, "state": "ORDER_STATE_CANCELED",
                    "cumQuantity": "1.25", "leavesQuantity": "0",
                    "avgPx": {"value": "0.29", "currency": "USD"},
                    "commissionNotionalTotalCollected": {"value": "0",
                                                         "currency": "USD"}}

    x.transport = FilledOnCancel()
    x.transport.quantity_increment = D("0.01")
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    rest(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[COID], decide_kwargs=kwargs(),
                              prov=None)
    assert out["ok"] is False
    assert out["reason"] == "DEFENSIVE_FILLED_DURING_CANCEL"
    assert D(out["filled_during_cancel"]) == D("1.25")
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == D(0)


def test_unreadable_live_orders_refuse_before_anything_is_cancelled(ex):
    x, t, E = ex
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=["__unreadable__"],
                              decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_LIVE_ORDERS_UNREADABLE"
    assert t.cancelled == []


def test_nothing_unmatched_after_the_cancel_is_not_defended(ex):
    x, t, E = ex
    # matched already: both sides hold the same quantity
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    x.store.apply_fill(SLUG, "DOWN", D("3.39"), D("0.55"), basis="OWN")
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_NOTHING_UNMATCHED"
    assert x._defensive_mandate is None


def test_the_quantity_is_quantised_down_to_the_venue_grid(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.397"), D("0.37"), basis="OWN")
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True
    assert D(out["leg"]["qty"]) == D("3.39")


def test_an_unknown_quantity_grid_refuses(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    x.transport.quantity_increment = None
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_QUANTITY_INCREMENT_UNKNOWN"


def test_one_defensive_attempt_per_window_and_one_charge_for_it(ex):
    """A mandate expires on the clock, not on consumption, so a second
    attempt in the same window must be refused outright -- otherwise an
    order refused downstream could be rebuilt identically and ride a
    mandate that was already paid for."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    rest(x)
    first = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                                resting=[COID], decide_kwargs=kwargs(),
                                prov=None)
    assert first["ok"] is True
    charged = x.store.defensive_spent()
    second = x.defensive_execute(market=SLUG, buy_side="DOWN",
                                 held_side="UP", resting=[],
                                 decide_kwargs=kwargs(), prov=None)
    assert second["ok"] is False
    assert second["reason"] == "DEFENSIVE_ALREADY_MANDATED"
    assert x.store.defensive_spent() == charged      # charged ONCE


def test_a_mandate_in_another_market_does_not_block_this_one(ex):
    x, t, E = ex
    x._defensive_mandate = {
        "market": "cpc-btc-updown-15m-2026-09-23-1600z", "side": "DOWN",
        "price": "0.80", "qty": "1.00", "expires_at": NOW_TS + 55.0}
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True


def test_an_unreadable_mandate_expiry_blocks_rather_than_releasing(ex):
    x, t, E = ex
    x._defensive_mandate = {"market": SLUG, "expires_at": "banana"}
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_ALREADY_MANDATED"
