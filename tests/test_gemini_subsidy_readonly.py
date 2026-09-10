"""subsidy_verify must be structurally incapable of placing an order.

This guard runs in CI BEFORE the Gemini secrets enter scope, so a broken
read-only contract kills the job while it still holds nothing.

The module is the only authenticated Gemini path we intend to run, and it
runs where real credentials are. "It only reads" has to be a property of the
import graph, not a claim in a docstring.
"""
from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path("src/gemini_maker/subsidy_verify.py")
AUTH = pathlib.Path("src/gemini_prediction_auth.py")

ORDER_ENDPOINTS = ("/v1/order/new", "/v1/order/cancel", "/v1/order/status",
                   "/v1/cancel", "order/new", "neworder")
ORDER_VERBS = ("place_order", "submit_order", "new_order", "post_order",
               "cancel_order", "run_session")


def _tree(p): return ast.parse(p.read_text())


def test_subsidy_verify_names_no_order_endpoint():
    text = SRC.read_text()
    for ep in ORDER_ENDPOINTS:
        assert ep not in text, f"order endpoint {ep!r} present"


def test_subsidy_verify_calls_no_order_function():
    calls = {ast.unparse(n.func) for n in ast.walk(_tree(SRC))
             if isinstance(n, ast.Call)}
    for verb in ORDER_VERBS:
        assert not any(verb in c for c in calls), f"{verb} reachable"


def test_it_imports_only_the_read_side_of_the_auth_helper():
    names = set()
    for n in ast.walk(_tree(SRC)):
        if isinstance(n, ast.ImportFrom) and "gemini_prediction_auth" in (n.module or ""):
            names |= {a.name for a in n.names}
    assert names == {"has_credentials", "private_rest_request"}, names


def test_the_auth_helper_itself_carries_no_order_transport():
    """The dependency matters as much as the caller."""
    text = AUTH.read_text()
    for ep in ORDER_ENDPOINTS:
        assert ep not in text, f"auth helper references {ep!r}"


def test_it_fails_closed_without_credentials():
    text = SRC.read_text()
    assert "if not has_credentials():" in text
    assert '"verdict": "NO_CREDENTIALS"' in text or "NO_CREDENTIALS" in text


def test_evidence_declares_its_venue_and_capture_time():
    """The whole reason this exists: an untagged credit cannot calibrate any
    venue, and we nearly re-attributed a PMUS sample to Gemini for exactly
    that reason."""
    text = SRC.read_text()
    assert '"venue": "gemini"' in text
    assert '"generated_utc"' in text
    assert '"read_only": True' in text


def test_no_secret_value_is_ever_printed_or_persisted():
    """Names may appear; values must never be read into the payload."""
    text = SRC.read_text()
    for bad in ("os.getenv('GEMINI_API_SECRET')", 'os.getenv("GEMINI_API_SECRET")',
                "os.environ['GEMINI_API_SECRET']", 'os.environ["GEMINI_API_SECRET"]'):
        assert bad not in text, "must not read the secret value directly"


def test_the_workflow_pins_the_l5_branch_and_commits_only_evidence():
    wf = pathlib.Path(".github/workflows/gemini-subsidy-verify.yml").read_text()
    assert "ref: feature/l5-transfer-backtest" in wf
    assert "concurrency:" in wf, "a second run could commit over the first"
    assert "[skip ci]" in wf, "the evidence commit must not re-trigger CI"
    assert "analysis/gemini/subsidy_verify_" in wf
    assert "refusing to commit files outside analysis/gemini/" in wf
    # No AWS fallback in anything that RUNS. Scan the executable YAML only:
    # the header comment deliberately names /opt/arb and the AWS container to
    # record why that host is NOT used, and a test that cannot tell a comment
    # from a command fails for the wrong reason -- the same trap as asserting
    # on a docstring that says "stop submitting".
    executable = "\n".join(l for l in wf.splitlines()
                           if not l.lstrip().startswith("#"))
    for bad in ("aws ", "ssh ", "/opt/arb", "AWS_ACCESS_KEY"):
        assert bad not in executable, f"no AWS fallback permitted: {bad!r}"
    assert "/opt/arb" in wf, "the header must still record what was rejected"


def test_the_guard_runs_before_secrets_enter_scope():
    wf = pathlib.Path(".github/workflows/gemini-subsidy-verify.yml").read_text()
    guard = wf.index("test_gemini_subsidy_readonly.py")
    secret = wf.index("GEMINI_API_KEY:")
    assert guard < secret, "the read-only guard must precede credential use"
