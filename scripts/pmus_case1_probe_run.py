"""EXECUTABLE entrypoint for the ONE authorized Case-1 live calibration.

    python3 -m scripts.pmus_case1_probe_run

A THIN ORCHESTRATION WRAPPER. Every money-touching primitive is reused from code
that has already run live; this file creates no serializer, no transport and no
cancel path of its own:

    src.pmus_q1_executor            Transport, fresh_bbo, would_cross,
                                    persist_intent, _filled_qty
    src.l5_order_body               build + preview_envelope (proven serializer)
    src.pmus_q1_canary              cancel_and_resolve, open_order_ids,
                                    _poll_fill, window_end_epoch, Inventory
    src.pmus_ws_lifecycle_observer  /v1/ws/private subscribe frames + guard
    src.pmus_ws_evidence_recorder   auth_headers, market subscribe + guard,
                                    apply_events (the proven book folder)
    src.pmus_private_schema_probe   FrameLedger, order_snapshot_proof,
                                    READY_FRAMES, SocketReader via the sibling
    src.pmus_case1_probe            arming, price rule, reach detection,
                                    qualification, classification

ONE submission, q=1, post-only, non-marketable, entry price capped at $0.25,
maximum possible loss $0.25. THE ORDER IS NEVER MOVED: no requote, no reprice,
no replacement. If the book never reaches it, that is a valid NULL.

EXIT CODE IS A SAFETY SIGNAL, not an evidence signal. 0 only when the account was
left provably clean AND the run reached a preregistered terminal label. A refusal
exits nonzero so a one-shot that failed to arm can never be read as "ran fine".
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import pmus_schema_probe_run as SP          # noqa: E402
from src import l5_order_body as OB                      # noqa: E402
from src import pmus_case1_probe as K                    # noqa: E402
from src import pmus_private_schema_probe as P           # noqa: E402
from src import pmus_q1_canary as C                      # noqa: E402
from src import pmus_q1_executor as X                    # noqa: E402
from src import pmus_ws_evidence_recorder as R           # noqa: E402
from src import pmus_ws_lifecycle_observer as PV         # noqa: E402

READY_TIMEOUT_S = 45.0
REST_OPEN_TIMEOUT_S = 20.0
#: the public socket must keep delivering while we wait; this many seconds with
#: no new frame is a STALE FEED and a mandated stop.
PUBLIC_STALL_STOP_S = 20.0
POLL_S = 1.0


def market_slug() -> str:
    slug = os.getenv(X.MARKET_ENV, "").strip()
    if not slug:
        raise K.Case1Refuse(f"NO_MARKET: {X.MARKET_ENV} is unset")
    return slug


def book_view(state: Dict[str, Any], slug: str) -> Dict[str, Any]:
    """Current folded public book for our market, from the WS frames."""
    return dict(state.get(slug) or {})


def run(t: Optional[X.Transport] = None, *, sleep=time.sleep,
        clock=time.monotonic, now=time.time) -> Dict[str, Any]:
    session = os.getenv(K.SESSION_ENV, "").strip()
    out: Dict[str, Any] = {
        "probe": "case1_resting_order_crossed",
        "label": "LIVE CALIBRATION EVIDENCE -- not realized profit, not OOS",
        "session_id": session,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "submitted": False, "order_id": None, "outcome": "INCOMPLETE",
        "sweep_verified": False, "terminal_proved": False,
        "rest_open_proved": False, "stops_fired": [],
    }
    #: THE NARROW AUTHORIZATION IS CONSULTED BEFORE armed(), the same order the
    #: L5 session engine uses for HALT. The repository HALT means "no orders",
    #: so this probe does not bypass it: it requires its own time-boxed,
    #: SHA-pinned artifact that states the override explicitly and scopes it to
    #: this probe. Absence of that artifact is the safe state.
    out["global_halt_present"] = K.halt_present()
    try:
        auth = K.authorization()
    except K.Case1Refuse as exc:
        out["outcome"] = "REFUSED"
        out["refusal"] = str(exc)
        return out
    out["authorization"] = {
        "probe": auth.get("probe"), "expires_utc": auth.get("expires_utc"),
        "reviewed_sha": auth.get("reviewed_sha"),
        "max_entry_price_usd": auth.get("max_entry_price_usd"),
        "max_loss_usd": auth.get("max_loss_usd"),
        "overrides_global_halt": auth.get("overrides_global_halt"),
        "authorizes_l5_session_engine": auth.get(
            "authorizes_l5_session_engine"),
    }
    if not K.armed(session or "\x00"):
        out["outcome"] = "REFUSED"
        out["refusal"] = f"NOT_ARMED: {K.arming_contract()}"
        return out
    if not X.has_credentials():
        out["outcome"] = "REFUSED"
        out["refusal"] = "NO_CREDENTIALS"
        return out

    #: EVERY refusal below this point is REPORTED, never raised out of `run`:
    #: these checks used to sit outside the try block, so a missing market slug
    #: escaped as an exception instead of a clean REFUSED report.
    try:
        slug = market_slug()
    except K.Case1Refuse as exc:
        out["outcome"] = "REFUSED"
        out["refusal"] = str(exc)
        return out
    out["slug"] = slug
    w_end = C.window_end_epoch(slug)
    if w_end is None:
        out["outcome"] = "REFUSED"
        out["refusal"] = f"UNPARSEABLE_WINDOW: cannot read a close from {slug}"
        return out
    out["window_end_utc"] = datetime.fromtimestamp(
        w_end, timezone.utc).isoformat()
    #: THE TAIL GUARD RUNS BEFORE ANY SOCKET OR TRANSPORT EXISTS. It used to run
    #: after the readiness wait, which meant a window already past its close
    #: still opened two authenticated sockets and burned the timeout first.
    if now() > w_end - K.WINDOW_TAIL_GUARD_S:
        out["outcome"] = "REFUSED"
        out["refusal"] = (f"WINDOW_TAIL: {round(w_end - now(), 1)}s to close, "
                          f"guard is {K.WINDOW_TAIL_GUARD_S}s")
        return out

    t = t or X.Transport()
    pub_frames: List[dict] = []
    prv_frames: List[dict] = []
    pub_ledger, prv_ledger = P.FrameLedger(), P.FrameLedger()
    book: Dict[str, Any] = {}
    readers = [
        SP.SocketReader("markets", R.WS_URL, R.WS_PATH, pub_frames, pub_ledger,
                        R.guard_outbound, lambda: R.subscribe_frames([slug])),
        SP.SocketReader("private", PV.PRIVATE_URL, PV.PRIVATE_PATH, prv_frames,
                        prv_ledger, PV.guard_outbound, PV.subscribe_frames),
    ]
    inv = C.Inventory()
    order_id = ""
    info: Dict[str, Any] = {"booked_qty": D(0)}
    side, px = "", D(0)
    reach = {"touched": False, "reached": False, "reached_after_rest": False}
    reached_before_rest = False
    bbo_before: Dict[str, Any] = {}
    bbo_at_reach: Dict[str, Any] = {}
    bbo_after: Dict[str, Any] = {}
    transitions: List[Dict[str, Any]] = []

    def fold() -> None:
        """Fold every public frame seen so far into the book state."""
        while len(book.get("_folded", [])) < len(pub_frames):
            i = len(book.setdefault("_folded", []))
            try:
                R.apply_events(book, pub_frames[i], now())
            except Exception:                            # noqa: BLE001
                pass
            book["_folded"].append(i)

    try:
        for r in readers:
            r.start()
        t0 = clock()
        while clock() - t0 < READY_TIMEOUT_S:
            if (len(pub_frames) >= P.READY_FRAMES
                    and P.order_snapshot_proof(prv_frames) is not None):
                break
            sleep(0.25)
        out["public_frames_at_preflight"] = len(pub_frames)
        out["private_frames_at_preflight"] = len(prv_frames)
        for r in readers:
            if r.error:
                raise K.Case1Refuse(f"SOCKET_ERROR[{r.name}]: {r.error}")
        #: the readiness wait itself can consume enough time to enter the tail
        if now() > w_end - K.WINDOW_TAIL_GUARD_S:
            raise K.Case1Refuse(
                f"WINDOW_TAIL: {round(w_end - now(), 1)}s to close, guard is "
                f"{K.WINDOW_TAIL_GUARD_S}s")

        pre = K.preflight(t, slug, public_frames=len(pub_frames),
                          private_frames=prv_frames, sleep=sleep)
        side, px = pre["side"], pre["price"]
        out.update({"side": side, "price": str(px),
                    "own_touch_at_preflight": pre["own_touch"],
                    "bbo_at_preflight": pre["bbo"],
                    "worst_case_loss": pre["worst_case_loss"],
                    "private_readiness_frame": pre["private_readiness_frame"]})

        body = K.build_body(slug, side, px)
        X.persist_intent({"stage": "case1_probe", "slug": slug,
                          "side": body["outcomeSide"], "own_side": side,
                          "price": str(px), "qty": K.QTY, "post_only": True,
                          "worst_case_loss": pre["worst_case_loss"],
                          "submitted_utc": datetime.now(timezone.utc)
                          .isoformat(), "submitted_monotonic": clock()})
        prev = t.request("POST", "/v1/order/preview", OB.preview_envelope(body))
        if prev.get("error"):
            raise K.Case1Refuse(f"PREVIEW_REJECTED: {prev.get('error')}")
        resp = t.request("POST", "/v1/orders", body)
        order_id = str(resp.get("id") or resp.get("orderId")
                       or (resp.get("order") or {}).get("id") or "")
        if not order_id:
            raise X.Halt("NO_ORDER_ID")
        out["submitted"] = True
        out["order_id"] = order_id
        out["submit_response_state"] = resp.get("state")
        X.persist_intent({"stage": "case1_probe:accepted", "order_id": order_id,
                          "accepted_utc": datetime.now(timezone.utc)
                          .isoformat(), "accepted_monotonic": clock()})

        # ---- condition 1: prove we were REALLY resting, privately and by REST
        t1 = clock()
        while clock() - t1 < REST_OPEN_TIMEOUT_S:
            if order_id in C.open_order_ids(t, slug, sleep=sleep):
                out["rest_open_proved"] = True
                break
            sleep(POLL_S)
        priv = K.extract_private(prv_frames, order_id)
        out["private_new_frames"] = priv["frames_naming_our_order"]
        rest_proved = bool(out["rest_open_proved"]
                           and priv["frames_naming_our_order"] > 0)
        out["rest_proved"] = rest_proved
        if not rest_proved:
            raise K.Case1Stop(
                f"REST_NOT_PROVED: rest_open={out['rest_open_proved']} "
                f"private_frames_naming_order={priv['frames_naming_our_order']}")
        fold()
        bbo_before = book_view(book, slug)
        out["bbo_before"] = {k: v for k, v in bbo_before.items()
                             if k in ("bid", "ask", "bid_qty", "ask_qty")}
        #: THE ORDERING BASELINE, and the whole point of the experiment. "Crossed"
        #: must mean the public book moved to or through our resting price AFTER
        #: private NEW + REST OPEN, with our order unchanged. So the reach state
        #: is sampled HERE, at the instant rest was proved: if the book was
        #: already there, no later observation can establish that ordering and the
        #: verdict is AMBIGUOUS_EVENT_ORDER rather than a Case-1 result.
        base = K.reach_state(side, px, bbo_before.get("bid"),
                             bbo_before.get("ask"))
        reached_before_rest = bool(base["reached"])
        out["reach_baseline_at_rest"] = base
        out["reached_before_rest"] = reached_before_rest
        out["rest_proved_monotonic"] = clock()

        # ---- condition 2: WAIT. Never move the order. A null is a result.
        t2 = clock()
        last_frames, last_change = len(pub_frames), clock()
        while True:
            if clock() - t2 >= K.OBSERVE_MAX_S:
                out["stops_fired"].append("OBSERVE_MAX_S")
                break
            if now() > w_end - K.WINDOW_TAIL_GUARD_S:
                out["stops_fired"].append("WINDOW_TAIL_GUARD")
                break
            for r in readers:
                if r.error:
                    raise K.Case1Stop(f"STREAM_FAILURE[{r.name}]: {r.error}")
            if len(pub_frames) != last_frames:
                last_frames, last_change = len(pub_frames), clock()
            elif clock() - last_change > PUBLIC_STALL_STOP_S:
                raise K.Case1Stop(
                    f"STALE_FEED: no public frame for "
                    f"{round(clock() - last_change, 1)}s")
            fold()
            bv = book_view(book, slug)
            st = K.reach_state(side, px, bv.get("bid"), bv.get("ask"))
            if st["touched"] and not reach["touched"]:
                reach["touched"] = True
                transitions.append({"event": "touched", "monotonic": clock(),
                                    "utc": datetime.now(timezone.utc)
                                    .isoformat(),
                                    "bid": bv.get("bid"), "ask": bv.get("ask"),
                                    "bid_qty": bv.get("bid_qty"),
                                    "ask_qty": bv.get("ask_qty")})
            if st["reached"] and not reach["reached"]:
                reach["reached"] = True
                #: a transition observed AFTER the baseline, with the order
                #: untouched since: this is the ordered Case-1 event.
                reach["reached_after_rest"] = not reached_before_rest
                bbo_at_reach = dict(bv)
                transitions.append({"event": "reached", "monotonic": clock(),
                                    "utc": datetime.now(timezone.utc)
                                    .isoformat(),
                                    "bid": bv.get("bid"), "ask": bv.get("ask"),
                                    "bid_qty": bv.get("bid_qty"),
                                    "ask_qty": bv.get("ask_qty")})
            #: a fill is resolved from the PRIVATE stream, which is why we are
            #: here; the REST poll is only a backstop for an unexpected state.
            o = C._poll_fill(t, order_id, sleep)
            state_s = X.order_state_str(o)
            if X._filled_qty(o) > 0:
                out["stops_fired"].append("FILLED")
                break
            if "REJECT" in state_s or "CANCEL" in state_s:
                #: we never cancelled, so this is an UNEXPECTED order state
                raise K.Case1Stop(f"UNEXPECTED_ORDER_STATE: {state_s}")
            if reach["reached"] and clock() - t2 > 30.0:
                #: crossed and still unfilled after a grace period is itself the
                #: Case-1 answer; stop observing rather than hold risk longer.
                out["stops_fired"].append("REACHED_AND_SETTLED_OBSERVATION")
                break
            sleep(POLL_S)
        out["observed_seconds"] = round(clock() - t2, 3)
        fold()
        bbo_after = book_view(book, slug)
        out["bbo_after"] = {k: v for k, v in bbo_after.items()
                            if k in ("bid", "ask", "bid_qty", "ask_qty")}
    except K.Case1Refuse as exc:
        out["outcome"] = "REFUSED"
        out["refusal"] = str(exc)
    except K.Case1Stop as exc:
        out["outcome"] = "STOPPED"
        out["stop"] = str(exc)
        out["stops_fired"].append(str(exc).split(":", 1)[0])
    except Exception as exc:                             # noqa: BLE001
        out["outcome"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        filled = D(0)
        try:
            if order_id:
                res = C.cancel_and_resolve(t, order_id, slug, info, inv,
                                           sleep=sleep)
                out["cancel_result"] = {k: str(v) for k, v in res.items()}
                out["terminal_proved"] = res.get("terminal") is True
                out["terminal_state"] = res.get("state")
                out["terminal_classification"] = res.get("classification")
                filled = D(str(res.get("filled_qty") or 0))
                out["filled_qty"] = str(filled)
            still = C.open_order_ids(t, slug, sleep=sleep) if order_id else []
            out["open_orders_after"] = still
            out["sweep_verified"] = (not still)
        except Exception as exc:                         # noqa: BLE001
            out["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            out["sweep_verified"] = False
        SP._STOP.set()
        fold()
        bv = book_view(book, slug)
        priv = K.extract_private(prv_frames, order_id)
        trades = (bv.get("trades") or [])
        out["reach"] = dict(reach)
        out["book_transitions"] = transitions
        out["bbo_at_reach"] = {k: v for k, v in (bbo_at_reach or {}).items()
                               if k in ("bid", "ask", "bid_qty", "ask_qty")}
        out["public_book_updates"] = bv.get("book_updates")
        out["public_trades_seen"] = len(trades)
        out["consuming_trades"] = (K.consuming_trades(side, px, trades)
                                   if side else [])
        out["private_evidence"] = priv
        out["private_fields"] = priv["fields"]
        out["private_fields_unavailable"] = priv["fields_unavailable"]
        out["private_schema"] = prv_ledger.report()
        out["public_frames_total"] = len(pub_frames)
        out["private_frames_total"] = len(prv_frames)
        out["carried_to_settlement"] = str(filled)
        qual = K.qualifies({
            "rest_proved": out.get("rest_proved"),
            #: the ORDERED condition, never the bare one
            "reached": reach["reached_after_rest"],
            #: the venue said SOMETHING terminal about our order privately
            "private_terminal": bool(out.get("terminal_proved")
                                     and priv["frames_naming_our_order"] > 0),
            "public_sequence": (bool(bv.get("book_updates"))
                                and bool(transitions or not reach["reached"])),
        })
        out["qualification"] = qual
        #: THE LIVE-REPORT VERDICT. Six mutually exclusive labels, so "no fill"
        #: and "no opportunity" can never share a bucket.
        role = K.fill_role(priv["fields"], px or K.MAX_ENTRY_PX)
        safety_ok = bool(out.get("terminal_proved")
                         and out.get("sweep_verified")
                         and not out.get("cleanup_error")
                         and not out.get("open_orders_after")
                         and out.get("outcome") not in ("STOPPED", "ERROR"))
        out["fill_role"] = role
        out["safety_ok"] = safety_ok
        out["verdict"] = K.verdict(
            safety_ok=safety_ok, rest_proved=bool(out.get("rest_proved")),
            reached_before_rest=bool(out.get("reached_before_rest")),
            reached_after_rest=bool(reach["reached_after_rest"]),
            filled_qty=filled, role=role)
        if out["outcome"] == "INCOMPLETE":
            out["outcome"] = K.classify(
                submitted=bool(out["submitted"]),
                rest_proved=bool(out.get("rest_proved")),
                reach={"touched": reach["touched"],
                       "reached": reach["reached_after_rest"]},
                filled_qty=filled, qual=qual)
    return out


TERMINAL_LABELS = ("NULL_NOT_REACHED", "CASE1_OBSERVED_FILLED",
                   "CASE1_OBSERVED_NOT_FILLED", "CASE1_INCONCLUSIVE")


def terminal_ok(out: Dict[str, Any]) -> bool:
    """POSITIVE proof the account was left clean AND a preregistered terminal
    label was reached. A refusal, a stop, an error, an unresolved cancellation
    or any remaining open order all exit nonzero."""
    return bool(out.get("outcome") in TERMINAL_LABELS
                and out.get("submitted")
                and out.get("terminal_proved")
                and out.get("sweep_verified")
                and not out.get("open_orders_after")
                and not out.get("cleanup_error"))


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: SP._STOP.set())
        except Exception:                                # noqa: BLE001
            pass
    out = run()
    out["terminal_ok"] = terminal_ok(out)
    print("CASE1_PROBE_REPORT " + json.dumps(out, default=str), flush=True)
    print(json.dumps(out, indent=1, default=str), flush=True)
    return 0 if out["terminal_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
