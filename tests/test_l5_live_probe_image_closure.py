"""The live-probe image must contain the import closure it actually runs.

Observe-only at 21:36Z died one second after container start with

    ImportError: cannot import name 'pmus_l5_persistence_v2' from 'src'

and Probe 02, armed with real money, died of the identical error nine
minutes into its window without saying so. Both because
``l5_window_actions.new_l5_actions`` defers ``from src import
l5_pmus_realtime_observer`` to call time, the observer imports
``pmus_l5_persistence_v2`` and ``src.polymarket_l5.action_model`` at module
scope, and the Dockerfile copied the observer but not those.

A deferred import inside a rarely-exercised branch is invisible to every
test that runs against the repository working tree, because the working
tree has the whole repository in it. The only thing that can catch it is a
test that builds the image's file set and imports from *that*.

So: parse the real COPY lines out of the real Dockerfile, materialize
exactly those paths into an empty tree, and run the production import chain
there in a subprocess with a clean environment. Then remove each file of
the resulting closure in turn and prove the check goes red -- a packaging
test that cannot fail is not a packaging test.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/l5_live_probe/Dockerfile"

#: the window the failed observe-only run targeted, reused so the exercised
#: path is the one production took rather than a convenient synthetic one.
SLUG = "cpc-btc-updown-15m-2026-09-24-2130z"

#: proven necessary by the import graph on 2026-09-24: the observer imports
#: pv2 and src.polymarket_l5.action_model at module scope, and action_model
#: does `from . import config`, so the package init and config come with it.
#: Named explicitly so a future edit that drops one is a failure with a
#: history attached, not a mystery.
REQUIRED_DEPS = (
    "src/pmus_l5_persistence_v2.py",
    "src/polymarket_l5/__init__.py",
    "src/polymarket_l5/config.py",
    "src/polymarket_l5/action_model.py",
)


def copy_specs() -> list[tuple[str, str]]:
    """(source, destination) for every COPY line in the probe Dockerfile."""
    out: list[tuple[str, str]] = []
    for line in DOCKERFILE.read_text().splitlines():
        s = line.strip()
        if not s.startswith("COPY "):
            continue
        parts = s.split()[1:]
        if len(parts) != 2:            # --from, multi-source: not used here
            raise AssertionError("unhandled COPY form: %s" % s)
        out.append((parts[0], parts[1]))
    return out


def materialize(tree: Path) -> None:
    """Build the image's file set, and nothing else, under `tree`."""
    for src, dst in copy_specs():
        s, d = ROOT / src, tree / dst
        assert s.exists(), "Dockerfile COPYs a path that is not in the repo: %s" % src
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(s, d)


def image_modules() -> list[str]:
    """Importable module names for the .py files the Dockerfile copies."""
    mods = []
    for _, dst in copy_specs():
        p = Path(dst)
        if p.suffix != ".py" or p.name == "__init__.py":
            continue
        mods.append(".".join(p.with_suffix("").parts))
    return sorted(mods)


#: imports every module the image carries -- a module that ships but cannot
#: be imported is a latent version of this same defect -- then makes the
#: exact call that broke in production, with the feed injected so nothing
#: touches the network, and reports the closure it actually loaded.
PROBE = textwrap.dedent(
    """
    import importlib, json, sys

    for name in %(mods)r:
        importlib.import_module(name)

    from src import l5_window_actions as WA
    from src import l5_market_binding as MB
    ws = WA.window_start(%(slug)r)
    assert ws is not None
    # the identity gate admits nothing without an authoritative binding, and
    # this check must not reach the network, so the binding is injected. The
    # ADMISSION POLICY is tested in test_l5_market_identity.py; what is being
    # proved here is only that the image can import and run the path.
    TOK_UP, TOK_DOWN = "1111", "2222"
    WA._BINDINGS[int(ws)] = MB.Binding(
        window_start=int(ws), condition_id="0xabc",
        token_up=TOK_UP, token_down=TOK_DOWN, slug="x")
    row = {"timestamp": ws + 10, "side": "BUY", "outcome": "Up",
           "price": 0.42, "size": 30, "transactionHash": "0xdeadbeef",
           "asset": TOK_UP, "conditionId": "0xabc",
           "slug": "bitcoin-up-or-down", "title": "x"}
    acts, ok = WA.new_l5_actions(%(slug)r, set(), activity=lambda limit=100: [row])
    assert ok is True, "feed reported failure"
    assert len(acts) == 1, "row did not survive the shared window path"

    import os
    here = os.path.realpath(os.getcwd()) + os.sep
    loaded = sorted(
        os.path.realpath(m.__file__)[len(here):]
        for m in list(sys.modules.values())
        if getattr(m, "__file__", None)
        and os.path.realpath(m.__file__).startswith(here)
    )
    print("CLOSURE_OK " + json.dumps(loaded))
    """
)


def run_probe(tree: Path) -> subprocess.CompletedProcess:
    """Run the production import chain inside the image tree, isolated.

    Clean environment and cwd inside the tree: with the repository on
    sys.path the missing modules resolve and the defect vanishes, which is
    precisely how it reached production twice.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", PROBE % {"mods": image_modules(), "slug": SLUG}],
        cwd=str(tree), env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture(scope="module")
def image(tmp_path_factory) -> Path:
    tree = tmp_path_factory.mktemp("l5_live_probe_image")
    materialize(tree)
    return tree


def test_required_deps_are_copied_into_the_image():
    """The four modules the 21:36Z ImportError chain named are packaged."""
    copied = {src for src, _ in copy_specs()}
    missing = [d for d in REQUIRED_DEPS if d not in copied]
    assert not missing, (
        "deploy/l5_live_probe/Dockerfile no longer copies %s -- this is the "
        "defect that killed observe-only and Probe 02" % missing)


def test_image_import_closure_is_complete(image):
    """Every module in the image imports, and the L5 action path runs.

    Against the Dockerfile as it stood at 784faf1 this fails with the exact
    production error.
    """
    r = run_probe(image)
    assert r.returncode == 0, (
        "the live-probe image cannot run its own L5 action path:\n%s"
        % (r.stderr or r.stdout))
    assert "CLOSURE_OK " in r.stdout


def closure_files(image: Path) -> list[str]:
    r = run_probe(image)
    assert r.returncode == 0, r.stderr
    line = [l for l in r.stdout.splitlines() if l.startswith("CLOSURE_OK ")][0]
    return json.loads(line[len("CLOSURE_OK "):])


def test_removing_any_closure_module_fails_the_check(image, tmp_path):
    """Leave-one-out: the check is sensitive to every module it depends on.

    Without this, a packaging test that silently stopped exercising the
    chain would keep passing while the image rotted.

    `__init__.py` files are held out and checked separately: Python 3
    implicit namespace packages make a directory importable without one, so
    removing them does NOT break the import -- they are packaged for what
    they document, not because the runtime needs them. That is asserted
    rather than assumed, so if the behaviour ever changes the test says so.
    """
    work = tmp_path / "loo"
    shutil.copytree(image, work)
    files = closure_files(image)
    assert len(files) >= 20, "closure looks too small to be the real path: %d" % len(files)

    load_bearing = [f for f in files if not f.endswith("__init__.py")]
    package_inits = [f for f in files if f.endswith("__init__.py")]
    assert load_bearing, "no load-bearing modules in the closure"

    def survives(rel: str) -> bool:
        target = work / rel
        backup = target.read_bytes()
        target.unlink()
        try:
            return run_probe(work).returncode == 0
        finally:
            target.write_bytes(backup)

    survived = [rel for rel in load_bearing if survives(rel)]
    assert not survived, (
        "removing these from the image did not fail the check, so the check "
        "does not actually cover them: %s" % survived)

    load_bearing_inits = [rel for rel in package_inits if not survives(rel)]
    assert not load_bearing_inits, (
        "these package inits are load-bearing after all and must be moved "
        "into the covered set: %s" % load_bearing_inits)


def test_package_inits_are_still_packaged(image):
    """Namespace packages make these optional at runtime; ship them anyway."""
    for rel in ("src/__init__.py", "scripts/__init__.py",
                "src/polymarket_l5/__init__.py"):
        assert (image / rel).exists(), "image is missing %s" % rel
