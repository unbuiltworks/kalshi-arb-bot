"""Read a Dockerfile's manifest: what it COPIES and what it INSTALLS.

Deliberately a parser, not an executor. The packaging test compares this
manifest against the static import closure, so a missing module is a test
failure on this machine instead of a `SHADOW_FEED_NOT_READY` a window at a time
on Railway.
"""
from __future__ import annotations

import os
import re
import shlex
from typing import Dict, List, Set, Tuple

#: distribution name -> the top-level module names it provides. Only the
#: distributions this repo's images actually install; an unknown distribution
#: is reported rather than guessed, because guessing is how a fail-closed test
#: turns into a fail-open one.
DIST_PROVIDES: Dict[str, Tuple[str, ...]] = {
    "websockets": ("websockets",),
    "cryptography": ("cryptography",),
    "requests": ("requests",),
    "urllib3": ("urllib3",),
    "pyyaml": ("yaml",),
    "python-dateutil": ("dateutil",),
    "numpy": ("numpy",),
    "pandas": ("pandas",),
    "scipy": ("scipy",),
    "scikit-learn": ("sklearn",),
    "pytz": ("pytz",),
}

_REQ = re.compile(r"^([A-Za-z0-9._-]+)")


def _lines(path: str) -> List[str]:
    out, buf = [], ""
    for raw in open(path, encoding="utf-8"):
        s = raw.rstrip("\n")
        if s.lstrip().startswith("#"):
            continue
        if s.rstrip().endswith("\\"):
            buf += s.rstrip()[:-1] + " "
            continue
        out.append((buf + s).strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [x for x in out if x]


def copied_paths(dockerfile: str) -> Set[str]:
    """Every source path the Dockerfile COPYs, normalised repo-relative."""
    got: Set[str] = set()
    for line in _lines(dockerfile):
        if not line.upper().startswith("COPY "):
            continue
        parts = shlex.split(line[5:])
        parts = [p for p in parts if not p.startswith("--")]
        if len(parts) < 2:
            continue
        for src in parts[:-1]:                 # last token is the destination
            got.add(os.path.normpath(src))
    return got


def installed_dists(dockerfile: str) -> Set[str]:
    """Distributions named on a `pip install` line."""
    got: Set[str] = set()
    for line in _lines(dockerfile):
        if "pip install" not in line:
            continue
        tail = line.split("pip install", 1)[1]
        for tok in shlex.split(tail):
            if tok.startswith("-"):
                continue
            m = _REQ.match(tok)
            if m:
                got.add(m.group(1).lower())
    return got


def provided_modules(dockerfile: str) -> Tuple[Set[str], Set[str]]:
    """(importable top-level names, distributions we do not have a mapping for)"""
    mods: Set[str] = set()
    unknown: Set[str] = set()
    for dist in installed_dists(dockerfile):
        prov = DIST_PROVIDES.get(dist)
        if prov is None:
            unknown.add(dist)
            continue
        mods.update(prov)
    return mods, unknown


def module_is_covered(module: str, copied: Set[str]) -> bool:
    """Does the COPY list actually place this module's file in the image?

    A module is covered by an explicit file COPY, or by a COPY of a directory
    that contains it. Package modules also need their `__init__.py`, which the
    caller checks separately by asking about the package itself.
    """
    rel = module.replace(".", os.sep)
    for cand in (rel + ".py", os.path.join(rel, "__init__.py")):
        if cand in copied:
            return True
        parts = cand.split(os.sep)
        for i in range(1, len(parts)):
            if os.sep.join(parts[:i]) in copied:
                return True
    return False
