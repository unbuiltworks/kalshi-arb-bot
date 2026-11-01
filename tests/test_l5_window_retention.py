"""`prune_windows` — the only module in the observer that deletes anything.

Ported to this branch with the retention fix. The broader wallet-ingestion
suite it originally lived in depends on runner changes that are deliberately
NOT part of this port, so the retention contract is pinned here on its own.

WHAT MUST HOLD, all four of them safety properties rather than behaviour:
the pre-cutover manifest is never touched; the live window is never removed
from under its open tape; the floor never prunes below MIN_RETAIN_WINDOWS;
and a floor that cannot be reached is REPORTED, not satisfied by emptying
the volume.
"""
from __future__ import annotations

import json
import os

from src import l5_window_collector as C
from src import l5_window_retention as RET


def _mk(root, slug, mb=1):
    d = os.path.join(root, "windows", slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "frames.jsonl"), "wb") as fh:
        fh.write(b"x" * (mb * 1024))
    return d


#: 48 windows = 12 hours, deliberately MORE than MIN_RETAIN_WINDOWS (24) so
#: the floor is a real constraint in these tests rather than vacuously true.
SLUGS = ["cpc-btc-updown-15m-2026-10-25-%02d%02dz" % (h, m)
         for h in range(12) for m in (0, 15, 30, 45)]


def test_no_count_bound_by_default_so_nothing_is_pruned_for_age(tmp_path):
    """DEFAULT_RETAIN_WINDOWS is 0: the default prune frees only what the
    free-space floor demands, never windows merely for being old."""
    assert C.DEFAULT_RETAIN_WINDOWS == 0
    for s in SLUGS:
        _mk(str(tmp_path), s)
    r = RET.prune_windows(str(tmp_path), floor_mb=0.0)
    assert r["count_bound_active"] is False
    assert r["pruned_count"] == 0
    assert r["pruned_for_count"] == 0


def test_the_live_window_is_never_pruned(tmp_path):
    for s in SLUGS:
        _mk(str(tmp_path), s)
    live = SLUGS[0]                      # the OLDEST, so age would pick it
    r = RET.prune_windows(str(tmp_path), retain=24, keep_slugs=[live],
                          floor_mb=0.0)
    assert live not in r["pruned_slugs"]
    assert live in r["protected"]
    assert os.path.isdir(os.path.join(str(tmp_path), "windows", live))


def test_the_pre_cutover_manifest_is_never_touched(tmp_path):
    for s in SLUGS:
        _mk(str(tmp_path), s)
    keep = SLUGS[1]
    # the manifest records FILES, and a manifested window is protected by
    # its slug -- see manifest_protected's comment on verify_pre_cutover.
    with open(os.path.join(str(tmp_path), C.PRE_CUTOVER), "w") as fh:
        json.dump({"files": [
            {"path": "windows/%s/frames.jsonl" % keep}]}, fh)
    prot = RET.manifest_protected(str(tmp_path))
    assert keep in prot
    r = RET.prune_windows(str(tmp_path), retain=24, floor_mb=0.0)
    assert keep not in r["pruned_slugs"]
    assert os.path.isdir(os.path.join(str(tmp_path), "windows", keep))


def test_a_count_bound_is_floored_at_min_retain_windows(tmp_path):
    """Asking to keep 2 windows keeps MIN_RETAIN_WINDOWS instead."""
    for s in SLUGS:
        _mk(str(tmp_path), s)
    r = RET.prune_windows(str(tmp_path), retain=2, floor_mb=0.0)
    assert r["retain_windows"] == C.MIN_RETAIN_WINDOWS
    assert r["windows_after"] >= C.MIN_RETAIN_WINDOWS


def test_oldest_first(tmp_path):
    for s in SLUGS:
        _mk(str(tmp_path), s)
    r = RET.prune_windows(str(tmp_path), retain=24, floor_mb=0.0)
    if r["pruned_slugs"]:
        assert r["pruned_slugs"] == sorted(r["pruned_slugs"])
        assert r["pruned_slugs"][0] == SLUGS[0]


def test_an_unreachable_floor_is_reported_not_satisfied(tmp_path):
    """A floor larger than the disk must not empty the volume."""
    for s in SLUGS:
        _mk(str(tmp_path), s)
    r = RET.prune_windows(str(tmp_path), floor_mb=1e12)
    assert r["floor_unreachable"] is True
    assert r["windows_after"] >= C.MIN_RETAIN_WINDOWS


def test_the_report_carries_what_an_operator_needs(tmp_path):
    for s in SLUGS:
        _mk(str(tmp_path), s)
    r = RET.prune_windows(str(tmp_path), floor_mb=0.0)
    for k in ("free_mb_before", "free_mb_after", "windows_before",
              "windows_after", "pruned_slugs", "pruned_for_count",
              "pruned_for_floor", "protected_count", "floor_unreachable"):
        assert k in r, k


def test_prune_never_raises(tmp_path):
    """Retention is housekeeping and must never be why a window goes
    uncollected."""
    r = RET.prune_windows(os.path.join(str(tmp_path), "does-not-exist"))
    assert isinstance(r, dict)


def test_the_collector_module_calls_no_destructive_verb():
    """The structural invariant that makes this safe: the recorder cannot
    remove evidence, so deletion is auditable in one file."""
    import inspect
    src = inspect.getsource(C)
    for verb in ("shutil.rmtree", "os.remove", "os.unlink", ".unlink(",
                 "os.rmdir", "shutil.move", "shutil.copytree"):
        assert verb not in src, verb
