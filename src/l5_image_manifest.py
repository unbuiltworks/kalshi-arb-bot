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


class UnsupportedCopy(Exception):
    """A COPY/ADD form whose effect on the image layout we cannot determine.

    Raised rather than ignored. The whole point of this manifest is to be a
    gate, and a form we silently skip is a form that ships broken.
    """


def image_roots(dockerfile: str) -> Tuple[List[str], str]:
    """(import roots, working directory) as the image will actually have them.

    Roots are every entry of PYTHONPATH -- it is colon-separated and treating it
    as one literal path dropped legitimate destinations -- falling back to the
    working directory when PYTHONPATH is unset. Both ENV spellings are read
    (`ENV K=V` and the legacy `ENV K V`), and a relative WORKDIR is resolved
    cumulatively against the previous one, as Docker does.
    """
    workdir, pypath = "/", None
    for line in _lines(dockerfile):
        instr = _instruction(line)
        rest = line.split(None, 1)
        args = rest[1] if len(rest) > 1 else ""
        if instr == "WORKDIR":
            got = shlex.split(args)
            if got:
                workdir = got[0] if os.path.isabs(got[0]) \
                    else os.path.normpath(os.path.join(workdir, got[0]))
        elif instr == "ENV":
            toks = shlex.split(args)
            if len(toks) == 2 and "=" not in toks[0]:
                if toks[0] == "PYTHONPATH":       # legacy `ENV KEY value`
                    pypath = toks[1]
            else:
                for tok in toks:
                    if tok.startswith("PYTHONPATH="):
                        pypath = tok.split("=", 1)[1]
    roots = [r for r in (pypath or workdir).split(":") if r]
    return (roots or ["/"]), workdir


def image_root(dockerfile: str) -> str:
    """The FIRST import root. Kept for callers that want one path."""
    return image_roots(dockerfile)[0][0]


def _instruction(line: str) -> str:
    """The Dockerfile instruction, upper-cased.

    Docker's parser splits the instruction from its arguments on ANY
    whitespace, so `ADD\tfoo bar` is a valid ADD. Testing
    `line.upper().startswith("ADD ")` -- a literal space -- missed it, and a
    tab-separated ADD therefore slipped the whole gate: an independent review
    reproduced shipping `src/pmus_q1_executor.py`, the order path, with every
    layer green including the no-POST assertion. Split, do not prefix-match.
    """
    parts = line.split(None, 1)
    return parts[0].upper() if parts else ""


def copy_specs(dockerfile: str):
    """[(source, destination)] for every COPY, verbatim.

    ADD and multi-stage `COPY --from=` are REFUSED, not skipped: ADD has
    unpacking semantics we do not model, and a `--from` source lives in a build
    stage that is not this repository, so nothing on disk can stand in for it.
    """
    out = []
    for line in _lines(dockerfile):
        instr = _instruction(line)
        if instr == "ADD":
            raise UnsupportedCopy("ADD is not modelled: %s" % line)
        if instr != "COPY":
            continue
        rest = line.split(None, 1)
        parts = shlex.split(rest[1]) if len(rest) > 1 else []
        for flag in [p for p in parts if p.startswith("--")]:
            # case-insensitive: buildkit rejects a wrong-case flag at build
            # time, but a parser that reads `--FROM=` as an unknown flag and
            # strips it would treat a build-stage COPY as a repo COPY.
            if flag.upper().startswith("--FROM"):
                raise UnsupportedCopy(
                    "COPY --from names a build stage, not this repo: %s" % line)
        parts = [p for p in parts if not p.startswith("--")]
        if len(parts) < 2:
            raise UnsupportedCopy("COPY with no destination: %s" % line)
        dst = parts[-1]
        for src in parts[:-1]:
            out.append((os.path.normpath(src), dst))
    return out


def image_paths(dockerfile: str) -> Set[str]:
    """Where each COPY actually PUTS its source, relative to the import root.

    THE HOLE THIS CLOSES. The first version of this module returned only the
    SOURCE paths and discarded the destination, and the sandbox then
    reconstructed the REPOSITORY layout rather than the IMAGE layout. So
    `COPY src/l5_live_book_feed.py /opt/wrong/l5_live_book_feed.py` passed
    every offline layer while the deployed image died on
    `ModuleNotFoundError: src.l5_live_book_feed` -- exactly the class of
    failure this file exists to prevent. Reviewed, reproduced, closed.

    A destination that lands outside the import root, or that renames the file
    so its path no longer matches its module path, yields a path that simply
    does not cover the module -- so the coverage check fails, which is the
    fail-closed direction.
    """
    roots, workdir = image_roots(dockerfile)
    out: Set[str] = set()
    for src, dst in copy_specs(dockerfile):
        is_dir_dst = dst.endswith("/") or dst in (".", "./")
        if os.path.isdir(src):
            # A DIRECTORY SOURCE COPIES ITS CONTENTS, NOT ITSELF. Keeping the
            # source basename modelled `COPY src/polymarket_l5 src/` as landing
            # `src/polymarket_l5/...` when Docker actually lands `config.py` at
            # `src/config.py` -- so the gate read the package as covered while
            # the image died on ModuleNotFoundError. Found by review, fail-open,
            # now expanded file by file.
            base_dst = dst if is_dir_dst else dst + "/"
            for dirpath, _dirs, files in os.walk(src):
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    inner = os.path.relpath(full, src)
                    out.update(_place(os.path.join(base_dst, inner),
                                      roots, workdir))
            continue
        if is_dir_dst:
            placed = os.path.join(dst, os.path.basename(src.rstrip("/")))
        else:
            placed = dst
        out.update(_place(placed, roots, workdir))
    return out


def _place(placed: str, roots: List[str], workdir: str) -> Set[str]:
    """Where `placed` ends up relative to whichever import root contains it.

    A relative destination resolves against WORKDIR -- not against the import
    root. Assuming those were the same was fail-open whenever PYTHONPATH and
    WORKDIR differ: the file landed under WORKDIR, off sys.path, while the gate
    read it as covered.
    """
    placed = os.path.normpath(placed)
    if not os.path.isabs(placed):
        placed = os.path.normpath(os.path.join(workdir, placed))
    got: Set[str] = set()
    for root in roots:
        r = root.rstrip("/") or "/"
        if placed == r or placed.startswith(r + "/") or r == "/":
            got.add(os.path.normpath(os.path.relpath(placed, r)))
    return got


def copied_paths(dockerfile: str) -> Set[str]:
    """Deprecated name kept for callers that only need the source list.

    Prefer `image_paths`, which accounts for the destination. This one cannot
    see a renaming or misplacing COPY.
    """
    return {src for src, _dst in copy_specs(dockerfile)}


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
