"""The 1415z residual cap incident, reproduced from the six real fills.

2026-10-31, Stage B2. The arming preflight advertised
`effective_cap_contracts: 3` and unmatched inventory reached 4.25 contracts.
The sixth order -- UP 2.08 placed on top of a realized UP 2.17 -- increased
|residual| and qualified for NEITHER documented exemption, so the gate at
`l5_live_execution.py:7286` should have refused it RESIDUAL_ABOVE_CAP.

The fills, from `L5_LIVE_LIFECYCLE` (own prices; commission 0.0000 on all):

    14:16:34  UP   1.96 @ 0.32   -> residual UP 1.96
    14:17:08  DOWN 1.96 @ 0.68   -> residual 0      (pair 1.0000)
    14:17:41  UP   1.83 @ 0.27   -> residual UP 1.83
    14:19:06  DOWN 1.83 @ 0.67   -> residual 0      (pair 0.9400)
    14:20:38  UP   2.17 @ 0.28   -> residual UP 2.17
    14:21:13  UP   2.08 @ 0.25   -> residual UP 4.25   <-- should not have been allowed

These tests drive the REAL store so that `residual`, `max_clip` and
`outstanding_residual` are the production functions, and then evaluate the
gate's own predicate. They pin the arithmetic that the incident violated.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX


MARKET = "cpc-btc-updown-15m-2026-10-31-1415z"

#: (side, qty, own price) in the order they filled
FILLS = (("UP", "1.96", "0.32"),
         ("DOWN", "1.96", "0.68"),
         ("UP", "1.83", "0.27"),
         ("DOWN", "1.83", "0.67"),
         ("UP", "2.17", "0.28"))

#: the order that must have been refused
ORDER_6 = ("UP", D("2.08"))


def _store(tmp_path, n_fills: int):
    """A real store with the first `n_fills` of the incident booked."""
    st = LX.InventoryStore(str(tmp_path / "live.json"))
    for i, (side, qty, px) in enumerate(FILLS[:n_fills]):
        st.apply_fill(MARKET, side, D(qty), D(px),
                      coid="l5f-replay-%d" % i, basis=LX.OWN)
    return st


def _gate(store, side: str, qty: D, k: D = D(2)):
    """The gate's predicate, evaluated exactly as handle() evaluates it."""
    resid_before = store.residual(MARKET)
    outstanding = store.outstanding_residual(MARKET)
    prosp_before = resid_before + outstanding
    signed = qty if side == "UP" else -qty
    prosp_after = prosp_before + signed
    b_abs = -prosp_before if prosp_before < 0 else prosp_before
    a_abs = -prosp_after if prosp_after < 0 else prosp_after
    exempt_open = b_abs == 0
    increases = a_abs > b_abs
    clip = store.max_clip(MARKET)
    if qty > clip:
        clip = qty
    cap = min(k * clip, LX.ABS_RESIDUAL_CAP_CONTRACTS)
    return {"resid_before": resid_before, "outstanding": outstanding,
            "b_abs": b_abs, "a_abs": a_abs, "clip": clip, "cap": cap,
            "exempt_open": exempt_open, "increases": increases,
            "binds": bool((not exempt_open) and increases and a_abs > cap)}


# --------------------------------------------- the state the fills produce

def test_the_five_fills_leave_a_realized_residual_of_2_17(tmp_path):
    st = _store(tmp_path, 5)
    assert st.residual(MARKET) == D("2.17")
    assert st.position(MARKET, "UP") == D("5.96")
    assert st.position(MARKET, "DOWN") == D("3.79")


def test_max_clip_is_the_largest_single_fill(tmp_path):
    st = _store(tmp_path, 5)
    assert st.max_clip(MARKET) == D("2.17")


def test_the_two_pairs_completed_before_the_strand(tmp_path):
    """Fills 1-4 net flat: the incident is not a pairing failure."""
    st = _store(tmp_path, 4)
    assert st.residual(MARKET) == D(0)


# ------------------------------------------------- THE INCIDENT ITSELF

def test_order_six_MUST_be_refused_residual_above_cap(tmp_path):
    """The regression. UP 2.08 on top of UP 2.17 is 4.25 against a cap of 3."""
    st = _store(tmp_path, 5)
    g = _gate(st, *ORDER_6)
    assert g["resid_before"] == D("2.17")
    assert g["b_abs"] == D("2.17")
    assert g["exempt_open"] is False, (
        "the book held 2.17; the opening-leg exemption must not apply")
    assert g["increases"] is True, (
        "UP on top of UP increases |residual|; the reducing-leg exemption "
        "must not apply")
    assert g["cap"] == D(3), "min(2 * 2.17, 3) is 3"
    assert g["a_abs"] == D("4.25")
    assert g["binds"] is True, (
        "4.25 > 3 on an exposure-increasing order with no exemption: this "
        "order must be refused RESIDUAL_ABOVE_CAP. Production allowed it.")


def test_the_realized_outcome_is_the_one_the_cap_should_have_prevented(tmp_path):
    st = _store(tmp_path, 5)
    st.apply_fill(MARKET, "UP", D("2.08"), D("0.25"),
                  coid="l5f-replay-5", basis=LX.OWN)
    assert st.residual(MARKET) == D("4.25")
    assert st.residual(MARKET) > LX.ABS_RESIDUAL_CAP_CONTRACTS


# ------------------------------- the exemptions, so the fix cannot disarm them

def test_a_genuine_opening_leg_is_still_exempt(tmp_path):
    """The first order into a flat market must stay uncapped."""
    st = LX.InventoryStore(str(tmp_path / "flat.json"))
    g = _gate(st, "UP", D("2.08"))
    assert g["b_abs"] == D(0)
    assert g["exempt_open"] is True
    assert g["binds"] is False


def test_a_reducing_leg_is_still_exempt_at_any_depth(tmp_path):
    """A DOWN leg against UP inventory reduces |residual| and must pass."""
    st = _store(tmp_path, 5)
    g = _gate(st, "DOWN", D("2.17"))
    assert g["increases"] is False
    assert g["binds"] is False


def test_a_reducing_leg_that_overshoots_TO_A_BIGGER_IMBALANCE_is_caught(tmp_path):
    """`increases` is about MAGNITUDE, not direction of travel -- correctly.

    I first assumed an overshoot would read as "reducing" and slip past the
    gate. It does not. DOWN 6.00 against UP 2.17 lands at DOWN 3.83, and
    |3.83| > |2.17|, so `increases` is True and the cap binds. The gate is
    sound on overshoot and this test pins that.
    """
    st = _store(tmp_path, 5)
    g = _gate(st, "DOWN", D("6.00"))
    assert g["a_abs"] == D("3.83")
    assert g["increases"] is True
    assert g["binds"] is True


def test_an_overshoot_that_lands_closer_to_flat_still_passes(tmp_path):
    """And the asymmetry that remains is the harmless one.

    DOWN 4.00 against UP 2.17 lands at DOWN 1.83, which is CLOSER to flat
    than it started, so it passes however deep the book was. That is the
    intended exemption.
    """
    st = _store(tmp_path, 5)
    g = _gate(st, "DOWN", D("4.00"))
    assert g["a_abs"] == D("1.83")
    assert g["increases"] is False
    assert g["binds"] is False


# ------------------------------------------------ the cap is OFF when k is unset

def test_an_unset_k_turns_the_whole_policy_off(tmp_path):
    """`residual_cap_k() is None` means the block never runs at all.

    Recorded because it is the one configuration under which 4.25 would be
    expected rather than a defect -- and the heartbeat echoed k=2, so it was
    NOT the configuration that ran.
    """
    from src import l5_pmus_live_runner as R
    import os
    old = os.environ.pop("PMUS_RESIDUAL_CAP_K", None)
    try:
        assert R.residual_cap_k() is None
    finally:
        if old is not None:
            os.environ["PMUS_RESIDUAL_CAP_K"] = old


# ================= PRIORITY 2: the final pre-submit guard, end to end
#
# These drive `FaithfulExecutor.final_residual_guard` -- the production
# method, on the production path -- rather than a local copy of the
# predicate. The 1415z incident is the first case.

def _executor(store):
    """A FaithfulExecutor with k=2 and nothing else wired.

    The guard touches only `residual_cap_k` and the store, so the rest of
    the executor is deliberately left unconfigured: a test that needed a
    transport would be testing something else.
    """
    ex = LX.FaithfulExecutor.__new__(LX.FaithfulExecutor)
    ex.residual_cap_k = D(2)
    ex.store = store
    return ex


def _record_live(store, coid, market, side, qty, price="0.25"):
    """Put an order in `live_orders` the way record_submit does."""
    live = store.state.setdefault("live_orders", {})
    live[coid] = {"market": market, "side": side,
                  "price": str(price), "qty": str(qty),
                  "remaining": str(qty)}
    store.save()


def test_final_guard_refuses_the_1415z_order_six(tmp_path):
    """THE INCIDENT, through the production guard.

    Five fills booked (realized UP 2.17) and order 6 recorded live as
    record_submit would have left it. The guard must refuse.
    """
    st = _store(tmp_path, 5)
    _record_live(st, "l5f-fe3923af", MARKET, "UP", "2.08")
    g = _executor(st).final_residual_guard(
        MARKET, "UP", D("2.08"), coid="l5f-fe3923af")
    assert g["ok"] is False
    assert g["reason"] == "FINAL_RESIDUAL_ABOVE_CAP"
    assert g["prospective_before"] == "2.17"
    assert g["prospective_after"] == "4.25"
    assert g["cap_contracts"] == "3"
    assert g["increases_abs_prospective"] is True
    assert g["prospective_book_flat"] is False


def test_final_guard_does_not_double_count_the_order(tmp_path):
    """`outstanding` already holds this order; before must come out at 2.17.

    Computing prospective_before as realized+outstanding (rather than
    subtracting the order) would read 4.25 as the BEFORE figure and refuse
    orders that are actually fine. This pins the arithmetic.
    """
    st = _store(tmp_path, 5)
    _record_live(st, "c1", MARKET, "UP", "2.08")
    g = _executor(st).final_residual_guard(MARKET, "UP", D("2.08"), coid="c1")
    assert g["residual_realized"] == "2.17"
    assert g["outstanding_residual_incl_this_order"] == "2.08"
    assert g["prospective_before"] == "2.17"


def test_final_guard_preserves_a_risk_reducing_completion(tmp_path):
    """The DOWN leg that pairs the 2.17 must still go. Any depth."""
    st = _store(tmp_path, 5)
    _record_live(st, "c2", MARKET, "DOWN", "2.17")
    g = _executor(st).final_residual_guard(MARKET, "DOWN", D("2.17"), coid="c2")
    assert g["ok"] is True
    assert g["increases_abs_prospective"] is False


def test_final_guard_exempts_a_genuine_first_leg(tmp_path):
    """A flat book places its opening leg however big the leg is."""
    st = LX.InventoryStore(str(tmp_path / "flat2.json"))
    _record_live(st, "c3", MARKET, "UP", "2.90")
    g = _executor(st).final_residual_guard(MARKET, "UP", D("2.90"), coid="c3")
    assert g["prospective_book_flat"] is True
    assert g["ok"] is True


def test_final_guard_fails_closed_when_inventory_is_unreadable(tmp_path):
    """An unreadable position must REFUSE, never read as flat."""
    class _Broken:
        state: dict = {}

        def residual(self, market):
            raise RuntimeError("store unavailable")

        def outstanding_residual(self, market):
            return D(0)

        def max_clip(self, market):
            return D(2)

    g = _executor(_Broken()).final_residual_guard(
        MARKET, "UP", D("2.08"), coid="c4")
    assert g["ok"] is False
    assert g["reason"].startswith("RESIDUAL_STATE_UNREADABLE")


def test_final_guard_is_inert_when_the_policy_is_off(tmp_path):
    """k unset means the whole policy is off; the guard must not invent one."""
    st = _store(tmp_path, 5)
    ex = _executor(st)
    ex.residual_cap_k = None
    g = ex.final_residual_guard(MARKET, "UP", D("2.08"), coid="c5")
    assert g["ok"] is True
    assert g["reason"] == "POLICY_OFF"


def test_final_guard_runs_the_whole_six_fill_sequence(tmp_path):
    """END TO END: replay all six orders through the production guard.

    Orders 1-5 must pass and order 6 must refuse. This is the sequence the
    venue actually filled, in order, against the real store.
    """
    st = LX.InventoryStore(str(tmp_path / "e2e.json"))
    ex = _executor(st)
    seq = list(FILLS) + [("UP", "2.08", "0.25")]
    verdicts = []
    for i, (side, qty, px) in enumerate(seq):
        coid = "l5f-e2e-%d" % i
        _record_live(st, coid, MARKET, side, qty, px)
        g = ex.final_residual_guard(MARKET, side, D(qty), coid=coid)
        verdicts.append(bool(g["ok"]))
        st.state.setdefault("live_orders", {}).pop(coid, None)
        if g["ok"]:
            st.apply_fill(MARKET, side, D(qty), D(px),
                          coid=coid, basis=LX.OWN)
    assert verdicts == [True, True, True, True, True, False], verdicts
    # the position the cap was supposed to prevent never gets built
    assert st.residual(MARKET) == D("2.17")
    assert st.residual(MARKET) <= LX.ABS_RESIDUAL_CAP_CONTRACTS
