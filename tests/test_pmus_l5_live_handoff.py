"""L5 -> PMUS sequential live handoff (issue #167 comment 5547365082).

The probe must answer "what would L5 do NEXT at this state?", never
"is there already a cheap pair?".
"""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from src import pmus_l5_live_handoff as ho
from src import pmus_sports_l5_decision as l5d


def _inv(yes=0, no=0, ycost="0", ncost="0"):
    inv = l5d.Inventory()
    inv.yes, inv.no = yes, no
    inv.yes_cost, inv.no_cost = D(ycost), D(ncost)
    return inv


# ---- entry is the MODEL's next action, not a pre-formed pair ----

def test_entry_comes_from_the_action_model_not_a_cheap_pair():
    """A flat book with no pre-existing pair still produces an action:
    the model is asked what L5 does next, and answers."""
    plan = ho.next_action(D("0.45"), D("0.47"), _inv(), "")
    assert plan["decision"] == "quote"
    assert plan["relation"] in ("open", "extend", "complete")
    assert plan["side"] in ("YES", "NO")
    assert plan["state"].startswith("p45-55c|")


def test_pair_bound_is_a_risk_constraint_not_an_entry_condition():
    """<=0.99 must never gate an OPEN. It applies only to a completion,
    where it bounds the risk of the pair we would actually hold."""
    inv = _inv()
    opened = ho.next_action(D("0.45"), D("0.47"), inv, "")
    assert ho.risk_gate(opened, inv, D("0"), 0, D("0.009")) is None

    # holding 1 YES at 0.60, a completion priced to exceed 0.99 is refused
    held = _inv(yes=1, ycost="0.60")
    comp = {"decision": "quote", "relation": "complete", "side": "NO",
            "_px": D("0.45"), "_side_yes": False}
    why = ho.risk_gate(comp, held, D("0"), 0, D("0.009"))
    assert why is not None and "pair_risk_bound" in why


def test_off_distribution_state_produces_no_action():
    """A state the frozen model never visited cannot be replicated."""
    plan = ho.next_action(D("0.02"), D("0.03"), _inv(), "")
    assert plan["decision"] == "no_action"
    assert plan["reason"] == "v2_off_distribution"


# ---- hard risk envelope, fail closed ----

def test_notional_is_price_times_quantity_never_price_alone():
    """Regression: treating the contract PRICE as notional made the $0.05
    event cap reject every action on its first call, because a 0.45
    contract 'cost' 0.45 rather than 0.45 x 0.02 = $0.009."""
    assert ho.action_notional(D("0.45"), D("0.02")) == D("0.009000")
    plan = ho.next_action(D("0.45"), D("0.47"), _inv(), "")
    assert ho.risk_gate(plan, _inv(), D("0"), 0,
                        ho.action_notional(D("0.45"), D("0.02"))) is None


def test_every_cap_fails_closed():
    inv = _inv()
    plan = ho.next_action(D("0.45"), D("0.47"), inv, "")
    ok = D("0.009")
    assert "max_actions" in ho.risk_gate(plan, inv, D("0"),
                                         ho.MAX_ACTIONS, ok)
    assert "action_notional_cap" in ho.risk_gate(plan, inv, D("0"), 0,
                                                 D("0.50"))
    assert "event_notional_cap" in ho.risk_gate(plan, inv, D("0.049"), 0, ok)
    assert "daily_loss_stop" in ho.risk_gate(
        plan, inv, D("0"), 0, ok, realized_pnl=D("-1.00"))
    deep = _inv(yes=ho.MAX_RESIDUAL_UNITS, ycost="1.35")
    assert "residual_cap" in ho.risk_gate(plan, deep, D("0"), 0, ok)


def test_disarmed_by_default_and_isolated_from_other_keys(monkeypatch):
    """The q1/micro/spring keys must not arm this, and vice versa."""
    for env in ("PMUS_Q1_LIVE", "PMUS_MICRO_LIVE", "PMUS_SPRING_LIVE"):
        monkeypatch.setenv(env, "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "s")
    monkeypatch.setenv("PMUS_Q1_ARM", "s")
    assert ho.armed() is False

    monkeypatch.setenv(ho.HANDOFF_LIVE_ENV, "YES")
    assert ho.armed() is False                    # needs sid + matching arm
    monkeypatch.setenv(ho.HANDOFF_SID_ENV, "abc")
    assert ho.armed() is False
    monkeypatch.setenv(ho.HANDOFF_ARM_ENV, "wrong")
    assert ho.armed() is False
    monkeypatch.setenv(ho.HANDOFF_ARM_ENV, "abc")
    assert ho.armed() is True


# ---- audit trail and labels ----

def test_audit_row_preserves_strict_status_labels():
    inv = _inv()
    plan = ho.next_action(D("0.45"), D("0.47"), inv, "")
    row = ho.audit_row(1.0, {"bid": "0.45"}, plan, None, None, inv)
    assert row["status"] == "shadow"
    assert row["submit_decision"] == "shadow_only"
    assert row["realized_pnl"] is None
    refused = ho.audit_row(1.0, {"bid": "0.45"}, plan,
                           "residual_cap:4>3", None, inv)
    assert refused["status"] == "shadow_refused"
    assert refused["refuse_reason"] == "residual_cap:4>3"
    assert refused["realized_pnl"] is None


def test_session_report_states_every_cap_and_never_claims_realized():
    inv = _inv()
    rep = ho.session_report("aec-x", [], inv, D("0"))
    caps = rep["caps"]
    assert caps["auto_restart"] is False
    assert caps["max_event_notional"] == "0.05"
    assert caps["pair_risk_bound"] == "0.99"
    assert rep["fees"]["rebates_counted_toward_bound"] is False
    assert rep["realized_pnl"] is None
    assert rep["armed"] is False


def test_module_has_no_independent_order_path():
    """Transport is reused from the isolated micro executor, never
    reimplemented here."""
    src = Path(ho.__file__).read_text()
    for banned in ("urllib", "requests", "POST", "/v1/orders", "X-PM-"):
        assert banned not in src, banned


# ---- the action budget must leave room to get flat ----

def test_entries_stop_before_completion_becomes_unreachable():
    """Six actions all spent on entries leaves a residual with no budget
    left to complete it -- the probe ends legged by construction. The
    reservation refuses the entry instead."""
    inv = _inv(yes=2, ycost="0.80")          # residual +2; a 3rd is legal
    plan = ho.next_action(D("0.45"), D("0.47"), inv, "extend")
    assert plan["decision"] == "quote", "the residual cap must not pre-empt"
    # 3 actions already placed; a 4th entry would leave residual 3 with
    # only 2 actions of budget left -- one short of getting flat.
    gate = ho.risk_gate(plan, inv, D("0.01"), 3, D("0.005"))
    assert gate == "completion_capacity_reserved:2<3", gate
    # the same entry earlier in the event, with budget to spare, passes
    assert ho.risk_gate(plan, inv, D("0.01"), 1, D("0.005")) is None


def test_the_reservation_never_blocks_a_completion():
    """Reserving budget FOR completions must not refuse a completion."""
    inv = _inv(yes=3, ycost="1.20")
    completion = {"decision": "quote", "relation": "complete",
                  "_px": D("0.50"), "_side_yes": False}
    assert ho.risk_gate(completion, inv, D("0.01"), 5, D("0.005")) is None


def test_the_reservation_only_tightens_it_never_raises_a_cap():
    """An early entry with plenty of budget left is unaffected."""
    inv = _inv()
    plan = ho.next_action(D("0.45"), D("0.47"), inv, "")
    assert ho.risk_gate(plan, inv, D("0"), 0, D("0.005")) is None


def test_session_report_distinguishes_admitted_from_submitted():
    """Unarmed, nothing is ever submitted; a report that only counted
    submissions made a full session read as though nothing happened."""
    inv = _inv(yes=1, ycost="0.45")
    rows = [ho.audit_row(1.0, {}, {"relation": "open"}, None,
                         {"filled": True}, inv),
            ho.audit_row(2.0, {}, {"relation": "extend"}, "residual_cap:4>3",
                         None, inv)]
    rep = ho.session_report("evt", rows, inv, D("0.005"))
    assert rep["actions_submitted"] == 0        # disarmed
    assert rep["actions_admitted"] == 1
    assert rep["actions_refused"] == 1
    assert rep["actions_filled_shadow"] == 1
    assert rep["residual_uncompleted"] == 1
    assert rep["ended_legged"] is True
