"""The pieces the settlement-gate workflow runs, and the order it runs them in."""
import json
import pathlib
import re

import pytest

from scripts import gemini_recent_expiries as EX
from scripts import gemini_settlement_gate_report as REP

WF = pathlib.Path(".github/workflows/gemini-settlement-gate.yml")

def _code(text):
    """`text` with its `#` comment lines removed.

    THE THIRD TIME THIS TRAP HAS BITTEN -- see `_sets_the_arm` below. The
    armed step's post-mortem comment explains the drift by NAMING the flags
    it forbids ("Hand-built --family/--envelope/--entry-tte-band literals are
    what let run 35601903823's two steps resolve different experiments"), so
    a substring assertion flags the explanation instead of the command. These
    checks are about what the step RUNS; comments are not what it runs.
    """
    return "\n".join(ln for ln in text.splitlines()
                      if not ln.lstrip().startswith("#"))




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
    """MOVED 2026-09-21 to the q=1 branch, after run 35553352750 proved the
    old pin was the defect: the workflow came from the dispatched ref and the
    SOURCE came from the instrumentation branch, so the armed step ran a
    driver that had never heard of --envelope live_q1 and placed ZERO ORDERS.
    The checkout and the push target must name the SAME branch, or an
    evidence commit merges one tree into the other as a side effect."""
    wf = WF.read_text()
    assert "ref: claude/gemini-btc-economic-viability-h3u7zl" in wf
    assert "HEAD:claude/gemini-btc-economic-viability-h3u7zl" in wf


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
    step = _step("ONE authorized Gemini continuous window")
    assert "github.event.inputs.armed_continuous == 'true'" in step
    assert 'default: "false"' in wf


def test_the_armed_step_names_the_exact_authorization():
    """Derived from arming.EXPECTED_AUTH_ID rather than restated, so the
    workflow and the code cannot name different authorizations -- which is a
    way to spend one authorization under the label of another."""
    from src.gemini_execution import arming
    wf = WF.read_text()
    assert 'GEMINI_CANARY_AUTH: "%s"' % arming.EXPECTED_AUTH_ID in wf
    # THE ARMED STEP DEREFERENCES THAT ONE BINDING rather than repeating the
    # id. Same reason as `test_the_tree_is_armed_for_exactly_one_named_
    # authorization`: arming is one line, and a `sed` can no longer arm the
    # unarmed proof step by accident -- which it once did.
    assert '--auth-id "${GEMINI_CANARY_AUTH}"' in _code(
        _step("ONE authorized Gemini continuous window"))
    assert "GEMINI_CANARY_AUTH_EXPIRES" in wf
    # AND IT MUST NOT BE IN THE PAST while the tree is armed. It was stale
    # from the 09-18 windows when CONTINUOUS-8 was armed and refused twice on
    # it -- correctly, before the latch, but the refusal cost two dispatches
    # to diagnose and a test would have cost none.
    if LIVE_AUTHORIZATION is not None:
        import datetime as _dt
        raw = re.search(r'GEMINI_CANARY_AUTH_EXPIRES: "([^"]+)"', wf).group(1)
        exp = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        assert exp > _dt.datetime.now(_dt.timezone.utc), (
            "the arm expires at %s, which is already past: an armed tree "
            "whose expiry has lapsed cannot run the window it was armed for"
            % raw)


def test_no_spent_authorization_can_still_be_dispatched():
    """Every identifier that has been spent stays out of the workflow. A
    dispatchable spent identifier is live-path surface that reads as usable."""
    wf = WF.read_text()
    for spent in ("OWNER-2026-09-17-GEMINI-CANARY-1",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-1",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-2",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-3",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-4",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-5",
                  "OWNER-2026-09-18-GEMINI-CONTINUOUS-6"):
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
    step = _step("Commit ONLY the evidence artifact")
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
        # THE COHORT LEDGER. It records the slots an armed window spends; a
        # run that does not commit it discards its own budget accounting and
        # leaves the next window reading 0 consumed.
        "analysis/gemini/cohorts/*/ledger.jsonl":
            "analysis/gemini/cohorts/COHORT-2026-09-18-C5/ledger.jsonl",
        "analysis/gemini/cohorts/*/cohort.json":
            "analysis/gemini/cohorts/COHORT-2026-09-18-C5/cohort.json",
    }
    for spec, example in samples.items():
        assert spec in step, "the add-list no longer stages %s" % spec
        assert re.match(pattern, example), (
            "%s is staged but the allow-list rejects it" % example)


def test_the_allow_list_still_rejects_anything_outside_the_evidence_dir():
    import re
    wf = WF.read_text()
    pattern = re.search(r"grep -qvE '([^']+)'",
                        _step("Commit ONLY the evidence artifact")
                        ).group(1)
    for bad in ("src/gemini_execution/ws_continuous.py",
                ".github/workflows/gemini-settlement-gate.yml",
                "analysis/gemini/../../src/x.py",
                "analysis/l5/authorization/HALT",
                "analysis/gemini/something_else.json"):
        assert not re.match(pattern, bad), bad


def test_the_armed_step_binds_the_cohort_not_the_spent_historical_ledger():
    """The armed step was invoking run_continuous WITHOUT --cohort.

    The historical ledger is 12/12 permanently spent by CONTINUOUS-2, -3 and
    -4. An authorized window that binds it would refuse on budget at the
    moment it matters, and the repair would be improvised under arm-time
    pressure. CONTINUOUS-5's budget lives in COHORT-2026-09-18-C5, so the
    armed step must name it.

    THE BINDING MOVED, THE INVARIANT DID NOT. Hand-written `--cohort` in the
    armed step is exactly the drift class run 35601903823 exposed, so the
    cohort is now a LiveProbeSpec field and both steps consume the spec. What
    must still hold is that a spent ledger can never be the one bound.
    """
    from src.gemini_execution import live_probe_spec as SPECX

    SPENT = ("COHORT-2026-09-18-C5", "COHORT-2026-09-18-C6",
             "COHORT-2026-09-18-C7", "COHORT-2026-09-18-C8B",
             "COHORT-2026-09-21-Q1LIVE-1")

    wf = WF.read_text()
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    run = armed.split("run:", 1)[1]
    run = _code(run)
    assert "--spec" in run, "the armed step must consume the declared spec"
    assert "--cohort" not in run, (
        "a hand-built --cohort is the drift class; it belongs to the spec")
    for spent in SPENT:
        assert spent not in run, spent

    # and no DECLARED spec may name a spent ledger either
    for sid, s in sorted(SPECX.SPECS.items()):
        assert s.cohort not in SPENT, (sid, s.cohort)


def test_the_armed_step_names_the_reviewed_q1_envelope_explicitly():
    """The q=1 bounds reach the driver ONLY through this flag.

    Without it `run_continuous` builds the Driver on LE.DEFAULT, which is the
    0.01 SHADOW envelope -- so an armed step that omitted it would run bounds
    nobody authorized while every document said otherwise.

    THE FLAG MOVED INTO THE SPEC. What must not change is that the driver
    cannot end up on LE.DEFAULT: the spec names a LIVE envelope explicitly,
    and no step may name one by hand where the two could disagree.
    """
    from src.gemini_execution import live_envelope as LEQ
    from src.gemini_execution import live_probe_spec as SPECX

    wf = WF.read_text()
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    run = _code(armed.split("run:", 1)[1])
    assert "--spec" in run
    assert "--envelope" not in run
    assert "shadow_q001" not in run

    for sid, s in sorted(SPECX.SPECS.items()):
        assert s.envelope_obj is not LEQ.DEFAULT, sid
        assert s.envelope_obj is not LEQ.SHADOW_Q001, sid
        assert str(s.envelope_obj.action_size) == "1", sid


def test_the_q1_cohort_is_exactly_twelve_and_chained_to_C8B():
    """Budget 12 = CP.MAX_ACTIONS = LIVE_Q1.max_actions.

    It is a FRESH DURABLE LEDGER, not a bigger budget: the authorized limit
    is twelve action attempts and this is exactly twelve. Consumption must be
    monotonic and must never exceed it -- a count that FELL would be a reset
    wearing the cohort's name.
    """
    import json
    import pathlib
    from src.gemini_execution import cohort as CO
    from src.gemini_execution import continuous_probe as CPX
    from src.gemini_execution import live_envelope as LEX

    c = CO.state("COHORT-2026-09-21-Q1LIVE-1")
    assert c["budget"] == 12 == CPX.MAX_ACTIONS == LEX.LIVE_Q1.max_actions
    assert 0 <= c["consumed"] <= c["budget"]
    assert c["remaining"] == c["budget"] - c["consumed"]
    pre = c["predecessor"]
    assert pre["intact"] is True
    assert pre["predecessor_ledger"].endswith(
        "COHORT-2026-09-18-C8B/ledger.jsonl")
    assert pre["recorded_consumed"] == 11
    manifest = json.loads(pathlib.Path(
        "analysis/gemini/cohorts/COHORT-2026-09-21-Q1LIVE-1/cohort.json"
    ).read_text())
    assert manifest["envelope_max_actions"] == CPX.MAX_ACTIONS
    assert manifest["budget"] <= CPX.MAX_ACTIONS


def test_the_abandoned_C8_cohort_is_never_bound_and_stays_at_zero():
    """C8 was created at 6 slots, then superseded by C8B at 12 before it ever
    ran. It is kept as evidence and must never be used.

    It is pinned HERE rather than by the cohort chain on purpose:
    `verify_predecessor` is SINGLE-HOP, so whichever ledger C8B names is the
    only one the live path enforces before the latch -- and C8's ledger is
    EMPTY, carrying no mutation history to protect. Chaining C8B to it would
    have traded C7's six spent slots for an empty file.
    """
    import json
    import pathlib
    from src.gemini_execution import cohort as CO

    wf = WF.read_text()
    # the exact id, not the C8B prefix match
    assert not re.search(r'--cohort\s+"COHORT-2026-09-18-C8"', wf)

    c8 = CO.state("COHORT-2026-09-18-C8")
    assert c8["budget"] == 6 and c8["consumed"] == 0
    assert pathlib.Path(c8["ledger_path"]).read_bytes() == b""

    c8b = CO.state("COHORT-2026-09-18-C8B")
    # THE BUDGET IS THE INVARIANT, NOT THE COUNT. `consumed == 0` was a
    # moment, and it broke the instant CONTINUOUS-8 ran -- the same defect as
    # pinning a latch count or an executable hash. What must hold forever is
    # that the budget is 12, that consumption never exceeds it, and that
    # consumption is MONOTONIC: a count that FELL would be a reset wearing
    # the cohort's name.
    assert c8b["budget"] == 12
    assert 0 <= c8b["consumed"] <= c8b["budget"]
    assert c8b["remaining"] == c8b["budget"] - c8b["consumed"]
    # ... and C8B guards the ledger that actually holds spent slots
    pre = c8b["predecessor"]
    assert pre["intact"] is True
    assert pre["predecessor_ledger"].endswith("COHORT-2026-09-18-C7/ledger.jsonl")
    assert pre["recorded_consumed"] == 6

    # the budget is a TIGHTENING of the envelope, never an extension
    from src.gemini_execution import continuous_probe as CPX
    assert c8b["budget"] <= CPX.MAX_ACTIONS
    manifest = json.loads(pathlib.Path(
        "analysis/gemini/cohorts/COHORT-2026-09-18-C8B/cohort.json"
    ).read_text())
    assert manifest["envelope_max_actions"] == CPX.MAX_ACTIONS


def test_the_unarmed_band_defaults_to_the_standing_rule():
    """The knob exists to prove the ARMED band read-only. Its default must
    not make every routine gate run sit through a 420s wait."""
    wf = WF.read_text()
    i = wf.index("unarmed_entry_band:")
    assert 'default: "90:300"' in wf[i:i + 800]
    # the STEP, not a fixed slice: the first cut was 900 chars and stopped
    # short of the very line it was checking.
    unarmed = wf.split("Unarmed production path", 1)[1].split(
        "ONE authorized Gemini continuous window", 1)[0]
    assert "unarmed_entry_band" in unarmed and "'90:300'" in unarmed
    # and the ARMED step's band is FIXED IN THE SPEC, never dispatch-supplied
    # (it used to be a literal in the step; that is the drift class).
    from src.gemini_execution import live_probe_spec as SPECX
    armed = _code(wf.split("ONE authorized Gemini continuous window", 1)[1])
    assert "--entry-tte-band" not in armed
    assert "github.event.inputs.unarmed_entry_band" not in armed
    assert SPECX.BTC15M_Q1.entry_tte_band == "840:880"


def test_the_job_outlives_the_armed_step_which_outlives_the_bounded_wait():
    """A HARD KILL IS WORSE THAN A STOP.

    The armed step now WAITS for its entry band before the window starts. The
    job timeout was 15 minutes against a 12-minute step, sized for a window
    that began immediately -- with the wait added, the runner would have been
    killed mid-window, and a killed runner never reaches the terminal
    withdrawal, so the orders stay live on the venue with nothing left to
    cancel them. That is CONTINUOUS-5's post-stop fill with no recovery path.
    """
    import re as _re
    from src.gemini_execution import run_continuous as RCX
    wf = WF.read_text()
    job = int(_re.search(r"^  gate:.*?^    timeout-minutes: (\d+)", wf,
                         _re.M | _re.S).group(1))
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    step = int(_re.search(r"timeout-minutes: (\d+)", armed).group(1))
    # the window itself: entry band top, less the driver's withdraw floor.
    # The band now comes from the SPEC the armed step consumes, not from a
    # literal in the step -- and BTC15m's 880s ceiling is nearly three times
    # BTC5m's 280, so this bound is the one that actually had to be rechecked.
    from src.gemini_execution import live_probe_spec as SPECX
    band_hi = float(SPECX.BTC15M_Q1.entry_tte_band.split(":")[1])
    need_s = RCX.MAX_ENTRY_WAIT_S + band_hi
    assert step * 60 > need_s, (step, need_s)
    assert job > step, (job, step)


def test_the_job_outlives_EVERY_step_not_just_the_armed_one():
    """The armed step's 12 vs the job's 15 was caught; the SOAK's 45 against
    the job's 30 was the same defect in reverse. Pin the general rule."""
    import re as _re
    wf = WF.read_text()
    job = int(_re.search(r"^  gate:.*?^    timeout-minutes: (\d+)", wf,
                         _re.M | _re.S).group(1))
    steps = [int(m) for m in _re.findall(r"^        timeout-minutes: (\d+)",
                                         wf, _re.M)]
    assert steps, "no step declares a timeout; this guard would pass vacuously"
    assert job > max(steps), (job, steps)


#: the ONE authorization the tree may be armed for right now. When C7 is
#: spent this becomes None again and the assertions below flip back to the
#: disarmed shape without any other edit.
LIVE_AUTHORIZATION = None


def test_the_tree_is_armed_for_exactly_one_named_authorization():
    """What must never hold is an armed tree with no live authorization
    behind it, or an armed tree naming a DIFFERENT authorization than the
    code does. Set LIVE_AUTHORIZATION to None to assert the disarmed shape.
    """
    from src.gemini_execution import arming as ARM
    from src.gemini_execution import bundle as BUN
    from src.gemini_execution import ws_continuous as WCX

    wf = WF.read_text()
    assert len(re.findall(r"^\s*GEMINI_CANARY_LIVE:", wf, re.M)) == 1

    sentinel = "NO-AUTHORIZATION-IN-FORCE"
    want = LIVE_AUTHORIZATION or sentinel
    assert 'GEMINI_CANARY_AUTH: "%s"' % want in wf
    assert ARM.EXPECTED_AUTH_ID == want
    ids = set(re.findall(r'--auth-id "([^"]+)"', wf))
    # THE ARMED STEP NOW DEREFERENCES THE ENV BLOCK rather than repeating the
    # id. That is strictly better -- arming is one line, and a `sed` can no
    # longer arm the unarmed proof step by accident, which it once did -- but
    # it means the literal set must admit the reference.
    REF = "${GEMINI_CANARY_AUTH}"
    assert REF in ids, ("the armed step must take its id from the single "
                        "GEMINI_CANARY_AUTH binding: %s" % ids)
    # AND THE STATIC READINESS STEP TAKES A READ-ONLY REFERENCE. It measures
    # the tree against a NAMED authorization without ever arming one, so it
    # dereferences a dispatch input rather than the arm. Admitted only on
    # proof that the input defaults to the sentinel and that the step
    # carrying it sets no GEMINI_CANARY_LIVE -- otherwise this would be a
    # second, quieter way to arm.
    STATIC_REF = "${AUTH_ID}"
    if STATIC_REF in ids:
        assert re.search(r"cohort_auth_id:.*?default: \"\"",
                         wf, re.S), "cohort_auth_id must default to blank"
        step = wf[wf.index("Static readiness ("):]
        step = step[:step.index("      - name:", 10)]
        assert "GEMINI_CANARY_LIVE" not in step, (
            "the static readiness step must never set the transport arm")
        assert "--static-readiness" in step
        assert "NO-AUTHORIZATION-IN-FORCE" in step, (
            "a blank input must fall back to the sentinel, not to an arm")
    literals = ids - {REF, STATIC_REF}
    # the UNARMED production-path step keeps the sentinel: it exists to be
    # refused, and an armed one would prove nothing.
    assert literals <= {want, sentinel} and sentinel in literals, ids
    if LIVE_AUTHORIZATION is None:
        assert literals == {sentinel}, ids
        assert WCX.REQUIRED_AUTH_ID is None and WCX.AUTHORIZED_SHA is None
        assert BUN.AUTHORIZED_BUNDLE is None
    else:
        assert 'GEMINI_CANARY_AUTH: "%s"' % want in wf, want
        assert WCX.REQUIRED_AUTH_ID == want
        # the constants must name the RUNNING code, not a remembered hash:
        # an authorization naming something else cannot arm, by design.
        assert WCX.AUTHORIZED_SHA == WCX.executable_sha()
        assert BUN.AUTHORIZED_BUNDLE == BUN.bundle_sha()
        assert WCX.AUTHORIZED_ENVELOPE == WCX.envelope_sha()
    # THE ENVELOPE MOVED, DELIBERATELY, on 2026-09-21: the q=1 LIVE bounds
    # joined the hash, so b3bff654... describes an envelope that no longer
    # exists. Pinning it here would now assert that no reviewed change may
    # ever land. The two invariants worth pinning are kept and are stronger:
    # the SHADOW (0.01) bounds did not move, and the authorization granted
    # against the old envelope is dead rather than silently carried forward.
    assert WCX.envelope_sha() != (
        "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17")
    from src.gemini_execution import continuous_probe as CPX
    assert str(CPX.ACTION_SIZE) == "0.01" and str(CPX.MAX_RESIDUAL) == "0.04"
    assert str(CPX.MAX_TOTAL_SPEND) == "0.15"
    # The executable hash is deliberately NOT pinned in this test. It pinned
    # 7dc3daae... and broke the moment an authorized fix landed -- a test
    # asserting that no fix may ever land. The hash belongs in the
    # authorization, which is exactly what invalidates it on a change.
    assert len(WCX.executable_sha()) == 64


def test_one_latch_per_authorization_and_no_latch_without_one():
    """One authorization, one window, and the latch makes that durable across
    a fresh runner with a clean disk.

    This asserted a COUNT, which broke the moment CONTINUOUS-6 ran -- a test
    pinning a moment again. The invariant is the CORRESPONDENCE: every latch
    on disk belongs to an authorization we know was granted, and every
    granted authorization has exactly one latch. A latch naming an
    authorization nobody issued is the thing that must never appear.
    """
    import pathlib
    granted = {
        "OWNER-2026-09-17-GEMINI-CANARY-1",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-1",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-2",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-3",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-4",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-5",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-6",
        "OWNER-2026-09-18-GEMINI-CONTINUOUS-7",
        "OWNER-2026-09-19-GEMINI-CONTINUOUS-8",
        # Owner-granted 2026-09-21 for the one BTC5m window at q=1. SPENT
        # without trading: run 35570948794 latched it (INTENT_PERSISTED,
        # venue_order_id null) and then refused, because run_canary.BRANCH
        # still named the instrumentation branch and the latch could not be
        # pushed. ZERO ORDERS, cohort 0/12, no cash moved. It appears here
        # because it WAS granted -- the registry records grants, not
        # outcomes -- and a spent authorization never arms again.
        "OWNER-2026-09-21-GEMINI-Q1-LIVE-1",
        # Owner-granted 2026-09-21 after LIVE-1 was spent by the unpushable
        # latch. LIVE-2 REACHED THE VENUE: run 35573430715 placed real orders
        # and took one fill. Its latch is on disk; spent authorizations never
        # arm again.
        "OWNER-2026-09-21-GEMINI-Q1-LIVE-2",
        # Owner-granted 2026-09-21 for ONE BTC15m window at q=1, cohort
        # COHORT-2026-09-21-BTC15M-Q1LIVE-1. IT TRADED: run 35628347483
        # entered GEMI-BTC15M2609211715-UP at tte 878.1s, placed three q=1
        # maker-or-cancel orders, took TWO fills (NO 1 @ 0.22, NO 1 @ 0.17),
        # was stopped by the risk gate at imbalance 2, and withdrew with zero
        # orders live. Spent; disarmed immediately afterwards. ITS LATCH
        # OUTLIVES THE DISARM ON PURPOSE -- that is what makes "one
        # authorization, one window" durable across a fresh runner, so the
        # registry records GRANTS, not what is currently armed.
        "OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1",
        # Owner-granted 2026-09-21 to remove the cap-2 truncation: ONE BTC15m
        # q=1 window under the CAP-4 envelope, cohort
        # COHORT-2026-09-21-BTC15M-CAP4-1, worst case $4.00.
        "OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-LIVE-1",
        # Owner-granted 2026-09-21: the SAME CAP-4 experiment rerun on a fresh
        # ledger after the EP012 durable-commitment fix. No parameter changed.
        "OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-LIVE-2",
        # Owner-granted 2026-09-21: the SAME CAP-4 experiment once more, on
        # the transient-UNREADABLE recovery build. Two validation goals --
        # the corrected EP012 accounting under real fills, and a bounded
        # HOLD if the venue produces one. Nothing is induced.
        "OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-LIVE-3",
    }
    d = pathlib.Path("analysis/gemini/canary_latch")
    on_disk = {p.name[len("latch_"):-len(".json")] for p in d.glob("latch_*.json")}
    assert on_disk <= granted, "a latch names an unissued authorization: %s" % (
        on_disk - granted)
    assert len(on_disk) == len(list(d.glob("latch_*.json"))), "duplicate latch"
    assert "OWNER-2026-09-18-GEMINI-CONTINUOUS-6" in on_disk


def test_c5_stays_frozen_while_c6_spends_its_own_budget():
    """Budget is never regained by rewriting evidence, and never shared.

    C5 is permanently 3 of 6 with its UNKNOWN intact. C6 spends only its own
    slots, and consumption is MONOTONIC in both: a cohort whose consumed
    count FELL would be a reset wearing the cohort's name.
    """
    import json
    from src.gemini_execution import cohort as CO

    c5 = CO.state("COHORT-2026-09-18-C5")
    assert c5["budget"] == 6 and c5["consumed"] == 3 and c5["remaining"] == 3
    rows = [json.loads(l) for l in open(c5["ledger_path"], encoding="utf-8")
            if l.strip()]
    outcomes = {r["slot"]: r["outcome"] for r in rows if r.get("kind") == "RESOLVE"}
    assert outcomes == {0: "ACCEPTED", 1: "ACCEPTED", 2: "UNKNOWN"}, outcomes

    c6 = CO.state("COHORT-2026-09-18-C6")
    assert c6["budget"] == 6 and c6["consumed"] == 6 and c6["remaining"] == 0
    c7 = CO.state("COHORT-2026-09-18-C7")
    assert c7["budget"] == 6
    assert 0 <= c7["consumed"] <= 6
    assert c7["predecessor"]["predecessor_ledger"].endswith(
        "COHORT-2026-09-18-C6/ledger.jsonl")
    assert c7["predecessor"]["recorded_consumed"] == 6
    assert c7["predecessor"]["intact"] is True
    assert c6["consumed"] + c6["remaining"] == c6["budget"]
    # the chain: C6 records C5's ledger, so a rewrite of C5 is detectable
    assert c6["predecessor"]["predecessor_ledger"].endswith(
        "COHORT-2026-09-18-C5/ledger.jsonl")
    assert c6["predecessor"]["recorded_consumed"] == 3
    assert c6["predecessor"]["intact"] is True


# ============================================================================
# EXECUTION PLUMBING, from the post-mortem of run 35553352750 (ZERO ORDERS)
# ============================================================================

Q1_BRANCH = "claude/gemini-btc-economic-viability-h3u7zl"
OLD_BRANCH = "claude/l5-execution-instrumentation-io6nzi"


def test_the_runner_checks_out_the_reviewed_tree():
    """DEFECT 1. The checkout pinned the instrumentation branch, so a dispatch
    from the q=1 branch took its WORKFLOW from one tree and its SOURCE from
    another. The armed step carried the right flags at a driver that had never
    heard of them."""
    wf = WF.read_text()
    assert re.search(r"^\s*ref:\s*%s\s*$" % re.escape(Q1_BRANCH), wf, re.M)
    # ASSERTED ON THE CONSTRUCTS, NOT ON THE WORD. The comment above the
    # checkout names the old branch on purpose -- that is the post-mortem, and
    # a test that forbade the name would delete the reason. What must not
    # exist is the old branch as an EFFECTIVE ref or push target.
    for construct in (r"^\s*ref:\s*%s" % re.escape(OLD_BRANCH),
                      r"git push\s+origin\s+HEAD:%s" % re.escape(OLD_BRANCH),
                      r"git fetch\s+origin\s+%s" % re.escape(OLD_BRANCH)):
        assert not re.search(construct, wf, re.M), construct


def test_evidence_is_pushed_back_where_it_came_from():
    """The push target FOLLOWS the checkout, necessarily. Left alone, it would
    have pushed the q=1 HEAD onto the instrumentation branch -- merging the
    reviewed tree into it as a side effect of an evidence commit."""
    wf = WF.read_text()
    assert "git push origin HEAD:%s" % Q1_BRANCH in wf
    assert "git fetch origin %s" % Q1_BRANCH in wf


def test_the_armed_step_can_fail():
    """DEFECT 2. `python ... | tee` exits with TEE's status, so the armed step
    of run 35553352750 died on argparse in 0.09s and was recorded SUCCESS."""
    wf = WF.read_text()
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    run = armed.split("run:", 1)[1]
    pipefail = run.index("set -o pipefail")
    driver = run.index("python -m src.gemini_execution.run_continuous")
    assert pipefail < driver, (
        "pipefail must be set BEFORE the driver runs, not after it")
    assert "| tee" in run, "the tee is deliberate; the swallowed status was not"


def test_pipefail_actually_propagates_under_the_runner_shell():
    """The property itself, not a promise about it. GitHub runs these steps as
    `bash -e {0}`; this drives a deliberately failing command through the same
    shape both ways."""
    import subprocess
    import tempfile

    def _status(prelude):
        with tempfile.NamedTemporaryFile("w", suffix=".sh",
                                         delete=False) as fh:
            fh.write(prelude
                     + 'python3 -c "import sys; sys.exit(3)" 2>&1 | tee '
                     + fh.name + '.out\n')
            path = fh.name
        return subprocess.run(["bash", "-e", path]).returncode

    assert _status("") == 0, (
        "without pipefail a failing command exits 0 -- this is the defect")
    assert _status("set -o pipefail\n") == 3, (
        "with pipefail the driver's status reaches the step")


def test_the_latch_pushes_to_the_branch_the_runner_checked_out():
    """DEFECT 3, found by run 35570948794 refusing.

    The latch is a LIVE SAFETY DEPENDENCY: make_durable() refuses the mutation
    if it cannot push. So run_canary.BRANCH and the workflow's checkout ref
    must name the same branch, or the driver checks out one tree and tries to
    latch onto another -- which is what happened, and which refused with ZERO
    ORDERS. The dangerous direction is the one that did NOT happen: a rebase
    that SUCCEEDED would have pushed the q=1 tree onto the instrumentation
    branch as a side effect of latching.
    """
    from src.gemini_execution import run_canary as RC

    wf = WF.read_text()
    ref = re.search(r"^\s*ref:\s*(\S+)\s*$", wf, re.M).group(1)
    assert RC.BRANCH == ref, (
        "latch branch %r != checkout ref %r; the latch cannot be pushed from "
        "the tree the runner is running" % (RC.BRANCH, ref))
    assert RC.BRANCH == Q1_BRANCH


def test_the_latch_is_a_safety_dependency_outside_the_bundle():
    """A fact worth failing on if it silently changes.

    run_canary.py can refuse every mutation -- make_durable() aborts the send
    when the latch cannot be pushed -- yet it is NOT a bundle member, so
    editing it does not invalidate an authorization. That is why DEFECT 3 was
    fixable without re-granting, and equally why a future edit to the latch
    would disarm nothing. Recorded so the gap is known rather than overlooked.
    """
    from src.gemini_execution import bundle as B

    assert "run_canary.py" not in B.MEMBERS
    # AND THE MEMBERS THAT DO CARRY BOUNDS ARE STILL COVERED. This previously
    # pinned literal hashes, which made it a MOMENT rather than an invariant:
    # it had to break the moment ws_continuous.py or continuous_probe.py
    # legitimately changed, and it did, on the q-size wiring. What must hold
    # is that every file carrying a bound is inside the hash.
    for carries_a_bound in ("ws_continuous.py", "continuous_probe.py",
                            "live_envelope.py", "run_continuous.py"):
        assert carries_a_bound in B.MEMBERS
    assert len(B.bundle_sha()) == 64


def test_the_plumbing_fix_moved_no_bound_and_no_flag():
    """The corrected run must be the SAME experiment. Nothing about the
    envelope, the size, the band, the cohort or the policy may have moved."""
    from src.gemini_execution import continuous_probe as CPX
    from src.gemini_execution import live_envelope as LEX

    wf = WF.read_text()
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    run = armed.split("run:", 1)[1]
    # The three literals this used to read are gone from the step BY DESIGN
    # -- they are the drift class -- so the same experiment is now pinned on
    # the spec that both steps consume.
    from src.gemini_execution import live_probe_spec as SPECX
    run = _code(run)
    assert "--spec" in run and "--expect-spec-sha" in run
    for gone in ("--envelope", "--entry-tte-band", "--cohort"):
        assert gone not in run, gone
    assert SPECX.BTC5M_Q1.envelope == "live_q1"
    assert SPECX.BTC5M_Q1.entry_tte_band == "240:280"
    assert LEX.LIVE_Q1.action_size == CPX.D("1")
    assert LEX.LIVE_Q1.max_committed_usd == CPX.D("2.50")
    assert LEX.LIVE_Q1.max_residual_usd == CPX.D("2.00")
    assert LEX.LIVE_Q1.max_working_orders == 2
    assert LEX.LIVE_Q1.max_concurrent_markets == 1
    assert LEX.LIVE_Q1.max_actions == 12
    # AND THE COHORT IS THE SAME ONE, NOW SPENT BY THE WINDOW IT WAS FOR.
    # This asserted consumed == 0, which pinned a MOMENT rather than an
    # invariant -- it had to break the first time the probe actually traded,
    # and it did. The durable properties are that the budget was never
    # raised, and that consumption never exceeds it.
    from src.gemini_execution import cohort as CO
    c = CO.state("COHORT-2026-09-21-Q1LIVE-1")
    assert c["budget"] == 12
    assert 0 <= c["consumed"] <= 12
    assert c["consumed"] + c["remaining"] == 12


def test_an_unpushable_latch_refuses_without_spending_the_grant():
    """THE FAILURE THAT COST A GRANT, made free.

    latch.consume() marks the authorization spent on disk BEFORE
    make_durable() pushes it. So when the push could never have succeeded, the
    old ordering burned an owner grant for an infrastructure reason with
    nothing traded -- which is precisely how
    OWNER-2026-09-21-GEMINI-Q1-LIVE-1 died in run 35570948794.

    The probe must therefore run BEFORE the consume, and it must push nothing.
    """
    import inspect
    from src.gemini_execution import run_canary as RC
    from src.gemini_execution import run_continuous as RCN

    src = inspect.getsource(RCN)
    # ON THE CALL, NEVER THE MENTION. Both names appear in the explanatory
    # comment above the probe, so index() finds the prose and reports an
    # ordering that is not the code's. This file has walked into that exact
    # trap twice before (see _sets_the_arm); assert on the ASSIGNMENT.
    # the assignment, however it is spelled. It is now conditional -- a
    # cohort proves pushability once, before it consumes the grant's latch --
    # so the pattern matches `x = can_make_durable()` and
    # `x = (can_make_durable() if ...)` alike, and still refuses to match the
    # prose.
    probe = re.search(r"^\s*\w+ = \(?can_make_durable\(\)", src,
                      re.M).start()
    consume = re.search(r"^\s*\w+ = latch\.consume\(", src, re.M).start()
    assert probe < consume, (
        "the pushability probe must run BEFORE the latch is consumed, or it "
        "cannot protect the grant it exists to protect")

    # AND IT PUSHES NOTHING. A probe that pushed would be a mutation of its
    # own, and on the wrong side of the arm.
    probe_src = inspect.getsource(RC.can_make_durable)
    assert '"push"' not in probe_src
    assert '"fetch"' in probe_src and '"merge-base"' in probe_src


def test_the_pushability_probe_rejects_exactly_the_run69_situation():
    """Unrelated history -- the run-69 failure -- must come back not-ok, and a
    branch that IS an ancestor of HEAD must come back ok. Driven on real git
    repositories rather than on a mock of git's opinion."""
    import subprocess
    import tempfile
    import pathlib
    from src.gemini_execution import run_canary as RC

    def _run(*a, cwd):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                              text=True)

    with tempfile.TemporaryDirectory() as tmp:
        remote = pathlib.Path(tmp) / "remote.git"
        work = pathlib.Path(tmp) / "work"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                       check=True)
        subprocess.run(["git", "clone", "-q", str(remote), str(work)],
                       check=True)
        _run("config", "user.email", "t@t", cwd=work)
        _run("config", "user.name", "t", cwd=work)
        (work / "a").write_text("1")
        _run("add", "a", cwd=work)
        _run("commit", "-qm", "base", cwd=work)
        base_branch = _run("rev-parse", "--abbrev-ref", "HEAD",
                           cwd=work).stdout.strip()
        _run("push", "-q", "origin", "HEAD:refs/heads/good", cwd=work)
        # an UNRELATED branch on the same remote: no shared history at all
        _run("checkout", "-q", "--orphan", "other", cwd=work)
        (work / "b").write_text("2")
        _run("add", "b", cwd=work)
        _run("commit", "-qm", "orphan", cwd=work)
        _run("push", "-q", "origin", "HEAD:refs/heads/unrelated", cwd=work)
        _run("checkout", "-q", base_branch, cwd=work)

        import os
        cwd0 = os.getcwd()
        branch0 = RC.BRANCH
        try:
            os.chdir(work)
            RC.BRANCH = "good"
            assert RC.can_make_durable()["ok"] is True
            RC.BRANCH = "unrelated"
            bad = RC.can_make_durable()
            assert bad["ok"] is False
            assert "ancestor" in bad["why"]
            RC.BRANCH = "no-such-branch"
            assert RC.can_make_durable()["ok"] is False
        finally:
            os.chdir(cwd0)
            RC.BRANCH = branch0


def test_the_workflow_is_actually_parseable_yaml():
    """THE GAP THAT LET A BROKEN WORKFLOW BE PUSHED.

    Every other test in this file reads the workflow as TEXT, so all 59 of
    them stayed green while the file was unparseable and GitHub refused the
    dispatch with "Workflow does not have 'workflow_dispatch' trigger" -- a
    message that points at the trigger and not at the real cause.

    The break was an inserted line indented LESS than the `run: |` block it
    sat in, which terminates the block scalar and makes YAML read the next
    shell line as a mapping key. A text assertion cannot see that; a parse
    can.
    """
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(WF.read_text())
    # `on` is the YAML 1.1 boolean True unless quoted, so accept either
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), "the workflow lost its trigger block"
    assert "workflow_dispatch" in on
    steps = doc["jobs"]["gate"]["steps"]
    assert len(steps) > 10
    # and every step that runs a shell block has a non-empty one, which is
    # what a silently-truncated block scalar would destroy
    for s in steps:
        if "run" in s:
            assert str(s["run"]).strip(), s.get("name")
