"""THE import-closure implementation. One copy, because three were the bug.

WHY THIS MODULE EXISTS (2026-09-28)

The Rung-A artifact `e48960f7` built cleanly, was reviewed against a "44-file
closure" that a shell script computed, was checked by a test helper that computed
its own closure, and then died in production with

    ImportError: cannot import name 'config' from 'src.polymarket_l5'

because BOTH implementations filtered on `ast.ImportFrom.level == 0` and so
skipped every `from . import x` in the repository. Two copies, one blind spot,
and they agreed with each other while disagreeing with Python. The COPY list was
generated from one of them and verified against the other, so "byte-identical"
was true and meaningless.

There is therefore exactly one implementation here, and every caller -- the
packaging guard, the Dockerfile equality test, the REPO_TESTS_001 classifier --
imports it. A blind spot found once is fixed once.

NOT imported by `scripts/l5_pmus_live_oneshot`, so this module is not itself in
the probe's capability graph; it is tooling about the graph, not part of it.

TWO DIFFERENT QUESTIONS, DELIBERATELY SEPARATE

`closure()` answers "what must be in the image", and for that a 0-byte
`__init__.py` absolutely counts: omit it and the import fails.

`behaviour_bearing()` answers "does this module do anything", and for that the
same 0-byte file counts for nothing. Conflating the two is what made
REPO_TESTS_001 overinclusive: every test that says `from src import anything`
transitively "reaches" `src/__init__.py`, which is EMPTY. 385 test files reached
the closure; the relationship for most of them was shared package ancestry
carrying no behaviour at all.
"""
from __future__ import annotations

import ast
import os
from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple


def _pkg_of(relpath: str) -> str:
    """Dotted package that CONTAINS this file."""
    return ".".join(p for p in os.path.dirname(relpath).split(os.sep) if p)


def imported_names(path: str, *, root: str = ".") -> Set[str]:
    """Every dotted name this file imports, absolute AND relative.

    `n.level` is resolved against the containing package. Both the module and
    each imported attribute are emitted, because `from p import m` may name a
    submodule rather than an attribute and only the filesystem can tell.
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, SyntaxError, ValueError):
        return set()
    rel = os.path.relpath(path, root)
    out: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = _pkg_of(rel).split(".")
                if n.level > 1:                       # `..x` and deeper
                    base = base[: len(base) - (n.level - 1)]
                mod = ".".join([b for b in base if b]
                               + ([n.module] if n.module else []))
            else:
                mod = n.module or ""
            if not mod:
                continue
            out.add(mod)
            for a in n.names:
                out.add(mod + "." + a.name)
    return out


def path_for(mod: str, *, root: str = ".") -> Optional[str]:
    """Local file backing a dotted name, module or package. None if external."""
    p = os.path.join(root, *mod.split("."))
    for cand in (p + ".py", os.path.join(p, "__init__.py")):
        if os.path.exists(cand):
            return os.path.relpath(cand, root)
    return None


def closure(entry: str, *, root: str = ".") -> Set[str]:
    """Every local file reachable by import from `entry`, including `entry`.

    This is the set that MUST be in the image. Package markers included: a
    missing 0-byte `__init__.py` fails the import just as surely as missing code.
    """
    seen, q = {entry}, deque([entry])
    while q:
        cur = q.popleft()
        for mod in imported_names(os.path.join(root, cur), root=root):
            p = path_for(mod, root=root)
            if p and p not in seen:
                seen.add(p)
                q.append(p)
    return seen


def module_name(relpath: str) -> str:
    m = relpath[:-3].replace(os.sep, ".") if relpath.endswith(".py") else relpath
    return m[: -len(".__init__")] if m.endswith(".__init__") else m


def behaviour_bearing(relpath: str, *, root: str = ".") -> bool:
    """Does this module define or execute ANYTHING?

    False for a pure package marker: empty, or only a docstring, `__all__`,
    `from __future__`, and imports. Such a file must be PACKAGED and can never
    be the reason a test is considered artifact-relevant, because it contains no
    behaviour for a test to exercise.
    """
    try:
        tree = ast.parse(open(os.path.join(root, relpath), encoding="utf-8").read())
    except (OSError, SyntaxError, ValueError):
        return True            # unreadable: assume it matters, never less safe
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue                                        # docstring
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            if all(isinstance(t, ast.Name) and t.id == "__all__"
                   for t in targets):
                continue                                    # re-export list
        return True
    return False


def behaviour_modules(files: Iterable[str], *, root: str = ".") -> Set[str]:
    """Dotted names of the closure files that actually carry behaviour."""
    return {module_name(f) for f in files if behaviour_bearing(f, root=root)}


def reaches(entry: str, targets: Set[str], *, root: str = ".") -> Optional[List[str]]:
    """Shortest import chain from `entry` to any dotted name in `targets`.

    Returns the chain as file paths ending in the matched dotted name, or None.
    The chain IS the proof: a classification that cannot show its path is an
    assertion, and assertions are what put an unrunnable image into production.
    """
    prev: Dict[str, Optional[str]] = {entry: None}
    q = deque([entry])
    while q:
        cur = q.popleft()
        for mod in sorted(imported_names(os.path.join(root, cur), root=root)):
            if mod in targets:
                chain, c = [mod], cur
                while c is not None:
                    chain.append(c)
                    c = prev[c]
                return list(reversed(chain))
            p = path_for(mod, root=root)
            if p and p not in prev:
                prev[p] = cur
                q.append(p)
    return None


def classify(test_file: str, packaged: Set[str], *, root: str = ".") -> Tuple[str, Optional[List[str]]]:
    """(verdict, proof) for one test file against a packaged closure.

    ARTIFACT_BEHAVIOUR    reaches a behaviour-bearing packaged module -> its
                          failure could describe packaged behaviour, so it is a
                          Rung-A blocker until shown otherwise.
    TEST_GRAPH_OVERINCLUSION
                          reaches the closure ONLY through package markers that
                          carry no behaviour. Repository debt, not a blocker.
    DISJOINT              no import path into the closure at all.
    """
    bearing = behaviour_modules(packaged, root=root)
    proof = reaches(test_file, bearing, root=root)
    if proof:
        return "ARTIFACT_BEHAVIOUR", proof
    allmods = {module_name(f) for f in packaged}
    proof = reaches(test_file, allmods, root=root)
    if proof:
        return "TEST_GRAPH_OVERINCLUSION", proof
    return "DISJOINT", None
