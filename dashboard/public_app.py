"""Public read-only entrypoint for ARB Command Center.

Dashboard read routes are intentionally public for easy viewing. This module removes
the dashboard Basic Auth dependency from those GET endpoints at startup instead of
trying to satisfy Basic Auth by rewriting request headers.

POST /api/events is untouched and remains protected by LEDGER_INGEST_TOKEN bearer
authentication, so public viewers cannot write trades or P&L into the ledger.
"""
from __future__ import annotations

from typing import Any

from app import app, require_dashboard_auth


PUBLIC_READ_PATHS = {
    "/",
    "/api/trades",
    "/api/summary",
    "/api/export.csv",
}


def _public_dashboard_reader() -> str:
    return "public"


# FastAPI has already compiled the endpoint dependency graph while importing app.py.
# Replace only the read-side Basic Auth dependency in that compiled graph. Clearing
# the dependency's children is important because HTTPBasic itself is a nested
# dependency that would otherwise still emit a browser WWW-Authenticate challenge.
for route in app.routes:
    if getattr(route, "path", None) not in PUBLIC_READ_PATHS:
        continue
    dependant: Any = getattr(route, "dependant", None)
    if dependant is None:
        continue
    for dependency in dependant.dependencies:
        if dependency.call is require_dashboard_auth:
            dependency.call = _public_dashboard_reader
            dependency.dependencies = []
