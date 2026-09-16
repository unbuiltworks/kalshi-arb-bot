"""The shadow image cannot trade, and that is structural rather than declared."""
from __future__ import annotations

import json
import os

DF = "deploy/l5_v1_shadow/Dockerfile"
MANIFEST = "deploy/l5_v1_shadow/railway.json"
R1 = "deploy/l5_r1_probe/Dockerfile"


def _copies(path):
    return [l.split()[1] for l in open(path) if l.startswith("COPY ")]


def test_no_execution_module_is_in_the_shadow_image():
    """The safety property, stated as a set difference against the live image
    rather than as a promise."""
    shadow = set(_copies(DF))
    forbidden = {"src/pmus_micro_executor.py", "src/pmus_q1_executor.py",
                 "src/pmus_q1_executor_v4.py", "src/pmus_fill_ledger.py",
                 "src/pmus_spring_l5_session.py",
                 "src/pmus_l5_continuous_session.py",
                 "src/pmus_spring_l5_probe_entry.py"}
    assert shadow & forbidden == set(), shadow & forbidden
    # and every one of those IS in the live image, so the check is not vacuous
    assert forbidden & set(_copies(R1)) == forbidden


def test_the_shadow_image_declares_no_credential_and_no_arming_variable():
    body = open(DF).read() + open(MANIFEST).read()
    for name in ("PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY", "PMUS_SPRING_LIVE",
                 "PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
                 "PMUS_SPRING_GATE_EVIDENCE"):
        assert name not in body, name


def test_the_shipped_modules_contain_no_order_verb():
    for src in _copies(DF):
        if not src.endswith(".py"):
            continue
        body = open(src, encoding="utf-8").read()
        for verb in ("place_limit_buy", "place_order", "submit_order",
                     "cancel_exact"):
            assert verb not in body, (src, verb)


def test_the_role_guard_refuses_to_boot_under_another_role():
    cmd = [l for l in open(DF) if l.startswith("CMD")][0]
    assert "l5-v1-shadow" in cmd and "SERVICE_ROLE_MISMATCH" in cmd
    assert "exit 64" in cmd
    assert "sed " not in cmd and "patch " not in cmd


def test_a_shadow_may_restart_where_a_money_mover_may_not():
    """Deliberately NOT 'NEVER': this service holds no position and moves no
    money, so restarting it loses nothing and keeps the sample continuous.
    The live probe's NEVER is a different property for a different reason."""
    m = json.loads(open(MANIFEST).read())
    assert m["deploy"]["restartPolicyType"] == "ON_FAILURE"
    r1 = json.loads(open("deploy/l5_r1_probe/railway.json").read())
    assert r1["deploy"]["restartPolicyType"] == "NEVER"


def test_the_manifest_start_command_matches_the_image_cmd():
    """A manifest that disagrees with the image is how a boot-time surprise
    gets in -- the legacy probe's sed arrived exactly that way."""
    m = json.loads(open(MANIFEST).read())
    start = m["deploy"]["startCommand"]
    cmd = [l for l in open(DF) if l.startswith("CMD")][0]
    assert "src.l5_v1_shadow_runner" in start and "src.l5_v1_shadow_runner" in cmd
    assert "l5-v1-shadow" in start and "SERVICE_ROLE_MISMATCH" in start
    assert "sed " not in start and "patch " not in start
