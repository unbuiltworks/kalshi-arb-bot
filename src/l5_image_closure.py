"""Static import closure of a deployable entrypoint, including LAZY imports.

WHY THIS EXISTS. The no-POST shadow image has now failed four times in a row at
runtime on a module that was not in its COPY list, each failure discovered from
Railway logs a window at a time: `l5_authenticated_observation`, then the
missing `websockets` dependency, then `pmus_ws_evidence_recorder`. The guard
written after the first failure imported the entrypoints and walked
`sys.modules`, which cannot work: `l5_live_book_feed` imports
`pmus_ws_evidence_recorder` INSIDE its connect coroutine, so the import only
executes against a live socket and a runtime walk never reaches it.

So this module does not import anything it is measuring. It reads the source
with `ast` and walks every `Import`/`ImportFrom` node at ANY nesting depth --
module level, inside a function, inside `try`, inside a class, inside an `if` --
transitively, and reports:

  * every FIRST-PARTY module reachable from the entrypoint;
  * every THIRD-PARTY top-level package reachable from it;
  * where each was found, so a failure names a file and a line.

FAIL CLOSED. A file that cannot be parsed, an import that cannot be resolved to
either a first-party file or a known-stdlib name, is reported as UNRESOLVED and
the caller must treat that as a failure rather than an absence of findings.
"""
from __future__ import annotations

import ast
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

#: first-party top-level packages in this repository
FIRST_PARTY = ("src", "scripts", "tests")


def _module_to_path(mod: str, root: str) -> Optional[str]:
    rel = mod.replace(".", os.sep)
    for cand in (os.path.join(root, rel + ".py"),
                 os.path.join(root, rel, "__init__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def _is_first_party(mod: str) -> bool:
    return mod.split(".")[0] in FIRST_PARTY


def _stdlib() -> Set[str]:
    names = set(getattr(sys, "stdlib_module_names", ()))
    # a few that matter here and are not always listed
    names |= {"__future__", "typing", "dataclasses", "decimal", "collections"}
    return names


class Closure:
    """The transitive import closure of one or more entrypoints."""

    def __init__(self, root: str = ".", exclude=()) -> None:
        self.root = os.path.abspath(root)
        #: modules the image DELIBERATELY does not carry. The walk stops at
        #: them, so anything reachable only through one is not required either.
        #: An exclusion is a claim that the code path is never taken, and the
        #: packaging test must prove it rather than assume it.
        self.exclude = frozenset(exclude)
        self.stdlib = _stdlib()
        #: module -> list of (file, lineno, "lazy"|"module") sites importing it
        self.sites: Dict[str, List[Tuple[str, int, str]]] = {}
        self.first_party: Set[str] = set()
        self.third_party: Set[str] = set()
        self.unresolved: List[Tuple[str, str, int]] = []
        self.unparsed: List[Tuple[str, str]] = []
        self._seen: Set[str] = set()
        self.excluded_hit: Set[str] = set()

    # ------------------------------------------------------------- walking

    def _record(self, mod: str, where: str, line: int, depth: int) -> None:
        kind = "module" if depth == 0 else "lazy"
        self.sites.setdefault(mod, []).append((where, line, kind))

    def _imports_in(self, path: str) -> List[Tuple[str, int, int]]:
        """(module, lineno, nesting_depth). depth > 0 means a LAZY import."""
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            self.unparsed.append((path, "%s: %s" % (type(exc).__name__, exc)))
            return []
        found: List[Tuple[str, int, int]] = []

        def walk(node: ast.AST, depth: int) -> None:
            for child in ast.iter_child_nodes(node):
                d = depth
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef)):
                    d = depth + 1
                if isinstance(child, ast.Import):
                    for a in child.names:
                        found.append((a.name, child.lineno, depth))
                elif isinstance(child, ast.ImportFrom):
                    if child.level:
                        # relative import: resolve against this file's package
                        pkg = os.path.relpath(os.path.dirname(path), self.root)
                        pkg = pkg.replace(os.sep, ".")
                        base = pkg if child.level == 1 else ".".join(
                            pkg.split(".")[:-(child.level - 1)])
                        mod = "%s.%s" % (base, child.module) if child.module \
                            else base
                        found.append((mod, child.lineno, depth))
                        # `from . import config` -- level=1, module=None, and
                        # the NAME is the module. The sandbox layer caught this
                        # one after the static walk missed it; both branches
                        # now expand names identically.
                        if _is_first_party(mod):
                            for a in child.names:
                                found.append(("%s.%s" % (mod, a.name),
                                              child.lineno, depth))
                    else:
                        mod = child.module or ""
                        found.append((mod, child.lineno, depth))
                        # `from src import pmus_ws_evidence_recorder` -- the
                        # NAME is the module. This is the exact shape that
                        # broke the image, so it is resolved explicitly.
                        if _is_first_party(mod):
                            for a in child.names:
                                found.append(("%s.%s" % (mod, a.name),
                                              child.lineno, depth))
                walk(child, d)

        walk(tree, 0)
        return found

    def add_entrypoint(self, module: str) -> None:
        self._visit(module, root_call=True)

    def _visit(self, module: str, root_call: bool = False) -> None:
        if module in self._seen:
            return
        self._seen.add(module)
        if module in self.exclude and not root_call:
            self.excluded_hit.add(module)
            return
        path = _module_to_path(module, self.root)
        if path is None:
            if root_call:
                raise FileNotFoundError("entrypoint %r not found" % module)
            return
        self.first_party.add(module)
        # a package's __init__.py is part of the closure: without it the
        # subpackage is not importable at all. `scripts/__init__.py` was
        # already in the COPY list and would otherwise read as "copied but not
        # required", which is the wrong answer in the dangerous direction.
        parts = module.split(".")
        for i in range(1, len(parts)):
            pkg = ".".join(parts[:i])
            if _module_to_path(pkg, self.root):
                self.first_party.add(pkg)
        for mod, line, depth in self._imports_in(path):
            if not mod:
                continue
            top = mod.split(".")[0]
            if _is_first_party(mod):
                if _module_to_path(mod, self.root) is None:
                    # `from src import CONSTANT` resolves to a name, not a
                    # module; that is legitimate and not a missing file.
                    continue
                self._record(mod, path, line, depth)
                self._visit(mod)
            elif top in self.stdlib:
                continue
            elif top.startswith("_"):
                continue
            else:
                self.third_party.add(top)
                self._record(top, path, line, depth)

    # ------------------------------------------------------------- reporting

    def lazy_only(self) -> Dict[str, List[Tuple[str, int, str]]]:
        """Modules whose EVERY import site is nested -- the invisible ones."""
        return {m: s for m, s in self.sites.items()
                if s and all(k == "lazy" for _f, _l, k in s)}

    def as_dict(self) -> dict:
        return {"first_party": sorted(self.first_party),
                "excluded_hit": sorted(self.excluded_hit),
                "third_party": sorted(self.third_party),
                "lazy_only": sorted(self.lazy_only()),
                "unparsed": self.unparsed,
                "unresolved": self.unresolved}


def closure_of(entrypoints, root: str = ".", exclude=()) -> Closure:
    c = Closure(root, exclude=exclude)
    for ep in entrypoints:
        c.add_entrypoint(ep)
    return c
