"""Serves the halt latch over HTTP, GET only, so the executor can read it.

WHY THE WATCHDOG HOSTS IT. A Railway volume attaches to exactly one service.
The latch must be durable, must be written only by the watchdog, and must be
readable by the executor. Giving the volume to the WATCHDOG and letting the
executor GET over private networking satisfies all three at once. The reverse
-- the executor hosting the latch -- would put a write path into the
money-capable service, which is the thing being guarded.

GET ONLY, AND STRUCTURALLY. `do_POST`, `do_PUT` and `do_DELETE` are not
defined, so `BaseHTTPRequestHandler` answers 501 to all of them: there is no
code here that could be persuaded to write a halt, let alone clear one.
The one served path is the one key `HaltGate` reads.

A MISSING LATCH IS A 404, NOT AN EMPTY SUCCESS. `HttpHaltStore` maps every
failure -- including 404 -- to "could not be established", and `HaltGate`
turns that into "not permitted". So serving nothing fails closed, which is
why this file never invents a body.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from src import l5_halt_gate as HG
from src import l5_mandate_signature as MS

#: THE SERVED KEYS, AS AN EXACT ALLOWLIST. Two reasons it is a list of exact
#: names rather than "whatever is on the volume": the volume also holds the
#: watchdog's own working state, and `FileStore._path` maps a key straight
#: onto a filesystem path, so an unconstrained key would be a traversal.
#:
#: The two mandate documents are served here because the executor's
#: `HttpSeqFloorStore` and `HttpRevocationStore` read them, and because the
#: watchdog is the only process that may write them -- the same asymmetry
#: this file already establishes for the latch, one key over.
SERVED_KEYS = (HG.HALT_KEY, MS.FLOOR_KEY, MS.REVOCATION_KEY)


def make_handler(store: Any):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "l5-halt/1"

        def do_GET(self) -> None:                      # noqa: N802
            path = self.path.split("?", 1)[0].lstrip("/")
            if path in ("health", "healthz"):
                return self._send(200, b'{"ok":true}')
            if path not in SERVED_KEYS:
                return self._send(404, b'{"error":"no such key"}')
            try:
                raw = store.get(path)
            except BaseException:                      # noqa: BLE001
                # a store that cannot be read is not an empty store
                return self._send(503, b'{"error":"store unreadable"}')
            if raw is None:
                return self._send(404, b'{"error":"no halt record"}')
            return self._send(200, raw)

        def _send(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a) -> None:             # quiet by default
            return

    return Handler


def serve(store: Any, *, port: int = 8080, background: bool = True):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(store))
    if not background:
        httpd.serve_forever()
        return httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
