"""The REAL second-leg completion: the route `WOULD_SUBMIT` used to stand in for.

WHAT THIS IS ABOUT. `maybe_ts_completion` computed the economics, asked the
bounded policy, got a yes from both, and then wrote

    rec["crossed"] = "WOULD_SUBMIT"
    rec["submission_route"] = "deliberately not wired"

On 2026-11-06 it reached that string 43 times in the 0300z window and 22
times in 0315z -- every one with `ok: true, cross: true` -- while the two
residuals those windows left settled for -$1.00. The live records are
archived under `analysis/l5/issue195/` and the figures below are taken from
them verbatim.

WHAT IS ASSERTED. That the route exists, that it is doubly default-OFF, that
it cancels and PROVES before it crosses, that it can never exceed the
confirmed unmatched deficit, that it cannot be run twice for the same market
and window across a restart, and that every uncertainty refuses.

Nothing here opens a socket, uses a credential, or submits to a venue.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_defensive_completion as DC
from src import l5_live_authorization as AUTH
from src import l5_pmus_live_runner as RUN
from src import l5_taker_completion_policy as TCP
from src import l5_two_sided_probe as TS

#: the 0300z window of 2026-11-06, the one that settled -$0.59
SLUG = "cpc-btc-updown-15m-2026-11-06-0300z"
COID = "l5ts-complement-0300z"
NOW_TS = dt.datetime(2026, 11, 6, 3, 2, 12,
                     tzinfo=dt.timezone.utc).timestamp()
#: +132.4s, which is where the live record's first authorized decision sits
WS = NOW_TS - 132.4


# ------------------------------------------------------------- the harness

def venue_rows(signed_net, market=SLUG):
    """A row shaped as `position_row` demands: unsigned bought/sold halves
    whose difference proves the sign, plus the signed net it must agree
    with."""
    n = D(str(signed_net))
    bought, sold = (n, D(0)) if n >= 0 else (D(0), -n)
    return [{"marketSlug": market, "qtyBought": str(bought),
             "qtySold": str(sold), "netPositionDecimal": str(n)}]


@pytest.fixture(autouse=True)
def _switches_on(monkeypatch):
    """BOTH switches, because the whole point is that neither alone is
    permission. The off cases set them back themselves."""
    monkeypatch.setenv(TCP.POLICY_ENV, "YES")
    monkeypatch.setenv(TCP.OWNER_CROSS_ENV, "YES")


@pytest.fixture()
def ex(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E

    class PosTransport(H.FakeTransport):
        venue_positions = []
        def positions(self):
            return list(self.venue_positions)

    class _Book:
        """The authenticated mirror the executor re-reads at execution.
        0.67/0.68 is the real 0300z top of book, so own DOWN is 0.33 --
        cheaper than the 0.3500 these tests authorize, i.e. the price has
        NOT moved against us."""
        snap = {"bid": "0.6700", "ask": "0.6800", "stream_age_s": "0.05",
                "bid_shares": "448", "ask_shares": "120.84"}

        def snapshot(self, _slug):
            return dict(self.snap) if self.snap is not None else None

    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "TAKER_COMPLETION")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    t = PosTransport()
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                           transport=t, now_fn=H.NOW, maker_only=True)
    x.ts_book = _Book()
    return x, t, E


def hold(x, *, up="2", dn="1", market=SLUG, up_px="0.6200",
         dn_px="0.3500"):
    """The 0300z book: UP 2 at own 0.6200, DOWN 1 at own 0.3500.

    THE COST BASIS AND ITS ATTESTATION ARE SET TOO, not just the quantity.
    `TS.top_up` reads a missing price as D(0) -- a FREE leg -- and a free leg
    makes every completion look like an improvement however dear, so a
    helper that set only `positions` had the tests measuring the wrong
    arithmetic. `fill_count` is what `cost_attested` reads, and the
    execution-time check refuses an unattested leg outright.
    """
    pos = x.store.state.setdefault("positions", {})
    lc = x.store.state.setdefault("leg_cost", {})
    fc = x.store.state.setdefault("fill_count", {})
    for side, qty, px in (("UP", up, up_px), ("DOWN", dn, dn_px)):
        pos["%s|%s" % (market, side)] = str(qty)
        lc["%s|%s" % (market, side)] = str(D(str(qty)) * D(str(px)))
        fc["%s|%s" % (market, side)] = 1 if D(str(qty)) > 0 else 0
    x.store.save()
    x.transport.venue_positions = venue_rows(D(str(up)) - D(str(dn)), market)


def proposal(**kw):
    """What `l5_taker_completion_policy.authorize` returns when it says yes,
    with the live 0300z figures."""
    base = {"ok": True, "authorized": True, "reason": "AUTHORIZED",
            "side": "DOWN", "qty": "1", "price": "0.3500",
            "market": SLUG,
            "policy": "BOUNDED_TAKER_COMPLETION",
            "expires_at": WS + 600.0}
    base.update(kw)
    return base


def rest(x, coid=COID, side="DOWN", vid="v1", remaining="1"):
    x.store.state.setdefault("live_orders", {})[coid] = {
        "market": SLUG, "side": side, "venue_order_id": vid,
        "client_order_id": coid, "remaining": remaining,
        "state": "WORKING", "price": "0.32"}
    x.store.save()


def run(x, *, resting=(), prop=None, **kw):
    base = dict(market=SLUG, buy_side="DOWN", held_side="UP",
                resting=list(resting), proposal=prop or proposal(),
                window_start=WS, now_t=NOW_TS, prov=None)
    base.update(kw)
    return x.completion_taker_execute(**base)


# =====================================================================
# 1. THE SWITCHES. Neither alone is permission, and both default OFF.
# =====================================================================

def test_both_switches_default_off():
    import os
    for env in (TCP.POLICY_ENV, TCP.OWNER_CROSS_ENV):
        os.environ.pop(env, None)
    assert TCP.taker_completion_enabled() is False


@pytest.mark.parametrize("on,off", [
    (TCP.POLICY_ENV, TCP.OWNER_CROSS_ENV),
    (TCP.OWNER_CROSS_ENV, TCP.POLICY_ENV),
])
def test_one_switch_alone_is_not_permission(monkeypatch, on, off):
    monkeypatch.setenv(on, "YES")
    monkeypatch.delenv(off, raising=False)
    assert TCP.taker_completion_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "on", "YES ", "yes", ""])
def test_only_a_literal_yes_enables_crossing(monkeypatch, value):
    monkeypatch.setenv(TCP.POLICY_ENV, "YES")
    monkeypatch.setenv(TCP.OWNER_CROSS_ENV, value)
    expected = value.strip().upper() == "YES"
    assert TCP.taker_completion_enabled() is expected


def test_with_the_switches_off_the_route_reads_no_state_and_cancels_nothing(
        ex, monkeypatch):
    x, t, E = ex
    monkeypatch.delenv(TCP.POLICY_ENV, raising=False)
    hold(x)
    rest(x)
    out = run(x, resting=[COID])
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_TAKER_NOT_ENABLED"
    # the complement is untouched: nothing was cancelled on the way to a
    # refusal that should have happened before any state was read
    assert COID in x.store.live_orders()
    assert x._defensive_mandate is None


def test_an_unauthorized_proposal_is_refused_before_anything_is_cancelled(ex):
    x, t, E = ex
    hold(x)
    rest(x)
    out = run(x, resting=[COID],
              prop=proposal(authorized=False, reason="MARGIN_BELOW_FLOOR"))
    assert out["reason"] == "COMPLETION_TAKER_NOT_AUTHORIZED"
    assert out["proposal_reason"] == "MARGIN_BELOW_FLOOR"
    assert COID in x.store.live_orders()


# =====================================================================
# 2. THE 0300z AND 0315z CASES FROM THE LIVE RECORD.
# =====================================================================

def test_0300z_two_up_one_down_authorizes_exactly_one_contract(ex):
    """L5_TS_COMPLETION, 03:02:12Z: up_qty 2 @ 0.6200, dn_qty 1 @ 0.3500,
    deficit 1 DOWN, price 0.3500, worst_improvement_usd 0.6300."""
    x, t, E = ex
    hold(x, up="2", dn="1")
    out = run(x)
    assert out["ok"] is True
    assert out["leg"]["side"] == "DOWN"
    assert out["leg"]["qty"] == "1"
    assert out["leg"]["price"] == "0.3500"
    assert out["leg"]["mode"] == "TAKER"
    assert out["leg"]["post_only"] is False
    assert out["exposure"]["residual_before"] == "1"
    assert out["exposure"]["residual_after"] == "0"


def test_0315z_one_up_no_down_authorizes_the_single_deficit(ex):
    """The second losing window: 1 UP / 0 DOWN, residual 1."""
    x, t, E = ex
    hold(x, up="1", dn="0")
    out = run(x, prop=proposal(qty="1", price="0.6300"))
    assert out["ok"] is True
    assert out["leg"]["qty"] == "1"
    assert out["exposure"]["residual_after"] == "0"


def test_the_mandate_names_exactly_the_order_and_covers_only_it(ex):
    x, t, E = ex
    hold(x)
    out = run(x)
    m = out["mandate"]
    assert m["origin"] == TCP.MANDATE_ORIGIN
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.3500", "qty": "1"})
    # one cent away, or one contract away, and it covers nothing
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.3600", "qty": "1"}) is None
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.3500", "qty": "2"}) is None
    assert x.defensive_mandate_covers(
        SLUG, {"side": "UP"}, {"price": "0.3500", "qty": "1"}) is None
    assert x.defensive_mandate_covers(
        "other-market", {"side": "DOWN"},
        {"price": "0.3500", "qty": "1"}) is None


def test_the_mandate_is_gated_by_its_own_switches_not_the_defensive_flag(
        ex, monkeypatch):
    """A taker-completion mandate must not become reachable by turning the
    DEFENSIVE flag on, and must stop covering anything when its own
    switches go off."""
    x, t, E = ex
    hold(x)
    run(x)
    ok = (SLUG, {"side": "DOWN"}, {"price": "0.3500", "qty": "1"})
    assert x.defensive_mandate_covers(*ok)
    monkeypatch.delenv(TCP.OWNER_CROSS_ENV, raising=False)
    monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, "1")
    assert x.defensive_mandate_covers(*ok) is None


def test_an_unrecognised_origin_is_read_as_defensive(ex, monkeypatch):
    """The stricter reading of a mandate we did not write."""
    x, t, E = ex
    x._defensive_mandate = {"market": SLUG, "side": "DOWN", "price": "0.35",
                            "qty": "1", "expires_at": NOW_TS + 60.0}
    ok = (SLUG, {"side": "DOWN"}, {"price": "0.35", "qty": "1"})
    monkeypatch.delenv(DC.DEFENSIVE_ENABLED_ENV, raising=False)
    assert x.defensive_mandate_covers(*ok) is None
    monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, "1")
    assert x.defensive_mandate_covers(*ok)


# =====================================================================
# 3. CANCEL, THEN PROVE. The order of operations IS the safety property.
# =====================================================================

def test_the_resting_complement_is_cancelled_before_a_mandate_exists(ex):
    x, t, E = ex
    hold(x)
    rest(x)
    out = run(x, resting=[COID])
    assert out["ok"] is True
    assert out["confirmations"] and out["confirmations"][0]["confirmed"]
    assert COID not in x.store.live_orders()


def test_an_unconfirmed_cancellation_issues_no_mandate(ex, monkeypatch):
    x, t, E = ex
    hold(x)
    rest(x)
    monkeypatch.setattr("src.l5_live_execution.deadman",
                        lambda *a, **k: {"cancelled": []})
    out = run(x, resting=[COID])
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_CANCEL_NOT_CONFIRMED"
    assert x._defensive_mandate is None


def test_a_cancel_that_raises_leaves_the_position_as_it_was(ex, monkeypatch):
    x, t, E = ex
    hold(x)
    rest(x)
    monkeypatch.setattr(
        "src.l5_live_execution.deadman",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("venue 500")))
    out = run(x, resting=[COID])
    assert out["reason"] == "COMPLETION_CANCEL_FAILED"
    assert x._defensive_mandate is None


def test_a_fill_booked_while_cancelling_stops_the_tick(ex, monkeypatch):
    """OVERHEDGING IS REFUSED, NOT RESIZED IN FLIGHT. If the complement
    filled while we were cancelling it, the residual it was there to match
    has already shrunk -- possibly to nothing."""
    x, t, E = ex
    hold(x)
    rest(x)
    monkeypatch.setattr(
        "src.l5_defensive_completion.cancel_confirmed",
        lambda res, coid: {"confirmed": True, "filled_during_cancel": "1"})
    monkeypatch.setattr("src.l5_live_execution.deadman",
                        lambda *a, **k: {"cancelled": [COID]})
    out = run(x, resting=[COID])
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_FILLED_DURING_CANCEL"
    assert out["filled_during_cancel"] == "1"
    assert x._defensive_mandate is None


def test_an_unreadable_live_order_list_refuses(ex):
    x, t, E = ex
    hold(x)
    assert run(x, resting=["__unreadable__"])["reason"] == (
        "COMPLETION_LIVE_ORDERS_UNREADABLE")


# =====================================================================
# 4. NEVER BEYOND THE CONFIRMED DEFICIT. PARTIAL FILLS AND MOVEMENT.
# =====================================================================

def test_the_order_is_the_smaller_of_the_deficit_and_the_authorization(ex):
    """The policy authorized 5 against a deficit of 1. The order is 1."""
    x, t, E = ex
    hold(x, up="2", dn="1")
    out = run(x, prop=proposal(qty="5"))
    assert out["ok"] is True and out["leg"]["qty"] == "1"


def test_a_partially_filled_complement_shrinks_the_completion(ex):
    """The deficit is re-read from the ledger AFTER the cancel, so a
    complement that filled 0.6 of its 1 leaves 0.4 to complete, not 1."""
    x, t, E = ex
    hold(x, up="2", dn="1.6")
    out = run(x, prop=proposal(qty="1"))
    assert out["ok"] is True
    assert D(out["leg"]["qty"]) == D("0.40")
    assert out["exposure"]["residual_before"] == "0.4"


def test_a_complement_that_filled_in_full_leaves_nothing_to_complete(ex):
    x, t, E = ex
    hold(x, up="2", dn="2")
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_NOTHING_UNMATCHED"
    assert x._defensive_mandate is None


def test_a_completion_may_never_reverse_the_position(ex):
    x, t, E = ex
    hold(x, up="1", dn="0")
    # a proposal for 3 against a residual of 1 is clamped to 1, never 3
    out = run(x, prop=proposal(qty="3"))
    assert out["ok"] is True and D(out["leg"]["qty"]) == D(1)


def test_buying_the_side_we_already_hold_is_refused(ex):
    """Residual is UP - DOWN, so an unmatched UP is reduced only by buying
    DOWN. Buying more UP is an OPENING wearing a completion's name."""
    x, t, E = ex
    hold(x, up="2", dn="1")
    out = run(x, buy_side="UP", held_side="DOWN", prop=proposal(side="UP"))
    assert out["ok"] is False
    assert out["reason"] in ("COMPLETION_NOTHING_UNMATCHED",
                             "COMPLETION_WOULD_INCREASE_EXPOSURE")
    assert x._defensive_mandate is None


@pytest.mark.parametrize("buy,held", [("DOWN", "DOWN"), ("UP", "UP"),
                                      ("SIDEWAYS", "UP"), ("DOWN", "")])
def test_sides_that_do_not_describe_a_reduction_are_refused(ex, buy, held):
    x, t, E = ex
    hold(x)
    out = run(x, buy_side=buy, held_side=held)
    assert out["reason"] == "COMPLETION_SIDES_NOT_OPPOSED"


def test_a_proposal_for_the_wrong_side_is_refused(ex):
    x, t, E = ex
    hold(x)
    out = run(x, prop=proposal(side="UP"))
    assert out["reason"] == "COMPLETION_PROPOSAL_SIDE_MISMATCH"


@pytest.mark.parametrize("market", ["some-other-market", "", None])
def test_a_proposal_that_does_not_name_this_market_is_refused(ex, market):
    """The side check alone would not catch a proposal priced on one
    market's book reaching another's, and the mandate would then name an
    order in the wrong market. An absent market is a mismatch, not a
    wildcard."""
    x, t, E = ex
    hold(x)
    out = run(x, prop=proposal(market=market))
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_PROPOSAL_MARKET_MISMATCH"
    assert x._defensive_mandate is None


def test_the_runner_stamps_the_market_it_priced_on():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.submit_ts_completion)
    assert "market=str(slug)," in src


@pytest.mark.parametrize("bad,reason", [
    ({"qty": "0"}, "COMPLETION_PROPOSAL_QTY_UNREADABLE"),
    ({"qty": None}, "COMPLETION_PROPOSAL_QTY_UNREADABLE"),
    ({"price": "0"}, "COMPLETION_PROPOSAL_PRICE_UNREADABLE"),
    ({"price": "1.5"}, "COMPLETION_PROPOSAL_PRICE_UNREADABLE"),
    ({"price": "nope"}, "COMPLETION_PROPOSAL_PRICE_UNREADABLE"),
])
def test_an_unreadable_proposal_field_refuses_with_its_own_name(ex, bad,
                                                                reason):
    x, t, E = ex
    hold(x)
    assert run(x, prop=proposal(**bad))["reason"] == reason


def test_a_deficit_below_the_increment_is_refused_not_rounded_up(ex):
    x, t, E = ex
    hold(x, up="1.005", dn="1")
    out = run(x, prop=proposal(qty="1"))
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_BELOW_MIN_INCREMENT"


def test_an_unknown_quantity_increment_refuses(ex):
    x, t, E = ex
    hold(x)
    x.transport.quantity_increment = None
    assert run(x)["reason"] == "COMPLETION_QUANTITY_INCREMENT_UNKNOWN"


# =====================================================================
# 5. THE VENUE'S EXPOSURE, NOT OURS.
# =====================================================================

def test_a_venue_that_disagrees_with_the_ledger_refuses(ex):
    x, t, E = ex
    hold(x, up="2", dn="1")
    x.transport.venue_positions = venue_rows("5")      # venue says 5, we say 1
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_VENUE_POSITION_DISAGREES"
    assert out["residual_local"] == "1" and out["residual_venue"] == "5"
    assert x._defensive_mandate is None


def test_a_venue_reporting_no_position_refuses(ex):
    x, t, E = ex
    hold(x)
    x.transport.venue_positions = []
    assert run(x)["reason"] == "COMPLETION_VENUE_POSITION_ABSENT"


def test_an_unreadable_positions_endpoint_refuses(ex):
    x, t, E = ex
    hold(x)

    def boom():
        raise RuntimeError("venue 503")
    x.transport.positions = boom
    out = run(x)
    assert out["reason"] == "COMPLETION_VENUE_POSITIONS_UNREADABLE"
    assert "RuntimeError" in out["error"]


# =====================================================================
# 6. DUPLICATE PREVENTION: UNCERTAIN ACKS, RESTARTS, SECOND TICKS.
# =====================================================================

def test_an_uncertain_ledger_refuses_to_spend(ex):
    x, t, E = ex
    hold(x)
    x.store.mark_uncertain("one terminal order is not yet booked")
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_ORDERS_UNCERTAIN"
    assert x._defensive_mandate is None


def test_a_second_attempt_in_the_same_window_is_refused(ex):
    x, t, E = ex
    hold(x)
    assert run(x)["ok"] is True
    second = run(x)
    assert second["ok"] is False
    assert second["reason"] == "COMPLETION_ALREADY_MANDATED"


def test_the_claim_is_durable_so_a_restart_cannot_cross_twice(ex, tmp_path):
    """THE UNCERTAIN-ACK CASE, WHICH IS THE ONE THAT COSTS MONEY. The claim
    is written BEFORE the wire and is on the volume, so a process that dies
    between the claim and the venue comes back already holding it."""
    x, t, E = ex
    hold(x)
    first = run(x)
    assert first["ok"] is True
    assert first["claim"]["window_start"] == str(WS)

    # a fresh process, same volume: a new executor with no in-process mandate
    import tests.test_l5_live_execution as H
    fresh = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                               transport=t, now_fn=H.NOW, maker_only=True)
    fresh.ts_book = x.ts_book
    assert fresh._defensive_mandate is None
    out = fresh.completion_taker_execute(
        market=SLUG, buy_side="DOWN", held_side="UP", resting=[],
        proposal=proposal(), window_start=WS, now_t=NOW_TS, prov=None)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_ALREADY_CLAIMED"
    assert fresh._defensive_mandate is None


def test_the_claim_is_per_window_so_the_next_window_may_complete(ex,
                                                                 tmp_path):
    x, t, E = ex
    hold(x)
    assert run(x)["ok"] is True
    import tests.test_l5_live_execution as H
    fresh = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                               transport=t, now_fn=H.NOW, maker_only=True)
    fresh.ts_book = x.ts_book
    out = fresh.completion_taker_execute(
        market=SLUG, buy_side="DOWN", held_side="UP", resting=[],
        proposal=proposal(), window_start=WS + 900.0, now_t=NOW_TS,
        prov=None)
    assert out["ok"] is True


def test_the_claim_records_what_was_claimed(ex):
    x, t, E = ex
    hold(x)
    run(x)
    c = x.store.state["ts_completion_claims"]["%s|DOWN" % SLUG]
    assert c["qty"] == "1" and c["price"] == "0.3500"
    assert c["origin"] == TCP.MANDATE_ORIGIN
    assert "never released by a retry" in c["labels"]


# =====================================================================
# 7. THE BUDGET, CHARGED BEFORE THE WIRE.
# =====================================================================

def test_the_budget_is_charged_before_the_mandate_and_survives_a_restart(
        ex, tmp_path):
    x, t, E = ex
    hold(x)
    before = x.store.defensive_spent()
    out = run(x)
    assert out["ok"] is True
    after = x.store.defensive_spent()
    assert after > before
    # charged at the WORST case: price * qty plus the taker fee
    expected = D("0.3500") + TS.worst_fee(D(1), D("0.3500"))
    assert after - before == expected
    import tests.test_l5_live_execution as H
    assert H.store(tmp_path).defensive_spent() == after


def test_the_charge_names_the_taker_completion_in_the_log(ex):
    x, t, E = ex
    hold(x)
    run(x)
    log = x.store.state["defensive_log"]
    assert log[-1]["reason"] == "TAKER_COMPLETION"
    assert log[-1]["origin"] == TCP.MANDATE_ORIGIN


def test_a_budget_that_refuses_the_charge_issues_no_mandate(ex, monkeypatch):
    x, t, E = ex
    hold(x)
    monkeypatch.setattr(
        type(x.store), "defensive_charge",
        lambda self, usd, detail: (_ for _ in ()).throw(
            ValueError("DEFENSIVE_BUDGET_EXHAUSTED")))
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_BUDGET_NOT_CHARGEABLE"
    assert x._defensive_mandate is None
    assert "ts_completion_claims" not in x.store.state or not x.store.state[
        "ts_completion_claims"]


def test_the_two_routes_share_one_budget_and_one_mandate_slot(ex,
                                                              monkeypatch):
    """One crossing order per market per window stays ONE invariant: a
    defensive unwind cannot follow a taker completion in the same window."""
    x, t, E = ex
    monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, "1")
    hold(x)
    assert run(x)["ok"] is True
    out = x.defensive_execute(
        market=SLUG, buy_side="DOWN", held_side="UP", resting=[],
        decide_kwargs={"now_t": NOW_TS, "window_start": WS}, prov=None)
    assert out["ok"] is False
    assert out["reason"] == "DEFENSIVE_ALREADY_MANDATED"


def test_a_mandate_with_an_unreadable_expiry_blocks_rather_than_passes(ex):
    x, t, E = ex
    hold(x)
    x._defensive_mandate = {"market": SLUG, "side": "DOWN",
                            "expires_at": "not-a-time"}
    assert run(x)["reason"] == "COMPLETION_ALREADY_MANDATED"


def test_a_mandate_for_another_market_does_not_block_this_one(ex):
    x, t, E = ex
    hold(x)
    x._defensive_mandate = {"market": "some-other-market", "side": "DOWN",
                            "expires_at": NOW_TS + 600.0}
    assert run(x)["ok"] is True


def test_the_mandate_expiry_is_bounded_by_the_window(ex):
    x, t, E = ex
    hold(x)
    out = run(x)
    assert float(out["mandate"]["expires_at"]) <= WS + 900.0
    assert float(out["mandate"]["expires_at"]) > NOW_TS


def test_a_proposal_with_no_expiry_still_gets_a_bounded_one(ex):
    x, t, E = ex
    hold(x)
    p = proposal()
    p.pop("expires_at")
    out = run(x, prop=p)
    assert out["ok"] is True
    assert float(out["mandate"]["expires_at"]) > float(NOW_TS)


# =====================================================================
# 8. THE RUNNER'S WIRING: `WOULD_SUBMIT` IS GONE AND A ROUTE IS THERE.
# =====================================================================

def test_the_would_submit_placeholder_is_gone_from_the_runner():
    """CHECKED ON THE PARSED TREE, not on the text. The method's own comment
    quotes the placeholder it replaced -- that history is worth keeping --
    so a substring test would read the explanation as the defect."""
    import ast
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_ts_completion)
    tree = ast.parse(src.lstrip())
    strings = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "WOULD_SUBMIT" not in strings
    assert not any("deliberately not wired" in v for v in strings)
    # and the real route is called instead
    assert "self.submit_ts_completion(dec, src, pol, now)" in src


def test_the_submission_goes_through_the_existing_handle_re_entry():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.submit_ts_completion)
    assert "self.executor.handle(" in src
    assert "LX.CONTROLLER_GENERATED" in src
    assert '"relation": "complete"' in src
    # and it adds no transport of its own
    assert "PmusTransport" not in src
    assert ".submit(" not in src


def test_the_proposal_is_single_use_on_both_sides():
    """A refusal downstream must not leave a live proposal for the next
    tick to ride."""
    import inspect
    from src import l5_live_execution as E
    from src import l5_pmus_live_runner as RUN
    handle = inspect.getsource(E.FaithfulExecutor.handle)
    assert "self.taker_completion_proposal = None" in handle
    runner = inspect.getsource(RUN.Runner.submit_ts_completion)
    assert runner.count("self.executor.taker_completion_proposal = None") >= 2


def test_the_ranking_is_consulted_and_can_only_refuse():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.submit_ts_completion)
    assert "l5_inventory_decision as ID" in src
    assert "ID.TAKER_COMPLETE" in src and "ID.TAKER_PARTIAL" in src
    # the ranked size can only ever reduce the policy's
    assert "min(D(str(pol.get(\"qty\"))), D(str(chosen.get(\"qty\"))))" in src


def test_the_defensive_route_is_still_asked_first():
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E.FaithfulExecutor.handle)
    d = src.index("_dact = self.defensive_execute(")
    tcomp = src.index("_tact = self.completion_taker_execute(")
    assert d < tcomp
    assert "(_dact is None or not _dact.get(\"ok\")) and _tprop" in src


# =====================================================================
# 9. THE SHARED LOSS BUDGET IS TESTED, NOT MERELY CHARGED.
#
# THE GAP THIS PINS. `defensive_charge` ONLY ACCUMULATES -- it refuses
# nothing. The defensive path is bounded because `DEF.decide` tests
# `spent + exit_downside > loss_budget_usd` before `defensive_execute`
# charges. This method does not call `DEF.decide`, so charging without
# testing left the campaign-wide $2.00 budget unenforced on the completion
# route, and let a completion exhaust the budget the defensive unwind
# depends on -- the two share it on purpose.
# =====================================================================

def test_a_completion_is_refused_when_the_shared_budget_is_exhausted(ex):
    x, t, E = ex
    hold(x)
    x.store.state["defensive_spent_usd"] = str(DC.DEFENSIVE_LOSS_BUDGET_USD)
    x.store.save()
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_LOSS_BUDGET_EXHAUSTED"
    assert x._defensive_mandate is None
    assert "ts_completion_claims" not in x.store.state or not x.store.state[
        "ts_completion_claims"]


def test_the_budget_sizes_the_cross_down_instead_of_throwing_it_away(ex):
    """A flat refusal respected the budget and discarded the part of the
    rescue the budget could still afford. With one cent less than the full
    worst case left, the cross is cut to what fits -- and the spend still
    lands inside the bound."""
    x, t, E = ex
    hold(x, up="20", dn="0")             # a deficit big enough to cut
    worst = D("0.3500") + TS.worst_fee(D(1), D("0.3500"))
    x.store.state["defensive_spent_usd"] = str(
        DC.DEFENSIVE_LOSS_BUDGET_USD - worst + D("0.01"))
    x.store.save()
    out = run(x, prop=proposal(qty="20"))
    assert out["ok"] is True
    assert D(out["leg"]["qty"]) < D("20")
    assert x.store.defensive_spent() <= DC.DEFENSIVE_LOSS_BUDGET_USD


def test_a_budget_with_nothing_left_still_refuses_outright(ex):
    x, t, E = ex
    hold(x)
    x.store.state["defensive_spent_usd"] = str(DC.DEFENSIVE_LOSS_BUDGET_USD)
    x.store.save()
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_LOSS_BUDGET_EXHAUSTED"


def test_a_resize_must_re_earn_the_improvement_floor(ex):
    """A smaller cross removes less exposure and may no longer pay for its
    own fee. The floor is tested AGAIN at the affordable size."""
    x, t, E = ex
    hold(x, up="20", dn="0")
    x.store.state["defensive_spent_usd"] = str(
        DC.DEFENSIVE_LOSS_BUDGET_USD - D("0.40"))
    x.store.save()
    # a floor the FULL size clears (~$12.48 of improvement on 20 contracts)
    # but the affordable ~1.1 contracts does not (~$0.76), so the refusal
    # under test is the RESIZE's own re-check and not the earlier one
    out = run(x, prop=proposal(qty="20",
                               min_worst_improvement_usd="5.00"))
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_BUDGET_RESIZE_BELOW_FLOOR"
    assert D(out["affordable_qty"]) < D("20")
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == (
        DC.DEFENSIVE_LOSS_BUDGET_USD - D("0.40")), (
        "a refused resize must not charge the budget")


def test_exactly_enough_budget_is_allowed(ex):
    x, t, E = ex
    hold(x)
    worst = D("0.3500") + TS.worst_fee(D(1), D("0.3500"))
    x.store.state["defensive_spent_usd"] = str(
        DC.DEFENSIVE_LOSS_BUDGET_USD - worst)
    x.store.save()
    assert run(x)["ok"] is True


def test_an_unreadable_budget_reads_as_exhausted(ex):
    """A budget we cannot read is spent, which `defensive_spent` already
    delivers by returning the whole budget when the read raises."""
    x, t, E = ex
    hold(x)
    x.store.state["defensive_spent_usd"] = "not-a-number"
    x.store.save()
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_LOSS_BUDGET_EXHAUSTED"


def test_a_completion_cannot_exhaust_the_budget_past_the_cap(ex):
    """The two routes share one budget on purpose, so a completion must not
    be able to spend past the bound the defensive unwind relies on."""
    x, t, E = ex
    hold(x)
    assert run(x)["ok"] is True
    assert x.store.defensive_spent() <= DC.DEFENSIVE_LOSS_BUDGET_USD


def test_the_budget_check_is_the_same_bound_DEF_uses():
    """One bound, one constant, read from the same place by both routes."""
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E.FaithfulExecutor.completion_taker_execute)
    assert "DEF.DEFENSIVE_LOSS_BUDGET_USD" in src
    assert "self.store.defensive_spent()" in src
    # tested BEFORE it is charged
    assert src.index("COMPLETION_LOSS_BUDGET_EXHAUSTED") < src.index(
        "defensive_charge(")


# =====================================================================
# 10. THE DURABLE CLAIM LEDGER IS BOUNDED.
#
# One entry per (market, side) with a 15-minute window is 192 a day and
# nothing ever removed them, in the one file the loss halt is measured
# from and which is rewritten whole on every save.
# =====================================================================

def test_the_claim_ledger_does_not_grow_without_bound(ex):
    from src import l5_live_execution as E
    x, t, E2 = ex
    claims = x.store.state.setdefault("ts_completion_claims", {})
    for i in range(E.CLAIM_RETENTION + 50):
        claims["market-%d|DOWN" % i] = {
            "market": "market-%d" % i, "side": "DOWN",
            "claimed_at": str(float(i))}
    hold(x)
    assert run(x)["ok"] is True
    kept = x.store.state["ts_completion_claims"]
    assert len(kept) <= E.CLAIM_RETENTION + 1
    # the row just written is never the one evicted
    assert "%s|DOWN" % SLUG in kept
    # and the OLDEST are the ones gone
    assert "market-0|DOWN" not in kept


def test_eviction_survives_an_unreadable_claim_time(ex):
    from src import l5_live_execution as E
    x, t, E2 = ex
    claims = x.store.state.setdefault("ts_completion_claims", {})
    for i in range(E.CLAIM_RETENTION + 10):
        claims["junk-%d|DOWN" % i] = {"claimed_at": None}
    hold(x)
    assert run(x)["ok"] is True
    assert "%s|DOWN" % SLUG in x.store.state["ts_completion_claims"]


def test_the_retention_bound_is_far_longer_than_a_window(ex):
    """A claim only has to outlive its own 15-minute window."""
    from src import l5_live_execution as E
    assert E.CLAIM_RETENTION >= 64


# =====================================================================
# 11. ADVERSARIAL: NOTHING SHORT OF FULL OWNER AUTHORIZATION SUBMITS.
# =====================================================================

AUTH_KEYS = ("policy", "owner_cross", "authorized_proposal")


@pytest.mark.parametrize("policy", [True, False])
@pytest.mark.parametrize("owner_cross", [True, False])
@pytest.mark.parametrize("authorized_proposal", [True, False])
def test_no_partial_authorization_can_reach_a_mandate(
        ex, monkeypatch, policy, owner_cross, authorized_proposal):
    """EVERY COMBINATION. A mandate is the only thing that exempts an order
    from the maker-only overlay, so "no mandate" is "no crossing order".
    Only all three together may produce one."""
    x, t, E = ex
    hold(x)
    if policy:
        monkeypatch.setenv(TCP.POLICY_ENV, "YES")
    else:
        monkeypatch.delenv(TCP.POLICY_ENV, raising=False)
    if owner_cross:
        monkeypatch.setenv(TCP.OWNER_CROSS_ENV, "YES")
    else:
        monkeypatch.delenv(TCP.OWNER_CROSS_ENV, raising=False)
    prop = proposal() if authorized_proposal else proposal(
        authorized=False, reason="MARGIN_BELOW_FLOOR")
    out = run(x, prop=prop)
    expected = policy and owner_cross and authorized_proposal
    assert bool(out.get("ok")) is expected, out.get("reason")
    assert (x._defensive_mandate is not None) is expected


def test_a_mandate_stops_covering_the_instant_a_switch_goes_off(
        ex, monkeypatch):
    """Even an ALREADY ISSUED mandate must stop exempting anything if the
    authorization is withdrawn -- it is consulted per order, not cached."""
    x, t, E = ex
    hold(x)
    assert run(x)["ok"] is True
    ok = (SLUG, {"side": "DOWN"}, {"price": "0.3500", "qty": "1"})
    assert x.defensive_mandate_covers(*ok)
    for env in (TCP.POLICY_ENV, TCP.OWNER_CROSS_ENV):
        monkeypatch.delenv(env, raising=False)
        assert x.defensive_mandate_covers(*ok) is None
        monkeypatch.setenv(env, "YES")
    assert x.defensive_mandate_covers(*ok)


def test_the_maker_only_overlay_refuses_a_taker_with_no_mandate(ex):
    """The overlay is what actually stops the order. With no mandate a TAKER
    intent is refused MAKER_ONLY_NO_TAKER regardless of anything else."""
    x, t, E = ex
    x._defensive_mandate = None
    assert x.maker_only is True
    assert x.defensive_mandate_covers(
        SLUG, {"side": "DOWN"}, {"price": "0.3500", "qty": "1"}) is None


def test_the_runner_never_calls_submit_when_the_policy_withholds():
    """`submit_ts_completion` is reached only inside the branch that
    requires dec.ok AND the owner cross flag AND armed AND the policy's own
    authorization -- checked on the parsed tree, not on prose."""
    import ast
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_ts_completion)
    tree = ast.parse(src.lstrip())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        for stmt in ast.walk(node):
            if (isinstance(stmt, ast.Call)
                    and isinstance(stmt.func, ast.Attribute)
                    and stmt.func.attr == "submit_ts_completion"):
                found.append(test)
    assert found, "submit_ts_completion is not called at all"
    for test in found:
        for required in ("dec.get('ok')", "authorized", "t.armed",
                         "pol.get('authorized')"):
            assert required in test, (required, test)


def test_the_shadow_only_transport_blocks_submit_whatever_is_mandated():
    """The structural lock sits in FRONT of every authorization, so a
    mandate cannot reach the wire through it."""
    from src import l5_pmus_live_runner as RUN
    assert "submit" in RUN.ShadowOnlyTransport.BLOCKED

    class Inner:
        armed = True
        def submit(self, intent):
            raise AssertionError("the lock must not delegate submit")

    locked = RUN.ShadowOnlyTransport(Inner())
    with pytest.raises(Exception) as got:
        locked.submit({"side": "DOWN"})
    assert "SHADOW_ONLY" in str(got.value)


# =====================================================================
# 12. EXACT-ORDER CANCELLATION.
# =====================================================================

def test_only_the_named_orders_are_cancelled(ex, monkeypatch):
    """`deadman` is given `only=` the exact client order ids, never the
    market. The REAL deadman still runs -- this only observes what it was
    asked to cancel -- so the confirmation path is the real one."""
    import src.l5_live_execution as LXM
    seen = {}
    real = LXM.deadman

    def spy(store, transport, *, reason, only=None, **kw):
        seen["reason"] = reason
        seen["only"] = list(only or [])
        return real(store, transport, reason=reason, only=only, **kw)

    x, t, E = ex
    hold(x)
    rest(x, coid=COID, vid="v1")
    rest(x, coid="untouched-order", side="UP", vid="v9")
    monkeypatch.setattr(LXM, "deadman", spy)
    out = run(x, resting=[COID])
    assert out["ok"] is True
    assert seen["only"] == [COID]
    assert seen["reason"] == "TAKER_COMPLETION"
    # the order this route was not asked to cancel is still on the book
    assert "untouched-order" in x.store.live_orders()


def test_every_named_order_must_be_confirmed_not_just_the_first(ex,
                                                                monkeypatch):
    x, t, E = ex
    hold(x)
    rest(x, coid="a", vid="va")
    rest(x, coid="b", vid="vb")
    monkeypatch.setattr("src.l5_live_execution.deadman",
                        lambda *a, **k: {"cancelled": ["a"]})
    monkeypatch.setattr(
        "src.l5_defensive_completion.cancel_confirmed",
        lambda res, coid: {"confirmed": coid in (res.get("cancelled") or [])})
    out = run(x, resting=["a", "b"])
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_CANCEL_NOT_CONFIRMED"
    assert x._defensive_mandate is None


def test_the_cancel_is_recorded_before_the_verdict_that_could_submit(ex):
    """So a cancellation stands in the record even if everything after it
    refuses or raises."""
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.completion_taker_execute)
    assert src.index("TAKER_COMPLETION_CANCEL") < src.index(
        "_defensive_mandate = {")


# =====================================================================
# 13. MAKER-FIRST PROTECTS A FRESH HEDGE FROM BEING CANCELLED.
#
# This route CANCELS the resting complement and crosses. What stops it
# doing that to a quote that has barely been posted is the policy's
# maker-patience gate, which must run BEFORE the proposal exists.
# =====================================================================

def _policy(**kw):
    """A PROFIT-mode completion, deliberately.

    The real 0300z price of 0.3500 makes the CONSERVATIVE worst outcome
    marginally negative once both legs are charged a taker fee -- which is
    the whole finding that those windows were rescues and not profit
    completions, and it means 0.3500 cannot be used to isolate a later gate.
    0.2000 is a genuine profit completion, so these tests exercise the gate
    each one names rather than tripping on positivity first.
    """
    dec = {"ok": True, "side": "DOWN", "qty": "1", "price": "0.2000",
           "deficit": "1", "displayed_depth": "1312.39",
           "both_positive_after": True,
           "worst_before": "-0.4000", "worst_after": "0.3900"}
    base = dict(decision=dec, enabled=True, owner_authorized=True,
                # ONE unmatched leg and no existing complement, so the
                # conservative re-pricing is not blended down by a dearer
                # leg already on the books and clears the +$0.25 floor.
                held_up_qty=D(1), held_dn_qty=D(0),
                held_up_px=D("0.38"), held_dn_px=D(0),
                maker_attempted=True, maker_rested_s=D(120),
                # deep enough that the CONSERVATIVE re-pricing at the 0.25
                # haircut still clears one contract, so these tests exercise
                # the gate they name rather than the depth check.
                book_rungs=[["0.2000", "1312.39"]],
                window_outlay_spent_usd=D(0), halted=False)
    base.update(kw)
    return TCP.authorize(**base)


def test_a_hedge_that_has_barely_rested_is_not_crossed_over(ex):
    out = _policy(maker_rested_s=D(5))
    assert out["authorized"] is False
    assert out["reason"] == "MAKER_PATIENCE_NOT_SPENT"


def test_a_hedge_that_was_never_posted_is_not_crossed_over(ex):
    assert _policy(maker_attempted=False)["reason"] == "MAKER_NOT_ATTEMPTED"


def test_an_unknown_maker_history_fails_closed(ex):
    assert _policy(maker_attempted=None)["reason"] == "MAKER_HISTORY_UNKNOWN"
    assert _policy(maker_rested_s=None)["reason"] == "MAKER_RESTED_UNKNOWN"


def test_a_halt_outranks_the_opportunity(ex):
    assert _policy(halted=True)["reason"] == "HALTED"
    assert _policy(halted=None)["reason"] == "HALT_STATE_UNKNOWN"


def test_the_runner_reads_the_same_halt_the_money_path_reads():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.taker_policy_halted)
    assert "self.halted()" in src
    assert "day_realized_line()" in src
    assert "would_halt" in src


def test_the_window_outlay_cap_binds_across_repeated_crosses(ex):
    assert _policy(window_outlay_spent_usd=D("99"))["reason"] == (
        "WINDOW_OUTLAY_EXHAUSTED")
    assert _policy(window_outlay_spent_usd=None)["reason"] == (
        "WINDOW_OUTLAY_SPENT_UNKNOWN")


def test_the_runner_records_the_window_outlay_so_the_cap_reads_a_real_number():
    """It read a structural zero while nothing crossed; now a submitted
    completion charges it at the worst case."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.submit_ts_completion)
    assert "_taker_outlay_usd" in src
    assert "TSP.worst_fee(qty, px)" in src
    accessor = inspect.getsource(RUN.Runner.taker_outlay_spent)
    assert "_taker_outlay_usd" in accessor


# =====================================================================
# 14. WHAT THE MANDATE ACTUALLY WIDENS, STATED RATHER THAN INCIDENTAL.
#
# Found by auditing `pair_cost_refusal` against my own change. A mandate
# does two things beyond exempting the maker-only overlay, and the
# mandate's own label did not spell either out:
#
#   cap = DEF.DEFENSIVE_PAIR_CEILING if _mand else TS.max_pair_cost()
#
# so a TAKER_COMPLETION mandate raises the pair ceiling from 0.96 to 1.20
# for that one order -- ABOVE the $1.00 certain-loss line -- and the three
# `completion_pair_rescue` conditions are SKIPPED for a mandated order
# (`if not _mand: ... else: _resc = None`).
#
# That is the intended shape for a rescue: 0.96 is an ENTRY ceiling for
# spread capture, and a leg that would otherwise settle worthless is the
# one case where paying above $1.00 is the SMALLER loss. The campaign's own
# completed pair cost 0.97 and its commit called it "a RESCUE, not spread
# capture". What bounds it is not a pair-cost line but the improvement
# floor and the outlay caps -- and `completion_taker_execute` enforces the
# two directional conditions itself, which is why skipping them there is
# safe. These tests pin all of that.
# =====================================================================

def _pair_args(px="0.99", qty="1"):
    return (SLUG, {"side": "DOWN"}, {"price": px, "qty": qty})


def test_without_a_mandate_the_pair_ceiling_is_the_entry_ceiling(ex):
    x, t, E = ex
    assert x._defensive_mandate is None
    assert TS.max_pair_cost() <= D("0.96")


def test_a_taker_completion_mandate_raises_the_pair_ceiling_to_the_defensive_one(
        ex):
    """Asserted on the source of the one line that chooses the cap, because
    driving `pair_cost_refusal` needs a full attested ledger and the
    property under test is which constant is selected."""
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.pair_cost_refusal)
    assert ("cap = (DEF.DEFENSIVE_PAIR_CEILING if _mand "
            "else TS.max_pair_cost())") in src
    assert DC.DEFENSIVE_PAIR_CEILING == D("1.20")
    assert DC.DEFENSIVE_PAIR_CEILING > D("1.00") > TS.max_pair_cost()


def test_the_rescue_conditions_are_skipped_for_a_mandated_order(ex):
    """So the mandate HOLDER must enforce them -- which is why the two
    directional ones are enforced inside completion_taker_execute."""
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.pair_cost_refusal)
    assert "if not _mand:" in src
    assert "_resc = None" in src


def test_completion_taker_execute_enforces_both_directional_conditions(ex):
    """REDUCES and NO-REVERSAL, on the store's own venue-confirmed
    positions, so a mandated order can neither increase directional
    exposure nor cross through flat."""
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.completion_taker_execute)
    assert "COMPLETION_WOULD_INCREASE_EXPOSURE" in src
    assert "COMPLETION_WOULD_REVERSE_EXPOSURE" in src
    # and against the VENUE's exposure, not only ours
    assert "COMPLETION_VENUE_POSITION_DISAGREES" in src


def test_what_bounds_a_dear_rescue_is_the_improvement_floor_not_the_ceiling():
    """A pair at 1.20 is a 20-cent-a-share certain loss. The thing that
    stops it being taken for no reason is the improvement floor: the cross
    must remove at least that much worst-case exposure."""
    dec = {"ok": True, "side": "DOWN", "qty": "1", "price": "0.9500",
           "deficit": "1", "displayed_depth": "809",
           "book_rungs": [["0.9500", "809"]],
           "both_positive_after": False,
           "worst_before": "-0.4000", "worst_after": "-0.3600"}
    common = dict(decision=dec, enabled=True, owner_authorized=True,
                  held_up_qty=D(1), held_dn_qty=D(0),
                  held_up_px=D("0.38"), held_dn_px=D(0),
                  book_rungs=[["0.9500", "809"]],
                  maker_attempted=True, maker_rested_s=D(120),
                  window_outlay_spent_usd=D(0), halted=False,
                  loss_reduction=True)
    # a four-cent improvement does not clear a 25-cent floor
    out = TCP.authorize(**dict(common,
                               min_worst_improvement_usd=D("0.25")))
    assert out["authorized"] is False
    assert out["reason"].startswith("RESCUE_")
    # and the floor must be NAMED at all
    assert TCP.authorize(**common)["reason"] == (
        "RESCUE_IMPROVEMENT_FLOOR_UNSET")


# =====================================================================
# 15. THE RANKER'S VERDICT ACTUALLY BLOCKS THE SUBMISSION.
#
# `submit_ts_completion` consults `l5_inventory_decision` on top of the two
# gates that already said yes. A source scan proves it is CALLED; this
# proves it DECIDES -- that a HOLD verdict means `executor.handle` is
# never reached, and a taker verdict means it is.
# =====================================================================

class _SubStore:
    def __init__(self, up="2", dn="1"):
        self.state = {"submitted": {}}
        self._up, self._dn = D(up), D(dn)
        #: the LIFETIME completion budget. Production debits this inside
        #: `completion_taker_execute` and nowhere else, which is what makes
        #: a debit the signature of a real bounded taker cross.
        self._defensive_spent = D(0)

    def defensive_spent(self):
        return self._defensive_spent

    def defensive_charge(self, amount, _rec=None):
        self._defensive_spent += D(str(amount))
        return self._defensive_spent

    def live_orders(self):
        return {}

    def position(self, _m, side):
        return self._up if side == "UP" else self._dn

    def leg_cost(self, _m):
        return {"UP": self._up * D("0.62"), "DOWN": self._dn * D("0.35")}


class _SubExec:
    """Stands in for the real executor.

    `crosses` mirrors what `completion_taker_execute` does on a bounded
    taker cross: it DEBITS the lifetime budget. With `crosses=False` it
    behaves like a controller re-entry that posted a maker quote under
    `execution_mode: MAKER_ONLY` -- submitted, no debit -- which is the
    2026-11-06 20:15z case that was misreported as a cross.
    """

    def __init__(self, crosses=True, debit="0.45"):
        self.calls = []
        self.taker_completion_proposal = None
        self.validation_gates = None
        self.crosses = bool(crosses)
        self.debit = D(str(debit))
        self.store = None

    def handle(self, *a, **kw):
        self.calls.append(kw)
        if self.crosses and self.store is not None:
            self.store.defensive_charge(self.debit)
        return {"submitted": True, "coid": "l5f-x"}


class _SubBook:
    def __init__(self, rungs):
        self._rungs = rungs

    def snapshot(self, _slug):
        return {"bid": "0.6700", "ask": "0.6800", "bid_shares": "448",
                "ask_shares": "120.84", "stream_age_s": "0.05"}


class _SubTransport:
    market_slug = SLUG
    tick_size = D("0.01")
    quantity_increment = D("0.01")
    armed = True


class _SubRunner:
    submit_ts_completion = RUN.Runner.submit_ts_completion
    maker_completion_history = RUN.Runner.maker_completion_history
    _taker_policy_number = RUN.Runner._taker_policy_number
    # a staticmethod on the real Runner; rebinding it as a plain
    # attribute would make it an instance method and lose the first arg
    _bc_capture = staticmethod(RUN.Runner._bc_capture)
    # the five-stage classifier and the budget readers it compares across
    completion_stages = RUN.Runner.completion_stages
    defensive_spent_usd = RUN.Runner.defensive_spent_usd
    COMPLETION_STAGES = RUN.Runner.COMPLETION_STAGES

    def __init__(self, rungs):
        import collections
        self.transport = _SubTransport()
        self.store = _SubStore()
        self.book = _SubBook(rungs)
        self.executor = _SubExec()
        self.executor.store = self.store
        self.stats = collections.Counter()
        self._rungs = rungs


def _src(rungs, **kw):
    base = {
        "ok": True, "side": "DOWN", "up_qty": D(2), "dn_qty": D(1),
        "up_cost_px": D("0.62"), "dn_cost_px": D("0.35"),
        "min_qty": D("0.01"), "tick": D("0.01"),
        "book_rungs": rungs, "maker_own_px": None,
        "maker_queue_ahead": D("120.84"), "mid_wire": D("0.675"),
        "cash_headroom": D("10.41"), "worst_case_ceiling": D("1.50"),
    }
    base.update(kw)
    return base


def _dec(price, **kw):
    base = {"ok": True, "side": "DOWN", "qty": "1", "price": price,
            "deficit": "1", "displayed_depth": "448"}
    base.update(kw)
    return base


def _pol(price, qty="1"):
    return {"ok": True, "authorized": True, "reason": "AUTHORIZED",
            "side": "DOWN", "qty": qty, "price": price}


def test_a_taker_verdict_reaches_the_existing_handle_re_entry(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs), _pol("0.3300"),
                                 1793934115.0)
    assert out["crossed"] is True
    assert len(r.executor.calls) == 1
    ctl = r.executor.calls[0]["controller"]
    assert ctl["decision"]["relation"] == "complete"
    assert ctl["decision"]["side"] == "DOWN"
    assert out["inventory_decision"]["chosen"] in ("TAKER_COMPLETE",
                                                   "TAKER_PARTIAL")


def test_a_hold_verdict_never_reaches_handle(monkeypatch):
    """A complement so dear that nothing improves the worse outcome. The
    two upstream gates said yes; the ranker is what stops it."""
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.9900", "448"]]
    r = _SubRunner(rungs)
    out = r.submit_ts_completion(_dec("0.9900"), _src(rungs), _pol("0.9900"),
                                 1793934115.0)
    assert out["crossed"] is False
    assert out["withheld_by"].startswith("RANKED_HOLD")
    assert r.executor.calls == []
    assert r.executor.taker_completion_proposal is None


def test_a_stale_book_never_reaches_handle(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)
    r.book.snapshot = lambda _s: {"bid": "0.67", "ask": "0.68",
                                  "stream_age_s": "900"}
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs), _pol("0.3300"),
                                 1793934115.0)
    assert out["crossed"] is False
    assert r.executor.calls == []


def test_the_order_size_is_the_smaller_of_the_two_independent_sizings(
        monkeypatch):
    """The policy's conservative quantity and the ranker's own size are
    both computed; the order is the smaller, which can only reduce it."""
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)
    r.submit_ts_completion(_dec("0.3300", qty="50"), _src(rungs),
                           _pol("0.3300", qty="50"), 1793934115.0)
    sent = D(r.executor.calls[0]["controller"]["decision"]["l5_size"])
    assert sent <= D("1"), sent          # the deficit, not the authorization


def test_an_unreadable_ledger_never_reaches_handle(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)

    def boom(*a, **k):
        raise RuntimeError("ledger unreadable")
    r.store.position = boom
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs), _pol("0.3300"),
                                 1793934115.0)
    assert out["crossed"] is False
    assert out["withheld_by"] == "LEDGER_UNREADABLE"
    assert r.executor.calls == []


def test_the_proposal_is_cleared_even_when_handle_raises(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)

    def boom(*a, **k):
        raise RuntimeError("venue 500")
    r.executor.handle = boom
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs), _pol("0.3300"),
                                 1793934115.0)
    assert out["crossed"] is False
    assert out["withheld_by"].startswith("HANDLE_RAISED")
    assert r.executor.taker_completion_proposal is None


def test_the_window_outlay_is_charged_only_on_a_submission(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)
    r.executor.handle = lambda *a, **k: {"submitted": False,
                                         "refused": "MAX_LIVE_ORDERS"}
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs), _pol("0.3300"),
                                 1793934115.0)
    assert out["crossed"] is False
    assert getattr(r, "_taker_outlay_usd", None) is None
    r2 = _SubRunner(rungs)
    r2.submit_ts_completion(_dec("0.3300"), _src(rungs), _pol("0.3300"),
                            1793934115.0)
    assert D(str(r2._taker_outlay_usd)) > 0


# =====================================================================
# 16. THE $2.00 BUDGET CANNOT BE EXCEEDED BY REPEATED COMPLETIONS,
#     ACROSS WINDOWS, ACROSS MARKETS, OR ACROSS RESTARTS.
#
# `defensive_spent_usd` has exactly ONE write site --
# `InventoryStore.defensive_charge` -- it is monotone increasing, and
# nothing in src/ or scripts/ ever resets or clears it. It lives in the
# store on the mounted volume, so it is a LIFETIME bound rather than a
# per-campaign one. These tests hammer it.
# =====================================================================

def _hold_market(x, market, *, up="2", dn="1"):
    hold(x, up=up, dn=dn, market=market)


def test_repeated_completions_across_markets_stop_at_the_budget(ex):
    """Each attempt is charged at the worst case BEFORE the wire, so a
    sequence of them walks the budget down and then refuses -- it never
    walks through it."""
    x, t, E = ex
    granted, refused = 0, 0
    for i in range(40):
        market = "cpc-btc-updown-15m-2026-11-06-%04dz" % i
        _hold_market(x, market)
        x._defensive_mandate = None          # a fresh window, mandate expired
        out = x.completion_taker_execute(
            market=market, buy_side="DOWN", held_side="UP", resting=[],
            proposal=proposal(market=market), window_start=WS + i * 900.0,
            now_t=NOW_TS + i * 900.0, prov=None)
        if out.get("ok"):
            granted += 1
        else:
            refused += 1
            assert out["reason"] == "COMPLETION_LOSS_BUDGET_EXHAUSTED", (
                i, out["reason"])
    assert granted > 0, "the first attempts must be permitted"
    assert refused > 0, "the budget must eventually bind"
    assert x.store.defensive_spent() <= DC.DEFENSIVE_LOSS_BUDGET_USD


def test_the_budget_survives_a_restart_and_is_not_re_granted(ex, tmp_path):
    """The one failure that would matter most: a process that comes back
    believing it has a fresh $2.00."""
    import tests.test_l5_live_execution as H
    x, t, E = ex
    spent_before = None
    for i in range(40):
        market = "cpc-btc-updown-15m-2026-11-06-%04dz" % i
        # a FRESH executor every iteration, same volume -- i.e. a restart
        fresh = E.FaithfulExecutor(H.store(tmp_path),
                                   H.limits(max_live_orders=1),
                                   transport=t, now_fn=H.NOW, maker_only=True)
        fresh.ts_book = x.ts_book
        _hold_market(fresh, market)
        out = fresh.completion_taker_execute(
            market=market, buy_side="DOWN", held_side="UP", resting=[],
            proposal=proposal(market=market), window_start=WS + i * 900.0,
            now_t=NOW_TS + i * 900.0, prov=None)
        spent = fresh.store.defensive_spent()
        assert spent <= DC.DEFENSIVE_LOSS_BUDGET_USD, (i, str(spent))
        if spent_before is not None:
            assert spent >= spent_before, "a restart must not reduce spend"
        spent_before = spent
        if not out.get("ok"):
            assert out["reason"] == "COMPLETION_LOSS_BUDGET_EXHAUSTED"
    assert spent_before <= DC.DEFENSIVE_LOSS_BUDGET_USD
    assert spent_before > 0


def test_the_budget_has_exactly_one_write_site_and_is_monotone():
    """If a second writer appeared, or any reset, the bound above would be
    a statement about today's code and not about the invariant."""
    import pathlib
    import re
    hits = []
    for path in pathlib.Path("src").glob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r'\[["\']defensive_spent_usd["\']\]\s*=', line):
                hits.append("%s:%d" % (path.name, i))
    # the FILE and the COUNT, never the line number: pinning a line makes
    # this test fail on any edit above it, which says nothing about the
    # invariant it is here to protect.
    assert len(hits) == 1, hits
    assert hits[0].startswith("l5_live_execution.py:"), hits
    # and the charge only ever ADDS: a negative amount is floored at zero
    import inspect
    from src import l5_live_execution as E2
    body = inspect.getsource(E2.InventoryStore.defensive_charge)
    assert "if amt < 0:" in body and "amt = D(0)" in body
    assert "total = self.defensive_spent() + amt" in body


def test_an_unreadable_budget_is_the_whole_budget_not_zero():
    """So a corrupted ledger exhausts the budget rather than resetting it."""
    import inspect
    from src import l5_live_execution as E2
    body = inspect.getsource(E2.InventoryStore.defensive_spent)
    assert "return DEF.DEFENSIVE_LOSS_BUDGET_USD" in body


def test_one_mandate_per_market_per_window_bounds_attempts_per_window(ex):
    """Two completions cannot be mandated in one market in one window, so
    the per-window spend is bounded by ONE worst case."""
    x, t, E = ex
    hold(x)
    assert run(x)["ok"] is True
    spent_after_one = x.store.defensive_spent()
    second = run(x)
    assert second["ok"] is False
    assert second["reason"] == "COMPLETION_ALREADY_MANDATED"
    assert x.store.defensive_spent() == spent_after_one, (
        "a refused second attempt must not charge the budget")


# =====================================================================
# 17. THE 1.20 CEILING IS NOT PERMISSION. THE ECONOMICS ARE RE-EARNED
#     AFTER THE CANCEL, AGAINST THE BOOK AS IT IS THEN.
#
# `proposal` is TCP.authorize's verdict and it was computed BEFORE the
# cancel. Between then and the mandate there are venue round trips -- the
# cancel, its verification re-read, the positions read -- and the book
# moves. Nothing downstream would catch stale economics, because
# `pair_cost_refusal` SKIPS completion_pair_rescue for a mandated order
# precisely because the holder is supposed to have established this.
# =====================================================================

def test_a_price_that_moved_against_us_is_refused_not_chased(ex):
    """Chasing a rescue is how a rescue becomes the loss it was meant to
    avoid. Authorized at own 0.3500; the book now wants 0.4500."""
    x, t, E = ex
    hold(x)
    x.ts_book.snap = {"bid": "0.5500", "ask": "0.5600",
                      "stream_age_s": "0.05"}
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_PRICE_MOVED_AGAINST_US"
    assert D(out["executable_px"]) > D(out["authorized_px"])
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == 0


def test_a_price_that_moved_in_our_favour_still_pays_only_what_was_authorized(
        ex):
    x, t, E = ex
    hold(x)
    x.ts_book.snap = {"bid": "0.8000", "ask": "0.8100",
                      "stream_age_s": "0.05"}      # own DOWN now 0.20
    out = run(x)
    assert out["ok"] is True
    assert out["leg"]["price"] == "0.3500"          # never re-priced upward


def test_an_unreadable_book_at_execution_refuses(ex):
    x, t, E = ex
    hold(x)
    x.ts_book.snap = None
    out = run(x)
    assert out["reason"] == "COMPLETION_BOOK_UNREADABLE_AT_EXECUTION"
    assert x._defensive_mandate is None


def test_a_gapped_book_at_execution_refuses(ex):
    x, t, E = ex
    hold(x)
    x.ts_book.snap = {"bid": "0.6700", "ask": "0.6800",
                      "gap_suspected": True}
    assert run(x)["reason"] == "COMPLETION_BOOK_GAP_AT_EXECUTION"


@pytest.mark.parametrize("snap", [
    {"bid": "0.6700"},                              # one-sided
    {"ask": "0.6800"},
    {"bid": "0.6800", "ask": "0.6700"},              # crossed
    {"bid": "0", "ask": "0.6800"},
])
def test_a_book_that_is_not_two_sided_at_execution_refuses(ex, snap):
    x, t, E = ex
    hold(x)
    x.ts_book.snap = snap
    assert run(x)["reason"] == (
        "COMPLETION_BOOK_NOT_TWO_SIDED_AT_EXECUTION")


def test_a_completion_that_reduces_no_exposure_is_refused_at_execution(ex):
    """The exact thing the 1.20 ceiling must never be read as permitting.

    AND THE ARITHMETIC WORTH KNOWING. A matched pair caps the worse outcome
    at `1 - q - p - fees`, and holding leaves `-q - fee`, so the improvement
    is about `1 - p` less the extra fee. Measured on a 1 UP leg at own 0.05:

        complement own px   0.90   0.95   0.98   0.99
        improvement        0.09   0.04   0.01   0.00

    So worst-case improvement is positive for ALMOST ANY complement below
    ~0.99 -- which is exactly why the improvement floor and the EV give-up
    budget carry the weight here, and why "it improves the worse outcome"
    is a necessary and badly insufficient condition on its own. At 0.99 the
    improvement is zero and this refuses.
    """
    x, t, E = ex
    hold(x, up="1", dn="0", up_px="0.0500")
    x.ts_book.snap = {"bid": "0.0100", "ask": "0.0200",
                      "stream_age_s": "0.05"}       # own DOWN 0.99
    out = run(x, prop=proposal(price="0.9900"))
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_NO_RISK_REDUCTION_AT_EXECUTION"
    assert D(out["improvement_usd"]) <= 0
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == 0


def test_an_unattested_held_basis_is_refused_rather_than_priced_at_zero(ex):
    """`TS.top_up` reads a missing price as D(0) -- a FREE leg -- and a free
    leg makes every completion look like an improvement however dear."""
    x, t, E = ex
    hold(x)
    x.store.state["fill_count"] = {}          # quantity adopted, not filled
    x.store.save()
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_BASIS_UNATTESTED_AT_EXECUTION"
    assert x._defensive_mandate is None


def test_the_improvement_is_re_earned_at_the_clamped_quantity(ex):
    """The policy priced 1 contract; the post-cancel deficit is 0.4. The
    improvement is recomputed at 0.4, not inherited from the 1."""
    x, t, E = ex
    hold(x, up="2", dn="1.6")
    out = run(x, prop=proposal(qty="1",
                               min_worst_improvement_usd="0.01"))
    assert out["ok"] is True
    assert D(out["leg"]["qty"]) == D("0.40")


def test_an_execution_time_floor_breach_is_named_distinctly(ex):
    """So the tape can tell a rescue that never qualified from one whose
    economics evaporated during the cancel."""
    x, t, E = ex
    hold(x)
    out = run(x, prop=proposal(min_worst_improvement_usd="99.00"))
    assert out["reason"] == (
        "COMPLETION_IMPROVEMENT_BELOW_FLOOR_AT_EXECUTION")
    assert D(out["improvement_usd"]) > 0        # it DID reduce risk ...
    assert D(out["improvement_usd"]) < D("99.00")   # ... just not enough


def test_the_re_verification_runs_before_the_budget_is_charged(ex):
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.completion_taker_execute)
    assert src.index("COMPLETION_NO_RISK_REDUCTION_AT_EXECUTION") < src.index(
        "defensive_charge(")
    assert src.index("COMPLETION_PRICE_MOVED_AGAINST_US") < src.index(
        "_defensive_mandate = {")


def test_the_runner_carries_the_floor_into_the_proposal():
    import inspect
    from src import l5_pmus_live_runner as RUN2
    src = inspect.getsource(RUN2.Runner.submit_ts_completion)
    assert "min_worst_improvement_usd" in src
    assert "TCP.MIN_WORST_IMPROVEMENT_ENV" in src


# =====================================================================
# 12. THE REAL FILL PATH, END TO END INTO THE EXECUTION CHECK.
#
# Every test above sets `fill_count` by hand, which proves the CHECK but
# not that the thing it checks is what a genuine fill produces. The
# ledger key is built in exactly one place on the fill path -- inside
# `apply_fill`, which normalises both the side and the price convention
# -- so if that key disagreed with the one `cost_attested` reads, step 8b
# would refuse a real maker fill with COMPLETION_BASIS_UNATTESTED_AT_
# EXECUTION and the Phase B window would be spent discovering it.
#
# The live reconciler currently lists 54 legs under
# `leg_cost_basis_unknown`, so this is not hypothetical: unattested legs
# exist on the volume right now. These tests pin that they are the
# reconcile-written ones and that a fill is not among them.
# =====================================================================

def test_a_fill_booked_the_real_way_attests_its_own_basis(ex):
    """No hand-written fill_count anywhere in this test."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("2"), D("0.6200"), basis="OWN")
    x.store.apply_fill(SLUG, "DOWN", D("1"), D("0.3500"), basis="OWN")
    x.store.save()
    assert x.store.cost_attested(SLUG, "UP") is True
    assert x.store.cost_attested(SLUG, "DOWN") is True


def test_the_execution_check_accepts_a_genuinely_filled_leg(ex):
    """The Phase B question: does a real maker fill reach submission?

    Not asserting the completion SUCCEEDS -- that needs the switches and
    an armed transport. Asserting only that it is not refused for want of
    an attested basis, which is the one refusal that would make B3
    unreachable however the rest is configured.
    """
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("2"), D("0.6200"), basis="OWN")
    x.store.apply_fill(SLUG, "DOWN", D("1"), D("0.3500"), basis="OWN")
    x.store.save()
    x.transport.venue_positions = venue_rows(D("1"), SLUG)
    out = run(x)
    assert out.get("reason") != "COMPLETION_BASIS_UNATTESTED_AT_EXECUTION"
    # and in fact it goes all the way: a real fill reaches submission
    assert out["ok"] is True, out.get("reason")


def test_apply_fill_and_the_hand_written_helper_build_the_same_key(ex):
    """If these diverge, every test above is testing a key nothing writes."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("2"), D("0.6200"), basis="OWN")
    x.store.save()
    real = set(x.store.state["fill_count"])
    x.store.state["fill_count"] = {}
    x.store.state["positions"] = {}
    x.store.state["leg_cost"] = {}
    hold(x, up="2", dn="0")
    assert {k for k in x.store.state["fill_count"]
            if x.store.state["fill_count"][k]} == real


def test_a_reconcile_written_quantity_is_the_unattested_case(ex):
    """The 54 legs on the volume are this shape, not the fill shape."""
    x, t, E = ex
    k = "%s|UP" % SLUG
    x.store.state.setdefault("positions", {})[k] = "2"
    x.store.save()
    assert x.store.cost_attested(SLUG, "UP") is False


def test_a_genuine_fill_at_zero_is_attested_and_a_missing_one_is_not(ex):
    """The distinction the counter exists for: free shares vs lost cost."""
    x, t, E = ex
    x.store.apply_fill(SLUG, "UP", D("1"), D("0.0000"), basis="OWN")
    x.store.save()
    assert x.store.leg_cost(SLUG)["UP"] == D("0")
    assert x.store.cost_attested(SLUG, "UP") is True     # free, and known
    assert x.store.cost_attested(SLUG, "DOWN") is False  # nothing at all


# =====================================================================
# 13. THE LEGACY UNATTESTED INVENTORY THAT IS ON THE VOLUME RIGHT NOW.
#
# `L5_LIVE_RECONCILE` on deployment a342fccc, every sweep, 2026-11-06:
#
#   drift  cpc-btc-updown-15m-2026-10-12-0030z|DOWN  ours 2       venue 0
#          cpc-btc-updown-15m-2026-10-14-2230z|UP    ours 2.9000  venue 0
#   leg_cost_basis_unknown  54 entries
#   gross_detail_lost       both markets above
#
# These are real rows on the live volume: quantity our book believes in,
# which the venue does not report, with no attested cost. Three separate
# things must refuse to cross on them, and a test for each -- because any
# ONE of them holding is not evidence that the others do.
# =====================================================================

LEGACY_DN = "cpc-btc-updown-15m-2026-10-12-0030z"
LEGACY_UP = "cpc-btc-updown-15m-2026-10-14-2230z"


def _legacy(x, market, side, qty):
    """The reconcile shape: quantity, no leg_cost, no fill_count."""
    x.store.state.setdefault("positions", {})["%s|%s" % (market, side)] = qty
    x.store.state.pop("fill_count", None)
    x.store.state.pop("leg_cost", None)
    x.store.save()


def test_the_live_legacy_DOWN_leg_cannot_authorize_a_cross(ex):
    x, t, E = ex
    _legacy(x, LEGACY_DN, "DOWN", "2")
    assert x.store.cost_attested(LEGACY_DN, "DOWN") is False
    out = run(x, market=LEGACY_DN, buy_side="UP", held_side="DOWN",
              prop=proposal(side="UP"))
    assert out["ok"] is False
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == 0


def test_the_live_legacy_UP_leg_cannot_authorize_a_cross(ex):
    x, t, E = ex
    _legacy(x, LEGACY_UP, "UP", "2.9000")
    assert x.store.cost_attested(LEGACY_UP, "UP") is False
    out = run(x, market=LEGACY_UP, buy_side="DOWN", held_side="UP")
    assert out["ok"] is False
    assert x._defensive_mandate is None
    assert x.store.defensive_spent() == 0


def test_legacy_inventory_in_ANOTHER_market_is_not_reachable_from_this_one(ex):
    """A proposal names its market. The 54 legs live in other markets, and
    the route must not find them from the window it is working: a proposal
    for one market cannot be spent on inventory in another."""
    x, t, E = ex
    _legacy(x, LEGACY_DN, "DOWN", "2")
    hold(x)                      # attested inventory in the CURRENT market
    out = run(x, prop=proposal(market=LEGACY_DN))
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_PROPOSAL_MARKET_MISMATCH"
    assert x._defensive_mandate is None


def test_a_legacy_leg_the_venue_does_not_report_is_refused(ex):
    """ours 2, venue 0. Even with a basis invented, the venue disagrees."""
    x, t, E = ex
    k = "%s|DOWN" % LEGACY_DN
    x.store.state.setdefault("positions", {})[k] = "2"
    x.store.state.setdefault("leg_cost", {})[k] = "0.70"
    x.store.state.setdefault("fill_count", {})[k] = 1
    x.store.save()
    x.transport.venue_positions = venue_rows(D("0"), LEGACY_DN)
    out = run(x, market=LEGACY_DN, buy_side="UP", held_side="DOWN",
              prop=proposal(side="UP"))
    assert out["ok"] is False
    assert x._defensive_mandate is None


def test_every_one_of_the_54_unattested_keys_reads_as_unattested(ex):
    """The whole list from the live sweep, not a sample of it."""
    x, t, E = ex
    keys = [
        "cpc-btc-updown-15m-2026-10-14-2230z|UP",
        "cpc-btc-updown-15m-2026-10-15-1915z|DOWN",
        "cpc-btc-updown-15m-2026-10-15-1930z|UP",
        "cpc-btc-updown-15m-2026-10-15-1945z|DOWN",
        "cpc-btc-updown-15m-2026-10-15-2215z|DOWN",
        "cpceth-eth-updown-15m-2026-10-15-2230z|DOWN",
        "cpcsol-sol-updown-15m-2026-10-15-2330z|UP",
        "cpcxrp-xrp-updown-15m-2026-10-16-0100z|DOWN",
        "cpc-btc-updown-1d-2026-10-15-2100z|DOWN",
        "cpc-btc-updown-15m-2026-11-05-2315z|UP",
    ]
    pos = x.store.state.setdefault("positions", {})
    for k in keys:
        pos[k] = "1"
    x.store.state.pop("fill_count", None)
    x.store.save()
    for k in keys:
        m, s = k.rsplit("|", 1)
        assert x.store.cost_attested(m, s) is False, k


# =====================================================================
# 14. THE LIMITS BIND WITHOUT LOGGING.
#
# Two log lines on this service were truncating and both have now been
# bounded. That raises a fair question: is any limit enforced THROUGH
# the logging, so that reducing or losing a log line could loosen a
# bound? These tests answer it by breaking logging outright -- `emit`
# raises on every call -- and asserting every bound still refuses.
#
# If a limit were enforced via its telemetry, these tests fail.
# =====================================================================

class _Boom(Exception):
    pass


def _break_emit(monkeypatch):
    """Make every emit raise. Logging is now hostile, not merely absent."""
    def boom(*a, **k):
        raise _Boom("logging is broken")
    import src.l5_live_execution as _EX
    monkeypatch.setattr(_EX, "emit", boom, raising=False)
    return boom


def test_the_2_dollar_lifetime_budget_still_refuses_with_logging_broken(
        ex, monkeypatch):
    """DEFENSIVE_LOSS_BUDGET_USD is the $2.00 lifetime completion budget."""
    x, t, E = ex
    hold(x)
    rest(x)
    x.store.state["defensive_spent_usd"] = "2.00"      # budget exhausted
    x.store.save()
    _break_emit(monkeypatch)
    out = run(x, resting=[COID])
    assert out["ok"] is False
    assert x._defensive_mandate is None
    assert D(str(x.store.defensive_spent())) <= D("2.00")


def test_the_budget_is_read_from_the_volume_not_from_a_log(ex):
    """It is durable state, so a lost log line cannot reset it."""
    x, t, E = ex
    x.store.state["defensive_spent_usd"] = "1.75"
    x.store.save()
    reloaded = type(x.store)(x.store.path)
    assert D(str(reloaded.defensive_spent())) == D("1.75")


def test_broken_logging_cannot_create_authorization_it_did_not_have(
        ex, monkeypatch):
    """The mandate is the single exemption point for the maker_only
    overlay. Breaking logging must not be able to open it.

    NOT asserting that no mandate appears at all: with the switches on,
    an attested basis and budget remaining, a mandate IS the correct
    outcome and a broken log must not suppress a legitimate completion
    either. What must hold is that logging state cannot SUBSTITUTE for
    authorization -- so the unauthorized cases are the test.
    """
    x, t, E = ex
    _break_emit(monkeypatch)
    for setup in ("switch_off", "budget_spent", "unattested"):
        x._defensive_mandate = None
        hold(x)
        rest(x)
        if setup == "switch_off":
            monkeypatch.delenv(TCP.POLICY_ENV, raising=False)
        elif setup == "budget_spent":
            monkeypatch.setenv(TCP.POLICY_ENV, "YES")
            x.store.state["defensive_spent_usd"] = "2.00"
        else:
            monkeypatch.setenv(TCP.POLICY_ENV, "YES")
            x.store.state["defensive_spent_usd"] = "0"
            x.store.state["fill_count"] = {}
        x.store.save()
        try:
            out = run(x, resting=[COID])
            assert out["ok"] is False, setup
        except _Boom:
            pass                  # failing closed by raising is acceptable
        assert x._defensive_mandate is None, setup


def test_the_switches_still_gate_with_logging_broken(ex, monkeypatch):
    x, t, E = ex
    monkeypatch.delenv(TCP.POLICY_ENV, raising=False)
    hold(x)
    rest(x)
    _break_emit(monkeypatch)
    out = run(x, resting=[COID])
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_TAKER_NOT_ENABLED"
    assert COID in x.store.live_orders()


def test_an_unattested_basis_still_refuses_with_logging_broken(
        ex, monkeypatch):
    x, t, E = ex
    hold(x)
    x.store.state["fill_count"] = {}
    x.store.save()
    _break_emit(monkeypatch)
    out = run(x)
    assert out["ok"] is False
    assert out["reason"] == "COMPLETION_BASIS_UNATTESTED_AT_EXECUTION"


def _code_lines(fn):
    """Source lines that are CODE, not docstring or comment. The first
    version of the test below matched `defensive_charge` in the
    docstring on line 30 and reported a defect that was not there."""
    import inspect
    out = []
    for i, L in enumerate(inspect.getsource(fn).split("\n")):
        t = L.strip()
        if not t or t.startswith("#") or t.startswith('"') or t.startswith("*"):
            continue
        out.append((i, L))
    return out


def test_the_budget_check_precedes_the_charge_in_source_order(ex):
    """Ordering, not just presence: a charge before its test would spend
    the budget and only then discover it was gone.

    Verified against the real code lines: the budget is READ, then the
    refusal is tested, and only afterwards is anything charged.
    """
    rows = _code_lines(type(ex[0]).completion_taker_execute)
    refuse = [i for i, L in rows
              if "> DEF.DEFENSIVE_LOSS_BUDGET_USD" in L]
    charge = [i for i, L in rows if ".defensive_charge(" in L]
    assert refuse, "no budget refusal found"
    assert charge, "no charge found"
    assert min(refuse) < min(charge), (
        "budget charged at line %d before it was tested at line %d"
        % (min(charge), min(refuse)))


def test_an_unreadable_budget_is_treated_as_fully_spent(ex):
    """Fail closed: the handler sets `spent` to the whole budget, so an
    unreadable figure refuses rather than reading as zero."""
    rows = _code_lines(type(ex[0]).completion_taker_execute)
    i_except = [i for i, L in rows
                if "spent = DEF.DEFENSIVE_LOSS_BUDGET_USD" in L]
    assert i_except, "no fail-closed assignment for an unreadable budget"


def test_every_bound_the_owner_named_is_read_from_state_not_telemetry(ex):
    """$2 completion budget, worst-case deficit, unmatched contracts and
    the cash gate are all read from the store or the gates object."""
    import inspect
    src = inspect.getsource(type(ex[0]).completion_taker_execute)
    for token in ("defensive_spent", "DEFENSIVE_LOSS_BUDGET",
                  "cost_attested", "deadman", "cancel_confirmed"):
        assert token in src, token


# =====================================================================
# 15. THE FIVE STAGES OF A COMPLETION, NEVER COLLAPSED.
#
# THE INCIDENT, 2026-11-06 20:15z. L5_TS_COMPLETION_RESULT reported
# `{"captured":true,"submitted":true,"refused":null}` for a ranked
# TAKER_COMPLETE, side UP, qty 1, own price 0.4300, route
# CONTROLLER_REENTRY. Reconciling the order tape afterwards:
#
#   there is NO submission at own 0.4300 anywhere in that window.
#   the nearest UP orders are 0.3900 (filled) and 0.3700 (cancelled),
#   six and eight seconds later.
#
# Under `execution_mode: MAKER_ONLY` the placement path posted a MAKER
# quote and returned ok, and `_bc_capture` reads `submitted` from `ok`.
# So a field named `crossed` was set from "an order was placed".
#
# The budget is the clean discriminator: `completion_taker_execute` is
# the ONLY path that debits `defensive_spent_usd`. No debit, no cross.
# =====================================================================

def _stages(spent_before, spent_after, submitted, order_ref=None):
    import src.l5_pmus_live_runner as R
    import collections

    class S:
        stats = collections.Counter()
        completion_stages = R.Runner.completion_stages
    return S().completion_stages(
        {"submitted": submitted, "order_ref": order_ref},
        want_side="UP", want_px=D("0.4300"),
        spent_before=spent_before, spent_after=spent_after)


def test_controller_reentry_with_no_budget_debit_is_not_a_cross():
    """The 20:15z case, exactly: submitted true, budget unmoved."""
    st = _stages("0", "0", True, order_ref="DJDCEDVG13Z6")
    assert st["venue_submitted"] is True
    assert st["taker_cross_confirmed"] is False
    assert st["classification"] == "CONTROLLER_REENTRY_NO_CROSS"
    assert st["budget_debited_usd"] == "0"


def test_a_real_debit_is_a_confirmed_cross():
    st = _stages("0", "0.45", True, order_ref="X1")
    assert st["taker_cross_confirmed"] is True
    assert st["classification"] == "TAKER_CROSS_CONFIRMED"
    assert st["budget_debited_usd"] == "0.45"


def test_an_unreadable_budget_leaves_the_cross_UNKNOWN_not_false():
    """None is UNKNOWN. Reading it as False would under-report a cross
    that did happen, which is the mirror of the bug being fixed."""
    for before, after in (("0", None), (None, "0.45"), (None, None)):
        st = _stages(before, after, True)
        assert st["taker_cross_confirmed"] is None, (before, after)
        assert st["classification"] == "CROSS_UNDETERMINED_BUDGET_UNREADABLE"


def test_not_submitted_is_its_own_classification():
    st = _stages("0", "0", False)
    assert st["venue_submitted"] is False
    assert st["taker_cross_confirmed"] is False
    assert st["classification"] == "NOT_SUBMITTED"
    assert st["acknowledged"] is None       # nothing to acknowledge


def test_an_unreadable_placement_is_not_a_cross_either():
    st = _stages("0", "0", None)
    assert st["classification"] == "PLACEMENT_UNREADABLE"
    assert st["taker_cross_confirmed"] is False


def test_the_requested_price_is_recorded_so_it_can_be_compared():
    """The 20:15z defect was invisible because the RANKED price was
    logged and the PLACED price was not. The requested price is now on
    the record for exactly that comparison."""
    st = _stages("0", "0", True)
    assert st["requested_price_own"] == "0.4300"
    assert st["requested_side"] == "UP"


def test_fill_is_never_asserted_here():
    """A fill is a later, separate venue event."""
    st = _stages("0", "0.45", True, order_ref="X1")
    assert st["filled"] is None
    assert "L5_LIVE_LIFECYCLE" in st["labels"]


def test_all_five_stages_are_named_and_present():
    import src.l5_pmus_live_runner as R
    st = _stages("0", "0", True, order_ref="X1")
    assert R.Runner.COMPLETION_STAGES == (
        "controller_selected", "venue_submitted", "acknowledged",
        "filled", "budget_debited")
    for k in ("controller_selected", "venue_submitted", "acknowledged",
              "filled"):
        assert k in st, k
    assert "budget_debited_usd" in st


def test_the_outlay_follows_the_cross_not_the_placement():
    """The per-window taker outlay must not accrue for a maker quote."""
    import inspect
    import src.l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.submit_ts_completion)
    i = src.index('out["crossed"] = bool(stages.get("taker_cross_confirmed"))')
    tail = src[i:i + 400]
    assert 'if out["crossed"]:' in tail, tail[:200]


def test_the_result_line_carries_the_classification():
    import inspect
    import src.l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.submit_ts_completion)
    assert '"taker_cross_confirmed": stages.get("taker_cross_confirmed")' in src
    assert '"classification": stages.get("classification")' in src


def test_END_TO_END_the_20_15z_case_is_no_longer_reported_as_a_cross(
        monkeypatch):
    """The regression that matters. A controller re-entry that posts a
    MAKER quote and returns ok must come back NOT crossed, with the
    classification naming why, and must NOT accrue the taker outlay."""
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)
    r.executor.crosses = False            # MAKER_ONLY placement, no debit
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs),
                                 _pol("0.3300"), 1793934115.0)
    assert out["outcome"]["submitted"] is True      # an order WAS placed
    assert out["crossed"] is False                  # but it did not cross
    st = out["stages"]
    assert st["taker_cross_confirmed"] is False
    assert st["classification"] == "CONTROLLER_REENTRY_NO_CROSS"
    assert st["budget_debited_usd"] == "0"
    assert st["requested_price_own"] == "0.3300"
    assert out["withheld_by"].startswith(
        "PLACEMENT:CONTROLLER_REENTRY_NO_CROSS")
    # and the per-window taker outlay did NOT accrue for a maker quote
    assert D(str(getattr(r, "_taker_outlay_usd", 0) or 0)) == 0


def test_END_TO_END_a_real_cross_is_reported_and_charged(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    rungs = [["0.3300", "448"]]
    r = _SubRunner(rungs)
    r.executor.crosses = True
    out = r.submit_ts_completion(_dec("0.3300"), _src(rungs),
                                 _pol("0.3300"), 1793934115.0)
    assert out["crossed"] is True
    assert out["stages"]["classification"] == "TAKER_CROSS_CONFIRMED"
    assert D(out["stages"]["budget_debited_usd"]) > 0
    assert D(str(r._taker_outlay_usd)) > 0


def test_the_budget_telemetry_reports_spent_limit_and_remaining():
    import src.l5_pmus_live_runner as R
    import collections

    class S:
        defensive_budget_telemetry = R.Runner.defensive_budget_telemetry
        defensive_spent_usd = R.Runner.defensive_spent_usd
        stats = collections.Counter()

        def __init__(self):
            self.store = _SubStore()

    s = S()
    t = s.defensive_budget_telemetry()
    assert t["spent_usd"] == "0"
    assert t["limit_usd"] == "2.00"
    assert t["remaining_usd"] == "2.00"
    s.store.defensive_charge("0.45")
    t2 = s.defensive_budget_telemetry()
    assert t2["spent_usd"] == "0.45"
    assert t2["remaining_usd"] == "1.55"
    assert "LIFETIME" in t2["labels"]


def test_an_unreadable_budget_telemetry_is_none_not_zero():
    import src.l5_pmus_live_runner as R
    import collections

    class Bad:
        def defensive_spent(self):
            raise RuntimeError("volume unreadable")

    class S:
        defensive_budget_telemetry = R.Runner.defensive_budget_telemetry
        defensive_spent_usd = R.Runner.defensive_spent_usd
        stats = collections.Counter()
        store = Bad()

    t = S().defensive_budget_telemetry()
    assert t["spent_usd"] is None
    assert t["remaining_usd"] is None       # not computed from a guess
    assert t["limit_usd"] == "2.00"
