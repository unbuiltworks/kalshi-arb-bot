"""The DISARMED supervisor loop.

It does three things and refuses everything else: keep AuthBook connected,
poll for an owner-signed authorization, and — only when one verifies — hand
that single experiment to XR2's unchanged runner.

WHAT MAKES THE IDLE STATE SAFE. It is not a flag. While idle this loop holds
no `BoundedOrderTransport` at all: the object that can POST is constructed
inside `_run_one`, after verification, and is dropped when the experiment
ends. A supervisor sitting idle for a week has no order route in memory.

ORDERING, AND WHY. Verification consumes nothing, so a refusal is free. The
AUTHORIZATION is then consumed BEFORE the experiment, so a document whose
outcome we cannot observe cannot be replayed. The SUBMISSION latch is
consumed much later, inside `submit_one`, immediately before the POST, and
remains a separate durable file — a consumed authorization with an unspent
latch is the normal, correct record of "authorized, gates refused, no order
attempted".

NOTHING HERE RETRIES. A refused document is logged and left alone; the owner
re-signs if they meant something else. A failed experiment is terminal.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from analysis.l5.live_exec.spec import MAX_POSSIBLE_LOSS_USD, VENUE_MIN_QTY
from analysis.l5.live_exec4 import authz as A
from analysis.l5.live_exec4.spec import (AUTHZ_LEDGER_DIR, AUTHZ_URL_ENV,
                                         MAX_EXPERIMENTS_PER_PROCESS,
                                         MAX_EXPERIMENTS_PER_UTC_DAY,
                                         OWNER_PUBLIC_KEY_B64,
                                         POLL_INTERVAL_S, PROBE,
                                         XR3_FINGERPRINT, assert_frozen,
                                         owner_key_supplied)

FETCH_TIMEOUT_S = 10.0
#: a document larger than this is refused unread -- an authorization is a
#: few hundred bytes and a large body is not one
MAX_DOC_BYTES = 8192


def _stamp(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))


def fetch_document(url: str) -> Dict[str, Any]:
    """Outbound HTTPS GET. Any failure is a refusal, never an exception that
    stops the supervisor: an unreachable document means stay DISARMED."""
    if not url:
        return {"ok": False, "why": "%s is not set" % AUTHZ_URL_ENV}
    if not url.startswith("https://"):
        return {"ok": False, "why": "authorization URL must be https"}
    try:
        import ssl
        import urllib.request
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "exec1xr3"})
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(),
                timeout=FETCH_TIMEOUT_S) as r:
            raw = r.read(MAX_DOC_BYTES + 1)
        if len(raw) > MAX_DOC_BYTES:
            return {"ok": False, "why": "document exceeds %d bytes"
                                        % MAX_DOC_BYTES}
        return {"ok": True, "doc": json.loads(raw or b"{}"),
                "bytes": len(raw)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "fetch failed: %s" % type(exc).__name__}


def _utc_day(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t or time.time()))


class Supervisor:
    """Idle, DISARMED, until an owner-signed document says otherwise."""

    def __init__(self, *, runner: Optional[Any] = None,
                 book: Optional[Any] = None,
                 now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        assert_frozen()
        self._runner = runner
        self._book = book
        self._now = now
        self._sleep = sleep
        self.experiments_run = 0
        self.day_counts: Dict[str, int] = {}
        self.log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------- gating
    def caps(self) -> Dict[str, Any]:
        day = _utc_day(self._now())
        used_today = self.day_counts.get(day, 0)
        return {"per_process_used": self.experiments_run,
                "per_process_max": MAX_EXPERIMENTS_PER_PROCESS,
                "per_day_used": used_today,
                "per_day_max": MAX_EXPERIMENTS_PER_UTC_DAY,
                "utc_day": day,
                "ok": (self.experiments_run < MAX_EXPERIMENTS_PER_PROCESS
                       and used_today < MAX_EXPERIMENTS_PER_UTC_DAY)}

    def assess(self, doc: Any, *, ledger_dir: str = AUTHZ_LEDGER_DIR
               ) -> Dict[str, Any]:
        """Verify a document. Consumes nothing. DISARMED unless ok."""
        if not owner_key_supplied():
            return {"ok": False,
                    "refusals": ["OWNER_PUBLIC_KEY_NOT_SUPPLIED"],
                    "note": ("the build carries the placeholder key, so no "
                             "signature can verify and nothing can arm")}
        v = A.verify(doc, public_key_b64=OWNER_PUBLIC_KEY_B64,
                     my_fingerprint=XR3_FINGERPRINT,
                     ledger_dir=ledger_dir, now=self._now(),
                     expect_probe=PROBE, expect_qty=str(VENUE_MIN_QTY),
                     expect_max_loss=str(MAX_POSSIBLE_LOSS_USD))
        caps = self.caps()
        if v["ok"] and not caps["ok"]:
            v = dict(v)
            v["ok"] = False
            v["refusals"] = list(v["refusals"]) + ["EXPERIMENT_CAP_REACHED"]
        v["caps"] = caps
        return v

    # ------------------------------------------------------------ one run
    def _run_one(self, doc: Dict[str, Any], *,
                 ledger_dir: str = AUTHZ_LEDGER_DIR) -> Dict[str, Any]:
        """Consume the authorization, then hand off to XR2's runner.

        The order transport is constructed by the runner, inside this call
        and nowhere else.
        """
        record = A.consume_authorization(ledger_dir, doc)
        self.experiments_run += 1
        day = _utc_day(self._now())
        self.day_counts[day] = self.day_counts.get(day, 0) + 1

        # XR2's runner reads the slug and session from the environment, so
        # the verified document is projected onto exactly those two names
        # for the duration of this one experiment and then removed. The
        # document is the authority; the env is only how the frozen runner
        # is told about it.
        saved = {k: os.environ.get(k) for k in
                 ("PMUS_EXEC1_GATE_EVIDENCE", "PMUS_EXEC1_SESSION_ID",
                  "PMUS_EXEC1_ARM", "PMUS_EXEC1_LIVE",
                  "PMUS_EXEC1_AUTHORIZED_FINGERPRINT")}
        try:
            os.environ["PMUS_EXEC1_GATE_EVIDENCE"] = str(doc["market_slug"])
            os.environ["PMUS_EXEC1_SESSION_ID"] = str(doc["session_id"])
            os.environ["PMUS_EXEC1_ARM"] = str(doc["session_id"])
            os.environ["PMUS_EXEC1_LIVE"] = "YES"
            runner = self._runner
            if runner is None:
                from analysis.l5.live_exec3 import runner as R
                from analysis.l5.live_exec3.spec import XR2_FINGERPRINT
                os.environ["PMUS_EXEC1_AUTHORIZED_FINGERPRINT"] = \
                    XR2_FINGERPRINT
                runner = R.run_experiment
            out = runner(book=self._book)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return {"authorization": record, "experiment": out}

    # --------------------------------------------------------- the loop
    def tick(self, *, url: Optional[str] = None,
             ledger_dir: str = AUTHZ_LEDGER_DIR) -> Dict[str, Any]:
        """One poll. Returns what happened; never raises on a refusal."""
        got = fetch_document(url if url is not None
                             else os.getenv(AUTHZ_URL_ENV, "").strip())
        if not got["ok"]:
            return {"state": "IDLE_DISARMED", "at": _stamp(self._now()),
                    "reason": got["why"], "orders_submitted": 0}
        v = self.assess(got["doc"], ledger_dir=ledger_dir)
        if not v["ok"]:
            return {"state": "IDLE_DISARMED", "at": _stamp(self._now()),
                    "refusals": v["refusals"], "caps": v.get("caps"),
                    "orders_submitted": 0}
        try:
            ran = self._run_one(got["doc"], ledger_dir=ledger_dir)
        except A.AuthzRefused as exc:
            return {"state": "IDLE_DISARMED", "at": _stamp(self._now()),
                    "refusals": ["RACED_ON_CONSUME:%s" % exc],
                    "orders_submitted": 0}
        except A.AuthzLedgerUnavailable as exc:
            return {"state": "IDLE_DISARMED", "at": _stamp(self._now()),
                    "refusals": ["AUTHZ_LEDGER_UNAVAILABLE:%s" % exc],
                    "orders_submitted": 0}
        exp = ran["experiment"] or {}
        return {"state": "EXPERIMENT_COMPLETE", "at": _stamp(self._now()),
                "authorization_id": doc_id(got["doc"]),
                "authorization_record": ran["authorization"],
                "orders_submitted": exp.get("orders_submitted", 0),
                "stopped_at": exp.get("stopped_at"),
                "experiment": exp}

    def serve_forever(self, *, max_ticks: Optional[int] = None,
                      url: Optional[str] = None,
                      ledger_dir: str = AUTHZ_LEDGER_DIR
                      ) -> List[Dict[str, Any]]:
        n = 0
        while max_ticks is None or n < max_ticks:
            out = self.tick(url=url, ledger_dir=ledger_dir)
            self.log.append(out)
            print("EXEC1XR3_TICK " + json.dumps(out, default=str),
                  flush=True)
            n += 1
            if max_ticks is None or n < max_ticks:
                self._sleep(POLL_INTERVAL_S)
        return self.log


def doc_id(doc: Any) -> Optional[str]:
    try:
        return str(doc.get("authorization_id"))
    except Exception:                                          # noqa: BLE001
        return None
