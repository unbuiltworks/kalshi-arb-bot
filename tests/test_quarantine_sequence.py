"""The full quarantine lifecycle, proven step by step and mutation-tested.

OWNER: "acquire -> residual -> cancel resting orders -> venue reconcile ->
QUARANTINED -> residual remains charged to combined_loss -> selector proposes
next market -> portfolio gate admit/refuse -> original residual remains
monitored/completable/settleable. Mutation-test every safety transition."
"""
from __future__ import annotations

import pytest

from src import pmus_quarantine_sequence as QS
from src import pmus_residual_quarantine as RQ
from decimal import Decimal as D

from src import pmus_spring_l5_session as S


def _inv(up="4.9", dn="12.13", cost_up="2.499", cost_dn="3.2025"):
    """The county-cricket shape: residual -7.23, floor -0.8015."""
    inv = S.Inventory()
    inv.up, inv.dn = D(up), D(dn)
    inv.cost_up, inv.cost_dn = D(cost_up), D(cost_dn)
    return inv


def _market(**kw):
    m = {"slug": "aec-county-not-ham-2026-09-08", "inventory": _inv(),
         "resting_orders": 0, "venue_confirmed": True, "in_held": True,
         "settlement_monitored": True}
    m.update(kw)
    return m


def _run(**kw):
    kw.setdefault("market", _market())
    kw.setdefault("realized_today", "-0.80")
    kw.setdefault("proposed", {"slug": "aec-nfl-sf-lar-2026-09-10",
                               "worst_case": "-1.05"})
    kw.setdefault("book", {"complement_px": D("0.63")})
    return QS.run_sequence(**kw)


def test_the_whole_sequence_runs_clean_in_order():
    out = _run()
    assert out["ok"] is True, out["failed"]
    assert out["observed_order"] == list(QS.STEPS)


def test_quarantine_does_not_change_the_loss_charge_by_one_cent():
    """The step that matters most: quarantine frees a scheduling slot and
    frees NOTHING else."""
    out = _run()
    step = next(s for s in out["steps"] if s["step"] == "residual_still_charged")
    assert step["ok"] is True
    assert step["combined_loss_before"] == step["combined_loss_after"]


def test_the_quarantined_market_is_still_held_and_still_monitored():
    out = _run()
    last = out["steps"][-1]
    assert last["ok"] is True
    assert last["in_held"] is True
    assert last["settlement_monitored"] is True


def test_a_resting_order_stops_the_sequence_at_that_step():
    out = _run(market=_market(resting_orders=1))
    assert out["ok"] is False
    assert "cancel_resting_orders" in out["failed"]
    assert "quarantined" in out["failed"]


def test_an_unconfirmed_position_stops_the_sequence():
    out = _run(market=_market(venue_confirmed=False))
    assert out["ok"] is False
    assert "venue_reconcile" in out["failed"]


def test_the_gate_reaching_a_refusal_is_a_pass_but_admitting_is_recorded():
    """admit and refuse are both correct outcomes; failing to decide is not.

    THE AMOUNTS ARE DERIVED FROM THE ACTIVE RUNG, NOT HARD-CODED. R5F carries
    a -$15.00 stop and the suite's default R1 carries -$1.00; a test written
    around -15 asserts the environment rather than the gate. This is the same
    ladder-sourced constant that has now caught me four times.
    """
    stop = D(str(S.daily_loss_stop()))
    floor = S.guaranteed_settlement_floor(_inv())      # -0.8015, a loss

    # clearly INSIDE: leave a dollar of headroom after the proposal
    roomy = _run(realized_today=str(stop - floor + D("2.00")),
                 proposed={"slug": "x", "worst_case": "-1.00"})
    g_roomy = next(s for s in roomy["steps"] if s["step"] == "portfolio_gate")
    assert g_roomy["ok"] is True
    assert g_roomy["gate"]["admit"] is True

    # clearly OUTSIDE: the proposal alone would breach the same stop
    tight = _run(realized_today=str(stop - floor),
                 proposed={"slug": "x", "worst_case": "-1.00"})
    g_tight = next(s for s in tight["steps"] if s["step"] == "portfolio_gate")
    assert g_tight["ok"] is True, "the gate must still DECIDE"
    assert g_tight["gate"]["admit"] is False
    assert "budget_after_proposed_above_stop" in g_tight["gate"]["failed"]


def test_a_proposal_without_a_stated_worst_case_is_refused_not_defaulted():
    out = _run(proposed={"slug": "x"})
    gate = next(s for s in out["steps"] if s["step"] == "portfolio_gate")
    assert gate["gate"]["admit"] is False
    assert "proposed_worst_case_missing" in gate["gate"]["failed"]


def test_the_caller_mapping_is_never_mutated():
    m = _market()
    _run(market=m)
    assert "state" not in m


def test_sequence_submits_no_order():
    """Prose may DESCRIBE submitting nothing; code must not import an order
    path. Comments and docstrings are excluded so the check tests the module
    rather than its own explanation of itself."""
    import ast
    tree = ast.parse(open("src/pmus_quarantine_sequence.py").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    joined = " ".join(imported).lower()
    for banned in ("executor", "micro", "q1"):
        assert banned not in joined, f"order path imported: {imported}"
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for banned in ("submit", "place_order", "cancel_order", "post"):
        assert banned not in calls


# ---- mutation: every safety transition must be load-bearing --------------

def test_mutation_dropping_quarantined_from_held_breaks_the_charge(monkeypatch):
    """If a quarantined market stopped counting as held, the loss charge
    would fall and quarantine would become a way to hold unlimited stranded
    risk one market at a time."""
    real = RQ.held_inventories
    monkeypatch.setattr(
        RQ, "held_inventories",
        lambda positions: real([p for p in positions
                                if p.get("state") != "QUARANTINED"]))
    out = _run()
    assert out["ok"] is False
    assert "residual_still_charged" in out["failed"]


def test_mutation_allowing_a_resting_order_lets_a_live_market_be_quarantined(
        monkeypatch):
    def lax(market):
        e = {"eligible": True, "checks": {}, "failed": []}
        return e
    monkeypatch.setattr(RQ, "eligibility", lax)
    out = _run(market=_market(resting_orders=1))
    step = next(s for s in out["steps"] if s["step"] == "quarantined")
    assert step["ok"] is True, (
        "mutation did not change behaviour -- eligibility is not what stops "
        "a market with resting orders from being quarantined")


def test_mutation_dropping_settlement_monitoring_fails_the_last_step():
    out = _run(market=_market(settlement_monitored=False))
    assert out["ok"] is False
    assert "quarantined" in out["failed"]
    assert "residual_still_monitored_completable_settleable" in out["failed"]


@pytest.mark.parametrize("field", ["in_held", "settlement_monitored",
                                   "venue_confirmed"])
def test_each_quarantine_precondition_is_individually_required(field):
    out = _run(market=_market(**{field: False}))
    assert out["ok"] is False
    assert "quarantined" in out["failed"]
