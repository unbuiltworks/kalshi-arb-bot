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
        cohort_id="COHORT-2026-09-21-BTC15M-Q1LIVE-1",
        remaining_budget=12, max_working_orders=2,
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
          "action_size", "cohort_prefix", "cohort_id", "remaining_budget",
          "max_working_orders", "max_committed_usd", "max_residual_usd",
          "max_concurrent_markets", "minimum_rest_time_s")}
    r.update({"frame_quantity": "1", "policy_max_rungs": 2,
              "lifecycle_max_rungs": 2, "cohort_predecessor_intact": True,
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


# ============================ the gate as the WORKFLOW actually invokes it

def test_the_workflow_invokes_the_gate_module_not_just_the_readiness_run():
    """`run_continuous --readiness` proves the boundary held. It does NOT run
    the comparison, the call-graph audit or the stale sweep. Having
    live_readiness.py in the repo while the Actions path never calls it is
    the same believed-not-proven class the gate exists to close."""
    import pathlib
    import yaml
    doc = yaml.safe_load(pathlib.Path(
        ".github/workflows/gemini-settlement-gate.yml").read_text())
    step = [str(s.get("run") or "") for s in doc["jobs"]["gate"]["steps"]
            if "LIVE_READINESS_GATE" in str(s.get("name") or "")]
    assert step, "no LIVE_READINESS_GATE step"
    run = step[0]
    assert "src.gemini_execution.live_readiness" in run
    assert "--report" in run and "--branch" in run
    # and it must be able to fail the step
    assert run.index("set -o pipefail") < run.index("live_readiness")


def test_the_gate_cli_exits_nonzero_on_FAIL(tmp_path):
    """A gate that returns FAIL and exits 0 is a green step over a red gate."""
    import json
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"state": "DO_NOT_TRADE"}))
    rc = LR.main(["--spec", "btc15m_q1", "--report", str(rep),
                  "--branch", BRANCH])
    assert rc == 1


def test_the_four_terms_states_stay_four():
    """RETRACTION_001. A no-credential read is NOT `terms_accepted: False`,
    and rendering it as False cost the owner an account action they did not
    need to take."""
    assert LR.terms_provenance(
        {"readable": False, "verdict": "NO_CREDENTIALS"}
    )["state"] == LR.TERMS_NO_CREDENTIALS
    assert LR.terms_provenance({"readable": False})["state"] == \
        LR.TERMS_UNREADABLE
    assert LR.terms_provenance(None)["state"] == LR.TERMS_UNREADABLE
    assert LR.terms_provenance({"terms_accepted": True})["state"] == \
        LR.TERMS_TRUE
    assert LR.terms_provenance({"terms_accepted": False})["state"] == \
        LR.TERMS_FALSE
    # and only TRUE passes the gate -- the other three are FAIL, as themselves
    for ts in ({"readable": False, "verdict": "NO_CREDENTIALS"},
               {"readable": False}, None, {"terms_accepted": False}):
        assert LR.terms_provenance(ts)["state"] != LR.TERMS_TRUE


def test_the_widest_surface_is_DERIVED_from_the_frozen_policy():
    """Not a fixture -- and CAPACITY IS NOT AUTHORITY.

    My first version returned `outcomes x rung_count x leg_slots` from
    `policy_max_rungs` and compared the envelope against it. That conflated
    two different quantities, and comparing capital against the larger one
    would have made "the lifecycle can reach for a fourth order" into an
    argument for authorizing a fourth order. They are now separate:

      policy_max_rungs     (2) what the FROZEN surface authorizes
      lifecycle_max_rungs  (1) what reconcile_surface can represent

    The envelope must cover (2). (1) > (2) is an unfrozen execution rule.
    """
    from src.gemini_execution import live_probe_spec as SPEC
    assert LR.policy_max_rungs(SPEC.BTC5M_Q1) == 2      # 2 legs x 1 rung
    assert LR.policy_max_rungs(SPEC.BTC15M_Q1) == 2     # SAME: rung_count 1
    # (1) is now MEASURED by driving the production reconciler through the
    # worst case, not computed. It read 4 on BTC15m while a free leg slot
    # could become a second price level; the lifecycle now caps each leg at
    # what the desired surface asks for, so the two agree.
    assert LR.lifecycle_max_rungs(SPEC.BTC5M_Q1) == 2
    assert LR.lifecycle_max_rungs(SPEC.BTC15M_Q1) == 2


def test_a_btc15m_envelope_narrower_than_its_own_geometry_is_caught():
    """THE RULE THAT PRODUCED THE BLOCKER, KEPT AFTER THE BLOCKER IS FIXED.

    The lifecycle no longer rests more than the frozen policy authorises, so
    the live derivation now agrees. What must survive is the CHECK: a
    resolved set where it does not agree still has to FAIL, or re-widening
    the reconciler would pass silently.
    """
    from src.gemini_execution import live_envelope as LEX
    from src.gemini_execution import live_probe_spec as SPEC

    # today, on the real code
    assert LR.lifecycle_max_rungs(SPEC.BTC15M_Q1) == \
        LR.policy_max_rungs(SPEC.BTC15M_Q1)
    assert LR.policy_max_rungs(SPEC.BTC15M_Q1) <= \
        LEX.LIVE_Q1_BTC15M.max_working_orders

    # and the rule still bites on the state that produced run 35616887007
    m = _gate(resolved=_resolved(
        policy_max_rungs=2, lifecycle_max_rungs=4,
        max_working_orders=LEX.LIVE_Q1_BTC15M.max_working_orders))
    assert m["verdict"] == LR.FAIL
    v = m["checks"]["cross_field"]["violations"]
    assert any("UNFROZEN EXECUTION RULE" in x for x in v), v
    assert not any("envelope allows" in x for x in v), v


# ======================= THREE GATE READER DEFECTS, FROM RUN 35615018494
#
# The driver fix worked: family BTC15m, envelope ..._Q1_BTC15M_V1, band
# 840:880, symbol GEMI-BTC15M2609211515-UP, spec hash matching, zero
# mutations. The gate then reported three failures that were ITS OWN
# misreadings of that evidence. A gate that misreads is worse than no gate,
# because it is believed. Each is pinned on the REAL payload shape.

#: verbatim from run 35615018494's report. There is NO `terms_accepted` key.
REAL_TERMS = {"readable": True, "verdict": "ACCEPTED", "method": "GET",
              "status": 200, "hasAcceptedLatest": True, "acceptedVersion": 1,
              "latestVersion": 1, "blocks_orders": False, "why": None}

#: verbatim shape: the symbol lives under `chosen`, not at the top level
REAL_SELECTION = {"eligible": True,
                  "chosen": {"symbol": "GEMI-BTC15M2609211515-UP",
                             "tte_s": 877.81, "contract_status": "active",
                             "market_state": "open"},
                  "band": [840.0, 880.0], "cohort": "BTC15m"}


def test_the_venues_own_ACCEPTED_is_not_rendered_UNREADABLE():
    """RETRACTION_001 WITH THE SIGN FLIPPED. I read only `terms_accepted`,
    which metadata.terms_status does not emit, so an authenticated 200
    ACCEPTED came back UNREADABLE and failed the gate."""
    got = LR.terms_provenance(REAL_TERMS)
    assert got["state"] == LR.TERMS_TRUE, got


def test_an_explicit_refusal_still_beats_the_verdict_summary():
    """Fail closed: the specific field wins over the summary word."""
    ts = dict(REAL_TERMS, hasAcceptedLatest=False)
    assert LR.terms_provenance(ts)["state"] == LR.TERMS_FALSE


def test_a_readable_response_that_says_neither_is_still_UNREADABLE():
    ts = {"readable": True, "verdict": "SOMETHING_NEW"}
    assert LR.terms_provenance(ts)["state"] == LR.TERMS_UNREADABLE


def test_the_chosen_symbol_is_read_from_where_the_report_puts_it():
    rep = {"symbol_selection": REAL_SELECTION}
    assert LR._chosen_symbol(rep) == "GEMI-BTC15M2609211515-UP"
    assert LR._chosen_symbol({}) == ""
    # the flat spelling still works, so an older report is not misread
    assert LR._chosen_symbol({"symbol_selection": {"symbol": "X"}}) == "X"


def test_the_mutation_boundary_is_declared_before_any_early_return():
    """It was assigned only where the session is wrapped, so every run that
    returned earlier -- the authorization gate, a family mismatch -- left it
    None and the gate could not tell BLOCKED from never-reached."""
    import inspect

    from src.gemini_execution import run_continuous as RCX
    src = inspect.getsource(RCX._build)
    first_set = src.index('rep["mutation_boundary"]')
    first_return = src.index("return rep")
    assert first_set < first_return, (
        "the boundary must be declared before the first early return")
    assert 'BLOCKED (readiness)' in src[first_set:first_set + 400]


# ============== THE COHORT FIELD, FROM RUN 35623377568 ("expected
# ============== NO_COHORT_DECLARED, resolved None" -- and neither was true)

def test_the_granted_cohort_is_expected_and_resolved_as_the_SAME_thing():
    """BOTH SIDES WERE WRONG AT ONCE, which is why they disagreed loudly
    enough to notice. The expectation read `spec.cohort`, which stays None by
    design because the granted ledger lives in `live_cohort`; the resolved
    side read `cohort["id"]` where the key is `cohort_id`. The truth was
    COHORT-2026-09-21-BTC15M-Q1LIVE-1 at 12/12 available, and neither side
    said so."""
    from src.gemini_execution import cohort as CO
    from src.gemini_execution import live_probe_spec as SPEC

    s = SPEC.BTC15M_Q1
    e = LR.expectation_from_spec(s, branch=BRANCH, auth_id="A")
    r = LR.resolve_from_report({"cohort": CO.state(s.budget_cohort)}, s)

    assert e.cohort_id == s.budget_cohort
    assert r["cohort_id"] == s.budget_cohort
    assert e.remaining_budget == 12 and r["remaining_budget"] == 12

    # and the two agree THROUGH `compare`, which is where the disagreement
    # actually surfaced. Only the cohort fields are under test here, so the
    # rest of the resolved set is the passing fixture -- mixing a real
    # expectation's git sha and hashes with it would fail on those instead
    # and prove nothing about the cohort.
    diffs = LR.compare(e, {**_resolved(),
                           "cohort_id": r["cohort_id"],
                           "remaining_budget": r["remaining_budget"]})["diffs"]
    assert [d["field"] for d in diffs if d["field"] in
            ("cohort_id", "remaining_budget")] == [], diffs


def test_a_partly_spent_cohort_fails_the_ONE_window_grant():
    """"No second window" as a CHECK rather than as prose: the expectation is
    the envelope's full max_actions, so a ledger with any slot already spent
    can no longer satisfy a one-window grant."""
    m = _gate(resolved=_resolved(remaining_budget=11))
    assert m["verdict"] == LR.FAIL
    assert any(d["field"] == "remaining_budget"
               for d in m["checks"]["fields"]["diffs"])


def test_binding_a_different_cohort_than_the_grant_named_fails():
    m = _gate(resolved=_resolved(cohort_id="COHORT-2026-09-21-Q1LIVE-1"))
    assert m["verdict"] == LR.FAIL
    assert any(d["field"] == "cohort_id"
               for d in m["checks"]["fields"]["diffs"])


def test_a_broken_predecessor_chain_fails():
    """A rewritten predecessor ledger is how a spent budget becomes unspent.
    UNREADABLE is not intact: only an explicit True passes."""
    for bad in (False, None, "yes"):
        got = LR.cross_field_invariants(_resolved(
            cohort_predecessor_intact=bad))
        assert got["ok"] is False, bad
        assert any("predecessor" in v for v in got["violations"]), bad
    assert LR.cross_field_invariants(_resolved())["ok"] is True
