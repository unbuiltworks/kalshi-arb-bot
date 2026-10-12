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


# ------------------------------------------------- it cannot be hidden

def test_the_unresolved_record_exists_and_names_the_market():
    recs = RUN.unresolved_accounting()
    assert recs, "the unresolved record vanished"
    r = [x for x in recs if x["market"] == UNRESOLVED]
    assert len(r) == 1
    assert r[0]["cost_basis"] == "UNKNOWN"
    assert r[0]["qty"] == "1.0000"
    assert r[0]["venue_confirmed_quantity"] is True


def test_it_states_the_missing_evidence_and_what_is_forbidden():
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


def test_a_caller_cannot_mutate_the_registry_through_the_accessor():
    RUN.unresolved_accounting()[0]["cost_basis"] = "0.18"
    assert RUN.unresolved_accounting()[0]["cost_basis"] == "UNKNOWN"


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


class _Runner:
    try_arm = RUN.Runner.try_arm

    def __init__(self, store):
        self.transport = _T()
        self.now_t = 1000.0
        self.win = collections.Counter()
        self.reconciled = True
        self.store = store

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


def test_a_silently_cleared_unresolved_row_refuses_arming(monkeypatch):
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

def test_DELETING_the_unresolved_row_still_refuses_arming(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, p: seen.append((k, p)))
    r = _Runner(_ArmStore(positions={}, violations={}, uncertain=None))
    r.try_arm()
    assert r.transport.arms == 0, "a deleted row restored live authorization"
    assert r.win["arm_refused_unresolved_accounting"] == 1
    d = [p for k, p in seen if k == "L5_LIVE_DISARMED"][0]["discrepancies"][0]
    assert d["now"] == "row ABSENT from the ledger"
    assert "not evidence it was resolved" in d["labels"]


def test_an_entirely_empty_ledger_still_refuses_arming():
    """Losing the whole ledger file must not read as all-clear."""
    r = _Runner(_ArmStore(positions={}))
    r.try_arm()
    assert r.transport.arms == 0


def test_an_unreadable_named_ledger_fails_CLOSED(monkeypatch):
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


def test_the_registry_is_visible_independently_of_the_ledger():
    """It must be readable with no store at all."""
    assert RUN.unresolved_accounting()[0]["market"] == UNRESOLVED
    assert RUN.unresolved_accounting()[0]["ledger"] == "l5-live-20261012z"


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
