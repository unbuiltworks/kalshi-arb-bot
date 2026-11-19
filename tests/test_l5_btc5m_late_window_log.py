"""The late-window decision record: reporting only, and provably so.

WHAT IT IS FOR. To answer "were there real, executable opportunities the
shipped model rejected and the corrected one would have accepted", a
decision record needs the strike, the official BRTI bucket and its age,
BOTH probabilities, the executable prices WITH depth, and each candidate's
gate verdict and reason -- all as of the decision, not reconstructed
afterwards.

WHAT IT MUST NOT BE. A reporting field that can change a decision, delay
one, cost a venue call, or break a window when it cannot be built. These
tests are mostly about that half.

THE THREAD HAZARD THIS AVOIDS, since it is the reason for the design.
`exact_late_variance()` reads os.environ and `l5_btc_reference` polls on a
daemon thread. Computing the comparison by flipping that variable would
race the poller against the caller. `corrected_p_up` is instead a PURE
function of the estimate already made -- the correction changes only the
spread, and the estimate already carries the mean, the strike, sigma and
tau -- so no environment is touched and no second series read happens.
"""
from __future__ import annotations

import math

import pytest

from src import l5_btc5m_runner as RUN
from src import l5_btc_reference as BR


def _est(**kw):
    base = {"ok": True, "p_up": 0.6940, "reason": None,
            "seconds_left": 5.0, "strike": 100000.0, "index": 100001.0,
            "distance": 1.0, "bucket_t": 1795000000, "index_age_s": 0.4,
            "receive_lag_s": 0.2, "source": "VENUE_INDEX_BRTI_60S",
            "expected_settlement": 100001.0, "sd": 4.222,
            "sigma_used": 3.0}
    base.update(kw)
    return base


# ------------------------------------- corrected_p_up: pure and bounded

def test_it_reproduces_the_corrected_probability():
    """The documented 0.694 -> 0.974 move at five seconds."""
    got = BR.corrected_p_up(_est())
    assert got == pytest.approx(0.9738, abs=5e-4)


def test_it_declines_to_distinguish_where_the_estimators_agree():
    """At tau >= 60 the two are identical by construction, so a
    "corrected" number would be a false distinction."""
    assert BR.corrected_p_up(_est(seconds_left=60.0)) is None
    assert BR.corrected_p_up(_est(seconds_left=240.0)) is None


@pytest.mark.parametrize("bad", [
    {"ok": False}, {"seconds_left": None}, {"seconds_left": 0.0},
    {"seconds_left": -3.0}, {"strike": None}, {"sigma_used": 0.0},
    {"sigma_used": -1.0}, {"expected_settlement": None},
    {"seconds_left": float("nan")}, {"sigma_used": float("inf")},
])
def test_an_unusable_estimate_yields_no_corrected_probability(bad):
    assert BR.corrected_p_up(_est(**bad)) is None


@pytest.mark.parametrize("notdict", [None, 3, "x", [], ()])
def test_a_non_mapping_is_not_an_estimate(notdict):
    assert BR.corrected_p_up(notdict) is None


def test_it_touches_no_environment():
    """THE WHOLE POINT. A daemon thread is polling; this must not race it.

    The CODE is scanned, not the prose: the docstring explains that the
    function avoids os.environ, so scanning the raw source matched its own
    explanation. Stripping the docstring first is the honest test, and it
    is the second time in this session a doc comment tripped a
    string-matching test.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(BR.corrected_p_up).strip())
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]            # drop the docstring
    code = ast.unparse(fn)
    for forbidden in ("environ", "getenv", "setenv",
                      "exact_late_variance"):
        assert forbidden not in code, forbidden
    #: and the comparison it performs is arithmetic only
    assert "erfc" in code and "past_window_residual_var" in code


def test_it_is_monotone_in_distance_and_in_time():
    lo = BR.corrected_p_up(_est(expected_settlement=100000.5))
    hi = BR.corrected_p_up(_est(expected_settlement=100002.0))
    assert 0.5 < lo < hi <= 1.0
    near = BR.corrected_p_up(_est(seconds_left=5.0))
    far = BR.corrected_p_up(_est(seconds_left=40.0))
    assert near > far        # less time left, more certain


def test_the_shipped_estimator_is_not_consulted(monkeypatch):
    """Even with the production flag ON, this is the same function."""
    off = BR.corrected_p_up(_est())
    monkeypatch.setenv(BR.EXACT_LATE_VAR_ENV, BR.EXACT_LATE_VAR)
    assert BR.corrected_p_up(_est()) == off


# ----------------------------------------- the threshold and its volume

def test_the_threshold_is_where_the_estimators_begin_to_differ():
    assert RUN.LATE_LOG_S == 45.0
    on = BR.settlement_distribution(a_now=1.0, a_prev=1.0, sigma=1.0,
                                    seconds_left=RUN.LATE_LOG_S)["sd"]
    #: at 45s the shipped and corrected spreads agree to better than 1%,
    #: which is why logging above it would be rows of zero difference
    import os
    os.environ[BR.EXACT_LATE_VAR_ENV] = BR.EXACT_LATE_VAR
    try:
        ex = BR.settlement_distribution(a_now=1.0, a_prev=1.0, sigma=1.0,
                                        seconds_left=RUN.LATE_LOG_S)["sd"]
    finally:
        os.environ.pop(BR.EXACT_LATE_VAR_ENV, None)
    assert abs(ex / on - 1.0) < 0.01


def test_the_volume_is_nine_lines_a_window():
    """STEP_S paces the loop, so the extra lines per 5-minute window are
    bounded by this. A change to either constant must be deliberate."""
    assert RUN.STEP_S == 5.0
    assert math.ceil(RUN.LATE_LOG_S / RUN.STEP_S) == 9


# ---------------------------------- it is built AFTER the decision

def test_the_block_is_assembled_after_the_executor_has_stepped():
    """Reporting cannot alter or delay a decision. Pinned structurally:
    in the source, `self.ex.step(` appears BEFORE the block is built, and
    the block appears before the log call that carries it."""
    import inspect
    src = inspect.getsource(RUN)
    i_step = src.index("rep = self.ex.step(")
    i_late = src.index("_late = None")
    i_log = src.index('self._log("L5_BTC5M_STEP"')
    assert i_step < i_late < i_log


def test_the_block_is_wrapped_so_a_failure_cannot_stop_a_window():
    import inspect
    src = inspect.getsource(RUN)
    i_late = src.index("_late = None")
    tail = src[i_late:i_late + 4000]
    assert "except BaseException" in tail
    assert '_late = {"error": type(exc).__name__}' in tail


def test_no_venue_call_is_added():
    """Every field comes from `est`, `sized`, `book` or `rep`, all of
    which the step already had. A new HTTP call in this block would be a
    new failure mode on the decision path."""
    import inspect
    src = inspect.getsource(RUN)
    i_late = src.index("_late = None")
    block = src[i_late:src.index('self._log("L5_BTC5M_STEP"')]
    for forbidden in ("urlopen", "requests", "self.transport",
                      "self.book_fn", "self.p_fn", "venue"):
        assert forbidden not in block, forbidden


def test_order_submission_and_authorization_are_untouched():
    """The diff adds a constant, a log field and a pure helper. Nothing
    in the submission or authorization path may mention the new names."""
    import inspect
    from src import l5_btc5m_execution as X
    ex_src = inspect.getsource(X)
    for name in ("LATE_LOG_S", "corrected_p_up", "_late"):
        assert name not in ex_src, name
