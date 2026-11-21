"""The eight-step activation chain: proof ends at the serving process.

OWNER: "A deployment request, SUCCESS build, or pushed commit alone is not
activation proof."

Every test below is a way that sentence could be violated. The chain has
already been violated once per mechanism in production -- 9912e9a8 read
SKIPPED and left broken code serving, d2624218 built SUCCESS and collected
nothing -- so each is pinned rather than trusted.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from src import pmus_boundary_activation as A


# --------------------------------------------------------------- fixtures
def _green(**over):
    """Everything the chain needs, satisfied. Override one key to break one
    step and only that step."""
    kit = {
        "read_flat": lambda: {"flat": True, "held": [], "status": "FLAT",
                              "flat_verified": True},
        "run_preflight": _pre,
        "deploy": lambda: {"id": "dep-1"},
        "read_deployment": lambda _id: {"status": "SUCCESS"},
        "read_serving": lambda: {"commit": "abc1234",
                                 "components": list(A.REQUIRED_COMPONENTS)},
        "check_envelope": lambda _s: {"ok": True, "mismatches": [],
                                      "rung": "R5F"},
        "expected_sha": "abc1234def",
    }
    kit.update(over)
    return kit


PRE_CHECKS = ("held_inventory_reconciled", "zero_orphan_or_foreign_ambiguity",
              "settlements_current", "combined_loss_budget_permits",
              "selector_has_fresh_candidate", "quarantine_tests_green",
              "envelope_unchanged")


def _pre(**over):
    """A seven-assertion preflight row. Pass False to fail one, the string
    "unknown" to leave one unanswered."""
    rows = []
    for name in PRE_CHECKS:
        v = over.get(name, True)
        rows.append({"check": name, "ok": v is True,
                     **({"unknown": True} if v == "unknown" else {})})
    return {"ok": all(r["ok"] for r in rows), "checks": rows,
            "failed": [r["check"] for r in rows if not r["ok"]]}


def _boom(*_a, **_kw):
    raise RuntimeError("transport died")


# ------------------------------------------------- the one permitting state
def _all_states():
    """Every module-level state constant, discovered -- so a state added
    later is covered by this test without anyone remembering to add it."""
    return [v for k, v in vars(A).items()
            if k.isupper() and isinstance(v, str) and k != "REQUIRED_COMPONENTS"]


def test_activation_proven_is_the_only_state_that_opens_acquisition():
    states = _all_states()
    assert A.ACTIVATION_PROVEN in states
    assert len(states) >= 12, "states collapsed; each failure mode is named"
    for s in states:
        assert A.may_acquire(s) is (s == A.ACTIVATION_PROVEN), s


@pytest.mark.parametrize("junk", [None, "", "proven", "ACTIVATION_PROVEN ",
                                  "activation_proven", True, 1, {}])
def test_may_acquire_refuses_anything_that_is_not_exactly_the_state(junk):
    assert A.may_acquire(junk) is False


def test_happy_path_proves_and_is_the_only_path_that_does():
    out = A.activate(**_green(), now=lambda: 1700.0)
    assert out["state"] == A.ACTIVATION_PROVEN
    assert out["may_acquire"] is True
    assert out["proven_at"] == 1700.0
    assert [s["step"] for s in out["steps"]] == [
        "flat", "preflight", "deploy_requested", "deploy_success",
        "serving_sha", "serving_components", "serving_envelope"]
    assert all(s["ok"] and not s["unknown"] for s in out["steps"])


# --------------------------------------------- each step stops on its own
STOPS = [
    # step 1: flat, from the probe's own authority
    ("read_flat", lambda: {"flat": False, "held": ["nfl-x"]}, A.IDLE_NOT_FLAT),
    ("read_flat", _boom, A.IDLE_FLATNESS_UNKNOWN),
    ("read_flat", lambda: None, A.IDLE_FLATNESS_UNKNOWN),
    ("read_flat", lambda: {"status": "HOLD"}, A.IDLE_FLATNESS_UNKNOWN),
    # steps 2-3: the seven preflight assertions
    ("run_preflight", lambda: _pre(settlements_current=False),
     A.IDLE_PREFLIGHT_FAILED),
    ("run_preflight", lambda: _pre(held_inventory_reconciled="unknown"),
     A.IDLE_PREFLIGHT_UNKNOWN),
    ("run_preflight", lambda: {"ok": True, "checks": [
        {"check": "c", "ok": True}]}, A.IDLE_PREFLIGHT_UNKNOWN),
    ("run_preflight", lambda: {"ok": True, "checks": []},
     A.IDLE_PREFLIGHT_UNKNOWN),
    ("run_preflight", _boom, A.IDLE_PREFLIGHT_UNKNOWN),
    ("run_preflight", lambda: None, A.IDLE_PREFLIGHT_UNKNOWN),
    # step 4: the explicit deploy
    ("deploy", lambda: {}, A.DEPLOY_REQUEST_FAILED),
    ("deploy", lambda: {"id": None}, A.DEPLOY_REQUEST_FAILED),
    ("deploy", _boom, A.DEPLOY_REQUEST_FAILED),
    # step 5: SUCCESS, not "requested"
    ("read_deployment", lambda _i: {"status": "SKIPPED"}, A.DEPLOY_NOT_SUCCESS),
    ("read_deployment", lambda _i: {"status": "BUILDING"}, A.DEPLOY_NOT_SUCCESS),
    ("read_deployment", lambda _i: {"status": "CRASHED"}, A.DEPLOY_NOT_SUCCESS),
    ("read_deployment", _boom, A.DEPLOY_NOT_SUCCESS),
    # step 6: the SERVING process's SHA
    ("read_serving", lambda: {"commit": "999beef",
                              "components": list(A.REQUIRED_COMPONENTS)},
     A.SERVING_SHA_MISMATCH),
    ("read_serving", lambda: {"components": list(A.REQUIRED_COMPONENTS)},
     A.SERVING_SHA_UNKNOWN),
    ("read_serving", _boom, A.SERVING_SHA_UNKNOWN),
    # step 6b: the approved components
    ("read_serving", lambda: {"commit": "abc1234",
                              "components": ["ranked_selector"]},
     A.SERVING_COMPONENTS_MISSING),
    ("read_serving", lambda: {"commit": "abc1234", "components": []},
     A.SERVING_COMPONENTS_MISSING),
    ("read_serving", lambda: {"commit": "abc1234"},
     A.SERVING_COMPONENTS_UNKNOWN),
    # step 7: the envelope, re-run from the new process
    ("check_envelope", lambda _s: {"ok": False, "mismatches": ["cap"]},
     A.SERVING_ENVELOPE_FAILED),
    ("check_envelope", lambda _s: {"mismatches": []},
     A.SERVING_ENVELOPE_UNKNOWN),
    ("check_envelope", _boom, A.SERVING_ENVELOPE_UNKNOWN),
    ("check_envelope", lambda _s: None, A.SERVING_ENVELOPE_UNKNOWN),
]


@pytest.mark.parametrize("key,fn,want", STOPS,
                         ids=[f"{k}->{w}" for k, fn, w in STOPS])
def test_each_break_stops_the_chain_in_its_own_named_state(key, fn, want):
    out = A.activate(**_green(**{key: fn}))
    assert out["state"] == want
    assert out["may_acquire"] is False
    assert out["proven_at"] is None


def test_every_stop_state_is_reachable_by_some_break():
    """No state exists that nothing can produce, and none is unreachable."""
    produced = {A.activate(**_green(**{k: fn}))["state"] for k, fn, _w in STOPS}
    named = {v for v in _all_states() if v != A.ACTIVATION_PROVEN}
    assert produced == named, sorted(named ^ produced)


# ------------------------------------------- UNKNOWN is never merged with FAIL
def test_unknown_and_fail_are_distinct_states_not_synonyms():
    pairs = [(A.IDLE_NOT_FLAT, A.IDLE_FLATNESS_UNKNOWN),
             (A.IDLE_PREFLIGHT_FAILED, A.IDLE_PREFLIGHT_UNKNOWN),
             (A.SERVING_SHA_MISMATCH, A.SERVING_SHA_UNKNOWN),
             (A.SERVING_COMPONENTS_MISSING, A.SERVING_COMPONENTS_UNKNOWN),
             (A.SERVING_ENVELOPE_FAILED, A.SERVING_ENVELOPE_UNKNOWN)]
    for failed, unknown in pairs:
        assert failed != unknown
        assert not A.may_acquire(failed) and not A.may_acquire(unknown)


@pytest.mark.parametrize("key", ["read_flat", "run_preflight", "deploy",
                                 "read_deployment", "read_serving",
                                 "check_envelope"])
def test_an_exception_is_recorded_unknown_and_never_as_a_negative_answer(key):
    """'The venue did not answer' must never be written down as 'no'."""
    out = A.activate(**_green(**{key: _boom}))
    last = out["steps"][-1]
    assert last["unknown"] is True, (key, last)
    assert last["ok"] is False
    assert "transport died" in str(last["detail"])


def test_preflight_unknown_wins_over_preflight_failed():
    """A run with both a hard failure and an unanswered check is UNKNOWN:
    we do not know the failure list is complete."""
    out = A.activate(**_green(run_preflight=lambda: _pre(
        settlements_current=False, held_inventory_reconciled="unknown")))
    assert out["state"] == A.IDLE_PREFLIGHT_UNKNOWN
    assert out["failed"] == ["settlements_current"]
    assert out["unknown"] == ["held_inventory_reconciled"]


# --------------------------- a request / a build / a commit are not proof
def test_a_deploy_request_alone_is_not_activation():
    out = A.activate(**_green(read_deployment=lambda _i: {"status": "BUILDING"}))
    assert out["deployment_id"] == "dep-1"          # the request succeeded
    assert out["may_acquire"] is False              # and proves nothing

def test_a_success_build_alone_is_not_activation():
    """The build is SUCCESS and the old process is still serving."""
    out = A.activate(**_green(
        read_serving=lambda: {"commit": "a88dff63",
                              "components": list(A.REQUIRED_COMPONENTS)}))
    assert [s for s in out["steps"] if s["step"] == "deploy_success"][0]["ok"]
    assert out["state"] == A.SERVING_SHA_MISMATCH
    assert out["may_acquire"] is False


def test_the_expected_sha_alone_is_not_activation():
    """Right code, wrong envelope: the SHA matches and acquisition stays shut."""
    out = A.activate(**_green(
        check_envelope=lambda _s: {"ok": False, "mismatches": ["session_cap"]}))
    assert [s for s in out["steps"] if s["step"] == "serving_sha"][0]["ok"]
    assert out["state"] == A.SERVING_ENVELOPE_FAILED
    assert out["mismatches"] == ["session_cap"]


def test_envelope_is_rechecked_against_the_serving_process_not_the_old_one():
    seen = []
    A.activate(**_green(check_envelope=lambda s: seen.append(s) or {"ok": True}))
    assert seen and seen[0]["commit"] == "abc1234"


def test_sha_match_is_prefix_tolerant_but_not_substring_sloppy():
    short = _green(read_serving=lambda: {
        "commit": "abc1234", "components": list(A.REQUIRED_COMPONENTS)})
    assert A.activate(**short)["state"] == A.ACTIVATION_PROVEN
    # a SHA that merely CONTAINS the expected one is not the same revision
    off = _green(read_serving=lambda: {
        "commit": "0abc1234def", "components": list(A.REQUIRED_COMPONENTS)})
    assert A.activate(**off)["state"] == A.SERVING_SHA_MISMATCH


# --------------------------------------------- ordering: nothing runs early
def test_no_deploy_is_requested_before_flat_and_preflight_both_pass():
    for key, fn, _w in STOPS:
        if key not in ("read_flat", "run_preflight"):
            continue
        calls = []
        A.activate(**_green(**{key: fn},
                            deploy=lambda: calls.append(1) or {"id": "x"}))
        assert calls == [], f"{key}/{_w} requested a deploy anyway"


def test_a_not_flat_probe_never_touches_the_deployment_api():
    calls = []
    out = A.activate(**_green(
        read_flat=lambda: {"flat": False},
        deploy=lambda: calls.append("deploy") or {"id": "x"},
        read_deployment=lambda _i: calls.append("read") or {"status": "SUCCESS"},
        read_serving=lambda: calls.append("serving") or {"commit": "abc1234"}))
    assert calls == []
    assert out["state"] == A.IDLE_NOT_FLAT


# ------------------------------------------------------- the module itself
def test_activate_never_raises_however_badly_the_injectables_behave():
    keys = ["read_flat", "run_preflight", "deploy", "read_deployment",
            "read_serving", "check_envelope"]
    bad = [_boom, lambda *a: None, lambda *a: "not a mapping",
           lambda *a: {"ok": object()}, lambda *a: 0]
    for k in keys:
        for fn in bad:
            out = A.activate(**_green(**{k: fn}))
            assert out["may_acquire"] is False
            assert isinstance(out["state"], str)


def test_module_holds_no_credentials_and_cannot_deploy_by_itself():
    """All venue and Railway access is injected. The module must not be able
    to reach either on its own, so it can be exercised without secrets."""
    src = inspect.getsource(A)
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "time", "typing"}, sorted(imported)
    # Over CODE only: the docstring names railway.json as the thing that
    # misled us, which is the opposite of a dependency on it.
    code = ast.unparse(ast.parse(_strip_docstrings(tree)))
    for banned in ("environ", "getenv", "requests", "httpx", "urllib",
                   "API_KEY", "SECRET", "subprocess", "railway", "open("):
        assert banned not in code, banned


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.fix_missing_locations(tree)


def test_required_components_are_the_four_that_were_approved():
    assert A.REQUIRED_COMPONENTS == (
        "ranked_selector", "settlement_horizon_protection",
        "residual_quarantine", "boundary_reader")


def test_components_are_asserted_not_merely_recorded():
    """Every one of the four is individually load-bearing."""
    for drop in A.REQUIRED_COMPONENTS:
        rest = [c for c in A.REQUIRED_COMPONENTS if c != drop]
        out = A.activate(**_green(
            read_serving=lambda: {"commit": "abc1234", "components": rest}))
        assert out["state"] == A.SERVING_COMPONENTS_MISSING, drop
        assert out["missing"] == [drop]


def test_a_preflight_that_ran_fewer_than_seven_assertions_is_unknown():
    """A stubbed or crashed checker returns ok=True over nothing. That shape
    must never reach the deploy step."""
    calls = []
    for n in range(7):
        rows = [{"check": f"c{i}", "ok": True} for i in range(n)]
        out = A.activate(**_green(
            run_preflight=lambda rows=rows: {"ok": True, "checks": rows},
            deploy=lambda: calls.append(1) or {"id": "x"}))
        assert out["state"] == A.IDLE_PREFLIGHT_UNKNOWN, n
    assert calls == []
    assert A.activate(**_green())["state"] == A.ACTIVATION_PROVEN


def test_a_non_boolean_verdict_is_never_read_as_a_pass():
    """Truthiness is not a verdict: only True passes, only False fails."""
    for key, mk in (("read_flat", lambda v: (lambda: {"flat": v})),
                    ("run_preflight", lambda v: (lambda: dict(_pre(), ok=v))),
                    ("check_envelope", lambda v: (lambda _s: {"ok": v}))):
        for junk in (object(), 1, "yes", [1], {"a": 1}):
            out = A.activate(**_green(**{key: mk(junk)}))
            assert out["may_acquire"] is False, (key, junk)
            assert out["steps"][-1]["unknown"] is True, (key, junk)


def test_the_row_always_carries_its_own_labels_and_expected_sha():
    for kit in (_green(), _green(read_flat=lambda: {"flat": False})):
        out = A.activate(**kit)
        assert out["kind"] == "boundary_activation"
        assert out["expected_sha"] == "abc1234def"
        assert out["required_components"] == list(A.REQUIRED_COMPONENTS)
        assert "SERVING process" in out["labels"]


# ------------------------------------------------------------- the seams
def test_real_preflight_output_binds_to_the_chain():
    """d2624218 built SUCCESS and collected nothing because no test crossed
    the seam between two modules. This one crosses it."""
    from src import pmus_boundary_preflight as P
    out = P.preflight(t=None, ranked_candidate=None, held_slugs=[],
                      run_tests=False)
    assert {"ok", "checks", "failed"} <= set(out)
    assert len(out["checks"]) == 7, "owner's seven assertions"
    assert all("check" in c and "ok" in c for c in out["checks"])
    # a real (failing) preflight row drives the chain to the idle state
    assert len(out["checks"]) >= A.EXPECTED_PREFLIGHT_CHECKS
    got = A.activate(**_green(run_preflight=lambda: out))
    assert got["state"] == A.IDLE_PREFLIGHT_FAILED
    assert got["failed"] == out["failed"]


def test_activate_signature_is_what_callers_bind_to():
    sig = inspect.signature(A.activate)
    assert list(sig.parameters) == [
        "read_flat", "run_preflight", "deploy", "read_deployment",
        "read_serving", "check_envelope", "expected_sha", "components", "now"]
    sig.bind(**_green())          # the fixture is a real call, not a guess


def test_the_transition_image_actually_ships_this_module():
    """A production-only ImportError is the same class of bug as the
    production-only FileNotFoundError that shipped once already: the module
    imports fine here and is absent from the image. Bind the two."""
    import os
    df = "deploy/pmus_boundary_transition/Dockerfile"
    assert os.path.exists(df), df
    body = open(df).read()
    assert "COPY src/ /app/src/" in body, (
        "the image no longer copies all of src/; "
        "pmus_boundary_activation must be copied explicitly")
    mod = A.__name__.split(".")[-1]
    assert os.path.exists(f"src/{mod}.py")


def test_the_transition_module_imports_this_one_at_module_scope():
    """The wiring is real, not a docstring."""
    from src import pmus_boundary_transition as BT
    assert BT.ba is A
    assert hasattr(BT, "REFUSED_PROOF")
