"""The observer image must contain every module its entrypoint can import.

WHY THIS TEST EXISTS. `scripts/l5_window_collector_run.py` gained
`from src import l5_window_retention as RET` at module level, and the
collector's Dockerfile was not updated. A module-level import of a file the
image does not COPY does not degrade -- the container dies at startup with
ModuleNotFoundError, so the retention fix that was supposed to restore
collection would instead have stopped it completely.

The executor image has had `test_the_image_copies_every_module_the_entrypoint_
can_reach` for exactly this reason and it caught the same class of defect on
`l5_promotion_gate`. The observer image had no equivalent. This is it.
"""
from __future__ import annotations

import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, "deploy", "l5_window_collector", "Dockerfile")
ENTRY = os.path.join(ROOT, "scripts", "l5_window_collector_run.py")


def _copied() -> set:
    with open(DOCKERFILE) as fh:
        return {ln.split()[1] for ln in fh if ln.startswith("COPY ")}


def _src_imports(path: str) -> set:
    """`src.X` modules imported by this file, at any level."""
    with open(path) as fh:
        tree = ast.parse(fh.read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "src":
            for a in node.names:
                out.add("src/%s.py" % a.name)
        elif isinstance(node, ast.ImportFrom) and \
                (node.module or "").startswith("src."):
            out.add("src/%s.py" % (node.module.split(".", 1)[1]))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("src."):
                    out.add("src/%s.py" % a.name.split(".", 1)[1])
    return out


def _closure() -> set:
    """Transitively, every src module the entrypoint can reach."""
    seen, todo = set(), [ENTRY]
    while todo:
        f = todo.pop()
        for m in _src_imports(f):
            if m in seen:
                continue
            seen.add(m)
            p = os.path.join(ROOT, m)
            if os.path.exists(p):
                todo.append(p)
    return seen


def test_the_retention_module_is_copied_into_the_image():
    """The specific regression: a module-level import with no COPY."""
    assert "src/l5_window_retention.py" in _copied()


def test_the_entrypoint_imports_the_retention_module_at_module_level():
    """Pins WHY the COPY is load-bearing. If this import ever becomes lazy
    the COPY is still correct, but the failure mode changes and this test
    should be re-read rather than deleted."""
    with open(ENTRY) as fh:
        head = fh.read().split("def ")[0]
    assert re.search(r"^from src import l5_window_retention", head, re.M)


def test_the_image_copies_every_module_the_entrypoint_can_reach():
    missing = sorted(m for m in _closure()
                     if os.path.exists(os.path.join(ROOT, m))
                     and m not in _copied())
    assert missing == [], missing


def test_the_collector_itself_is_copied():
    assert "src/l5_window_collector.py" in _copied()
