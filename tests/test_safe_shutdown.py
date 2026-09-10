"""HALT -> cancel -> VERIFY -> stop -> preserve -> disarm.

OWNER 2026-09-10, after the R5F halt on MIL -2.5: "We should not design a
live autonomous system whose safe shutdown procedure is 'Brett, open the app
and cancel whatever is left.'" The defect was structural -- sweep_orphan_orders
sat inside the `if armed:` branch, so disarming was the exact transition that
could orphan a resting order, and the disarmed process that came up next never
swept. These tests pin the fix and, more importantly, pin that it FAILS CLOSED.
"""
from __future__ import annotations

import pytest

from src import pmus_l5_continuous_session as cont


class FakeQ1Base:
    def __init__(self, pages):
        self.pages = list(pages)
        self.reads = 0

    def _get_with_retry(self, t, path):
        assert path == "/v1/orders/open"
        self.reads += 1
        page = self.pages[min(self.reads - 1, len(self.pages) - 1)]
        if isinstance(page, Exception):
            raise page
        return page


class _Stop(Exception):
    pass


def _sleep_n(n):
    calls = {"n": 0}
    def _s(_secs):
        calls["n"] += 1
        if calls["n"] >= n:
            raise _Stop
    return _s, calls


@pytest.fixture
def wired(monkeypatch):
    def _wire(pages, *, ours=("m1",), sweep=None, held=("m1",)):
        fake = FakeQ1Base(pages)
        monkeypatch.setattr(cont.base.q1, "base", fake, raising=False)
        monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
        monkeypatch.setattr(cont, "ledger_slugs", lambda: set(ours))
        monkeypatch.setattr(cont, "held_markets", lambda t: list(held))
        monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: (
            sweep if sweep is not None
            else {"seen": 1, "cancelled": [{"order_id": "o1", "slug": "m1",
                                            "confirmed": True}],
                  "foreign": [], "errors": []}))
        return fake
    return _wire


def test_verified_only_when_a_fresh_read_shows_ours_gone(wired):
    wired([{"orders": []}])
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_VERIFIED"
    assert rep["still_open_ours"] == []


def test_verification_is_a_second_read_not_the_sweep_s_own_word(wired):
    """The whole point. A cancel the venue did not honour is indistinguishable
    from one it did, looking only at the sweep's return value."""
    fake = wired([{"orders": [{"id": "o1", "marketSlug": "m1"}]}],
                 sweep={"seen": 1, "cancelled": [{"order_id": "o1",
                                                  "slug": "m1",
                                                  "confirmed": True}],
                        "foreign": [], "errors": []})
    rep = cont.safe_shutdown(t=object())
    assert fake.reads == 1, "must re-read the venue after sweeping"
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"] == "ours_still_open"
    assert rep["still_open_ours"] == [{"order_id": "o1", "slug": "m1"}]


def test_a_failed_verify_read_is_not_verified_never_assumed_clean(wired):
    wired([ConnectionError("boom")])
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"].startswith("verify_read_failed:")


def test_sweep_errors_block_verification(wired):
    wired([{"orders": []}],
          sweep={"seen": 1, "cancelled": [], "foreign": [],
                 "errors": ["o1:Timeout"]})
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"] == "sweep_errors"


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: False)
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"] == "no_credentials"


def test_a_foreign_order_does_not_block_our_shutdown_but_is_reported(wired):
    """Not ours to cancel. Reporting it is the honest outcome; refusing to
    confirm OUR shutdown because someone else has an order would make the
    procedure unusable."""
    wired([{"orders": [{"id": "x9", "marketSlug": "not-ours"}]}])
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_VERIFIED"
    assert rep["foreign_open"] == [{"order_id": "x9", "slug": "not-ours"}]


def test_held_inventory_is_preserved_and_reported_never_liquidated(wired):
    wired([{"orders": []}], held=("m1", "m2"))
    rep = cont.safe_shutdown(t=object())
    assert rep["held_preserved"] == ["m1", "m2"]
    # scan the EXECUTABLE body, not the docstring -- prose about "stop
    # submitting" is not a call to submit, and a test that cannot tell the
    # difference would fail for the wrong reason and pass for the wrong one
    import ast
    tree = ast.parse(open(cont.__file__).read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "safe_shutdown")
    stmts = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)
                            and isinstance(fn.body[0].value.value, str)) else fn.body
    code = "\n".join(ast.unparse(st) for st in stmts)
    for banned in ("place_order", "submit_order", "run_session", "sell"):
        assert banned not in code, f"shutdown path must never {banned}"
    calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(code))
             if isinstance(n, ast.Call)}
    assert "sweep_orphan_orders" in calls
    assert not any("place" in c or "submit" in c for c in calls), calls


def test_shutdown_short_circuits_before_armed_is_consulted(monkeypatch):
    """It must not matter whether the process is armed: the orphan window is
    created BY disarming, so a shutdown that only works while armed fixes
    nothing.

    This test used to assert on run_campaign's RETURN value. Once the halted
    state stopped exiting (LIVE 00:57:19Z), that premise died and the test
    HUNG on the real 300s idle rather than failing -- a hang says nothing
    about what broke. It now injects the sleep, which is the only way to
    observe a loop that is supposed to be infinite."""
    monkeypatch.setenv(cont.SHUTDOWN_ENV, "1")
    seen = {}
    monkeypatch.setattr(cont, "safe_shutdown",
                        lambda t=None: {"verdict": "SHUTDOWN_VERIFIED"})
    monkeypatch.setattr(cont.base, "armed",
                        lambda: seen.setdefault("armed_consulted", True))
    s, calls = _sleep_n(2)
    with pytest.raises(_Stop):
        cont.run_campaign(t=object(), campaign_s=0.0, sleep=s)
    assert calls["n"] == 2
    assert "armed_consulted" not in seen


def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(cont.SHUTDOWN_ENV, raising=False)
    assert cont.shutdown_requested() is False
    for on in ("1", "YES", "yes", "true"):
        monkeypatch.setenv(cont.SHUTDOWN_ENV, on)
        assert cont.shutdown_requested() is True


# --------------------------------------------------- never exit while halted

def test_shutdown_idle_never_returns_it_holds_the_container_up():
    """LIVE 2026-09-10T00:57:19Z: the first shutdown verified and then EXITED.
    The platform read the exit as a CRASH and rolled back to the PREVIOUS
    deployment. Had the verdict been NOT_VERIFIED that rollback could have
    restored an ARMED build -- a failed safety check resuming trading."""
    s, calls = _sleep_n(3)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "SHUTDOWN_VERIFIED"})
    assert calls["n"] == 3, "must keep looping, not return after one pass"


def test_idle_holds_up_even_when_verification_FAILED(monkeypatch):
    """The dangerous case. A NOT_VERIFIED shutdown must stay up loudest of
    all -- it is the state where an orphan may still be resting."""
    s, calls = _sleep_n(2)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s,
                           report={"verdict": "SHUTDOWN_NOT_VERIFIED",
                                   "reason": "ours_still_open"})
    assert calls["n"] == 2


def test_run_campaign_idles_after_shutdown_and_never_falls_through(monkeypatch):
    monkeypatch.setenv(cont.SHUTDOWN_ENV, "1")
    monkeypatch.setattr(cont, "safe_shutdown",
                        lambda t=None: {"verdict": "SHUTDOWN_NOT_VERIFIED"})
    reached = {"armed": False}
    monkeypatch.setattr(cont.base, "armed",
                        lambda: reached.__setitem__("armed", True))
    s, _ = _sleep_n(2)
    with pytest.raises(_Stop):
        cont.run_campaign(t=object(), campaign_s=0.0, sleep=s)
    assert reached["armed"] is False, "must never reach the trading path"


def test_the_idle_call_sits_between_the_report_and_the_return():
    """Wiring, not decoration: a shutdown_idle defined but not called would
    leave the exit-on-verify bug exactly where it was."""
    import ast
    tree = ast.parse(open(cont.__file__).read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    src = ast.unparse(fn)
    i_log = src.index("safe_shutdown")
    i_idle = src.index("shutdown_idle(")
    i_ret = src.index("'SHUTDOWN'")
    assert i_log < i_idle < i_ret


# ============ the dangerous case, made literal (OWNER 2026-09-10) ============
# NOT_VERIFIED means an order MAY still be resting. That is precisely when the
# process must not die: a dead container is "repaired" by rolling back to the
# previous deployment, which may be an ARMED one.

def test_not_verified_stays_alive_inert_and_keeps_saying_so(monkeypatch, capsys):
    monkeypatch.setenv(cont.SHUTDOWN_ENV, "1")
    monkeypatch.setattr(cont, "safe_shutdown", lambda t=None: {
        "verdict": "SHUTDOWN_NOT_VERIFIED", "reason": "ours_still_open",
        "still_open_ours": [{"order_id": "o1", "slug": "m1"}],
        "held_preserved": ["m1"]})

    def _must_not_run(*a, **k):
        raise AssertionError("a session ran while shut down")
    monkeypatch.setattr(cont.base, "run_session", _must_not_run)
    monkeypatch.setattr(cont.base, "armed",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("armed() consulted while shut down")))

    s, calls = _sleep_n(4)
    with pytest.raises(_Stop):
        cont.run_campaign(t=object(), campaign_s=0.0, sleep=s)

    assert calls["n"] == 4, "must keep idling, not exit after reporting"
    out = capsys.readouterr().out
    # _log emits COMPACT json (no space after the colon); asserting the
    # pretty form silently matched nothing and the test failed for the
    # wrong reason
    assert out.count('"kind":"shutdown_idle"') >= 3, \
        "the heartbeat must keep identifying the shutdown state"
    assert "SHUTDOWN_NOT_VERIFIED" in out


def test_no_order_submission_function_is_reachable_from_the_shutdown_path():
    """Reachability, not intention. Walk every function the shutdown branch
    can call and assert none of them submits."""
    import ast
    tree = ast.parse(open(cont.__file__).read())
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    reachable, frontier = set(), ["safe_shutdown", "shutdown_idle"]
    while frontier:
        name = frontier.pop()
        if name in reachable or name not in fns:
            continue
        reachable.add(name)
        for call in ast.walk(fns[name]):
            if isinstance(call, ast.Call):
                f = call.func
                frontier.append(f.id if isinstance(f, ast.Name)
                                else getattr(f, "attr", ""))
    banned = {"run_session", "place_order", "submit_order", "post_order",
              "open_order", "extend", "complete"}
    assert not (reachable & banned), reachable & banned
    assert "sweep_orphan_orders" in reachable, "cancel must still be reachable"


def test_a_healthy_process_cannot_be_restarted_back_into_an_armed_campaign():
    """Belt two. run_campaign never returns, so main() never loops -- but if a
    future refactor ever made it return, the supervisor must still refuse.
    'The process is healthy' is not a reason to resume trading."""
    assert cont.is_recoverable("SHUTDOWN_VERIFIED") is False
    assert cont.is_recoverable("SHUTDOWN_NOT_VERIFIED") is False
    for s in cont.RECOVERABLE_EXACT:
        assert not str(s).startswith("SHUTDOWN")
    assert not any(str(p).startswith("SHUTDOWN")
                   for p in cont.RECOVERABLE_PREFIXES)
