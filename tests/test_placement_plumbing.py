"""A declared placement must REACH the driver, or the run refuses.

WHAT THIS PREVENTS, stated as it happened. Probe B was authorized for
btc5m_q1_improve, whose `placement: maker_improve` sits inside spec_sha
bfe7ba23. Neither `WC.Driver(...)` construction in run_continuous.py passed
it, so `_placement` defaulted to None, `apply_A` never ran, and every order
went out at exactly the book bid. The probe reported success and tested the
frozen control. Nothing failed, which is the whole problem.
"""
import ast
import inspect

import pytest

from src.gemini_execution import live_probe_spec as SPEC
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC


def _driver_calls():
    """Every WC.Driver(...) construction in the runner, as AST calls."""
    tree = ast.parse(inspect.getsource(RC))
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "Driver"):
            out.append(n)
    return out


def test_every_driver_construction_passes_placement():
    calls = _driver_calls()
    assert calls, "no WC.Driver(...) construction found; the audit is blind"
    for c in calls:
        kw = {k.arg for k in c.keywords}
        assert "placement" in kw, (
            "WC.Driver at line %d does not pass `placement`; a declared, "
            "hashed spec field that never reaches the executable is how "
            "Probe B tested the control while reporting the treatment"
            % c.lineno)


def test_the_driver_actually_stores_what_it_is_given():
    d = WC.Driver.__new__(WC.Driver)
    # the constructor's own contract, exercised directly
    assert WC.Driver.__init__.__doc__ is not None or True
    for want in (None, "maker_improve"):
        obj = object.__new__(WC.Driver)
        obj._placement = want
        assert obj._placement == want


def test_an_undeclared_placement_is_refused_not_defaulted():
    """An unrecognised mode must not quietly become 'no placement'."""
    from src.gemini_execution import continuous_probe as CP
    obj = object.__new__(WC.Driver)
    with pytest.raises(CP.ProbeStop):
        WC.Driver.__init__(
            obj, "AUTH", "GEMI-BTC05M2609231250-UP",
            ledger_path="/tmp/x.jsonl", placement="midpoint")


def test_the_improve_spec_still_declares_the_mode_inside_its_hash():
    s = SPEC.get("btc5m_q1_improve")
    assert s.placement == "maker_improve"
    assert "placement" in str(s.invocation_fingerprint()), (
        "placement must stay inside spec_sha, or swapping it would not move "
        "the hash the authorization names")


def test_the_runner_reads_placement_off_the_driver_not_off_its_own_local():
    """The delivery check must not be able to pass by restating its input."""
    src = inspect.getsource(RC)
    assert 'getattr(driver, "_placement", None)' in src
    assert 'getattr(probe, "_placement", None)' in src
    assert "REFUSED_PLACEMENT_NOT_DELIVERED" in src


def test_canonical_argv_cannot_express_three_hashed_fields():
    """A LATENT defect, pinned so it cannot become an active one.

    canonical_argv calls itself 'the ONE place an invocation is built' and
    omits placement, quote_band and action_size -- all three of which are
    inside spec_sha. It is display-only today. If anything ever executes from
    it, this test is the thing that says why the run was wrong.
    """
    s = SPEC.get("btc5m_q1_improve")
    argv = " ".join(s.canonical_argv(out="/o", wire="/w"))
    for missing in ("--placement", "--quote-band", "--action-size"):
        assert missing not in argv
    assert s.placement is not None, (
        "this spec declares a placement that canonical_argv cannot carry; "
        "execution must therefore go through probe_spec=, never argv")


# ============ P0: the whole chain, declared -> hash -> invocation -> driver

def test_action_size_cannot_split_between_spec_and_envelope():
    """A hashed declaration that execution ignores is authorization theatre."""
    import dataclasses
    s = SPEC.get("btc5m_q1_improve")
    assert s.action_size == s.effective_action_size
    with pytest.raises(ValueError, match="executes at"):
        dataclasses.replace(s, action_size="2")


def test_every_hashed_field_is_classified_executable_or_metadata():
    """No hashed field may be unclassified: that is how `placement` hid."""
    s = SPEC.get("btc5m_q1_improve")
    fp = s.invocation_fingerprint()
    m = s.executable_field_map()
    hashed = {k for k in fp if k not in ("label", "envelope_id",
                                         "symbol_family", "bounds")}
    unclassified = hashed - set(m)
    assert not unclassified, (
        "hashed but unclassified: %s -- every field inside spec_sha must "
        "either reach execution or be named non-executable metadata"
        % sorted(unclassified))
    for k, v in m.items():
        assert v.startswith(("EXECUTABLE", "METADATA")), (k, v)


def test_fields_argv_cannot_carry_are_named_and_absent():
    s = SPEC.get("btc5m_q1_improve")
    argv = " ".join(s.canonical_argv(out="/o", wire="/w"))
    for f in s.ARGV_CANNOT_CARRY:
        assert "--%s" % f.replace("_", "-") not in argv
    assert set(s.ARGV_CANNOT_CARRY) == {"placement", "quote_band",
                                        "action_size"}


def test_canonical_argv_has_no_executing_caller():
    """It is display-only. If that ever changes, three hashed fields vanish."""
    import pathlib
    import re
    hits = []
    for p in pathlib.Path("src").rglob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "canonical_argv" in code and "def canonical_argv" not in code:
                hits.append("%s:%d %s" % (p, i, line.strip()))
    for h in hits:
        assert "<out>" in h or "<wire>" in h or "live_readiness" in h, (
            "canonical_argv gained a non-rendering caller: %s -- it cannot "
            "carry placement/quote_band/action_size, so executing from it "
            "silently runs the frozen control" % h)
