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
    """A no-POST image's safety is in what it omits.

    SUPERSEDED, and deliberately delegated rather than deleted. This file's
    `_closure()` imports the entry points and walks `sys.modules`, which cannot
    see an import written inside a function -- and `l5_live_book_feed` imports
    `pmus_ws_evidence_recorder` inside its connect coroutine, its `ingest` fold
    and its `_level_pair` parser. That blind spot is why the image failed on
    Railway four times, and it also makes this assertion WRONG in the dangerous
    direction: it calls a genuinely-required lazy module "unused" and invites
    someone to delete the COPY line again.

    The authoritative check is the AST closure in
    tests/test_shadow_image_packaging.py, which counts imports at any nesting
    depth. This test now asks that one, so the two can never disagree.
    """
    from src.l5_image_closure import closure_of
    from src import l5_image_manifest as M
    import tests.test_shadow_image_packaging as PK

    c = closure_of(PK.ENTRYPOINTS, exclude=tuple(PK.EXCLUDED))
    required = {m for m in c.first_party if m not in PK.EXCLUDED}
    copied = M.copied_paths(PK.DOCKERFILE)
    extra = []
    for src in sorted(copied):
        if not src.endswith(".py"):
            continue
        mod = src[:-3].replace(os.sep, ".")
        if mod.endswith(".__init__"):
            mod = mod[:-len(".__init__")]
        if mod not in required:
            extra.append(src)
    assert not extra, "copied but unused: %s" % extra


def test_the_runtime_walk_really_is_blind_to_lazy_imports():
    """Pins the reason the test above was superseded, so nobody re-derives it
    from four more Railway failures."""
    from src.l5_image_closure import closure_of
    import tests.test_shadow_image_packaging as PK

    runtime_seen = _closure()                       # sys.modules walk
    static = closure_of(PK.ENTRYPOINTS, exclude=tuple(PK.EXCLUDED))
    static_files = {m.split(".")[-1] + ".py" for m in static.first_party}
    invisible = sorted(static_files - runtime_seen)
    assert "pmus_ws_evidence_recorder.py" in invisible, (
        "the runtime walk now sees the lazy import; if that is genuinely true "
        "this test should be removed, but verify it rather than assuming it")


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
