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


def venue_rows(signed_net, market=SLUG):
    """A venue position row shaped as `position_row` demands: unsigned
    bought/sold halves whose difference proves the sign convention, plus the
    signed net it must agree with. This is the shape the real endpoint
    returns -- a double that answered `[]` could not express the
    venue-reconciliation contract at all, which is why these tests stopped
    passing when that contract was added."""
    n = D(str(signed_net))
    bought, sold = (n, D(0)) if n >= 0 else (D(0), -n)
    return [{"marketSlug": market, "qtyBought": str(bought),
             "qtySold": str(sold), "netPositionDecimal": str(n)}]


def sync(x, market=SLUG):
    """Make the venue agree with the ledger, as it does when nothing has
    gone wrong. Tests that need a disagreement set `venue_positions`
    directly."""
    x.transport.venue_positions = venue_rows(x.store.residual(market),
                                             market)


@pytest.fixture()
def ex(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E

    class PosTransport(H.FakeTransport):
        venue_positions = []

        def positions(self):
            return list(self.venue_positions)

    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "DEFENSIVE")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    t = PosTransport()
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
    sync(x)
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
    sync(x)
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
    sync(x)
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
    sync(x)
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
    sync(x)
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
    sync(x)
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
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_NOTHING_UNMATCHED"
    assert x._defensive_mandate is None


def test_the_quantity_is_quantised_down_to_the_venue_grid(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.397"), D("0.37"), basis="OWN")
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True
    assert D(out["leg"]["qty"]) == D("3.39")


def test_an_unknown_quantity_grid_refuses(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    x.transport.quantity_increment = None
    sync(x)
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
    sync(x)
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
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True


def test_an_unreadable_mandate_expiry_blocks_rather_than_releasing(ex):
    x, t, E = ex
    x._defensive_mandate = {"market": SLUG, "expires_at": "banana"}
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_ALREADY_MANDATED"


# ============ BOTH DIRECTIONS, AND THE ONE ERROR THAT WOULD INVERT IT
#
# The unmatched DOWN case has never been exercised live -- 2315z and 2300z
# were both unmatched UP -- so it is asserted here rather than assumed from
# the UP case working. residual = UP - DOWN, so:
#   unmatched UP   (residual > 0) is reduced by buying DOWN (BUY_SHORT,
#                  selling the YES token into the BID, consuming bid depth)
#   unmatched DOWN (residual < 0) is reduced by buying UP (BUY_LONG,
#                  lifting the ASK, consuming ask depth)

def test_an_unmatched_DOWN_is_reduced_by_buying_UP_at_the_ask(ex):
    x, t, E = ex
    # held DOWN 3.39 at own 0.37; the executable UP price is the ask, 0.80
    x.store.apply_fill(SLUG, "DOWN", D("3.39"), D("0.37"), basis="OWN")
    sync(x)
    out = x.defensive_execute(
        market=SLUG, buy_side="UP", held_side="DOWN", resting=[],
        decide_kwargs=kwargs(buy_side="UP", exit_price=D("0.80")),
        prov=None)
    assert out["ok"] is True, out
    assert out["leg"]["side"] == "UP"
    assert out["leg"]["wire"]["intent"] == "ORDER_INTENT_BUY_LONG"
    assert out["leg"]["wire"]["side"] == "ORDER_SIDE_BUY"
    assert out["leg"]["wire"]["outcomeSide"] == "OUTCOME_SIDE_YES"
    # and it moves the residual toward zero from BELOW
    assert D(out["exposure"]["residual_before"]) == D("-3.39")
    assert D(out["exposure"]["residual_after"]) == D("0")


def test_an_unmatched_UP_is_reduced_by_buying_DOWN_into_the_bid(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True
    assert out["leg"]["wire"]["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert D(out["exposure"]["residual_before"]) == D("3.39")
    assert D(out["exposure"]["residual_after"]) == D("0")


def test_an_inverted_side_pair_is_refused_by_name_in_both_directions(ex):
    """A same-side pair does not describe a reduction of anything, and
    a side this venue does not have would be priced as if it did:
    `position()` answers 0 for it and the quantity arithmetic would compute
    a plausible-looking order for a market leg that does not exist. Checked
    before any state is read or any order is cancelled, so the refusal names
    the real fault instead of surfacing as NOTHING_UNMATCHED."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    for buy, held in (("UP", "UP"), ("DOWN", "DOWN"), ("SIDEWAYS", "UP"),
                      ("DOWN", "SIDEWAYS"), (None, "UP")):
        x._defensive_mandate = None
        sync(x)
        out = x.defensive_execute(
            market=SLUG, buy_side=buy, held_side=held, resting=[],
            decide_kwargs=kwargs(buy_side=buy), prov=None)
        assert out["reason"] == "DEFENSIVE_SIDES_NOT_OPPOSED", (buy, held)
        assert x._defensive_mandate is None
        assert t.cancelled == []


def test_the_reduction_direction_is_forced_by_the_derivation(ex):
    """WHAT ACTUALLY PREVENTS AN ADDING ORDER, stated as the property it is
    rather than as a branch that cannot fire.

    With the sides validated as opposed, `need = position(held) -
    position(buy)` being positive already means the held side is the long
    one, and residual is defined as position(UP) - position(DOWN). So the
    side we buy is forced to be the one that shortens the residual, and
    `qty = floor(need/inc)*inc <= need = |residual|` cannot overshoot flat.
    `DEFENSIVE_WOULD_INCREASE_EXPOSURE` and `..._REVERSE_EXPOSURE` are
    assertions of that against the store's own positions, not the guard --
    this asserts the guarantee itself, over both directions and a range of
    partial-fill states."""
    x, t, E = ex
    for held, buy, px in (("UP", "DOWN", D("0.80")),
                          ("DOWN", "UP", D("0.80"))):
        for filled, matched in (("3.39", "0"), ("3.39", "3.00"),
                                ("1.50", "0.01"), ("0.02", "0.01")):
            y = E.FaithfulExecutor(
                E.InventoryStore(str(x.store.path) + held + filled + matched),
                x.limits, transport=x.transport, now_fn=lambda: __import__(
                    "datetime").datetime(2026, 9, 23, 16, 15, 5,
                                         tzinfo=__import__(
                                             "datetime").timezone.utc),
                maker_only=True)
            y.store.apply_fill(SLUG, held, D(filled), D("0.37"), basis="OWN")
            if D(matched) > 0:
                y.store.apply_fill(SLUG, buy, D(matched), D("0.55"),
                                   basis="OWN")
            before = y.store.residual(SLUG)
            y.transport = x.transport.__class__()
            y.transport.quantity_increment = D("0.01")
            y.transport.venue_positions = venue_rows(before)
            out = y.defensive_execute(
                market=SLUG, buy_side=buy, held_side=held, resting=[],
                decide_kwargs=kwargs(buy_side=buy, exit_price=px), prov=None)
            assert out["ok"] is True, (held, filled, matched, out)
            q = D(out["leg"]["qty"])
            after = before - q if buy == "DOWN" else before + q
            # it shortens the residual and never crosses through flat
            assert abs(after) < abs(before), (held, filled, matched)
            assert abs(after) == abs(before) - q
            assert before * after >= 0, "the residual may not change sign"
            assert D(out["exposure"]["residual_before"]) == before
            assert D(out["exposure"]["residual_after"]) == after


def test_a_flat_book_is_never_defended_from_either_side(ex):
    x, t, E = ex
    for buy, held in (("DOWN", "UP"), ("UP", "DOWN")):
        x._defensive_mandate = None
        sync(x)
        out = x.defensive_execute(
            market=SLUG, buy_side=buy, held_side=held, resting=[],
            decide_kwargs=kwargs(buy_side=buy), prov=None)
        assert out["reason"] == "DEFENSIVE_NOTHING_UNMATCHED", (buy, held)


def test_a_defensive_order_may_reach_flat_but_never_cross_through_it(ex):
    """Overhedging in its other form: a quantity larger than the residual
    would flip the position to the opposite side and create exactly the
    directional exposure this is supposed to remove."""
    x, t, E = ex
    # 3.39 UP held, but the complement leg already matched 3.00 of it, so
    # only 0.39 is unmatched -- the order may be 0.39 and no more.
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    x.store.apply_fill(SLUG, "DOWN", D("3.00"), D("0.55"), basis="OWN")
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True
    assert D(out["leg"]["qty"]) == D("0.39")
    assert D(out["exposure"]["residual_after"]) == D("0")


def test_the_executable_price_and_the_depth_come_from_the_same_side(ex):
    """A long UP is unwound by SELLING the YES token into the bid, so the
    price is 1 - bid and the liquidity that matters is bid depth. Reading
    ask depth against a bid-side price would let a thin bid pass the depth
    test on the strength of a deep ask."""
    from src import l5_two_sided_shadow as SH
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    # bid 0.20 with only 1.00 displayed; the ask side is deep
    sync(x)
    out = x.defensive_execute(
        market=SLUG, buy_side="DOWN", held_side="UP", resting=[],
        decide_kwargs=kwargs(exit_price=D("0.80"),
                             displayed_depth=D("1.00")), prov=None)
    assert out["ok"] is False
    assert out["verdict"]["reason"] == "INSUFFICIENT_DISPLAYED_DEPTH"
    assert x.store.defensive_spent() == D(0)


# =================== THE EXPOSURE MUST BE THE VENUE'S, NOT OUR LEDGER'S

def test_an_unreadable_positions_endpoint_refuses(ex):
    """Fail closed: the leg then holds to settlement, which is the
    behaviour this window had before the feature existed. Never a
    defensive order against a position we cannot confirm."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")

    def boom():
        raise RuntimeError("HTTP 503")
    t.positions = boom
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_VENUE_POSITIONS_UNREADABLE"
    assert "HTTP 503" in out["error"]
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == D(0)


def test_a_venue_with_no_position_in_this_market_refuses(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    t.venue_positions = venue_rows("2.00", market="some-other-market")
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_VENUE_POSITION_ABSENT"
    assert x._defensive_mandate is None


def test_any_disagreement_at_all_refuses_rather_than_picking_a_side(ex):
    """Not "resolve in favour of the smaller" -- neither number may size an
    order that spends money while they disagree."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    for venue in ("3.38", "3.40", "0", "-3.39"):
        x._defensive_mandate = None
        t.venue_positions = venue_rows(venue)
        out = x.defensive_execute(
            market=SLUG, buy_side="DOWN", held_side="UP", resting=[],
            decide_kwargs=kwargs(), prov=None)
        assert out["reason"] == "DEFENSIVE_VENUE_POSITION_DISAGREES", venue
        assert D(out["residual_local"]) == D("3.39")
        assert D(out["residual_venue"]) == D(venue)


def test_a_row_whose_halves_contradict_its_net_refuses_the_whole_action(ex):
    """`position_row` refuses such a row rather than resolving it, and that
    refusal must stop the defensive action, not be swallowed."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    t.venue_positions = [{"marketSlug": SLUG, "qtyBought": "3.39",
                          "qtySold": "0", "netPositionDecimal": "1.00"}]
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["reason"] == "DEFENSIVE_VENUE_POSITIONS_UNREADABLE"
    assert x._defensive_mandate is None


def test_a_matched_pair_nets_to_zero_at_the_venue_as_it_does_here(ex):
    """The identity that makes the comparison valid at all: we buy UP and
    wire "buy DOWN" as a SELL, so a matched book is 0 - 0 = 0 at the venue
    and residual 0 in the ledger. If that were not true, every completed
    pair would read as a disagreement."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.75"), D("0.24"), basis="OWN")
    x.store.apply_fill(SLUG, "DOWN", D("3.75"), D("0.71"), basis="OWN")
    assert x.store.residual(SLUG) == D(0)
    from src import l5_live_execution as EX
    rows = [EX.position_row(r) for r in venue_rows("0")]
    assert sum(D(r["signed_net"]) for r in rows) == x.store.residual(SLUG)


def test_the_venue_reconciliation_is_reported_on_the_authorized_action(ex):
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("3.39"), D("0.37"), basis="OWN")
    sync(x)
    out = x.defensive_execute(market=SLUG, buy_side="DOWN", held_side="UP",
                              resting=[], decide_kwargs=kwargs(), prov=None)
    assert out["ok"] is True
    assert D(out["exposure"]["residual_venue_confirmed"]) == D("3.39")
    assert out["exposure"]["residual_venue_confirmed"] \
        == out["exposure"]["residual_before"]
