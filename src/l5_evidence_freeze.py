"""Freezing evidence before reading it, and PROVING it stayed frozen.

Probe 08's window produced exactly two artifacts on the volume -- a journal
and an inventory file -- and its report died before it printed, so those two
files are the only record of what the run actually did. Recovery, as written,
read and wrote the same journal and reconciled the same inventory in place.
Extracting the evidence would therefore have modified it in the act of
reading it.

The discipline here is not "be careful". It is:

    hash the originals, work on copies, re-hash the originals, and report
    the comparison.

An unmutated original is then a verified fact in the report rather than a
claim about what the code meant to do. `verify_unchanged` is the whole point
of the module; everything else exists to make it meaningful.

Nothing here opens an original for writing. `stage_copy` refuses to overwrite
a destination that already exists, because a second run silently reusing a
copy that the first run mutated is exactly the failure this prevents.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
from typing import Any, Dict, List, Mapping, Optional, Sequence

CHUNK = 1 << 20


class EvidenceMutated(RuntimeError):
    """An original changed while we were reading it. Not recoverable."""

    def __init__(self, drift: Sequence[Mapping[str, Any]]) -> None:
        super().__init__("evidence changed under us: %s"
                         % ", ".join(str(d.get("path")) for d in drift))
        self.drift = [dict(d) for d in drift]


def sha256_file(path: str) -> Dict[str, Any]:
    """Digest, size and mtime of one file, or a record that it is absent.

    Absence is recorded rather than raised: a manifest that omits a missing
    file cannot later be distinguished from one that never looked.
    """
    rec: Dict[str, Any] = {"path": str(path)}
    if not path or not os.path.exists(path):
        rec["present"] = False
        return rec
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            size += len(b)
            h.update(b)
    st = os.stat(path)
    rec.update({
        "present": True,
        "sha256": h.hexdigest(),
        "bytes": size,
        "mtime_utc": dt.datetime.fromtimestamp(
            st.st_mtime, dt.timezone.utc).isoformat(),
    })
    return rec


def freeze_originals(paths: Sequence[str]) -> List[Dict[str, Any]]:
    """Read-only manifest of the files that must not change."""
    return [sha256_file(p) for p in paths if p]


def stage_copy(src: str, dst: str, *, overwrite: bool = False
               ) -> Dict[str, Any]:
    """Copy an original to a working path, and prove the copy is faithful.

    Refuses an existing destination unless explicitly told otherwise, so a
    rerun cannot quietly inherit a copy the previous run already mutated.
    """
    before = sha256_file(src)
    if not before.get("present"):
        return {"copied": False, "reason": "SOURCE_ABSENT", "src": before,
                "dst": {"path": str(dst), "present": False}}
    if os.path.exists(dst) and not overwrite:
        return {"copied": False, "reason": "DESTINATION_EXISTS",
                "src": before, "dst": sha256_file(dst)}
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.copyfile(src, dst)
    after = sha256_file(dst)
    if after.get("sha256") != before.get("sha256"):
        raise EvidenceMutated([{"path": dst, "expected": before.get("sha256"),
                                "found": after.get("sha256")}])
    src_after = sha256_file(src)
    if src_after.get("sha256") != before.get("sha256"):
        raise EvidenceMutated([{"path": src, "expected": before.get("sha256"),
                                "found": src_after.get("sha256")}])
    return {"copied": True, "src": before, "dst": after,
            "identical": True}


def verify_unchanged(manifest: Sequence[Mapping[str, Any]]
                     ) -> Dict[str, Any]:
    """Re-hash every frozen original and compare. The point of the module."""
    checked: List[Dict[str, Any]] = []
    drift: List[Dict[str, Any]] = []
    for rec in manifest:
        now = sha256_file(str(rec.get("path")))
        same = (now.get("present") == rec.get("present")
                and now.get("sha256") == rec.get("sha256")
                and now.get("bytes") == rec.get("bytes"))
        row = {"path": rec.get("path"), "unchanged": same,
               "before": rec.get("sha256"), "after": now.get("sha256"),
               "bytes_before": rec.get("bytes"), "bytes_after": now.get("bytes")}
        checked.append(row)
        if not same:
            drift.append(row)
    return {"checked": checked, "drift": drift,
            "all_unchanged": not drift,
            "labels": ("every original re-hashed after the run. This is a "
                       "measurement, not an assurance.")}
