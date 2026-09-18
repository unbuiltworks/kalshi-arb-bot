"""The venue-confirmed WebSocket order path, as a transport for the reviewed canary.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT.

It is a TRANSPORT ADAPTER. `src/gemini_execution/canary.py` already holds the
reviewed one-order machinery -- dual-key arming, the durable one-shot latch,
the fresh preflight, the frozen envelope, rejection capture, ambiguity as a
terminal state, and the strict accounting vocabulary. All of that is
transport-agnostic except for a single call, `tr.place(...)`. So the only thing
this module supplies is that call, spoken in the WebSocket dialect.

IT LIVES IN THIS PACKAGE BECAUSE IT MUTATES. `src/gemini_execution/` is the
only package in the repository permitted to reach a venue mutation path, and
`tests/test_gemini_write_path_isolation.py` enforces that repo-wide. An earlier
draft of this module sat at `src/gemini_l5_ws_canary.py`, outside the package,
which broke that invariant in three places at once: a module that can place an
order is a mutation surface wherever it is filed.

It is NOT a second canary. An earlier draft of this file re-implemented the
latch with a bare O_EXCL write and its own `submit_once`, which silently gave
up the durable_check that makes the one-shot guarantee survive an ephemeral CI
runner, the `INTENT_PERSISTED -> ... -> REALIZED` state vocabulary, and the
terminal-evidence record. Re-implementing reviewed safety code is how a second,
weaker rule quietly replaces the first.

WHY THE WEBSOCKET AND NOT REST. Every order this account has ever had accepted
went over `wss://ws.gemini.com`, `method: "order.place"`, through
`GeminiMakerSession`. No REST order has ever succeeded on these credentials;
the single REST attempt is canary #1 and it was answered HTTP 400. See
`analysis/gemini/PRIOR_ORDER_PATH_RECOVERY.md`.

THE TWO DIALECTS ARE ONE ORDER MODEL. The venue's own order history spells
`side`, `outcome` and `orderType` in lower case whichever transport created the
order. The WebSocket request dialect spells them `side: "BUY"`,
`eventOutcome: "YES"`, `type: "LIMIT"`. This adapter takes the canonical lower
-case intent the envelope produces and emits the WS spelling, so the envelope
never has to know which transport it is feeding.

MAKER-ONLY IS `timeInForce: "MOC"` HERE. Over REST it is the boolean
`makerOrCancel`; there is no `makerOrCancel` field in the WS dialect and no
`MOC` value in the REST `timeInForce` enum. Sending the REST spelling over this
socket would post an order with no post-only protection at all, which is the
one thing the envelope forbids at the venue level.

CLIENT ORDER IDS ARE ADMITTED HERE, AND THAT IS A REAL DIFFERENCE. The REST
`OrderRequest` schema does not list `clientOrderId` and `transport.place`
refuses one outright. The WS dialect does admit it: the venue stores it and
returns it as a first-class field on orders this account placed. It is the
reconciliation key REST does not have, so this adapter uses it.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, Mapping, Optional

from src.gemini_execution import arming, spec

#: a response we could not classify. Never a success and never a retry.
AMBIGUOUS = "AMBIGUOUS"

#: the WS request dialect. These are NOT the REST spellings; see the module
#: docstring. Nothing here is tunable by a caller.
WS_METHOD = "order.place"
WS_ORDER_TYPE = "LIMIT"
WS_MAKER_TIF = "MOC"
WS_SIDE_BUY = "BUY"
WS_OUTCOME_YES = "YES"

#: the venue's own ack field, and the value that means accepted.
ACK_STATUS_FIELD = "status"
ACK_ACCEPTED = 200

#: order-history statuses, queried in this order. `active` FIRST and not as an
#: afterthought: a resting maker order is the EXPECTED outcome of this canary
#: and it is neither filled nor cancelled, so a reconciliation that asks only
#: those two reports "not found" for the success case and cannot tell it apart
#: from an order that never existed. This ordering is taken from the proven
#: `_order_by_client` in `gemini_kalshi_xrp_maker_live_canary`.
ACTIVE_PATH = "/v1/prediction-markets/orders/active"
HISTORY_PATH = "/v1/prediction-markets/orders/history"
HISTORY_STATUSES = ("filled", "cancelled")

#: What the recovered evidence does and does not establish about THIS
#: transport. Stated as data so a report cannot quietly round it up to
#: "proven"; see `analysis/gemini/PRIOR_ORDER_PATH_RECOVERY.md`.
WS_TRANSPORT_EVIDENCE: Dict[str, Any] = {
    "venue_write_path": "PROVEN",
    "proven_by": {
        "orderId": "145828835409069189",
        "symbol": "GEMI-MLB-2608192240-STL-CIN-PPHITS-NCHURCHO1",
        "status": "filled", "price": "0.64", "quantity": "1",
        "createdAt": "2026-08-19T23:42:58.121Z",
    },
    "fractional_quantity_over_this_transport": "UNPROVEN",
    "fractional_quantity_why": (
        "the BTC5m contract's own metadata admits 0.01 (quantityMinimum 0.01, "
        "quantityIncrement 0.01), so the SIZE is contract-valid. But every "
        "order ever accepted on these credentials was quantity '1', and no "
        "fractional quantity has traversed this socket. Contract-valid is not "
        "transport-proven, and the two are recorded apart."),
    "maker_only_over_this_transport": "UNPROVEN",
    "maker_only_why": (
        "both recovered rows are fill-or-kill, i.e. TAKER orders. The MOC "
        "maker path is the one the live modules were written against, but a "
        "venue-ACCEPTED MOC order is not in the recovered evidence."),
    "labels": (
        "'PROVEN' here means only that this socket has carried an accepted "
        "order. It does not extend to the size or the maker semantics this "
        "canary would use; those are the two things the canary would newly "
        "establish."),
}


class GeminiMakerSession:
    """The reviewed historical transport shape, isolated from strategy logic.

    Kept structurally identical to the session every accepted order went
    through: authenticate once at the handshake, subscribe `orders@account`,
    then send plain JSON request frames on the open socket.
    """

    def __init__(self) -> None:
        self.ws = None
        self.reader: Optional[asyncio.Task] = None
        self.responses: Dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        from src.gemini_prediction_auth import authenticated_ws_connect
        self.ws = await authenticated_ws_connect(timeout=5.0)
        self.reader = asyncio.create_task(self._read())
        ack = await self.request("SUBSCRIBE", ["orders@account"], timeout=5.0)
        if int(ack.get(ACK_STATUS_FIELD, 0)) != ACK_ACCEPTED:
            raise RuntimeError(
                "Gemini orders@account subscription failed; without it a fill "
                "would not be observable and nothing may be sent")

    async def close(self) -> None:
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def _read(self) -> None:
        assert self.ws is not None
        async for raw in self.ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, Mapping):
                continue
            rid = str(msg.get("id") or "")
            if rid:
                fut = self.responses.get(rid)
                if fut is not None and not fut.done():
                    fut.set_result(dict(msg))
                continue
            if msg.get("e") == "orderUpdate":
                await self.events.put(dict(msg))

    async def request(self, method: str, params: Any, *,
                      timeout: float = 4.0) -> Dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("Gemini maker session not connected")
        rid = "l5-" + uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.responses[rid] = fut
        try:
            async with self.lock:
                await self.ws.send(json.dumps(
                    {"id": rid, "method": method, "params": params},
                    separators=(",", ":")))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.responses.pop(rid, None)


def ws_order_params(*, symbol: str, side: str, outcome: str, price: str,
                    quantity: str, client_order_id: str) -> Dict[str, Any]:
    """Translate the canonical lower-case intent into the WS request dialect.

    The envelope in `gemini_execution.canary` emits the venue's canonical
    spelling (`buy`, `yes`). Only this function knows the socket's spelling,
    so the envelope stays transport-independent.
    """
    if str(side).lower() != spec.SIDE_BUY:
        raise ValueError("this canary is BUY only; got %r" % (side,))
    if str(outcome).lower() != spec.OUTCOME_YES:
        raise ValueError("this canary is outcome YES only; got %r" % (outcome,))
    if not str(symbol).startswith("GEMI-BTC05M"):
        raise ValueError("this canary is BTC5m only; got %r" % (symbol,))
    return {
        "symbol": str(symbol),
        "side": WS_SIDE_BUY,
        "type": WS_ORDER_TYPE,
        "timeInForce": WS_MAKER_TIF,       # maker-only; NOT makerOrCancel here
        "price": str(price),
        "quantity": str(quantity),
        "eventOutcome": WS_OUTCOME_YES,
        "clientOrderId": str(client_order_id)[:36],
    }


def sanitized_ws_frame(*, symbol: str, price: str, quantity: str,
                       side: str = spec.SIDE_BUY,
                       outcome: str = spec.OUTCOME_YES) -> Dict[str, Any]:
    """The exact frame that would go on the wire, for owner review.

    Only the request id and the client order id are placeholders -- both are
    generated per attempt. Nothing is redacted, because nothing secret is
    present: the API key, the signature and the secret travel in the handshake
    headers and never appear in a frame.
    """
    return {
        "id": "<request-id>",
        "method": WS_METHOD,
        "params": ws_order_params(symbol=symbol, side=side, outcome=outcome,
                                  price=price, quantity=quantity,
                                  client_order_id="<client-order-id>"),
    }


def _ack_reason(ack: Mapping[str, Any]) -> Dict[str, Any]:
    """Whatever the venue said about WHY, without inventing structure.

    Canary #1 recorded only its status code and left no way to say why it was
    refused; that is the defect this exists to prevent. Everything the ack
    carries other than the echoed request id is kept.
    """
    named = {k: ack.get(k) for k in ("result", "reason", "message", "error",
                                     "data")
             if ack.get(k) is not None}
    return named or {"ack_keys": sorted(str(k) for k in ack)}


def reconcile_queries(symbol: str, *, now: Optional[float] = None
                      ) -> list:
    """The authoritative reads, IN ORDER. Active orders first.

    Exposed rather than inlined so the ordering is checkable: a readiness
    report that merely asserted "active is asked first" would be asserting its
    own comment.
    """
    now_ms = int((now if now is not None else time.time()) * 1000)
    queries = [(ACTIVE_PATH, {"symbol": symbol, "limit": 100, "offset": 0})]
    for status in HISTORY_STATUSES:
        queries.append((HISTORY_PATH, {
            "status": status, "symbol": symbol, "limit": 100,
            "from": now_ms - 120000, "to": now_ms + 2000}))
    return queries


async def _order_by_client(client_id: str, symbol: str, *,
                           rest=None) -> Optional[Dict[str, Any]]:
    """Authoritative order-history lookup. Active orders FIRST.

    A resting MOC maker order is the expected success of this canary and it is
    neither filled nor cancelled, so `active` has to be asked first or the
    success case reads as absent.
    """
    if rest is None:
        from src.gemini_prediction_auth import private_rest_request as rest
    for path, params in reconcile_queries(symbol):
        try:
            response = await rest("POST", path, params=params, timeout=4.0)
        except Exception:                                      # noqa: BLE001
            continue                    # an unreadable page is not an answer
        if response.status_code != 200:
            continue
        try:
            body = response.json()
        except ValueError:
            continue
        rows = body.get("orders", []) if isinstance(body, Mapping) else []
        for row in rows:
            if isinstance(row, Mapping) and \
                    str(row.get("clientOrderId") or "") == client_id:
                return dict(row)
    return None


class WsOrderTransport:
    """`gemini_execution.transport`'s place/find contract, over the socket.

    Instances are cheap and hold no connection until `place` is called. The
    session is opened and closed around the single request, exactly as the
    proven modules did.
    """

    def __init__(self, *, session_factory=GeminiMakerSession, rest=None,
                 reconcile_attempts: int = 20,
                 reconcile_pause_s: float = 0.05) -> None:
        self._session_factory = session_factory
        self._rest = rest
        self._reconcile_attempts = reconcile_attempts
        self._reconcile_pause_s = reconcile_pause_s

    async def _find(self, client_id: str, symbol: str
                    ) -> Optional[Dict[str, Any]]:
        for attempt in range(self._reconcile_attempts):
            row = await _order_by_client(client_id, symbol, rest=self._rest)
            if row is not None:
                return row
            if attempt + 1 < self._reconcile_attempts:
                await asyncio.sleep(self._reconcile_pause_s)
        return None

    async def place(self, *, symbol: str, side: str, outcome: str, price: str,
                    quantity: str, client_order_id: Optional[str] = None,
                    now: Optional[float] = None) -> Dict[str, Any]:
        """Send exactly one `order.place` frame. ARM-GATED.

        Returns the same classified shape `transport.place` returns, so the
        reviewed canary can consume either transport without knowing which it
        has.
        """
        arming.require(now)              # structural: no arm, no frame

        client_id = client_order_id or ("l5ws-" + uuid.uuid4().hex[:24])
        params = ws_order_params(symbol=symbol, side=side, outcome=outcome,
                                 price=price, quantity=quantity,
                                 client_order_id=client_id)
        out: Dict[str, Any] = {"request": {"method": WS_METHOD,
                                           "params": params},
                               "client_order_id": client_id,
                               "transport": "WEBSOCKET"}

        session = self._session_factory()
        try:
            try:
                await session.connect()
            except Exception as exc:                           # noqa: BLE001
                # Nothing was sent: the socket never carried a frame. This is
                # a refusal, not an ambiguity, and saying so keeps a failed
                # handshake out of the reconcile-forever bucket.
                out.update({"ok": False, "classification": "REJECTED",
                            "status": None,
                            "body": {"result": "error",
                                     "reason": "TRANSPORT_CONNECT_FAILED",
                                     "message": "%s: %s" % (
                                         type(exc).__name__, str(exc)[:200])},
                            "sent": False})
                return out

            try:
                ack = await session.request(WS_METHOD, params, timeout=4.0)
            except Exception as exc:                           # noqa: BLE001
                # The frame may or may not have reached the venue. Terminal,
                # and resolved by an authoritative READ -- never another send.
                out.update({"ok": False, "classification": AMBIGUOUS,
                            "status": None, "body": None,
                            "error": type(exc).__name__,
                            "detail": str(exc)[:200], "sent": True})
                return out

            out["ack"] = dict(ack)
            raw_status = ack.get(ACK_STATUS_FIELD)
            if raw_status is None:
                # An ack with no status is not a rejection. Treating a missing
                # field as "not 200" would call an accepted order refused.
                out.update({"ok": False, "classification": AMBIGUOUS,
                            "status": None, "body": None,
                            "error": "ACK_WITHOUT_STATUS",
                            "detail": _ack_reason(ack), "sent": True})
                return out
            try:
                status = int(raw_status)
            except (TypeError, ValueError):
                out.update({"ok": False, "classification": AMBIGUOUS,
                            "status": raw_status, "body": None,
                            "error": "ACK_STATUS_UNREADABLE",
                            "detail": _ack_reason(ack), "sent": True})
                return out
            out["status"] = status

            if status != ACK_ACCEPTED:
                out.update({"ok": False, "classification": "REJECTED",
                            "body": _ack_reason(ack), "sent": True})
                return out

            # Accepted. The ack's own shape is NOT assumed to carry an order
            # id -- the proven modules never read one from it -- so the venue's
            # order history is the authority for what now exists.
            row = await self._find(client_id, symbol)
            if row is None:
                out.update({
                    "ok": True, "classification": "ACCEPTED",
                    "body": {spec.ORDER_ID_FIELD: None,
                             spec.STATUS_FIELD: None},
                    "reconciliation_unreadable": True,
                    "note": ("the venue ACCEPTED the frame (ack 200) but the "
                             "order could not be read back. The order exists; "
                             "this is a read failure, not an ambiguous send. "
                             "Reconcile, do not resubmit."),
                    "sent": True})
                return out
            out.update({"ok": True, "classification": "ACCEPTED",
                        "body": row, "venue_order": row, "sent": True})
            return out
        finally:
            try:
                await session.close()
            except Exception:                                  # noqa: BLE001
                pass

    async def find_order(self, *, symbol: str,
                         client_order_id: Optional[str] = None,
                         order_id: Any = None) -> Dict[str, Any]:
        """Authoritative read used to resolve an in-flight or ambiguous latch."""
        if client_order_id:
            row = await self._find(client_order_id, symbol)
            if row is not None:
                return {"resolved": True, "order": row, "matched_on":
                        "clientOrderId"}
        if order_id is not None:
            from src.gemini_execution import transport as TR
            return await TR.find_order(symbol=symbol, order_id=order_id)
        return {"resolved": False, "why": (
            "no order carrying this client order id is active, filled or "
            "cancelled on this symbol")}


async def submit_once_ws(auth_id: str, **kwargs) -> Dict[str, Any]:
    """The reviewed one-order canary, driven over the WebSocket transport.

    Everything that makes this safe -- arming, the durable one-shot latch, the
    fresh preflight, the envelope, rejection capture, the strict accounting
    states -- belongs to `gemini_execution.canary` and is NOT duplicated here.
    This only chooses the transport.

    NOTHING IS SUBMITTED unless the process is armed with both keys and an
    unexpired owner authorization; see `gemini_execution/arming.py`.
    """
    from src.gemini_execution import canary as CANARY
    kwargs.setdefault("transport", WsOrderTransport())
    return await CANARY.submit_once(auth_id, **kwargs)
