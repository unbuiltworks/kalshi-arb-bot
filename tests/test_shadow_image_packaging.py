"""FAIL-CLOSED offline packaging test for the no-POST shadow image.

WHY. This image has now failed at runtime four separate times on something it
did not carry, each failure found in Railway logs one 15-minute window at a
time: `l5_authenticated_observation`, then `websockets`, then
`pmus_ws_evidence_recorder`. The previous guard imported the entrypoints and
walked `sys.modules`, which structurally cannot work -- `l5_live_book_feed`
imports `pmus_ws_evidence_recorder` inside its connect coroutine, its `ingest`
fold and its `_level` parser, so those imports only execute against a live
socket and no runtime walk reaches them.

WHAT THIS DOES INSTEAD, all offline and with no market connection:

  layer 1  static: recursive AST closure from the entrypoint, counting imports
           at ANY nesting depth, compared against the Dockerfile's COPY list.
  layer 2  third-party: every non-stdlib top-level package in the closure must
           be provided by a distribution the Dockerfile pip-installs.
  layer 3  sandbox: materialise a directory holding EXACTLY the files the
           Dockerfile copies, then in a subprocess with a scrubbed sys.path
           import the entrypoint and every required module. This is the real
           question -- "is the closure importable from the image contents
           alone" -- answered without docker and without a socket.
  layer 4  lazy sites: call the functions that hold the lazy imports inside
           that sandbox, so an import that only runs on the hot path is
           exercised here instead of on Railway.
  layer 5  no-POST: assert the order path is absent from the image AND that
           importing/driving the shadow never pulls it in.
  layer 6  docker, only if a daemon is reachable: build the image and repeat
           layer 3 inside it. SKIPS LOUDLY when there is no daemon; it never
           passes by default.

FAIL CLOSED: an unparsable file, an unresolvable import, an unrecognised
distribution, or an undeclared exclusion is a FAILURE, not an absence of
findings.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from src import l5_image_manifest as M
from src.l5_image_closure import closure_of

DOCKERFILE = "deploy/l5_shadow_signature/Dockerfile"
ENTRYPOINTS = ("scripts.l5_r7_shadow_runner",
               "scripts.r5_signature_extract")

#: Modules the image deliberately does not carry, each with the reason it is
#: safe. An exclusion is a CLAIM, and layer 5 proves it.
EXCLUDED = {
    "src.pmus_q1_executor":
        "the order path. Reached only through l5_venue_adapter._q1(), which "
        "that module documents as a path a read-only recorder image must never "
        "take. Carrying it would end the no-POST guarantee.",
    "src.polymarket_l5.transfer_backtest":
        "offline research code. Reached only through l5_venue_adapter._tb().",
}


def _closure():
    return closure_of(ENTRYPOINTS, exclude=tuple(EXCLUDED))


def _required(c):
    return sorted(m for m in c.first_party if m not in EXCLUDED)


# ------------------------------------------------------------------- layer 1

def test_the_closure_is_fully_resolvable():
    c = _closure()
    assert not c.unparsed, "unparsable source in the closure: %s" % (c.unparsed,)
    assert not c.unresolved, "unresolved imports: %s" % (c.unresolved,)
    assert len(c.first_party) > 10, "closure looks truncated: %s" % (
        sorted(c.first_party),)


def test_the_closure_sees_lazy_imports_a_sys_modules_walk_cannot():
    """Regression on the blind spot itself. `pmus_ws_evidence_recorder` is
    imported only inside functions; if the walker stops seeing it, the next
    image ships without it again."""
    c = _closure()
    lazy = c.lazy_only()
    assert "src.pmus_ws_evidence_recorder" in lazy, (
        "the walker no longer sees the import that broke the image")
    sites = [l for _f, l, k in lazy["src.pmus_ws_evidence_recorder"]
             if k == "lazy"]
    assert len(sites) >= 3, "expected several nested sites, got %s" % (sites,)
    assert "websockets" in lazy, "the websockets import is nested too"


def test_every_required_module_is_copied_into_the_image():
    c = _closure()
    copied = M.image_paths(DOCKERFILE)
    missing = [m for m in _required(c) if not M.module_is_covered(m, copied)]
    assert not missing, (
        "the image would crash at runtime on: %s" % ", ".join(missing))


def test_the_copy_list_carries_nothing_the_closure_does_not_need():
    """Not cosmetic: every extra module is surface area in an image whose
    security property is what it cannot do."""
    c = _closure()
    req = set(_required(c))
    copied = M.image_paths(DOCKERFILE)
    extra = []
    for src in sorted(copied):
        if not src.endswith(".py"):
            continue
        mod = src[:-3].replace(os.sep, ".")
        if mod.endswith(".__init__"):
            mod = mod[:-len(".__init__")]
        if mod not in req:
            extra.append(src)
    assert not extra, "copied but not required: %s" % (extra,)


def test_the_copy_destination_is_honoured_not_assumed():
    """THE FAIL-OPEN AN INDEPENDENT REVIEW FOUND AND I REPRODUCED.

    `copied_paths` returned only the SOURCE of each COPY, so
    `COPY src/l5_live_book_feed.py /opt/wrong/l5_live_book_feed.py` passed every
    offline layer while the deployed image died on
    `ModuleNotFoundError: src.l5_live_book_feed`. The manifest now resolves each
    COPY to where the file actually lands relative to the import root.
    """
    import tempfile as _tf
    src_df = open(DOCKERFILE, encoding="utf-8").read()
    for bad_dst in ("/opt/wrong/l5_live_book_feed.py", "./", "src/renamed.py"):
        mutated = src_df.replace(
            "COPY src/l5_live_book_feed.py src/l5_live_book_feed.py",
            "COPY src/l5_live_book_feed.py " + bad_dst)
        assert mutated != src_df, "the anchor COPY line moved"
        with _tf.NamedTemporaryFile("w", suffix=".Dockerfile",
                                    delete=False) as fh:
            fh.write(mutated)
            path = fh.name
        try:
            placed = M.image_paths(path)
            assert "src/l5_live_book_feed.py" not in placed, (
                "destination %r still read as covering the module" % bad_dst)
        finally:
            os.unlink(path)


def test_unmodellable_copy_forms_are_refused_not_skipped():
    """A form we silently skip is a form that ships broken."""
    import tempfile as _tf
    for line in ("ADD src/l5_live_book_feed.py src/l5_live_book_feed.py",
                 "COPY --from=builder src/ /app/src/",
                 "COPY src/l5_live_book_feed.py"):
        with _tf.NamedTemporaryFile("w", suffix=".Dockerfile",
                                    delete=False) as fh:
            fh.write("FROM python:3.11-slim\nWORKDIR /app\n" + line + "\n")
            path = fh.name
        try:
            with pytest.raises(M.UnsupportedCopy):
                M.copy_specs(path)
        finally:
            os.unlink(path)


def test_the_import_root_is_read_from_the_image_not_assumed():
    assert M.image_root(DOCKERFILE) == "/app"


# ------------------------------------------------------------------- layer 2

def test_every_third_party_import_is_installed():
    c = _closure()
    provided, unknown = M.provided_modules(DOCKERFILE)
    assert not unknown, (
        "distribution with no known import name, so coverage cannot be "
        "established: %s" % (sorted(unknown),))
    missing = sorted(t for t in c.third_party if t not in provided)
    assert not missing, (
        "the feed imports these and the image installs nothing that provides "
        "them: %s" % missing)


def test_the_dockerfile_installs_something_at_all():
    assert M.installed_dists(DOCKERFILE), (
        "no pip install line: the first fixed build failed exactly this way")


# --------------------------------------------------------- the image sandbox

def _materialise(dest: str) -> None:
    """Build the IMAGE's layout, not the repository's.

    The destination is what decides where a file lands, so the sandbox has to
    honour it: a COPY that renames or misplaces a module must produce a sandbox
    in which that module does not import, exactly as in the real image.
    """
    root = M.image_root(DOCKERFILE)
    for src, dst in M.copy_specs(DOCKERFILE):
        if dst.endswith("/") or dst in (".", "./"):
            placed = os.path.join(dst, os.path.basename(src.rstrip("/")))
        else:
            placed = dst
        placed = os.path.normpath(placed)
        if os.path.isabs(placed):
            if not (placed == root
                    or placed.startswith(root.rstrip("/") + "/")):
                continue          # outside the import root; unreachable anyway
            placed = os.path.relpath(placed, root)
        s = os.path.abspath(src)
        d = os.path.join(dest, placed)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        elif os.path.isfile(s):
            shutil.copy2(s, d)
        else:
            raise AssertionError("COPY names a path that does not exist: %s"
                                 % src)
    del root


PROBE = r'''
import json, os, sys, types
sys.path.insert(0, sys.argv[3])
mods = json.loads(sys.argv[1])
excluded = json.loads(sys.argv[2])
failed = {}
for m in mods:
    try:
        __import__(m)
    except BaseException as exc:
        failed[m] = "%s: %s" % (type(exc).__name__, exc)

# LAYER 4: run the lazy import sites. No socket is opened: these are the pure
# parser/fold helpers that hold the nested imports.
lazy = {}
try:
    from src import l5_live_book_feed as F
    F._level_pair({"px": {"value": "0.41"}, "qty": "10"})
    F._level_pair(["0.41", "10"])
except BaseException as exc:
    lazy["l5_live_book_feed._level_pair"] = "%s: %s" % (type(exc).__name__, exc)
try:
    from src import l5_live_book_feed as F
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-30-0000z")
    f.ingest({"channel": "book", "data": {}})
except BaseException as exc:
    name = type(exc).__name__
    if name in ("ImportError", "ModuleNotFoundError"):
        lazy["l5_live_book_feed.ingest"] = "%s: %s" % (name, exc)
try:
    from src import l5_window_actions as WA
    WA.current_slug(1_760_000_000.0)
except BaseException as exc:
    lazy["l5_window_actions.current_slug"] = "%s: %s" % (
        type(exc).__name__, exc)

leaked = sorted(m for m in excluded if m in sys.modules)
print("RESULT " + json.dumps({"failed": failed, "lazy": lazy,
                              "leaked": leaked}))
'''


def _run_sandbox(root: str, mods, excluded):
    probe = os.path.join(root, "_probe.py")
    open(probe, "w").write(PROBE)
    env = dict(os.environ)
    env["PYTHONPATH"] = root
    env.pop("PYTHONHOME", None)
    # the image has no credentials; the probe must not need any
    for k in ("PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY"):
        env.pop(k, None)
    out = subprocess.run(
        [sys.executable, "-E", "-s", probe, json.dumps(list(mods)),
         json.dumps(list(excluded)), root],
        cwd=root, env=env, capture_output=True, text=True, timeout=300)
    line = [l for l in out.stdout.splitlines() if l.startswith("RESULT ")]
    assert line, "probe produced no result.\nstdout:%s\nstderr:%s" % (
        out.stdout[-3000:], out.stderr[-3000:])
    return json.loads(line[-1][len("RESULT "):])


@pytest.fixture(scope="module")
def sandbox():
    c = _closure()
    with tempfile.TemporaryDirectory() as tmp:
        _materialise(tmp)
        yield tmp, c, _run_sandbox(tmp, _required(c), sorted(EXCLUDED))


# ------------------------------------------------------------------- layer 3

def test_every_required_module_imports_from_the_image_contents_alone(sandbox):
    _tmp, _c, res = sandbox
    assert not res["failed"], (
        "modules in the image cannot import with only the image present:\n"
        + "\n".join("  %s -> %s" % kv for kv in sorted(res["failed"].items())))


# ------------------------------------------------------------------- layer 4

def test_the_lazy_import_sites_execute_inside_the_image(sandbox):
    """The exact failure mode: an import that only runs on the hot path."""
    _tmp, _c, res = sandbox
    assert not res["lazy"], (
        "a lazy import site fails inside the image -- this is the "
        "SHADOW_FEED_NOT_READY class of failure:\n"
        + "\n".join("  %s -> %s" % kv for kv in sorted(res["lazy"].items())))


# ------------------------------------------------------------------- layer 5

def test_the_order_path_is_absent_from_the_image():
    copied = M.image_paths(DOCKERFILE)
    for mod in EXCLUDED:
        assert not M.module_is_covered(mod, copied), (
            "%s is in the image; the no-POST guarantee is that the code to "
            "place an order does not exist there" % mod)


def test_the_shadow_code_path_never_imports_the_order_path(sandbox):
    _tmp, _c, res = sandbox
    assert not res["leaked"], (
        "importing and driving the shadow pulled in: %s" % (res["leaked"],))


def test_every_exclusion_is_declared_with_a_reason():
    c = _closure()
    for mod in sorted(c.excluded_hit):
        assert EXCLUDED.get(mod), "undeclared exclusion: %s" % mod
    for mod, why in EXCLUDED.items():
        assert why and len(why) > 30, "exclusion %s needs a real reason" % mod


# ------------------------------------------------------------------- layer 6

def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=60).returncode == 0
    except BaseException:
        return False


@pytest.mark.skipif(not _docker_available(),
                    reason="no reachable docker daemon in this container; "
                           "layers 1-5 still prove the closure offline and "
                           "this layer is the belt-and-braces build check")
def test_the_built_image_contains_the_whole_closure():
    c = _closure()
    tag = "l5-shadow-packaging-test:local"
    build = subprocess.run(
        ["docker", "build", "-f", DOCKERFILE, "-t", tag, "."],
        capture_output=True, text=True, timeout=1800)
    assert build.returncode == 0, build.stderr[-4000:]
    script = "import importlib,sys\n" + "".join(
        "importlib.import_module(%r)\n" % m for m in _required(c))
    script += "print('OK')\n"
    run = subprocess.run(["docker", "run", "--rm", "-e", "PYTHONPATH=/app",
                          tag, "python3", "-c", script],
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0 and "OK" in run.stdout, (
        "the built image cannot import its own closure:\n%s\n%s"
        % (run.stdout[-2000:], run.stderr[-3000:]))
