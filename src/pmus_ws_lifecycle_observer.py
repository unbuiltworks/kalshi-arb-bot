"""P1. The private WS order/position stream, as an OBSERVER ONLY.

REST REMAINS AUTHORITATIVE. Nothing in this module books a fill, releases a
reservation, clears an UNKNOWN, or influences a LOCK5 decision. It records
what the private socket said, correlates it with what REST said, and reports
the disagreement. That is the whole contract, and it is enforced by test:
this module imports no money path, no mandate and no engine.

WHY OBSERVER ONLY. The capability probe (2026-09-29) proved
`/v1/ws/private` serves SUBSCRIPTION_TYPE_ORDER, ORDER_SNAPSHOT and
POSITION -- but the snapshot came back EMPTY, because the account held no
open orders. Not one incremental lifecycle event has ever been observed.
Moving authoritative state onto a payload schema nobody has seen would rest
"never infer a fill from disappearance" on an assumption. So: observe first,
measure agreement, and only then propose a cutover.

NEVER INFER A FILL FROM DISAPPEARANCE. An order vanishing from a snapshot is
recorded as `VANISHED_FROM_SNAPSHOT` and is explicitly NOT a fill, not a
cancel, and not terminal. Only an event that states a quantity is treated as
stating a quantity, and even then only REST may act on it.
"""
from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

PRIVATE_PATH = "/v1/ws/private"
PRIVATE_URL = "wss://api.polymarket.us" + PRIVATE_PATH

#: measured on the live venue 2026-09-29. The private socket has its OWN
#: enum -- these integers do NOT mean what they mean on /v1/ws/markets.
SUB_ORDER = 1              # accepted; live order events (inferred)
SUB_ORDER_SNAPSHOT = 2     # returned {"orders": [], "eof": true}
SUB_POSITION = 3           # accepted (inferred)
#: string forms the venue recognised, one fresh socket each
NAME_ORDER = "SUBSCRIPTION_TYPE_ORDER"
NAME_ORDER_SNAPSHOT = "SUBSCRIPTION_TYPE_ORDER_SNAPSHOT"
NAME_POSITION = "SUBSCRIPTION_TYPE_POSITION"

#: the only frame keys this module may ever emit
ALLOWED_KEYS = frozenset({"subscribe", "unsubscribe"})


def subscribe_frames() -> List[Dict[str, Any]]:
    """Snapshot first, then the live stream, then positions.

    Snapshot before stream so a reconnect has a known starting point; the
    ordering is recorded with the events so a gap between them is visible
    rather than silently healed.
    """
    return [
        {"subscribe": {"request_id": "snap-" + uuid.uuid4().hex[:8],
                       "subscription_type": NAME_ORDER_SNAPSHOT}},
        {"subscribe": {"request_id": "ord-" + uuid.uuid4().hex[:8],
                       "subscription_type": NAME_ORDER}},
        {"subscribe": {"request_id": "pos-" + uuid.uuid4().hex[:8],
                       "subscription_type": NAME_POSITION}},
    ]


def guard_outbound(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    bad = set(frame) - ALLOWED_KEYS
    if bad:
        raise RuntimeError("blocked non-read-only outbound frame keys: %s"
                           % (sorted(bad),))
    return frame


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, Mapping):
        v = v.get("value", v.get("px"))
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def order_id_of(rec: Mapping[str, Any]) -> Optional[str]:
    for k in ("id", "orderId", "order_id", "clientOrderId"):
        v = rec.get(k)
        if v:
            return str(v)
    return None


def extract_orders(msg: Any) -> List[Dict[str, Any]]:
    """Every order-shaped record in a private frame, with its envelope kind.

    Tolerant by design: the payload schema is UNOBSERVED, so this collects
    anything carrying an order id plus a state or quantity, records the raw
    record verbatim alongside, and never fails the caller on a shape it does
    not recognise.
    """
    out: List[Dict[str, Any]] = []

    def walk(node: Any, kind: str) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x, kind)
            return
        if not isinstance(node, Mapping):
            return
        k = str(node.get("subscriptionType") or kind or "")
        oid = order_id_of(node)
        if oid and ("state" in node or "cumQuantity" in node
                    or "cum_quantity" in node or "quantity" in node):
            out.append({"order_id": oid, "envelope": k, "raw": dict(node)})
        for v in node.values():
            walk(v, k)

    walk(msg, "")
    return out


class LifecycleObserver:
    """Records private-WS lifecycle and correlates it with REST.

    Holds NO authority. `disagreements()` is the deliverable.
    """

    def __init__(self, *, clock=time.time) -> None:
        self.clock = clock
        self.events: List[Dict[str, Any]] = []
        self.rest: Dict[str, Dict[str, Any]] = {}
        self.snapshots = 0
        self.reconnects = 0
        self.last_snapshot_ids: List[str] = []
        self.vanished: List[Dict[str, Any]] = []
        self.duplicates = 0
        self.out_of_order = 0
        self._seen: Dict[str, int] = {}
        self._seq = 0

    # ---- ingest -------------------------------------------------------
    def on_ws(self, msg: Any) -> List[Dict[str, Any]]:
        """One private frame in. Returns the rows it produced."""
        now = self.clock()
        rows = []
        is_snapshot = "orderSubscriptionSnapshot" in json.dumps(
            msg, default=str)[:4000] if msg is not None else False
        for rec in extract_orders(msg):
            self._seq += 1
            oid = rec["order_id"]
            raw = rec["raw"]
            cum = _d(raw.get("cumQuantity", raw.get("cum_quantity")))
            row = {
                "seq": self._seq, "ws_t": now, "order_id": oid,
                "envelope": rec["envelope"], "snapshot": bool(is_snapshot),
                "state": raw.get("state"),
                "cum_quantity": None if cum is None else str(cum),
                "avg_px": (lambda v: None if v is None else str(v))(
                    _d(raw.get("avgPx", raw.get("avg_px")))),
                "commission": (lambda v: None if v is None else str(v))(
                    _d(raw.get("commissionNotionalTotalCollected",
                               raw.get("commission")))),
                "raw": raw,
            }
            prev = self._seen.get(oid)
            if prev is not None and cum is not None:
                pc = _d(self.events[prev].get("cum_quantity"))
                if pc is not None and cum < pc:
                    # cumulative quantity is monotonic; a decrease is either
                    # out-of-order delivery or a schema we misread. Recorded,
                    # never acted on.
                    self.out_of_order += 1
                    row["anomaly"] = "CUM_QUANTITY_WENT_BACKWARDS"
                elif pc is not None and cum == pc and row["state"] == \
                        self.events[prev].get("state"):
                    self.duplicates += 1
                    row["anomaly"] = "DUPLICATE"
            self.events.append(row)
            self._seen[oid] = len(self.events) - 1
            rows.append(row)
        if is_snapshot:
            self.snapshots += 1
            ids = [r["order_id"] for r in rows]
            for gone in set(self.last_snapshot_ids) - set(ids):
                # NEVER A FILL. An order absent from a snapshot has not been
                # shown to have done anything.
                self.vanished.append({
                    "order_id": gone, "ws_t": now,
                    "classification": "VANISHED_FROM_SNAPSHOT",
                    "note": ("absence is not evidence: this is NOT a fill, "
                             "NOT a cancel and NOT terminal. REST decides.")})
            self.last_snapshot_ids = ids
        return rows

    def on_rest(self, order_id: str, verdict: Mapping[str, Any]) -> None:
        """The AUTHORITATIVE answer for one order, with its arrival time."""
        self.rest[str(order_id)] = {"rest_t": self.clock(),
                                    "verdict": dict(verdict)}

    def on_reconnect(self) -> None:
        self.reconnects += 1

    # ---- the deliverable ----------------------------------------------
    def correlate(self) -> List[Dict[str, Any]]:
        """Per order: WS's last word, REST's word, and every disagreement."""
        out = []
        last: Dict[str, Dict[str, Any]] = {}
        first: Dict[str, Dict[str, Any]] = {}
        for e in self.events:
            last[e["order_id"]] = e
            first.setdefault(e["order_id"], e)
        for oid in sorted(set(last) | set(self.rest)):
            w, r = last.get(oid), self.rest.get(oid)
            rv = (r or {}).get("verdict") or {}
            row: Dict[str, Any] = {
                "order_id": oid,
                "ws_seen": w is not None,
                "rest_seen": r is not None,
                "ws_first_t": (first.get(oid) or {}).get("ws_t"),
                "ws_last_t": (w or {}).get("ws_t"),
                "rest_t": (r or {}).get("rest_t"),
            }
            if w and r:
                row["ws_to_rest_latency_s"] = round(
                    r["rest_t"] - first[oid]["ws_t"], 4)
            for field, wk, rk in (("state", "state", "state"),
                                  ("cum_quantity", "cum_quantity", "filled_qty"),
                                  ("avg_px", "avg_px", "price_own"),
                                  ("commission", "commission", "commission")):
                wv = (w or {}).get(wk)
                rvv = rv.get(rk)
                agree: Optional[bool]
                if wv is None or rvv is None:
                    agree = None            # not comparable, not a mismatch
                elif field in ("cum_quantity", "avg_px", "commission"):
                    a, b = _d(wv), _d(rvv)
                    agree = None if (a is None or b is None) else (a == b)
                else:
                    agree = str(wv) == str(rvv)
                row[field] = {"ws": wv, "rest": rvv, "agree": agree}
            out.append(row)
        return out

    def disagreements(self) -> List[Dict[str, Any]]:
        return [r for r in self.correlate()
                if any(isinstance(r.get(f), dict) and r[f]["agree"] is False
                       for f in ("state", "cum_quantity", "avg_px",
                                 "commission"))]

    def report(self) -> Dict[str, Any]:
        corr = self.correlate()
        def tally(field):
            ok = sum(1 for r in corr if isinstance(r.get(field), dict)
                     and r[field]["agree"] is True)
            bad = sum(1 for r in corr if isinstance(r.get(field), dict)
                      and r[field]["agree"] is False)
            na = len(corr) - ok - bad
            return {"agree": ok, "disagree": bad, "not_comparable": na}
        lat = [r["ws_to_rest_latency_s"] for r in corr
               if r.get("ws_to_rest_latency_s") is not None]
        lat.sort()
        return {
            "LABEL": "WS_LIFECYCLE_OBSERVER",
            "authority": "REST",
            "ws_events": len(self.events),
            "orders_ws": len({e["order_id"] for e in self.events}),
            "orders_rest": len(self.rest),
            "ws_only": sorted(r["order_id"] for r in corr
                              if r["ws_seen"] and not r["rest_seen"]),
            "rest_only": sorted(r["order_id"] for r in corr
                                if r["rest_seen"] and not r["ws_seen"]),
            "snapshots": self.snapshots,
            "reconnects": self.reconnects,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "vanished_from_snapshot": self.vanished,
            "agreement": {f: tally(f) for f in
                          ("state", "cum_quantity", "avg_px", "commission")},
            "ws_to_rest_latency_s": {
                "n": len(lat),
                "median": lat[len(lat) // 2] if lat else None,
                "p95": lat[int(0.95 * len(lat))] if lat else None,
                "max": lat[-1] if lat else None},
            "disagreements": self.disagreements(),
            "labels": ("REST is authoritative. Nothing here books a fill or "
                       "clears an UNKNOWN. An order absent from a snapshot is "
                       "VANISHED_FROM_SNAPSHOT, which is not a fill."),
        }


class PrivateStream:
    """Background reader for /v1/ws/private. Feeds a LifecycleObserver.

    Owns its own socket and thread so the production wiring is two lines:
    construct, and stop. It sends only subscribe frames, holds no reference
    to the money path, and its ONLY output is `observer.on_ws`, which
    records. A failure here degrades observation and nothing else: the
    thread dies, `health()["connected"]` goes False, and the trading path
    does not notice, because REST is the authoritative source.
    """

    def __init__(self, observer: "LifecycleObserver", *,
                 auth_headers=None, url: str = PRIVATE_URL,
                 path: str = PRIVATE_PATH) -> None:
        self.observer = observer
        self.url = url
        self.path = path
        self._auth = auth_headers
        self.connected = False
        self.frames = 0
        self.errors: List[str] = []
        self.sessions = 0
        self._stop = None
        self._thread = None

    def health(self) -> Dict[str, Any]:
        return {"stream": "PMUS_WS_PRIVATE", "url": self.url,
                "connected": self.connected, "frames": self.frames,
                "sessions": self.sessions, "errors": self.errors[-3:],
                "authority": "NONE -- REST is authoritative"}

    def start(self) -> None:
        import threading
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="pmus-ws-private")
        self._thread.start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()

    # -------------------------------------------------------------- thread
    def _run(self) -> None:                                # pragma: no cover
        import asyncio
        while self._stop is not None and not self._stop.is_set():
            try:
                asyncio.run(self._session())
            except BaseException as exc:                   # noqa: BLE001
                self.errors.append("%s: %s" % (type(exc).__name__,
                                               str(exc)[:200]))
            self.connected = False
            if self._stop is not None and not self._stop.is_set():
                self.observer.on_reconnect()
                time.sleep(2.0)

    async def _session(self) -> None:                      # pragma: no cover
        import asyncio
        import websockets
        headers = self._auth() if callable(self._auth) else (self._auth or {})
        async with websockets.connect(self.url, additional_headers=headers,
                                      max_size=4 * 1024 * 1024,
                                      open_timeout=15) as ws:
            self.connected = True
            self.sessions += 1
            for frame in subscribe_frames():
                await ws.send(json.dumps(guard_outbound(frame)))
            while self._stop is not None and not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                self.frames += 1
                try:
                    msg = json.loads(raw)
                except BaseException:                      # noqa: BLE001
                    continue
                try:
                    self.observer.on_ws(msg)
                except BaseException as exc:               # noqa: BLE001
                    # observation must never raise into the trading process
                    self.errors.append("on_ws: %s" % (str(exc)[:160],))
