"""Durable off-box copies of CLOSED raw WS tape files. Upload-only, fail-open.

WHY. The raw tape lives on one Railway volume reachable only through the
container-file API, and that has repeatedly become unreachable mid-task. A
second copy in object storage removes the single point of access.

THE RULES THIS ENFORCES, each for a stated reason:

  * CLOSED, IMMUTABLE FILES ONLY. The file the recorder is currently appending
    to is never uploaded, because a partial object with a final-looking key is
    worse than no object: a later reader cannot tell it is short. `is_closed`
    decides from the tape's own hourly naming plus an mtime quiet period, never
    from a guess.
  * ASYNCHRONOUS AND FAIL-OPEN. Uploads run on a worker thread and every error
    is swallowed into a counter. Recording must never stall or die because
    object storage is unavailable -- the local tape stays authoritative.
  * IDEMPOTENT KEYS carrying the UTC hour and the file identity, so re-running
    a backfill overwrites the same object rather than multiplying copies.
  * VERIFIED. Size and sha256 are computed locally, sent as metadata, and the
    stored object's size is read back; a mismatch marks the upload failed and
    leaves the local file exactly as it was.
  * NEVER DELETES LOCAL RAW. There is no unlink, remove or rmtree in this
    module, and a test asserts that by source inspection. A successful upload
    is not permission to destroy the original; volume retention remains the
    recorder's own concern.
  * BACKFILL IS SEPARATE FROM LIVE CAPTURE. `backfill()` walks existing closed
    files; the live path only ever offers the file that has just rolled over.
  * NO CREDENTIALS IN LOGS. Only bucket, key, size and outcome are ever
    recorded; nothing reads or prints a key id or secret.

It holds no venue transport and no order path.
"""
from __future__ import annotations

import hashlib
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

#: a file untouched for this long is treated as closed
DEFAULT_QUIET_S = 120.0
CHUNK = 1024 * 1024


def sha256_and_size(path: str) -> tuple:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def object_key(path: str, *, prefix: str = "pmus-ws/raw") -> str:
    """Idempotent key carrying the UTC hour and the file's own identity.

    The recorder's hourly parts are named `pmus_ws_YYYYMMDD_HH.jsonl`, so the
    hour is already in the name; it is also hoisted into the path so a listing
    is browsable by day. Re-uploading the same file yields the SAME key.
    """
    name = os.path.basename(path)
    day = hour = "unknown"
    stem = name.split(".")[0]
    bits = stem.split("_")
    if len(bits) >= 4 and len(bits[2]) == 8:
        day, hour = bits[2], bits[3]
    elif len(bits) >= 3 and len(bits[2]) == 8:
        day, hour = bits[2], "daily"
    return f"{prefix}/{day}/{hour}/{name}"


def is_closed(path: str, *, now: Optional[float] = None,
              quiet_s: float = DEFAULT_QUIET_S) -> bool:
    """Is this file finished being written?

    Two independent conditions, both required: the tape part it names is not
    the CURRENT hour, and it has not been modified for `quiet_s`. Either alone
    is too weak -- a clock at :59 makes the hour test racy, and a quiet file
    may simply be in a lull.
    """
    import datetime as dt
    now = time.time() if now is None else now
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    if now - mtime < quiet_s:
        return False
    name = os.path.basename(path)
    cur = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)
    current_part = f"pmus_ws_{cur:%Y%m%d_%H}"
    return not name.startswith(current_part)


class RawS3Uploader:
    """Upload-only, fail-open, worker-thread copier for closed tape files."""

    def __init__(self, *, bucket: Optional[str] = None,
                 prefix: str = "pmus-ws/raw",
                 client: Any = None, quiet_s: float = DEFAULT_QUIET_S) -> None:
        self.bucket = bucket or os.getenv("PMUS_RAW_S3_BUCKET", "").strip()
        self.prefix = prefix or os.getenv("PMUS_RAW_S3_PREFIX", "pmus-ws/raw")
        self.quiet_s = quiet_s
        self._client = client
        self.q: "queue.Queue[str]" = queue.Queue(maxsize=256)
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()
        #: counters only. No credential, path secret or error body is stored.
        self.uploaded = self.failed = self.skipped_open = self.verified = 0
        self.errors: List[str] = []
        self.seen: Dict[str, str] = {}

    # -------------------------------------------------------------- enabled

    def enabled(self) -> bool:
        """Absent configuration is a NO-OP, never an error: the recorder must
        run identically when object storage is not set up."""
        return bool(self.bucket)

    def client(self) -> Any:                                 # pragma: no cover
        if self._client is None:
            import boto3
            self._client = boto3.client("s3")
        return self._client

    # ---------------------------------------------------------------- offer

    def offer(self, path: str) -> bool:
        """Queue a CLOSED file. Never blocks the caller, never raises."""
        try:
            if not self.enabled():
                return False
            if not is_closed(path, quiet_s=self.quiet_s):
                self.skipped_open += 1
                return False
            self.q.put_nowait(path)
            return True
        except Exception as exc:                             # noqa: BLE001
            self._note(f"offer:{type(exc).__name__}")
            return False

    def _note(self, why: str) -> None:
        self.failed += 1
        if len(self.errors) < 20:
            self.errors.append(why[:120])

    # --------------------------------------------------------------- upload

    def upload_one(self, path: str) -> Dict[str, Any]:
        """Upload with verification. Returns an outcome; never raises."""
        key = object_key(path, prefix=self.prefix)
        out: Dict[str, Any] = {"key": key, "bucket": self.bucket,
                               "ok": False}
        try:
            digest, size = sha256_and_size(path)
            out.update(sha256=digest, size=size)
            with open(path, "rb") as fh:
                self.client().put_object(
                    Bucket=self.bucket, Key=key, Body=fh,
                    Metadata={"sha256": digest, "bytes": str(size),
                              "source": os.path.basename(path)})
            head = self.client().head_object(Bucket=self.bucket, Key=key)
            stored = int(head.get("ContentLength") or -1)
            out["stored_size"] = stored
            if stored != size:
                out["why"] = f"size mismatch: local {size} stored {stored}"
                self._note("verify:size_mismatch")
                return out
            self.verified += 1
            self.uploaded += 1
            self.seen[key] = digest
            out["ok"] = True
            return out
        except Exception as exc:                             # noqa: BLE001
            out["why"] = f"{type(exc).__name__}"
            self._note(f"upload:{type(exc).__name__}")
            return out

    # ---------------------------------------------------------------- loops

    def _run(self) -> None:                                  # pragma: no cover
        while not self.stop.is_set():
            try:
                path = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            self.upload_one(path)

    def start(self) -> "RawS3Uploader":
        if not self.enabled():
            return self
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="pmus-raw-s3")
        self.thread.start()
        return self

    def close(self) -> None:
        self.stop.set()

    # ------------------------------------------------------------- backfill

    def backfill(self, root: str, *, limit: int = 500) -> Dict[str, Any]:
        """Offer every CLOSED file under `root`. Separate from live capture."""
        offered = 0
        try:
            names = sorted(os.listdir(root))
        except OSError as exc:
            self._note(f"backfill:{type(exc).__name__}")
            return {"offered": 0, "why": type(exc).__name__}
        for name in names:
            if offered >= limit:
                break
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            if self.offer(path):
                offered += 1
        return {"offered": offered, "scanned": len(names), "root": root}

    def status(self) -> Dict[str, Any]:
        return {"enabled": self.enabled(), "bucket": self.bucket,
                "prefix": self.prefix, "uploaded": self.uploaded,
                "verified": self.verified, "failed": self.failed,
                "skipped_still_open": self.skipped_open,
                "queued": self.q.qsize(), "errors": self.errors,
                "labels": ("upload-only, fail-open; local raw is never "
                           "deleted by this module and remains authoritative")}
