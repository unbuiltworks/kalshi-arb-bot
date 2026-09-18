"""The pieces the settlement-gate workflow runs, and the order it runs them in."""
import json
import pathlib
import re

import pytest

from scripts import gemini_recent_expiries as EX
from scripts import gemini_settlement_gate_report as REP

WF = pathlib.Path(".github/workflows/gemini-settlement-gate.yml")


# ------------------------------------------------------- expiry selection


def _step(name):
    """Exactly ONE step's text: from its name to the next step's name.

    A fixed character window was wrong -- it ran past the end of the step and
    into the ARMED canary that follows, so "this step does not set
    GEMINI_CANARY_LIVE" was reading the step that does.
    """
    wf = WF.read_text()
    i = wf.index(name)
    rest = wf[i:]
    m = re.search(r"\n      - name: ", rest[1:])
    return rest[:m.start() + 1] if m else rest



def _sets_the_arm(step):
    """Does this step ASSIGN the arm, as opposed to naming it in a comment?

    The disarmed steps mention GEMINI_CANARY_LIVE precisely in order to say
    they do not set it, so a substring check flags the documentation instead
    of the behaviour. This is the second time that trap was walked into in
    this file; asserting on the assignment closes it for every caller.
    """
    import re as _re
    return bool(_re.search(r"^\s*GEMINI_CANARY_LIVE:\s*\S+", step, _re.M))


def test_every_symbol_it_returns_has_already_expired():
    """Settlement is only a question once a contract has expired; asking about
    a live window reads the venue's silence as a finding."""
    now = 1789683000.0                       # 2026-09-17T22:10:00Z
    for s in EX.recent_expiries(6, now=now):
        stamp = s.split("-")[1][-10:]
        import datetime
        dt = datetime.datetime.strptime(stamp, "%y%m%d%H%M").replace(
            tzinfo=datetime.timezone.utc)
        assert dt.timestamp() < now, s


def test_exactly_on_a_boundary_does_not_return_the_boundary_itself():
    """At 22:10:00 the 22:10 window has not settled; it has only just ended."""
    got = EX.recent_expiries(1, now=1789683000.0)
    assert got == ["GEMI-BTC05M2609172205-UP"]


def test_a_second_after_the_boundary_does_return_it():
    got = EX.recent_expiries(1, now=1789683001.0)
    assert got == ["GEMI-BTC05M2609172210-UP"]


def test_symbols_are_newest_first_and_distinct():
    got = EX.recent_expiries(6, now=1789683001.0)
    assert len(set(got)) == 6
    assert got[0] > got[-1]


def test_it_reaches_the_network_and_the_environment_not_at_all():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(EX))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    assert not (mods & {"os", "httpx", "urllib", "requests", "socket"})


# ------------------------------------------------------------- the report

def test_the_report_states_that_it_authorizes_nothing():
    out = REP.render({"gate": {"gate": "OPEN", "missing_union": []},
                      "settlement_endpoint_exists": True,
                      "settlement_endpoint_found": ["/v1/settlements"],
                      "per_symbol": [], "account_reads": []})
    assert "authorizes nothing" in out
    assert "not that a canary is approved" in out


def test_the_report_names_each_symbols_first_break():
    out = REP.render({"gate": {}, "per_symbol": [
        {"symbol": "GEMI-X", "proof_chain": {"first_break": "position"}}]})
    assert "GEMI-X" in out and "first_break=position" in out


def test_the_report_survives_a_minimal_payload():
    assert REP.render({}) and "authorizes nothing" in REP.render({})


# ------------------------------------------------- the workflow's own shape

def test_the_guard_runs_before_any_secret_enters_scope():
    """If the read-only contract is broken, the job must die without ever
    holding a credential."""
    wf = WF.read_text()
    guard = wf.index("tests/test_gemini_settlement_probe.py")
    secret = wf.index("GEMINI_API_KEY:")
    assert guard < secret


def test_it_fails_closed_on_missing_credentials():
    wf = WF.read_text()
    assert '"verdict": *"NO_CREDENTIALS"' in wf
    assert "failing closed" in wf
    assert "exit 1" in wf


def test_it_commits_evidence_only():
    wf = WF.read_text()
    assert "refusing to commit files outside analysis/gemini/" in wf
    assert "analysis/gemini/settlement_gate_" in wf
    assert "[skip ci]" in wf


def test_it_checks_out_the_designated_branch():
    wf = WF.read_text()
    assert "ref: claude/l5-execution-instrumentation-io6nzi" in wf
    assert "HEAD:claude/l5-execution-instrumentation-io6nzi" in wf


def test_it_is_manual_only():
    """A settlement probe that fires on a push would run unreviewed."""
    wf = WF.read_text()
    assert "workflow_dispatch:" in wf
    for trigger in ("  push:", "  schedule:", "  pull_request:"):
        assert trigger not in wf


def test_it_submits_no_order_and_says_so():
    wf = WF.read_text()
    assert "NO ORDER IS SUBMITTED" in wf
    assert "no order transport" in wf


def test_the_evidence_push_survives_a_concurrent_push():
    """Run 3 built both artifacts and lost them: another push landed while it
    ran and the non-fast-forward killed the job. A reading cannot be
    reproduced -- the venue has moved on by the next run -- so the push
    rebases and retries rather than discarding it."""
    wf = WF.read_text()
    assert "rebasing onto remote" in wf
    assert "git rebase FETCH_HEAD" in wf
    assert "git rebase --abort" in wf


def test_the_preflight_step_cannot_fail_the_job_for_having_no_window():
    """A BTC5m window is only eligible for part of each cycle."""
    wf = WF.read_text()
    i = wf.index("Canary preflight")
    assert "|| true" in wf[i:i + 900]


def _commit_filter():
    """The allowlist regex the job applies to its own staged files."""
    import re
    wf = WF.read_text()
    m = re.search(r"grep -qvE '(\^analysis/gemini/[^']+)'", wf)
    assert m, "the commit step no longer applies a path allowlist"
    return re.compile(m.group(1))


@pytest.mark.parametrize("name,allowed", [
    ("analysis/gemini/settlement_gate_20260918T004500Z.json", True),
    ("analysis/gemini/canary_preflight_20260918T004500Z.json", True),
    ("analysis/gemini/execution_readiness_20260918T004500Z.json", True),
    ("analysis/gemini/ws_dry_run_20260918T004500Z.json", True),
    ("analysis/gemini/canary_latch/latch_OWNER-2026-09-17-X.json", True),
    # everything else stays out, whatever the job happens to leave behind
    ("src/gemini_execution/transport.py", False),
    ("analysis/gemini/notes.json", False),
    ("analysis/gemini/execution_readiness_20260918T004500Z.json.bak", False),
    (".github/workflows/gemini-settlement-gate.yml", False),
])
def test_only_evidence_artifacts_are_committable(name, allowed):
    """Asserted by MATCHING the filter, not by quoting it: a test that pins
    the regex's text has to be edited every time a legitimate artifact is
    added, which trains you to edit it without checking what it now admits."""
    assert bool(_commit_filter().match(name)) is allowed, name


# ------------------------------------------- the one authorized canary step

def test_the_armed_step_is_off_unless_explicitly_armed():
    wf = WF.read_text()
    i = wf.index("ONE authorized Gemini continuous window")
    step = wf[i:i + 2000]
    assert "github.event.inputs.armed_continuous == 'true'" in step
    assert 'default: "false"' in wf


def test_the_armed_step_names_the_exact_authorization():
    """Derived from arming.EXPECTED_AUTH_ID rather than restated, so the
    workflow and the code cannot name different authorizations -- which is a
    way to spend one authorization under the label of another."""
    from src.gemini_execution import arming
    wf = WF.read_text()
    assert 'GEMINI_CANARY_AUTH: "%s"' % arming.EXPECTED_AUTH_ID in wf
    assert '--auth-id "%s"' % arming.EXPECTED_AUTH_ID in wf
    assert "GEMINI_CANARY_AUTH_EXPIRES" in wf


def test_no_spent_authorization_can_still_be_dispatched():
    """Every identifier that has been spent stays out of the workflow. A
    dispatchable spent identifier is live-path surface that reads as usable."""
    wf = WF.read_text()
    for spent in ("OWNER-2026-09-17-GEMINI-CANARY-1",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-1",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-2"):
        assert 'GEMINI_CANARY_AUTH: "%s"' % spent not in wf, spent
        assert '--auth-id "%s"' % spent not in wf, spent


def test_the_wire_artifact_is_collected_with_the_run():
    """The place-ack capture is worthless if the artifact never leaves the
    runner. The add-list and the allow-list must BOTH know it -- they drifted
    once before and a job threw away evidence it had correctly gathered."""
    wf = WF.read_text()
    assert "--wire    analysis/gemini/continuous_wire_" in wf
    assert "analysis/gemini/continuous_wire_*.json" in wf
    assert "continuous_wire" in wf.split("grep -qvE")[1][:400]


def test_the_spent_canary_step_is_gone_and_stays_gone():
    """OWNER-2026-09-17-GEMINI-CANARY-1 is spent: its latch is on disk in
    state REJECTED and arming.EXPECTED_AUTH_ID has moved on, so the step could
    no longer arm anything. A dead armed step is worse than no step -- it is
    live-path surface that reads as usable -- so it was removed with its
    dispatch input rather than left switched off."""
    wf = WF.read_text()
    assert "ONE authorized Gemini canary" not in wf
    assert "run_canary" not in wf
    assert "armed_canary" not in wf
    assert "OWNER-2026-09-17-GEMINI-CANARY-1" not in wf


def test_no_other_step_arms_execution():
    """Every read-only step must run with the arm absent."""
    wf = WF.read_text()
    assert wf.count('GEMINI_CANARY_LIVE: "YES"') == 1


def test_the_latch_path_may_be_committed():
    wf = WF.read_text()
    assert "analysis/gemini/canary_latch/" in wf
    assert "canary_latch/latch_" in wf


def test_the_ws_dry_run_step_is_disarmed_and_cannot_submit():
    """It runs in the credentialed job, so its inability to send has to be
    structural rather than a matter of which flags the step happens to pass."""
    step = _step("L5 WS canary DRY RUN")
    assert not _sets_the_arm(step)
    assert "GEMINI_CANARY_AUTH:" not in step
    assert "|| true" in step                 # a missing window is a result
    assert "ws_dry_run_" in step
    assert not _sets_the_arm(step)


def test_the_arm_is_SET_exactly_once_in_the_whole_workflow():
    """Counting mentions was wrong: the disarmed steps name the variable in a
    comment in order to say they do not set it, and that comment is the
    documentation, not a second arm. What must be unique is the ASSIGNMENT."""
    import re
    wf = WF.read_text()
    assigns = re.findall(r'^\s*GEMINI_CANARY_LIVE:\s*\S+', wf, re.M)
    assert len(assigns) == 1, assigns
    i = wf.index(assigns[0])
    assert "ONE authorized Gemini continuous window" in wf[:i]
    # and that one assignment sits under the armed-only conditional
    assert "github.event.inputs.armed_continuous == 'true'" in wf[:i]


def test_the_dry_run_retries_into_the_next_btc5m_window():
    """The eligible band is TTE 60..300s, so the last 60s of every 5-minute
    cycle has no eligible window. Run 35335282701 hit exactly that and
    reported NO_ELIGIBLE_WINDOW; roughly one dispatch in five would."""
    step = _step("L5 WS canary DRY RUN")
    assert "for attempt in 1 2 3 4 5" in step
    assert "sleep 45" in step
    assert not _sets_the_arm(step)   # still disarmed, still cannot submit


def test_the_retry_is_bounded_and_still_writes_a_negative_result():
    """"No window" is the honest answer when it is really true, so a run that
    never finds one must still leave an artifact rather than nothing."""
    step = _step("L5 WS canary DRY RUN")
    assert 'attempt" = "5"' in step          # bounded
    assert 'if [ ! -s "$OUT" ]; then rm -f "$OUT"; fi' in step


def test_the_readiness_step_also_retries_into_a_live_window():
    """Gates that read false because no window was open are worse than no
    gates: run 35336015691 reported four of them false at 10:43:25Z and the
    dry run succeeded 21 seconds later."""
    step = _step("Execution readiness")
    assert "for attempt in 1 2 3 4 5" in step
    assert "sleep 45" in step
    assert "preflight_ok" in step          # it retries on the real condition
    assert not _sets_the_arm(step)


def test_both_window_dependent_steps_use_the_same_bound():
    """One retry budget, so a dispatch cannot take arbitrarily long."""
    for name in ("Execution readiness", "L5 WS canary DRY RUN"):
        step = _step(name)
        assert 'attempt" = "5"' in step, name
        assert "sleep 45" in step, name


def test_REGRESSION_the_add_list_and_the_allow_list_name_the_same_files():
    """They drifted, and a job that gathered its evidence correctly then
    refused to commit it as "outside analysis/gemini/" -- which it was not.
    Every path the step stages must be accepted by the guard that follows."""
    import re
    wf = WF.read_text()
    i = wf.index("Commit ONLY the evidence artifact")
    step = wf[i:i + 3200]
    pattern = re.search(r"grep -qvE '([^']+)'", step).group(1)

    # every pathspec the `for` loop stages, as a concrete filename
    samples = {
        "analysis/gemini/settlement_gate_*.json":
            "analysis/gemini/settlement_gate_20260918T140310Z.json",
        "analysis/gemini/canary_preflight_*.json":
            "analysis/gemini/canary_preflight_20260918T140314Z.json",
        "analysis/gemini/execution_readiness_*.json":
            "analysis/gemini/execution_readiness_20260918T140314Z.json",
        "analysis/gemini/ws_dry_run_*.json":
            "analysis/gemini/ws_dry_run_20260918T140331Z.json",
        "analysis/gemini/continuous_run_*.json":
            "analysis/gemini/continuous_run_20260918T133310Z.json",
        "analysis/gemini/premutation_proof_*.json":
            "analysis/gemini/premutation_proof_20260918T140514Z.json",
        "analysis/gemini/continuous_ledger.jsonl":
            "analysis/gemini/continuous_ledger.jsonl",
        "analysis/gemini/canary_latch/*.json":
            "analysis/gemini/canary_latch/latch_OWNER-2026-09-18-X.json",
    }
    for spec, example in samples.items():
        assert spec in step, "the add-list no longer stages %s" % spec
        assert re.match(pattern, example), (
            "%s is staged but the allow-list rejects it" % example)


def test_the_allow_list_still_rejects_anything_outside_the_evidence_dir():
    import re
    wf = WF.read_text()
    i = wf.index("Commit ONLY the evidence artifact")
    pattern = re.search(r"grep -qvE '([^']+)'",
                        wf[i:i + 3200]).group(1)
    for bad in ("src/gemini_execution/ws_continuous.py",
                ".github/workflows/gemini-settlement-gate.yml",
                "analysis/gemini/../../src/x.py",
                "analysis/l5/authorization/HALT",
                "analysis/gemini/something_else.json"):
        assert not re.match(pattern, bad), bad
