"""An unresolved cost basis must stay unresolved, stay visible, and keep
live trading blocked -- while not costing us the shadow evidence from every
later independent window.

1900z: the venue itself reported net UP 1.0000 at 19:15:10Z and our gross
book agreed, so the QUANTITY is venue-confirmed. The market expired five
seconds later and positions cleared, and the venue exposes order detail only
at /v1/order/{id} (/v1/orders is 501, mytrades and trades are 404), so with
no discoverable id the COST is unobtainable and the possible loss stays
possible.
"""
from __future__ import annotations

import collections
import inspect
from decimal import Decimal as D

import pytest

from src import l5_maker_fill_model as FM
from src import l5_pmus_live_runner as RUN

UNRESOLVED = "cpc-btc-updown-15m-2026-10-12-1900z"
OTHER = "cpc-btc-updown-15m-2026-10-12-2000z"

#: 1900z was RESOLVED at 21:23:24Z from order D294PDYNPZ7Z: avgPx 0.1800,
#: cumQuantity 1, commission 0.0000, PASSIVE fill, and a POSITION_RESOLUTION
#: whose own realized line is -0.1800. Its registry row is kept but no longer
#: stands.
#:
#: THE PROTECTIONS STILL HAVE TO BE TESTED, so the tests that exercise them
#: inject a STANDING entry of their own instead of leaning on 1900z. Deleting
#: those tests along with the case they were written for would quietly retire
#: the deletion hole, the empty-ledger hole and the unreadable-ledger hole --
#: every one of which was found the hard way.
STANDING = {
    "id": "synthetic-standing-unresolved",
    "market": UNRESOLVED,
    "ledger": "l5-live-20261012z",
    "side": "UP",
    "qty": "1.0000",
    "cost_basis": "UNKNOWN",
    "discovered_utc": "2026-10-12T19:15:30Z",
    "venue_confirmed_quantity": True,
    "venue_evidence": "synthetic, for the protection tests",
    "missing_evidence": ("the venue order id. /v1/orders is 501 and order "
                         "detail exists only at /v1/order/{id}."),
    "may_not": ("book a cost basis from the balance delta, or zero the "
                "venue-confirmed quantity."),
}


@pytest.fixture
def standing(monkeypatch):
    """One STANDING unresolved entry, so the blocks stay under test."""
    monkeypatch.setattr(RUN, "UNRESOLVED_ACCOUNTING", [dict(STANDING)])
    return STANDING


# ------------------------------------------------- it cannot be hidden

def test_the_1900z_record_is_resolved_and_still_visible():
    """Resolved by the evidence it itself named, and never deleted."""
    assert [x for x in RUN.unresolved_accounting()
            if x["market"] == UNRESOLVED] == [], (
        "1900z no longer stands: the order row answered it")
    r = [x for x in RUN.resolved_accounting() if x["market"] == UNRESOLVED]
    assert len(r) == 1, "the closed case must stay on the books"
    r = r[0]
    assert r["cost_basis"] == "UNKNOWN", (
        "the record of what was unknown AT THE TIME is not rewritten")
    assert r["qty"] == "1.0000" and r["venue_confirmed_quantity"] is True
    assert r["resolved_utc"] == "2026-10-12T21:23:24Z"
    assert "D294PDYNPZ7Z" in r["resolved_evidence"]
    assert "0.1800" in r["resolved_evidence"]
    assert "balance" in r["resolved_not_by"]


def test_the_resolution_is_the_evidence_the_record_asked_for():
    r = [x for x in RUN.resolved_accounting()
         if x["market"] == UNRESOLVED][0]
    assert "order id" in r["missing_evidence"], "what was missing"
    assert "/v1/order" in r["resolved_evidence"], "what supplied it"
    # and what was forbidden was still forbidden when it was answered
    assert "balance delta" in r["may_not"] and "zero" in r["may_not"]


def test_the_cost_is_booked_from_the_order_row_and_not_the_balance():
    rep = [r for r in RUN.LEDGER_REPAIRS
           if r["market"] == UNRESOLVED]
    assert len(rep) == 1
    rep = rep[0]
    assert rep["qty"] == D("1.00") and rep["cost"] == D("0.18000")
    assert rep["expect_cost"] == D("0"), "it repairs a cost of ZERO"
    assert rep["expect_qty"] == D("1.0000")
    ev = rep["evidence"]
    assert ev["orders"] == ["D294PDYNPZ7Z"]
    assert "avgPx 0.1800" in ev["detail"]
    assert "ORDER_STATE_FILLED" in ev["detail"]
    assert "aggressor false" in ev["fill_role"]
    assert "settlementPx 0.0000" in ev["settlement"]
    assert "balance" in ev["not_used_as_evidence"]


def test_a_standing_entry_states_the_missing_evidence_and_the_forbidden(
        standing):
    r = RUN.unresolved_accounting()[0]
    assert "order id" in r["missing_evidence"]
    assert "501" in r["missing_evidence"]
    assert "balance delta" in r["may_not"]
    assert "zero" in r["may_not"]


def test_it_is_declared_in_code_not_derived_from_state():
    """orders_uncertain is recomputed every reconcile, so a book that
    stopped failing coherence would silently clear it. This must not be
    derivable from runtime state at all."""
    src = inspect.getsource(RUN.unresolved_accounting)
    assert "self" not in src and "state" not in src


def test_a_caller_cannot_mutate_the_registry_through_the_accessor(standing):
    RUN.unresolved_accounting()[0]["cost_basis"] = "0.18"
    assert RUN.unresolved_accounting()[0]["cost_basis"] == "UNKNOWN"


def test_resolving_requires_a_timestamp_in_code_not_a_runtime_value():
    """There is no flag, env var or state that lifts a standing entry."""
    src = inspect.getsource(RUN.unresolved_accounting)
    assert "resolved_utc" in src
    for forbidden in ("os.getenv", "environ", "self", "store"):
        assert forbidden not in src, forbidden


def test_the_heartbeat_always_reports_it():
    src = inspect.getsource(RUN.Runner.heartbeat)
    assert "unresolved_accounting" in src
    assert "blocks_live_authorization" in src


# ------------------------------------------------- live stays blocked

class _T:
    market_slug = OTHER
    armed = False

    def __init__(self):
        self.arms = 0

    def arm(self):
        self.arms += 1
        return {"armed": True}


class _ArmStore:
    """A ledger that may or may not hold the unresolved row."""

    def __init__(self, positions=None, violations=None, uncertain=None,
                 path="/data/l5-live-20261012z/inventory.json"):
        self.path = path
        self.state = {"positions": positions or {}}
        self._v = violations or {}
        self._u = uncertain

    def orders_uncertain(self):
        return self._u

    def book_violations(self, m):
        return self._v.get(m)


@pytest.fixture(autouse=True)
def _boundary_satisfied(monkeypatch, tmp_path, request):
    """Satisfy the shared arming boundary for the tests that actually arm.

    NOT applied to the meta-tests that assert the shadow lock's own default
    and the graft closure: setting L5_SHADOW_ONLY=0 here broke
    `test_the_shadow_lock_is_untouched`, which exists precisely to pin that
    the lock defaults ON. A fixture that silently defeats the assertion it is
    sharing a file with is worse than no fixture.
    """
    if request.node.name in ("test_the_shadow_lock_is_untouched",
                             "test_no_gate_value_changed",
                             "test_try_arm_reads_the_module_level_helper"):
        return
    from src import l5_live_authorization as AUTH
    from src import pmus_auth as Q1
    sid = "unresolved-accounting-session"
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.delenv(AUTH.MARKET_ENV, raising=False)
    monkeypatch.delenv(AUTH.NOT_AFTER_ENV, raising=False)
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "test-auth-holder")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "test-auth-holder")
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)


class _Limits:
    def as_dict(self):
        return {"max_usd_per_market": "5", "max_total_exposure_usd": "5",
                "daily_realized_loss_halt_usd": "5"}


class _Executor:
    limits = _Limits()


class _Runner:
    try_arm = RUN.Runner.try_arm

    def __init__(self, store):
        self.transport = _T()
        self.now_t = 1000.0
        self.win = collections.Counter()
        self.reconciled = True
        self.store = store
        # `try_arm` now goes through the one shared arming boundary, which
        # needs a recorded preflight and a capital envelope. This file pins
        # down the UNRESOLVED-ACCOUNTING registry, not authorization, so the
        # stub satisfies the rest; the boundary's own refusals are covered in
        # tests/test_l5_arming_boundary.py.
        self.preflight_status = "PASSED"
        self.executor = _Executor()

    def halted(self):
        return False

    def reconcile_if_needed(self, now):
        pass


UNRESOLVED_ROW = {"%s|UP" % UNRESOLVED: "1.0000"}
VIOLATION = {UNRESOLVED: [{"violation": "QUANTITY_WITHOUT_COST"}]}


def test_the_primary_block_is_the_pre_existing_uncertainty_refusal():
    """orders_uncertain already refuses while the row is incoherent --
    proven in production, arm_refused_orders_uncertain hit 221 in 1715z.
    The registry does NOT duplicate it."""
    r = _Runner(_ArmStore(UNRESOLVED_ROW, VIOLATION,
                          uncertain="incoherent book"))
    r.try_arm()
    assert r.transport.arms == 0
    assert r.win["arm_refused_orders_uncertain"] == 1
    assert r.win["arm_refused_unresolved_accounting"] == 0


def test_a_silently_cleared_unresolved_row_refuses_arming(standing, monkeypatch):
    """The one case orders_uncertain cannot notice: the ledger still holds
    the market but has stopped reporting a violation, with no evidence
    supplied. That is a discrepancy, not an all-clear."""
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, p: seen.append((k, p)))
    r = _Runner(_ArmStore(UNRESOLVED_ROW, violations={}, uncertain=None))
    r.try_arm()
    assert r.transport.arms == 0, "armed with the loss unaccounted"
    assert r.win["arm_refused_unresolved_accounting"] == 1
    why = [p for k, p in seen if k == "L5_LIVE_DISARMED"][0]
    assert "silently cleared" in why["why"]
    assert why["discrepancies"][0]["market"] == UNRESOLVED


def test_an_unrelated_ledger_is_not_blocked():
    """A registry entry must not make the arming path unreachable on a book
    it was never about -- which would turn arming into untestable dead
    code. This is the defect the arm-retry suite caught. Scoped by LEDGER,
    so a different ledger is free."""
    r = _Runner(_ArmStore(positions={"%s|UP" % OTHER: "1"},
                          path="/data/l5-live-OTHERDAY/inventory.json"))
    r.try_arm()
    assert r.transport.arms == 1
    assert r.win["arm_refused_unresolved_accounting"] == 0


def test_a_store_with_no_path_is_not_blocked():
    """The arm-retry stub has no path. InventoryStore declares one, so that
    is a partial stub rather than an undeclared attribute."""
    r = _Runner(_ArmStore(positions={}, path=""))
    r.try_arm()
    assert r.transport.arms == 1


# ---- THE DELETION HOLE. Scoping to the row's PRESENCE meant deleting it
# removed the protection. Scoping to the LEDGER closes that.

def test_DELETING_the_unresolved_row_still_refuses_arming(standing, monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, p: seen.append((k, p)))
    r = _Runner(_ArmStore(positions={}, violations={}, uncertain=None))
    r.try_arm()
    assert r.transport.arms == 0, "a deleted row restored live authorization"
    assert r.win["arm_refused_unresolved_accounting"] == 1
    d = [p for k, p in seen if k == "L5_LIVE_DISARMED"][0]["discrepancies"][0]
    assert d["now"] == "row ABSENT from the ledger"
    assert "not evidence it was resolved" in d["labels"]


def test_an_entirely_empty_ledger_still_refuses_arming(standing):
    """Losing the whole ledger file must not read as all-clear."""
    r = _Runner(_ArmStore(positions={}))
    r.try_arm()
    assert r.transport.arms == 0


def test_an_unreadable_named_ledger_fails_CLOSED(standing, monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)

    class Broken:
        path = "/data/l5-live-20261012z/inventory.json"
        state = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("gone")))

        def orders_uncertain(self):
            return None

        def book_violations(self, m):
            return None

    r = _Runner(Broken())
    r.try_arm()
    assert r.transport.arms == 0, "unreadable book concluded all-clear"
    assert r.win["arm_refused_unresolved_accounting"] == 1


def test_the_registry_is_visible_independently_of_the_ledger(standing):
    """It must be readable with no store at all."""
    assert RUN.unresolved_accounting()[0]["market"] == UNRESOLVED
    assert RUN.unresolved_accounting()[0]["ledger"] == "l5-live-20261012z"


def test_the_closed_case_is_also_visible_with_no_store_at_all():
    assert any(r["market"] == UNRESOLVED for r in RUN.resolved_accounting())


def test_the_detector_is_a_module_function_not_a_runner_method():
    """try_arm is grafted onto stubs in the arm-retry suite, so a new
    instance attribute is the duck-typing hazard the graft closure test
    exists to catch -- and it bit this change once already."""
    assert callable(RUN.unresolved_discrepancies)
    assert not hasattr(RUN.Runner, "unresolved_discrepancies")
    src = inspect.getsource(RUN.Runner.try_arm)
    assert "unresolved_discrepancies(self.store)" in src
    assert "orders_uncertain" in src


def test_the_shadow_lock_is_untouched():
    assert RUN.ShadowOnlyTransport.BLOCKED == ("submit",)
    assert RUN.shadow_only() is True


def test_no_gate_value_changed():
    from src import l5_live_validation_gates as VG
    g = VG.ValidationGates()
    assert (g.max_cash_deployed_usd, g.max_unmatched_contracts,
            g.max_cumulative_loss_usd) == (D("10"), D("5"), D("20"))
    assert g.cash_policy == VG.NO_RECYCLING


# ------------------------------------------------- observation is separated

class _Store:
    def __init__(self, violations=None):
        self._v = violations or {}

    def book_violations(self, m):
        return self._v.get(m)


class _R2:
    observable_despite_refusal = RUN.Runner.observable_despite_refusal

    def __init__(self, slug=OTHER, violations=None):
        self.model = FM.ModelSession(slug)
        self.store = _Store(violations)


REC = {"MAKER_TREATMENT": {"rested_at_l5_price": "0.52", "qty": "1.8"},
       "L5_NATIVE": {"side": "UP"}, "action_key": "ak-1"}


def test_an_independent_window_is_observable_despite_the_refusal():
    got = _R2().observable_despite_refusal(REC, "ORDER_STATE_UNCERTAIN")
    assert got is not None
    assert got["mode"] == "MAKER" and got["price"] == "0.52"


def test_the_unresolved_market_itself_is_never_observed():
    assert _R2(slug=UNRESOLVED).observable_despite_refusal(
        REC, "ORDER_STATE_UNCERTAIN") is None


def test_a_window_whose_own_book_is_incoherent_is_never_observed():
    r = _R2(violations={OTHER: [{"violation": "QUANTITY_WITHOUT_COST"}]})
    assert r.observable_despite_refusal(REC, "ORDER_STATE_UNCERTAIN") is None


@pytest.mark.parametrize("reason", [
    "MAKER_ONLY_NO_TAKER", "RESIDUAL_ABOVE_CAP", "HALT:halt_asserted",
    "ORDER_STATE_UNKNOWN", "TICK_SIZE_UNKNOWN", None, ""])
def test_no_other_refusal_is_ever_observed_past(reason):
    """Those are decisions about what THIS engine would do. Modelling past
    them would describe a different engine."""
    assert _R2().observable_despite_refusal(REC, reason) is None


def test_a_record_without_a_maker_translation_is_not_invented():
    assert _R2().observable_despite_refusal(
        {"L5_NATIVE": {"side": "UP"}}, "ORDER_STATE_UNCERTAIN") is None


def test_observation_cannot_reach_the_order_path():
    import ast
    tree = ast.parse(inspect.getsource(
        RUN.Runner.observable_despite_refusal).lstrip())
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for bad in ("submit", "arm", "cancel", "place"):
        assert bad not in called


def test_a_modelled_despite_refusal_row_is_labelled_as_such(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, p: seen.append((k, p)))

    class R3(_R2):
        model_place = RUN.Runner.model_place

        def __init__(self):
            super().__init__()
            self.stats = collections.Counter()
            self.model.ingest_frame(
                {"marketData": {"marketSlug": OTHER,
                                "bids": [{"px": {"value": "0.53"},
                                          "qty": "300"}],
                                "offers": [{"px": {"value": "0.54"},
                                            "qty": "400"}]}}, 1.0)

    r = R3()
    r.model_place(None, {"pmus": {"bbo": {"bid": "0.53", "ask": "0.54"}}},
                  9.0, rec=REC, refused_reason="ORDER_STATE_UNCERTAIN")
    rec = [p for k, p in seen if k == "L5_MODEL_INTENT"][0]
    assert rec["observed_despite_live_refusal"] is True
    assert rec["blocked_live_by"] == "ORDER_STATE_UNCERTAIN"
    assert "not a claim the engine would have traded" in rec["labels"]
    assert "MODELLED" in rec["labels"]


def test_the_real_ledger_is_never_touched_by_observation():
    """The model must remain absent from the execution module entirely."""
    from src import l5_live_execution as LX
    src = inspect.getsource(LX)
    for name in ("l5_maker_fill_model", "ModelSession", "HypoInventory",
                 "unresolved_accounting"):
        assert name not in src


# ===================================================== effective residual cap
# The model was wired to SafetyLimits.max_abs_residual (10) while the
# BINDING live constraint is ValidationGates.max_unmatched_contracts (5),
# because ValidationGates is consulted after SafetyLimits and can only ADD
# refusals. Modelled sequences were therefore LOOSER than live would permit.

class _Lim:
    def __init__(self, residual):
        self.max_abs_residual = residual


class _VG:
    def __init__(self, unmatched):
        self.max_unmatched_contracts = unmatched


class _Ex:
    def __init__(self, vg):
        self.validation_gates = vg


class _CapRunner:
    effective_residual_cap = RUN.Runner.effective_residual_cap

    def __init__(self, residual=D("10"), unmatched=D("5")):
        self.limits = _Lim(residual)
        self.executor = _Ex(_VG(unmatched)) if unmatched is not None else None


def test_the_model_takes_the_TIGHTER_of_the_two_limits():
    assert _CapRunner(D("10"), D("5")).effective_residual_cap() == D("5")


def test_it_still_takes_the_tighter_one_when_the_roles_reverse():
    """Derived, not hardcoded: if SafetyLimits were the tighter one it must
    win instead."""
    assert _CapRunner(D("3"), D("5")).effective_residual_cap() == D("3")


def test_it_is_not_hardcoded_to_five():
    assert _CapRunner(D("8"), D("7")).effective_residual_cap() == D("7")
    import inspect
    src = inspect.getsource(RUN.Runner.effective_residual_cap)
    assert 'D("5")' not in src and "min(caps)" in src


def test_it_falls_back_to_the_configured_gates_without_an_executor():
    r = _CapRunner(D("10"), None)
    # no executor -> reads validation_gates(), whose default unmatched is 5
    assert r.effective_residual_cap() == D("5")


def test_the_runner_wires_the_derived_cap_not_safetylimits():
    import inspect
    src = inspect.getsource(RUN.Runner.roll)
    assert "self.effective_residual_cap()" in src
    assert "max_abs_residual=self.limits.max_abs_residual" not in src


# ---- the model's own behaviour under the effective cap

def _sess(cap="5"):
    s = FM.ModelSession(OTHER, max_abs_residual=D(cap))
    s.ingest_frame({"marketData": {
        "marketSlug": OTHER,
        "bids": [{"px": {"value": "0.0600"}, "qty": "500"}],
        "offers": [{"px": {"value": "0.0700"}, "qty": "500"},
                   {"px": {"value": "0.0800"}, "qty": "500"}]}}, 100.0)
    return s


def _sweep_fill(s, side, own_px, qty, t):
    s.place(side, D(own_px), D(qty), t, D("0.06"), D("0.07"))
    wire = D(own_px) if side == FM.UP else D(1) - D(own_px)
    through = wire - D("0.01") if side == FM.UP else wire + D("0.01")
    s.advance([{"px": str(through), "qty": "99999",
                "dir": "SELL" if side == FM.UP else "BUY", "t": t + 1}])


def test_modelled_inventory_cannot_exceed_the_cap_over_a_SEQUENCE():
    s = _sess("5")
    for i in range(6):
        _sweep_fill(s, FM.DOWN, "0.92", "1.8", 10.0 + i * 10)
    for sc in FM.SCENARIOS:
        assert abs(s.inv[sc].residual(OTHER)) <= D("5"), (
            "%s reached %s" % (sc, s.inv[sc].residual(OTHER)))


def test_suppressed_fills_are_counted_and_reported():
    s = _sess("5")
    for i in range(6):
        _sweep_fill(s, FM.DOWN, "0.92", "1.8", 10.0 + i * 10)
    rep = s.close(up_won=False, wire_at_close=D("0.01"))
    assert rep["max_abs_residual"] == "5"
    assert s.suppressed_by_residual_cap[FM.OPTIMISTIC] >= 1
    assert rep["scenarios"][FM.OPTIMISTIC]["suppressed_by_residual_cap"] >= 1


def test_inventory_REDUCING_fills_remain_permitted_at_the_cap():
    """The deployed rule exactly: a leg that shrinks |residual| is never
    refused, at any depth -- otherwise a capped book could never unwind."""
    s = _sess("2")
    _sweep_fill(s, FM.DOWN, "0.92", "2.0", 10.0)
    assert s.inv[FM.OPTIMISTIC].residual(OTHER) == D("-2.0")
    before = s.suppressed_by_residual_cap[FM.OPTIMISTIC]
    _sweep_fill(s, FM.UP, "0.06", "2.0", 30.0)      # closes the imbalance
    assert s.inv[FM.OPTIMISTIC].residual(OTHER) == D("0")
    assert s.suppressed_by_residual_cap[FM.OPTIMISTIC] == before


def test_the_cap_can_only_suppress_never_create_a_fill():
    tight, loose = _sess("5"), _sess("10")
    for i in range(6):
        _sweep_fill(tight, FM.DOWN, "0.92", "1.8", 10.0 + i * 10)
        _sweep_fill(loose, FM.DOWN, "0.92", "1.8", 10.0 + i * 10)
    a = tight.close(up_won=False)["scenarios"][FM.OPTIMISTIC]
    b = loose.close(up_won=False)["scenarios"][FM.OPTIMISTIC]
    assert a["modelled_fills"] <= b["modelled_fills"]
    assert abs(tight.inv[FM.OPTIMISTIC].residual(OTHER)) <= D("5")
