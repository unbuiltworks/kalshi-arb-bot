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
    i = wf.index("Commit ONLY the evidence artifact")
    pattern = re.search(r"grep -qvE '([^']+)'",
                        wf[i:i + 3200]).group(1)
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
    """
    wf = WF.read_text()
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    assert "--cohort" in armed, "the armed step must bind a mutation cohort"
    # the FRESH cohort. C5 is spent at 3/6 and its three unused slots stay
    # unused; rebinding it would be recycling a spent budget.
    assert "COHORT-2026-09-18-C8B" in armed
    run = armed.split("run:", 1)[1]
    for spent in ("COHORT-2026-09-18-C5", "COHORT-2026-09-18-C6",
                  "COHORT-2026-09-18-C7"):
        assert spent not in run, spent


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
    assert c8b["budget"] == 12 and c8b["consumed"] == 0
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
    assert 'default: "90:300"' in wf[i:i + 500]
    # the STEP, not a fixed slice: the first cut was 900 chars and stopped
    # short of the very line it was checking.
    unarmed = wf.split("Unarmed production path", 1)[1].split(
        "ONE authorized Gemini continuous window", 1)[0]
    assert "unarmed_entry_band" in unarmed and "'90:300'" in unarmed
    # and the ARMED step's band is fixed in the file, never dispatch-supplied
    armed = wf.split("ONE authorized Gemini continuous window", 1)[1]
    assert '--entry-tte-band "240:280"' in armed
    assert "github.event.inputs.unarmed_entry_band" not in armed


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
    # the window itself: entry band top, less the driver's withdraw floor
    band_hi = float(_re.search(r'--entry-tte-band "\d+:(\d+)"',
                               armed).group(1))
    need_s = RCX.MAX_ENTRY_WAIT_S + band_hi
    assert step * 60 > need_s, (step, need_s)
    assert job > step, (job, step)


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
    # the UNARMED production-path step keeps the sentinel: it exists to be
    # refused, and an armed one would prove nothing.
    assert ids <= {want, sentinel} and sentinel in ids, ids
    if LIVE_AUTHORIZATION is None:
        assert ids == {sentinel}, ids
        assert WCX.REQUIRED_AUTH_ID is None and WCX.AUTHORIZED_SHA is None
        assert BUN.AUTHORIZED_BUNDLE is None
    else:
        assert want in ids, ids
        assert WCX.REQUIRED_AUTH_ID == want
        # the constants must name the RUNNING code, not a remembered hash:
        # an authorization naming something else cannot arm, by design.
        assert WCX.AUTHORIZED_SHA == WCX.executable_sha()
        assert BUN.AUTHORIZED_BUNDLE == BUN.bundle_sha()
        assert WCX.AUTHORIZED_ENVELOPE == WCX.envelope_sha()
    # THE ENVELOPE NEVER MOVES. That is the invariant worth pinning here.
    assert WCX.envelope_sha() == (
        "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17")
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
