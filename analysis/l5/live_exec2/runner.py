"""The runner. This is the code that makes the authorized order.

Not a preflight. `run_experiment` is the whole experiment: select the window,
check the clock, wait for a fresh authenticated book, apply the frozen side
rule, price at the same-side touch, run every local gate, preview, consume the
durable latch, POST once, watch the lifecycle, take the markouts, withdraw at
the boundary, and cancel in a `finally` block no matter how it ends.

DISARMED it runs the SAME path and stops at the final pre-POST refusal, so
what gets tested is the entrypoint that will run live rather than a
look-alike.

THE AMBIGUOUS POST IS THE HARD CASE. If the POST raises, times out, or
returns something we cannot read, we do NOT know whether an order rested. The
latch is already spent by then, deliberately, so no retry is possible; the run
records AMBIGUOUS_SUBMISSION, attempts cancel-all for the market anyway, and
stops. Treating an unknown as "nothing happened" is how you end up with two
live orders.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src import l5_pmus_auth_book as AB
from src import pmus_auth as A
from analysis.l5.live_exec.spec import VENUE_MIN_QTY
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import sides as S
from analysis.l5.live_exec2 import timing as T
from analysis.l5.live_exec2.runner_spec import (PROBE, XR_FINGERPRINT,
                                                assert_frozen,
                                                fingerprint_pinned)
from analysis.l5.live_micro.ledger import OrderRecord, markouts
from analysis.l5.live_exec.ledger import exec_account

#: how long to wait for a fresh two-sided authenticated book before giving up
BOOK_WAIT_S = 90.0
#: how long a filled/resting order is observed before the markout window ends
OBSERVE_S = 65.0


def _now() -> float:
    return time.time()


def _stamp(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or _now()))


def select_market(explicit: Optional[str] = None) -> Dict[str, Any]:
    """One market. The current btc-updown-15m window, or the owner's slug."""
    slug = (explicit or "").strip() or AB.slug_for(AB.window_start())
    return {"slug": slug, "window_start_unix": AB.slug_start(slug),
            "selected_at": _stamp(), "series": "btc-updown-15m"}


def read_venue(http: Any, slug: str) -> Dict[str, Any]:
    from scripts.l5_exec1xr_meta import resolve_market
    mk = resolve_market(http, slug)
    return {"minimumTradeQty": mk.get("minimumTradeQty"),
            "tick": mk.get(A.TICK_FIELD), "slug": mk.get("slug"),
            "raw_keys": sorted(mk)[:12]}


def await_fresh_book(book: Any, slug: str, *, deadline_s: float = BOOK_WAIT_S
                     ) -> Dict[str, Any]:
    """Wait for ONE fresh, two-sided authenticated snapshot. No fallback."""
    end = _now() + deadline_s
    tries, last = 0, None
    while _now() < end:
        tries += 1
        snap = book.snapshot(slug)
        if snap is not None and snap.get("bid") is not None \
                and snap.get("ask") is not None:
            age = snap.get("stream_age_s")
            if age is not None and float(age) <= 2.0:
                return {"ok": True, "snapshot": snap, "tries": tries,
                        "age_s": float(age), "at": _now()}
            last = "stale: %ss" % age
        else:
            last = "not two-sided" if snap else "no snapshot"
        time.sleep(1.0)
    return {"ok": False, "snapshot": None, "tries": tries, "why": last,
            "NOTE": "no gateway fallback exists; AuthBook or nothing"}


def depth_ahead_at(snap: Dict[str, Any], side_token: str) -> Optional[str]:
    """Anonymous resting size at our price. Anonymous: it is not OUR queue
    position, and the venue does not tell us who is ahead."""
    q = snap.get("bid_shares") if side_token == S.UP else snap.get("ask_shares")
    return None if q is None else str(q)


def our_price(snap: Dict[str, Any], side_token: str) -> D:
    """Offset 0 from the touch, on our own terms: the same-side best bid.

    The venue quotes one book in YES terms. Our own-side best bid is the YES
    bid when we are UP, and 1 - (YES ask) when we are DOWN -- the NO bid.
    """
    if side_token == S.UP:
        return D(str(snap["bid"]))
    return D("1") - D(str(snap["ask"]))


def opposite_touch(snap: Dict[str, Any], side_token: str) -> Optional[D]:
    """The live quote our order must not reach, in our own terms."""
    if side_token == S.UP:
        return None if snap.get("ask") is None else D(str(snap["ask"]))
    return None if snap.get("bid") is None else D("1") - D(str(snap["bid"]))


def venue_role(order_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Venue-confirmed passive vs aggressor, from the execution objects.

    The authoritative selector is `isAggressor` on the execution, NOT the
    boolean `aggressor` field -- a prior probe read that one and the venue
    sent False for a print it had not classified, which is how a role got
    "resolved" without evidence. Absent the key, the role stays UNRESOLVED
    and no maker claim is made.
    """
    execs: List[Dict[str, Any]] = []
    for key in ("executions", "trades", "fills"):
        v = order_obj.get(key)
        if isinstance(v, list):
            execs.extend([e for e in v if isinstance(e, dict)])
    roles, fees = [], D("0")
    for e in execs:
        if "isAggressor" in e:
            roles.append("taker" if e.get("isAggressor") else "maker")
        c = e.get("commissionNotionalCollected")
        if c is not None:
            fees += D(str(c))           # signed: negative means credited
    role = roles[0] if roles and len(set(roles)) == 1 else None
    return {"role": role, "n_executions": len(execs),
            "fees_usd": fees,
            "source": ("execution.isAggressor -> passiveExecution/"
                       "aggressorExecution"),
            "forbidden_field_not_used": "aggressor (a boolean)",
            "unresolved_reason": (None if role else
                                  "no isAggressor on any execution")}


def _place(t: O.BoundedOrderTransport, *, slug: str, side_token: str,
           price: D, checks: Dict[str, Any]) -> Dict[str, Any]:
    """The only call site of submit_one in this codebase."""
    return O.submit_one(t, slug=slug, outcome_side=side_token, price=price,
                        qty=VENUE_MIN_QTY, checks=checks)


def run_experiment(*, explicit_slug: Optional[str] = None,
                   observe_s: float = OBSERVE_S,
                   book_wait_s: float = BOOK_WAIT_S,
                   transport: Optional[Any] = None,
                   book: Optional[Any] = None) -> Dict[str, Any]:
    """The complete one-order experiment. Returns the full evidence record."""
    assert_frozen()
    rec: Dict[str, Any] = {
        "probe": PROBE, "spec_fingerprint": XR_FINGERPRINT,
        "started_utc": _stamp(), "orders_submitted": 0,
        "matched_pair_economics": "N/A_SINGLE_LEG_EXPERIMENT",
        "matched_is_not_zero_it_is_not_applicable": True,
        "stopped_at": None, "refusals": [], "pin": fingerprint_pinned(),
    }
    order: Optional[OrderRecord] = None
    order_id: Optional[str] = None
    slug: Optional[str] = None
    t = transport or O.BoundedOrderTransport()
    owned_book = book is None
    bk = book or AB.AuthBook().start()
    try:
        # --- market, clock -------------------------------------------------
        mkt = select_market(explicit_slug)
        rec["market"] = mkt
        slug = mkt["slug"]
        tte = T.tte_s(mkt["window_start_unix"], _now())
        place_ok = T.placement_allowed(tte)
        rec["timing"] = {"tte_s": tte, "placement": place_ok,
                         "withdraw": T.must_withdraw(tte)}
        if not place_ok["allowed"]:
            rec["refusals"].append(place_ok["reason"])
            rec["stopped_at"] = "TIMING"
            return rec

        # --- the frozen side, before the book is consulted for a decision --
        sid = __import__("os").getenv("PMUS_EXEC1_SESSION_ID", "").strip()
        rec["side_rule"] = (S.side_for(slug, sid) if sid else
                            {"error": "PMUS_EXEC1_SESSION_ID unset; the "
                                      "frozen side rule has no input"})
        if "error" in rec["side_rule"]:
            rec["refusals"].append("SIDE_RULE_INPUT_MISSING")
            # keep going: the disarmed run should still exercise the rest

        # --- venue metadata ------------------------------------------------
        try:
            venue = read_venue(t, slug)
        except Exception as exc:                               # noqa: BLE001
            venue = {"error": type(exc).__name__}
        rec["venue"] = venue

        # --- fresh authenticated book --------------------------------------
        fresh = await_fresh_book(bk, slug, deadline_s=book_wait_s)
        rec["book_wait"] = {k: v for k, v in fresh.items()
                            if k != "snapshot"}
        if not fresh["ok"]:
            rec["refusals"].append("NO_FRESH_TWO_SIDED_AUTHENTICATED_BOOK")
            rec["stopped_at"] = "BOOK"
            return rec
        snap = fresh["snapshot"]
        side_token = rec["side_rule"].get("side") or S.UP
        price = our_price(snap, side_token)
        opp = opposite_touch(snap, side_token)
        book_t = _now() - fresh["age_s"]

        decision_t = _now()
        rec["decision"] = {
            "decision_timestamp_utc": _stamp(decision_t),
            "decision_timestamp_unix": decision_t,
            "side": side_token,
            "side_label": rec["side_rule"].get("label"),
            "price": str(price), "quantity": str(VENUE_MIN_QTY),
            "venue_bid_yes": str(snap["bid"]), "venue_ask_yes": str(snap["ask"]),
            "own_side_touch": str(price),
            "own_side_opposite_touch": None if opp is None else str(opp),
            "spread": str(D(str(snap["ask"])) - D(str(snap["bid"]))),
            "book_age_s": fresh["age_s"], "book_timestamp_unix": book_t,
            "depth_ahead_anonymous": depth_ahead_at(snap, side_token),
            "depth_ahead_is_not_our_queue_position": True,
            "tte_s": T.tte_s(mkt["window_start_unix"], decision_t),
            "offset_ticks_from_touch": 0,
            "bbo_source": "l5_pmus_auth_book.AuthBook",
        }

        # --- every local gate, together ------------------------------------
        checks = O.preflight_submission(
            slug=slug, outcome_side=side_token, price=price,
            qty=VENUE_MIN_QTY, min_qty=venue.get("minimumTradeQty"),
            tick=venue.get("tick"), book_bid=price, book_ask=opp,
            book_t=book_t, now=_now())
        rec["preflight"] = checks
        pin = fingerprint_pinned()
        rec["pin"] = pin
        if not pin["matches_this_build"]:
            checks = dict(checks)
            checks["ok"] = False
            checks["refusals"] = list(checks["refusals"]) + [
                "FINGERPRINT_NOT_PINNED_BY_OWNER"]
            rec["preflight"] = checks
        if not checks["ok"]:
            rec["refusals"].extend(checks["refusals"])
            rec["stopped_at"] = "FINAL_PRE_POST_REFUSAL"
            rec["reached_final_gate"] = True
            return rec

        # --- preview, latch, the one POST ----------------------------------
        rec["reached_final_gate"] = True
        try:
            out = _place(t, slug=slug, side_token=side_token, price=price,
                         checks=checks)
        except O.MutationRefused as exc:
            rec["refusals"].append("REFUSED:%s" % exc)
            rec["stopped_at"] = "REFUSED_AT_SUBMIT"
            return rec
        except L.LatchSpent as exc:
            rec["refusals"].append("DURABLE_LATCH_SPENT:%s" % exc)
            rec["stopped_at"] = "LATCH_SPENT"
            return rec
        except O.PreviewRejected as exc:
            # The PREVIEW failed: a what-if that creates nothing, run
            # strictly before latch consumption. Nothing crossed the order
            # boundary, and the exception type says so without the latch
            # having to be read backwards.
            rec["preview_rejected"] = {
                "error": type(exc).__name__, "message": str(exc)[:600],
                "detail": exc.detail,
                "latch_consumed": exc.latch_consumed,
                "crossed_order_boundary": exc.crossed_order_boundary,
                "latch": L.state(),
                "MEANING": ("the venue declined the what-if. No order was "
                            "created and there is nothing to cancel."),
            }
            rec["refusals"].append("PREVIEW_REJECTED")
            rec["stopped_at"] = "PREVIEW_REJECTED"
            return rec
        except O.SubmissionAmbiguous as exc:
            rec["ambiguous_submission"] = {
                "error": type(exc).__name__, "detail": exc.detail,
                "message": str(exc)[:600],
                "latch_consumed": exc.latch_consumed,
                "crossed_order_boundary": exc.crossed_order_boundary,
                "latch": L.state(),
                "MEANING": ("we do not know whether an order rested. No "
                            "retry is possible or permitted."),
            }
            rec["refusals"].append("AMBIGUOUS_SUBMISSION")
            rec["stopped_at"] = "AMBIGUOUS_SUBMISSION"
            return rec
        except Exception as exc:                               # noqa: BLE001
            # The latch may or may not have been consumed, and the POST may
            # or may not have reached the venue. Both unknowns point the
            # same way: stop.
            # Kept, but no longer the only outcome: the phase now names
            # itself, so a preview failure is not reported as a possible
            # order. See the XR2W correction.
            rec["unclassified_failure"] = {
                "error": type(exc).__name__, "detail": str(exc)[:300],
                "latch": L.state(),
                "MEANING": ("the phase could not be established from the "
                            "exception. Treated as possibly having "
                            "submitted."),
            }
            rec["refusals"].append("UNCLASSIFIED_SUBMISSION_FAILURE")
            rec["stopped_at"] = "UNCLASSIFIED_SUBMISSION_FAILURE"
            return rec

        rec["orders_submitted"] = 1
        rec["submission"] = out
        order_id = out.get("order_id")
        submitted_t = _now()
        order = OrderRecord("exec1xr", slug, side_token, price, VENUE_MIN_QTY)
        if not order_id:
            rec["ambiguous_submission"] = {
                "error": "NO_ORDER_ID",
                "venue_response_keys": sorted(out.get("venue_response") or {}),
                "MEANING": ("the venue answered but we cannot name the "
                            "order, so its state is unknown. No retry."),
            }
            rec["refusals"].append("AMBIGUOUS_SUBMISSION_NO_ORDER_ID")
            rec["stopped_at"] = "AMBIGUOUS_SUBMISSION"
            return rec
        order.advance("ACKNOWLEDGED", at=submitted_t, detail=order_id)

        # --- lifecycle + markouts, from authenticated data only -------------
        marks: Dict[int, Optional[D]] = {}
        horizons = [1, 5, 10, 30, 60]
        events: List[Dict[str, Any]] = []
        end = submitted_t + observe_s
        while _now() < end:
            time.sleep(0.5)
            el = _now() - submitted_t
            for h in horizons:
                if h not in marks and el >= h:
                    s2 = bk.snapshot(slug)
                    a2 = None if s2 is None else s2.get("stream_age_s")
                    # a mark is valid only from a FRESH two-sided book; a
                    # missing horizon stays missing rather than interpolated
                    marks[h] = (our_price(s2, side_token)
                                if s2 is not None and a2 is not None
                                and float(a2) <= 2.0 else None)
            try:
                st = t.request("GET", "/v1/order/%s" % order_id)
            except Exception as exc:                           # noqa: BLE001
                events.append({"t": _stamp(), "read_error":
                               type(exc).__name__})
                continue
            o = st.get("order") or st
            events.append({"t": _stamp(), "unix": _now(),
                           "state": o.get("state"),
                           "cumQuantity": o.get("cumQuantity"),
                           "leavesQuantity": o.get("leavesQuantity")})
            cum = D(str(o.get("cumQuantity") or 0))
            if cum > order.filled_qty:
                delta = cum - order.filled_qty
                vr = venue_role(o)
                if vr["role"] is not None:
                    order.record_fill(delta, price, at=_now(),
                                      maker_taker=vr["role"],
                                      fee_usd=vr["fees_usd"])
                else:
                    # a real fill whose liquidity role the venue has not
                    # stated. The quantity is recorded; the role is NOT
                    # guessed, so the ledger is not given a maker claim it
                    # cannot support.
                    rec.setdefault("unresolved_role_fills", []).append(
                        {"at": _stamp(), "qty": str(delta),
                         "venue_role": vr})
                    order.filled_qty += delta
                    order.advance("FILLED" if order.filled_qty
                                  >= order.intended_qty
                                  else "PARTIALLY_FILLED", at=_now(),
                                  detail={"role": "UNRESOLVED"})
                rec["venue_confirmed_liquidity_role"] = vr
            tte_now = T.tte_s(mkt["window_start_unix"], _now())
            if T.must_withdraw(tte_now)["withdraw"]:
                events.append({"t": _stamp(), "withdrawal":
                               "TTE boundary reached"})
                break
        rec["lifecycle_events"] = events
        rec["rest_time_s"] = round(_now() - submitted_t, 3)
        rec["markouts"] = markouts(order, marks)
        # NO_OBSERVATION has two causes and they are not the same claim, so
        # the record says which: the horizon had not elapsed inside the
        # observation window, or it elapsed and the book was not fresh.
        rec["markout_horizons_elapsed"] = sorted(marks)
        rec["markout_horizons_not_elapsed"] = [h for h in horizons
                                               if h not in marks]
        rec["markout_note"] = ("a horizon with no fresh two-sided "
                               "authenticated book is reported as missing, "
                               "never interpolated; a horizon that did not "
                               "elapsed inside the observation window is "
                               "listed separately from one that elapsed "
                               "without an observation")
        rec["order_state"] = order.state
        rec["filled_qty"] = str(order.filled_qty)
        rec["account"] = exec_account(order)
        rec["settlement"] = {
            "observed": False,
            "note": ("settlement and cash confirmation are later events; "
                     "they are reported when observed, and P&L is not "
                     "REALIZED until FILLED+SETTLED+CASH_CONFIRMED"),
        }
        return rec
    finally:
        # --- deadman: always, and it never creates an order ----------------
        dm: Dict[str, Any] = {"attempted_utc": _stamp(),
                              "may_create_an_order": False}
        # An AMBIGUOUS submission may have rested a real order, so the
        # deadman runs for it too -- that is the whole point of a deadman.
        ambiguous = bool(rec.get("ambiguous_submission")
                         or rec.get("unclassified_failure")
                         or rec.get("preview_rejected"))
        if (rec.get("orders_submitted") or ambiguous) and slug:
            dm["ran_because"] = ("ambiguous submission: an order may exist"
                                 if ambiguous and not rec.get(
                                     "orders_submitted")
                                 else "an order was submitted")
            if order_id:
                try:
                    dm["cancel_exact"] = O.cancel_exact(t, order_id=order_id,
                                                        slug=slug)
                except Exception as exc:                       # noqa: BLE001
                    dm["cancel_exact_error"] = type(exc).__name__
            dm["cancel_all"] = O.deadman_cancel_all(t, slug=slug)
            if order is not None and order.filled_qty < VENUE_MIN_QTY:
                try:
                    order.advance("CANCELLED", at=_now(), detail="deadman")
                except Exception:                              # noqa: BLE001
                    pass
        else:
            dm["skipped"] = ("no submission was attempted, so there is "
                             "nothing that could be resting")
        rec["deadman"] = dm
        rec["ended_utc"] = _stamp()
        if owned_book:
            try:
                bk.stop()
            except Exception:                                  # noqa: BLE001
                pass
        print("EXEC1XR_RECORD " + json.dumps(rec, default=str), flush=True)
