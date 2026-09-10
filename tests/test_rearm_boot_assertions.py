"""The nine re-arm conditions, as a GATE, not as a log line.

OWNER 2026-09-10, re-arming after the UnboundLocalError halt: "Before the
first submission, require: ... Clear PMUS_SPRING_SHUTDOWN and re-arm only
after those boot assertions pass."

WHY A GATE AND NOT A PRINTOUT. campaign_start has printed most of these
values for days. It printed `rung.pair_cost_max = 0.98` while the rule that
actually bound was 1.00; it printed nothing about complete_requote until a
null result turned out to be unattributable; and it printed a healthy boot
row for a build whose combined-loss gate raised on first execution. A value
in a log is a claim about the process. A gate is the process refusing.

WHAT EACH CHECK IS ALLOWED TO TRUST. Only what it can establish right now,
about itself or from a fresh venue read. Not the deployment dashboard, not
the commit message, not the previous run. An exception inside a check is
that check FAILING -- "could not establish" and "holds" must never collapse
into the same answer, which is the whole lesson of the -$15 brake reading a
number that had gone stale.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_spring_l5_session as base


class _Stop(Exception):
    pass


CLEAN_SWEEP = {"seen": 0, "cancelled": [], "foreign": [], "errors": []}


def _all_green(monkeypatch):
    """Every condition satisfied, so a single failure can be isolated."""
    monkeypatch.setattr(cont, "_fresh_open_orders",
                        lambda t: {"ours": 0, "foreign": 0})
    monkeypatch.setattr(cont, "held_markets", lambda t: [])
    monkeypatch.setattr(cont, "_combined_now", lambda: (D("-0.1533"), True))
    monkeypatch.setattr(cont, "_active_rung_id", lambda: "R5F")
    monkeypatch.setattr(base, "requote_relations",
                        lambda: ("open", "extend", "complete"))


def test_all_nine_pass_on_the_state_the_owner_described(monkeypatch):
    """-0.1533 against -15.00, flat, swept, R5F, 0.25-0.75, ceilings 1.00."""
    _all_green(monkeypatch)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is True, [c for c in rep["checks"] if not c["ok"]]
    names = [c["check"] for c in rep["checks"]]
    for required in ("crash_fix_51e384fb", "orphan_sweep_clean",
                     "no_open_orders_fresh_read", "flat_held_state",
                     "combined_loss_above_stop", "rung", "band",
                     "requote_relations", "completion_pair_ceiling",
                     "matched_pair_invariant_enforced",
                     "complete_reject_streak_max"):
        assert required in names, required


# ------------------------------------------------- each one actually gates
def test_the_crash_fix_check_is_not_a_tautology():
    """IT NEARLY WAS. The first crash_fix_present grepped the file for the
    gate's condition, found its OWN string literal first, then found its own
    copies of the binding and the call in the right order, and returned True
    -- on any build, including the one that died at 04:47:28Z.

    Parsing fixes it: run the same logic over source with the binding
    removed and it must say False. A self-check that cannot fail proves
    nothing, and this one is load-bearing for arming live money."""
    import ast
    import pathlib
    src = pathlib.Path(base.__file__).read_text()
    broken = src.replace(
        '                dec = report["decisions"][-1]\n'
        '                mark_noop(dec, f"combined_loss_stop:',
        '                mark_noop(dec, f"combined_loss_stop:')
    assert broken != src, "the binding moved; retarget this test"

    def _verdict(text):
        tree = ast.parse(text)
        want = "relation in ('open', 'extend') and risk_reduction_only"
        gates = [n for n in ast.walk(tree)
                 if isinstance(n, ast.If) and ast.unparse(n.test) == want]
        if len(gates) != 1:
            return False
        bind = use = None
        for i, st in enumerate(gates[0].body):
            if bind is None and isinstance(st, ast.Assign) and any(
                    isinstance(tg, ast.Name) and tg.id == "dec"
                    for tg in st.targets):
                bind = i
            if use is None and any(
                    isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id == "mark_noop" for c in ast.walk(st)):
                use = i
        return bind is not None and use is not None and bind < use

    assert _verdict(src) is True
    assert _verdict(broken) is False, "the check passes on the crashing build"
    assert base.crash_fix_present() is True


def test_the_crash_check_reads_statements_not_quotes():
    """No text search for the gate condition: that is what let a literal
    impersonate a statement."""
    import inspect
    src = inspect.getsource(base.crash_fix_present)
    assert "ast.parse" in src and "ast.walk" in src
    assert ".index(" not in src.split('"""')[-1], \
        "the executable body must not fall back to string indexing"


def test_a_stale_crashing_artifact_is_refused(monkeypatch):
    """Condition 1. A build without the binding is the build that died."""
    _all_green(monkeypatch)
    monkeypatch.setattr(base, "crash_fix_present", lambda: False)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert [c["check"] for c in rep["checks"] if not c["ok"]] == \
        ["crash_fix_51e384fb"]


@pytest.mark.parametrize("sweep", [
    {"seen": 1, "cancelled": [], "foreign": [], "errors": []},
    {"seen": 0, "cancelled": [], "foreign": [{"order_id": "x"}], "errors": []},
    {"seen": 0, "cancelled": [], "foreign": [], "errors": ["boom"]},
    None,
])
def test_any_sweep_doubt_refuses(monkeypatch, sweep):
    """Condition 2. 'No foreign ambiguity' means a sweep that saw nothing,
    cancelled nothing and errored on nothing -- not merely one that returned."""
    _all_green(monkeypatch)
    rep = cont.boot_assertions(object(), sweep)
    assert rep["ok"] is False
    assert "orphan_sweep_clean" in [c["check"] for c in rep["checks"]
                                    if not c["ok"]]


@pytest.mark.parametrize("rows", [{"ours": 1, "foreign": 0},
                                  {"ours": 0, "foreign": 1}])
def test_an_open_order_of_either_kind_refuses(monkeypatch, rows):
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "_fresh_open_orders", lambda t: rows)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "no_open_orders_fresh_read" in [c["check"] for c in rep["checks"]
                                           if not c["ok"]]


def test_the_open_order_check_is_a_fresh_read_not_the_sweeps_own_return():
    """The distinction safe_shutdown was built on: a cancel the venue did
    not honour looks identical to one it did, from the caller's side."""
    import ast
    import inspect
    src = inspect.getsource(cont._fresh_open_orders)
    assert "/v1/orders/open" in src
    # Scan the CODE, not the prose. The first version matched its own
    # docstring -- which names the sweep precisely to say it is not trusted
    # -- and failed for the opposite of the reason it was written. Same
    # mistake as the "no AWS fallback" test matching its own header comment.
    fn = ast.parse(src.lstrip()).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]                       # drop the docstring node
    assert "sweep" not in ast.unparse(fn)
    assert "sweep" in inspect.getdoc(cont._fresh_open_orders), \
        "the reason it is a fresh read must stay written down"


def test_held_inventory_refuses(monkeypatch):
    """Condition 3. The owner asked for a FLAT start."""
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "held_markets", lambda t: ["m1"])
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "flat_held_state" in [c["check"] for c in rep["checks"]
                                 if not c["ok"]]


def test_a_tripped_budget_refuses(monkeypatch):
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "_combined_now", lambda: (D("-15.00"), False))
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "combined_loss_above_stop" in [c["check"] for c in rep["checks"]
                                          if not c["ok"]]


def test_the_budget_check_is_strict_because_the_stop_is_inclusive():
    """combined_budget_tripped fires AT -15.00, so `>= stop` would arm a
    lane that the session would immediately refuse to trade in."""
    import inspect
    src = inspect.getsource(cont._combined_now)
    assert "combined > base.daily_loss_stop()" in src
    assert ">=" not in src


@pytest.mark.parametrize("rung", ["R4", "R5", "r5f", ""])
def test_any_rung_but_R5F_refuses(monkeypatch, rung):
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "_active_rung_id", lambda: rung)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "rung" in [c["check"] for c in rep["checks"] if not c["ok"]]


@pytest.mark.parametrize("rel", [("open", "extend"),
                                 ("open", "extend", "complete", "hold"),
                                 ("complete",)])
def test_dynamic_complete_must_cover_exactly_the_three(monkeypatch, rel):
    """Condition 6. OFF is the failure the owner cares about; a WIDER tuple
    is a different behaviour than the one authorised and also refuses."""
    _all_green(monkeypatch)
    monkeypatch.setattr(base, "requote_relations", lambda: rel)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "requote_relations" in [c["check"] for c in rep["checks"]
                                   if not c["ok"]]


def test_a_moved_completion_ceiling_refuses(monkeypatch):
    _all_green(monkeypatch)
    monkeypatch.setattr(base, "completion_pair_ceiling", lambda: D("1.03"))
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "completion_pair_ceiling" in [c["check"] for c in rep["checks"]
                                         if not c["ok"]]


def test_the_whole_position_invariant_must_be_WIRED_not_merely_defined(
        monkeypatch):
    """Conditions 7/8 are different rules. The marginal ceiling existed and
    passed its own tests while the AVERAGE pair reached 1.375 in simulation;
    only the call site makes it a rule."""
    assert cont._pair_invariant_enforced() is True
    monkeypatch.setattr(base, "__file__", "/nonexistent/x.py")
    _all_green(monkeypatch)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "matched_pair_invariant_enforced" in [c["check"] for c in
                                                 rep["checks"] if not c["ok"]]


def test_a_moved_rejection_breaker_refuses(monkeypatch):
    _all_green(monkeypatch)
    monkeypatch.setattr(base, "COMPLETE_REJECT_STREAK_MAX", 9)
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    assert "complete_reject_streak_max" in [c["check"] for c in rep["checks"]
                                            if not c["ok"]]


def test_the_band_is_checked_by_value():
    """0.25-0.75 is condition 5 and it is a MONEY surface: a wider band is
    a parameter change, which this re-arm explicitly forbids."""
    assert cont.REARM_BAND == (D("0.25"), D("0.75"))
    assert (base.SPRING_BAND_LO, base.SPRING_BAND_HI) == cont.REARM_BAND


# ------------------------------------------------------ failing is closed
def test_an_unestablishable_check_fails_rather_than_passing(monkeypatch):
    """The lesson of the stale budget: 'could not establish' must never read
    the same as 'holds'."""
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "held_markets",
                        lambda t: (_ for _ in ()).throw(OSError("venue")))
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    assert rep["ok"] is False
    bad = [c for c in rep["checks"] if c["check"] == "flat_held_state"][0]
    assert bad["got"] == "ERROR:OSError"


def test_boot_assertions_never_raises(monkeypatch):
    """It is called on the live boot path; raising there is a crash, and a
    crash rolls back to the previous build."""
    monkeypatch.setattr(base, "crash_fix_present",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    rep = cont.boot_assertions(None, None)
    assert rep["ok"] is False and rep["checks"]


# ----------------------------------------------------------- the wiring
def test_the_gate_ships_dark_and_an_owner_arms_it(monkeypatch):
    """It cannot be always-on: the nine conditions describe THIS re-arm, and
    a gate that refused a future R4 campaign would be a bug wearing a safety
    label. Wiring it unconditionally also hung the whole suite -- every
    existing armed test idled forever instead of returning."""
    monkeypatch.delenv(cont.REARM_GATE_ENV, raising=False)
    assert cont.rearm_gate_enabled() is False
    for raw in ("1", "true", "TRUE", "yes", "on", " On "):
        monkeypatch.setenv(cont.REARM_GATE_ENV, raw)
        assert cont.rearm_gate_enabled() is True, raw
    for raw in ("0", "false", "no", "off", "", "maybe", "2"):
        monkeypatch.setenv(cont.REARM_GATE_ENV, raw)
        assert cont.rearm_gate_enabled() is False, raw


def test_with_the_gate_off_the_checks_do_not_run(monkeypatch):
    """OFF must mean the previous behaviour exactly -- no venue reads, no
    refusal -- so arming it is the only thing that changes."""
    import ast
    src = open(cont.__file__).read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    body = ast.unparse(fn)
    i = body.index("boot_assertions(t, sweep)")
    assert "rearm_gate_enabled()" in body[max(0, i - 200):i + 200]


def test_the_gate_is_between_the_sweep_and_any_session(monkeypatch):
    """Defined but uncalled would leave the re-arm ungated."""
    import ast
    src = open(cont.__file__).read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    body = ast.unparse(fn)
    i_sweep = body.index("sweep_orphan_orders(t)")
    i_gate = body.index("boot_assertions(t, sweep)")
    assert i_sweep < i_gate
    assert "CAMPAIGN_STOP:REARM_REFUSED" in body


def test_a_failed_gate_holds_the_container_up_instead_of_exiting():
    """Same rule as the shutdown path: exiting reads as a crash and the
    platform's repair is a rollback -- here, to the build that crashed."""
    import ast
    src = open(cont.__file__).read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    body = ast.unparse(fn)
    # The idle call comes BEFORE the return, and carries the refusal verdict.
    # Looking only forward from the first "REARM_REFUSED" found the verdict
    # string inside that very call and then missed it.
    i_idle = body.index("shutdown_idle(sleep, {'verdict': 'REARM_REFUSED'")
    i_ret = body.index("'CAMPAIGN_STOP:REARM_REFUSED'")
    assert i_idle < i_ret, "a refused re-arm must idle, not exit"


def test_the_refusal_names_which_conditions_failed(monkeypatch):
    """An operator must not have to diff a log to find out what blocked it."""
    _all_green(monkeypatch)
    monkeypatch.setattr(cont, "_active_rung_id", lambda: "R4")
    monkeypatch.setattr(cont, "held_markets", lambda t: ["m1"])
    rep = cont.boot_assertions(object(), CLEAN_SWEEP)
    failed = [c["check"] for c in rep["checks"] if not c["ok"]]
    assert set(failed) == {"rung", "flat_held_state"}
    for c in rep["checks"]:
        assert "got" in c
