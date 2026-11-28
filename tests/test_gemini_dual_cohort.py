"""Two cohorts, one process, genuinely independent."""
import asyncio
import json
import os

import pytest

from src.gemini_execution import shadow_soak as SS


class _Args:
    def __init__(self, **kw):
        self.windows = 0
        self.cycles = 3
        self.interval = 0.0
        self.policy = "GEMINI_RECYCLE"
        self.band_lo = None
        self.band_hi = None
        self.cohorts = "btc5m,btc15m"
        self.state_root = "/tmp"
        self.evidence_dir = "x"
        self.out = "y"
        self.__dict__.update(kw)


# ============================================ independent evidence paths

def test_EACH_COHORT_GETS_ITS_OWN_EVIDENCE_DIRECTORY():
    ev5, out5 = SS._cohort_paths("/state", "BTC5m")
    ev15, out15 = SS._cohort_paths("/state", "BTC15m")
    assert ev5 != ev15 and out5 != out15
    assert ev5 == "/state/gemini-btc5m/continuous_v1"
    assert out5 == "/state/gemini-btc5m/btc5m_continuous.json"


def test_BTC15M_KEEPS_THE_PATH_IT_HAS_BEEN_WRITING_TO():
    """Adding a lane must not orphan the lane that already has history."""
    ev, out = SS._cohort_paths("/state", "BTC15m")
    assert ev == "/state/gemini-btc15m/continuous_v1"
    assert out == "/state/gemini-btc15m/btc15m_continuous.json"


# ================================================= failure isolation

def test_ONE_COHORT_RAISING_DOES_NOT_STOP_THE_OTHER(monkeypatch):
    """The whole reason both families live in one process rather than one lane.

    multi_async gathers two FOREVER loops, so it never returns on its own --
    the harness starts it as a task, waits for the healthy cohort to make
    progress while the broken one keeps failing, and then cancels.
    """
    done = {"btc5m": 0}
    raised = {"n": 0}

    async def fake_window(runner, *, cycles, interval_s, cohort, policy,
                          on_cycle=None):
        await asyncio.sleep(0)
        if cohort.name == "BTC15m":
            raised["n"] += 1
            raise RuntimeError("btc15m is broken")
        done["btc5m"] += 1
        return {"kind": "window", "symbol": "S", "cycles": cycles,
                "state": "COMPLETED", "hypothetical": {}, "inventory": {},
                "habitat": {}}

    monkeypatch.setattr(SS, "one_window", fake_window)
    monkeypatch.setattr(SS, "COHORT_BACKOFF_S", (0.0,))
    monkeypatch.setattr(SS, "MIN_BACKOFF_S", 0.001)

    async def run():
        task = asyncio.ensure_future(SS.multi_async(_Args()))
        for _ in range(2000):
            if done["btc5m"] >= 5 and raised["n"] >= 5:
                break
            await asyncio.sleep(0.002)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert done["btc5m"] >= 5, (
        "the healthy cohort was starved by the broken one (%d windows)"
        % done["btc5m"])
    assert raised["n"] >= 5, "the broken cohort stopped retrying"


def test_a_cohort_never_gives_up_it_backs_off():
    assert SS.COHORT_BACKOFF_S[0] < SS.COHORT_BACKOFF_S[-1]
    assert SS.COHORT_BACKOFF_S[-1] <= 300.0


def test_THE_BACKOFF_HAS_A_FLOOR_SO_A_BROKEN_COHORT_CANNOT_STARVE_ITS_SIBLING():
    """Found by driving the backoff to zero: a broken BTC15m spun 353,506
    times and BTC5m made no progress. Both families share one event loop, so
    an immediate retry is not a retry, it is a denial of service."""
    import ast
    import inspect
    assert SS.MIN_BACKOFF_S >= 0.5
    body = ast.unparse(ast.parse(inspect.getsource(SS.cohort_loop)))
    assert "max(MIN_BACKOFF_S" in body


def test_a_failing_cohort_reports_it_does_not_flood(capsys):
    hb = SS.CohortHeartbeat("BTC15m")
    for _ in range(50):
        hb.fail(RuntimeError("same thing every time"))
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) <= SS.ERROR_QUIET_AFTER
    assert hb.errors == 50, "it stops printing, it does not stop counting"
    assert hb.last_error["consecutive"] == 50


def test_a_new_error_breaks_the_quiet(capsys):
    hb = SS.CohortHeartbeat("BTC5m")
    for _ in range(10):
        hb.fail(RuntimeError("first"))
    capsys.readouterr()
    hb.fail(ValueError("second"))
    assert "second" in capsys.readouterr().out


def test_gather_cannot_cancel_a_sibling():
    import ast
    import inspect
    src = inspect.getsource(SS.multi_async)
    assert "return_exceptions=True" in src


def test_each_cohort_builds_its_own_runner():
    """No shared runner, band, guard, inventory or evidence dir."""
    import ast
    import inspect
    fn = ast.parse(inspect.getsource(SS.cohort_loop))
    body = ast.unparse(fn)
    assert "CR.ContinuousRunner(" in body
    assert "_cohort_paths(a.state_root, co.name)" in body


# ================================================= the heartbeat contract

def test_THE_HEARTBEAT_CARRIES_EVERY_FIELD_ASKED_FOR(capsys):
    hb = SS.CohortHeartbeat("BTC5m")
    hb.emit()
    out = capsys.readouterr().out
    assert out.startswith("L5_COHORT_HB ")
    row = json.loads(out[len("L5_COHORT_HB "):])
    for k in ("cohort", "current_contract", "cycles", "readable", "refused",
              "rungs_resting", "first_fills", "COMPLETE_fills",
              "matched_pairs", "residual", "last_rollover", "last_error"):
        assert k in row, k
    assert row["mutations_sent"] == 0
    assert "HYPOTHETICAL" in row["labels"]


def test_readable_and_refused_are_counted_apart(capsys):
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import hypothetical as HY
    hb = SS.CohortHeartbeat("BTC15m")
    hyp = HY.HypotheticalBook(symbol="S", policy="GEMINI_RECYCLE")
    inv = CP.Inventory()
    for i in range(4):
        hb.cycle(symbol="S", cycle=i + 1, tte_s=250.0, inv=inv, hyp=hyp,
                 book_readable=(i % 2 == 0), book_why=None)
    assert hb.readable == 2 and hb.refused == 2
    assert hb.cycles == 4


def test_a_cohort_heartbeat_is_never_summed_across_families():
    a = SS.CohortHeartbeat("BTC5m")
    b = SS.CohortHeartbeat("BTC15m")
    assert a.cohort != b.cohort
    assert a.__dict__.keys() == b.__dict__.keys()
    # they share no mutable state
    a.readable = 5
    assert b.readable == 0


def test_rungs_resting_is_live_not_end_of_window():
    """At window end the surface is withdrawn and the answer is always 0."""
    import inspect
    src = inspect.getsource(SS.one_window)
    assert "on_cycle(" in src
    doc = " ".join(inspect.getsource(SS.CohortHeartbeat.cycle).split())
    assert "rungs_resting" in doc


# ============================================ nothing about safety moved

def test_THE_DUAL_PATH_STILL_SENDS_NOTHING():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(SS))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "place" not in called and "cancel" not in called


def test_single_cohort_path_is_untouched_when_cohorts_is_absent():
    """Every existing invocation and test uses it; it must not change."""
    import ast
    import inspect
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(SS)))
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.unparse(fn)
    assert "if a.cohorts:" in body
    assert "return asyncio.run(main_async(a))" in body
