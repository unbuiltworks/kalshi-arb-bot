"""The V1.1 shadow image cannot trade either, and for the same structural
reasons -- asserted against the live image rather than promised in prose.

V1.1 runs BESIDE the V1 control, so these also pin the properties that make the
two comparable: the control's own image and manifest are untouched, and V1.1
ships the same Stage-2 artifact rather than a re-fit one.
"""
from __future__ import annotations

import json

DF = "deploy/l5_v1_1_shadow/Dockerfile"
MANIFEST = "deploy/l5_v1_1_shadow/railway.json"
V1_DF = "deploy/l5_v1_shadow/Dockerfile"
R1 = "deploy/l5_r1_probe/Dockerfile"


def _copies(path):
    return [l.split()[1] for l in open(path) if l.startswith("COPY ")]


def test_no_execution_module_is_in_the_shadow_image():
    shadow = set(_copies(DF))
    forbidden = {"src/pmus_micro_executor.py", "src/pmus_q1_executor.py",
                 "src/pmus_q1_executor_v4.py", "src/pmus_fill_ledger.py",
                 "src/pmus_spring_l5_session.py",
                 "src/pmus_l5_continuous_session.py",
                 "src/pmus_spring_l5_probe_entry.py"}
    assert shadow & forbidden == set(), shadow & forbidden
    assert forbidden & set(_copies(R1)) == forbidden      # not a vacuous check


def test_the_shadow_image_declares_no_credential_and_no_arming_variable():
    """Scans for the dangerous FORM -- a variable being set -- not for the name.
    The manifest names these precisely to record that it carries none of them,
    and a bare substring check reads that disclaimer as the violation."""
    body = open(DF).read() + open(MANIFEST).read()
    for name in ("PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY", "PMUS_SPRING_LIVE",
                 "PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
                 "PMUS_SPRING_GATE_EVIDENCE"):
        for form in (f"{name}=", f'"{name}":', f"ENV {name}", f"ARG {name}"):
            assert form not in body, form
    # and the check is not vacuous: this is what being set looks like
    assert "PMUS_SERVICE_ROLE=l5-v1-1-shadow" in open(DF).read()


def test_the_shipped_modules_contain_no_order_verb():
    for src in _copies(DF):
        if not src.endswith(".py"):
            continue
        body = open(src, encoding="utf-8").read()
        for verb in ("place_limit_buy", "place_order", "submit_order",
                     "cancel_exact"):
            assert verb not in body, (src, verb)


def test_no_mandate_is_carried_because_none_could_be_consumed():
    """A shadow that shipped a live mandate would be claiming an envelope it
    has no way to spend, and the R1 image's absent mandate is the safe state."""
    assert not any("live_mandate" in c for c in _copies(DF))
    assert "live_mandate" not in open(MANIFEST).read()


def test_the_role_guard_refuses_to_boot_under_another_role():
    cmd = [l for l in open(DF) if l.startswith("CMD")][0]
    assert "l5-v1-1-shadow" in cmd and "SERVICE_ROLE_MISMATCH" in cmd
    assert "exit 64" in cmd
    assert "sed " not in cmd and "patch " not in cmd


def test_the_manifest_start_command_matches_the_image_cmd():
    m = json.loads(open(MANIFEST).read())
    start = m["deploy"]["startCommand"]
    cmd = [l for l in open(DF) if l.startswith("CMD")][0]
    for s in (start, cmd):
        assert "src.l5_v1_1_shadow_runner" in s
        assert "l5-v1-1-shadow" in s and "SERVICE_ROLE_MISMATCH" in s
        assert "sed " not in s and "patch " not in s
    assert m["deploy"]["restartPolicyType"] == "ON_FAILURE"


def test_the_state_dir_variable_is_the_one_the_runner_actually_reads():
    """A misnamed variable would silently fall through to the default and the
    volume would look mounted while the rows went somewhere else."""
    from src import l5_v1_1_shadow_runner as R
    body = open(DF).read()
    assert f"{R.STATE_DIR_ENV}=" in body
    assert "L5_V1_STATE_DIR=" not in body          # the V1 name, easy to copy


def test_v1_1_ships_the_same_stage_two_artifact_as_the_control():
    """If V1.1 carried a re-fit transition model the comparison would be
    measuring two changes at once. Stage 1 and Stage 3 are the new artifact."""
    v1, v11 = set(_copies(V1_DF)), set(_copies(DF))
    assert "analysis/l5/l5_action_model_v2.json" in v1 & v11
    added = v11 - v1
    assert added == {"src/l5_v1_1_shadow.py", "src/l5_v1_1_shadow_runner.py",
                     "analysis/l5/act_wait/__init__.py",
                     "analysis/l5/act_wait/l5_act_wait_v1.json"}, added


def test_deploying_v1_1_does_not_touch_the_control_service():
    """The V1 manifest watches only V1 paths, so a V1.1 commit cannot rebuild
    the control mid-sample and reset it."""
    v1 = json.loads(open("deploy/l5_v1_shadow/railway.json").read())
    for pat in v1["build"]["watchPatterns"]:
        assert "v1_1" not in pat, pat
    assert v1["build"]["dockerfilePath"] == V1_DF
