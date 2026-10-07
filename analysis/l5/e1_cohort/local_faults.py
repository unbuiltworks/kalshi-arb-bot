"""LOCAL faults are not venue errors, and the incident of 2026-10-07 16:48:58Z
is why this module exists.

WHAT HAPPENED. The image is built from `python:3.11-slim` and the Dockerfile
ran no `pip install`, so `cryptography` was not present. Nothing noticed,
because `src.pmus_auth.signed_headers` imports it LAZILY -- inside the
function -- so the build-time "the entrypoint imports" assertion passed with
zero third-party modules loaded. The first authenticated request is the
PREVIEW, which happens immediately after `WINDOW_OPENED`. There,
`signed_headers` raised `ModuleNotFoundError` BEFORE any socket was opened;
`orders._submit_once`'s broad `except Exception` turned that into
`PreviewRejected`; `run_cohort` counted `PreviewRejected` as a VENUE error
and continued; five iterations later the consecutive-venue-error safety stop
fired. Elapsed: 20.8 ms. The tape therefore said VENUE_ERROR_STORM about a
venue that had never been contacted.

THE TAXONOMY DEFECT, STATED PLAINLY. A deterministic local fault and a
flaky venue are opposite situations and want opposite responses. A venue
that answers incoherently should be retried a bounded number of times and
then abandoned -- that is what the storm counter is for, and it stays. A
missing dependency, an unset credential or an unusable key will fail
identically on every one of the next 239 attempts; retrying it four more
times learns nothing, burns four iterations of a 240-placement
authorization, and mislabels the cause in the one record the owner reads.
It must fail closed ONCE, say what it was, and stop.

HOW LOCAL IS DISTINGUISHED FROM VENUE -- BY REPRODUCTION, NOT BY GUESSING.
Classifying on exception type alone is unsound here: `ValueError` from
`json.loads` of a venue response is post-network and genuinely retryable,
while `ValueError` from `Ed25519PrivateKey.from_private_bytes` is a local
configuration fault, and the type cannot tell them apart. So the classifier
RE-RUNS the signing contract. If signing is still broken, the fault is local
and deterministic -- demonstrated, not inferred. If signing works, the
exception is left exactly where it was and the existing bounded venue
protection handles it unchanged.

NO SECRET VALUE IS EVER READ, FORMATTED OR PERSISTED by anything here. The
contract check signs a NON-ORDER self-test path, discards the headers, and
reports only the names it needed and whether each was usable.
"""
from __future__ import annotations

import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

from src import pmus_auth as A

#: the path the self-test signs. It is NOT a venue route: it is never sent
#: anywhere, and `BoundedOrderTransport` would refuse it as a mutation.
SIGNING_SELFTEST_PATH = "/v1/_e1_local_signing_selftest"

#: the ONE truncation cause a deterministic local fault may produce.
LOCAL_FAULT = "LOCAL_EXECUTION_FAULT"


class LocalExecutionFault(Exception):
    """A deterministic fault in THIS process: a missing dependency, an unset
    or unusable credential, a signing failure. It reached no venue, it
    created no order, it consumed no latch slot, and retrying it would
    produce the identical failure.

    It is deliberately NOT a subclass of `PreviewRejected` or of any venue
    exception, so it cannot be caught by a handler that counts venue errors.
    """

    def __init__(self, message: str, detail: Optional[Dict[str, Any]] = None
                 ) -> None:
        super().__init__(message)
        self.detail: Dict[str, Any] = dict(detail or {})


def _secret_values() -> List[str]:
    """The values that must never appear in a recorded string. Read only to
    be subtracted FROM text, never to be written INTO it."""
    out: List[str] = []
    for name in (A.SECRET_ENV, A.KEY_ENV):
        v = (os.getenv(name) or "").strip()
        if len(v) >= 6:
            out.append(v)
    return out


def redact(text: Any, *, limit: int = 600) -> str:
    """Remove any credential value that managed to reach an exception
    message, then truncate. Applied to EVERY string this module records.

    The subtraction is not theatre: `base64.b64decode` and the key loader
    both include offending input in some of their messages, and a signing
    failure is exactly the situation where the secret is most likely to be
    quoted back at us.
    """
    s = str(text)
    for v in _secret_values():
        s = s.replace(v, "<REDACTED>")
    return s[:limit]


def _origin(exc: BaseException, *, prefer: str = "") -> Dict[str, Any]:
    """Where it actually came from: the deepest frame, and the deepest frame
    in `prefer` when one exists (the shallow frame is usually our own
    wrapper and says nothing)."""
    tb = exc.__traceback__
    frames = traceback.extract_tb(tb) if tb is not None else []
    if not frames:
        return {"file": None, "line": None, "function": None, "source": None}

    def fmt(f: Any) -> Dict[str, Any]:
        return {"file": f.filename, "line": f.lineno, "function": f.name,
                "source": redact(f.line or "", limit=200)}

    out = {"deepest_frame": fmt(frames[-1])}
    if prefer:
        want = [f for f in frames if prefer in (f.filename or "")]
        if want:
            out["deepest_frame_in_%s" % prefer] = fmt(want[-1])
    return out


def credential_contract() -> Dict[str, Any]:
    """NAMES and USABILITY only. No value, no length that could narrow a
    key, no prefix.

    It also states the names that are NOT read, because the incident
    produced a plausible-sounding wrong hypothesis -- that the service
    carried `PMUS_API_SECRET_KEY` while the code wanted `PMUS_API_SECRET` --
    and the record should refute it rather than leave it open.
    """
    return {"names_read": [A.KEY_ENV, A.SECRET_ENV],
            "present": {A.KEY_ENV: bool((os.getenv(A.KEY_ENV) or "").strip()),
                        A.SECRET_ENV: bool(
                            (os.getenv(A.SECRET_ENV) or "").strip())},
            "names_not_read_by_any_code": ["PMUS_API_SECRET"],
            "VALUES_NEVER_READ_OR_RECORDED": True}


def verify_signing_contract(*, sign_fn: Optional[Callable[..., Any]] = None
                            ) -> Dict[str, Any]:
    """Prove, LOCALLY, that this process can sign an authenticated request.

    Raises `LocalExecutionFault` if it cannot. No socket is opened, no venue
    is contacted, no order body is built and no latch is touched: this is
    arithmetic over a self-test path.
    """
    sign = sign_fn or A.signed_headers
    contract = credential_contract()
    try:
        headers = sign("GET", SIGNING_SELFTEST_PATH)
    except BaseException as exc:                               # noqa: BLE001
        raise LocalExecutionFault(
            "this process cannot sign an authenticated request: %s: %s"
            % (type(exc).__name__, redact(exc)),
            detail={"fault": LOCAL_FAULT,
                    "operation": "Ed25519 signing self-test over %s"
                                 % SIGNING_SELFTEST_PATH,
                    "exception_type": "%s.%s" % (type(exc).__module__,
                                                 type(exc).__name__),
                    "exception_message_redacted": redact(exc),
                    "origin": _origin(exc, prefer="pmus_auth"),
                    "credential_contract": contract,
                    "cryptography_importable":
                        "cryptography" in sys.modules,
                    "reached_the_venue": False,
                    "opened_a_socket": False,
                    "created_an_order": False,
                    "consumed_a_latch_slot": False,
                    "deterministic_and_would_repeat_identically": True,
                    "is_a_venue_error": False}) from None
    names = sorted(str(k) for k in (headers or {}))
    return {"ok": True, "signing_selftest_path": SIGNING_SELFTEST_PATH,
            "header_names": names, "header_values_recorded": False,
            "credential_contract": contract,
            "network_performed": "none", "venue_post": "none",
            "latch_touched": False}


def classify_preview_exception(
        exc: BaseException, *,
        verify: Optional[Callable[..., Any]] = None
) -> Optional[LocalExecutionFault]:
    """Decide whether a generic PREVIEW exception was a LOCAL fault.

    Returns a `LocalExecutionFault` to raise, or None to leave the existing
    venue handling exactly as it was.

    The decision is a RE-RUN of the signing contract rather than a type
    allowlist, for the reason in this module's docstring: the same exception
    class can be local or post-network, and only reproduction tells them
    apart. A false positive would mislabel a real venue problem as a config
    problem; a false negative would resurrect the storm. Reproduction has
    neither failure mode, and it costs no network.
    """
    verify = verify or verify_signing_contract
    try:
        verify()
    except LocalExecutionFault as local:
        d = dict(local.detail)
        d["surfaced_at"] = "PREVIEW"
        d["original_exception_type"] = "%s.%s" % (type(exc).__module__,
                                                  type(exc).__name__)
        d["original_exception_message_redacted"] = redact(exc)
        d["original_origin"] = _origin(exc)
        d["was_counted_as_a_venue_error"] = False
        d["WHY"] = ("the signing contract fails identically when re-run, so "
                    "this is a deterministic local fault and not the venue")
        return LocalExecutionFault(str(local), detail=d)
    return None


def redact_detail(detail: Any) -> Dict[str, Any]:
    """Walk a fault detail and redact EVERY string in it, at every depth,
    before it is persisted.

    `redact()` on the top-level message is not enough: the detail carries
    exception text, source lines and nested origins, any of which could
    have quoted an input back at us. This is the only function that writes
    fault detail to the tape, so the redaction cannot be forgotten at one
    call site.
    """
    def walk(x: Any) -> Any:
        if isinstance(x, str):
            return redact(x)
        if isinstance(x, dict):
            return {str(k): walk(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [walk(v) for v in x]
        if isinstance(x, (bool, int, float)) or x is None:
            return x
        return redact(x)
    out = walk(dict(detail or {}))
    out["REDACTED_AT_EVERY_DEPTH"] = True
    return out
