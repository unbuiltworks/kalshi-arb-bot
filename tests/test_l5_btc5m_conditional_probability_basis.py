"""The entry-EV gate's rates must be CONDITIONAL ON A FILL, or refuse.

THE DEFECT, measured 2026-11-19 on two hours of real 1-second BRTI, 20
complete BTC5m windows, using the production settlement model:

    UP rung 0.08 reached   11/20      DOWN rung 0.08 reached   9/20
    EITHER leg reachable   20/20      BOTH legs (a pair)       0/20
    reached legs that settled a WIN   0/20

Production rests both straddle legs at a fixed 0.08 while the market
trades 0.50-0.67. The gate was fed `p_lone_win = 0.0981`, which is a
sound UNCONDITIONAL estimate -- in a prediction market the price is about
the probability, and 0.08 is about 8%. But the EV needs the rate
CONDITIONAL ON OUR RESTING ORDER HAVING FILLED, and those differ by
exactly the adverse selection: the 0.08 leg is reached BECAUSE its
probability has collapsed.

    configured (unconditional)  ev = +0.0785/share  ADMITS
    measured   (conditional)    ev = -0.0643/share  REFUSES

THE GATE WAS NEVER WRONG. Its input was unconditional where conditional
is required, and nothing in the configuration said which it was.

WHAT THIS FIX DOES NOT DO. It does not install 0/20 as a probability. A
20-window sample in one regime is not a universal rate, and the owner was
right to say so. It installs no number at all: absent an attestation the
rates read as None and the gate declines, which is the correct state
while no conditional estimate exists.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as X

ENVS = (X.STRADDLE_P_PAIR_NATURAL_ENV,
        X.STRADDLE_P_COMPLETE_ENV,
        X.STRADDLE_P_LONE_WIN_ENV)


@pytest.fixture(autouse=True)
def _rates(monkeypatch):
    """All three rates present and in range, so the ONLY thing under test
    is the basis attestation."""
    monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.070")
    monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.131")
    monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.0981")
    monkeypatch.delenv(X.STRADDLE_P_BASIS_ENV, raising=False)


# ------------------------------------------------- refuse by default

def test_an_unattested_basis_refuses_every_rate():
    """THE FIX, as one assertion. The values are present and valid."""
    assert X.straddle_p_basis_attested() is False
    for env in ENVS:
        assert X.straddle_probability(env) is None, env


def test_the_rates_are_still_readable_once_attested(monkeypatch):
    """It must not be always-refuse, or it is a disable switch and not a
    correctness gate."""
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)
    assert X.straddle_probability(X.STRADDLE_P_PAIR_NATURAL_ENV) == D("0.070")
    assert X.straddle_probability(X.STRADDLE_P_COMPLETE_ENV) == D("0.131")
    assert X.straddle_probability(X.STRADDLE_P_LONE_WIN_ENV) == D("0.0981")


@pytest.mark.parametrize("raw,ok", [
    ("CONDITIONAL_ON_FILL", True),
    ("conditional_on_fill", True),
    ("  CONDITIONAL_ON_FILL  ", True),
    ("CONDITIONAL", False),
    ("UNCONDITIONAL", False),
    ("yes", False),
    ("1", False),
    ("true", False),
    ("CONDITIONAL_ON_FILLS", False),
    ("", False),
])
def test_only_the_exact_token_attests(monkeypatch, raw, ok):
    """A word that READS like an attestation is not one. This is the
    whole point: the old configuration said nothing about basis, so a
    loose token would reproduce the defect under a new name."""
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, raw)
    assert X.straddle_p_basis_attested() is ok
    assert (X.straddle_probability(X.STRADDLE_P_LONE_WIN_ENV)
            is not None) is ok


# ------------------------------- the existing validity checks still bind

def test_attesting_does_not_excuse_an_invalid_rate(monkeypatch):
    """The basis gate is ADDITIVE: it cannot rescue a value that is not a
    probability. A clamped rate is a rate nobody measured."""
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)
    for bad in ("0", "-0.1", "1.5", "abc", ""):
        monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, bad)
        assert X.straddle_probability(X.STRADDLE_P_LONE_WIN_ENV) is None, bad


def test_an_absent_rate_still_refuses_even_when_attested(monkeypatch):
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)
    monkeypatch.delenv(X.STRADDLE_P_LONE_WIN_ENV, raising=False)
    assert X.straddle_probability(X.STRADDLE_P_LONE_WIN_ENV) is None


def test_the_caller_default_is_honoured(monkeypatch):
    """Callers pass a default; an unattested basis must return THAT, not
    silently substitute something else."""
    assert X.straddle_probability(ENVS[0], D("0.5")) == D("0.5")
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, "nope")
    assert X.straddle_probability(ENVS[0], D("0.5")) == D("0.5")


# --------------------------------- the gate declines end to end

def test_the_entry_gate_declines_while_the_basis_is_unattested():
    """`decide` refuses the mode when a rate is None, so the unattested
    state reaches the decision and stops it."""
    import os
    from decimal import Decimal
    from src import l5_btc5m_controller as C
    os.environ["L5_BTC5M_CONTROLLER"] = "ON"
    params = C.Params(
        entry_mode=C.STRADDLE, straddle_repeat_enabled=True,
        straddle_max_side_usd=Decimal("0.30"),
        straddle_max_unmatched_usd=Decimal("0.10"),
        max_exposure_usd=Decimal("5.00"), no_new_entry_after_s=270.0,
        straddle_price=Decimal("0.08"), clip_usd=Decimal("0.08"),
        hedge_enabled=False, reserve_mode="NONE",
        #: what the executor hands over when the basis is unattested
        straddle_p_pair_natural=X.straddle_probability(ENVS[0]),
        straddle_p_complete=X.straddle_probability(ENVS[1]),
        straddle_p_lone_win=X.straddle_probability(ENVS[2]))
    res = C.decide(book={"bid": "0.66", "ask": "0.67", "age_s": 0.1},
                   t_in_window_s=120.0,
                   inventory={C.UP: {"qty": "0", "cost": "0"},
                              C.DOWN: {"qty": "0", "cost": "0"}},
                   resting=[], params=params)
    legs = [o for o in res["orders"] if o.get("reason") == "STRADDLE_LEG"]
    assert legs == []
    assert "NOT_SET" in str(res.get("why"))


# ------------------------------- the measurement that motivated it

def test_the_measured_conditional_inputs_make_the_gate_refuse():
    """The arithmetic the decision rests on, pinned so a later change to
    `entry_ev` cannot quietly flip it."""
    from src import l5_straddle_ev as SEV

    def ev(ppn, plw):
        got = SEV.entry_ev(price=D("0.08"), other_price=D("0.08"),
                           pair_target=D("0.96"), p_pair_natural=D(ppn),
                           p_complete=D("0.131"), p_lone_win=D(plw),
                           fee_per_share=D("0"), adverse_per_share=D("0"))
        return D(str(got.get("ev_per_share") if isinstance(got, dict)
                     else got))

    assert ev("0.070", "0.0981") > 0      # unconditional -> ADMITS
    assert ev("0", "0") < 0               # conditional   -> REFUSES


def test_no_probability_is_substituted_when_the_basis_is_unattested():
    """0/20 in one regime is not a rate, and nothing may install it.

    MY FIRST VERSION OF THIS TEST WAS WORTHLESS: it grepped the source
    for `= ZERO`, which is the VALIDITY check rejecting non-positive
    rates, and its other assertion ended in `or True` so it could not
    fail. The property actually worth pinning is that the refusing path
    hands back the CALLER'S default untouched -- it substitutes no number
    of its own, zero included.
    """
    sentinel = object()
    assert X.straddle_probability(ENVS[0], sentinel) is sentinel
    assert X.straddle_probability(ENVS[1], sentinel) is sentinel
    assert X.straddle_probability(ENVS[2], sentinel) is sentinel
    #: and with no default the refusal is None, never 0
    got = X.straddle_probability(ENVS[0])
    assert got is None and got != D("0")


def test_the_attestation_itself_carries_no_rate():
    """It answers a yes/no question and must not smuggle a number."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(X.straddle_p_basis_attested)))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]              # drop the docstring, which
        #: quotes the measured numbers and would match any scan for them
    code = ast.unparse(fn)
    nums = [n for n in ast.walk(ast.parse(code))
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))]
    assert nums == [], [n.value for n in nums]
