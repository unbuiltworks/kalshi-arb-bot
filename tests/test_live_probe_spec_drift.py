"""The drift class that produced run 35601903823, and the spec that ends it.

WHAT HAPPENED. The workflow reconstructed the experiment's arguments BY HAND
in two separate steps. Readiness got `--entry-tte-band 840:880` and nothing
else, so it resolved family BTC5m and envelope SHADOW_Q001 and intersected
the band to the empty [840, 300]. The armed step meanwhile still carried
literal `--envelope live_q1` and `--entry-tte-band 240:280` with no family.
Two hand-built command lines for one experiment can always disagree.

These tests assert the disagreement is now IMPOSSIBLE, not merely absent.
"""
import pathlib
import re

import pytest

from src.gemini_execution import live_probe_spec as SPEC

WF = pathlib.Path(".github/workflows/gemini-settlement-gate.yml")
S = SPEC.BTC15M_Q1


# ---------------------------------------------- the spec resolves one way

def test_the_btc15m_spec_is_the_candidate_exactly():
    assert S.family == "btc15m"
    assert S.envelope == "live_q1_btc15m"
    assert S.entry_tte_band == "840:880"
    assert S.action_size == "1"
    assert S.symbol_family == "GEMI-BTC15M"
    assert S.envelope_obj.name == "GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1"
    assert S.family_obj.prefix == "BTC15M"


def test_readiness_and_live_argv_differ_ONLY_by_the_boundary_flag():
    """The whole invariant, on the argv itself."""
    r = S.canonical_argv(out="/o", wire="/w", readiness=True)
    lv = S.canonical_argv(out="/o", wire="/w", readiness=False)
    assert set(r) - set(lv) == {"--readiness"}
    assert set(lv) - set(r) == set()


def test_the_spec_hash_is_identical_for_readiness_and_live():
    """spec_sha covers the EXPERIMENT, so the two runs hash the same."""
    assert S.spec_sha() == S.spec_sha()
    assert len(S.spec_sha()) == 64
    # and the auth id, which legitimately differs, is excluded
    fp = S.invocation_fingerprint()
    assert "auth_id" not in fp


def test_an_undeclared_spec_is_refused_not_defaulted():
    with pytest.raises(SPEC.SpecUnknown):
        SPEC.get("btc15m_q2")


# =============================== THE FIVE NEW DRIFT REGRESSIONS

def _steps():
    """Steps BY NAME, from the parsed YAML.

    My first version split the raw text on "LIVE_READINESS_GATE" and on the
    armed step's title -- both of which also appear in the workflow's INPUT
    DESCRIPTIONS, so the split landed on `- run: pip install` and the
    assertions tested the wrong region entirely. Parsing is not optional
    here; the whole point of these tests is to read the real step.
    """
    import yaml
    doc = yaml.safe_load(WF.read_text())
    return {str(st.get("name") or ""): str(st.get("run") or "")
            for st in doc["jobs"]["gate"]["steps"]}


def _code(step):
    """The step's CODE, with its comment lines removed.

    THE SAME TRAP `_sets_the_arm` DOCUMENTS TWICE. These assertions are about
    what the command line CONTAINS, and the armed step's post-mortem comment
    explains the drift by naming the very flags it forbids
    ("Hand-built --family/--envelope/--entry-tte-band literals are what let
    run 35601903823's two steps resolve different experiments"). A substring
    test flags that prose and the gate cries wolf. So: drop the comments, and
    match the flag as an ARGUMENT (`--family <value>`), never as text.
    """
    return "\n".join(ln for ln in step.splitlines()
                      if not ln.lstrip().startswith("#"))


def _passes(step, flag):
    """True iff `flag` appears as a real argument with a value."""
    return re.search(r"%s\s+\S" % re.escape(flag), _code(step)) is not None


def _readiness_step():
    steps = _steps()
    hit = [v for k, v in steps.items() if "LIVE_READINESS_GATE" in k]
    assert hit, ("the workflow must INVOKE the gate as a step; having the "
                 "module in the repo is not the same as the Actions path "
                 "running it. steps: %s" % list(steps))
    return hit[0]


def _armed_step():
    steps = _steps()
    hit = [v for k, v in steps.items()
           if "ONE authorized Gemini continuous window" in k]
    assert hit, list(steps)
    return hit[0]


def test_A_readiness_omitting_family_while_live_specifies_it_is_impossible():
    """Both steps must take the family from the SPEC, not a literal."""
    for step in (_readiness_step(), _armed_step()):
        assert _passes(step, "--spec"), step[:300]
        assert not _passes(step, "--family"), (
            "a hand-built --family is exactly how the two steps drifted")


def test_B_readiness_omitting_the_envelope_is_impossible():
    for step in (_readiness_step(), _armed_step()):
        assert not _passes(step, "--envelope"), (
            "the envelope must come from the spec, not a literal")


def test_C_readiness_shadow_while_live_q1_is_impossible():
    """Neither step may name an envelope at all, so they cannot differ."""
    both = _code(_readiness_step()) + _code(_armed_step())
    for literal in ("shadow_q001", "live_q1_btc15m", "live_q1"):
        assert literal not in both, literal


def test_D_a_band_without_a_family_cannot_be_expressed():
    """840:880 with a defaulted BTC5m family produced the empty [840,300]."""
    for step in (_readiness_step(), _armed_step()):
        assert not _passes(step, "--entry-tte-band"), (
            "the band must come from the spec, which carries its family")
    # and the spec's own band is the BTC15m one, intersected against the
    # BTC15m ceiling rather than BTC5m's 300
    assert S.entry_tte_band == "840:880"
    assert S.family_obj.max_entry_tte_s >= 880.0


def test_E_the_armed_step_no_longer_carries_the_stale_literals():
    """`live_q1` and `240:280` in the armed step are the drift itself."""
    armed = _code(_armed_step())
    assert "240:280" not in armed
    assert re.search(r"--envelope\s+live_q1\b", armed) is None
    assert _passes(armed, "--spec")


def test_both_steps_name_the_SAME_spec_id():
    """Not "both say btc15m_q1" -- both must READ ONE VALUE.

    A literal repeated in two places is the drift class itself: it is correct
    until somebody edits one of them. So the assertion is that exactly one
    `--spec` expression exists across the two steps, that it is a reference
    rather than a literal, and that the two `SPEC_ID` bindings behind it are
    character-identical and default to this candidate.
    """
    r, a = _readiness_step(), _armed_step()
    ids = set(re.findall(r"--spec\s+(\S+)", _code(r) + _code(a)))
    assert len(ids) == 1, ids
    token = ids.pop()
    assert "SPEC_ID" in token, ("the spec id must be a reference, not a "
                                "literal repeated per step: %s" % token)

    import yaml
    doc = yaml.safe_load(WF.read_text())
    binds = [str((st.get("env") or {}).get("SPEC_ID"))
             for st in doc["jobs"]["gate"]["steps"]
             if "SPEC_ID" in (st.get("env") or {})]
    assert len(set(binds)) == 1, binds
    assert S.spec_id in binds[0], binds[0]


def test_the_armed_step_consumes_the_readiness_spec_hash():
    """The invariant has to be enforced by the workflow, not just available."""
    assert _passes(_armed_step(), "--expect-spec-sha")


def test_the_workflow_actually_invokes_the_readiness_gate():
    """Having live_readiness.py in the repo is insufficient."""
    wf = WF.read_text()
    assert "--readiness" in wf
    assert "LIVE_READINESS_GATE" in wf
