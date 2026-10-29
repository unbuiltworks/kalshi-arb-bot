"""Residual quarantine: the ONE thing it changes, and the many it must not.

OWNER GO 2026-09-10 required proof by MUTATION: "removing a quarantined
residual from `held`, the loss budget, settlement monitoring, or complement
management fails." Each of those is a test below that passes only while the
residual is still fully carried, and fails the moment it is dropped.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_residual_quarantine as Q
from src import pmus_spring_l5_session as S


def _inv(up="0", dn="0", cost_up="0", cost_dn="0"):
    inv = S.Inventory()
    inv.up, inv.dn = D(up), D(dn)
    inv.cost_up, inv.cost_dn = D(cost_up), D(cost_dn)
    return inv


def _stranded():
    """The county-cricket shape: 4.9 UP / 12.13 DN, $5.7015 spent, residual
    -7.23. Floor = min(4.9, 12.13) - 5.7015 = -0.8015."""
    return _inv(up="4.9", dn="12.13", cost_up="2.499", cost_dn="3.2025")


@pytest.fixture
def stop15(monkeypatch):
    """Pin the daily stop to the LIVE rung's -$15.00.

    daily_loss_stop() reads the ACTIVE rung, and the suite's default rung is
    -$1.00. A test that hard-codes -15 without pinning it is asserting the
    environment, not the gate -- and would pass or fail depending on which
    rung happened to be active. This is the second time that has caught me.
    """
    monkeypatch.setattr(S, "daily_loss_stop", lambda: D("-15.00"))
    return D("-15.00")


def _pos(slug="stranded", state="QUARANTINED", inv=None):
    return {"slug": slug, "state": state, "inventory": inv or _stranded(),
            "resting_orders": 0, "venue_confirmed": True,
            "in_held": True, "settlement_monitored": True}


# ------------------------------------------------------------ ships dark
def test_quarantine_ships_dark():
    assert Q.quarantine_enabled({}) is False
    assert Q.quarantine_enabled({Q.QUARANTINE_ENV: "1"}) is True
    for junk in ("", "maybe", "0", "off", "TRUE-ish"):
        got = Q.quarantine_enabled({Q.QUARANTINE_ENV: junk})
        assert got is (junk.strip().lower() in ("1", "true", "yes", "on")), junk


def test_active_acquisition_concurrency_is_still_one():
    assert Q.ACTIVE_ACQUISITION_SLOTS == 1


# ------------------------------------------------------- eligibility gates
@pytest.mark.parametrize("kw,gate", [
    ({"resting_orders": 1}, "zero_resting_orders"),
    ({"venue_confirmed": False}, "venue_confirmed"),
    ({"in_held": False}, "stays_in_held"),
    ({"settlement_monitored": False}, "stays_settlement_monitored"),
])
def test_each_condition_can_refuse_alone_and_names_itself(kw, gate):
    m = {**_pos(), **kw}
    out = Q.eligibility(m)
    assert not out["eligible"]
    assert gate in out["failed"]
    with pytest.raises(Q.QuarantineRefused) as e:
        Q.quarantine(m)
    assert gate in e.value.failed


def test_a_flat_market_is_not_quarantined_it_simply_leaves():
    """Quarantining flat would hide a scheduler bug behind a safety label."""
    out = Q.eligibility({**_pos(), "inventory": _inv()})
    assert not out["eligible"] and "has_inventory" in out["failed"]


def test_an_unestablished_condition_is_a_refusal_not_a_pass():
    """`venue_confirmed` missing entirely must refuse: 'we could not check'
    and 'it holds' are different answers."""
    m = _pos(); m.pop("venue_confirmed")
    assert not Q.eligibility(m)["eligible"]
    m2 = _pos(); m2.pop("resting_orders")
    assert not Q.eligibility(m2)["eligible"]


def test_a_dormant_market_quarantines_and_keeps_everything_else():
    out = Q.quarantine(_pos())
    assert out["occupies_acquisition_slot"] is False
    assert out["in_held"] is True
    assert out["settlement_monitored"] is True
    assert out["counted_in_combined_loss"] is True
    assert out["complement_management_active"] is True
    assert out["dollar_caps_changed"] is None
    assert out["exposure_forgiven"] is None


# ========================= THE MUTATIONS ==================================
# Each shows the answer CHANGING when the quarantined residual is dropped.

def test_MUTATION_dropping_a_quarantined_residual_from_held_changes_the_budget():
    """OWNER: removing a quarantined residual from `held` must fail."""
    active, quar = _pos("active", "ACQUIRING"), _pos("quar", "QUARANTINED")
    both = Q.held_inventories([active, quar])
    only_active = [active["inventory"]]           # the mutation
    real = S.combined_loss(D("0"), both)
    mutant = S.combined_loss(D("0"), only_active)
    assert len(both) == 2 and len(only_active) == 1
    assert real < mutant, "dropping the quarantined market understates the loss"
    assert real == mutant + S.guaranteed_settlement_floor(quar["inventory"])


def test_MUTATION_held_inventories_cannot_be_filtered_by_state():
    """held_inventories exists to be the one place that cannot be narrowed."""
    rows = [_pos("a", "ACQUIRING"), _pos("b", "QUARANTINED"),
            _pos("c", "QUARANTINED")]
    assert len(Q.held_inventories(rows)) == 3
    import inspect
    src = inspect.getsource(Q.held_inventories)
    assert "QUARANTIN" not in src.split('"""')[2], \
        "the collector must not branch on state"


def test_MUTATION_full_conservative_downside_is_carried_undiscounted():
    """No weighting, no discount, no 'it's dormant so it's less risky'."""
    quar = _pos()
    floor = S.guaranteed_settlement_floor(quar["inventory"])
    assert floor == D("4.9") - D("5.7015")
    head = Q.portfolio_headroom(D("0"), [quar])
    assert D(head["combined_loss_usd"]) == floor
    assert head["quarantined_markets"] == 1 and head["held_markets"] == 1


def test_MUTATION_a_quarantined_market_still_blocks_admission_at_the_limit(stop15):
    """The gate must refuse BECAUSE of the quarantined exposure. If the
    residual were dropped, the same proposal would be admitted."""
    quar = _pos("quar", "QUARANTINED",
                _inv(up="0", dn="20", cost_up="0", cost_dn="14.4"))
    assert S.guaranteed_settlement_floor(quar["inventory"]) == D("-14.4")
    stop = D(S.daily_loss_stop())
    proposed = stop / 2                       # a loss well inside the budget
    with_quar = Q.may_admit_new_acquisition(D("0"), [quar], proposed)
    without = Q.may_admit_new_acquisition(D("0"), [], proposed)
    assert with_quar["admit"] is False
    assert "budget_after_proposed_above_stop" in with_quar["failed"]
    assert without["admit"] is True, \
        "the mutation (no quarantined residual) would have admitted it"


def test_MUTATION_settlement_monitoring_is_a_precondition_not_a_consequence():
    """A market cannot be quarantined AND dropped from settlement: dropping
    it makes it ineligible in the first place."""
    m = {**_pos(), "settlement_monitored": False}
    with pytest.raises(Q.QuarantineRefused):
        Q.quarantine(m)


def test_MUTATION_complement_management_survives_quarantine():
    """A quarantined market may still COMPLETE, using the SAME predicate the
    live path uses -- and the answer must be identical."""
    inv = _stranded()                                  # residual -7.23, UP short
    out = Q.may_complete_in_quarantine(inv, side_up=True, px="0.30", qty="7")
    direct = S.reduces_risk_only(inv, True, D("0.30"), D("7"))
    assert out["risk_reducing"] is bool(direct)
    assert out["allowed"] is bool(direct and S.pair_invariant_ok(
        inv, True, D("0.30"), D("7")))
    assert D(out["floor_after_usd"]) >= D(out["floor_before_usd"])


def test_a_completion_that_breaches_the_pair_ceiling_is_refused_in_quarantine():
    """The ceiling predicate is bound directly, not via a hasattr fallback.
    An earlier draft called a function that does not exist and defaulted to
    True -- a ceiling check that always passes."""
    inv = _inv(up="10", dn="0", cost_up="1.0")         # a_up 0.10
    out = Q.may_complete_in_quarantine(inv, side_up=False, px="0.99", qty="10")
    assert out["within_pair_ceiling"] is False
    assert out["allowed"] is False


# ---------------------------------------------------------------- the gate
def test_the_gate_is_the_owners_expression_verbatim(stop15):
    quar = _pos("quar", "QUARANTINED",
                _inv(up="0", dn="10", cost_up="0", cost_dn="6"))
    out = Q.may_admit_new_acquisition(D("-2"), [quar], D("-5"))
    # combined = -2 + (-6) = -8 ; + (-5) = -13 ; > -15 -> admit
    assert out["combined_loss_usd"] == "-8"
    assert out["combined_after_proposed_usd"] == "-13"
    assert out["daily_loss_stop_usd"] == "-15.00"
    assert out["admit"] is True


def test_the_gate_boundary_is_strict_at_exactly_the_stop(stop15):
    """At exactly -$15 the stop HAS fired, matching combined_budget_tripped's
    inclusive boundary."""
    quar = _pos("quar", "QUARANTINED",
                _inv(up="0", dn="10", cost_up="0", cost_dn="10"))
    at = Q.may_admit_new_acquisition(D("0"), [quar], D("-5"))     # -15 exactly
    assert at["combined_after_proposed_usd"] == "-15"
    assert at["admit"] is False, "at the stop, the stop has fired"
    inside = Q.may_admit_new_acquisition(D("0"), [quar], D("-4.99"))
    assert inside["admit"] is True


def test_quarantine_does_not_relax_the_single_acquisition_slot(stop15):
    """The budget is nowhere near tripped here, so the ONLY reason to refuse
    is the slot."""
    out = Q.may_admit_new_acquisition(D("0"), [], D("-1"),
                                      active_acquisitions=1)
    assert out["admit"] is False
    assert out["failed"] == ["acquisition_slot_free"]
    free = Q.may_admit_new_acquisition(D("0"), [], D("-1"),
                                       active_acquisitions=0)
    assert free["admit"] is True


def test_the_gate_reads_the_ACTIVE_rung_not_a_hard_coded_fifteen():
    """Under the suite's -$1.00 rung the same proposal that fits a -$15
    budget must be REFUSED. The gate follows the ladder, not a constant."""
    assert S.daily_loss_stop() == D("-1.00"), "suite default rung"
    out = Q.may_admit_new_acquisition(D("0"), [], D("-5"))
    assert out["admit"] is False
    assert out["daily_loss_stop_usd"] == "-1.00"


def test_a_positive_worst_case_is_refused_rather_than_negated():
    """A 'worst case' that is a gain would BUY headroom."""
    with pytest.raises(ValueError, match="must be a loss"):
        Q.may_admit_new_acquisition(D("0"), [], D("5"))


def test_quarantine_raises_no_cap():
    """Nothing in this module touches a dollar cap."""
    import inspect
    src = inspect.getsource(Q)
    for bad in ("session_notional_cap(", "per_market_cap", "max_unmatched_usd(",
                "completion_pair_ceiling() =", "daily_loss_stop() ="):
        assert bad not in src, bad


# ============================ THE STALL REPLAY ============================
from src import pmus_quarantine_stall_replay as R   # noqa: E402


def test_the_actual_stall_admitted_nothing():
    assert R.replay()["actual"]["markets_admitted_during_stall"] == 0


def test_the_cricket_floor_is_the_real_one():
    """min(4.9, 12.13) - 5.7015, from the campaign's own inventory row."""
    assert R.floor_of(R.CRICKET["up"], R.CRICKET["dn"],
                      R.CRICKET["spent"]) == D("-0.8015")


def test_cadence_is_the_one_actually_observed_before_the_stall():
    """20 markets in 93 minutes, not an idealised rate."""
    assert R.PRE_STALL_MARKETS == 20 and R.PRE_STALL_MINUTES == 93.0
    assert round(R.cadence_per_hour(), 2) == 12.90


def test_a_completed_pair_below_par_consumes_no_budget():
    """The mechanism by which completing buys capacity: floor >= 0."""
    assert R.floor_of(D("19.87"), D("19.87"), D("18.8307")) > 0
    out = R.replay(arm="empirical")
    zero_cost = [a for a in out["admitted"] if a["worst_case_usd"] == "0"]
    assert zero_cost, "the empirical arm must contain completed markets"


def test_both_arms_are_reported_and_the_pessimistic_one_is_the_lower_bound():
    emp = R.replay(arm="empirical")
    pes = R.replay(arm="pessimistic")
    assert pes["additional_markets_admitted"] < emp["additional_markets_admitted"]
    assert pes["arm"] == "pessimistic" and emp["arm"] == "empirical"


def test_the_gate_refuses_correctly_in_both_arms():
    for arm in ("empirical", "pessimistic"):
        out = R.replay(arm=arm)
        ref = out["gate_refused_at"]
        assert ref is not None, f"{arm} must reach the gate"
        would_be = D(ref["combined_would_be_usd"])
        assert would_be <= D(ref["stop_usd"]), \
            f"{arm}: refusal must be BECAUSE the budget would breach"
        assert D(out["final_combined_loss_usd"]) > D("-15.00")


def test_the_gate_binds_before_the_opportunity_slots_run_out():
    """68 slots existed; the budget is what stops acquisition, not the clock.
    That is the whole argument for a portfolio gate over a concurrency dial."""
    out = R.replay()
    assert out["opportunity_slots_in_window"] == 68
    assert out["additional_markets_admitted"] < out["opportunity_slots_in_window"]


def test_the_quarantined_market_is_still_carried_throughout_the_replay():
    out = R.replay()
    q = out["quarantined_market"]
    assert q["still_in_held"] and q["still_settlement_monitored"]
    assert q["still_in_combined_loss"]
    assert D(q["guaranteed_floor_usd"]) < 0
    # and its floor is inside the final combined loss, not beside it
    assert D(out["final_combined_loss_usd"]) <= D(q["guaranteed_floor_usd"])


def test_a_tighter_stop_admits_strictly_fewer():
    loose = R.replay(stop=D("-15.00"))["additional_markets_admitted"]
    tight = R.replay(stop=D("-5.00"))["additional_markets_admitted"]
    assert tight < loose
