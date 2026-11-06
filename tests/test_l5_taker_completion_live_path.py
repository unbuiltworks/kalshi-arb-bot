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

    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "TAKER_COMPLETION")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    t = PosTransport()
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                           transport=t, now_fn=H.NOW, maker_only=True)
    return x, t, E


def hold(x, *, up="2", dn="1", market=SLUG):
    """The 0300z book: UP 2 at own 0.6200, DOWN 1 at own 0.3500."""
    pos = x.store.state.setdefault("positions", {})
    pos["%s|UP" % market] = str(up)
    pos["%s|DOWN" % market] = str(dn)
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
