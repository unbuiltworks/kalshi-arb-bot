"""Entrypoint that adds off-box raw durability AROUND the reviewed recorder.

WHY A THIRD MODULE. Both `pmus_ws_evidence_recorder` and
`pmus_ws_evidence_recorder_enriched` are part of the reviewed artifact
`e48960f7`, so editing EITHER would register as a lineage delta on an
execution-class file. The owner's decision was to spend no lineage exception on
a durability layer that is orthogonal to capture, so this module wraps them
from outside and both stay byte-identical. A test asserts that.

WHAT IT ADDS, and nothing else: a daemon thread that periodically offers
ALREADY-CLOSED raw tape files to `pmus_raw_s3_uploader`, then delegates to the
enriched recorder's own `main()`.

WHAT IT DOES NOT TOUCH: book or trade parsing, subscription logic, raw writes,
rollover behaviour, discovery, gates, telemetry, or any order-capable code. It
imports no transport and no order path.

FAIL-OPEN IS THE WHOLE POINT. Capture is the thing we cannot lose. Every part
of the durability path is wrapped so that a missing bucket, a missing boto3, a
credential error, a full disk or a thread crash leaves the recorder running
exactly as it would without this module. Absent configuration is a silent
no-op, not an error.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional, Set

RAW_DIR_ENV = "PMUS_RAW_DIR"
DEFAULT_RAW_DIR = "/state/pmus-ws/raw"
SCAN_EVERY_ENV = "PMUS_RAW_S3_SCAN_EVERY_S"
DEFAULT_SCAN_EVERY_S = 300.0
BACKFILL_EVERY_ENV = "PMUS_RAW_S3_BACKFILL_EVERY_S"
DEFAULT_BACKFILL_EVERY_S = 3600.0


def raw_dir() -> str:
    return os.getenv(RAW_DIR_ENV, DEFAULT_RAW_DIR)


class DurabilityWorker:
    """Periodic scanner that offers closed raw files, and nothing more.

    `offered` is keyed by OBJECT KEY, not path, so a periodic scan cannot
    re-queue a file it has already handed over -- without that, a directory of
    hundreds of closed hourly parts would flood the queue on every scan.
    """

    def __init__(self, uploader: Any, *, root: Optional[str] = None,
                 scan_every_s: Optional[float] = None,
                 backfill_every_s: Optional[float] = None) -> None:
        self.up = uploader
        self.root = root or raw_dir()
        self.scan_every_s = float(
            scan_every_s if scan_every_s is not None
            else os.getenv(SCAN_EVERY_ENV, DEFAULT_SCAN_EVERY_S))
        self.backfill_every_s = float(
            backfill_every_s if backfill_every_s is not None
            else os.getenv(BACKFILL_EVERY_ENV, DEFAULT_BACKFILL_EVERY_S))
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        #: object keys already handed to the uploader in this process
        self.offered: Set[str] = set()
        self.scans = self.offers = self.skipped_known = self.errors = 0
        self.last_backfill: float = 0.0

    # ------------------------------------------------------------- one pass

    def scan_once(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        """Offer every closed file whose key we have not already offered.

        Never raises: a scan failure must not touch the recorder.
        """
        from src import pmus_raw_s3_uploader as U
        now = time.time() if now is None else now
        self.scans += 1
        out = {"offered": 0, "skipped_known": 0, "closed_seen": 0}
        try:
            names = sorted(os.listdir(self.root))
        except Exception:                                    # noqa: BLE001
            self.errors += 1
            return {**out, "why": "raw dir unreadable"}
        for name in names:
            path = os.path.join(self.root, name)
            try:
                if not os.path.isfile(path):
                    continue
                if not U.is_closed(path, now=now):
                    continue
                out["closed_seen"] += 1
                key = U.object_key(path, prefix=self.up.prefix)
                if key in self.offered:
                    self.skipped_known += 1
                    out["skipped_known"] += 1
                    continue
                if self.up.offer(path):
                    self.offered.add(key)
                    self.offers += 1
                    out["offered"] += 1
            except Exception:                                # noqa: BLE001
                self.errors += 1
                continue
        return out

    def backfill_once(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        """Historical sweep, kept SEPARATE from the live rollover path."""
        now = time.time() if now is None else now
        self.last_backfill = now
        try:
            return self.up.backfill(self.root)
        except Exception:                                    # noqa: BLE001
            self.errors += 1
            return {"offered": 0, "why": "backfill failed"}

    # ---------------------------------------------------------------- loop

    def _run(self) -> None:                                  # pragma: no cover
        while not self.stop.is_set():
            try:
                self.scan_once()
                now = time.time()
                if now - self.last_backfill >= self.backfill_every_s:
                    self.backfill_once(now=now)
            except Exception:                                # noqa: BLE001
                self.errors += 1
            self.stop.wait(self.scan_every_s)

    def start(self) -> "DurabilityWorker":
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="pmus-raw-durability")
        self.thread.start()
        return self

    def status(self) -> Dict[str, Any]:
        return {"root": self.root, "scans": self.scans, "offers": self.offers,
                "skipped_known_keys": self.skipped_known,
                "errors": self.errors, "known_keys": len(self.offered),
                "scan_every_s": self.scan_every_s,
                "backfill_every_s": self.backfill_every_s}


def start_durability() -> Optional[DurabilityWorker]:
    """Start the worker, or return None. NEVER raises, never blocks capture."""
    try:
        from src import pmus_raw_s3_uploader as U
        up = U.RawS3Uploader()
        if not up.enabled():
            print("PMUS_RAW_DURABILITY {\"enabled\": false, \"reason\": "
                  "\"no bucket configured; recorder unaffected\"}", flush=True)
            return None
        up.start()
        w = DurabilityWorker(up).start()
        print("PMUS_RAW_DURABILITY " + str(
            {"enabled": True, **w.status()}).replace("'", '"'), flush=True)
        return w
    except Exception as exc:                                 # noqa: BLE001
        # A durability failure is never a capture failure.
        print("PMUS_RAW_DURABILITY {\"enabled\": false, \"error\": \"%s\"}"
              % type(exc).__name__, flush=True)
        return None


def main() -> int:
    start_durability()
    from src import pmus_ws_evidence_recorder_enriched as enriched
    return enriched.main()


if __name__ == "__main__":
    raise SystemExit(main())
