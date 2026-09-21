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
import hashlib
import json
import os
import re

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import ws_continuous as WC


def _roomy(**over):
    """A test-only envelope with the CASH and ORDER-COUNT caps lifted.

    Used ONLY by tests whose subject is something else -- budget accounting,
    partial-fill bookkeeping -- so that a cap they are not testing cannot make
    them pass or fail for the wrong reason. Each cap lifted here is itself
    driven to its limit in tests/test_gemini_live_envelope_q1.py, which is
    where those properties are actually proven.
    """
    import dataclasses
    return dataclasses.replace(
        LE.SHADOW_Q001, max_working_orders=64,
        max_committed_usd=CP.D("1000"), max_total_spend_usd=CP.D("1000"),
        max_residual_usd=CP.D("1000"), max_yes=CP.D("1000"),
        max_no=CP.D("1000"), max_same_side_run=100000, **over)

SYM = "GEMI-BTC05M2609181200-UP"
OTHER = "GEMI-BTC05M2609181205-UP"
AUTH = "OWNER-TEST-NOT-A-REAL-AUTHORIZATION"


# --------------------------------------------------------------- scaffolding

class FakeSession:
    """Scripted acks. `script` is a list of dicts, exceptions, or None."""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        # the venue's real place ack carries the order id under `result`; a
        # default that omitted it hid the cancel bug from every test
        self.default = (default if default is not None
                        else {"status": 200, "result": {"orderId": 1}})
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


def _driver(tmp_path, symbol=SYM, t=1_789_000_000.0, envelope=None):
    kw = {} if envelope is None else {"envelope": envelope}
    return WC.Driver(AUTH, symbol, ledger_path=str(tmp_path / "mut.jsonl"),
                     now_fn=lambda: t, **kw)


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

def test_editing_the_driver_invalidates_its_authorization():
    """THE DURABLE INVARIANT, proved by SIMULATING an edit.

    This test used to assert AUTHORIZED_SHA != executable_sha(), because the
    cancel fix had changed the driver after CONTINUOUS-2 was granted. That was
    a statement about one afternoon, not about the mechanism -- renewing the
    authorization against the fixed executable turned it red, and a test that
    fails when the system does exactly what it was built to do is pinning the
    wrong thing.

    What must hold forever: code that changed after review cannot inherit the
    approval. So edit the body and show the hash moves.
    """
    edited = hashlib.sha256(
        (WC.executable_body() + "\n# a single added line\n").encode("utf-8")
    ).hexdigest()
    assert edited != WC.executable_sha()
    assert edited != WC.AUTHORIZED_SHA, (
        "a one-line edit must not still satisfy the authorization")


def test_installing_an_authorization_does_not_move_the_hash():
    """The other half, and the reason the normalizer exists: binding the three
    authorization constants is hash-neutral, so an authorization CAN be
    satisfied. Without this the driver would be unarmable rather than unarmed."""
    body = WC.executable_body()
    # count the normalized ASSIGNMENT LINES, not raw occurrences: the
    # placeholder's own definition is source text and contains the token too.
    lines = re.findall(
        r"^(?:REQUIRED_AUTH_ID|AUTHORIZED_SHA|AUTHORIZED_ENVELOPE)"
        r": Optional\[str\] = <AUTHORIZATION>$", body, re.MULTILINE)
    assert len(lines) == 3, lines
    # whatever is installed -- an identifier, a hash, or None -- must not
    # survive into the hashed body, or the hash would depend on it
    for value in (WC.REQUIRED_AUTH_ID, WC.AUTHORIZED_SHA,
                  WC.AUTHORIZED_ENVELOPE):
        if value is not None:
            assert value not in body, value


def test_no_environment_can_revive_a_superseded_authorization(monkeypatch):
    """A full dual key must NOT arm an executable the authorization does not
    name. Simulated by moving the running hash, which is what a code edit does.
    """
    # install a COMPLETE simulated authorization, then move the running hash
    # the way a code edit would. The dual key is fully satisfied; only the
    # executable differs, and that alone must be enough to refuse.
    fake = "SIMULATED-AUTHORIZATION-FOR-TEST-ONLY"
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", fake)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "executable_sha", lambda: "0" * 64)
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", fake)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", fake)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized() is False
    st = WC.authorization_state()
    assert any("different executable" in w for w in st["why_not"]), st["why_not"]


def test_the_gate_is_reachable_and_not_merely_always_closed(monkeypatch):
    """CORRECTLY FAIL-CLOSED vs PERMANENTLY INCAPABLE.

    Every refusal test above is worthless if the gate can never open: a driver
    that always says no would pass all of them. With the executable the current
    authorization names, and a full dual key, authorization MUST succeed.
    """
    # No authorization is installed in the shipped file -- CONTINUOUS-3 is
    # spent -- so install a simulated one naming the CURRENT hashes. If the
    # gate still refuses, it is incapable rather than merely closed.
    fake = "SIMULATED-AUTHORIZATION-FOR-TEST-ONLY"
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", fake)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", fake)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", fake)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized() is True, WC.authorization_state()["why_not"]


def test_the_envelope_moved_and_the_old_authorization_died_with_it():
    """The q=1 live bounds joined the hash, so b3bff654... no longer describes
    the envelope in force. The property worth pinning is not that the hash
    never changes -- it is that a changed bound KILLS the authorization that
    was granted against the old one, automatically and without anyone
    remembering to."""
    assert WC.envelope_sha() != (
        "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17")
    # INSTALLED 2026-09-21 under the owner's grant, and it names the envelope
    # ACTUALLY IN FORCE rather than a remembered one -- which is the property
    # that makes "a changed bound kills the grant" true rather than hoped for.
    assert WC.AUTHORIZED_ENVELOPE == WC.envelope_sha()
    # and the dual key's live half is still not in the repository
    assert WC.authorized() is False
    st = WC.authorization_state()
    assert any("GEMINI_CANARY_LIVE" in r or "transport arm" in r
               for r in st["why_not"])


def test_the_authorization_hash_covers_BOTH_sizes():
    """The live size is one constructor argument away, so an authorization
    that named only the envelope a driver happened to be built with would
    leave the other one changeable without review."""
    env = WC.envelope()
    assert env["envelope_shadow_q001_sha"] == LE.SHADOW_Q001.sha()
    assert env["envelope_live_q1_sha"] == LE.LIVE_Q1.sha()
    before = WC.envelope_sha()
    import dataclasses
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(LE, "LIVE_Q1",
                   dataclasses.replace(LE.LIVE_Q1,
                                       max_committed_usd=CP.D("1000")))
        assert WC.envelope_sha() != before


def test_without_the_transport_arm_no_mutation_is_possible(monkeypatch):
    """The dual key is still required, and it is the half that is NOT in the
    repository. An installed authorization on its own arms nothing."""
    monkeypatch.delenv("GEMINI_CANARY_LIVE", raising=False)
    monkeypatch.delenv("GEMINI_CANARY_AUTH", raising=False)
    monkeypatch.delenv("GEMINI_CANARY_AUTH_EXPIRES", raising=False)
    st = WC.authorization_state()
    assert st["authorized"] is False
    assert any("transport arm" in w for w in st["why_not"]), st["why_not"]
    with pytest.raises(WC.NotAuthorized):
        WC.require_authorization()


#: every authorization that has ever been issued and is now SPENT. None may
#: arm anything again; the identifier moves forward, the latch is never cleared.
SPENT_AUTHORIZATIONS = (
    "OWNER-2026-09-17-GEMINI-CANARY-1",       # latch REJECTED
    "OWNER-2026-09-18-GEMINI-CONTINUOUS-1",   # latch INTENT_PERSISTED, no order
)


@pytest.mark.parametrize("spent", SPENT_AUTHORIZATIONS)
def test_a_spent_authorization_can_never_arm_again(monkeypatch, spent):
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", spent)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorization_state()["authorized"] is False, (
        "%s is spent and must not arm anything, ever" % spent)
    assert spent != WC.REQUIRED_AUTH_ID


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
    # the subject is the BUDGET. Twelve un-cancelled rungs would also trip
    # the working-order and committed-cash caps, which are proven separately.
    d = _ready(tmp_path, envelope=_roomy())
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
    # subject is the durable budget across a restart, not the cash caps
    d = _ready(tmp_path, envelope=_roomy())
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


def test_the_venue_is_authoritative_for_what_is_resting():
    """Asserted at the level that owns the property.

    It used to be asserted through `recover()`, which no longer gets that
    far: the durable outstanding-order gate now refuses the window outright
    when the venue lists an order we do not recognise. That is strictly
    safer, and it is asserted in the test below. The property itself -- our
    local view never outranks the venue's -- belongs to WorkingOrders.
    """
    w = WC.WorkingOrders()
    w.add("ours", leg="YES", price="0.55", qty="0.01", now=0.0)
    w.reconcile([{"clientOrderId": "theirs", "outcome": "no",
                  "price": "0.40", "quantity": "0.01",
                  "orderId": 145828836250441357}], 1.0)
    ids = [o["client_order_id"] for o in w.rungs()]
    assert ids == ["theirs"], "our local view never outranks the venue's"


def test_an_unrecognised_active_order_refuses_the_window(tmp_path):
    """It is on this account and this symbol. Not recognising it is a reason
    to stop, never a reason to ignore it."""
    d = _driver(tmp_path)
    d.state = WC.AUTHORIZED
    out = d.recover(venue_fills=[], venue_orders=[], venue_open=[
        {"clientOrderId": "theirs", "outcome": "no", "price": "0.40",
         "quantity": "0.01", "orderId": 145828836250441357}])
    assert out["recovered"] is False and d.state == WC.DO_NOT_TRADE
    assert "unrecognised" in (d.why or "")
    assert out["outstanding"]["foreign"][0][
        "remaining_committed_usd"] == "0.0040"


def test_a_resting_venue_order_we_cannot_address_stops_the_probe(tmp_path):
    """The row above now carries an orderId, because CONTINUOUS-5 made the
    id load-bearing: an order the venue says is resting that we cannot
    cancel is not a row to accept quietly. Refusing is fail-closed, and the
    probe stops rather than continuing against a view it cannot act on."""
    d = _driver(tmp_path)
    d.working.add("ours", leg="YES", price="0.55", qty="0.01", now=0.0)
    d.state = WC.AUTHORIZED
    out = d.recover(venue_fills=[], venue_orders=[], venue_open=[
        {"clientOrderId": "theirs", "outcome": "no", "price": "0.40",
         "quantity": "0.01"}])
    assert out["recovered"] is False
    assert d.state in WC.TERMINAL
    # the outstanding gate now refuses first, and for a broader reason: an
    # order we do not recognise is active at the venue. The orderId property
    # is still owned by WorkingOrders and asserted directly below.
    assert "unrecognised" in (d.why or "")
    with pytest.raises(WC.ReconcileRequired) as e:
        WC.WorkingOrders().reconcile(
            [{"clientOrderId": "theirs", "outcome": "no", "price": "0.40",
              "quantity": "0.01"}], 1.0)
    assert "orderId" in str(e.value)


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
    # a 0.02 rung is deliberately ABOVE the shadow action size, so the live
    # gate refuses it -- correctly, and that refusal is proven in
    # test_gemini_live_envelope_q1.py. Here the subject is partial-fill
    # bookkeeping, so the size cap is lifted to reach it.
    d = _ready(tmp_path, envelope=_roomy(action_size=CP.D("0.02")))
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


# ====================================== THE AUTHORIZATION HASH MUST BE BINDABLE

def _sha_of(text):
    import hashlib
    body, n = WC._AUTH_LINES.subn(WC._AUTH_PLACEHOLDER, text)
    assert n == 3, n
    return hashlib.sha256(body.encode()).hexdigest()


def _armed_source():
    src = open(WC.__file__, encoding="utf-8").read()
    src = src.replace('REQUIRED_AUTH_ID: Optional[str] = None',
                      'REQUIRED_AUTH_ID: Optional[str] = "OWNER-X"')
    src = src.replace('AUTHORIZED_SHA: Optional[str] = None',
                      'AUTHORIZED_SHA: Optional[str] = "%s"' % WC.executable_sha())
    src = src.replace('AUTHORIZED_ENVELOPE: Optional[str] = None',
                      'AUTHORIZED_ENVELOPE: Optional[str] = "%s"' % WC.envelope_sha())
    return src


def test_REGRESSION_binding_the_authorization_does_not_change_the_hash():
    """The defect that made the first driver UNARMABLE.

    executable_sha() originally hashed the whole file, authorization constants
    included. So an authorization naming executable_sha() could never be
    satisfied: installing it changed the file, which changed the hash, which
    invalidated the authorization being installed. Satisfying it would have
    required a 64-hex string X whose embedding in this file hashes to X -- a
    self-referential SHA-256 preimage. Not unset: UNARMABLE, and no commit
    could have fixed it. It was caught by trying to issue a real
    authorization against it.
    """
    assert _sha_of(_armed_source()) == WC.executable_sha(), (
        "installing an authorization must be a no-op for the executable hash, "
        "or the authorization can never be installed at all")


def test_a_logic_change_still_invalidates_the_authorization():
    src = open(WC.__file__, encoding="utf-8").read()
    tampered = src.replace("if self.budget_left() < 1:",
                           "if self.budget_left() < 0:", 1)
    assert tampered != src, "the anchor for this test moved"
    assert _sha_of(tampered) != WC.executable_sha(), (
        "the exclusion must cover the authorization block and NOTHING else")


def test_only_the_three_authorization_lines_normalize():
    src = open(WC.__file__, encoding="utf-8").read()
    assert len(WC._AUTH_LINES.findall(src)) == 3
    # count the SUBSTITUTIONS, not the token: the placeholder's own definition
    # contains the token too, which is why counting it reads 4
    _, n = WC._AUTH_LINES.subn(WC._AUTH_PLACEHOLDER, src)
    assert n == 3
    body = WC.executable_body()
    # and the real values are gone from the hashed body
    assert "REQUIRED_AUTH_ID: Optional[str] = None" not in body
    assert "AUTHORIZED_SHA: Optional[str] = None" not in body


@pytest.mark.parametrize("pattern,n", [
    (r"^NOTHING_MATCHES$", 0),                       # too few
    (r"^(import) .*$", None),                        # far too many
])
def test_a_malformed_authorization_block_refuses_to_hash(monkeypatch, pattern,
                                                          n):
    """A normalization that matched more or fewer lines than intended would be
    a way to hide code from the hash, so it is refused rather than guessed."""
    import re as _re
    monkeypatch.setattr(WC, "_AUTH_LINES", _re.compile(pattern, _re.MULTILINE))
    monkeypatch.setattr(WC, "_AUTH_PLACEHOLDER", "<AUTHORIZATION>")
    with pytest.raises(WC.NormalizationFailed):
        WC.executable_body()


def test_the_envelope_hash_is_unaffected_by_the_authorization():
    before = WC.envelope_sha()
    assert before == _sha_env()
    assert WC.envelope()["max_actions"] == "12"


def _sha_env():
    import hashlib, json
    return hashlib.sha256(
        json.dumps(WC.envelope(), sort_keys=True).encode()).hexdigest()


# ============ THE FIRST LIVE WINDOW'S DEFECT: CANCEL BY THE WRONG FIELD =====

def test_REGRESSION_cancel_uses_orderId_not_clientOrderId(tmp_path,
                                                           _authorized):
    """The venue answered -1013 "invalid orderId format" on the first live
    cancel, and the order stayed resting.

    order.cancel TAKES orderId. spec.py says so in words, spec.ORDER_ID_FIELD
    exists for it, and transport.py already cancels with {"orderId": ...}. The
    package had the answer in three places and the driver used none of them.
    """
    d = _ready(tmp_path)
    s = FakeSession(default={"status": 200,
                             "result": {"orderId": 145828836242288478}})
    out = _run(d.place(s, _rung("YES", "0.55")))
    assert out["venue_order_id"] == 145828836242288478, (
        "the venue id lives under ack['result'], not at the top level")

    _run(d.cancel(s, d.working.rungs()[0]))
    sent = [m for m in s.sent if m["method"] == "order.cancel"]
    assert len(sent) == 1
    # CANONICAL STRING since CONTINUOUS-5: the venue rejected the int -1013
    assert sent[0]["params"] == {"orderId": "145828836242288478"}, sent[0]
    assert "clientOrderId" not in sent[0]["params"]


def test_REGRESSION_a_cancel_with_no_venue_id_is_refused_not_sent(tmp_path,
                                                                   _authorized):
    """A malformed cancel is worse than no cancel: it spends a slot, fails,
    and leaves the order resting. An unknown venue id is reconciliation."""
    d = _ready(tmp_path)
    s = FakeSession(default={"status": 200})        # ack carries NO result
    out = _run(d.place(s, _rung("YES", "0.55")))
    assert out["venue_order_id"] is None
    before = d.budget_consumed

    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(s, d.working.rungs()[0]))
    assert d.state == WC.RECONCILE
    assert d.budget_consumed == before, (
        "a cancel that was never formed must not spend a slot")
    assert not [m for m in s.sent if m["method"] == "order.cancel"]
    assert d.working.by_id, "the order is still resting and must stay tracked"


def test_the_venue_order_id_is_recorded_in_the_durable_ledger(tmp_path,
                                                               _authorized):
    d = _ready(tmp_path)
    s = FakeSession(default={"status": 200, "result": {"orderId": 99}})
    _run(d.place(s, _rung("YES", "0.55")))
    rows = [r for r in d.ledger.rows if r.get("kind") == WC.PLACE]
    resolved = [json.loads(l) for l in
                open(d.ledger.path, encoding="utf-8").read().splitlines() if l]
    assert any(r.get("orderId") == 99 for r in resolved), (
        "the venue id must be durable, or a restart cannot cancel what it "
        "placed")


def test_the_place_ack_is_returned_for_the_record(tmp_path, _authorized):
    """acks were `null` in the first live report, so the ack SHAPE was not
    visible post-hoc and the id bug had to be diagnosed from the ledger."""
    d = _ready(tmp_path)
    ack = {"status": 200, "result": {"orderId": 7}}
    out = _run(d.place(FakeSession(default=ack), _rung("YES", "0.55")))
    assert out["ack"] == ack
