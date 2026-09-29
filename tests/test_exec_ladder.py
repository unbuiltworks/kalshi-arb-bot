"""The ladder's rung 0 must BE frozen, and each rung must move one thing.

Rung 0 is `pmus_native_parity.replay_window` with a passive lifecycle
observer, so it reproduces the frozen baseline by construction. If it ever
stops doing that, every number above it is meaningless -- which is exactly
what happened to the harness this replaced.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.audit_economic_complete import load, USABLE
from src import pmus_exec_ladder as X
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P

FROZEN = D("6.116725359099999999999999992")
CONSERVATIVE = D("5.922062959099999999999999992")


@pytest.fixture(scope="module")
def tape():
    return load()


@pytest.fixture(scope="module")
def rungs(tape):
    return {r["rung"]: r for r in X.ladder(tape, USABLE)}


def test_rung0_is_frozen_window_for_window(tape):
    """Not just the mean: every window, exactly."""
    bad = []
    for slug, w in USABLE.items():
        a = X.replay_window(tape[slug], winner=w, rung="R0_FROZEN_CONTROL")
        b = P.frozen_window(tape[slug], winner=w)
        if P.net_of(a) != P.net_of(b):
            bad.append((slug[-5:], str(P.net_of(a) - P.net_of(b))))
        assert (a.get("executable_states") or 0) == \
            (b.get("executable_states") or 0), slug
    assert bad == [], bad


def test_rung0_reproduces_the_baseline_exactly(rungs):
    r = rungs["R0_FROZEN_CONTROL"]
    assert r["mean_net"] == FROZEN
    assert r["completions"] == 44
    assert r["maker_fills"] == 136
    assert r["taker_fills"] == 0
    assert r["lost"] == []


def test_the_lifecycle_rung_is_free(rungs):
    """Classifying every fill through the production classifier on the
    venue's own record shape must cost nothing: the own-terms round trip is
    lossless."""
    assert rungs["R1_LIFECYCLE"]["mean_net"] == FROZEN
    assert rungs["R1_LIFECYCLE"]["delta_prev"] == 0
    assert rungs["R1_LIFECYCLE"]["maker_fills"] == 136


def test_one_fill_per_real_order_is_the_only_rung_that_costs(rungs):
    r = rungs["R2_ONE_FILL_PER_ORDER"]
    assert r["mean_net"] == CONSERVATIVE
    assert r["delta_prev"] == D("-0.19466240000000000")
    assert r["completions"] == 43
    assert r["maker_fills"] == 127
    assert len(r["lost"]) == 13
    assert {x["reason"] for x in r["lost"]} == {"NO_LIVE_ORDER_AT_THIS_PRICE"}


def test_maker_taker_latency_terminal_and_accounting_are_all_free(rungs):
    for name in ("R3_MAKER_TAKER", "R4_LATENCY", "R5_TERMINAL_HANDLING",
                 "R6_PRODUCTION_ACCOUNTING"):
        assert rungs[name]["delta_prev"] == 0, name
        assert rungs[name]["mean_net"] == CONSERVATIVE, name
    # taker legs become real orders at rung 3 and stay real
    assert rungs["R3_MAKER_TAKER"]["taker_fills"] == 47
    assert rungs["R6_PRODUCTION_ACCOUNTING"]["taker_fills"] == 47


def test_the_execution_faithful_result_clears_the_five_dollar_target(rungs):
    final = rungs["R6_PRODUCTION_ACCOUNTING"]["mean_net"]
    assert final == CONSERVATIVE
    assert final > D(5)


def test_no_rejection_probability_is_invented():
    """R4 is n=1. No rung may carry a rejection rate."""
    import inspect
    sig = inspect.signature(X.replay_window)
    assert sig.parameters["reject_rule"].default is None
    # and no rung turns one on
    src = inspect.getsource(X.ladder)
    assert "reject_rule" not in src
    for rung in X.RUNGS:
        assert "reject" not in " ".join(
            k for k, v in X.rung_flags(rung).items() if v)


def test_the_frozen_parameters_are_untouched():
    assert (str(E.THETA), E.VARIANT, E.HOLD_S, str(E.CLIP),
            str(E.RESIDUAL_CAP_K), str(E.PROFIT_LOCK_USD),
            E.FLOOR_GUARD, E.COMPLEMENT_BIAS_C) == \
        ("0.96", "B", 60.0, "50", "2", "5", False, None)
