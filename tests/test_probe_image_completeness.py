"""Every module the probe imports at boot must be IN the probe image.

THE DEFECT THIS EXISTS FOR. 565b4cde added

    from src import pmus_residual_quarantine as rq

at module scope in pmus_l5_continuous_session -- the probe's entry module --
without a matching COPY line. The suite stayed green, because the packaging
test only asserts COPY ⊆ watchPatterns and there was no COPY to check. An
image missing a boot-scope import does not degrade: the probe fails to import
and never starts at all, which on a money service is the worst possible
outcome of a "green" build.

There WAS a test named for this class, and it hardcoded two module names
instead of deriving them, so it passed while the exact defect it was written
for shipped. This one derives the set, transitively, from the entry module
named in the Dockerfile CMD. Adding an import is now enough to fail it;
nobody has to remember to update a list.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/pmus_spring_l5/Dockerfile"
RAILWAY = ROOT / "deploy/pmus_spring_l5/railway.json"


def _copied() -> set:
    """Module paths the image actually contains."""
    out = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            out.add(line.split()[1])
    return out


def _entry_modules(cmd_line: str = None) -> tuple:
    """EVERY module the CMD actually runs -- not the first one we happen to
    match, and not a module we assume it runs.

    The production CMD chains entrypoints:

        python3 -m src.pmus_r5ft_ledger_repair && exec python3 -m src.pmus_l5_continuous_session

    A single re.search returned only `pmus_r5ft_ledger_repair`, so the
    closure walked 4 modules and NEVER walked the session that actually
    trades. The completeness check was therefore live and green while
    checking almost nothing: a module dropped from the session's own import
    closure would not have been caught, which is the exact failure this file
    exists to prevent (565b4cde).

    Every `python3 -m src.<mod>` occurrence is an entrypoint whose imports
    must be present in the image, whatever shell plumbing (`&&`, `;`,
    `exec`, a `sh -c` wrapper) sits around it. Order-preserving and
    deduplicated; no module name is special-cased.
    """
    if cmd_line is None:
        cmd = [l for l in DOCKERFILE.read_text(encoding="utf-8").splitlines()
               if l.startswith("CMD ")]
        assert cmd, "no CMD in the probe Dockerfile"
        cmd_line = cmd[-1]
    found = re.findall(r"python3 -m (src\.[A-Za-z0-9_.]+)", cmd_line)
    assert found, f"could not read any entry module from: {cmd_line}"
    seen, out = set(), []
    for mod in found:
        if mod not in seen:
            seen.add(mod)
            out.append(mod)
    return tuple(out)


def _entry_closure(cmd_line: str = None) -> set:
    """The UNION of the import closures of every chained entrypoint.

    Union, not concatenation: a module reachable from both entrypoints is
    required once, and a module reachable from only the second is still
    required.
    """
    union: set = set()
    for mod in _entry_modules(cmd_line):
        union |= _closure(mod)
    return union


_SCOPED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _module_scope_src_imports(path: Path) -> set:
    """`from src import X` / `import src.X` that runs AT IMPORT TIME.

    Module scope is what executes when the process starts, and therefore what
    must exist for it to boot. A top-level `try:` or `if:` still counts -- it
    runs. A function or class body does NOT: it runs only when called, which
    is a different (also real, see below) failure mode.

    Getting this wrong once already: an earlier version walked each top-level
    node with ast.walk, which descends INTO function bodies, and flagged
    pmus_l5_persistence_v2's offline `build_artifact` importing
    action_model_v2 as if it were a boot dependency. The image was fine; the
    walk was not. A check that cries wolf about packaging is worse than none,
    because the next real one gets waved through."""
    def collect(nodes, out):
        for node in nodes:
            if isinstance(node, _SCOPED):
                continue                        # deferred until called
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "src":
                    out.update(a.name for a in node.names)
                elif mod.startswith("src."):
                    out.add(mod[4:])
                continue
            if isinstance(node, ast.Import):
                out.update(a.name[4:] for a in node.names
                           if a.name.startswith("src."))
                continue
            for field in ("body", "orelse", "finalbody", "handlers"):
                child = getattr(node, field, None)
                if isinstance(child, list):
                    collect(child, out)
        return out

    return collect(ast.parse(path.read_text(encoding="utf-8")).body, set())


def _closure(entry: str) -> set:
    """Every src module reachable from `entry` through module-scope imports."""
    start = entry[4:] if entry.startswith("src.") else entry
    seen, stack = set(), [start]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = ROOT / "src" / (mod.replace(".", "/") + ".py")
        if not path.exists():                   # package dirs handled below
            path = ROOT / "src" / mod.replace(".", "/") / "__init__.py"
        if not path.exists():
            continue
        stack.extend(_module_scope_src_imports(path))
    return seen


def _missing_from(copied: set) -> list:
    """Boot-scope modules absent from a given copy set."""
    missing = []
    for mod in sorted(_entry_closure()):
        candidates = {f"src/{mod.replace('.', '/')}.py",
                      f"src/{mod.replace('.', '/')}/__init__.py"}
        if candidates & copied:
            continue
        src = ROOT / "src" / (mod.replace(".", "/") + ".py")
        pkg = ROOT / "src" / mod.replace(".", "/") / "__init__.py"
        if src.exists() or pkg.exists():
            missing.append(mod)
    return missing


def test_every_boot_scope_import_is_in_the_image():
    """The check 565b4cde needed and did not have."""
    missing = _missing_from(_copied())
    assert not missing, (
        "imported at boot but absent from the probe image -- the probe will "
        f"fail to import and never start: {missing}")


def test_the_entry_modules_are_read_from_the_cmd_not_assumed():
    """Every chained entrypoint, and each one present in the image."""
    mods = _entry_modules()
    assert "src.pmus_l5_continuous_session" in mods, (
        "the module that actually trades must be an entrypoint the check walks")
    for mod in mods:
        assert f"src/{mod[4:].replace('.', '/')}.py" in _copied(), mod


def test_the_closure_is_transitive_not_just_direct():
    """pmus_residual_quarantine is reached directly; the session's own
    dependencies are reached through it. A one-level check would miss the
    second kind."""
    closure = _entry_closure()
    assert "pmus_residual_quarantine" in closure
    assert "pmus_spring_l5_session" in closure
    assert len(closure) > 10, "a one-level scan would be much smaller"


def test_this_check_would_have_failed_at_565b4cde():
    """Proof the check BITES, not just that it passes today.

    Reconstruct the exact 565b4cde condition -- the boot-scope import present,
    the COPY line absent -- and require the check to name the module. A
    packaging test that has never been shown to fail is indistinguishable
    from one that cannot."""
    assert _missing_from(_copied()) == [], "precondition: the image is whole"
    without = _copied() - {"src/pmus_residual_quarantine.py"}
    assert _missing_from(without) == ["pmus_residual_quarantine"]


@pytest.mark.parametrize("drop", [
    "src/pmus_spring_l5_session.py",
    "src/pmus_migration_close_live.py",
    "src/pmus_settlement_auditor.py",
    "src/pmus_fill_ledger.py",
])
def test_dropping_any_boot_module_is_caught(drop):
    """Not just the one that bit us: every boot-scope module is load-bearing
    and removing any of them must be named."""
    assert drop in _copied(), f"{drop} is no longer copied"
    missing = _missing_from(_copied() - {drop})
    assert missing, f"removing {drop} was not detected"


def test_copied_modules_are_also_watched():
    """Kept alongside the import check: 3b8d00b4 added the COPY and went red
    here, because a file in the image that nothing watches never rebuilds."""
    import json
    patterns = json.loads(RAILWAY.read_text(encoding="utf-8"))["build"][
        "watchPatterns"]
    import fnmatch
    for path in sorted(_copied()):
        assert any(fnmatch.fnmatch(path, p) or
                   (p.endswith("/**") and path.startswith(p[:-2]))
                   for p in patterns), f"{path} is built but never watched"


def test_runtime_only_imports_are_reported_not_silently_trusted():
    """Imports inside functions do not break boot, but they DO break the
    feature the moment it runs. They are listed here so the next person sees
    them rather than discovering one live."""
    session = ROOT / "src/pmus_l5_continuous_session.py"
    tree = ast.parse(session.read_text(encoding="utf-8"))
    top = {id(n) for n in tree.body}
    deferred = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "src" \
                and id(node) not in top:
            deferred.update(a.name for a in node.names)
    copied = _copied()
    unpackaged = sorted(
        m for m in deferred
        if f"src/{m}.py" not in copied and (ROOT / f"src/{m}.py").exists())
    assert not unpackaged, (
        "imported at RUNTIME but absent from the image -- the probe boots and "
        f"then fails the moment this path executes: {unpackaged}")


# ===========================================================================
# OWNER 2026-09-13: the parser must understand the ACTUAL production start
# command, including EVERY chained `python3 -m` entrypoint. A gate that walks
# only pmus_r5ft_ledger_repair and ignores pmus_l5_continuous_session is not
# a gate. These pin the parser itself, with synthetic commands so no real
# module name is special-cased.
# ===========================================================================
SINGLE = 'CMD ["sh", "-c", "python3 -m src.alpha"]'
CHAINED = ('CMD ["sh", "-c", "test x = y || exit 64; '
           'python3 -m src.alpha && exec python3 -m src.beta"]')


def test_A_a_single_entry_command_behaves_exactly_as_before():
    assert _entry_modules(SINGLE) == ("src.alpha",)


def test_B_a_chained_repair_and_exec_session_finds_both(): 
    assert _entry_modules(CHAINED) == ("src.alpha", "src.beta")


def test_B2_the_real_production_command_yields_both_entrypoints():
    mods = _entry_modules()
    assert len(mods) >= 2, f"production CMD chains entrypoints: {mods}"
    assert "src.pmus_l5_continuous_session" in mods
    assert "src.pmus_r5ft_ledger_repair" in mods


def test_C_a_dependency_reachable_only_from_the_second_entry_is_required(tmp_path):
    """The regression the old parser could not express: the session's own
    closure. pmus_spring_l5_session is reachable ONLY through
    pmus_l5_continuous_session, never through the repair module."""
    repair_only = _closure("src.pmus_r5ft_ledger_repair")
    assert "pmus_spring_l5_session" not in repair_only, (
        "precondition: the first entrypoint does not reach it")
    assert "pmus_spring_l5_session" in _entry_closure(), (
        "the union must require it")


def test_D_omitting_a_second_entry_dependency_makes_the_check_FAIL():
    """The assertion must BITE, not merely pass today."""
    only_second = sorted(_closure("src.pmus_l5_continuous_session")
                         - _closure("src.pmus_r5ft_ledger_repair"))
    assert only_second, "precondition: the second entry adds dependencies"
    victim = only_second[0]
    crippled = {c for c in _copied()
                if c != f"src/{victim.replace('.', '/')}.py"}
    assert victim in _missing_from(crippled), (
        f"dropping {victim} -- reachable only from the second entrypoint -- "
        "must be caught")


def test_E_shared_dependencies_are_deduplicated():
    a, b = _closure("src.pmus_r5ft_ledger_repair"), _closure("src.pmus_l5_continuous_session")
    shared = a & b
    assert shared, "precondition: the entrypoints share dependencies"
    union = _entry_closure()
    assert union == a | b
    assert len(union) < len(a) + len(b), "a concatenation would over-count"


def test_E2_a_repeated_entry_module_is_listed_once():
    assert _entry_modules(
        'CMD ["sh", "-c", "python3 -m src.alpha && python3 -m src.alpha"]'
    ) == ("src.alpha",)


@pytest.mark.parametrize("cmd", [
    'CMD ["sh", "-c", "exec python3 -m src.alpha"]',
    'CMD ["sh", "-c", "cd /app; python3 -m src.alpha"]',
    'CMD ["sh", "-c", "guard || exit 64; python3 -m src.alpha && exec python3 -m src.beta"]',
    'CMD ["sh", "-c", "sed -i s/a/b/ f.py && python3 -m src.alpha && exec python3 -m src.beta"]',
])
def test_F_shell_wrappers_and_exec_do_not_prevent_extraction(cmd):
    mods = _entry_modules(cmd)
    assert "src.alpha" in mods
    assert mods[0] == "src.alpha", "order preserved"


def test_G_the_production_command_passes_only_with_the_full_closure():
    """Green now -- and provably for the right reason: every module in the
    UNION is in the image, and removing any one of them fails."""
    assert _missing_from(_copied()) == []
    union = sorted(_entry_closure())
    assert len(union) > 10
    for mod in union[:5]:                       # bounded: proof it bites
        crippled = {c for c in _copied()
                    if c != f"src/{mod.replace('.', '/')}.py"}
        if crippled != _copied():               # only if it was in the set
            assert mod in _missing_from(crippled), mod
