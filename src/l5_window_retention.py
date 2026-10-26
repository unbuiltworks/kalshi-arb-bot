"""Bound the collector's volume. THE ONLY MODULE HERE THAT DELETES ANYTHING.

WHY IT IS ITS OWN FILE. `l5_window_collector` is asserted to call no
destructive verb -- no unlink, rmtree, remove, truncate, rmdir, move or
copytree -- so the module that records evidence is structurally incapable
of removing it, and `verify_pre_cutover` can prove the pre-cutover history
unchanged byte for byte. That invariant is right and this module does not
weaken it: deletion lives out here, in the operational layer, where it is
one named function with its own tests.

WHAT IT REPAIRS. There was no retention of any kind. The collector wrote
one directory per 15-minute window, each with a verbatim frame tape, 96 a
day, forever. The 50 GB volume filled and from 2026-10-08 19:30Z to
2026-10-26 the collector raised

    OSError: [Errno 28] No space left on device

out of `WindowRecord.__init__`, the first statement of `collect_one`, every
five seconds -- so `new_l5_actions` was never reached and L5's wallet was
not polled once in 17 days, while the deployment reported SUCCESS.

WHAT IT REFUSES TO DO. Delete history it was not asked to delete. The count
bound is OFF by default, so the default prune frees only what the
free-space floor demands; the live window is protected by name; nothing
recorded in the pre-cutover manifest is ever touched; and a floor that
cannot be reached is REPORTED rather than satisfied by emptying the volume.
Deletion is irreversible, so the safe direction is to keep too much.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.l5_window_collector import (DEFAULT_FREE_FLOOR_MB,
                                     DEFAULT_RETAIN_WINDOWS, FREE_FLOOR_ENV,
                                     MIN_RETAIN_WINDOWS, PRE_CUTOVER,
                                     RETAIN_ENV, _float_env, _int_env,
                                     free_mb, window_dirs)


def manifest_protected(root: str) -> frozenset:
    """Paths the pre-cutover manifest recorded. Never prunable.

    `verify_pre_cutover` re-hashes every one of these and asserts they are
    unchanged. Deleting one would not just lose history, it would break the
    proof that the history was preserved.
    """
    import json
    p = Path(root) / PRE_CUTOVER
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        return frozenset()
    out = set()
    for f in (doc.get("files") or []):
        rel = str(f.get("path") or f.get("rel") or f.get("name") or "")
        if not rel:
            continue
        out.add(rel)
        parts = rel.split("/")
        # A MANIFESTED WINDOW IS PROTECTED BY ITS SLUG, not by "windows".
        # `inventory` hashes every file on the volume, so if the manifest
        # was taken while window directories already existed, those
        # directories are part of the preservation proof. Protecting only
        # the top-level name would leave each slug directory prunable and
        # silently break `verify_pre_cutover`.
        if parts[0] == "windows" and len(parts) > 1:
            out.add(parts[1])
        else:
            out.add(parts[0])
    return frozenset(out)


def _rmtree(p: Path) -> bool:
    try:
        shutil.rmtree(p)
        return True
    except OSError:
        return False


def prune_windows(root: str, *, retain: Optional[int] = None,
                  floor_mb: Optional[float] = None,
                  keep_slugs: Sequence[str] = ()) -> Dict[str, Any]:
    """Bound the volume. Returns a machine-readable report; never raises.

    Two passes, in this order. The COUNT pass drops whatever sits beyond the
    retained window count. The FLOOR pass then keeps dropping the oldest
    remaining window until free space clears the floor, and stops at
    MIN_RETAIN_WINDOWS whether or not it got there -- a floor it cannot
    reach by deleting is reported as `floor_unreachable`, because silently
    emptying the volume would destroy the evidence the collector exists for.

    `keep_slugs` is never pruned: the window being collected right now owns
    its directory and must not have it removed underneath the open tape.
    """
    retain = _int_env(RETAIN_ENV, DEFAULT_RETAIN_WINDOWS) if retain is None \
        else retain
    floor_mb = _float_env(FREE_FLOOR_ENV, DEFAULT_FREE_FLOOR_MB) \
        if floor_mb is None else floor_mb
    retain = int(retain)
    #: a count bound, once asked for, still never goes below the floor of
    #: windows we refuse to delete over a capacity fault
    if retain > 0:
        retain = max(retain, MIN_RETAIN_WINDOWS)
    protected = {str(s) for s in keep_slugs} | set(manifest_protected(root))
    rep: Dict[str, Any] = {
        "root": str(root), "retain_windows": retain,
        "count_bound_active": retain > 0,
        "free_floor_mb": round(float(floor_mb), 1),
        "free_mb_before": None, "free_mb_after": None,
        "windows_before": 0, "windows_after": 0,
        "pruned_count": 0, "pruned_for_count": 0, "pruned_for_floor": 0,
        "pruned_slugs": [], "failed_slugs": [],
        "floor_unreachable": False, "protected": sorted(protected)[:20],
        "protected_count": len(protected),
        "labels": ("retention is a BOUND on the volume, not a policy about "
                   "what is interesting. Oldest window first, the live "
                   "window never, and never below MIN_RETAIN_WINDOWS. With "
                   "count_bound_active false, windows are removed ONLY to "
                   "clear the free-space floor -- the least history that "
                   "lets collection continue. Deletion is irreversible: "
                   "export the volume before setting a count bound."),
    }
    try:
        rep["free_mb_before"] = free_mb(str(root))
        dirs = [p for p in window_dirs(root) if p.name not in protected]
        rep["windows_before"] = len(dirs) + len(
            [1 for p in window_dirs(root) if p.name in protected])

        # pass 1: the count bound, only if one was asked for
        excess = max(0, len(dirs) - retain) if retain > 0 else 0
        for p in dirs[:excess]:
            if _rmtree(p):
                rep["pruned_slugs"].append(p.name)
                rep["pruned_for_count"] += 1
            else:
                rep["failed_slugs"].append(p.name)
        dirs = dirs[excess:]

        # pass 2: the byte floor
        free = free_mb(str(root))
        while free is not None and free < floor_mb and len(dirs) > MIN_RETAIN_WINDOWS:
            p = dirs.pop(0)
            if _rmtree(p):
                rep["pruned_slugs"].append(p.name)
                rep["pruned_for_floor"] += 1
            else:
                rep["failed_slugs"].append(p.name)
            free = free_mb(str(root))
        if free is not None and free < floor_mb:
            rep["floor_unreachable"] = True

        rep["free_mb_after"] = free
        rep["windows_after"] = len(window_dirs(root))
        rep["pruned_count"] = (rep["pruned_for_count"]
                               + rep["pruned_for_floor"])
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        rep["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    for k in ("free_mb_before", "free_mb_after"):
        if isinstance(rep[k], float):
            rep[k] = round(rep[k], 1)
    return rep


