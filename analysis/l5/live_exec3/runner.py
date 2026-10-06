"""The XR2 runner: XR's experiment, with the TTE recheck and the pre-open wait.

Everything about WHAT is traded is imported from XR, unchanged: the side rule,
the same-side touch at offset 0, the gates, the lifecycle, the markouts, the
deadman. This module adds only WHEN:

  * `_await_window` lets the process start before the frozen window opens and
    wait for it. For that slug only. There is no code here that can look at
    another market.
  * `_tte_guard` is handed to `orders.submit_one`, which calls it immediately
    before consuming the durable latch and again immediately before the POST.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Optional

from src import l5_pmus_auth_book as AB
from analysis.l5.live_exec.spec import VENUE_MIN_QTY
from analysis.l5.live_exec.ledger import exec_account
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import sides as S
from analysis.l5.live_exec2 import timing as T
from analysis.l5.live_exec2.runner import (await_fresh_book, depth_ahead_at,
                                           opposite_touch, our_price,
                                           read_venue, venue_role)
from analysis.l5.live_exec3.spec import (MAX_PREOPEN_WAIT_S, PROBE,
                                         XR2_FINGERPRINT, assert_frozen,
                                         fingerprint_pinned)
from analysis.l5.live_micro.ledger import OrderRecord, markouts

BOOK_WAIT_S = 90.0
OBSERVE_S = 65.0
#: how often the pre-open wait re-reads the clock
POLL_S = 5.0


def _now() -> float:
    return time.time()


def _stamp(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or _now()))


def frozen_slug() -> Dict[str, Any]:
    """The ONE market. No default, because a default is a substitution."""
    slug = os.getenv("PMUS_EXEC1_GATE_EVIDENCE", "").strip()
    if not slug:
        return {"ok": False, "slug": None,
                "why": ("PMUS_EXEC1_GATE_EVIDENCE is empty. XR2 will not "
                        "fall back to the current window: that is a "
                        "substitution, and the slug must be frozen before "
                        "the window is observed.")}
    return {"ok": True, "slug": slug,
            "window_start_unix": AB.slug_start(slug),
            "no_discovery": True, "no_substitution": True,
            "no_roll_forward": True}


def _await_window(slug: str, window_start: Optional[float], *,
                  max_wait_s: float = MAX_PREOPEN_WAIT_S,
                  sleep: Callable[[float], None] = time.sleep,
                  now: Optional[Callable[[], float]] = None
                  ) -> Dict[str, Any]:
    """Wait for THIS window to open. Never for a different one.

    Returns as soon as the window is open (TTE <= the window length). If the
    window is already past its placement cutoff on arrival, or the wait bound
    runs out, it says so and the caller SKIPs -- it does not advance to the
    next window, which does not exist as far as this function is concerned.
    """
    now = now or _now
    if window_start is None:
        return {"opened": False, "waited_s": 0.0,
                "why": "WINDOW_START_UNREADABLE: cannot time this slug"}
    deadline = now() + max_wait_s
    waited_from = now()
    polls = 0
    while True:
        tte = T.tte_s(window_start, now())
        if tte is not None and tte <= T.WINDOW_LEN_S:
            return {"opened": True, "polls": polls,
                    "waited_s": round(now() - waited_from, 1),
                    "tte_at_open_s": round(tte, 3), "slug_waited_for": slug}
        if now() >= deadline:
            return {"opened": False, "polls": polls,
                    "waited_s": round(now() - waited_from, 1),
                    "why": "PREOPEN_WAIT_EXHAUSTED after %.0fs; the frozen "
                           "window had not opened. SKIP, never the next one."
                           % max_wait_s}
        polls += 1
        sleep(min(POLL_S, max(0.0, deadline - now())))


def make_tte_guard(window_start: Optional[float], *,
                   now: Optional[Callable[[], float]] = None
                   ) -> Callable[[], Dict[str, Any]]:
    """The just-in-time guard. THE invariant lives here.

    Called immediately before the latch is consumed and again immediately
    before the POST. Unknown TTE is a refusal, not a pass.
    """
    clock = now or _now

    def guard() -> Dict[str, Any]:
        tte = T.tte_s(window_start, clock())
        v = T.placement_allowed(tte)
        return {"ok": bool(v["allowed"]), "tte_s": v["tte_s"],
                "reason": v["reason"], "checked_utc": _stamp(clock()),
                "invariant": ("no submission when TTE <= %.1fs"
                              % T.PLACEMENT_CUTOFF_TTE_S)}
    return guard


def run_experiment(*, observe_s: float = OBSERVE_S,
                   book_wait_s: float = BOOK_WAIT_S,
                   max_preopen_wait_s: float = MAX_PREOPEN_WAIT_S,
                   transport: Optional[Any] = None,
                   book: Optional[Any] = None) -> Dict[str, Any]:
    assert_frozen()
    rec: Dict[str, Any] = {
        "probe": PROBE, "spec_fingerprint": XR2_FINGERPRINT,
        "started_utc": _stamp(), "orders_submitted": 0,
        "matched_pair_economics": "N/A_SINGLE_LEG_EXPERIMENT",
        "matched_is_not_zero_it_is_not_applicable": True,
        "stopped_at": None, "refusals": [], "tte_checks": [],
    }
    order: Optional[OrderRecord] = None
    order_id: Optional[str] = None
    slug: Optional[str] = None
    t = transport or O.BoundedOrderTransport()
    owned_book = book is None
    bk = book or AB.AuthBook().start()
    try:
        # --- the ONE frozen market ----------------------------------------
        mk = frozen_slug()
        rec["market"] = mk
        if not mk["ok"]:
            rec["refusals"].append("NO_FROZEN_SLUG")
            rec["stopped_at"] = "NO_FROZEN_SLUG"
            return rec
        slug = mk["slug"]
        ws = mk["window_start_unix"]

        # --- wait for it, and only it -------------------------------------
        wait = _await_window(slug, ws, max_wait_s=max_preopen_wait_s)
        rec["preopen_wait"] = wait
        if not wait["opened"]:
            rec["refusals"].append(wait["why"])
            rec["stopped_at"] = "WINDOW_NEVER_OPENED"
            return rec

        # --- CHECK 1: run start, exactly as XR did ------------------------
        tte0 = T.tte_s(ws, _now())
        place_ok = T.placement_allowed(tte0)
        rec["timing"] = {"tte_s": tte0, "placement": place_ok,
                         "withdraw": T.must_withdraw(tte0)}
        rec["tte_checks"].append({"when": "RUN_START", **place_ok})
        if not place_ok["allowed"]:
            rec["refusals"].append(place_ok["reason"])
            rec["stopped_at"] = "TIMING"
            return rec

        # --- the frozen side ----------------------------------------------
        sid = os.getenv("PMUS_EXEC1_SESSION_ID", "").strip()
        rec["side_rule"] = (S.side_for(slug, sid) if sid else
                            {"error": "PMUS_EXEC1_SESSION_ID unset; the "
                                      "frozen side rule has no input"})
        if "error" in rec["side_rule"]:
            rec["refusals"].append("SIDE_RULE_INPUT_MISSING")

        try:
            venue = read_venue(t, slug)
        except Exception as exc:                               # noqa: BLE001
            venue = {"error": type(exc).__name__}
        rec["venue"] = venue

        fresh = await_fresh_book(bk, slug, deadline_s=book_wait_s)
        rec["book_wait"] = {k: v for k, v in fresh.items() if k != "snapshot"}
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
            "side": side_token, "side_label": rec["side_rule"].get("label"),
            "price": str(price), "quantity": str(VENUE_MIN_QTY),
            "venue_bid_yes": str(snap["bid"]), "venue_ask_yes": str(snap["ask"]),
            "own_side_touch": str(price),
            "own_side_opposite_touch": None if opp is None else str(opp),
            "spread": str(D(str(snap["ask"])) - D(str(snap["bid"]))),
            "book_age_s": fresh["age_s"], "book_timestamp_unix": book_t,
            "depth_ahead_anonymous": depth_ahead_at(snap, side_token),
            "depth_ahead_is_not_our_queue_position": True,
            "tte_s": T.tte_s(ws, decision_t),
            "offset_ticks_from_touch": 0,
            "bbo_source": "l5_pmus_auth_book.AuthBook",
        }

        checks = O.preflight_submission(
            slug=slug, outcome_side=side_token, price=price,
            qty=VENUE_MIN_QTY, min_qty=venue.get("minimumTradeQty"),
            tick=venue.get("tick"), book_bid=price, book_ask=opp,
            book_t=book_t, now=_now())
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
            # Report what the just-in-time guard WOULD have said, so a
            # disarmed run still demonstrates checks 2 and 3 rather than
            # merely asserting they exist.
            rec["tte_recheck_would_say"] = make_tte_guard(ws)()
            rec["tte_checks"].append({"when": "PRE_LATCH_AND_PRE_POST_DRY",
                                      **rec["tte_recheck_would_say"]})
            return rec

        rec["reached_final_gate"] = True
        guard = make_tte_guard(ws)
        try:
            out = O.submit_one(t, slug=slug, outcome_side=side_token,
                               price=price, qty=VENUE_MIN_QTY,
                               checks=checks, guard=guard)
        except O.GuardRefused as exc:
            # CHECK 2 or CHECK 3 said no. Which one it was is visible in the
            # message, and the latch state says whether it cost a run.
            rec["refusals"].append("TTE_RECHECK_REFUSED:%s" % exc)
            rec["stopped_at"] = "TTE_RECHECK_REFUSED"
            rec["latch_after_refusal"] = L.state()
            rec["tte_checks"].append({"when": "PRE_LATCH_OR_PRE_POST",
                                      "ok": False, "detail": str(exc)})
            return rec
        except O.MutationRefused as exc:
            rec["refusals"].append("REFUSED:%s" % exc)
            rec["stopped_at"] = "REFUSED_AT_SUBMIT"
            return rec
        except L.LatchSpent as exc:
            rec["refusals"].append("DURABLE_LATCH_SPENT:%s" % exc)
            rec["stopped_at"] = "LATCH_SPENT"
            return rec
        except Exception as exc:                               # noqa: BLE001
            rec["ambiguous_submission"] = {
                "error": type(exc).__name__, "detail": str(exc)[:300],
                "latch": L.state(),
                "MEANING": ("we do not know whether an order rested. No "
                            "retry is possible or permitted."),
            }
            rec["refusals"].append("AMBIGUOUS_SUBMISSION")
            rec["stopped_at"] = "AMBIGUOUS_SUBMISSION"
            return rec

        rec["orders_submitted"] = 1
        rec["submission"] = out
        rec["tte_checks"].append({"when": "PRE_LATCH_AND_PRE_POST",
                                  "ok": True,
                                  "detail": "both rechecks passed"})
        order_id = out.get("order_id")
        submitted_t = _now()
        order = OrderRecord("exec1xr2", slug, side_token, price, VENUE_MIN_QTY)
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
                    marks[h] = (our_price(s2, side_token)
                                if s2 is not None and a2 is not None
                                and float(a2) <= 2.0 else None)
            try:
                st = t.request("GET", "/v1/order/%s" % order_id)
            except Exception as exc:                           # noqa: BLE001
                events.append({"t": _stamp(),
                               "read_error": type(exc).__name__})
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
                    rec.setdefault("unresolved_role_fills", []).append(
                        {"at": _stamp(), "qty": str(delta), "venue_role": vr})
                    order.filled_qty += delta
                    order.advance("FILLED" if order.filled_qty
                                  >= order.intended_qty
                                  else "PARTIALLY_FILLED", at=_now(),
                                  detail={"role": "UNRESOLVED"})
                rec["venue_confirmed_liquidity_role"] = vr
            if T.must_withdraw(T.tte_s(ws, _now()))["withdraw"]:
                events.append({"t": _stamp(),
                               "withdrawal": "TTE boundary reached"})
                break
        rec["lifecycle_events"] = events
        rec["rest_time_s"] = round(_now() - submitted_t, 3)
        rec["markouts"] = markouts(order, marks)
        rec["markout_horizons_elapsed"] = sorted(marks)
        rec["markout_horizons_not_elapsed"] = [h for h in horizons
                                               if h not in marks]
        rec["markout_note"] = ("a horizon with no fresh two-sided "
                               "authenticated book is missing, never "
                               "interpolated")
        rec["order_state"] = order.state
        rec["filled_qty"] = str(order.filled_qty)
        rec["account"] = exec_account(order)
        rec["settlement"] = {"observed": False,
                             "note": ("reported when observed; not REALIZED "
                                      "until FILLED+SETTLED+CASH_CONFIRMED")}
        return rec
    finally:
        dm: Dict[str, Any] = {"attempted_utc": _stamp(),
                              "may_create_an_order": False}
        ambiguous = bool(rec.get("ambiguous_submission"))
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
        print("EXEC1XR2_RECORD " + json.dumps(rec, default=str), flush=True)
