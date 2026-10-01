"""EXECUTABLE entrypoint for the authorized private-WS schema probe.

    python3 -m scripts.pmus_schema_probe_run

A THIN ORCHESTRATION WRAPPER, not a new executor. Every money-touching
primitive is reused from code that already ran live:

    src.pmus_q1_executor      Transport, fresh_bbo, would_cross, persist_intent,
                              _get_with_retry, QTY
    src.l5_order_body         build + preview_envelope (the proven serializer)
    src.pmus_q1_canary        cancel_and_resolve, open_order_ids,
                              terminal_no_fill, _poll_fill, record, Inventory
    src.pmus_ws_lifecycle_observer   /v1/ws/private subscribe frames + guard
    src.pmus_ws_evidence_recorder    auth_headers, market subscribe + guard
    src.pmus_private_schema_probe    arming, probe_price, preflight, FrameLedger

SOLE OBJECTIVE: capture the RAW /v1/ws/private frames for one real resting
order. A fill is a FAILURE. Maximum economic exposure is one cent.

EXIT CODE IS A SAFETY SIGNAL: 0 only when the terminal assertions pass -- the
order positively resolved and the venue reports zero open orders in the market.
Anything else is nonzero.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_order_body as OB                     # noqa: E402
from src import pmus_private_schema_probe as P          # noqa: E402
from src import pmus_q1_canary as C                     # noqa: E402
from src import pmus_q1_executor as X                   # noqa: E402
from src import pmus_ws_evidence_recorder as R          # noqa: E402
from src import pmus_ws_lifecycle_observer as PV        # noqa: E402

READY_TIMEOUT_S = 45.0
REST_OPEN_TIMEOUT_S = 20.0
#: enough private evidence to stop early: an accept/open frame naming our order
EVIDENCE_FRAMES = 1

_STOP = threading.Event()


class SocketReader(threading.Thread):
    """Read one authenticated socket and append raw frames to a ledger.

    Outbound frames pass the module's own guard, so this reader can only ever
    subscribe and unsubscribe.
    """

    def __init__(self, name: str, url: str, path: str, frames: List[dict],
                 ledger: P.FrameLedger, guard, subscribe) -> None:
        super().__init__(name=name, daemon=True)
        self.url, self.path = url, path
        self.frames, self.ledger = frames, ledger
        self.guard, self.subscribe = guard, subscribe
        self.error: Optional[str] = None

    def run(self) -> None:                              # pragma: no cover
        try:
            import asyncio
            asyncio.run(self._session())
        except Exception as exc:                        # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"

    async def _session(self) -> None:                   # pragma: no cover
        import websockets
        headers = R.auth_headers(os.environ[R.KEY_ENV],
                                os.environ[R.SECRET_ENV], path=self.path)
        async with websockets.connect(self.url, additional_headers=headers,
                                      max_size=8 * 1024 * 1024,
                                      open_timeout=15) as ws:
            for frame in self.subscribe():
                await ws.send(json.dumps(self.guard(frame)))
            while not _STOP.is_set():
                try:
                    import asyncio
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except Exception:                       # noqa: BLE001
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:                       # noqa: BLE001
                    msg = {"unparsed": str(raw)[:2000]}
                self.frames.append(msg)
                self.ledger.add(msg, source=self.name)


def market_slug() -> str:
    slug = os.getenv(X.MARKET_ENV, "").strip()
    if not slug:
        raise P.ProbeRefuse(f"NO_MARKET: {X.MARKET_ENV} is unset")
    return slug


def private_evidence(frames: List[dict], order_id: str) -> List[dict]:
    """Frames that mention our order id ANYWHERE in their structure.

    Deliberately structural: the schema is what we are here to learn, so this
    must not depend on a presumed field name.
    """
    def mentions(node: Any) -> bool:
        if isinstance(node, str):
            return order_id in node
        if isinstance(node, dict):
            return any(mentions(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(mentions(v) for v in node)
        return False
    return [f for f in frames if mentions(f)]


def run(t: Optional[X.Transport] = None, *, sleep=time.sleep,
        clock=time.monotonic) -> Dict[str, Any]:
    session = os.getenv(P.SESSION_ENV, "").strip()
    out: Dict[str, Any] = {
        "probe": "private_ws_schema", "session_id": session,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        #: INCOMPLETE is a sentinel distinct from every terminal label. It used
        #: to be "REFUSED", which collides with the exclusion list in `finally`,
        #: so a genuinely completed observation could never become OBSERVED and
        #: a successful run reported ERROR.
        "submitted": False, "order_id": None, "outcome": "INCOMPLETE",
        "sweep_verified": False, "terminal_proved": False,
    }
    if not P.armed(session or "\x00"):
        out["outcome"] = "REFUSED"
        #: state the ACTUAL contract. An earlier message said "all four must
        #: equal the session id", which is wrong and would have had an operator
        #: set PMUS_Q1_LIVE to the session id and refuse forever.
        out["refusal"] = (
            f"NOT_ARMED: {P.LIVE_ENV} must be exactly 'YES', and "
            f"{P.SESSION_ENV}, {P.ARM_ENV} and {P.PROBE_ARM_ENV} must all "
            f"equal the same session id")
        return out
    if not X.has_credentials():
        out["outcome"] = "REFUSED"
        out["refusal"] = "NO_CREDENTIALS"
        return out

    slug = market_slug()
    out["slug"] = slug
    t = t or X.Transport()
    pub_frames: List[dict] = []
    prv_frames: List[dict] = []
    pub_ledger, prv_ledger = P.FrameLedger(), P.FrameLedger()
    readers = [
        SocketReader("markets", R.WS_URL, R.WS_PATH, pub_frames, pub_ledger,
                     R.guard_outbound, lambda: R.subscribe_frames([slug])),
        SocketReader("private", PV.PRIVATE_URL, PV.PRIVATE_PATH, prv_frames,
                     prv_ledger, PV.guard_outbound, PV.subscribe_frames),
    ]
    inv = C.Inventory()
    order_id = ""
    info: Dict[str, Any] = {"booked_qty": D(0)}
    try:
        for r in readers:
            r.start()
        # 4: BOTH sockets must prove they are receiving before anything else
        t0 = clock()
        while clock() - t0 < READY_TIMEOUT_S:
            #: public: a fresh-frame count. private: a COMPLETED order snapshot,
            #: because that socket is silent when the account is idle.
            if (len(pub_frames) >= P.READY_FRAMES
                    and P.order_snapshot_proof(prv_frames) is not None):
                break
            sleep(0.25)
        out["public_frames_at_preflight"] = len(pub_frames)
        out["private_frames_at_preflight"] = len(prv_frames)
        for r in readers:
            if r.error:
                raise P.ProbeRefuse(f"SOCKET_ERROR[{r.name}]: {r.error}")
        pre = P.preflight(t, slug, public_frames=len(pub_frames),
                          private_frames=prv_frames, sleep=sleep)
        out["private_readiness_frame"] = pre.get("private_readiness_frame")
        px = pre["price"]
        out["price"] = str(px)
        out["touch_at_preflight"] = str(pre["touch"])

        # 7-9: ONE order, proven serializer, intent on disk first
        body = P.build_body(slug, px)
        X.persist_intent({"stage": "schema_probe", "slug": slug,
                          "side": body["outcomeSide"], "price": str(px),
                          "qty": X.QTY, "post_only": True,
                          "submitted_utc": datetime.now(timezone.utc)
                          .isoformat(),
                          "submitted_monotonic": clock()})
        prev = t.request("POST", "/v1/order/preview", OB.preview_envelope(body))
        if prev.get("error"):
            raise P.ProbeRefuse(f"PREVIEW_REJECTED: {prev.get('error')}")
        resp = t.request("POST", "/v1/orders", body)
        order_id = str(resp.get("id") or resp.get("orderId")
                       or (resp.get("order") or {}).get("id") or "")
        if not order_id:
            raise X.Halt("NO_ORDER_ID")
        out["submitted"] = True
        out["order_id"] = order_id
        X.persist_intent({"stage": "schema_probe:accepted",
                          "order_id": order_id,
                          "accepted_utc": datetime.now(timezone.utc)
                          .isoformat(),
                          "accepted_monotonic": clock(),
                          "venue_state": resp.get("state")})

        # 10: prove OPEN through REST while the private socket keeps capturing
        t1 = clock()
        rest_open = False
        while clock() - t1 < REST_OPEN_TIMEOUT_S:
            if order_id in C.open_order_ids(t, slug, sleep=sleep):
                rest_open = True
                break
            o = C._poll_fill(t, order_id, sleep)
            if X._filled_qty(o) > 0:
                break
            sleep(1.0)
        out["rest_open_proved"] = rest_open

        # 11-12: observe until we have evidence naming our order, or deadline
        t2 = clock()
        while clock() - t2 < P.OBSERVE_MAX_S:
            ev = private_evidence(prv_frames, order_id)
            if len(ev) >= EVIDENCE_FRAMES:
                break
            o = C._poll_fill(t, order_id, sleep)
            if X._filled_qty(o) > 0:
                break
            sleep(1.0)
        out["private_frames_naming_our_order"] = len(
            private_evidence(prv_frames, order_id))
        out["observed_seconds"] = round(clock() - t2, 3)
    except P.ProbeRefuse as exc:
        out["outcome"] = "REFUSED"
        out["refusal"] = str(exc)
    except Exception as exc:                            # noqa: BLE001
        out["outcome"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # 15: the cleanup path ALWAYS runs
        try:
            if order_id:
                res = C.cancel_and_resolve(t, order_id, slug, info, inv,
                                           sleep=sleep)
                out["cancel_result"] = {k: str(v) for k, v in res.items()}
                #: THE REAL CONTRACT, read from the function rather than its
                #: stale docstring: it returns `terminal`, `state`,
                #: `classification` and (with an inventory) `filled_qty`, and
                #: RAISES CancelUnconfirmed otherwise. There is no "cancelled".
                out["terminal_proved"] = res.get("terminal") is True
                out["terminal_state"] = res.get("state")
                out["terminal_classification"] = res.get("classification")
                filled = D(str(res.get("filled_qty") or 0))
                out["filled_qty"] = str(filled)
                if filled > 0 or "FILLED" in str(
                        res.get("classification") or "").upper():
                    out["outcome"] = "PROBE_FAILURE_UNEXPECTED_FILL"
            still = C.open_order_ids(t, slug, sleep=sleep) if order_id else []
            out["open_orders_after"] = still
            out["sweep_verified"] = (not still)
        except Exception as exc:                        # noqa: BLE001
            out["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            out["sweep_verified"] = False
        _STOP.set()
        out["private_frames_total"] = len(prv_frames)
        out["public_frames_total"] = len(pub_frames)
        out["private_schema"] = prv_ledger.report()
        #: only the untouched sentinel becomes OBSERVED. Every terminal label
        #: already set -- REFUSED, ERROR, PROBE_FAILURE_UNEXPECTED_FILL -- is
        #: preserved, so a failure can never be overwritten as a success.
        if out["outcome"] == "INCOMPLETE":
            out["outcome"] = "OBSERVED" if out["submitted"] else "REFUSED"
    return out


def terminal_ok(out: Dict[str, Any]) -> bool:
    """Success requires POSITIVE PROOF that the probe did its job and left the
    account clean. Never the absence of bad news.

    A REFUSED run is SAFE but is NOT success: a one-shot that fails to arm and
    exits 0 is operationally ambiguous -- Railway and any automation would read
    it as "ran fine" when nothing was observed. Only OBSERVED with both terminal
    assertions exits 0; refusal, an unexpected fill, an unresolved cancellation,
    a remaining open order or a failed reconciliation all exit nonzero.
    """
    return bool(out.get("outcome") == "OBSERVED"
                and out.get("submitted")
                and out.get("terminal_proved")
                and out.get("sweep_verified")
                and not out.get("open_orders_after")
                and not out.get("cleanup_error"))


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: _STOP.set())
        except Exception:                               # noqa: BLE001
            pass
    out = run()
    out["terminal_ok"] = terminal_ok(out)
    #: the exit code is a SAFETY signal, and `outcome` is the evidence signal.
    #: A safe refusal still exits nonzero so automation cannot mistake "armed
    #: incorrectly, observed nothing" for "observed the schema".
    print("SCHEMA_PROBE_REPORT " + json.dumps(out, default=str), flush=True)
    print(json.dumps(out, indent=1, default=str), flush=True)
    return 0 if out["terminal_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
