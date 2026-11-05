"""Same-pass two-sided quoting: the policy module and the controller loop.

THE CENTRAL GUARANTEE. With L5_DUAL_QUOTE unset the controller makes exactly
ONE placement per tick, which is today's behaviour and the reason full-suite
parity holds. Asserted against the literal 1, not against the module's own
constant.
"""
import collections
import inspect

import pytest

from src import l5_dual_quote as DQ
from src import l5_pmus_live_runner as R


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in (DQ.DUAL_ENV, DQ.REPLENISH_ENV):
        monkeypatch.delenv(k, raising=False)


# ------------------------------------------------------- default is today

def test_off_by_default():
    assert DQ.enabled() is False
    assert DQ.replenish_enabled() is False
    assert DQ.max_placements_per_pass() == 1
    assert DQ.opening_preference() is None


@pytest.mark.parametrize("tok", ["on", "On", "1", "true", "TRUE", "yes",
                                 "", " ", "OFF", "ON ", " ON", "ENABLED"])
def test_only_the_exact_token_enables(monkeypatch, tok):
    monkeypatch.setenv(DQ.DUAL_ENV, tok)
    expect = tok.strip() == "ON"
    assert DQ.enabled() is expect
    assert DQ.max_placements_per_pass() == (2 if expect else 1)


def test_enabled_allows_exactly_two(monkeypatch):
    monkeypatch.setenv(DQ.DUAL_ENV, "ON")
    assert DQ.max_placements_per_pass() == 2


def test_the_pass_ceiling_cannot_be_exceeded(monkeypatch):
    """A third order in one tick is not a pair and has no economics gated."""
    monkeypatch.setenv(DQ.DUAL_ENV, "ON")
    assert DQ.max_placements_per_pass() <= DQ.MAX_PLACEMENTS_CEILING == 2


# ------------------------------------------------------- replenishment

def test_replenish_requires_the_dual_switch_too(monkeypatch):
    """Replenishing a single-leg controller just adds unpaired first legs."""
    monkeypatch.setenv(DQ.REPLENISH_ENV, "ON")
    assert DQ.enabled() is False
    assert DQ.replenish_enabled() is False


def test_replenish_on_needs_both(monkeypatch):
    monkeypatch.setenv(DQ.DUAL_ENV, "ON")
    assert DQ.replenish_enabled() is False
    monkeypatch.setenv(DQ.REPLENISH_ENV, "ON")
    assert DQ.replenish_enabled() is True


# --------------------------------------------- L5 guidance is advisory

def test_opening_preference_is_l5s_measured_median(monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv(DQ.DUAL_ENV, "ON")
    assert DQ.opening_preference() == D("0.490")


def test_the_l5_facts_are_the_profitable_era_only():
    o = DQ.L5_OBSERVED
    assert o["windows"] == 214
    assert "August" in o["labels"] and "excluded" in o["labels"]
    assert o["residual_lost_frac"] < DQ.D("0.60")


def test_the_guidance_cannot_widen_a_band():
    """It returns a number and places nothing.

    Checks the BODY, not the docstring -- the docstring says "it places
    nothing", which a naive substring scan reads as the word "place".
    """
    import ast as _ast
    fn = _ast.parse(inspect.getsource(DQ.opening_preference)).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], _ast.Expr)
                           and isinstance(fn.body[0].value, _ast.Constant)
                           ) else fn.body
    code = "\n".join(_ast.dump(n) for n in body)
    for bad in ("environ", "setenv", "submit", "handle", "Call(func=Attribute"):
        assert bad not in code, bad


# ------------------------------------------------------------ safety

def test_policy_and_safety_are_disjoint():
    knobs = {"max_placements_per_pass", "opening_preference",
             "replenish_enabled", "enabled"}
    assert knobs & set(DQ.SAFETY) == set()


@pytest.mark.parametrize("must", [
    "max_total_exposure_usd", "max_cash_deployed_usd", "max_abs_residual",
    "ABS_RESIDUAL_CAP_CONTRACTS", "daily_realized_loss_halt_usd",
    "max_worst_case_deficit_usd", "max_live_orders", "reconciliation",
    "exact_order_cancellation", "watchdog_deadman", "L5_LIVE_HALT",
    "halt_latch", "completion_pair_ceiling", "max_pair_cost",
    "entry_cutoff_s", "complete_cutoff_s", "campaign_approval",
])
def test_the_named_non_negotiables_are_safety(must):
    assert must in DQ.SAFETY


# -------------------------------------------- the controller pass loop

def test_the_loop_runs_once_when_off():
    src = inspect.getsource(R.Runner.maybe_controller_open)
    assert "DQ.max_placements_per_pass()" in src
    assert "for _p in range(max(1, int(passes)))" in src


def test_a_later_pass_re_reads_the_book():
    """A stale book would price the complement against a moved market."""
    src = inspect.getsource(R.Runner.maybe_controller_open)
    i = src.index("for _p in range")
    tail = src[i:]
    assert "self.book.snapshot(slug)" in tail
    assert "PASS2_NO_BOOK" in tail


def test_a_later_pass_rechecks_the_slot_from_the_ledger():
    src = inspect.getsource(R.Runner.maybe_controller_open)
    tail = src[src.index("for _p in range"):]
    assert "still_fillable_order_count()" in tail
    assert "max_live_orders()" in tail
    assert "PASS2_NO_SLOT" in tail


def test_an_unreadable_ledger_ends_the_loop_fail_closed():
    src = inspect.getsource(R.Runner.maybe_controller_open)
    tail = src[src.index("for _p in range"):]
    assert "PASS2_LEDGER_UNREADABLE" in tail
    i = tail.index("PASS2_LEDGER_UNREADABLE")
    assert "break" in tail[i:i + 300], "must stop, not continue"


def test_a_refusal_ends_the_loop():
    """The precheck gates the PAIR, so a refused pair gets no second leg."""
    src = inspect.getsource(R.Runner.maybe_controller_open)
    tail = src[src.index("for _p in range"):]
    # the LAST occurrence is the loop-ending `if`; the first is the stats line
    i = tail.rindex('(rec or {}).get("refused_reason")')
    assert "break" in tail[i:i + 120], tail[i:i + 120]


def test_every_pass_is_a_full_handle_call():
    """Nothing may bypass a gate by reusing pass one's verdict."""
    src = inspect.getsource(R.Runner.maybe_controller_open)
    tail = src[src.index("for _p in range"):]
    assert tail.count("self.executor.handle(") == 1, (
        "one call site inside the loop, so both passes take the same path")


def test_the_second_pass_is_separately_observable():
    src = inspect.getsource(R.Runner.maybe_controller_open)
    assert 'pass2:' in src


def test_the_complement_side_is_not_forced_by_the_controller():
    """Claim state picks the unclaimed leg; the controller must not override."""
    src = inspect.getsource(R.Runner.maybe_controller_open)
    tail = src[src.index("for _p in range"):]
    for bad in ('dec["side"] =', "dec['side'] =", '"side": _other',
                "flip", "opposite"):
        assert bad not in tail, bad


def test_the_module_is_in_both_images():
    for d in ("deploy/l5_pmus_live/Dockerfile",
              "deploy/l5_live_probe/Dockerfile"):
        assert "COPY src/l5_dual_quote.py" in open(d).read(), d


def test_no_top_level_alias_is_bound_twice():
    """The FM collision broke 58 tests; guard DQ the same way."""
    import ast
    tree = ast.parse(open("src/l5_pmus_live_runner.py").read())
    seen = collections.defaultdict(set)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                if al.asname:
                    seen[al.asname].add(al.name)
    assert {a: sorted(m) for a, m in seen.items() if len(m) > 1} == {}


def test_describe_reports_what_is_in_force(monkeypatch):
    d = DQ.describe()
    assert d["enabled"] is False and d["max_placements_per_pass"] == 1
    monkeypatch.setenv(DQ.DUAL_ENV, "ON")
    d = DQ.describe()
    assert d["enabled"] is True and d["max_placements_per_pass"] == 2
    assert "max_total_exposure_usd" in d["safety_never_touched_here"]
