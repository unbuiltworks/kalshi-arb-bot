"""An ignored path must never be one the image copies.

Deployments d1447ee0 and 067f0b23 both failed fetching a 221 MB build
snapshot, so data/ (935 MB) and tests/ (42 MB) were excluded from it. That is
safe ONLY because nothing under them is copied into the image, and "only
because" is exactly the kind of fact that rots silently: a later COPY from an
ignored path does not fail the build loudly, it ships an image missing a file.

So the invariant is asserted instead of remembered.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/l5_pmus_live/Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def _ignored_dirs() -> set:
    out = set()
    for ln in DOCKERIGNORE.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or ln.startswith("!"):
            continue
        if ln.endswith("/"):
            out.add(ln.rstrip("/").lstrip("/"))
    return out


def _copy_sources() -> list:
    srcs = []
    for ln in DOCKERFILE.read_text().splitlines():
        m = re.match(r"\s*(?:COPY|ADD)\s+(?:--[^\s]+\s+)*(.+)$", ln)
        if not m:
            continue
        parts = m.group(1).split()
        # the last token is the destination
        srcs += parts[:-1] if len(parts) > 1 else parts
    return srcs


def test_the_dockerfile_copies_something_at_all():
    """A guard that passes because it found nothing is not a guard."""
    assert len(_copy_sources()) >= 50


def test_no_copied_path_lives_under_an_ignored_directory():
    ignored = _ignored_dirs()
    assert {"data", "tests"} <= ignored, ignored
    bad = [s for s in _copy_sources()
           if s.split("/")[0].lstrip("./") in ignored]
    assert not bad, (
        "these are COPYd but excluded from the build snapshot, so the image "
        "would be built without them: %s" % bad)


def test_the_directories_the_image_does_need_are_not_ignored():
    """analysis/ carries l5_action_model_v2.json and must survive."""
    ignored = _ignored_dirs()
    needed = {s.split("/")[0] for s in _copy_sources()}
    assert needed & ignored == set(), needed & ignored
    assert "analysis" in needed and "analysis" not in ignored
    assert "src" in needed and "config" in needed
