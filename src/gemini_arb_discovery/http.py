"""Read-only public HTTP for api.gemini.com.

PROVENANCE. The failure-as-a-result contract and the proxy CA handling here are
reused from `src/l5_shadow/venues.py` on the branch
`research/gemini-btc5m-information-edge`, which established that a venue we
could not reach must read as "unknown", never "absent": collapsing the two
silently lets a scanner report a market as having no book when in fact the
request failed.

Only GET is reachable from this module. There is no POST path and no credential.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

GEMINI = "https://api.gemini.com"
UA = {"User-Agent": "gemini-arb-discovery/0.1 (read-only research)"}
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    try:
        c.load_verify_locations(CA_BUNDLE)
    except OSError:
        pass  # not in the proxied environment; fall back to system CAs
    return c


def get(path_or_url: str, timeout: float = 20.0) -> Dict[str, Any]:
    """{ok, body, status, recv, rtt_s, err}. A failure is a RESULT with a reason."""
    url = path_or_url if path_or_url.startswith("http") else GEMINI + path_or_url
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as fh:
            return {"ok": True, "status": fh.status, "body": fh.read().decode("utf-8"),
                    "recv": time.time(), "rtt_s": round(time.time() - t0, 3), "err": None}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": None, "recv": time.time(),
                "rtt_s": round(time.time() - t0, 3), "err": "http_%d" % exc.code}
    except Exception as exc:  # noqa: BLE001 - a reason, not a crash
        return {"ok": False, "status": None, "body": None, "recv": time.time(),
                "rtt_s": round(time.time() - t0, 3), "err": type(exc).__name__}


def get_json(path_or_url: str, timeout: float = 20.0) -> Dict[str, Any]:
    r = get(path_or_url, timeout=timeout)
    if not r["ok"]:
        return {"readable": False, "err": r["err"], "status": r["status"]}
    try:
        return {"readable": True, "json": json.loads(r["body"]), "recv": r["recv"],
                "rtt_s": r["rtt_s"]}
    except ValueError:
        return {"readable": False, "err": "unparseable", "status": r["status"]}
