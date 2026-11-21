"""Where L5_LIVE_HALT actually stops an order, and where it does not.

THE CLAIM UNDER TEST, because an earlier report of mine got it wrong.
`l5_intent_gate.GATE_ORDER` lists OPERATOR_HALT at position 4, ahead of
SHADOW_ONLY_LOCK and DURABLE_HALT_LATCH -- but that module is a READ-ONLY
evaluation layer. Its own docstring says it "DOES NOT REWRITE THE EXECUTOR"
and evaluates intents "without submitting anything", so its ordering is a
MODEL of the enforcing chain and not the chain. Citing it as proof of
enforcement was wrong.

WHAT IS ACTUALLY TRUE.

  * The final wire call is `PmusTransport.submit`, which begins
    `self._require_armed()` and raises NotArmed when `armed` is False.
  * The executor's in-path HALT_GATE reads `self.halt_gate` -- the DURABLE
    REMOTE latch -- and never reads the local env.
  * `L5_LIVE_HALT` binds through the ARMED FLAG, at three sites:
      1. `Runner.try_arm` returns before arming while halted;
      2. `authorize_arming` gate 3 raises ArmingRefusal("HALTED");
      3. two per-tick sweeps disarm an armed transport and fire the deadman.

So it is not a wire-path READ. It is wire-path EFFECTIVE via `armed`, with
at most one tick of exposure if asserted while already armed.
"""
import inspect
import io

import pytest

from src import l5_live_authorization as AUTH
from src import l5_pmus_transport as T


def _src(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


# ------------------------------------------- the final wire call requires arm

def test_submit_requires_armed_as_its_first_act():
    """Parsed, not line-sniffed: a docstring's continuation lines do not
    start with a quote, so a textual scan mistakes them for statements."""
    import ast
    import textwrap
    body = inspect.getsource(T.PmusTransport.submit)
    fn = ast.parse(textwrap.dedent(body)).body[0]
    stmts = [st for st in fn.body
             if not (isinstance(st, ast.Expr)
                     and isinstance(st.value, ast.Constant)
                     and isinstance(st.value.value, str))]
    first = stmts[0]
    assert isinstance(first, ast.Expr), ast.dump(first)[:120]
    assert isinstance(first.value, ast.Call)
    assert first.value.func.attr == "_require_armed", ast.dump(first)[:160]


def test_require_armed_raises_rather_than_returning_falsy():
    body = inspect.getsource(T.PmusTransport._require_armed)
    assert "if not self.armed:" in body
    assert "raise NotArmed" in body


def test_a_disarmed_transport_cannot_submit():
    t = T.PmusTransport.__new__(T.PmusTransport)
    t.armed = False
    t.preflight_report = {"preflight": "PASSED"}
    with pytest.raises(T.NotArmed):
        t._require_armed()


def test_an_armed_transport_passes_that_guard():
    t = T.PmusTransport.__new__(T.PmusTransport)
    t.armed = True
    t.preflight_report = {"preflight": "PASSED"}
    assert t._require_armed() is None


# ------------------------- the in-path halt is the REMOTE latch, not the env

def test_the_executor_in_path_halt_reads_the_durable_gate_only():
    from src import l5_live_execution as E
    body = inspect.getsource(E.FaithfulExecutor.handle)
    assert "hg = self.halt_gate" in body
    assert "hg.submissions_permitted()" in body
    # and it refuses on a gate it cannot read
    assert "HALT_GATE_UNREADABLE" in body
    # the LOCAL env halt is not consulted here at all
    assert "self.halted()" not in body
    assert "L5_LIVE_HALT" not in body


def test_the_executor_has_no_local_halt_accessor():
    from src import l5_live_execution as E
    assert not hasattr(E.FaithfulExecutor, "halted")


# ------------------------------- the local env halt binds via the armed flag

def test_arming_is_refused_while_the_local_halt_is_set():
    """Gate 3 of authorize_arming, fed `self.halted()` by the runner."""
    s = _src("src/l5_live_authorization.py")
    assert "if halted:" in s
    assert 'raise ArmingRefusal("HALTED"' in s
    assert "halted: bool" in s


def test_the_runner_feeds_its_own_env_halt_into_that_gate():
    s = _src("src/l5_pmus_live_runner.py")
    assert "halted=self.halted()," in s


def test_try_arm_returns_before_arming_while_halted():
    s = _src("src/l5_pmus_live_runner.py")
    i = s.index("def try_arm(")
    assert "or self.halted():" in s[i:i + 300]


def test_two_per_tick_sweeps_disarm_an_armed_halted_transport():
    s = _src("src/l5_pmus_live_runner.py")
    needle = "if self.halted() and self.transport is not None and self.transport.armed:"
    assert s.count(needle) == 2, s.count(needle)
    assert 'self.transport.disarm("operator_halt")' in s


def test_halted_is_the_local_env_var_and_nothing_else():
    """UPDATED FOR THE DELEGATION, with the same intent and a stronger
    check. `Runner.halted` no longer names the variable itself: it calls
    the module-level `operator_halted()`, which the authorization preflight
    calls too, so the readiness verdict and `try_arm` cannot disagree about
    whether the halt is engaged -- the 2026-11-09 08:30Z defect.

    Asserting on the literal's LOCATION would now fail for a refactor that
    made the property stronger. So this asserts the property: `halted`
    delegates and reads no environment of its own, the delegate reads that
    one variable and nothing else, and any non-empty value still halts.
    """
    s = _src("src/l5_pmus_live_runner.py")
    i = s.index("def halted(")
    block = s[i:i + 600]
    assert "operator_halted()" in block
    assert "getenv" not in block          # it reads no env of its own

    j = s.index("def operator_halted(")
    delegate = s[j:j + 2600]
    assert "LIVE_HALT_ENV" in delegate
    # any non-empty value halts; clearing requires an EMPTY value
    assert ".strip()" in delegate
    assert s.index('LIVE_HALT_ENV = "L5_LIVE_HALT"') > 0

    # and the behaviour itself, not only the source
    import os
    from src import l5_pmus_live_runner as _R
    before = os.environ.get("L5_LIVE_HALT")
    try:
        for raw, want in (("1", True), ("0", True), ("", False),
                          ("   ", False), ("anything", True)):
            os.environ["L5_LIVE_HALT"] = raw
            assert _R.operator_halted() is want, raw
    finally:
        if before is None:
            os.environ.pop("L5_LIVE_HALT", None)
        else:
            os.environ["L5_LIVE_HALT"] = before


# --------------------------------- submission_gate() is REPORTING, not a gate

def test_submission_gate_does_not_gate_on_the_local_halt():
    """The field that misled me: it is reported beside the gate, not in it."""
    s = _src("src/l5_pmus_live_runner.py")
    i = s.index("def submission_gate(")
    j = s.index("def effective_cash_gate(", i)
    block = s[i:j]
    # it reports the four conditions it blocks on...
    for cond in ("shadow_lock", "halt_latch", "owner_arm_token", "armed"):
        assert cond in block
    # ...and self.halted() is NOT among them
    assert "self.halted()" not in block


def test_the_heartbeat_reports_both_and_names_them_apart():
    s = _src("src/l5_pmus_live_runner.py")
    assert '"halt": self.submission_gate().get("blocked")' in s
    assert '"halt_local_env": self.halted()' in s


def test_the_intent_gate_is_declared_read_only():
    """So its GATE_ORDER cannot be cited as enforcement evidence."""
    s = _src("src/l5_intent_gate.py")
    assert "DOES NOT REWRITE THE EXECUTOR" in s
    assert "without submitting anything" in s
    from src import l5_intent_gate as G
    assert "OPERATOR_HALT" in G.GATE_ORDER
