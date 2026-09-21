"""LIVE_READINESS_GATE, and the nine integration misses it must catch.

Every one of these reached owner authorization looking correct. For each, the
defect is RECREATED here and the gate must return FAIL. A gate that only
passes on the happy path would have passed on all nine.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import live_envelope as LE
from src.gemini_execution import live_readiness as LR

BRANCH = "claude/gemini-btc-economic-viability-h3u7zl"


def _expectation(**over):
    base = dict(
        branch=BRANCH, commit_sha="deadbeef", executable_sha="exe",
        envelope_sha="env", bundle_sha="bun",
        auth_id="OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-3W-1",
        family="btc15m", symbol_family="GEMI-BTC15M",
        envelope_id="GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1",
        action_size="1", cohort_prefix="GEMI-BTC15M",
        remaining_budget=36, max_working_orders=2,
        max_committed_usd="2.50", max_residual_usd="2.00",
        max_concurrent_markets=1, minimum_rest_time_s="30.0",
        argv=("--family", "btc15m", "--envelope", "live_q1_btc15m"))
    base.update(over)
    return LR.Expectation(**base)


def _resolved(**over):
    """A RESOLVED set that passes, so each test can break exactly one thing."""
    e = _expectation()
    r = {k: getattr(e, k) for k in
         ("branch", "commit_sha", "executable_sha", "envelope_sha",
          "bundle_sha", "auth_id", "family", "symbol_family", "envelope_id",
          "action_size", "cohort_prefix", "remaining_budget",
          "max_working_orders", "max_committed_usd", "max_residual_usd",
          "max_concurrent_markets", "minimum_rest_time_s")}
    r.update({"frame_quantity": "1", "policy_max_rungs": 2,
              "rest_floor_invoked": True, "retained_counts_as_risk": True,
              "mutations_sent": 0})
    r.update(over)
    return r


def _gate(expected=None, resolved=None):
    e = expected or _expectation()
    r = resolved or _resolved()
    checks = {"fields": LR.compare(e, r),
              "cross_field": LR.cross_field_invariants(r)}
    return LR.build_manifest(e, r, checks)


def test_the_happy_path_passes_and_is_hashed():
    m = _gate()
    assert m["verdict"] == LR.PASS, m["checks"]
    assert len(m["manifest_sha"]) == 64


def test_the_manifest_hash_changes_when_any_resolved_value_changes():
    """Readiness goes STALE if anything moves afterwards."""
    a = _gate()
    b = _gate(resolved=_resolved(action_size="0.01"))
    assert a["manifest_sha"] != b["manifest_sha"]


# =================================================== THE NINE HISTORICAL MISSES

def test_1_stale_checkout_branch():
    """Run 35553352750: workflow from one branch, source from another."""
    got = LR.stale_reference_sweep(
        branch="claude/l5-execution-instrumentation-io6nzi",
        cohort_prefix="GEMI-BTC15M", envelope_id="X")
    assert got["ok"] is False
    kinds = {h["kind"] for h in got["hits"]}
    assert "checkout_ref" in kinds or "latch_branch" in kinds


def test_2_pipeline_failure_hidden_by_tee():
    """`python ... | tee` exits with tee's status. The 0.09s argparse death
    that was recorded SUCCESS."""
    import subprocess
    import tempfile

    def _status(prelude):
        with tempfile.NamedTemporaryFile("w", suffix=".sh",
                                         delete=False) as fh:
            fh.write(prelude + 'python3 -c "import sys; sys.exit(3)" '
                     '2>&1 | tee ' + fh.name + '.out\n')
            path = fh.name
        return subprocess.run(["bash", "-e", path]).returncode

    assert _status("") == 0, "the defect: a failing command exits 0"
    assert _status("set -o pipefail\n") == 3, "the fix must propagate"

    # and the production workflow must carry the fix
    import pathlib
    wf = pathlib.Path(".github/workflows/gemini-settlement-gate.yml").read_text()
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    run = armed.split("run:", 1)[1]
    assert run.index("set -o pipefail") < run.index("python -m")


def test_3_stale_latch_push_branch():
    """LIVE-1 died here: the latch pushed to a branch it shared no history
    with, spending an owner grant with nothing traded."""
    got = LR.stale_reference_sweep(branch="some-other-branch",
                                   cohort_prefix="GEMI-BTC15M",
                                   envelope_id="X")
    assert got["ok"] is False
    assert any(h["kind"] == "latch_branch" for h in got["hits"])


def test_4_authorized_q1_but_wire_carries_0_01():
    """LIVE-2 exactly: the envelope was a CAP and the wire sent 0.01."""
    m = _gate(resolved=_resolved(frame_quantity="0.01"))
    assert m["verdict"] == LR.FAIL
    assert any("wire" in v or "frame" in v
               for v in m["checks"]["cross_field"]["violations"])


def test_5_frozen_rest_floor_not_invoked_on_the_live_path():
    """stale_orders(): tested, asserted, called nowhere in production."""
    m = _gate(resolved=_resolved(rest_floor_invoked=False))
    assert m["verdict"] == LR.FAIL
    assert any("minimum_rest_time" in v
               for v in m["checks"]["cross_field"]["violations"])


def test_6_envelope_exists_but_the_cli_cannot_select_it():
    """The BTC15m envelope was proposed before the CLI could reach it."""
    import subprocess
    out = subprocess.run(
        ["python", "-m", "src.gemini_execution.run_continuous", "--help"],
        capture_output=True, text=True).stdout
    assert "live_q1_btc15m" in out, (
        "an envelope the invocation cannot name is not authorizable")
    assert "--family" in out


def test_7_cohort_exists_but_the_runner_defaults_to_the_wrong_family():
    """contract_cohort.BTC15M existed; nothing threaded it, so every run fell
    through to BTC5m."""
    m = _gate(resolved=_resolved(family="btc5m",
                                 symbol_family="GEMI-BTC05M"))
    assert m["verdict"] == LR.FAIL
    # both the field comparison AND a cross-field rule must object
    assert any(d["field"] == "family" for d in m["checks"]["fields"]["diffs"])
    assert m["checks"]["cross_field"]["ok"] is False


def test_8_family_envelope_mismatch():
    m = _gate(resolved=_resolved(family="btc5m",
                                 symbol_family="GEMI-BTC05M",
                                 envelope_id=LE.LIVE_Q1_BTC15M.name))
    assert m["verdict"] == LR.FAIL
    assert any("BTC15m envelope" in v
               for v in m["checks"]["cross_field"]["violations"])


def test_9_bundle_changed_after_review():
    m = _gate(resolved=_resolved(bundle_sha="a-different-bundle"))
    assert m["verdict"] == LR.FAIL
    assert any(d["field"] == "bundle_sha"
               for d in m["checks"]["fields"]["diffs"])


# ============================================ additional structural rules

def test_an_envelope_too_small_for_the_policys_surface_fails():
    """The check that would have caught a 2-order envelope against a policy
    emitting 4 rungs."""
    m = _gate(resolved=_resolved(policy_max_rungs=4, max_working_orders=2))
    assert m["verdict"] == LR.FAIL
    assert any("rungs" in v for v in m["checks"]["cross_field"]["violations"])


def test_retained_orders_must_count_as_risk():
    m = _gate(resolved=_resolved(retained_counts_as_risk=False))
    assert m["verdict"] == LR.FAIL


def test_any_mutation_in_readiness_mode_fails():
    m = _gate(resolved=_resolved(mutations_sent=1))
    assert m["verdict"] == LR.FAIL


def test_an_absent_resolved_field_is_a_failure_not_a_pass():
    r = _resolved()
    r.pop("cohort_prefix")
    m = _gate(resolved=r)
    assert m["verdict"] == LR.FAIL
    assert any(d["resolved"] == "<ABSENT>"
               for d in m["checks"]["fields"]["diffs"])


# ============================================ the call-graph audit itself

def test_the_call_graph_audit_passes_on_the_current_tree():
    got = LR.call_graph_audit()
    assert got["ok"] is True, got["unreached"]


def test_the_call_graph_audit_catches_an_unreachable_safety_function():
    """stale_orders is the archetype: it IS defined, it IS tested, and the
    live path does not call it."""
    got = LR.call_graph_audit(
        claims=(("src.gemini_execution.continuous_probe", "stale_orders"),))
    assert got["ok"] is False
    assert got["unreached"][0]["name"] == "stale_orders"


def test_the_mutation_boundary_refuses_and_records():
    import asyncio

    class _Inner:
        async def request(self, method, params, **kw):
            raise AssertionError("the inner session must never be reached")

    s = LR.ReadinessSession(_Inner())
    with pytest.raises(LR.MutationBoundaryReached):
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            s.request("order.place", {"quantity": "1", "price": "0.5"}))
    assert s.sent == 0
    assert s.attempted[0]["method"] == "order.place"
    assert s.attempted[0]["params"]["quantity"] == "1"


def test_the_sweep_does_not_flag_a_correct_branch_followed_by_a_separator():
    """MY OWN FALSE POSITIVE, pinned. `(\\S+)` captured the trailing ';' from
    `git push origin HEAD:branch; then` and reported the correct branch as
    stale. A gate that cries wolf gets ignored, which is its own failure."""
    got = LR.stale_reference_sweep(branch=BRANCH, cohort_prefix="GEMI-BTC15M",
                                   envelope_id="GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1")
    assert got["ok"] is True, got["hits"]
