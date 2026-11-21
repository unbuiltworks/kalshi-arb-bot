"""Append-only session journal with provenance, so a run can be replayed.

Every record is one line of JSON carrying the session id, a monotonically
increasing sequence number, the wall clock, and the record's kind. Nothing is
ever rewritten or deleted: a correction is a NEW record that names what it
supersedes. That is the only way a session weeks later can be trusted to be
what actually happened rather than what we later wished had happened.

The journal lives on the recorder's volume, so a container restart resumes
the same file rather than losing the session. The sequence continues from
whatever is already on disk, and the resume itself is journalled, so a gap
caused by a restart is visible instead of silent.

FAIL SOFT ON WRITE. A journal that cannot write must not take down the
capture it is observing: the failure is counted and surfaced on the next
health line. A journal that is silently empty, on the other hand, is a lie,
so `writes_failed` is reported on every window summary.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


class Journal:
    def __init__(self, path: str, session_id: Optional[str] = None) -> None:
        self.path = Path(path)
        self.session_id = session_id or uuid.uuid4().hex[:16]
        self.seq = 0
        self.writes_failed = 0
        self._resumed_from = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                with self.path.open("r", errors="replace") as fh:
                    for line in fh:
                        if line.strip():
                            self.seq += 1
                self._resumed_from = self.seq
        except OSError:
            self.writes_failed += 1

    def write(self, kind: str, payload: Dict[str, Any]) -> Optional[dict]:
        self.seq += 1
        rec = {
            "seq": self.seq,
            "session_id": self.session_id,
            "t": time.time(),
            "utc": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, separators=(",", ":"),
                                    default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            self.writes_failed += 1
            return None
        return rec

    def resume_note(self) -> Dict[str, Any]:
        return {"resumed_from_seq": self._resumed_from,
                "path": str(self.path),
                "note": ("a restart continues this file; the gap it causes is "
                         "journalled rather than hidden")}
