"""OWNER AUTHORIZATION 2026-09-16: CP1 states its own action size.

THE DEFECT. CP1 named neither `target_notional_usd` nor `max_leg_notional_usd`,
and absence is not "no lever" -- it is DEFAULT_TARGET_NOTIONAL $0.01 /
DEFAULT_MAX_LEG_NOTIONAL $0.02. `market_micro_spec()` reads those,
`choose_micro_quantity()` turns them into the submitted quantity
(`qty = spec["qty"]`, pmus_spring_l5_session.py:3500 and :3714), so the probe
sized at $0.0090 per action and ~$0.054 per market against a $5.00
authorization. Option A raised the CAP but not the SPEND.

DERIVED FROM FROZEN EVIDENCE, NOT PICKED. L5's own per-fill median is
8.84-25.0 contracts by price band (l5_behavior.json q5, 71,982 buy fills);
scaled by the authorized k=0.00619 that is 0.055-0.155 contracts. $0.10/$0.125
yields 0.11-0.22 contracts, inside that k-scaled distribution.
"""
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ld
from src import pmus_micro_executor as mx
from src import pmus_spring_l5_session as spring

MINQ = D("0.01")                     # the venue minimum lot
GRID = [D(str(i)) / D(100) for i in range(1, 100)]     # every tick
TARGET, MAX_LEG = D("0.10"), D("0.125")


def _cp1():
    return ld.rung("CP1")


# ---- 1. CP1 no longer falls back to the micro defaults -------------------

def test_cp1_states_its_own_sizing_and_does_not_inherit_micro():
    cp1 = _cp1()
    assert cp1.get("target_notional_usd") == "0.10"
    assert cp1.get("max_leg_notional_usd") == "0.125"
    assert ld.target_notional(cp1) == TARGET
    assert ld.max_leg_notional(cp1) == MAX_LEG
    # the values it used to inherit
    assert ld.DEFAULT_TARGET_NOTIONAL == D("0.01")
    assert ld.DEFAULT_MAX_LEG_NOTIONAL == D("0.02")
    assert ld.target_notional(cp1) > ld.DEFAULT_TARGET_NOTIONAL
    assert ld.max_leg_notional(cp1) > ld.DEFAULT_MAX_LEG_NOTIONAL


def test_the_size_materially_exceeds_the_micro_regime():
    """Requirement 1. Measured through the real sizing function, not asserted
    on the constants -- what matters is the ORDER that reaches the venue."""
    micro = mx.choose_micro_quantity(D("0.45"), MINQ, ld.DEFAULT_TARGET_NOTIONAL,
                                     ld.DEFAULT_MAX_LEG_NOTIONAL) * D("0.45")
    now = mx.choose_micro_quantity(D("0.45"), MINQ, TARGET, MAX_LEG) * D("0.45")
    assert micro == D("0.0090")
    assert now == D("0.0990")
    assert now / micro == 11


def test_the_order_carries_enough_lots_for_a_partial_fill_to_be_observable():
    """The probe's whole objective is fill/partial-fill/queue evidence. Two
    lots of the 0.01 minimum cannot show a meaningful partial; this asserts
    the floor that makes the evidence possible at all."""
    for px in ("0.25", "0.45", "0.90"):
        q = mx.choose_micro_quantity(D(px), MINQ, TARGET, MAX_LEG)
        assert q / MINQ >= 10, f"{px}: only {q / MINQ} lots"


# ---- 2/3. B3 preserved: R3L cannot enlarge, tighter still tightens --------

def test_persisted_r3l_cannot_enlarge_cp1_sizing():
    eff = ld.tightest(_cp1(), ld.rung("R3L"))
    assert eff["id"] == "CP1"
    assert ld.target_notional(eff) == TARGET
    assert ld.max_leg_notional(eff) == MAX_LEG
    # R3L's own values, explicitly excluded by the authorization
    assert ld.target_notional(ld.rung("R3L")) == D("2.50")
    assert ld.max_leg_notional(ld.rung("R3L")) == D("3.25")
    assert ld.target_notional(eff) < ld.target_notional(ld.rung("R3L"))


def test_no_rung_on_the_ladder_can_enlarge_cp1_sizing():
    """The sweep, not one pair."""
    cp1 = _cp1()
    for stored in ld.ALL_RUNGS:
        eff = ld.tightest(cp1, stored)
        assert ld.target_notional(eff) <= TARGET, stored["id"]
        assert ld.max_leg_notional(eff) <= MAX_LEG, stored["id"]


def test_a_tighter_persisted_sizing_still_tightens_cp1():
    """The owner's inverse requirement. R1 states neither lever, so its
    EFFECTIVE sizing is the micro default -- genuinely tighter, and it must
    still bind."""
    eff = ld.tightest(_cp1(), ld.rung("R1"))
    assert ld.target_notional(eff) == D("0.01")
    assert ld.max_leg_notional(eff) == D("0.02")
    # and an explicitly-tighter stored rung too
    tight = dict(_cp1(), id="TIGHT", target_notional_usd="0.03",
                 max_leg_notional_usd="0.04")
    eff2 = ld.tightest(_cp1(), tight)
    assert eff2["id"] == "CP1"
    assert ld.target_notional(eff2) == D("0.03")
    assert ld.max_leg_notional(eff2) == D("0.04")


def test_b3_still_holds_every_other_dimension():
    """The sizing change must not have loosened anything B3 pinned."""
    cp1 = _cp1()
    eff = ld.tightest(cp1, ld.rung("R3L"))
    for k in ld.RISK_DIMENSIONS:
        p, e = ld._effective(cp1, k), ld._effective(eff, k)
        assert ld._tighter_of(k, p, e) == e, f"{k} loosened {p} -> {e}"
    assert D(eff["per_market_cap_usd"]) == D("5.00")
    assert int(eff["residual_units"]) == 3
    assert ld.daily_loss_stop(eff) == D("-6.00")


# ---- 4. restart/resume preserves CP1 -------------------------------------

def test_restart_resume_preserves_cp1_sizing(monkeypatch, tmp_path):
    """Through run_campaign's ARMED restore with R3L on the volume, twice."""
    from tests.test_b3_persisted_rung_intersection import (
        _harness, _run, _save_state)
    monkeypatch.setenv(ld.RUNG_ENV, "CP1")
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    ld.set_active(None)
    fake = _harness(monkeypatch, tmp_path)
    _save_state("R3L")

    seen = []
    rep = _run(fake, seen)
    assert rep["rung"]["ladder_rung"] == "CP1"
    assert rep["rung"]["per_action_target_usd"] == "0.10"
    assert rep["rung"]["per_leg_max_usd"] == "0.125"
    assert seen, "no session ran -- the armed restore was never exercised"
    for s in seen:
        assert s["target"] == "0.10" and s["leg"] == "0.125", s

    ld.set_active(None)                      # a fresh process
    seen2 = []
    rep2 = _run(fake, seen2)
    assert rep2["rung"]["ladder_rung"] == "CP1"
    assert rep2["rung"]["per_action_target_usd"] == "0.10"
    assert rep2["rung"]["per_leg_max_usd"] == "0.125"
    assert seen2, "no session ran after the restart"
    for s in seen2:
        assert s["target"] == "0.10" and s["leg"] == "0.125", s


# ---- 5. six worst-case actions cannot exceed the $5 market cap ------------

def test_six_worst_case_actions_cannot_exceed_the_market_cap():
    """Swept over EVERY tick, both tails, not three sample prices.

    The bound is also structural: choose_micro_quantity validates
    qty*p <= max_leg AND qty*(1-p) <= max_leg, so cash at risk per action can
    never exceed max_leg whatever the price. Six of those is 6 x $0.125 =
    $0.75, which is the analytic ceiling the sweep must not beat.
    """
    worst = max(mx.choose_micro_quantity(p, MINQ, TARGET, MAX_LEG)
                * max(p, D(1) - p) for p in GRID)
    assert worst <= MAX_LEG, "an action exceeded the per-leg bound"
    assert 6 * worst <= D("0.7500")
    assert 6 * worst < D("5.00")
    # and with a very large margin -- the cap is an outer bound, not the
    # operating constraint, which is what lets the CONTROLLER decide
    assert 6 * worst / D("5.00") < D("0.16")


def test_one_action_cannot_consume_most_of_the_market_envelope():
    """Requirement 3."""
    worst = max(mx.choose_micro_quantity(p, MINQ, TARGET, MAX_LEG)
                * max(p, D(1) - p) for p in GRID)
    assert worst / D("5.00") < D("0.03"), f"one action is {worst} of $5.00"


def test_every_quantity_respects_the_venue_lot_grid():
    """Requirement 5. Nothing may be submitted off the 0.01 grid."""
    for p in GRID:
        q = mx.choose_micro_quantity(p, MINQ, TARGET, MAX_LEG)
        assert q > 0
        assert (q / MINQ) % 1 == 0, f"{p}: {q} is not a lot multiple"


def test_the_sizing_never_halts_anywhere_on_the_grid():
    """target > max_leg Halts MICRO_BAD_NOTIONAL_BOUNDS and a max_leg too
    small for one lot Halts MICRO_MIN_LOT_EXCEEDS_MAX. Both would take the
    probe down at a price rather than refuse an order, so both are swept."""
    assert TARGET <= MAX_LEG
    for p in GRID:
        mx.choose_micro_quantity(p, MINQ, TARGET, MAX_LEG)


# ---- 6. the transport mandate remains the hard outer boundary -------------

@pytest.mark.real_mandate_env
def test_the_mandate_still_clamps_the_explicit_sizing(monkeypatch):
    import json
    monkeypatch.setenv("PMUS_SERVICE_ROLE", "")
    monkeypatch.setenv("PMUS_LIVE_MANDATE", json.dumps({
        "authorization_id": "SIZING-TEST", "authorized_at": "2026-01-01T00:00:00Z",
        "service": "test-suite", "strategy": "t", "strategy_version": "t",
        "max_markets": 1, "max_total_markets": 3,
        "max_spend_per_market": "1.00", "max_total_spend": "3.00",
        "max_residual": "3.00", "max_daily_realized_loss": "6.00",
        "expiry": "2999-01-01T00:00:00Z"}))
    ld.set_active_effective(ld.tightest(_cp1(), ld.rung("R3L")))
    try:
        assert spring.session_notional_cap() == D("1.00")   # min(rung, mandate)
    finally:
        ld.set_active(None)


def test_guarded_transport_and_min_rung_mandate_are_untouched():
    from src import pmus_live_mandate as m
    src = open(m.__file__, encoding="utf-8").read()
    for name in ("target_notional", "max_leg_notional", "tightest",
                 "RISK_DIMENSIONS"):
        assert name not in src, f"{name} leaked into the mandate module"
    body = open(spring.__file__, encoding="utf-8").read()
    body = body.split("def session_notional_cap(")[1].split("\ndef ")[0]
    assert "min(rung" in body


# ---- 7. the $3 residual and $6 daily-loss boundaries are unchanged --------

def test_the_residual_and_daily_loss_boundaries_are_unchanged():
    cp1 = _cp1()
    assert int(cp1["residual_units"]) == 3
    assert ld.daily_loss_stop(cp1) == D("-6.00")
    eff = ld.tightest(cp1, ld.rung("R3L"))
    assert int(eff["residual_units"]) == 3
    assert ld.daily_loss_stop(eff) == D("-6.00")


def test_the_residual_overshoot_is_bounded_and_disclosed():
    """THE ONE PLACE THE SIZING TOUCHES A RISK BOUNDARY, pinned to a number.

    Both the decision gate (`abs(behavioral_residual()) >= max_residual_units()`,
    pmus_spring_l5_session.py:3190) and the wire guard (`resid > max_residual`,
    pmus_live_mandate.py:492) test the residual ALREADY HELD, not the residual
    the proposed order would create. So one action may cross the ceiling, and
    the overshoot is exactly one action's contracts.

    That is pre-existing and not introduced here -- at the inherited micro
    sizing the overshoot was 0.02 contracts ($3.02 worst case). Stating its new
    value is the point: it scales with action size, so it is pinned here and
    fails if a later sizing change quietly widens it.
    """
    q_max = max(mx.choose_micro_quantity(p, MINQ, TARGET, MAX_LEG) for p in GRID)
    assert q_max == D("0.22")
    # unmatched inventory is priced at $1/share by the wire guard, so
    # contracts and worst-case dollars are the same number
    assert D(3) + q_max == D("3.22")
    micro_q = max(mx.choose_micro_quantity(p, MINQ, ld.DEFAULT_TARGET_NOTIONAL,
                                           ld.DEFAULT_MAX_LEG_NOTIONAL)
                  for p in GRID)
    assert D(3) + micro_q == D("3.02")       # what it was before
    # it stays far inside the campaign and daily bounds either way
    assert D(3) + q_max < D("15.00")
    assert D(3) + q_max < D("6.00")


def test_the_worst_case_campaign_spend_is_unchanged_at_fifteen_dollars():
    w = ld.worst_case(_cp1())
    assert w["per_market_usd"] == "5.00"
    assert w["per_campaign_usd"] == "15.00"
    assert w["max_concurrent_open_exposure_usd"] == "5.00"
    assert w["daily_loss_stop_usd"] == "-6.00"


def test_cp1_is_still_unpromotable_and_off_the_auto_ladder():
    assert _cp1()["promote"] is None
    assert "CP1" not in [r["id"] for r in ld.RUNGS]
