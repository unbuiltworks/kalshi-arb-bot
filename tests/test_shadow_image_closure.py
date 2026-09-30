"""The shadow image's COPY list must match the real import closure.

WHY THIS EXISTS. Three images this session shipped without a module they
needed and crashed on ImportError at container start -- the shadow runner on
`l5_authenticated_observation`, and two earlier ones. A hand-written COPY
list is a second, silent copy of the dependency graph, and it drifts. This
computes the graph and compares.

It also fails on EXTRA entries, because a no-POST image's safety rests on
what it does NOT contain: an unnecessary module is either dead weight or a
capability nobody meant to ship.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

DOCKERFILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "deploy", "l5_shadow_signature", "Dockerfile")

#: modules the image must never contain, however the closure changes. These
#: are the ones that can build or send an order.
FORBIDDEN = ("pmus_native_money_path", "pmus_live_mandate",
             "l5_live_authorization", "l5_pmus_transport", "l5_order_body",
             "pmus_q1_executor")


def _copied() -> set:
    src = open(DOCKERFILE, encoding="utf-8").read()
    out = set()
    for m in re.finditer(r"^COPY\s+(\S+)\s+\S+\s*$", src, re.M):
        p = m.group(1)
        if p.startswith("src/") and p.endswith(".py"):
            out.add(os.path.basename(p))
    return out


_CACHE = None

#: computed in a SUBPROCESS, once. Doing it in-process gave an empty set on
#: the second call, because the modules were already in sys.modules from the
#: first -- so the "nothing unused" test passed vacuously while reporting
#: every module as unused. Measuring a fresh interpreter is the only way to
#: get the real closure more than once.
_PROBE = (
    "import sys, json; sys.path.insert(0, %r);"
    "b=set(sys.modules);"
    "import scripts.l5_r7_shadow_runner, scripts.r5_signature_extract;"
    "import src.l5_live_book_feed;"
    "print(json.dumps(sorted(m.split('.',1)[1]+'.py'"
    " for m in set(sys.modules)-b if m.startswith('src.') and m!='src')))"
)


def _closure() -> set:
    global _CACHE
    if _CACHE is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", _PROBE % root],
                             capture_output=True, text=True, cwd=root,
                             timeout=120)
        assert out.returncode == 0, out.stderr[-800:]
        _CACHE = set(json.loads(out.stdout.strip().splitlines()[-1]))
    return set(_CACHE)


def test_the_image_copies_every_module_the_entry_points_import():
    missing = sorted(_closure() - _copied())
    assert not missing, (
        "the image would crash at container start on: %s" % missing)


def test_the_image_copies_nothing_the_closure_does_not_need():
    """A no-POST image's safety is in what it omits."""
    extra = sorted(_copied() - _closure() - {"__init__.py"})
    assert not extra, "copied but unused: %s" % extra


def test_no_order_capable_module_is_ever_copied():
    copied = _copied()
    for bad in FORBIDDEN:
        assert bad + ".py" not in copied, (
            "%s would give this image the ability to create an order" % bad)


def test_the_closure_itself_contains_no_order_capable_module():
    """Belt and braces: even if the COPY list is right, an entry point that
    started importing the money path would make the image POST-capable."""
    closure = _closure()
    for bad in FORBIDDEN:
        assert bad + ".py" not in closure, (
            "an entry point now imports %s" % bad)
