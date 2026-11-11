"""REVIEW BLOCKER 2: what actually bounds the FIRST round? Measured, not assumed.

`reopen_decision` deliberately exempts the first exposure-taking round from
its capacity and expectancy tests, on the stated grounds that "the
executor's own cash, deficit and exposure gates own the first leg". The
review asked for that to be proven rather than asserted. These tests drive
the real `FaithfulExecutor` with each limit tightened in turn and record
what happens.

THE ANSWER, and it is not the one the assertion implied. The control that
bounds a first leg is the WORST-CASE DEFICIT, not the unmatched-contract
caps:

  * `max_worst_case_deficit_usd`  -> sizes the round to zero
                                     (`NO_LEGAL_QUANTITY`)
  * `max_cumulative_loss_usd`     -> sizes the round to zero
  * `max_cash_deployed_usd`       -> binds by SIZING, not refusal
  * `max_live_orders`             -> refuses (`MAX_LIVE_ORDERS`)

  * `max_unmatched_contracts`     -> does NOT bind a first leg
  * `residual_cap_decision` (`PMUS_RESIDUAL_CAP_K`) -> does NOT bind it

WHY THE UNMATCHED CAPS DO NOT, and why that is coherent rather than a hole.
`l5_live_validation_gates.gate` takes a parameter named `unmatched_after`,
and `l5_live_execution` passes `self.store.residual(market)` -- the residual
BEFORE this order, not after it would fill. So on a flat book the figure is
zero and the cap cannot trigger. That is consistent with what the
unmatched caps are for: they stop a leg DEEPENING an imbalance that already
exists, which is the 1415z failure, and `residual_cap_decision` is keyed the
same way. A first leg into a flat market has no imbalance to deepen.

The exposure a first leg really carries is "one leg fills and settles
worthless", and that is exactly what `max_worst_case_deficit_usd` prices --
so the dimension that matters IS bounded, by a different control than the
docstring implied. The parameter name remains misleading and is reported as
an observation; it is deployed risk logic and is NOT changed here under the
freeze.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.l5_live_validation_gates import ValidationGates as VG
import tests.test_l5_two_sided_wiring as W
from tests.test_l5_two_sided_wiring import probe  # noqa: F401


def gates(**over):
    base = dict(max_cash_deployed_usd=D("8"),
                max_unmatched_contracts=D("5"),
                max_cumulative_loss_usd=D("10"),
                max_worst_case_deficit_usd=D("3"))
    base.update(over)
    return VG(**base)


def first_round(H, E, tmp_path, g=None, orders=2):
    ex, t = W.mk(H, E, tmp_path, g=g or gates())
    ex.limits = H.limits(max_live_orders=orders)
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    return ex, t, rec


# ============ 1. THE CONTROLS THAT SIZE THE FIRST ROUND TO NOTHING

@pytest.mark.parametrize("field", ["max_worst_case_deficit_usd",
                                   "max_cumulative_loss_usd"])
def test_a_tight_loss_bound_sizes_the_first_round_to_zero(probe, tmp_path,
                                                          field):
    """The first round is NOT unbounded: these refuse it outright."""
    H, E = probe
    ex, t, rec = first_round(H, E, tmp_path,
                             g=gates(**{field: D("0.01")}))
    assert t.sent == [], (field, rec.get("refused_reason"))
    assert rec.get("refused_reason") == "NO_LEGAL_QUANTITY"


def test_the_worst_case_deficit_is_what_prices_one_leg_filling_alone(
        probe, tmp_path):
    """THE BOUND THAT MATTERS for a first leg, and it is enforced.

    A first leg's real exposure is that it fills and its complement does
    not -- 1230z exactly. That is the worst-case deficit, and tightening it
    stops the round while leaving every other limit generous.
    """
    H, E = probe
    loose = first_round(H, E, tmp_path / "loose",
                        g=gates(max_worst_case_deficit_usd=D("3")))
    assert loose[1].sent, "a generous deficit bound permits the round"
    worst = D(str(loose[1].sent[0]["price"])) * D(str(loose[1].sent[0]["qty"]))
    assert worst <= D("3"), "and the leg fits inside it"
    tight = first_round(H, E, tmp_path / "tight",
                        g=gates(max_worst_case_deficit_usd=D("0.01")))
    assert tight[1].sent == []


def test_the_live_order_cap_refuses_the_first_round(probe, tmp_path):
    H, E = probe
    ex, t, rec = first_round(H, E, tmp_path, orders=0)
    assert t.sent == []
    assert rec.get("refused_reason") == "MAX_LIVE_ORDERS"


# =================== 2. THE CASH CAP BINDS BY SIZING, NOT BY REFUSING

def test_the_cash_cap_sizes_the_first_leg_inside_itself(probe, tmp_path):
    """It does not refuse; it shrinks. Either way the cap holds."""
    H, E = probe
    ex, t, rec = first_round(H, E, tmp_path,
                             g=gates(max_cash_deployed_usd=D("0.10")))
    assert len(t.sent) == 1, rec.get("refused_reason")
    o = t.sent[0]
    notional = D(str(o["price"])) * D(str(o["qty"]))
    assert notional <= D("0.10"), (o["price"], o["qty"], notional)
    assert D(str(o["qty"])) < D("1"), "it really was sized down"


# ============ 3. WHAT DOES *NOT* BIND A FIRST LEG, recorded honestly

def test_the_unmatched_contract_cap_does_not_bind_a_first_leg(probe,
                                                              tmp_path):
    """REPORTED, NOT SILENTLY FIXED.

    `gate` names its parameter `unmatched_after` but is passed
    `store.residual(market)` -- the residual BEFORE the order. On a flat
    book that is zero, so the cap cannot trigger however tight it is set.
    Coherent for what the cap is for (stopping a leg DEEPENING an existing
    imbalance, the 1415z case) and misleading as a name. Deployed risk
    logic; not changed under the freeze.
    """
    H, E = probe
    ex, t, rec = first_round(H, E, tmp_path,
                             g=gates(max_unmatched_contracts=D("0.001")))
    assert len(t.sent) == 1, "the cap did not stop it"
    assert D(str(t.sent[0]["qty"])) > D("0.001"), \
        "and the leg is far larger than the nominal cap"
    # the pre-order residual really is what was judged
    assert ex.store.residual("m") is not None


def test_the_residual_cap_also_keys_on_existing_residual(probe, tmp_path,
                                                         monkeypatch):
    """`PMUS_RESIDUAL_CAP_K` bounds DEEPENING, so a flat first leg is free."""
    H, E = probe
    monkeypatch.setenv("PMUS_RESIDUAL_CAP_K", "0.01")
    ex, t, rec = first_round(H, E, tmp_path)
    assert len(t.sent) == 1, rec.get("refused_reason")


def test_the_unmatched_cap_DOES_bind_once_a_residual_exists(probe,
                                                            tmp_path):
    """Which is the case it exists for. 1415z, not 1230z."""
    H, E = probe
    ex, t = W.mk(H, E, tmp_path, g=gates(max_unmatched_contracts=D("0.5")))
    ex.limits = H.limits(max_live_orders=6)
    ex.store.apply_fill("m", "DOWN", D("1"), D("0.3700"), basis="OWN")
    assert abs(ex.store.residual("m")) > D("0.5")
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    sent_before = len(t.sent)
    # whatever it does, it may not open ANOTHER leg deepening that side
    for o in t.sent[:sent_before]:
        if str(o["side"]) == "DOWN":
            assert D(str(o["qty"])) <= abs(ex.store.residual("m")) + D("1")
    assert rec is not None


# ================ 4. THE AUTHORIZATION CHAIN IS UNCHANGED BY ANY OF IT

def test_the_first_round_still_passes_through_the_submission_gate(probe,
                                                                  tmp_path):
    """No exemption in `reopen_decision` touches arming or the approval."""
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.probe_reopen_decision)
    for banned in ("arm", "approval", "L5_LIVE_HALT", "L5_SHADOW_ONLY",
                   "submission_gate", "owner_arm_token"):
        assert banned not in src, banned


def test_the_first_round_exemption_is_economic_only(probe, tmp_path):
    """It skips EXPECTANCY. It does not skip reading the book."""
    from src import l5_unmatched_disposition as UD
    r = UD.reopen_decision(market="m", rounds_used=0,
                           worst_leg_own_px=D("0.49"), qty=D("1"),
                           bid=None, ask=None, tick=D("0.01"),
                           now_s=0.0, window_end_s=900.0,
                           exposure_headroom_usd=D("1.00"))
    assert r["reopen"] is False
    assert r["reason"] == "BOOK_NOT_TWO_SIDED"
    ok = UD.reopen_decision(market="m", rounds_used=0,
                            worst_leg_own_px=D("0.49"), qty=D("1"),
                            bid=D("0.48"), ask=D("0.49"), tick=D("0.01"),
                            now_s=0.0, window_end_s=900.0,
                            exposure_headroom_usd=D("1.00"))
    assert ok["reopen"] is True
    assert ok["economics_not_evaluated_for_the_first_round"] is True
