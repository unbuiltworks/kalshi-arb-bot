"""Adversarial suite for the unarmed Gemini continuous execution driver.

The driver exists to close two measured seam defects: a budget that only bound
a caller who opted in, and a budget that was spent on re-decisions rather than
on orders. Most of what follows attacks those two, and the rest attacks the
properties that make a one-window probe bounded at all.

NOTHING HERE ARMS ANYTHING AGAINST A VENUE. `_authorized` patches the module's
own authorization constants so the mutation paths are reachable AT ALL in a
test; the shipped file has them as None, and
test_without_authorization_no_mutation_is_possible proves that from the
un-patched module.
"""
import ast
import asyncio
import json
import os

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import ws_continuous as WC

SYM = "GEMI-BTC05M2609181200-UP"
OTHER = "GEMI-BTC05M2609181205-UP"
AUTH = "OWNER-TEST-NOT-A-REAL-AUTHORIZATION"


# --------------------------------------------------------------- scaffolding

class FakeSession:
    """Scripted acks. `script` is a list of dicts, exceptions, or None."""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default if default is not None else {"status": 200}
        self.sent = []

    async def request(self, method, params, timeout=5.0):
        self.sent.append({"method": method, "params": params})
        r = self.script.pop(0) if self.script else self.default
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def _authorized(monkeypatch):
    """Make the mutation paths reachable inside the test process only."""
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized(), WC.authorization_state()["why_not"]


def _driver(tmp_path, symbol=SYM, t=1_789_000_000.0):
    return WC.Driver(AUTH, symbol, ledger_path=str(tmp_path / "mut.jsonl"),
                     now_fn=lambda: t)


def _ready(tmp_path, *, fills=None, open_orders=None, orders=None, **kw):
    """A driver past recovery and binding, with the venue answering."""
    d = _driver(tmp_path, **kw)
    d.state = WC.PREFLIGHT_OK
    d.state = WC.AUTHORIZED
    r = d.recover(venue_fills=fills if fills is not None else [],
                  venue_open=open_orders if open_orders is not None else [],
                  venue_orders=orders if orders is not None else [])
    assert r["recovered"], r
    assert d.bind()["bound"]
    return d


def _rung(leg, price, qty="0.01"):
    return {"leg": leg, "price": price, "qty": qty, "action": CP.OPEN}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


# ============================================================ AUTHORIZATION

def test_without_authorization_no_mutation_is_possible():
    """The shipped module, unpatched."""
    st = WC.authorization_state()
    assert st["authorized"] is False
    assert WC.REQUIRED_AUTH_ID is None
    assert WC.AUTHORIZED_SHA is None
    assert WC.AUTHORIZED_ENVELOPE is None
    with pytest.raises(WC.NotAuthorized):
        WC.require_authorization()


def test_no_environment_variable_can_supply_an_authorization(monkeypatch):
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", "anything-at-all")
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized() is False, (
        "an authorization must be a reviewed commit, never an env var")


def test_an_edited_executable_invalidates_the_authorization(monkeypatch):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", "0" * 64)      # a different file
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    st = WC.authorization_state()
    assert st["authorized"] is False
    assert any("different executable" in w for w in st["why_not"])


def test_a_widened_envelope_invalidates_the_authorization(monkeypatch):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", "0" * 64)
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized() is False


def test_an_expired_authorization_cannot_mutate(monkeypatch, tmp_path):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2020-01-01T00:00:00Z")
    assert WC.authorized() is False
    d = _driver(tmp_path)
    with pytest.raises(WC.NotAuthorized):
        _run(d.place(FakeSession(), _rung("YES", "0.55")))
    assert d.ledger.consumed() == 0, "a refused call must not spend a slot"


# ================================================== SEAM 2: RESTING-ORDER DIFF

def test_an_unchanged_desired_surface_places_nothing_twice(tmp_path,
                                                           _authorized):
    d = _ready(tmp_path)
    want = [_rung("YES", "0.55"), _rung("NO", "0.40")]
    s = FakeSession()
    for r in WC.diff_surface(want, d.working)["place"]:
        _run(d.place(s, r["rung"]))
    assert d.budget_consumed == 2

    # the controller re-decides the SAME surface, forever
    for _ in range(50):
        plan = WC.diff_surface(want, d.working)
        assert plan["mutations"] == 0, plan
        assert len(plan["keep"]) == 2
    assert d.budget_consumed == 2, (
        "50 re-decisions of an unchanged surface must cost nothing; this is "
        "the defect that spent 12 slots in 6 cycles")
    assert len(s.sent) == 2


def test_two_flat_state_rungs_consume_exactly_two_slots(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    for r in WC.diff_surface([_rung("YES", "0.55"), _rung("NO", "0.40")],
                             d.working)["place"]:
        _run(d.place(s, r["rung"]))
    assert d.budget_consumed == 2
    assert d.budget_left() == CP.MAX_ACTIONS - 2


def test_a_changed_price_costs_a_cancel_and_a_place(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    _run(d.place(s, _rung("YES", "0.55")))
    assert d.budget_consumed == 1

    plan = WC.diff_surface([_rung("YES", "0.56")], d.working)
    assert plan["mutations"] == 2, plan
    assert len(plan["cancel"]) == 1 and len(plan["place"]) == 1
    for c in plan["cancel"]:
        _run(d.cancel(s, c["order"]))
    for p in plan["place"]:
        _run(d.place(s, p["rung"]))
    assert d.budget_consumed == 3


def test_a_removed_rung_is_cancelled_not_left_resting(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    _run(d.place(s, _rung("YES", "0.55")))
    plan = WC.diff_surface([], d.working)
    assert len(plan["cancel"]) == 1 and plan["mutations"] == 1


def test_a_near_match_is_not_treated_as_the_same_order(tmp_path, _authorized):
    d = _ready(tmp_path)
    _run(d.place(FakeSession(), _rung("YES", "0.55", qty="0.01")))
    plan = WC.diff_surface([_rung("YES", "0.55", qty="0.02")], d.working)
    assert plan["mutations"] == 2, (
        "a different size is a different order; keeping it would leave the "
        "venue holding something we stopped intending")


# ========================================================== SEAM 1: THE BUDGET

def test_the_slot_is_spent_before_the_frame_goes_out(tmp_path, _authorized):
    """A send that never returns has already cost its slot."""
    d = _ready(tmp_path)
    s = FakeSession(script=[TimeoutError("socket died")])
    with pytest.raises(WC.ReconcileRequired):
        _run(d.place(s, _rung("YES", "0.55")))
    assert d.budget_consumed == 1
    assert d.ledger.consumed() == 1
    assert d.state == WC.RECONCILE


def test_a_rejected_order_still_costs_its_slot(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession(script=[{"status": 400, "reason": "nope"}])
    out = _run(d.place(s, _rung("YES", "0.55")))
    assert out["rejected"] is True
    assert d.budget_consumed == 1, (
        "counting only accepted orders lets a rejection loop run forever "
        "inside a budget meant to bound how often we touch the venue")
    assert d.working.rungs() == []


def test_twelve_cannot_become_thirteen(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    for i in range(CP.MAX_ACTIONS):
        _run(d.place(s, _rung("YES" if i % 2 else "NO", "0.%02d" % (10 + i))))
    assert d.budget_consumed == CP.MAX_ACTIONS == 12
    assert d.budget_left() == 0
    with pytest.raises(CP.ProbeStop):
        _run(d.place(s, _rung("YES", "0.99")))
    assert d.budget_consumed == 12, "the refused 13th must not be recorded"
    assert len(s.sent) == 12


def test_a_plan_that_would_overrun_the_budget_stops_instead(tmp_path,
                                                            _authorized):
    d = _ready(tmp_path)
    d.budget_consumed = CP.MAX_ACTIONS - 1
    out = d.decide(200.0, book=_book())
    if out.get("plan") and out["plan"]["mutations"] > 1:
        assert d.state == WC.STOPPED
        assert "slot" in (d.why or "")


# ------------------------------------------------------------------- restart

def test_restart_cannot_reset_the_action_count(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    for i in range(3):
        _run(d.place(s, _rung("YES", "0.%02d" % (50 + i))))
    assert d.budget_consumed == 3

    # a brand new process, same durable ledger
    d2 = _driver(tmp_path)
    assert d2.ledger.consumed() == 3
    d2.state = WC.AUTHORIZED
    r = d2.recover(venue_fills=[], venue_open=[], venue_orders=[])
    assert r["recovered"], r
    assert d2.budget_consumed == 3, "a restart must never re-open the budget"
    assert d2.budget_left() == CP.MAX_ACTIONS - 3


def test_a_venue_that_knows_more_than_the_ledger_wins(tmp_path):
    led = WC.MutationLedger(str(tmp_path / "m.jsonl"), auth_id=AUTH,
                            symbol=SYM)
    led.attempt(WC.PLACE, {"client_order_id": "a"})
    used, why = WC.reconstruct_budget(led, [{"id": 1}, {"id": 2}, {"id": 3}])
    assert used == 3, why
    assert "read upward" in why


def test_an_unreadable_venue_stops_rather_than_assuming_zero(tmp_path):
    led = WC.MutationLedger(str(tmp_path / "m.jsonl"), auth_id=AUTH,
                            symbol=SYM)
    used, why = WC.reconstruct_budget(led, None)
    assert used is None
    assert "never assumes an empty one" in why


def test_a_torn_ledger_row_is_a_consumed_slot_not_a_skipped_one(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps({"kind": WC.PLACE, "slot": 0,
                             "outcome": WC.ACCEPTED}) + "\n{partial")
    led = WC.MutationLedger(str(p), auth_id=AUTH, symbol=SYM)
    assert led.consumed() == 2
    assert len(led.unresolved()) == 1


def test_an_unresolved_attempt_forces_reconciliation_before_anything_else(
        tmp_path, _authorized):
    d = _ready(tmp_path)
    d.ledger.attempt(WC.PLACE, {"client_order_id": "dangling"})
    d2 = _driver(tmp_path)
    d2.state = WC.AUTHORIZED
    r = d2.recover(venue_fills=[], venue_open=[], venue_orders=[])
    assert r["recovered"] is False
    assert d2.state == WC.RECONCILE


def test_restart_cannot_reset_inventory(tmp_path):
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    r = d.recover(
        venue_fills=[{"leg": "YES", "qty": "0.01", "price": "0.55"},
                     {"leg": "YES", "qty": "0.01", "price": "0.55"}],
        venue_open=[], venue_orders=[])
    assert r["recovered"]
    assert d.inv.yes_qty == CP.D("0.02")
    assert d.inv.residual() == CP.D("0.02")


def test_unreadable_exposure_stops(tmp_path):
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    r = d.recover(venue_fills=None, venue_open=[], venue_orders=[])
    assert r["recovered"] is False
    assert d.state == WC.DO_NOT_TRADE
    assert "never assumes flat" in (d.why or "")


def test_unreadable_open_orders_stop(tmp_path):
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    r = d.recover(venue_fills=[], venue_open=None, venue_orders=[])
    assert r["recovered"] is False
    assert d.state == WC.DO_NOT_TRADE


def test_the_venue_is_authoritative_for_what_is_resting(tmp_path):
    d = _driver(tmp_path)
    d.working.add("ours", leg="YES", price="0.55", qty="0.01", now=0.0)
    d.state = WC.AUTHORIZED
    d.recover(venue_fills=[], venue_orders=[], venue_open=[
        {"clientOrderId": "theirs", "outcome": "no", "price": "0.40",
         "quantity": "0.01"}])
    ids = [o["client_order_id"] for o in d.working.rungs()]
    assert ids == ["theirs"], "our local view never outranks the venue's"


# =================================================================== FILLS

def test_a_full_fill_updates_inventory_and_retires_the_order(tmp_path,
                                                             _authorized):
    d = _ready(tmp_path)
    out = _run(d.place(FakeSession(), _rung("YES", "0.55")))
    coid = out["client_order_id"]
    r = d.apply_fill(coid, "0.01", "0.55", partial=False)
    assert r["applied"] and d.inv.yes_qty == CP.D("0.01")
    assert d.working.by_id == {}


def test_a_partial_fill_updates_inventory_and_leaves_the_rest_resting(
        tmp_path, _authorized):
    d = _ready(tmp_path)
    out = _run(d.place(FakeSession(), {"leg": "YES", "price": "0.55",
                                       "qty": "0.02"}))
    coid = out["client_order_id"]
    r = d.apply_fill(coid, "0.01", "0.55", partial=True)
    assert r["applied"] and d.inv.yes_qty == CP.D("0.01")
    assert d.working.by_id[coid]["qty"] == "0.01", (
        "the remainder is still in the queue and must stay resting")


def test_inventory_moves_before_the_next_decision(tmp_path, _authorized):
    d = _ready(tmp_path)
    out = _run(d.place(FakeSession(), _rung("YES", "0.55")))
    before = d.decide(200.0, book=_book())
    d.apply_fill(out["client_order_id"], "0.01", "0.55", partial=False)
    after = d.decide(200.0, book=_book())
    assert before["turn"]["actions"] != after["turn"]["actions"] or \
        d.inv.yes_qty == CP.D("0.01")
    assert d.inv.yes_qty == CP.D("0.01")


def test_a_fill_for_an_order_we_do_not_hold_is_not_applied(tmp_path):
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    d.recover(venue_fills=[], venue_open=[], venue_orders=[])
    r = d.apply_fill("someone-elses", "0.01", "0.55", partial=False)
    assert r["applied"] is False
    assert d.inv.yes_qty == CP.D("0")


# ============================================================= RISK ENVELOPE

def _book(ybid="0.55", yask="0.60"):
    return {"readable": True, "identity_consistent": True,
            "yes": {"bid": ybid, "ask": yask},
            "no": {"bid": str(1 - float(yask)), "ask": str(1 - float(ybid))}}


def _fill_n(d, leg, n, price="0.01"):
    for _ in range(n):
        d.inv.apply_fill(leg, "0.01", price, source="test")


@pytest.mark.parametrize("leg,n,bound", [
    ("YES", 5, "residual"),          # 0.05 > MAX_RESIDUAL 0.04
])
def test_a_rung_that_would_breach_residual_or_imbalance_is_refused(
        tmp_path, _authorized, leg, n, bound):
    d = _ready(tmp_path)
    _fill_n(d, leg, n)
    assert abs(d.inv.residual()) > CP.MAX_RESIDUAL
    with pytest.raises(CP.ProbeStop):
        _run(d.place(FakeSession(), _rung(leg, "0.01")))
    assert d.state == WC.STOPPED


def test_a_rung_that_would_breach_the_yes_cap_is_refused(tmp_path,
                                                          _authorized):
    d = _ready(tmp_path)
    # balance the legs so imbalance is not what trips first
    _fill_n(d, "YES", 10)
    _fill_n(d, "NO", 10)
    assert d.inv.yes_qty == CP.D("0.10") == CP.MAX_YES
    with pytest.raises(CP.ProbeStop):
        _run(d.place(FakeSession(), _rung("YES", "0.01")))
    assert "past" in (d.why or "")


def test_a_rung_that_would_breach_total_spend_is_refused(tmp_path,
                                                          _authorized):
    d = _ready(tmp_path)
    _fill_n(d, "YES", 8, price="0.99")
    _fill_n(d, "NO", 8, price="0.99")
    assert d.inv.spend() > CP.MAX_TOTAL_SPEND, d.inv.spend()
    with pytest.raises(CP.ProbeStop):
        _run(d.place(FakeSession(), _rung("YES", "0.99")))


def test_the_envelope_matches_the_frozen_controller_exactly():
    e = WC.envelope()
    assert e["max_actions"] == "12"
    assert e["action_size"] == "0.01"
    assert e["max_total_spend_usd"] == "0.15"
    assert e["max_residual"] == "0.04"
    assert e["max_imbalance"] == "0.04"
    assert e["max_yes"] == "0.10" and e["max_no"] == "0.10"
    assert e["maker_tif"] == "MOC"


# ============================================================ WINDOW BINDING

def test_the_symbol_is_immutable(tmp_path):
    d = _driver(tmp_path)
    assert d.symbol == SYM
    with pytest.raises(AttributeError):
        d.symbol = OTHER


def test_a_non_btc5m_symbol_is_refused_at_construction(tmp_path):
    with pytest.raises(CP.ProbeStop):
        WC.Driver(AUTH, "GEMI-BTC15M2609181200-UP",
                  ledger_path=str(tmp_path / "m.jsonl"))


def test_a_changed_symbol_mid_window_stops(tmp_path, _authorized):
    d = _ready(tmp_path)
    with pytest.raises(CP.ProbeStop):
        d.guard.check_symbol(OTHER)
    assert d.guard.state == CP.PROBE_STOPPED


def test_there_is_no_rollover_to_a_next_window(tmp_path, _authorized):
    d = _ready(tmp_path)
    out = d.decide(10.0, book=_book())          # under the withdraw floor
    assert d.state == WC.WITHDRAWING
    d.stop(WC.STOPPED, "withdrawn")
    assert d.state == WC.STOPPED
    # terminal really is terminal
    d.stop(WC.RUNNING, "try to restart")
    assert d.state == WC.STOPPED


def test_bind_before_recovery_is_refused(tmp_path):
    d = _driver(tmp_path)
    assert d.bind()["bound"] is False
    assert d.state == WC.DO_NOT_TRADE


# ============================================================ END OF WINDOW

def test_under_thirty_seconds_withdraws(tmp_path, _authorized):
    d = _ready(tmp_path)
    _run(d.place(FakeSession(), _rung("YES", "0.55")))
    out = d.decide(20.0, book=_book())
    assert d.state == WC.WITHDRAWING
    assert out["plan"]["place"] == []
    assert len(out["plan"]["cancel"]) == 1


def test_between_thirty_and_sixty_suppresses_open_and_extend(tmp_path,
                                                              _authorized):
    d = _ready(tmp_path)
    out = d.decide(45.0, book=_book())
    assert out["window_phase"] == CP.WINDOW_COMPLETE_ONLY
    acts = out["turn"]["actions"]
    assert all(a["action"] not in (CP.OPEN, CP.EXTEND) for a in acts), acts
    assert {h["held_by"] for h in out["turn"]["holds"]} == {"END_OF_WINDOW"}


def test_an_unknown_tte_withdraws(tmp_path, _authorized):
    assert CP.window_phase(None) == CP.WINDOW_WITHDRAWN


def test_a_stale_market_state_stops(tmp_path, _authorized):
    d = _ready(tmp_path)
    st = CP.state_from_venue(SYM, 200.0, d.inv, now=1_789_000_000.0,
                             book=_book())
    st["state_age_s"] = CP.DEADMAN_S + 5.0
    out = d.guard.step(st, d.inv, now=1_789_000_000.0)
    assert out.get("stop") and "DEADMAN" in out["stop"]


# ====================================================== NO TAKER PATH AT ALL

def test_no_sell_ioc_fok_or_taker_path_exists():
    src = open(WC.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            dd = ast.get_docstring(n, clean=False)
            if dd:
                docs.add(dd)
    literals -= docs
    for bad in ("SELL", "IOC", "FOK", "MARKET", "TAKER"):
        assert not any(bad == str(v).upper() for v in literals), bad
    # and the only methods it ever calls on a session are the two it declares
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "cancel_session" not in calls


def test_only_place_and_cancel_are_ever_sent(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    _run(d.place(s, _rung("YES", "0.55")))
    _run(d.cancel(s, d.working.rungs()[0]))
    assert {m["method"] for m in s.sent} <= {"order.place", "order.cancel"}


def test_every_frame_is_a_maker_only_buy(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession()
    _run(d.place(s, _rung("YES", "0.55")))
    _run(d.place(s, _rung("NO", "0.40")))
    for m in s.sent:
        if m["method"] != "order.place":
            continue
        assert m["params"]["side"] == "BUY"
        assert m["params"]["type"] == "LIMIT"
        assert m["params"]["timeInForce"] == "MOC"


# ============================================================ AMBIGUITY

def test_an_ambiguous_cancel_stops_and_reconciles(tmp_path, _authorized):
    d = _ready(tmp_path)
    _run(d.place(FakeSession(), _rung("YES", "0.55")))
    o = d.working.rungs()[0]
    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(FakeSession(script=[{"status": 500}]), o))
    assert d.state == WC.RECONCILE
    assert d.working.by_id, "an unconfirmed cancel leaves the order resting"


def test_an_ack_without_a_status_is_ambiguous_not_a_failure(tmp_path,
                                                             _authorized):
    d = _ready(tmp_path)
    with pytest.raises(WC.ReconcileRequired):
        _run(d.place(FakeSession(script=[{"nope": True}]), _rung("YES", "0.55")))
    assert d.state == WC.RECONCILE
    assert d.ledger.consumed() == 1


def test_the_preamble_runs_in_order_and_stops_on_the_first_refusal(tmp_path):
    """No authorization -> the run never reaches recovery or a socket."""
    d = _driver(tmp_path)
    rep = _run(d.run_window(
        FakeSession(), tte_of=lambda: 200.0, venue_fills=[], venue_open=[],
        venue_orders=[],
        readers={"terms_for": lambda s: dict(
                     PF.FROZEN for PF.FROZEN in []) or {
                         "quantityMinimum": "0.01",
                         "quantityIncrement": "0.01",
                         "priceMinimum": "0.01", "priceIncrement": "0.01",
                         "contract_status": "active",
                         "market_state": "open"},
                 "terms_status": lambda: {"hasAcceptedLatest": True,
                                          "acceptedVersion": 1},
                 "outcome_book": lambda s: _book()}))
    assert rep["state"] == WC.DO_NOT_TRADE
    assert "not authorized" in (rep["why"] or "")
    assert rep["budget_consumed"] in (None, 0)
    assert "recovery" not in rep


def test_a_resolved_ledger_replays_clean_on_restart(tmp_path):
    """REGRESSION. _load() read RESOLVE rows but never applied them, so every
    clean restart saw its own finished attempts as unresolved and refused to
    continue -- safe, but it made ordinary restart recovery impossible."""
    p = str(tmp_path / "m.jsonl")
    led = WC.MutationLedger(p, auth_id=AUTH, symbol=SYM)
    slot = led.attempt(WC.PLACE, {"client_order_id": "a"})
    led.resolve(slot, WC.ACCEPTED, {"status": 200})
    assert led.unresolved() == []

    again = WC.MutationLedger(p, auth_id=AUTH, symbol=SYM)
    assert again.consumed() == 1, "the slot is still spent"
    assert again.unresolved() == [], "and it is still resolved"
    assert again.client_order_ids() == ["a"]


def test_an_unresolved_attempt_survives_the_replay(tmp_path):
    p = str(tmp_path / "m.jsonl")
    led = WC.MutationLedger(p, auth_id=AUTH, symbol=SYM)
    led.attempt(WC.PLACE, {"client_order_id": "dangling"})
    again = WC.MutationLedger(p, auth_id=AUTH, symbol=SYM)
    assert len(again.unresolved()) == 1


def test_the_first_flat_state_decision_is_two_maker_buys(tmp_path,
                                                          _authorized):
    """From the FROZEN controller, not from hand-built rungs."""
    d = _ready(tmp_path)
    out = d.decide(200.0, book=_book())
    acts = out["turn"]["actions"]
    assert [a["action"] for a in acts] == [CP.OPEN, CP.OPEN]
    assert sorted(a["leg"] for a in acts) == ["NO", "YES"]
    assert out["plan"]["mutations"] == 2
    assert out["plan"]["keep"] == []

    s = FakeSession()
    for p in out["plan"]["place"]:
        _run(d.place(s, p["rung"]))
    assert d.budget_consumed == 2

    # and the SAME decision on the next cycle costs nothing more
    again = d.decide(195.0, book=_book())
    assert again["plan"]["mutations"] == 0
    assert len(again["plan"]["keep"]) == 2
    assert d.budget_consumed == 2

    for m in s.sent:
        assert m["params"]["side"] == "BUY"
        assert m["params"]["timeInForce"] == "MOC"
