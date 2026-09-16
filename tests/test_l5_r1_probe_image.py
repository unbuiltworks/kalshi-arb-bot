"""The isolated R1 probe image must execute the reviewed source, unmodified."""
import os

DF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "deploy", "l5_r1_probe", "Dockerfile")
LEGACY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "deploy", "pmus_spring_l5", "Dockerfile")


def _directives(path):
    """Executable lines only. A comment describing a mutation is not one."""
    return [l.rstrip("\n") for l in open(path)
            if l.strip() and not l.lstrip().startswith("#")]


def test_the_image_performs_no_source_mutation():
    """The legacy image patches source at BUILD time and again at BOOT. This
    one must do neither, or 'exact reviewed source' means nothing."""
    for d in _directives(DF):
        assert "sed " not in d, d
        assert "apply_complete_reject_retry" not in d, d
        assert "py_compile" not in d, d
        assert "patch " not in d, d


def test_the_legacy_image_really_does_mutate_source():
    """Guards the test above: if the legacy image ever stopped mutating, the
    comparison would be vacuous and nobody would notice."""
    legacy = "\n".join(_directives(LEGACY))
    assert "apply_complete_reject_retry" in legacy


def test_no_legacy_state_root_and_no_r5ft_repair():
    for d in _directives(DF):
        assert "/state/pmus-spring" not in d, d
        assert "pmus_r5ft_ledger_repair" not in d, d


def test_the_state_root_is_dedicated():
    assert any("PMUS_Q1_STATE_DIR=/state/l5-r1-probe" in d
               for d in _directives(DF))


def test_r1_and_one_market_are_pinned_in_the_image():
    """A service variable overrides image ENV, so the envelope must not depend
    on one -- the legacy service is the cautionary case."""
    d = "\n".join(_directives(DF))
    assert "PMUS_SPRING_RUNG=R1" in d
    assert "PMUS_SPRING_RUNG_MARKETS=1" in d


def test_the_image_sets_no_arming_variable():
    d = "\n".join(_directives(DF))
    for name in ("PMUS_SPRING_LIVE", "PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
                 "PMUS_SPRING_GATE_EVIDENCE"):
        assert name not in d, f"{name} must never be baked into the image"


def test_the_role_guard_is_distinct_from_the_legacy_role():
    d = "\n".join(_directives(DF))
    assert "PMUS_SERVICE_ROLE=l5-r1-probe" in d
    assert "spring-l5-probe" not in d, ("the legacy role must not start this "
                                        "image, nor this role that one")


def test_the_command_execs_the_reviewed_module_directly():
    cmd = [d for d in _directives(DF) if d.startswith("CMD")]
    assert len(cmd) == 1
    assert "exec python3 -m src.pmus_l5_continuous_session" in cmd[0]
    assert "&&" not in cmd[0].split("SERVICE_ROLE_MISMATCH")[1], (
        "no extra boot step may run before the session")


def test_the_dropped_copies_are_exactly_the_two_legacy_ones():
    got = {d for d in _directives(DF) if d.startswith("COPY")}
    legacy = {d for d in _directives(LEGACY) if d.startswith("COPY")}
    dropped = legacy - got
    assert all(("apply_complete_reject_retry" in d or
                "pmus_r5ft_ledger_repair" in d) for d in dropped), dropped
    assert not (got - legacy), "the R1 image must not add files of its own"


# ------------------------------------------------- the manifest

import json

MANIFEST = os.path.join(os.path.dirname(DF), "railway.json")


def test_the_manifest_cannot_bypass_the_role_guard():
    """The legacy service's LIVE startCommand differs from its manifest and
    that is exactly how the boot-time sed got in. Here they must agree."""
    m = json.loads(open(MANIFEST).read())
    start = m["deploy"]["startCommand"]
    cmd = [d for d in _directives(DF) if d.startswith("CMD")][0]
    assert "l5-r1-probe" in start and "SERVICE_ROLE_MISMATCH" in start
    assert "src.pmus_l5_continuous_session" in start
    assert "src.pmus_l5_continuous_session" in cmd
    assert "sed " not in start


def test_redeploy_is_owner_gated_and_the_gate_does_not_exist():
    m = json.loads(open(MANIFEST).read())
    pats = m["build"]["watchPatterns"]
    assert pats == ["deploy/l5_r1_probe/ENABLE_REDEPLOY_BY_OWNER_ONLY/**"]
    assert not os.path.exists(os.path.join(os.path.dirname(DF),
                                           "ENABLE_REDEPLOY_BY_OWNER_ONLY"))


def test_restart_is_never():
    m = json.loads(open(MANIFEST).read())
    assert m["deploy"]["restartPolicyType"] == "NEVER"


def test_the_manifest_sets_no_arming_variable():
    blob = open(MANIFEST).read()
    for name in ("PMUS_SPRING_LIVE", "PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
                 "PMUS_SPRING_GATE_EVIDENCE"):
        assert f'"{name}"' not in blob and f"{name}=" not in blob
