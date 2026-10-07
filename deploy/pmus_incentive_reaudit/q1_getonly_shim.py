"""GET-ONLY stand-in for pmus_q1_executor / pmus_q1_executor_v4.

The re-audit needs signed GETs and a few constants from the Q1 executor
modules. Those real modules also carry live order paths -- POST
/v1/order/preview, POST /v1/orders, POST /v1/order/{id}/cancel. Shipping
them would put an order-capable path inside a read-only audit image, and
the owner's constraint is: NO POST / order-capable path.

So the image copies THIS FILE over both module names instead. It provides
exactly the surface the audit uses, and its transport
**raises on any method except GET, before a socket is opened**. There is
no POST here to disable, and no flag that could re-enable one.

Surface required, enumerated from the callers rather than guessed:
  pmus_incentive_audit : GATEWAY, Transport, state_dir
  pmus_settlement_auditor : Transport, _get_with_retry, ledger_append,
                            state_dir, base.GATEWAY, base._get_with_retry
  scripts.pmus_incentive_reaudit : Transport, KEY_ENV, SECRET_ENV,
                            LIVE_ENV, SID_ENV, ARM_ENV, GATE_ENV
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_auth as A

API = A.API
GATEWAY = A.GATEWAY

KEY_ENV = A.KEY_ENV
SECRET_ENV = A.SECRET_ENV
LIVE_ENV = "PMUS_Q1_LIVE"
SID_ENV = "PMUS_Q1_SESSION_ID"
ARM_ENV = "PMUS_Q1_ARM"
GATE_ENV = "PMUS_Q1_GATE_EVIDENCE"

#: v4 callers reach through `q1.base.*`; point it at this module so
#: base.GATEWAY and base._get_with_retry resolve to the GET-only versions.
base = sys.modules[__name__]


class MethodRefused(RuntimeError):
    """A non-GET method was attempted. There is no POST path in this image."""


class Transport:
    """Signed HTTP, GET only. No POST exists on this class."""

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = API) -> Any:
        m = str(method).upper()
        if m != "GET":
            raise MethodRefused(
                "%s %s refused: this image is the read-only incentive "
                "re-audit and has no order path" % (m, path))
        if body is not None:
            raise MethodRefused(
                "a request body is refused on a GET-only transport")
        headers = A.signed_headers("GET", path)
        req = urllib.request.Request(base + path, headers=headers,
                                     method="GET")
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=30) as r:
            return json.loads(r.read() or b"{}")


def has_credentials() -> bool:
    return A.has_credentials()


def armed() -> bool:
    """True if ANY Q1 arming variable is set. The runner refuses on this."""
    return any(os.getenv(k, "").strip()
               for k in (LIVE_ENV, SID_ENV, ARM_ENV, GATE_ENV))


def state_dir() -> Path:
    p = Path(os.getenv("PMUS_Q1_STATE_DIR", "/state/pmus-q1"))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def ledger_append(row: Mapping[str, Any]) -> None:
    """Append-only, and only ever called by read paths here. Never raises
    into the audit: a missing state dir must not fail a measurement."""
    try:
        with open(state_dir() / "ledger.jsonl", "a", encoding="utf8") as fh:
            fh.write(json.dumps(dict(row), default=str) + "\n")
    except OSError:
        pass


def _get_with_retry(t: Transport, path: str, base: str = API,
                    tries: int = 4, pause: float = 0.6) -> Any:
    """Paced GET with backoff. Mutations are impossible here, so there is
    no question of replaying one."""
    last = None
    for i in range(tries):
        try:
            return t.request("GET", path, base=base)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                time.sleep(pause * (2 ** i))
                continue
            if 500 <= exc.code < 600:
                time.sleep(pause * (2 ** i))
                continue
            raise
        except OSError as exc:
            last = exc
            time.sleep(pause * (2 ** i))
    if last is not None:
        raise last
    raise RuntimeError("unreachable")
