"""The six boundary-preflight assertions: fail closed, and pass only on the
reviewed envelope.

OWNER: "If any preflight assertion fails, remain safely idle."
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_boundary_preflight as P


class _T:  # a transport stand-in; never touches the network
    pass


def _ok_env(monkeypatch, cap="7.50", ceiling="1.00", rung="R5F"):
    from src import pmus_spring_l5_session as S
    from src import pmus_l5_ramp_ladder as L
    monkeypatch.setattr(S, "session_notional_cap", lambda: D(cap))
    monkeypatch.setattr(S, "completion_pair_ceiling", lambda: D(ceiling))
    monkeypatch.setattr(L, "active", lambda: {"id": rung})


def _all_green(monkeypatch):
    """Everything the preflight needs, satisfied."""
    from src import pmus_spring_l5_session as S
    from src import pmus_l5_continuous_session as C
    _ok_env(monkeypatch)
    monkeypatch.setattr(S, "reconcile_submissions",
                        lambda t, slug, **kw: {"unresolved": []})
    monkeypatch.setattr(S, "daily_loss_stop", lambda: D("-15.00"))
    monkeypatch.setattr(C, "unsettled_slugs", lambda: [])
    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: {"ours": 0, "foreign": 0})
    monkeypatch.setattr(C, "_combined_now", lambda: (D("-0.15"), {}))
    monkeypatch.setattr(P, "quarantine_tests_green", lambda paths=None: True)


CANDIDATE = {"state": "MARKET_STATE_OPEN", "bid": 0.40, "ask": 0.41,
             "maker_pair_cost": 0.99, "in_band_fraction": 1.0,
             "flow_usd_per_min": 5000.0}


def test_a_fully_clean_preflight_passes(monkeypatch):
    _all_green(monkeypatch)
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert out["ok"] is True, out["failed"]
    assert out["failed"] == []


@pytest.mark.parametrize("break_it,gate", [
    ("reconcile", "held_inventory_reconciled"),
    ("orphans", "zero_orphan_or_foreign_ambiguity"),
    ("settlements", "settlements_current"),
    ("budget", "combined_loss_budget_permits"),
    ("candidate", "selector_has_fresh_candidate"),
    ("tests", "quarantine_tests_green"),
    ("envelope", "envelope_unchanged"),
])
def test_each_assertion_can_fail_alone_and_names_itself(monkeypatch,
                                                        break_it, gate):
    from src import pmus_spring_l5_session as S
    from src import pmus_l5_continuous_session as C
    _all_green(monkeypatch)
    cand = CANDIDATE
    if break_it == "reconcile":
        monkeypatch.setattr(S, "reconcile_submissions",
                            lambda t, slug, **kw: {"unresolved": ["x"]})
    elif break_it == "orphans":
        monkeypatch.setattr(C, "_fresh_open_orders",
                            lambda t: {"ours": 0, "foreign": 1})
    elif break_it == "settlements":
        monkeypatch.setattr(C, "unsettled_slugs", lambda: ["pending"])
    elif break_it == "budget":
        monkeypatch.setattr(C, "_combined_now", lambda: (D("-20.00"), {}))
    elif break_it == "candidate":
        cand = None
    elif break_it == "tests":
        monkeypatch.setattr(P, "quarantine_tests_green", lambda paths=None: False)
    elif break_it == "envelope":
        _ok_env(monkeypatch, cap="25.00")
    slugs = ["held"] if break_it == "reconcile" else []
    out = P.preflight(t=_T(), ranked_candidate=cand, held_slugs=slugs)
    assert out["ok"] is False
    assert gate in out["failed"], out["failed"]
    assert out["action_if_failed"] == "remain safely idle; do not enable"


# ------------------------------------------------------------- fail closed
def test_an_exception_inside_a_check_is_a_FAILURE_not_an_unknown(monkeypatch):
    _all_green(monkeypatch)
    from src import pmus_l5_continuous_session as C

    def boom():
        raise RuntimeError("venue unreachable")

    monkeypatch.setattr(C, "unsettled_slugs", boom)
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert out["ok"] is False
    assert "settlements_current" in out["failed"]


def test_no_transport_cannot_pass_the_reconcile_check(monkeypatch):
    """'we could not ask the venue' is not 'the venue agrees'."""
    _all_green(monkeypatch)
    out = P.preflight(t=None, ranked_candidate=CANDIDATE, held_slugs=[])
    assert "held_inventory_reconciled" in out["failed"]


def test_a_skipped_test_run_is_a_failed_check(monkeypatch):
    _all_green(monkeypatch)
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[],
                      run_tests=False)
    assert "quarantine_tests_green" in out["failed"]


def test_foreign_order_ambiguity_fails_even_when_ours_is_zero(monkeypatch):
    """An order we cannot attribute is not an absence of orders."""
    _all_green(monkeypatch)
    from src import pmus_l5_continuous_session as C
    monkeypatch.setattr(C, "_fresh_open_orders", lambda t: {"ours": 0})
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert "zero_orphan_or_foreign_ambiguity" in out["failed"]


# --------------------------------------------------------- the envelope
def test_the_cap_is_read_from_the_active_rung_not_inlined(monkeypatch):
    """R5F carries $7.50; the ladder's default R1 carries $0.05. A preflight
    that inlined 7.50 would assert the environment, not the envelope."""
    _all_green(monkeypatch)
    _ok_env(monkeypatch, cap="0.05", rung="R1")
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert "envelope_unchanged" in out["failed"]
    row = next(c for c in out["checks"] if c["check"] == "envelope_unchanged")
    assert "R1" in row["got"] and "0.05" in row["got"]


def test_a_raised_completion_ceiling_fails_the_envelope(monkeypatch):
    _all_green(monkeypatch)
    _ok_env(monkeypatch, ceiling="1.03")
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert "envelope_unchanged" in out["failed"]


def test_more_than_one_acquisition_slot_fails_the_envelope(monkeypatch):
    _all_green(monkeypatch)
    from src import pmus_residual_quarantine as Q
    monkeypatch.setattr(Q, "ACTIVE_ACQUISITION_SLOTS", 3)
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert "envelope_unchanged" in out["failed"]


def test_the_required_envelope_is_the_reviewed_one():
    e = P.REQUIRED_ENVELOPE
    assert e["active_acquisition_slots"] == 1
    assert e["directional_acquisition_cap_usd"] == D("7.50")
    assert e["whole_position_pair_ceiling_usd"] == D("1.00")
    assert e["complete_ceiling_usd"] == D("1.00")
    assert e["maker_only"] is True
    assert e["taker_rescue_enabled"] is False


def test_an_ineligible_candidate_fails(monkeypatch):
    _all_green(monkeypatch)
    stale = {**CANDIDATE, "flow_usd_per_min": 0.0}
    out = P.preflight(t=_T(), ranked_candidate=stale, held_slugs=[])
    assert "selector_has_fresh_candidate" in out["failed"]


def test_the_budget_check_uses_the_active_stop_not_a_constant(monkeypatch):
    """Under a -$1.00 rung, a -$5 combined loss must refuse."""
    _all_green(monkeypatch)
    from src import pmus_spring_l5_session as S
    from src import pmus_l5_continuous_session as C
    monkeypatch.setattr(S, "daily_loss_stop", lambda: D("-1.00"))
    monkeypatch.setattr(C, "_combined_now", lambda: (D("-5.00"), {}))
    out = P.preflight(t=_T(), ranked_candidate=CANDIDATE, held_slugs=[])
    assert "combined_loss_budget_permits" in out["failed"]
