"""Public read-only wrapper for ARB Command Center.

Dashboard GET routes are intentionally public for easy viewing. The underlying app
still retains its existing Basic Auth dependency, but this wrapper satisfies it
server-side with Railway environment credentials so browsers are never prompted.

Non-GET requests are passed through unchanged. In particular, POST /api/events
remains protected by the separate LEDGER_INGEST_TOKEN bearer token.
"""
from __future__ import annotations

import base64
import os
from typing import Any

from app import app as protected_app


class PublicReadOnlyDashboard:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        username = os.environ["DASHBOARD_USERNAME"]
        password = os.environ["DASHBOARD_PASSWORD"]
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.authorization = f"Basic {token}".encode("ascii")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("method") in {"GET", "HEAD"}:
            # The public dashboard is read-only. Replace any caller Authorization
            # header only for reads so the browser never receives a Basic Auth
            # challenge. Writes remain fully protected by their original auth.
            public_scope = dict(scope)
            headers = [
                (key, value)
                for key, value in scope.get("headers", [])
                if key.lower() != b"authorization"
            ]
            headers.append((b"authorization", self.authorization))
            public_scope["headers"] = headers
            scope = public_scope
        await self.inner(scope, receive, send)


app = PublicReadOnlyDashboard(protected_app)
