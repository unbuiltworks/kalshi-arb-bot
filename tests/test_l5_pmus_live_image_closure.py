"""The CANONICAL EXECUTOR's image must contain the import closure it runs.

`deploy/l5_live_probe/Dockerfile` has had this test since the 21:36Z
ImportError chain. `deploy/l5_pmus_live/Dockerfile` -- the image
`l5-pmus-live` actually runs, under `restartPolicyType: ALWAYS` -- has not,
and it is the one that matters more: a missing module there is a crash loop on
a money-capable service, and the repository cannot catch it because with the
whole repo on `sys.path` every import resolves and the defect vanishes.

So this materialises exactly the Dockerfile's COPY paths into an empty tree
and imports the production entrypoint from THERE, in a subprocess with a
clean environment and the cwd inside the tree. Then it removes each file of
the resulting closure in turn and proves the check goes red: a packaging test
that cannot fail is not a packaging test.

It does not need a Docker daemon, which is the point -- the question is what
the image CONTAINS, and that is answerable from the Dockerfile and the
filesystem.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/l5_pmus_live/Dockerfile"

#: the entrypoint Railway runs: `startCommand: python3 -m src.l5_pmus_live_runner`
ENTRYPOINT = "src.l5_pmus_live_runner"

#: the modules the three ported safety fixes live in. Named explicitly so an
#: edit that drops one from the image is a failure with a history attached
#: rather than a silent loss of the fix in production.
PORTED_FIX_MODULES = (
    "src/l5_live_execution.py",          # aggregate cash cap
    "src/l5_pmus_live_runner.py",        # halt -> exact-order cancellation
    "src/l5_halt_gate.py",               # immutable latch under a bad read
    "src/l5_live_validation_gates.py",   # BOOK_UNCERTAIN
)

PROBE = r"""
import importlib, json, sys
sys.path.insert(0, ".")
m = importlib.import_module(%(entry)r)
# the three fixes must be REACHABLE from the entrypoint, not merely present
from src import l5_halt_gate as HG
from src import l5_live_execution as LX
from src import l5_live_validation_gates as VG
assert hasattr(HG, "_classify") and hasattr(HG, "HaltReadUnavailable")
assert hasattr(LX.FaithfulExecutor, "cash_legs_for_gates")
assert hasattr(LX.FaithfulExecutor, "outstanding_for_gates")
assert hasattr(m.Runner, "maybe_halt_deadman")
assert "book_uncertain" in VG.ValidationGates.refuse.__code__.co_varnames
# and the module-level objects the runner builds at import time
assert callable(m.halt_gate) and callable(m.validation_gates)
files = sorted(
    f for f in (getattr(sys.modules[k], "__file__", None)
                for k in list(sys.modules))
    if f and not f.startswith(sys.prefix) and "site-packages" not in f)
print("CLOSURE_OK " + json.dumps(files))
"""


def copy_specs() -> list:
    """(source, destination) for every COPY line in the executor Dockerfile."""
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        s = line.strip()
        if not s.startswith("COPY "):
            continue
        parts = s.split()[1:]
        if len(parts) != 2:
            raise AssertionError("unhandled COPY form: %s" % s)
        out.append((parts[0], parts[1]))
    return out


def materialize(tree: Path) -> None:
    for src, dst in copy_specs():
        s, d = ROOT / src, tree / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        elif s.exists():
            shutil.copy2(s, d)
        # a COPY of a path that does not exist would fail the build; that is
        # asserted separately so the message names the path.


def run_probe(tree: Path):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", PROBE % {"entry": ENTRYPOINT}],
        cwd=str(tree), env=env, capture_output=True, text=True, timeout=180)


@pytest.fixture(scope="module")
def image(tmp_path_factory) -> Path:
    tree = tmp_path_factory.mktemp("l5_pmus_live_image")
    materialize(tree)
    return tree


def test_every_copied_path_exists():
    """A COPY of a missing path fails the build at step N, not at runtime."""
    missing = [src for src, _ in copy_specs() if not (ROOT / src).exists()]
    assert not missing, (
        "deploy/l5_pmus_live/Dockerfile COPYs paths that do not exist: %s"
        % missing)


def test_the_ported_fixes_are_copied_into_the_image():
    copied = {src for src, _ in copy_specs()}
    missing = [m for m in PORTED_FIX_MODULES if m not in copied]
    assert not missing, (
        "the image does not carry %s, so the ported safety fix in it would "
        "not be running" % missing)


def test_the_image_imports_its_own_entrypoint_and_the_fixes_are_reachable(
        image):
    r = run_probe(image)
    assert r.returncode == 0, (
        "the executor image cannot import %s from its own file set:\n%s"
        % (ENTRYPOINT, r.stderr or r.stdout))
    assert "CLOSURE_OK " in r.stdout


def closure_files(image: Path) -> list:
    r = run_probe(image)
    assert r.returncode == 0, r.stderr
    line = [l for l in r.stdout.splitlines()
            if l.startswith("CLOSURE_OK ")][0]
    return json.loads(line[len("CLOSURE_OK "):])


def test_removing_any_closure_module_fails_the_check(image, tmp_path):
    """Leave-one-out. `__init__.py` is held out: Python 3 implicit namespace
    packages make a directory importable without one, so removing it does NOT
    break the import -- they are packaged for what they document. That is
    asserted rather than assumed."""
    files = closure_files(image)
    rel = sorted(Path(f).resolve().relative_to(image.resolve())
                 for f in files)
    checked = [p for p in rel if p.name != "__init__.py"]
    assert len(checked) >= 10, rel        # a closure this small is suspicious

    for victim in checked:
        tree = tmp_path / ("without_" + victim.name.replace(".py", ""))
        materialize(tree)
        (tree / victim).unlink()
        r = run_probe(tree)
        assert r.returncode != 0, (
            "removing %s from the image did not break the import, so this "
            "check is not actually exercising it" % victim)


def test_package_inits_are_still_packaged(image):
    inits = [p for p in (Path(f) for f in closure_files(image))
             if p.name == "__init__.py"]
    assert inits, "no package __init__ in the closure at all"


def test_every_lazily_imported_module_is_in_the_image():
    """THE GAP THAT SHIPPED A BROKEN AUDIT.

    This file materialises the COPY set and imports the entrypoint, which
    catches a missing module only if the import happens AT IMPORT TIME. The
    cost-basis audit is imported lazily, inside `if
    L5_COST_BASIS_AUDIT == YES`, so the module could be absent from the image
    and every test here still passed -- until the real boot raised
    ImportError: cannot import name 'l5_cost_basis_audit' from 'src' at
    2026-10-16T01:46:08Z, on a service that looked correctly wired.

    So the lazy imports are enumerated from the runner's own AST and each one
    is required to be in the image. A new `from src import X` inside any
    function of the runner now fails this test until it is packaged.
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    runner = root / "src" / "l5_pmus_live_runner.py"
    tree = ast.parse(runner.read_text())
    lazy = set()
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for n in ast.walk(fn):
            if isinstance(n, ast.ImportFrom) and n.module == "src":
                for a in n.names:
                    lazy.add(a.name)
    assert lazy, "the AST scan found no lazy imports -- it has stopped working"

    df = (root / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    copied = {ln.split()[1] for ln in df.splitlines()
              if ln.startswith("COPY ")}
    missing = sorted("src/%s.py" % m for m in lazy
                     if "src/%s.py" % m not in copied)
    assert not missing, "lazily imported but not in the image: %s" % missing
