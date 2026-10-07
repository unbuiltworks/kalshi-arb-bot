"""THE TEST FOR THE DEFECT THAT KEEPS REACHING RAILWAY: a short COPY list.

Three deploys of this service have now failed on a module that the
entrypoint imports and the Dockerfile does not COPY. The most recent was
`src/l5_market_binding.py`: it entered the import closure when
`authorization.py` began importing `selector.py`, which happened AFTER the
COPY list was computed by hand. 153 tests were green. The image could not
import its own entrypoint.

The COPY list is a hand-maintained restatement of a fact the source tree
already knows, so it drifts silently every time an import is added. This
test recomputes the TRANSITIVE AST IMPORT CLOSURE of the production
entrypoint and asserts the Dockerfile covers every file in it -- so the
drift fails the SUITE, in seconds, locally, instead of failing a build
minutes before a window.

It deliberately does NOT assert the converse (that every COPY is in the
closure). An over-broad COPY wastes image layers; it cannot crash the
container, and `analysis/` directories are copied wholesale on purpose.

Run:
    PYTHONPATH=. python3 -m pytest analysis/l5/e1_cohort/test_dockerfile_closure.py -q
"""
from __future__ import annotations

import ast
import os

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DOCKERFILE = os.path.join(REPO_ROOT, "deploy", "l5_e1_cohort", "Dockerfile")
ENTRYPOINT = "scripts/l5_e1_cohort_run.py"
FIRST_PARTY = ("src", "analysis", "scripts")


def _module_to_relpath(mod: str) -> str | None:
    """Resolve a dotted module to a repo-relative file, or None if not ours."""
    base = mod.replace(".", "/")
    for candidate in (base + ".py", base + "/__init__.py"):
        if os.path.isfile(os.path.join(REPO_ROOT, candidate)):
            return candidate
    return None


def _imports_of(relpath: str) -> set[str]:
    """Every dotted name `relpath` imports, including `from x import y` as x.y.

    `from pkg import name` is ambiguous between a submodule and an attribute,
    so both `pkg` and `pkg.name` are emitted and resolution decides.
    """
    with open(os.path.join(REPO_ROOT, relpath)) as fh:
        tree = ast.parse(fh.read(), relpath)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                package = os.path.dirname(relpath).replace("/", ".")
                found.add(package)
                for alias in node.names:
                    found.add(package + "." + alias.name)
            else:
                module = node.module or ""
                found.add(module)
                for alias in node.names:
                    found.add(module + "." + alias.name)
    return found


def import_closure() -> set[str]:
    """First-party files reachable from the entrypoint by real AST imports."""
    seen: set[str] = set()
    stack = [ENTRYPOINT]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for module in _imports_of(current):
            if not module.startswith(FIRST_PARTY):
                continue
            resolved = _module_to_relpath(module)
            if resolved is not None and resolved not in seen:
                stack.append(resolved)
    return seen


def copied_prefixes() -> list[str]:
    """Source paths the Dockerfile COPYs, as prefixes (a dir covers its tree)."""
    prefixes: list[str] = []
    with open(DOCKERFILE) as fh:
        for line in fh:
            line = line.strip()
            if not line.upper().startswith("COPY "):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            prefixes.append(parts[1])
    return prefixes


def _is_covered(relpath: str, prefixes: list[str]) -> bool:
    for prefix in prefixes:
        if prefix.endswith("/"):
            if relpath.startswith(prefix):
                return True
        elif relpath == prefix:
            return True
    return False


def test_entrypoint_resolves():
    """Guard the guard: a renamed entrypoint must not silently empty the test."""
    assert os.path.isfile(os.path.join(REPO_ROOT, ENTRYPOINT))
    closure = import_closure()
    # The closure is large and crosses three top-level packages. If a future
    # refactor collapses it to a handful of files this test would pass
    # vacuously, so require it to still look like the real dependency graph.
    assert len(closure) >= 30, sorted(closure)
    assert any(p.startswith("src/") for p in closure)
    assert any(p.startswith("analysis/") for p in closure)


def test_dockerfile_copies_every_imported_module():
    """Every file the armed entrypoint imports must be in the image."""
    closure = import_closure()
    prefixes = copied_prefixes()
    missing = sorted(p for p in closure if not _is_covered(p, prefixes))
    assert not missing, (
        "deploy/l5_e1_cohort/Dockerfile does not COPY these imported modules, "
        "so the image cannot import its own entrypoint: " + ", ".join(missing)
    )


def test_the_pmus_identity_and_book_modules_are_in_the_image():
    """The PMUS-native replacement for the market-binding regression.

    `src/l5_market_binding.py` was the module whose absence failed
    deployment d05d30b4, and it is a POLYMARKET.COM module: E1 no longer
    imports it, so pinning it here would pin a dependency the production
    path does not have. The modules that must now be present are the PMUS
    ones the decision path actually imports.
    """
    closure = import_closure()
    prefixes = copied_prefixes()
    for mod in ("src/l5_pmus_transport.py",        # identity / resolution
                "src/l5_pmus_auth_book.py",        # the decision feed
                "src/pmus_auth.py"):               # signing
        assert mod in closure, mod
        assert _is_covered(mod, prefixes), mod
    # and the Polymarket.com venue modules are GONE from the closure
    for gone in ("src/l5_market_binding.py", "src/l5_clob_ws.py",
                 "src/l5_book_shadow.py"):
        assert gone not in closure, gone


def test_namespace_packages_are_created():
    """Directories COPYed without __init__.py need the touch step."""
    with open(DOCKERFILE) as fh:
        body = fh.read()
    for pkg in ("scripts/__init__.py", "analysis/__init__.py", "analysis/l5/__init__.py"):
        assert pkg in body, pkg
