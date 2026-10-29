"""The executor publishes, READ-ONLY, which venue order ids are its own.

WHY THIS EXISTS. The watchdog's first design identified an unmanaged order by
`GET /v1/orders/open` plus an age test, and the owner was right to refuse it:
**this PMUS account is SHARED**. Account-wide open orders say what is resting,
never whose it is. An age test on that list would have cancelled the owner's
own manual orders -- including D23N1EHTTZ7D, which we were told explicitly
never to touch. `manualOrderIndicator` narrows it but does not settle it
either: every other automated service on this account also submits AUTOMATIC
orders.

Ownership is therefore a fact only the submitter holds, and the submitter
already records it durably: `InventoryStore.live_orders()` carries
`venue_order_id`, `market`, `side` and `session_id` for every order it ACKed,
on a volume that outlives the process. This serves exactly that, over GET.

THE DIRECTION OF THE WRITE IS THE WHOLE POINT. The executor does not write to
the watchdog, and the watchdog does not write to the executor: each exposes a
read and neither can mutate the other. The alternative -- the executor PUTting
its claims into the watchdog's store -- would put a write path from the
money-capable service into the thing that restrains it, and a buggy claim
would then be able to point the watchdog's cancel at a third party's order.

WHAT IS SERVED IS NOT AUTHORIZATION BY ITSELF. A claim here is necessary and
not sufficient: `l5_watchdog` additionally requires the venue's own order row
to agree on the market and to report AUTOMATIC. A MANUAL order can never be
cancelled by that path, however it is claimed.

GET ONLY, STRUCTURALLY: no `do_POST`/`do_PUT`/`do_DELETE` exists, so writes
answer 501. Nothing here can place, cancel or amend an order, and it reads the
ledger without holding a lock on it -- a stale read costs the watchdog a
cancel it will retry, while a lock held against the trading loop would cost a
window.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

#: the one served path.
REGISTRY_KEY = "l5/live_orders.json"

#: unset = not served at all, which is the default and changes nothing.
PORT_ENV = "L5_ORDER_REGISTRY_PORT"


def submission_disabled() -> bool:
    """True when this executor's submit() is STRUCTURALLY blocked.

    THE SAME PREDICATE THE AUTHORIZATION USES, read from the same variable,
    so the published answer cannot drift from the real one:
    `authorize_arming` step 1 refuses SHADOW_ONLY_LOCK on exactly this, and
    the default is LOCKED (unset reads as "1").

    WHY IT IS PUBLISHED. The owner-gated HALT reset must be able to VERIFY --
    not assume -- that nothing can submit while it runs, including across the
    brief watchdog restart the clear costs. The venue's open-order count says
    what is resting NOW; this says nothing new can be sent. The reset refuses
    if this field is missing, because an executor too old to publish it is an
    executor whose lock cannot be established.
    """
    import os
    return str(os.getenv("L5_SHADOW_ONLY", "1")).strip() != "0"


def registry(store: Any) -> Dict[str, Any]:
    """The ids this executor ACKed, with the market each belongs to.

    Only orders the ledger still believes are LIVE. A terminal order needs no
    cancelling, and publishing it would let the watchdog act on an id whose
    fate is already settled.
    """
    rows: List[Dict[str, Any]] = []
    try:
        live = store.live_orders()
    except BaseException:                                      # noqa: BLE001
        # A ledger we cannot read publishes NOTHING, never an empty claim
        # dressed up as a complete one.
        return {"readable": False, "orders": [],
                "submission_disabled": submission_disabled(),
                "labels": ("the ledger could not be read, so ownership is "
                           "UNKNOWN; an empty list here would read as 'we "
                           "own nothing', which is a different claim")}
    for coid, row in sorted(live.items()):
        vid = str((row or {}).get("venue_order_id") or "").strip()
        if not vid:
            continue                 # nothing to name, nothing to publish
        rows.append({"venue_order_id": vid,
                     "market": str(row.get("market") or ""),
                     "side": str(row.get("side") or ""),
                     "session_id": str(row.get("session_id") or ""),
                     "t": str(row.get("t") or "")})
    return {"readable": True, "orders": rows,
            # THE LOCK, PUBLISHED. Read by the owner-gated HALT reset, which
            # refuses unless this is true: a clear must never run while this
            # executor could send a new order.
            "submission_disabled": submission_disabled(),
            # THE REPORT'S OWN AGE, so a credential-free watchdog can tell a
            # STALE report from a fresh empty one. Those are opposite facts:
            # "the executor publishes nothing because it owns nothing" is
            # safe, and "the executor has stopped publishing" is the wedged
            # signal. Without this field they are indistinguishable.
            "as_of_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "labels": ("venue order ids this executor ACKed and still "
                       "believes live. A claim here is NECESSARY and NOT "
                       "SUFFICIENT: the watchdog also requires the venue's "
                       "own row to agree on the market and to report "
                       "AUTOMATIC, so a MANUAL order is never cancellable "
                       "by that path.")}


def make_handler(store: Any):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "l5-order-registry/1"

        def do_GET(self) -> None:                      # noqa: N802
            path = self.path.split("?", 1)[0].lstrip("/")
            if path in ("health", "healthz"):
                return self._send(200, b'{"ok":true}')
            if path != REGISTRY_KEY:
                return self._send(404, b'{"error":"no such key"}')
            body = json.dumps(registry(store), sort_keys=True).encode()
            return self._send(200, body)

        def _send(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a) -> None:
            return

    return Handler


def serve(store: Any, *, port: int, background: bool = True):
    httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), make_handler(store))
    if not background:
        httpd.serve_forever()
        return httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve_from_env(store: Any):
    """Serve only when a port is configured. Unset changes nothing."""
    port = str(os.getenv(PORT_ENV, "")).strip()
    if not port:
        return None
    try:
        return serve(store, port=int(port))
    except BaseException:                                      # noqa: BLE001
        return None
