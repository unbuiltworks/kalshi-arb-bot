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


def _entry_module() -> str:
    """The module the CMD actually runs -- not a module we assume it runs."""
    cmd = [l for l in DOCKERFILE.read_text(encoding="utf-8").splitlines()
           if l.startswith("CMD ")]
    assert cmd, "no CMD in the probe Dockerfile"
    m = re.search(r"python3 -m (src\.[A-Za-z0-9_.]+)", cmd[-1])
    assert m, f"could not read the entry module from: {cmd[-1]}"
    return m.group(1)


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
    for mod in sorted(_closure(_entry_module())):
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


def test_the_entry_module_is_read_from_the_cmd_not_assumed():
    assert _entry_module() == "src.pmus_l5_continuous_session"
    assert "src/pmus_l5_continuous_session.py" in _copied()


def test_the_closure_is_transitive_not_just_direct():
    """pmus_residual_quarantine is reached directly; the session's own
    dependencies are reached through it. A one-level check would miss the
    second kind."""
    closure = _closure(_entry_module())
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
