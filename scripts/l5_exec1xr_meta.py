"""Read-only market metadata for the XR runner.

Kept separate from the runner so the runner's own module imports nothing that
can mutate beyond `orders`' four routes, and separate from the preflight so
the execution image does not have to carry a second entrypoint.

Same two resources the validated preflight uses. The order-capable legacy
transport module is deliberately not imported.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from src import pmus_auth as A


def event_slug_for(slug: str) -> str:
    return slug[4:] if slug.startswith("cpc-") else slug


def resolve_market(http: Any, slug: str) -> Dict[str, Any]:
    for host in (A.API, A.GATEWAY):
        for path, pick in (("/v1/market/slug/%s" % slug, "market"),
                           ("/v1/events/slug/%s" % event_slug_for(slug),
                            "event")):
            try:
                r = http.request("GET", path, None, host) or {}
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:                              # noqa: BLE001
                continue
            obj = r.get(pick) or r
            if pick == "market":
                if isinstance(obj, Mapping) and obj.get("slug") == slug:
                    return dict(obj)
            else:
                for mk in (obj.get("markets") or []):
                    if isinstance(mk, Mapping) and mk.get("slug") == slug:
                        return dict(mk)
    raise LookupError("market metadata not served for %s" % slug)
