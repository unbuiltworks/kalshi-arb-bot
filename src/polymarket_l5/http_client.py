"""Hard read-only HTTP client.

Constraints enforced in code, not by convention:
  * HTTPS only, and only hosts in ``config.ALLOWED_HOSTS``.
  * GET only -- there is no code path here that can issue another verb.
  * No Authorization/API-key/signature headers are ever attached.

Uses the standard library so the exporter has no dependency beyond Python
itself; ``urllib`` honours HTTPS_PROXY / SSL_CERT_FILE from the environment.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from . import config
from .cache import RawCache

USER_AGENT = "kalshi-arb-bot/l5-readonly-analyzer (public data, GET only)"


class DisallowedRequest(RuntimeError):
    """Raised when a caller tries to leave the read-only public envelope."""


def _validate(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise DisallowedRequest(f"non-https url rejected: {url}")
    if parts.hostname not in config.ALLOWED_HOSTS:
        raise DisallowedRequest(f"host not on the public allowlist: {parts.hostname}")
    return url


def _encode(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return ""
    clean = {k: v for k, v in params.items() if v is not None}
    return urllib.parse.urlencode(clean, doseq=True)


def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    cache: Optional[RawCache] = None,
    tag: str = "misc",
    timeout: float = config.REQUEST_TIMEOUT_S,
    attempts: int = config.RETRY_ATTEMPTS,
    opener: Any = None,
) -> Tuple[int, Any, str]:
    """GET a JSON document. Returns ``(status, payload, error)``; never raises on HTTP error.

    Every attempt outcome that produces a response (success or HTTP error) is
    written to the raw cache so the run is reproducible and auditable.
    """
    _validate(url)
    query = _encode(params)
    full_url = f"{url}?{query}" if query else url
    fetch = opener or _urlopen_json

    status, payload, error = 0, None, "not attempted"
    for attempt in range(attempts):
        status, payload, error = fetch(full_url, timeout)
        retryable = status == 0 or status == 429 or 500 <= status < 600
        if not retryable:
            break
        if attempt < attempts - 1:
            backoff = config.RETRY_BACKOFF_S[min(attempt, len(config.RETRY_BACKOFF_S) - 1)]
            time.sleep(backoff)

    if cache is not None:
        cache.append(
            tag,
            url=url,
            params=dict(params or {}),
            status=status,
            payload=payload,
            error=error,
        )
    return status, payload, error


def _urlopen_json(full_url: str, timeout: float) -> Tuple[int, Any, str]:
    request = urllib.request.Request(
        full_url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(body), ""
            except json.JSONDecodeError as exc:
                return response.status, None, f"non-json body: {exc}"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
        except Exception:  # pragma: no cover - body is best effort
            pass
        return exc.code, None, f"http {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return 0, None, f"transport error: {exc.reason}"
    except Exception as exc:  # pragma: no cover - defensive
        return 0, None, f"unexpected error: {exc!r}"
