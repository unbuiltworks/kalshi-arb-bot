"""The set of placement modes is CLOSED, and it matches what execution reads.

Probe B declared `maker_improve` and ran the control because the field was
never delivered. P0 closed that. This file closes the second route to the same
outcome: a placement name that IS delivered, faithfully, and that execution
does not implement.

`ws_continuous` branches on `self._placement == "maker_improve"`. Every other
string -- a typo of that one included -- falls through to the default. Such a
spec hashes differently, is reviewed and authorized under its own identity, and
passes the post-construction delivery check, because delivery was never the
problem. Only a closed set of names catches it.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import re

import pytest

from src.gemini_execution import live_probe_spec as LPS

ROOT = pathlib.Path(__file__).resolve().parents[1]
WSC = ROOT / "src/gemini_execution/ws_continuous.py"


def test_the_guard_is_on_the_type_not_only_the_registry():
    """`_declare` only ever sees this module's own specs."""
    spec = LPS.get("btc5m_q1_improve")
    with pytest.raises(ValueError) as e:
        dataclasses.replace(spec, placement="maker_improvee")
    assert "does not implement" in str(e.value)


@pytest.mark.parametrize("bad", ["maker_improvee", "MAKER_IMPROVE",
                                 "maker improve", "improve", "", "default"])
def test_a_near_miss_is_refused_rather_than_run_as_the_control(bad):
    spec = LPS.get("btc5m_q1_improve")
    with pytest.raises(ValueError):
        dataclasses.replace(spec, placement=bad)


def test_none_remains_legal_because_it_IS_the_control():
    spec = LPS.get("btc5m_q1_improve")
    ctl = dataclasses.replace(spec, placement=None)
    assert ctl.placement is None
    # and it is a different experiment, so it must hash differently
    assert ctl.spec_sha() != spec.spec_sha()


def test_the_declared_set_matches_what_execution_actually_compares_against():
    """Anti-drift. Adding a branch to ws_continuous without adding the name
    here would leave a mode that executes but cannot be declared; adding a
    name here without a branch there is the Probe B failure all over again."""
    src = WSC.read_text()
    compared = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "_placement"):
            continue
        for c in node.comparators:
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                compared.add(c.value)
    assert compared, "no `self._placement == <str>` comparison found at all"
    declared = {p for p in LPS.PLACEMENTS if p is not None}
    assert compared == declared, (
        "ws_continuous branches on %s but live_probe_spec declares %s"
        % (sorted(compared), sorted(declared)))


def test_the_refusal_names_the_declared_set():
    spec = LPS.get("btc5m_q1_improve")
    with pytest.raises(ValueError) as e:
        dataclasses.replace(spec, placement="nope")
    for mode in (p for p in LPS.PLACEMENTS if p is not None):
        assert mode in str(e.value)


def test_the_chain_proof_still_passes_end_to_end():
    """The proof is a gate, not a report: a broken seam exits non-zero."""
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "analysis/gemini/audit/p23_chain_proof.py"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CHAIN PROVEN" in r.stdout
