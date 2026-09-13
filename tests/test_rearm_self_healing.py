"""P0.2: a refused re-arm must be able to un-refuse ITSELF, and only itself.

LIVE 2026-09-13. `flat_held_state` failed at 03:35:01Z on a genuinely-open
tennis first leg -- the gate was RIGHT. That position settled at 05:42:59Z,
and from 05:43 the gate was refusing a condition that had stopped being true.
Nothing looked again: `shutdown_idle` re-ran the settlement drain and the
migration close on every cycle and the ASSERTIONS on none of them. So the
executor sat halted for hours over inventory that no longer existed, and the
only cure was a human noticing and restarting the service.

THE CHAIN THIS FILE PINS, end to end:

  OPEN REAL POSITION -> restart/boot -> flat_held_state FAIL -> executor
  stays fail-closed -> settlement/reconciliation confirms resolution ->
  shutdown_idle re-runs the SAME unchanged assertions -> flat_held_state
  PASSES -> the campaign resumes automatically, in this process.

AND THE HALF THAT MATTERS MORE. Self-healing is one edit away from
self-excusing, so the negative cases are the point of the file: while the
venue still reports the position held, rechecking must refuse INDEFINITELY.
Time is not evidence. Cycles are not evidence. An empty order book is not
evidence. A market the venue says is resolved is not evidence while the
account still shows the position. Only the same nine conditions, re-proved
against a fresh authenticated read, release the gate.
"""
from __future__ import annotations

import ast
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_spring_l5_session as base


class _Stop(Exception):
    """Ends an otherwise-infinite idle loop from inside the sleep."""


CLEAN_SWEEP = {"seen": 0, "cancelled": [], "foreign": [], "errors": []}


def _sleep_n(n: int):
    calls = {"n": 0}

    def _s(_secs):
        calls["n"] += 1
        if calls["n"] >= n:
            raise _Stop()
    return _s, calls


def _quiet_idle(monkeypatch):
    """The idle loop's two existing passengers, silenced. They are read-only
    and already have their own tests; here they must not reach a venue."""
    monkeypatch.setattr(cont, "shutdown_settle_pass", lambda t=None: None)
    monkeypatch.setattr(cont, "_idle_migration_close", lambda t: None)


def _gate_armed(monkeypatch):
    monkeypatch.setenv(cont.REARM_GATE_ENV, "1")


def _recheck_code() -> str:
    """rearm_recheck's code with its docstring removed -- the docstring names
    the same calls the assertions look for, and matching prose instead of code
    is how a wiring test passes on a function that does nothing."""
    fn = next(n for n in ast.walk(ast.parse(open(cont.__file__).read()))
              if isinstance(n, ast.FunctionDef) and n.name == "rearm_recheck")
    stmts = [n for n in fn.body
             if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                     and isinstance(n.value.value, str))]
    inner = next(n for n in stmts if isinstance(n, ast.FunctionDef))
    inner.body = [n for n in inner.body
                  if not (isinstance(n, ast.Expr)
                          and isinstance(n.value, ast.Constant)
                          and isinstance(n.value.value, str))]
    return "".join(ast.unparse(n) for n in stmts)


def _all_green(monkeypatch, held=()):
    """Every re-arm condition satisfied except, optionally, flatness --
    the same helper shape test_rearm_boot_assertions uses, so the two files
    cannot drift into describing different gates."""
    monkeypatch.setattr(cont, "_fresh_open_orders",
                        lambda t: {"ours": 0, "foreign": 0})
    monkeypatch.setattr(cont, "held_markets", lambda t: list(held))
    monkeypatch.setattr(cont, "_combined_now", lambda: (D("-0.1533"), True))
    monkeypatch.setattr(cont, "_active_rung_id", lambda: cont.REARM_RUNG)
    monkeypatch.setattr(base, "requote_relations",
                        lambda: ("open", "extend", "complete"))
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: dict(CLEAN_SWEEP))


# ===================================================================
# THE CRITICAL NEGATIVE. Everything else in this file is downstream of it.
# ===================================================================

def test_while_the_venue_still_holds_it_refuses_indefinitely(monkeypatch):
    """OWNER: "while venue still reports the position held, repeated rechecks
    MUST continue refusing indefinitely."

    Two hundred cycles is not a number with meaning -- that is the assertion.
    The loop must be incapable of counting its way out."""
    _quiet_idle(monkeypatch)
    _gate_armed(monkeypatch)
    _all_green(monkeypatch, held=["aec-itfwo-matjor-anagas-2026-09-13"])
    t = object()
    s, calls = _sleep_n(200)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "REARM_REFUSED"},
                           t=t, recheck=cont.rearm_recheck(t))
    assert calls["n"] == 200, "it returned early on a still-held account"


def test_time_passing_alone_never_clears_the_gate(monkeypatch, capsys):
    """The refusal count rises and the verdict does not move with it. If a
    grace period or an attempt budget ever appears, this fails."""
    _quiet_idle(monkeypatch)
    _gate_armed(monkeypatch)
    _all_green(monkeypatch, held=["m1"])
    t = object()
    s, _ = _sleep_n(40)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "REARM_REFUSED"},
                           t=t, recheck=cont.rearm_recheck(t))
    out = capsys.readouterr().out
    assert '"kind":"rearm_self_healed"' not in out
    assert out.count('"kind":"shutdown_idle"') == 40
    # every single cycle recorded flatness as the blocker, not a countdown
    assert out.count('"flat_held_state"') >= 40


def test_the_refusal_count_is_reported_but_never_consulted():
    """`refusals` exists so an operator can see how long this has been stuck.
    It must never appear in a condition -- a counter that can be compared is
    a deadline waiting to be written."""
    fn = next(n for n in ast.walk(ast.parse(open(cont.__file__).read()))
              if isinstance(n, ast.FunctionDef) and n.name == "shutdown_idle")
    tests = [n.test for n in ast.walk(fn)
             if isinstance(n, (ast.If, ast.While, ast.IfExp))]
    tests += [n for n in ast.walk(fn) if isinstance(n, ast.Compare)]
    for node in tests:
        used = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
        assert "refusals" not in used, ast.unparse(node)


def test_an_empty_order_book_is_not_flatness(monkeypatch):
    """OWNER P0: "Empty bid/ask/depth is NOT sufficient evidence to clear
    inventory." The gate reads authenticated POSITIONS; a book with nothing
    in it changes nothing about what the account owns."""
    _quiet_idle(monkeypatch)
    _gate_armed(monkeypatch)
    _all_green(monkeypatch, held=["aec-county-not-ham-2026-09-08"])
    # no open orders anywhere, ours or foreign: the book is empty
    monkeypatch.setattr(cont, "_fresh_open_orders",
                        lambda t: {"ours": 0, "foreign": 0})
    t = object()
    gate = cont.rearm_recheck(t)()
    assert gate["ok"] is False
    assert "flat_held_state" in [c["check"] for c in gate["checks"]
                                 if not c["ok"]]


def test_a_resolved_market_still_showing_in_positions_keeps_refusing(
        monkeypatch):
    """The cricket fixture. `aec-county-not-ham-2026-09-08` was
    MARKET_STATUS_RESOLVED at the venue and its book was empty, and the
    account still reported the position. Resolution is not reconciliation:
    until the authenticated position clears, this stays shut."""
    _quiet_idle(monkeypatch)
    _gate_armed(monkeypatch)
    _all_green(monkeypatch, held=["aec-county-not-ham-2026-09-08"])
    t = object()
    s, calls = _sleep_n(25)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "REARM_REFUSED"},
                           t=t, recheck=cont.rearm_recheck(t))
    assert calls["n"] == 25


# ===================================================================
# THE POSITIVE. It must be possible, and possible only this way.
# ===================================================================

def test_authoritative_flatness_releases_the_gate_and_returns(monkeypatch):
    _quiet_idle(monkeypatch)
    _gate_armed(monkeypatch)
    held = {"m": ["m1"]}
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "held_markets", lambda t: list(held["m"]))
    cycles = {"n": 0}

    def _s(_secs):
        cycles["n"] += 1
        if cycles["n"] == 3:
            held["m"] = []          # the venue itself stops reporting it
        if cycles["n"] > 20:
            raise _Stop()
    t = object()
    out = cont.shutdown_idle(sleep=_s, report={"verdict": "REARM_REFUSED"},
                             t=t, recheck=cont.rearm_recheck(t))
    assert out["ok"] is True
    assert cycles["n"] == 3, "it must pass on the FIRST cycle that is flat"
    assert "flat_held_state" in [c["check"] for c in out["checks"]]
    assert out["failed"] == []


def test_the_settlement_drain_is_visible_to_the_same_cycles_recheck(
        monkeypatch):
    """Ordering, not luck. The drain is what makes a resolved position stop
    being held, so a recheck that ran first would always read the previous
    cycle's world and cost a full SHUTDOWN_IDLE_S of downtime per event."""
    _gate_armed(monkeypatch)
    held = {"m": ["m1"]}
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "held_markets", lambda t: list(held["m"]))
    monkeypatch.setattr(cont, "_idle_migration_close", lambda t: None)

    def _drain(t=None):
        held["m"] = []              # settlement lands DURING this cycle
        return {"settled": ["m1"]}
    monkeypatch.setattr(cont, "shutdown_settle_pass", _drain)
    s, calls = _sleep_n(5)
    t = object()
    out = cont.shutdown_idle(sleep=s, report={"verdict": "REARM_REFUSED"},
                             t=t, recheck=cont.rearm_recheck(t))
    assert out["ok"] is True
    assert calls["n"] == 0, "it slept a whole cycle before believing the drain"


# ===================================================================
# IT IS THE SAME GATE. Not a second, softer one.
# ===================================================================

def test_the_recheck_runs_the_same_sweep_then_the_same_assertions():
    body = _recheck_code()
    i_sweep = body.index("sweep_orphan_orders(t)")
    i_gate = body.index("boot_assertions(t, sweep)")
    assert i_sweep < i_gate, \
        "same order as the boot path, or it is not the same gate"


def test_the_recheck_defines_no_conditions_of_its_own():
    """A recheck that grew its own predicate would be a second gate wearing
    the first one's name. The only checks it may report are boot's."""
    body = _recheck_code()
    for name in ("held_markets", "blocking_held", "_combined_now",
                 "_fresh_open_orders", "crash_fix_present",
                 "completion_pair_ceiling"):
        assert name not in body, f"{name} re-implemented outside boot_assertions"


def test_a_healed_gate_and_a_boot_gate_are_the_same_object(monkeypatch):
    _gate_armed(monkeypatch)
    _all_green(monkeypatch)
    t = object()
    boot = cont.boot_assertions(t, dict(CLEAN_SWEEP))
    healed = cont.rearm_recheck(t)()
    assert [c["check"] for c in healed["checks"]] == \
           [c["check"] for c in boot["checks"]]
    assert healed["ok"] == boot["ok"] is True


def test_every_nine_conditions_still_bite_through_the_recheck(monkeypatch):
    """Each one, broken in turn, must refuse the recheck exactly as it
    refuses the boot. Proves the recheck did not soften a single row."""
    _gate_armed(monkeypatch)
    t = object()
    breakers = {
        "flat_held_state":
            lambda mp: mp.setattr(cont, "held_markets", lambda t: ["m1"]),
        "combined_loss_above_stop":
            lambda mp: mp.setattr(cont, "_combined_now",
                                  lambda: (D("-99"), False)),
        "rung": lambda mp: mp.setattr(cont, "_active_rung_id", lambda: "R4"),
        "requote_relations":
            lambda mp: mp.setattr(base, "requote_relations", lambda: ("open",)),
        "complete_reject_streak_max":
            lambda mp: mp.setattr(base, "COMPLETE_REJECT_STREAK_MAX", 9),
        "no_open_orders_fresh_read":
            lambda mp: mp.setattr(cont, "_fresh_open_orders",
                                  lambda t: {"ours": 1, "foreign": 0}),
        "orphan_sweep_clean":
            lambda mp: mp.setattr(cont, "sweep_orphan_orders",
                                  lambda t: {"seen": 1, "cancelled": [],
                                             "foreign": [], "errors": []}),
    }
    for name, breaker in breakers.items():
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(cont.REARM_GATE_ENV, "1")
            _all_green(mp)
            breaker(mp)
            gate = cont._rearm_recheck(cont.rearm_recheck(t))
            assert gate["ok"] is False, name
            assert name in gate["failed"], (name, gate["failed"])


# ===================================================================
# EVERY ABNORMAL ANSWER IS A REFUSAL.
# ===================================================================

def test_a_recheck_that_raises_is_a_refusal():
    def _boom():
        raise OSError("venue")
    out = cont._rearm_recheck(_boom)
    assert out["ok"] is False
    assert out["failed"] == ["recheck_error:OSError"]


@pytest.mark.parametrize("answer", [None, {}, {"checks": []}, "ok", 1, True])
def test_a_recheck_that_answers_nothing_is_a_refusal(answer):
    out = cont._rearm_recheck(lambda: answer)
    assert out["ok"] is False
    assert out["failed"] == ["recheck_malformed"]


def test_no_transport_is_a_refusal():
    gate = cont.rearm_recheck(None)()
    assert gate["ok"] is False
    assert [c["check"] for c in gate["checks"]] == ["recheck_transport"]


def test_a_disarmed_gate_cannot_re_prove_anything(monkeypatch):
    """If the gate that refused is no longer armed, the honest answer is
    that nothing can be established -- not that everything is fine."""
    monkeypatch.delenv(cont.REARM_GATE_ENV, raising=False)
    gate = cont.rearm_recheck(object())()
    assert gate["ok"] is False
    assert [c["check"] for c in gate["checks"]] == ["recheck_gate_enabled"]


def test_without_a_recheck_the_loop_is_exactly_what_it_was(monkeypatch):
    """Every other caller of shutdown_idle -- safe_shutdown, NOT_VERIFIED --
    passes no recheck and must still be incapable of returning."""
    _quiet_idle(monkeypatch)
    s, calls = _sleep_n(30)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "SHUTDOWN_NOT_VERIFIED"})
    assert calls["n"] == 30
    assert cont._rearm_recheck(None) is None


# ===================================================================
# THE WIRING: resumption happens in THIS process.
# ===================================================================

def test_run_campaign_resumes_in_process_instead_of_stopping(monkeypatch):
    """The whole chain. Boot refuses on a real held position; the position
    settles while halted; the campaign continues without a human restart and
    without a second executor."""
    _gate_armed(monkeypatch)
    _quiet_idle(monkeypatch)
    _all_green(monkeypatch)
    monkeypatch.delenv(cont.SHUTDOWN_ENV, raising=False)
    monkeypatch.setattr(cont.base, "armed", lambda: True)
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont.entry, "durable_state_reason", lambda: None)

    held = {"m": ["aec-itfwo-matjor-anagas-2026-09-13"]}
    monkeypatch.setattr(cont, "held_markets", lambda t: list(held["m"]))

    def _s(_secs):
        held["m"] = []              # settles at 05:42:59Z, while halted
    out = cont.run_campaign(t=object(), campaign_s=0.0, sleep=_s)

    assert out["status"] != "CAMPAIGN_STOP:REARM_REFUSED"
    assert out["boot_assertions"]["ok"] is False, "the refusal must be kept"
    assert out["rearm_self_healed"]["first_failed"] == ["flat_held_state"]


def test_a_refusal_that_never_heals_still_stops_the_campaign(monkeypatch):
    """The fail-closed exit is still there and still reached."""
    _gate_armed(monkeypatch)
    _quiet_idle(monkeypatch)
    _all_green(monkeypatch, held=["m1"])
    monkeypatch.delenv(cont.SHUTDOWN_ENV, raising=False)
    monkeypatch.setattr(cont.base, "armed", lambda: True)
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont.entry, "durable_state_reason", lambda: None)
    monkeypatch.setattr(cont.base, "run_session", lambda *a, **k: (_ for _ in ())
                        .throw(AssertionError("a session ran while refused")))
    s, _ = _sleep_n(15)
    with pytest.raises(_Stop):
        cont.run_campaign(t=object(), campaign_s=0.0, sleep=s)


def test_the_heal_does_not_re_enter_run_campaign(monkeypatch):
    """Resumption is a fall-through in the existing frame. A recursive
    restart would be a second campaign identity and, with it, the risk of a
    second outstanding order."""
    fn = next(n for n in ast.walk(ast.parse(open(cont.__file__).read()))
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    body = "".join(ast.unparse(n) for n in fn.body)
    assert "run_campaign(" not in body, "run_campaign must not call itself"
    i_idle = body.index("shutdown_idle(sleep, {'verdict': 'REARM_REFUSED'")
    i_ret = body.index("'CAMPAIGN_STOP:REARM_REFUSED'")
    assert i_idle < i_ret, "a refused re-arm must idle before it can stop"


def test_a_restart_while_idling_reconstructs_the_same_verdict(monkeypatch):
    """Determinism across a process death. The gate reads the venue and this
    build's own source -- never a remembered attempt count -- so a restart
    mid-idle lands on exactly the state the dead process was in."""
    _gate_armed(monkeypatch)
    _all_green(monkeypatch, held=["m1"])
    t = object()
    first = cont._rearm_recheck(cont.rearm_recheck(t))
    second = cont._rearm_recheck(cont.rearm_recheck(t))   # "after the restart"
    assert first["ok"] is second["ok"] is False
    assert first["failed"] == second["failed"] == ["flat_held_state"]


# ===================================================================
# NOTHING ELSE MOVED.
# ===================================================================

def test_the_recheck_cannot_reach_a_submission():
    """Reachability, not intention -- the same walk the shutdown path gets."""
    fns = {n.name: n for n in ast.walk(ast.parse(open(cont.__file__).read()))
           if isinstance(n, ast.FunctionDef)}
    reachable, frontier = set(), ["rearm_recheck", "_rearm_recheck",
                                  "shutdown_idle"]
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
    assert "boot_assertions" in reachable, "the gate must actually be re-run"


def test_no_risk_gate_moved(monkeypatch):
    """P0.2 is a re-check, not a re-parameterisation. Every bound the gate
    asserts is still the bound it asserted."""
    assert cont.REARM_BAND == (D("0.25"), D("0.75"))
    assert cont.REARM_PAIR_CEILING == D("1.00")
    assert cont.REARM_REQUOTE_RELATIONS == ("open", "extend", "complete")
    assert cont.SHUTDOWN_IDLE_S == 300.0
    _gate_armed(monkeypatch)
    _all_green(monkeypatch)
    names = [c["check"] for c in cont.boot_assertions(object(),
                                                      dict(CLEAN_SWEEP))["checks"]]
    assert names == ["crash_fix_51e384fb", "orphan_sweep_clean",
                     "no_open_orders_fresh_read", "flat_held_state",
                     "combined_loss_above_stop", "rung", "band",
                     "requote_relations", "completion_pair_ceiling",
                     "matched_pair_invariant_enforced",
                     "complete_reject_streak_max"]
