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


def test_the_rung_and_the_market_allowance_are_pinned_in_the_image():
    """A service variable overrides image ENV, so the envelope must not depend
    on one -- the legacy service is the cautionary case.

    RESTATED 2026-09-16 (OWNER "Option A"), not relaxed. It asserted R1 and ONE
    market, which were the right values until the owner authorized a bounded
    ten-market probe (three until OWNER 2026-09-17). The property under
    test never changed: both values must
    be IN THE IMAGE, so no Railway variable decides the envelope. Leaving the
    old numbers here would have been the same class of error as the legacy
    service -- the envelope stated in one place and run from another.
    """
    d = "\n".join(_directives(DF))
    assert "PMUS_SPRING_RUNG=CP1" in d
    assert "PMUS_SPRING_RUNG_MARKETS=10" in d
    # and it must be exactly one value each: an image that pins two is an
    # image whose envelope depends on directive order.
    assert d.count("PMUS_SPRING_RUNG=") == 1
    assert d.count("PMUS_SPRING_RUNG_MARKETS=") == 1


def test_the_pinned_rung_is_the_one_the_ladder_bounds():
    """The image may not name a rung the tested ladder does not bound. A typo
    here falls back to R1 silently; a rung that does not exist at all is an
    envelope nobody reviewed."""
    from src import pmus_l5_ramp_ladder as ladder
    r = ladder.rung("CP1")
    assert r["id"] == "CP1"
    assert r["per_market_cap_usd"] == "5.00"
    # OWNER 2026-09-17: breadth 3 -> 10. The image env and this rung are two of
    # the three numbers min()'d into the allowance (the mandate is the third),
    # so they must move together or the authorization is silently capped.
    assert r["markets_per_campaign"] == 10
    assert r["promote"] is None, "CP1 must never promote itself"


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
    # THE R1 IMAGE MAY ADD, BUT ONLY WHAT IS NAMED HERE. Parity with the
    # legacy image was the right rule while R1 was a strict subset of it. Since
    # 2026-09-16 R1 also ships its own AUTHORIZATION -- the HALT latch and the
    # (absent) live_mandate.json -- which the legacy service, halted on its own
    # env latch and building from a different branch, does not take.
    #
    # The exception is enumerated rather than relaxed: anything else the R1
    # image starts copying still fails here, which is the property this test
    # exists for.
    ALLOWED_R1_ADDITIONS = {
        "COPY analysis/l5/authorization/ analysis/l5/authorization/",
    }
    assert not (got - legacy - ALLOWED_R1_ADDITIONS), (
        "the R1 image must not add files of its own beyond its authorization")


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


def test_an_ordinary_push_cannot_deploy_this_service():
    """THE property, restated (OWNER 2026-09-16 P0 authorised a deploy).

    It used to assert the trigger directory did not EXIST, which was right
    while nothing was authorised to deploy -- but "the switch is missing" and
    "the switch is not being flipped" are different properties, and only the
    second one survives an authorised deploy. What must stay true is that the
    build trigger is exactly this one directory, so a push touching source,
    tests or analysis leaves the running probe alone.
    """
    m = json.loads(open(MANIFEST).read())
    assert m["build"]["watchPatterns"] == [
        "deploy/l5_r1_probe/ENABLE_REDEPLOY_BY_OWNER_ONLY/**"]


def test_every_deploy_of_this_service_is_recorded_with_its_authorisation():
    """If the switch exists it must carry the log, or "owner only" is a
    directory name rather than a control."""
    d = os.path.join(os.path.dirname(DF), "ENABLE_REDEPLOY_BY_OWNER_ONLY")
    if not os.path.exists(d):
        return                      # no deploy has been authorised yet
    trigger = os.path.join(d, "DEPLOY_TRIGGER")
    assert os.path.exists(trigger), "the trigger directory has no log"
    body = open(trigger, encoding="utf-8").read()
    assert "OWNER" in body and "DISARMED" in body
    # it is a log, not a switch anyone may flip: it must say so
    assert "Do not touch it" in body
    # AND IT MUST NEVER CARRY AN ARMING VALUE.
    #
    # Sharpened 2026-09-16: this used to forbid the variable NAMES followed by
    # "=", which also rejected `PMUS_SPRING_LIVE=NO` -- a DISARMING value, in
    # the log entry recording the emergency halt. Forbidding the record of a
    # stop is backwards, so the check now tests what actually arms.
    import re as _re
    assert not _re.search(r"PMUS_SPRING_LIVE\s*=\s*YES", body, _re.I), \
        "the log carries an arming value for PMUS_SPRING_LIVE"
    for name in ("PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
                 "PMUS_SPRING_GATE_EVIDENCE"):
        # an assignment with a non-empty token is a real id; naming the
        # variable in prose is not
        assert not _re.search(rf"{name}\s*=\s*\S", body), \
            f"the log carries a value for {name}"


def test_restart_is_never():
    m = json.loads(open(MANIFEST).read())
    assert m["deploy"]["restartPolicyType"] == "NEVER"


def test_the_manifest_sets_no_arming_variable():
    blob = open(MANIFEST).read()
    for name in ("PMUS_SPRING_LIVE", "PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
                 "PMUS_SPRING_GATE_EVIDENCE"):
        assert f'"{name}"' not in blob and f"{name}=" not in blob
