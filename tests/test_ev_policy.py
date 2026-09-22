"""The expected-value decision, and the two ways it could silently cheat.

WHY THIS POLICY EXISTS. Every earlier arm answered "is this unit worth
buying" with a CONSTANT -- a 0.96 pair cap, a $0.50 residual floor, CAP4. A
constant cannot be right because the answer depends on what the leg already
held is worth: refusing a 0.98 pair to keep a 0.30 residual destroys value,
and paying 0.90 to complete a 0.70 leg also destroys value. The rule has to
be asymmetric and the asymmetry has to come from measurement.

THE TWO WAYS IT COULD CHEAT, both asserted against:

  FUTURE LEAKAGE  `MarketState` must never be able to answer a question about
                  a print it has not been shown. A backtest whose gate reads
                  the market's own later flow will look brilliant and be
                  worthless.
  A REBATE TERM   this module is used for MODEL SELECTION, and the standing
                  instruction is that rebates stay out of it. A rebate
                  appearing here would let a losing rule be selected.
"""
from fractions import Fraction as F

import pytest

from src.gemini_execution import ev_policy as EV
from src.gemini_execution import ev_replay as R


# -- the empirical input ----------------------------------------------------
def test_price_is_not_the_win_probability():
    """THE WHOLE FINDING. A cheap leg wins far less often than it costs."""
    assert EV.p_win(F(3, 10)) < F(3, 10) / 2
    assert EV.p_win(F(7, 10)) > F(7, 10)


def test_win_probability_is_monotone_in_price():
    xs = [EV.p_win(F(k, 100)) for k in range(5, 100, 5)]
    assert xs == sorted(xs), xs


def test_win_probability_stays_a_probability():
    for k in range(0, 101):
        v = EV.p_win(F(k, 100))
        assert F(0) <= v <= F(1), (k, v)


# -- the completion rule ----------------------------------------------------
def test_a_cheap_residual_justifies_an_expensive_completion():
    """The owner's case: do not strand a 0.30 leg to avoid a 0.98 pair."""
    d = EV.complete_value(F(30, 100), F(68, 100))
    assert d["action"] == EV.COMPLETE
    assert d["implied_pair_cost_limit"] > F(1)


def test_an_expensive_residual_does_not_justify_paying_up():
    """A 0.70 leg is probably a winner; completing it sells that cheaply."""
    d = EV.complete_value(F(70, 100), F(25, 100))
    assert d["action"] == EV.HOLD
    assert d["implied_pair_cost_limit"] < F(1)


def test_the_completion_limit_is_not_monotone():
    """A flat pair cap cannot express this rule, which is why it is wrong.

    The tolerable pair cost RISES as the held leg gets cheaper (there is less
    to lose by abandoning it) and then FALLS again at the extremes.
    """
    lim = {p: EV.complete_value(F(p, 100), F(1, 2))["implied_pair_cost_limit"]
           for p in (20, 45, 70)}
    assert lim[45] > lim[20]
    assert lim[45] > lim[70]


def test_complete_and_hold_values_are_future_cashflow_only():
    """Sunk cost must not appear: the held leg's price is already paid."""
    d = EV.complete_value(F(30, 100), F(40, 100))
    assert d["complete_value"] == F(60, 100)          # 1 - q, not 1 - p - q


# -- acquisition ------------------------------------------------------------
def test_acquiring_with_no_history_falls_back_to_the_standalone_value():
    st = EV.MarketState()
    d = EV.acquire_value(F(30, 100), st, EV.NO)
    assert d["q_hat"] is None
    assert d["ev"] == EV.p_win(F(30, 100)) - F(30, 100)
    assert d["ev"] < 0                                 # a cheap leg alone loses


def test_a_likely_cheap_complement_makes_acquisition_worth_it():
    st = EV.MarketState()
    for i in range(9):
        st.observe(i, "buy", F(60, 100))               # NO buyable at 0.40
    d = EV.acquire_value(F(45, 100), st, EV.NO)
    assert d["p_complete"] > F(9, 10)
    assert d["ev"] > 0


def test_p_complete_never_returns_certainty_from_an_empty_tape():
    """An unknown market is neither hopeless nor a sure thing. Assuming
    either is how a backtest invents an edge."""
    st = EV.MarketState()
    v = st.p_complete(EV.YES)
    assert F(0) < v < F(1)


# -- the two cheats ---------------------------------------------------------
def test_market_state_cannot_see_a_print_it_was_not_given():
    """FUTURE LEAKAGE. State is fed print by print and holds no collection
    of the contract's later prints."""
    st = EV.MarketState()
    st.observe(1, "sell", F(4, 10))
    assert st.n_yes == 1 and st.n_no == 0
    assert st.q_hat(EV.NO) is None                     # nothing seen on NO yet
    st.observe(2, "buy", F(6, 10))
    assert st.q_hat(EV.NO) == F(4, 10)


def test_no_rebate_term_exists_in_model_selection():
    """Rebates are incremental income and must not select a losing rule."""
    import ast
    for path in ("src/gemini_execution/ev_policy.py",
                 "src/gemini_execution/ev_replay.py"):
        tree = ast.parse(open(path).read())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.arg for n in ast.walk(tree)
                  if isinstance(n, ast.arguments) for a in n.args + n.kwonlyargs}
        assert not any("rebate" in s.lower() or "reward" in s.lower()
                       for s in names), (path, names)


def test_both_modules_are_offline():
    import ast
    for path in ("src/gemini_execution/ev_policy.py",
                 "src/gemini_execution/ev_replay.py"):
        tree = ast.parse(open(path).read())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        assert mods <= {"fractions", "typing", "__future__", "src", "decimal"}, (path, mods)


# -- the replay -------------------------------------------------------------
def _c(prints, resolution="yes"):
    return {"symbol": "T", "duration": "15m", "day": "2026-09-20", "hour": 1,
            "event_ticker": "T", "prints": prints, "resolution": resolution}


def test_settlement_identity_holds():
    c = _c([[1, "sell", "0.40", "5"], [2, "buy", "0.45", "5"],
            [3, "sell", "0.30", "5"]])
    r = R.replay(c, policy="always")
    assert r["verdict"] == "SETTLED" and r["identity_holds"]


def test_an_unsettled_contract_is_never_scored():
    r = R.replay(_c([[1, "sell", "0.40", "5"]], resolution=None), policy="always")
    assert r["verdict"] == "UNSETTLED"
    assert "net" not in r


def test_capture_below_one_takes_strictly_fewer_units():
    pr = [[i, "sell" if i % 2 else "buy", "0.45", "5"] for i in range(1, 21)]
    full = R.replay(_c(pr), policy="always", max_legs=99)
    half = R.replay(_c(pr), policy="always", max_legs=99, capture=F(1, 2))
    assert half["fills"] < full["fills"]


def test_the_residual_cap_binds_even_when_ev_is_positive():
    pr = [[i, "sell", "0.75", "5"] for i in range(1, 12)]
    r = R.replay(_c(pr), policy="ev", max_residual=2, max_legs=99)
    assert r["residual"] <= 2


def test_the_leg_cap_binds():
    pr = [[i, "sell" if i % 2 else "buy", "0.45", "5"] for i in range(1, 40)]
    r = R.replay(_c(pr), policy="always", max_legs=6)
    assert r["fills"] <= 6
