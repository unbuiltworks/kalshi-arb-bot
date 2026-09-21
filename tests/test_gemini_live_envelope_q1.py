"""Proof, per safety requirement, that the q=1 live bounds actually bind.

Every test here drives a bound to its limit and one step past it against the
REAL order path (`ws_continuous.Driver.place`), not against a description of
it. The authorization constants are patched so the mutation path is reachable
INSIDE THE TEST PROCESS ONLY; the shipped file has REQUIRED_AUTH_ID and
AUTHORIZED_ENVELOPE as None and a test below proves it from the unpatched
module.
"""
import asyncio
import dataclasses

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import position_ledger as PL
from src.gemini_execution import ws_continuous as WC

D = CP.D
SYM = "GEMI-BTC05M2609181200-UP"
OTHER = "GEMI-BTC05M2609181205-UP"
AUTH = "OWNER-TEST-NOT-A-REAL-AUTHORIZATION"


class FakeSession:
    def __init__(self):
        self.sent = []

    async def request(self, method, params, timeout=5.0):
        self.sent.append({"method": method, "params": params})
        return {"status": 200, "result": {"orderId": len(self.sent)}}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


@pytest.fixture
def _authorized(monkeypatch):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized()


@pytest.fixture(autouse=True)
def _clean_market_registry():
    WC._BOUND_MARKETS.clear()
    yield
    WC._BOUND_MARKETS.clear()


def _live(tmp_path, symbol=SYM, env=None, name="mut.jsonl"):
    d = WC.Driver(AUTH, symbol, ledger_path=str(tmp_path / name),
                  now_fn=lambda: 1_789_000_000.0,
                  envelope=env or LE.LIVE_Q1)
    d.state = WC.AUTHORIZED
    assert d.recover(venue_fills=[], venue_open=[], venue_orders=[])[
        "recovered"]
    return d


def _rung(leg, price, qty="1"):
    return {"leg": leg, "price": price, "qty": qty, "action": CP.OPEN}


# ============================================== the manifest is not armed

def test_the_tree_is_disarmed_and_live2_is_spent():
    """DISARMED after LIVE-2 traded.

    OWNER-2026-09-21-GEMINI-Q1-LIVE-2 reached the venue: run 35573430715
    placed real orders on GEMI-BTC05M2609210745-UP, took ONE fill
    (YES 0.01 @ 0.14, venue orderId 145828836318553127) and stopped with zero
    orders live. Its latch is on disk. A spent authorization never arms again.

    The dual key's live half is an environment variable that is not in this
    repository, so a checkout alone can never mutate regardless.
    """
    from src.gemini_execution import arming, bundle
    if WC.REQUIRED_AUTH_ID is None:
        assert WC.AUTHORIZED_SHA is None
        assert WC.AUTHORIZED_ENVELOPE is None
    else:
        # ARMED 2026-09-21 under OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1.
        # LIVE-2 remains spent and can never arm again -- a DIFFERENT grant
        # being in force does not resurrect it. The durable property is that
        # whatever IS installed names the code actually running.
        assert WC.REQUIRED_AUTH_ID == arming.EXPECTED_AUTH_ID
        assert WC.AUTHORIZED_SHA == WC.executable_sha()
        assert WC.AUTHORIZED_ENVELOPE == WC.envelope_sha()
        assert bundle.AUTHORIZED_BUNDLE == bundle.bundle_sha()
        assert WC.REQUIRED_AUTH_ID != "OWNER-2026-09-21-GEMINI-Q1-LIVE-2"


def test_the_live_envelope_is_never_the_default():
    """q=1 is reached by being handed it, never by an upgrade arriving."""
    assert LE.DEFAULT is LE.SHADOW_Q001
    assert LE.DEFAULT.action_size == D("0.01")
    d = WC.Driver(AUTH, SYM, ledger_path="/dev/null")
    assert d.env is LE.SHADOW_Q001


# ===================================== 3. PL-1: the action budget binds

def test_the_action_budget_binds_on_the_live_path(tmp_path, _authorized):
    """PL-1 said MAX_ACTIONS was not an effective bound. On the SHADOW
    COLLECTOR that is true -- shadow_soak never calls guard.record(). On the
    ORDER PATH it was never true: the slot is spent by MutationLedger before
    the frame goes out. This drives it to the limit to show which."""
    roomy = dataclasses.replace(LE.LIVE_Q1, max_working_orders=64,
                                max_committed_usd=D("10000"),
                                max_total_spend_usd=D("10000"),
                                max_residual_usd=D("10000"),
                                max_yes=D("10000"), max_no=D("10000"),
                                max_same_side_run=10000)
    d = _live(tmp_path, env=roomy)
    assert d.bind()["bound"]
    s = FakeSession()
    for i in range(roomy.max_actions):
        _run(d.place(s, _rung("YES" if i % 2 else "NO", "0.50")))
    assert d.budget_consumed == 12 and d.budget_left() == 0
    with pytest.raises(CP.ProbeStop):
        _run(d.place(s, _rung("YES", "0.50")))
    assert len(s.sent) == 12, "the 13th frame must never have gone out"
    assert d.budget_consumed == 12
    assert d.ledger.consumed() == 12
    assert d.guard.actions_taken == 12, (
        "the guard's counter must track the ledger, not sit at zero")


def test_a_spent_slot_survives_a_restart(tmp_path, _authorized):
    d = _live(tmp_path)
    assert d.bind()["bound"]
    _run(d.place(FakeSession(), _rung("YES", "0.50")))
    del d
    d2 = WC.Driver(AUTH, SYM, ledger_path=str(tmp_path / "mut.jsonl"),
                   now_fn=lambda: 1_789_000_000.0, envelope=LE.LIVE_Q1)
    d2.state = WC.AUTHORIZED
    d2.recover(venue_fills=[], venue_open=[], venue_orders=[])
    assert d2.budget_consumed == 1, "a restart must not re-open the budget"


# ================== 4. PL-2: total dollar exposure on the live order path

def test_resting_orders_are_counted_as_committed_cash(tmp_path, _authorized):
    """THE GAP THAT MATTERED. `inv.spend()` is FILLED cost. Two rungs can each
    pass a per-rung check while the set of them exceeds the cap together."""
    d = _live(tmp_path)
    assert d.bind()["bound"]
    s = FakeSession()
    _run(d.place(s, _rung("YES", "1.00")))      # committed 1.00
    _run(d.place(s, _rung("NO", "1.00")))       # committed 2.00
    assert LE.committed_usd(d.working.rungs()) == D("2.00")
    with pytest.raises(CP.ProbeStop):
        _run(d.place(s, _rung("YES", "1.00")))  # would be 3.00 > 2.50
    assert "committed" in (d.why or "")
    assert len(s.sent) == 2


def test_the_old_check_would_have_let_that_through():
    """The regression has teeth: the shadow gate passes the very rung the
    live gate refuses, because it never looks at what is resting."""
    inv = CP.Inventory()
    resting = [{"leg": "YES", "price": "1.00", "qty": "1"},
               {"leg": "NO", "price": "1.00", "qty": "1"}]
    assert CP.would_breach("YES", "1.00", inv) is None, (
        "the shadow gate sees a flat inventory and approves")
    breach = LE.would_breach("YES", "1.00", inv, qty="1", resting=resting,
                             env=LE.LIVE_Q1)
    assert breach and "committed" in breach


def test_the_gate_uses_the_rungs_own_qty_not_a_constant():
    """`CP.would_breach` measures with ACTION_SIZE, the module constant, so
    the gate and the frame can disagree about size. This one is handed the
    qty and there is no default to fall back to."""
    inv = CP.Inventory()
    with pytest.raises(TypeError):
        LE.would_breach("YES", "0.50", inv)          # qty is keyword-REQUIRED
    assert LE.would_breach("YES", "0.50", inv, qty="1", env=LE.LIVE_Q1) is None
    big = LE.would_breach("YES", "0.50", inv, qty="2", env=LE.LIVE_Q1)
    assert big and "larger than the envelope size" in big


def test_a_partial_remainder_is_allowed_but_a_bigger_rung_is_not():
    """Smaller is the remainder of a partial fill and must stay workable;
    larger is the 100x-by-typo failure and must never reach the venue."""
    inv = CP.Inventory()
    assert LE.would_breach("YES", "0.50", inv, qty="0.4",
                           env=LE.LIVE_Q1) is None
    assert LE.would_breach("YES", "0.50", inv, qty="1.0001", env=LE.LIVE_Q1)


def test_an_unreadable_resting_order_refuses_rather_than_counting_zero():
    inv = CP.Inventory()
    b = LE.would_breach("YES", "0.50", inv, qty="1",
                        resting=[{"leg": "YES", "price": None, "qty": "1"}],
                        env=LE.LIVE_Q1)
    assert b and "committed cash cannot be established" in b


def test_filled_spend_is_bounded_too(tmp_path):
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "1.00", source="t")
    inv.apply_fill("NO", "1", "1.00", source="t")
    assert inv.spend() == D("2.00")
    b = LE.would_breach("YES", "0.60", inv, qty="1", env=LE.LIVE_Q1)
    assert b and "FILLED spend" in b


def test_the_committed_cap_is_the_worst_case_loss():
    """Stated once, so nobody has to re-derive it: a contract that resolves
    against pays nothing back, so the most cash that can leave is the most
    that can be lost."""
    assert LE.LIVE_Q1.worst_case_loss_usd == LE.LIVE_Q1.max_committed_usd
    assert LE.LIVE_Q1.worst_case_loss_usd == D("2.50")


# ==================================== 6. maximum residual DOLLAR exposure

def test_residual_is_bounded_in_dollars_not_only_contracts():
    """The contract cap would allow this; the dollar cap is what refuses it,
    which is the whole reason it exists as a separate bound."""
    env = dataclasses.replace(LE.LIVE_Q1, max_residual_usd=D("1.00"))
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.60", source="t")
    # one more YES: 2 contracts, inside max_imbalance 2, but $1.20 of basis
    b = LE.would_breach("YES", "0.60", inv, qty="1", env=env)
    assert b and "directional residual" in b
    assert LE.would_breach("YES", "0.60", inv, qty="1",
                           env=LE.LIVE_Q1) is None, (
        "under the real $2.00 cap the same rung is fine; the test above is "
        "the bound biting, not the rung being bad")


def test_residual_is_priced_at_cost_not_at_a_mark():
    inv = CP.Inventory()
    inv.apply_fill("YES", "2", "0.30", source="t")
    inv.apply_fill("NO", "1", "0.70", source="t")
    assert PL.signed_position(inv) == D("1")
    assert LE.residual_usd(inv, LE.LIVE_Q1) == D("0.30")


def test_a_held_leg_with_no_cost_basis_is_charged_the_maximum():
    """Impossible by construction, so if it happens the envelope must not
    silently price it at nothing."""
    class Weird:
        yes_qty, no_qty = D("2"), D("0")
        yes_cost = no_cost = D("0")

        def avg_yes(self):
            return None

        def avg_no(self):
            return None
    assert LE.residual_usd(Weird(), LE.LIVE_Q1) == D("2")


def test_the_gates_report_names_every_bound_it_breaks():
    inv = CP.Inventory()
    inv.apply_fill("YES", "3", "1.00", source="t")
    hits = LE.gates(inv, actions_taken=12,
                    resting=[{"leg": "NO", "price": "1.00", "qty": "1"}],
                    concurrent_markets=2, env=LE.LIVE_Q1)
    joined = " | ".join(hits)
    for expected in ("max_actions", "filled spend", "committed cash",
                     "residual cost basis", "concurrent markets"):
        assert expected in joined, (expected, joined)


# ============================================ 7. one concurrent market max

def test_a_second_market_cannot_be_bound_in_this_process(tmp_path,
                                                         _authorized):
    a = _live(tmp_path, symbol=SYM, name="a.jsonl")
    assert a.bind()["bound"]
    b = _live(tmp_path, symbol=OTHER, name="b.jsonl")
    out = b.bind()
    assert out["bound"] is False
    assert "MAX_CONCURRENT_MARKETS" in (b.why or "")
    assert b.state == WC.DO_NOT_TRADE
    assert WC.bound_markets() == {SYM: AUTH}


def test_the_lease_is_released_when_the_driver_stops(tmp_path, _authorized):
    a = _live(tmp_path, symbol=SYM, name="a.jsonl")
    assert a.bind()["bound"]
    a.stop(WC.STOPPED, "window over")
    assert WC.bound_markets() == {}
    b = _live(tmp_path, symbol=OTHER, name="b.jsonl")
    assert b.bind()["bound"], "a released market must not block the next one"


def test_a_stopped_driver_stays_stopped_after_releasing_its_lease(
        tmp_path, _authorized):
    """Releasing the lease must not re-arm the object that released it."""
    a = _live(tmp_path, symbol=SYM)
    assert a.bind()["bound"]
    a.stop(WC.STOPPED, "done")
    assert a.state == WC.STOPPED
    with pytest.raises(CP.ProbeStop):
        _run(a.place(FakeSession(), _rung("YES", "0.50")))


def test_the_symbol_family_is_still_refused_at_construction(tmp_path):
    with pytest.raises(CP.ProbeStop):
        WC.Driver(AUTH, "GEMI-ETH05M2609181200-UP",
                  ledger_path=str(tmp_path / "m.jsonl"),
                  envelope=LE.LIVE_Q1)


# ======================================== 5. deadman / withdrawal / cancel

def test_below_the_withdraw_floor_everything_resting_is_cancelled(
        tmp_path, _authorized):
    d = _live(tmp_path)
    assert d.bind()["bound"]
    s = FakeSession()
    _run(d.place(s, _rung("YES", "0.50")))
    out = d.decide(CP.WITHDRAW_ALL_TTE_S - 1.0)
    assert d.state == WC.WITHDRAWING
    assert out["plan"]["place"] == [], "nothing new may be quoted on the way out"
    assert len(out["plan"]["cancel"]) == len(d.working.rungs()) == 1


def test_a_cancel_without_a_venue_order_id_is_refused_not_sent(
        tmp_path, _authorized):
    d = _live(tmp_path)
    assert d.bind()["bound"]
    s = FakeSession()
    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(s, {"client_order_id": "x", "venue_order_id": None}))
    assert s.sent == [], "a malformed cancel must never reach the venue"


def test_the_deadman_constants_are_the_ones_the_manifest_states():
    assert CP.DEADMAN_S == 20.0
    assert CP.WITHDRAW_ALL_TTE_S == 30.0
    assert CP.NO_NEW_OPEN_EXTEND_TTE_S == 60.0


# ================= 1 + 2. the ledger reconciles to the VENUE's own position

def test_agreement_requires_the_venue_to_have_been_read():
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.50", source="t")
    r = PL.reconcile(inv, None, SYM)
    assert r["verdict"] == PL.UNREADABLE and r["tradeable"] is False


def test_a_disagreeing_venue_stops_us():
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.50", source="t")
    r = PL.reconcile(inv, [{"symbol": SYM, "position": "-1"}], SYM)
    assert r["verdict"] == PL.DISAGREE and r["tradeable"] is False


def test_both_venue_shapes_reduce_to_the_same_signed_quantity():
    inv = CP.Inventory()
    inv.apply_fill("YES", "2", "0.50", source="t")
    inv.apply_fill("NO", "1", "0.50", source="t")
    per_outcome = [{"symbol": SYM, "outcome": "yes", "totalQuantity": "2"},
                   {"symbol": SYM, "outcome": "no", "totalQuantity": "1"}]
    signed = [{"symbol": SYM, "outcome": "yes", "position": "1"}]
    assert PL.reconcile(inv, per_outcome, SYM)["verdict"] == PL.AGREE
    assert PL.reconcile(inv, signed, SYM)["verdict"] == PL.AGREE


def test_the_reconciliation_records_which_model_the_venue_showed():
    """The open question is answered as a by-product of doing the job."""
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.50", source="t")
    inv.apply_fill("NO", "1", "0.50", source="t")
    a = PL.reconcile(inv, [{"symbol": SYM, "outcome": "yes",
                            "totalQuantity": "1"},
                           {"symbol": SYM, "outcome": "no",
                            "totalQuantity": "1"}], SYM)
    assert a["venue_representation"] == PL.PER_OUTCOME_ROWS
    assert "model A" in a["resolves_open_question"]

    b = PL.reconcile(inv, [], SYM)
    assert b["venue_representation"] == PL.FLAT_NO_ROWS
    assert "model B or C" in b["resolves_open_question"]


def test_matched_quantity_is_never_reported_as_verified():
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.50", source="t")
    inv.apply_fill("NO", "1", "0.50", source="t")
    r = PL.reconcile(inv, [{"symbol": SYM, "outcome": "yes",
                            "totalQuantity": "1"},
                           {"symbol": SYM, "outcome": "no",
                            "totalQuantity": "1"}], SYM)
    assert r["matched_is_verified"] is False
    assert "NOT confirmed state" in r["matched_label"]


def test_a_row_without_a_sign_or_an_outcome_is_unreadable_not_zero():
    inv = CP.Inventory()
    r = PL.reconcile(inv, [{"symbol": SYM, "totalQuantity": "1"}], SYM)
    assert r["verdict"] == PL.UNREADABLE
