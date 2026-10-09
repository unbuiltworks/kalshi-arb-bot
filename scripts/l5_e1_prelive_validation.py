"""E1 PRE-LIVE VALIDATION. Read-only, against the REAL PMUS venue.

Six things, in one run, with nothing armed and nothing submitted:

  3. the repaired order-state reader, against the live venue, on the four
     endpoints that actually exist
  2. the order registry restored from the DURABLE TAPE on this volume --
     the real forty 1215z/1230z ids
  5. a live-book SHADOW WINDOW: the actual executable complement price from
     the real book, available depth, the fee model and the $0.96
     fee-inclusive pair ceiling, under unchanged limits
  6. proof that shared-account activity cannot contaminate E1 inventory or
     P&L
  7. the cohort's remaining windows and outstanding exposure
  4. the eligibility and complement gates, on live state

It uses the production functions -- `order_state.read_order_state`,
`order_registry.load_from_tape`, `runner.eligible`,
`runner.complement_price`, `runner.complement_tranche` -- so what it
measures is what would run. It constructs no order body, builds no mutating
transport, and the only writes anywhere are the log lines it prints.

E1 STAYS DISARMED. This script never reads an arming key, never consumes a
latch slot and never advances a window.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List

from analysis.l5.e1_cohort import order_registry as ORG
from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import settlement_audit as SA
from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.spec import (E1_FINGERPRINT, FIRST_LEG_QTY,
                                        MAX_COHORT_REALIZED_LOSS_USD,
                                        MAX_EXPOSURE_PER_WINDOW_USD,
                                        MAX_FIRST_LEG_PRICE,
                                        PAIR_COST_CEILING)
from src import pmus_auth as A

STATE_DIR = os.environ.get("E1_STATE_DIR", "/state/pmus-ws/e1_cohort")
#: terminal orders are read once; a second read proves the saving. This is
#: the SAME durable cache the armed provider uses, loaded from the same
#: volume directory -- so a read-only run exercises the real path and
#: leaves the venue-confirmed terminal rows behind for the armed boot,
#: instead of making every process pay forty cold GETs and be 429'd.
TERMINAL_CACHE: Dict[str, Dict[str, Any]] = {}
SESSION = os.environ.get("PMUS_E1_SESSION_ID") or None


def j(tag: str, obj: Any) -> None:
    print("%s %s" % (tag, json.dumps(obj, default=str)))


def main() -> int:
    t = A.ReadOnlyTransport()
    reader = OS.venue_reader(t)
    _loaded = OS.load_terminal_cache(STATE_DIR, session_id=SESSION)
    TERMINAL_CACHE.update(_loaded["cache"])
    j("TERMINAL_CACHE_LOAD", _loaded["report"])
    print("PRELIVE_BEGIN now=%s fingerprint=%s"
          % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             E1_FINGERPRINT))
    j("LIMITS", {"qty": str(FIRST_LEG_QTY),
                 "max_first_leg_price": str(MAX_FIRST_LEG_PRICE),
                 "pair_cost_ceiling": str(PAIR_COST_CEILING),
                 "max_window_exposure": str(MAX_EXPOSURE_PER_WINDOW_USD),
                 "max_cohort_loss": str(MAX_COHORT_REALIZED_LOSS_USD),
                 "UNCHANGED": True})

    # ---------------------------------------------------------------- (2)
    reg = ORG.load_from_tape(STATE_DIR, session_id=SESSION)
    j("REGISTRY", {"tape": reg["tape"], "tape_exists": reg["tape_exists"],
                   "rows_scanned": reg["rows_scanned"],
                   "unparseable_rows": reg["unparseable_rows"],
                   "session_id": reg["session_id"],
                   "n_known_ids": len(reg["known_ids"]),
                   "known_ids": reg["known_ids"],
                   "n_pending_intents": len(reg["pending"]),
                   "pending": reg["pending"],
                   "event_counts": reg["event_counts"]})

    # ---------------------------------------------------------------- (7)
    try:
        from analysis.l5.e1_cohort import latch as LATCH
        from analysis.l5.e1_cohort.progress import CohortProgress
        prog = CohortProgress(LATCH.LATCH_DIR)
        j("COHORT", {"session_id": prog.session_id,
                     "windows_used": prog.windows_used(),
                     "windows_remaining": prog.windows_remaining(),
                     "placements": prog.placements(),
                     "realized_loss_usd": str(prog.realized_loss_usd()),
                     "unresolved_residuals": prog.unresolved_residuals(),
                     "slugs_used": prog.slugs_used()})
    except Exception as exc:                                   # noqa: BLE001
        j("COHORT_ERR", {"error": type(exc).__name__,
                         "detail": str(exc)[:200]})

    # ---------------------------------------------------------------- (3)
    ws = PI.window_start(time.time())
    slugs = [PI.slug_for(ws + i * PI.WINDOW_S) for i in range(4)]
    t0 = time.time()
    view = OS.read_order_state(reader, slugs=slugs,
                               outcome_up="OUTCOME_SIDE_YES",
                               now=t0, now_fn=time.time,
                               known_ids=reg["known_ids"],
                               roles=reg["roles"],
                               terminal_cache=TERMINAL_CACHE,
                               pending_ids=reg["pending"])
    # ---- THE ARMED STARTUP CALL, REPLICATED EXACTLY --------------------
    #
    # THE BLIND SPOT THAT HID A REAL DEFECT. The pass below this one reads
    # with `outcome_up="OUTCOME_SIDE_YES"` and a scope of the four UPCOMING
    # slugs, so all forty historical orders fell outside the scope filter
    # and it reported `n_known: 0` -- nothing to price, nothing to catch.
    # `run_cohort` reconciles BEFORE it opens a window, so the real call has
    # NO pin: `slugs` empty and `outcome_up` None, with every known id in
    # scope. That is the call that refused the armed probe at 20:30:43Z
    # with ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE, so it is the call
    # this validator has to make.
    t_s = time.time()
    sview = OS.read_order_state(reader, slugs=[], outcome_up=None,
                                now=t_s, now_fn=time.time,
                                known_ids=reg["known_ids"],
                                roles=reg["roles"],
                                terminal_cache=TERMINAL_CACHE,
                                pending_ids=reg["pending"])
    j("STARTUP_PATH", {
        "REPLICATES": "run_cohort's reconcile_at_startup, pin-free",
        "outcome_up": None, "slugs": [], "no_window_pin": True,
        "n_known_ids_passed": len(reg["known_ids"]),
        "ok": sview.get("ok"), "reason": sview.get("reason"),
        "latency_s": round(time.time() - t_s, 3),
        "n_known": sview.get("n_known"),
        "per_id_gets_this_read": sview.get("per_id_gets_this_read"),
        "ids_served_from_terminal_cache":
            len(sview.get("ids_served_from_terminal_cache") or []),
        "cached_but_resting_re_read":
            sview.get("cached_but_resting_re_read"),
        "n_outstanding": sview.get("n_outstanding"),
        "n_resolved_history": sview.get("n_resolved_history"),
        "historical_resolved_filled_qty":
            sview.get("historical_resolved_filled_qty"),
        "held_slugs": sview.get("held_slugs"),
        "residual": sview.get("residual"),
        "aggregate_exposure_usd": sview.get("aggregate_exposure_usd"),
        "reconcile_at_startup": OS.reconcile_at_startup(sview),
        "THIS_IS_THE_GATE_THAT_BLOCKED_THE_PROBE": True})

    j("READER", {"ok": view.get("ok"), "reason": view.get("reason"),
                 "latency_s": round(time.time() - t0, 3),
                 "age_s": view.get("age_s"),
                 "paths": view.get("paths"),
                 "n_known": view.get("n_known"),
                 "n_outstanding": view.get("n_outstanding"),
                 "n_outstanding_first_legs":
                     view.get("n_outstanding_first_legs"),
                 "n_outstanding_complements":
                     view.get("n_outstanding_complements"),
                 "ids_absent_at_venue_404":
                     len(view.get("ids_absent_at_venue_404") or []),
                 "filled_ids": len(view.get("filled_ids") or []),
                 "net_filled_by_outcome_side":
                     view.get("net_filled_by_outcome_side"),
                 "residual": view.get("residual"),
                 "venue_positions": view.get("venue_positions"),
                 "derived_net_qty": view.get("derived_net_qty"),
                 "venue_net_qty": view.get("venue_net_qty"),
                 "open_notional_usd": view.get("open_notional_usd"),
                 "filled_notional_usd": view.get("filled_notional_usd"),
                 "aggregate_exposure_usd":
                     view.get("aggregate_exposure_usd")})
    # THE SECOND READ, to show the terminal cache doing its job and the
    # answer not changing.
    t1 = time.time()
    view2 = OS.read_order_state(reader, slugs=slugs,
                                outcome_up="OUTCOME_SIDE_YES",
                                now=t1, now_fn=time.time,
                                known_ids=reg["known_ids"],
                                roles=reg["roles"],
                                terminal_cache=TERMINAL_CACHE,
                                pending_ids=reg["pending"])
    j("READER_SECOND_PASS", {
        "ok": view2.get("ok"),
        "latency_s": round(time.time() - t1, 3),
        "per_id_gets_this_read": view2.get("per_id_gets_this_read"),
        "ids_served_from_terminal_cache":
            len(view2.get("ids_served_from_terminal_cache") or []),
        "same_answer": (view2.get("n_outstanding") ==
                        view.get("n_outstanding")
                        and view2.get("net_filled_by_outcome_side") ==
                        view.get("net_filled_by_outcome_side"))})

    j("GATES", {"outstanding_for_snapshot":
                    OS.outstanding_for_snapshot(view),
                "reconcile_at_startup": OS.reconcile_at_startup(view),
                "complement_gate": OS.complement_gate(view),
                "window_exposure": OS.window_exposure(
                    view, prospective_qty=str(FIRST_LEG_QTY),
                    prospective_price="0.50")})

    # ---------------------------------------------------------------- (6)
    # SHARED-ACCOUNT CONTAMINATION. The settlement reader is handed OUR
    # filled quantity and must refuse to attribute anything that does not
    # match it exactly. This asks the live venue for every cohort market.
    # ONE WALK of the activities feed, shared by every market below. The
    # previous run walked it three times in a few seconds and the venue
    # answered the second and third with an HTTPError.
    # THE FILTERED, RESUMABLE, 429-AWARE SCANNER -- not the plain walk.
    #
    # `OS.fetch_resolutions` restarts from page one every run, so each
    # attempt spent its page budget re-reading what the last one already
    # had and the venue answered 429 at around page twenty: the audit
    # reported ACTIVITIES_UNREADABLE every time and attributed nothing.
    # `settlement_audit.scan_resolutions` asks the venue for
    # `&types=ACTIVITY_TYPE_POSITION_RESOLUTION` only, backs off
    # exponentially within a bounded budget, and keeps its progress on the
    # durable volume -- so a 429 RESUMES rather than starting over, and
    # successive runs converge instead of repeating.
    scan_state = os.path.join(STATE_DIR, "e1.resolution_scan.json")
    # REFRESH, always. A finished scan is a snapshot of the moment it
    # finished; our own pairs settle after it. Durable progress still
    # earns its keep by surviving a 429 mid-walk.
    scan = SA.scan_resolutions(reader, state_path=scan_state,
                               refresh=True, sleep_fn=time.sleep)
    res = {"rows": scan["rows"], "complete": scan["complete"],
           "pages_read": scan["pages_read"],
           "error": (scan["errors"][0]["error"] if scan["errors"]
                     else None)}
    j("ACTIVITIES_WALK", {"pages_read": scan.get("pages_read"),
                          "complete": scan.get("complete"),
                          "resolution_rows": len(scan.get("rows") or []),
                          "filter": scan.get("filter"),
                          "filter_honoured": scan.get("filter_honoured"),
                          "resumed_from_page": scan.get("resumed_from_page"),
                          "rate_limit_hits": scan.get("rate_limit_hits"),
                          "state_path": scan_state,
                          "bounded_retries": True,
                          "error": res.get("error")})
    # THE 22:00z PROBE'S OWN PAIR is audited first: 0.13 shares on each
    # side, venue-confirmed FILLED, gross $0.1248. Expected settlement
    # quantity is the filled quantity of ONE side of the matched set.
    for slug, expected in (
            ("cpc-btc-updown-15m-2026-10-08-2200z", "0.13"),
            ("cpc-btc-updown-15m-2026-10-08-1215z", "2.60"),
            ("cpc-btc-updown-15m-2026-10-08-1230z", "0")):
        st = OS.settlement_for(reader, slug=slug, expected_qty=expected,
                               resolutions=res)
        j("SETTLEMENT", {"slug": slug, "expected_qty": expected,
                         "label": st.get("label"),
                         "rows_seen": len(st.get("rows") or []),
                         "attributed": st.get("attributed"),
                         "error": st.get("error"),
                         "NEVER_APPORTIONED": st.get("NEVER_APPORTIONED")})
    # and a deliberate mismatch: ask for a quantity that is NOT ours
    bad = OS.settlement_for(reader, slug="cpc-btc-updown-15m-2026-10-08-"
                                        "1215z", expected_qty="16.21",
                            resolutions=res)
    j("CONTAMINATION_CONTROL", {
        "asked_for_a_quantity_that_is_not_ours": "16.21",
        "attributed": bad.get("attributed"),
        "label": bad.get("label"),
        "reading": "if this attributes anything, exact-match attribution "
                   "is broken"})

    # ---------------------------------------------------------------- (5)
    # THE LIVE-BOOK SHADOW WINDOW. Real book, real depth, real tick, the
    # production complement pricer and the frozen ceilings. Nothing is
    # submitted; this says what WOULD be decided.
    feed = PBK.PmusDecisionFeed(clock=time.time)
    try:
        feed.start()
    except Exception as exc:                                   # noqa: BLE001
        j("FEED_ERR", {"error": type(exc).__name__})
    for slug in slugs:
        pin = {"slug": slug, "outcome_up": "OUTCOME_SIDE_YES",
               "outcome_down": "OUTCOME_SIDE_NO",
               "window_start": ws, "window_end": ws + PI.WINDOW_S}
        deadline = time.time() + 25.0
        health = {}
        while time.time() < deadline:
            health = feed.health(pin)
            if health.get("ok"):
                break
            time.sleep(0.5)
        if not health.get("ok"):
            j("SHADOW", {"slug": slug, "book": "UNAVAILABLE",
                         "why": health.get("why"),
                         "timing_authority": health.get("timing_authority")})
            continue
        book = feed.book(pin)
        if not book.get("ok"):
            j("SHADOW", {"slug": slug, "book": "NOT_READABLE",
                         "why": book.get("why")})
            continue
        fl = book.get("first_leg") or {}
        comp = book.get("complement") or {}
        row: Dict[str, Any] = {
            "slug": slug,
            "first_leg_side": (book.get("first_leg_side_choice") or {}
                               ).get("side"),
            "first_leg_best_bid": fl.get("best_bid"),
            "first_leg_best_ask": fl.get("best_ask"),
            "first_leg_depth_at_our_price": fl.get("size_at_our_price"),
            "complement_best_bid": comp.get("best_bid"),
            "complement_best_ask": comp.get("best_ask"),
            "complement_depth": comp.get("size_at_our_price"),
            "book_age_s": book.get("age_s"),
        }
        bid = fl.get("best_bid")
        if bid is not None:
            row["first_leg_within_ceiling"] = (
                D(str(bid)) <= MAX_FIRST_LEG_PRICE)
            # THE ACTUAL EXECUTABLE COMPLEMENT PRICE, from the production
            # pricer, against the real complement bid.
            try:
                cp = R.complement_price(
                    first_leg_price=str(bid), qty=str(FIRST_LEG_QTY),
                    complement_best_bid=str(comp.get("best_bid") or "0"))
                row["complement_price_usd"] = cp["price_usd"]
                row["pair_cost"] = cp["pair_cost"]
                row["within_pair_ceiling"] = cp["pair_cost"].get(
                    "within_ceiling")
                row["maker_only"] = cp["maker_only"]
                row["at_or_below_complement_best_bid"] = cp[
                    "at_or_below_complement_best_bid"]
            except Exception as exc:                           # noqa: BLE001
                row["complement"] = "INFEASIBLE"
                row["complement_refusal"] = str(exc)[:220]
                row["ceiling_relaxed"] = False
            # and the tranche the production sizer would send
            try:
                plan = R.complement_tranche(
                    cumulative_first_leg_filled_qty=str(FIRST_LEG_QTY),
                    already_complemented_qty="0", tranches_submitted=0)
                row["tranche_plan"] = plan
            except Exception as exc:                           # noqa: BLE001
                row["tranche_plan_error"] = type(exc).__name__
        j("SHADOW", row)

        # WHAT `eligible` WOULD SAY, on the LIVE book and LIVE order state.
        #
        # `ws_state` IS BUILT THE WAY `pmus_provider` BUILDS IT, field for
        # field, because the previous run read it off `book` -- where it
        # does not exist -- and produced E7 refusals that were my
        # harness's fault rather than the engine's judgement. A shadow that
        # reports a refusal the engine would not make is worse than no
        # shadow: it hides whatever the real answer was.
        ws_state = {
            "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
            "pinned_market_streaming": True,
            "book_unix": book.get("book_unix"),
            "market": slug,
            "identity": "slug_plus_outcome_side",
            "ctf_token_ids_used": False,
            "rest_is_sufficient_timing_authority": False,
            "health": health,
            "first_leg": {"best_bid": fl.get("best_bid"),
                          "best_ask": fl.get("best_ask")},
            "complement": {"best_bid": comp.get("best_bid"),
                           "best_ask": comp.get("best_ask")},
            "first_leg_side": fl.get("side"),
        }
        snap = {"probe": "COHORT-E1-LIVE", "venue": "polymarket_us",
                "window": 0, "window_open": True,
                "within_handover_tte": True, "market": slug,
                "pin": pin, "qty": str(FIRST_LEG_QTY),
                "first_leg_price": str(bid) if bid is not None else None,
                "first_leg": fl, "complement": comp,
                "ws_state": ws_state,
                "fingerprint_pinned": None,
                "order_state": view,
                "our_outstanding_orders":
                    OS.outstanding_for_snapshot(view),
                "our_open_residual": OS.residual_for_snapshot(view)}
        el = R.eligible(snap, SP.CohortTracker(), now=time.time())
        # THE ONLY REFUSALS A DISARMED, UNPINNED BUILD SHOULD PRODUCE on a
        # tradeable book. Anything else is a real finding.
        expected = {"E1_DISARMED",
                    "E1_FINGERPRINT_NOT_PINNED_TO_THIS_BUILD"}
        got = {str(r).split(":")[0] for r in el["refusals"]}
        j("SHADOW_ELIGIBLE", {
            "slug": slug, "ok": el["ok"], "refusals": el["refusals"],
            "ws_state_built_as_the_provider_builds_it": True,
            "only_arming_and_pinning_stand_in_the_way":
                got.issubset(expected),
            "unexpected_refusals": sorted(got - expected),
            "reading": ("if only_arming_and_pinning_stand_in_the_way is "
                        "true, the engine would have placed this pair had "
                        "the owner armed it -- and nothing else was "
                        "blocking")})

    # PERSIST WHAT THE VENUE CONFIRMED AS FINISHED. Writing terminal
    # order rows is not a trading action: it is the same save the armed
    # provider does, and it is what spares the next process the forty
    # cold GETs that the venue answered with 429.
    j("TERMINAL_CACHE_SAVE",
      OS.save_terminal_cache(TERMINAL_CACHE, STATE_DIR, session_id=SESSION))

    j("NOTE", {"armed": False, "orders_submitted": 0,
               "orders_cancelled": 0, "latch_slots_consumed": 0,
               "windows_advanced": 0, "limits_changed": False,
               "READ_ONLY": True})
    print("PRELIVE_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
