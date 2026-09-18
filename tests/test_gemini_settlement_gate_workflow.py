"""The pieces the settlement-gate workflow runs, and the order it runs them in."""
import json
import pathlib

import pytest

from scripts import gemini_recent_expiries as EX
from scripts import gemini_settlement_gate_report as REP

WF = pathlib.Path(".github/workflows/gemini-settlement-gate.yml")


# ------------------------------------------------------- expiry selection

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

def test_the_canary_step_is_off_unless_explicitly_armed():
    wf = WF.read_text()
    i = wf.index("ONE authorized Gemini canary")
    step = wf[i:i + 1200]
    assert "github.event.inputs.armed_canary == 'true'" in step
    assert 'default: "false"' in wf


def test_the_canary_step_names_the_exact_authorization():
    wf = WF.read_text()
    assert 'GEMINI_CANARY_AUTH: "OWNER-2026-09-17-GEMINI-CANARY-1"' in wf
    assert "GEMINI_CANARY_AUTH_EXPIRES" in wf


def test_no_other_step_arms_execution():
    """Every read-only step must run with the arm absent."""
    wf = WF.read_text()
    assert wf.count('GEMINI_CANARY_LIVE: "YES"') == 1


def test_the_latch_path_may_be_committed():
    wf = WF.read_text()
    assert "analysis/gemini/canary_latch/" in wf
    assert "canary_latch/latch_" in wf
