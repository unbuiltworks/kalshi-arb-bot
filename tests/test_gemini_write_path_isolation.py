"""Exactly one Gemini package may mutate, and this proves it repo-wide.

The two pre-existing guards (test_gemini_subsidy_readonly, the shadow
service's image check) are FILE-scoped: each protects the one module it names.
Neither would notice a mutation path appearing in a collector, a reporter or a
new probe. This is the architectural complement -- it scans every Gemini module
in the repository and allows the mutation surface in `src/gemini_execution/`
alone.

Those two guards are deliberately left intact. A safety test is narrowed in the
open or not at all.
"""
import ast
import pathlib

import pytest

from src.gemini_execution import spec

#: the only package permitted to reach a mutation path
EXECUTION_PKG = "src/gemini_execution"

#: strings that constitute reaching the venue's mutation surface.
#: The WebSocket METHOD NAMES are in here too, as of 2026-09-18: that
#: transport is the one this account has actually placed orders through, so
#: leaving it unguarded would have protected the path that has never worked
#: while ignoring the one that has.
MUTATION_MARKERS = tuple(spec.MUTATION_PATHS) + tuple(
    spec.WS_MUTATION_METHODS) + (
    "/v1/order/new", "/v1/order/cancel", "order/new", "neworder")

#: the write-capable entry points
MUTATION_CALLS = ("place", "cancel", "cancel_and_confirm", "submit_once",
                  "submit_once_ws")


def _gemini_modules():
    roots = [pathlib.Path("src"), pathlib.Path("analysis"),
             pathlib.Path("scripts")]
    out = []
    for r in roots:
        if not r.exists():
            continue
        for p in r.rglob("*.py"):
            s = str(p)
            if "gemini" not in s.lower():
                continue
            if s.startswith(EXECUTION_PKG):
                continue
            out.append(p)
    return out


def test_there_are_gemini_modules_to_check():
    """A guard that scans nothing passes vacuously."""
    mods = _gemini_modules()
    assert len(mods) >= 8, [str(m) for m in mods]


def test_no_other_gemini_module_names_a_mutation_path():
    offenders = []
    for p in _gemini_modules():
        txt = p.read_text()
        for marker in MUTATION_MARKERS:
            if marker in txt:
                offenders.append("%s contains %r" % (p, marker))
    assert not offenders, offenders


def test_no_other_gemini_module_imports_the_execution_package():
    offenders = []
    for p in _gemini_modules():
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            mods = set()
            if isinstance(n, ast.Import):
                mods = {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods = {n.module}
            for m in mods:
                if m.startswith("src.gemini_execution"):
                    offenders.append("%s imports %s" % (p, m))
    assert not offenders, offenders


def test_the_named_read_only_modules_are_covered_by_the_scan():
    """Shadow, subsidy verification, settlement probe, collectors/reporters --
    the owner named these explicitly, so the scan must actually reach them."""
    scanned = {str(p) for p in _gemini_modules()}
    for required in ("src/gemini_l5_surface_shadow.py",
                     "src/gemini_maker/subsidy_verify.py",
                     "src/gemini_settlement_probe.py",
                     "src/gemini_settlement.py",
                     "src/gemini_canary_preflight.py",
                     "src/gemini_l5_shadow_v2.py"):
        assert required in scanned, required


def test_the_preexisting_guards_are_still_present():
    """Narrowed in the open, never quietly dropped."""
    for f in ("tests/test_gemini_subsidy_readonly.py",
              "tests/test_gemini_l5_shadow_service.py"):
        assert pathlib.Path(f).exists(), f


def test_the_execution_package_is_the_only_holder_of_the_paths():
    hits = []
    for p in pathlib.Path("src").rglob("*.py"):
        if spec.PLACE_PATH in p.read_text():
            hits.append(str(p))
    assert hits and all(h.startswith(EXECUTION_PKG) for h in hits), hits


def test_the_preflight_cannot_mutate_even_though_it_is_credentialed():
    """It reads the same account with the same signer and must stay a read."""
    src = pathlib.Path("src/gemini_canary_preflight.py").read_text()
    for marker in MUTATION_MARKERS:
        assert marker not in src
    assert "gemini_execution" not in src
