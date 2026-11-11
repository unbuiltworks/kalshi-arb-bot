"""PMUS credentials and request signing, WITHOUT any order path.

`pmus_q1_executor` holds the same signing, but it also holds
`place_limit_buy`, `cancel_all_deadman` and `run_campaign`, which POST to
/v1/orders. That module therefore must not be shipped to the evidence
recorder: the recorder is the one service that holds the API credentials, and
its image states that no order endpoint exists in it. Putting a credentialed
execution stack there to obtain a read-only preflight would trade away exactly
the property the preflight is meant to establish.

So this module carries only what a READ needs -- hosts, credential env names,
the Ed25519 signature and a transport -- and deliberately carries no way to
place, amend or cancel anything. There is no arming here because there is
nothing to arm.

DRIFT. The signature must stay byte-identical to the executor's or an
authenticated read starts 401-ing in production. `tests/test_pmus_auth.py`
asserts equivalence against `pmus_q1_executor` for the same inputs, so the two
cannot diverge silently. The executor is left untouched: it is live execution
code, and a refactor of it is not worth the risk of obtaining a read.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Mapping, Optional

API = "https://api.polymarket.us"
GATEWAY = "https://gateway.polymarket.us"

KEY_ENV = "PMUS_API_KEY_ID"
SECRET_ENV = "PMUS_API_SECRET_KEY"

#: the venue's own name for the tick constraint, read from the market
TICK_FIELD = "orderPriceMinTickSize"

#: the owner arm contract, reusing the existing executor's environment names
#: so there is ONE way to arm this account and not two. Reading them places
#: no order and opens no socket; arming is still a separate act elsewhere.
LIVE_ENV = "PMUS_Q1_LIVE"
SID_ENV = "PMUS_Q1_SESSION_ID"
ARM_ENV = "PMUS_Q1_ARM"


def armed() -> bool:
    """True only when the owner has set all three, and the token matches."""
    sid = os.getenv(SID_ENV, "").strip()
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES" and bool(sid)
            and os.getenv(ARM_ENV, "").strip() == sid)


def has_credentials() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and \
        bool(os.getenv(SECRET_ENV, "").strip())


def sign_path(path: str) -> str:
    """The path the signature covers: the URL path WITHOUT its query string.

    Every authenticated GET carrying a query string answered 401 while signed
    over path+query.
    """
    return path.split("?", 1)[0]


def signed_headers(method: str, path: str,
                   now_ms: Optional[int] = None) -> dict:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    seed = base64.b64decode(os.environ[SECRET_ENV].strip())[:32]
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    sig = base64.b64encode(
        key.sign(f"{ts}{method}{sign_path(path)}".encode())).decode()
    return {"X-PM-Access-Key": os.environ[KEY_ENV].strip(),
            "X-PM-Timestamp": ts, "X-PM-Signature": sig,
            "Content-Type": "application/json"}


class ReadOnlyTransport:
    """Signed HTTP that can only GET.

    The refusal is here, in the only transport this image has, rather than in
    a caller's flag: a process that cannot construct a mutating request cannot
    be talked into one.
    """

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = API) -> Any:
        if str(method).upper() != "GET":
            raise PermissionError(
                "%s %s refused: this transport is read-only" % (method, path))
        import ssl
        import urllib.request
        headers = signed_headers("GET", path) if base == API else \
            {"User-Agent": "pmus-preflight"}
        req = urllib.request.Request(base + path, data=None,
                                     headers=headers, method="GET")
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=15) as r:
            return json.loads(r.read() or b"{}")
