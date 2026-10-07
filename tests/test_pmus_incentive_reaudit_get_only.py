"""The credentialed re-audit path must be provably GET-only, must never print
a credential value, and must refuse to share a process with an armed probe."""
import ast
import inspect
import os

import pytest

from src import pmus_incentive_audit as ia
from src import pmus_q1_executor as q1
from src import pmus_settlement_auditor as auditor
import scripts.pmus_incentive_reaudit as reaudit

MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def _request_methods(fn_or_mod):
    src = inspect.getsource(fn_or_mod)
    out = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = getattr(f, "attr", getattr(f, "id", ""))
        if name in ("request", "_get_with_retry") and n.args:
            a = n.args[0] if name == "request" else None
            out.append(a.value if isinstance(a, ast.Constant) else
                       ("GET" if name == "_get_with_retry" else "UNKNOWN"))
    return out


def test_every_transport_call_in_the_audit_module_is_a_literal_GET():
    methods = _request_methods(ia)
    assert methods and set(methods) == {"GET"}


def test_the_activity_walk_it_delegates_to_is_also_get_only():
    assert set(_request_methods(auditor.activity_pages)) == {"GET"}
    # and the retry helper it uses hardcodes GET, with mutations never retried
    assert set(_request_methods(q1._get_with_retry)) == {"GET"}


def _code_strings(mod):
    """String constants that are CODE, so a comment recording history is not
    mistaken for a live mutation path."""
    tree = ast.parse(inspect.getsource(mod))
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docstrings.add(d)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docstrings]


def test_the_audit_module_holds_no_order_mutation_path():
    strings = _code_strings(ia)
    for s in strings:
        assert not s.startswith("/v1/order")
        assert s not in MUTATING
    src = inspect.getsource(ia)
    for bad in ("signed_headers", "place_order", "ledger_append"):
        assert bad not in src


def test_the_runner_holds_no_mutation_path_either():
    for s in _code_strings(reaudit):
        assert not s.startswith("/v1/order")
        assert s not in MUTATING


def test_credential_state_is_a_presence_boolean_and_never_a_value(monkeypatch):
    monkeypatch.setenv(q1.KEY_ENV, "not-a-real-key")
    monkeypatch.setenv(q1.SECRET_ENV, "not-a-real-secret")
    p = reaudit.credential_presence()
    assert p == {q1.KEY_ENV: True, q1.SECRET_ENV: True}
    assert all(isinstance(v, bool) for v in p.values())
    blob = repr(p) + inspect.getsource(reaudit)
    assert "not-a-real-key" not in blob and "not-a-real-secret" not in blob
    assert os.environ[q1.KEY_ENV] not in blob


def test_it_refuses_to_run_in_a_process_that_is_armed(monkeypatch):
    monkeypatch.setenv(q1.LIVE_ENV, "YES")
    with pytest.raises(SystemExit) as e:
        reaudit.main(["--out", "/dev/null", "--dry-run"])
    assert "REFUSING" in str(e.value)
    assert "YES" not in str(e.value).split("(")[-1]   # names vars, not values


def test_the_output_keeps_the_three_week_gap_as_its_own_measurement():
    assert reaudit.PRIOR_MEASUREMENT_ENDED_UTC.startswith("2026-09-14T22:59:21")
    assert reaudit.PRIOR_MEASUREMENT["confirmed_incentives_usd"] == "16.18"
