#!/usr/bin/env python3
"""VOLUME RETENTION FOR THE RESEARCH STATE ROOT.

WHY THIS EXISTS. Eight recorders write JSONL to one volume at 1-5 second
intervals, every one of them opening a NEW timestamped segment on every
start, and nothing ever pruned them. The volume reached 100% and the
supervisor died with `OSError: [Errno 28] No space left on device` -- which
is the worst possible failure for an evidence recorder, because it is silent
until the moment everything stops at once.

WHAT IT DOES, AND WHAT IT REFUSES TO DO. It COMPRESSES closed segments and
it does not delete evidence. gzip on this data is lossless and typically
recovers an order of magnitude, so the cheapest fix keeps every byte:
`x.jsonl` becomes `x.jsonl.gz`, and the original is unlinked ONLY after the
archive has been read back and verified byte-for-byte against it. A
verification failure leaves both files in place and reports the failure.

DELETION IS OFF BY DEFAULT AND CANNOT BE TURNED ON BY PRESSURE. `sweep`
takes `delete_after_days`, and when it is None -- the default, and what the
supervisor passes -- nothing is ever unlinked except a verified source whose
archive exists. Running out of space does not unlock deletion; it widens
compression, which is a different thing. A recorder is allowed to fail
closed. It is not allowed to destroy the record to keep running.

THE ACTIVE SEGMENT IS NEVER TOUCHED. Every recorder holds its newest segment
open and appends to it; compressing a file out from under an open descriptor
loses the rows written after the copy. The newest file in each directory is
skipped, and so is anything modified within `min_age_s`.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Sequence

#: fractions of capacity at which the supervisor should start saying so
THRESHOLDS = ((0.70, "NOTICE"), (0.85, "WARN"), (0.95, "CRITICAL"))

#: a segment must be this old before it is considered closed
DEFAULT_MIN_AGE_S = 2 * 3600.0

#: suffixes that hold evidence worth compressing
COMPRESSIBLE = (".jsonl", ".json", ".log", ".ndjson")

#: never compress or delete these, whatever their age
PROTECTED = frozenset({"coverage.jsonl"})

LABELS = ("gzip is LOSSLESS: every byte of evidence is retained and an "
          "archive is verified against its source before the source is "
          "unlinked. Nothing here deletes evidence to free space.")


def level(pct: float) -> str:
    """OK / NOTICE / WARN / CRITICAL for a used fraction."""
    out = "OK"
    for at, name in THRESHOLDS:
        if pct >= at:
            out = name
    return out


def usage(root: str) -> Dict[str, Any]:
    """Capacity, used and free for the filesystem holding `root`."""
    try:
        t, u, f = shutil.disk_usage(root)
    except OSError as exc:                                       # noqa: BLE001
        return {"readable": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    pct = (u / t) if t else 0.0
    return {"readable": True, "total_bytes": t, "used_bytes": u,
            "free_bytes": f, "used_pct": round(pct, 4),
            "total_gb": round(t / 1e9, 2), "used_gb": round(u / 1e9, 2),
            "free_gb": round(f / 1e9, 2), "level": level(pct)}


def _sha(path: str, opener=open) -> str:
    h = hashlib.sha256()
    with opener(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def candidates(root: str, *, now: Optional[float] = None,
               min_age_s: float = DEFAULT_MIN_AGE_S) -> List[Dict[str, Any]]:
    """Closed segments eligible for compression, largest first.

    The newest file in EVERY directory is excluded, because that is the one
    its recorder still has open.
    """
    now = time.time() if now is None else now
    out: List[Dict[str, Any]] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        newest_name, newest_mtime = None, -1.0
        rows = []
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime > newest_mtime:
                newest_name, newest_mtime = fn, st.st_mtime
            if fn in PROTECTED or fn.endswith(".gz"):
                continue
            if not fn.endswith(COMPRESSIBLE):
                continue
            if st.st_size == 0:
                # THE OUTAGE LEFT DOZENS OF THESE. Every crash-restart while
                # the volume was full opened a new segment that could never
                # be written. They cost nothing, they are the record of the
                # restart loop, and gzipping an empty file would turn that
                # record into clutter without freeing a byte.
                continue
            rows.append({"path": p, "name": fn, "dir": dirpath,
                         "bytes": st.st_size, "mtime": st.st_mtime,
                         "age_s": now - st.st_mtime})
        for r in rows:
            if r["name"] == newest_name:
                r["skipped"] = "newest in its directory: its recorder is "\
                               "still appending to it"
                continue
            if r["age_s"] < min_age_s:
                r["skipped"] = "modified %.0fs ago, younger than the %.0fs "\
                               "closed-segment age" % (r["age_s"], min_age_s)
                continue
            out.append(r)
    out.sort(key=lambda r: r["bytes"], reverse=True)
    return out


def compress(path: str, *, level_: int = 6) -> Dict[str, Any]:
    """gzip one file and VERIFY before unlinking the source.

    The source is removed only when the archive decompresses to exactly the
    same sha256. Anything else leaves both files and says why.
    """
    gz = path + ".gz"
    before = os.path.getsize(path)
    src_sha = _sha(path)
    try:
        with open(path, "rb") as fin, gzip.open(gz, "wb",
                                                compresslevel=level_) as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
    except OSError as exc:                                       # noqa: BLE001
        # out of space mid-archive is the likely one; leave nothing behind
        try:
            os.unlink(gz)
        except OSError:
            pass
        return {"ok": False, "path": path, "why": "%s: %s"
                % (type(exc).__name__, exc), "source_kept": True}
    try:
        back = _sha(gz, opener=gzip.open)
    except OSError as exc:                                       # noqa: BLE001
        return {"ok": False, "path": path, "archive": gz,
                "why": "archive unreadable: %s" % (exc,), "source_kept": True}
    if back != src_sha:
        return {"ok": False, "path": path, "archive": gz,
                "why": "archive sha %s != source sha %s" % (back[:12],
                                                            src_sha[:12]),
                "source_kept": True}
    after = os.path.getsize(gz)
    os.unlink(path)
    return {"ok": True, "path": path, "archive": gz, "sha256": src_sha,
            "bytes_before": before, "bytes_after": after,
            "bytes_freed": before - after,
            "ratio": (round(before / after, 2) if after else None),
            "source_kept": False}


def sweep(root: str, *, now: Optional[float] = None,
          min_age_s: float = DEFAULT_MIN_AGE_S,
          max_files: int = 200,
          delete_after_days: Optional[float] = None) -> Dict[str, Any]:
    """Compress closed segments, newest-largest first, with accounting.

    `delete_after_days` is None by default and the supervisor never sets it.
    When it IS set, only archives (`.gz`) older than that are unlinked, and
    never an un-archived source.
    """
    now = time.time() if now is None else now
    before = usage(root)
    cands = candidates(root, now=now, min_age_s=min_age_s)
    done: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    freed = 0
    for c in cands[:max_files]:
        r = compress(c["path"])
        (done if r["ok"] else failed).append(r)
        if r["ok"]:
            freed += r["bytes_freed"]
    deleted: List[Dict[str, Any]] = []
    if delete_after_days is not None:
        cutoff = now - delete_after_days * 86400.0
        for dirpath, _d, filenames in os.walk(root):
            for fn in filenames:
                if not fn.endswith(".gz"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime >= cutoff:
                    continue
                size = st.st_size
                try:
                    os.unlink(p)
                except OSError:
                    continue
                freed += size
                deleted.append({"path": p, "bytes": size,
                                "age_days": round((now - st.st_mtime) / 86400,
                                                  2)})
    return {"root": root, "before": before, "after": usage(root),
            "candidates": len(cands), "compressed": len(done),
            "failed": len(failed), "failures": failed[:10],
            "bytes_freed": freed, "gb_freed": round(freed / 1e9, 3),
            "deleted_archives": len(deleted), "deleted": deleted[:10],
            "deletion_enabled": delete_after_days is not None,
            "labels": LABELS}
