"""E1's window provider, on PMUS identity and PMUS's own book.

This replaces `provider.LiveWindowProvider`, which selected on
`gamma-api.polymarket.com`, proved readiness on Polymarket.com's CLOB
websocket keyed by CTF token ids, and then handed that slug to
`api.polymarket.us`. The venue answered "Market not found".

WHAT IS PRESERVED, deliberately and by reuse:

  * the window BUDGET comes from durable progress, so a restart never
    grants another twelve;
  * readiness is BOUNDED by wall clock AND poll count, because `now_fn` is
    injected and a frozen clock never reaches a deadline -- that bug hung
    the suite once already;
  * a feed that never starts is TERMINAL for the sweep, not a market
    refusal: the remaining candidates are future windows that definitionally
    are not streaming yet, so enumerating them in milliseconds only
    manufactures false refusals;
  * the per-snapshot freshness re-check stays strict and immediate, because
    that IS the no-stale-book guarantee;
  * `eligible()` re-decides everything. This module proposes; it never
    concludes.

WHAT CHANGES. Identity is `slug` + `outcomeSide`, never a token id. The
readiness question is no longer "are both tokens streaming" but "is THIS
slug's book connected, two-sided and fresh", because PMUS quotes a market
once and the NO side is the arithmetic complement of the YES side -- the
venue's own convention, as `pmus_q1_executor.would_cross` states it.

THE BOOK THAT CAUSES A DECISION NAMES ITS OWN SLUG, and this module
asserts that name equals the pinned slug before it builds a snapshot. That
assertion is the whole point of the rebuild: the market we price is the
market we order.
"""
from __future__ import annotations

import time
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Callable, Dict, List, Optional

from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort import pmus_selector as SEL
from analysis.l5.e1_cohort.spec import (FIRST_LEG_QTY, PROBE, VENUE_TICK,
                                        WINDOWS)

#: the bounded readiness wait. Same values E1 already shipped.
WS_READY_TIMEOUT_S = 20.0
WS_READY_POLL_S = 0.25

#: how long before a window's end E1 stops opening first legs.
HANDOVER_TTE_S = 60.0


class WindowUnavailable(Exception):
    """No usable snapshot for this window right now."""


class FeedNotReady(WindowUnavailable):
    """The FEED never proved itself within the bounded wait. TERMINAL for
    the sweep: a feed that has not started is not a market refusal."""


def first_leg_price_from_book(leg: Dict[str, Any]) -> D:
    """Offset 0 at the same-side best bid, on the venue tick.

    NO new pricing logic. `eligible()` re-checks every clause, and E8 now
    refuses an absent, zero or non-finite bid before this price can reach
    a preview.
    """
    bid = leg.get("best_bid")
    if bid in (None, ""):
        raise WindowUnavailable("no best bid on the selected market")
    try:
        d = D(str(bid))
    except Exception:                                          # noqa: BLE001
        raise WindowUnavailable("best bid %r is not a number" % (bid,))
    if not d.is_finite():
        raise WindowUnavailable("best bid %r is not finite" % (bid,))
    return d.quantize(VENUE_TICK, rounding=ROUND_DOWN)


def await_pmus_book_ready(feed: Any, pin: Dict[str, Any], *, now_fn: Any,
                          sleep_fn: Any, timeout_s: Optional[float] = None,
                          poll_s: Optional[float] = None) -> Dict[str, Any]:
    """Wait, BOUNDED TWICE, for this slug's book to prove itself.

    The poll bound is not decoration. A wall-clock deadline alone is NOT a
    bound when `now_fn` is injected: against a frozen clock the deadline
    never arrives and the loop spins forever.
    """
    timeout_s = (WS_READY_TIMEOUT_S if timeout_s is None else float(timeout_s))
    poll_s = (WS_READY_POLL_S if poll_s is None else float(poll_s))
    deadline = now_fn() + timeout_s
    max_polls = max(1, int(timeout_s / max(poll_s, 1e-9)) + 1)
    polls = 0
    last: Dict[str, Any] = {}
    while True:
        polls += 1
        last = feed.health(pin)
        if last.get("ok"):
            last = dict(last)
            last["readiness"] = {"waited_for_the_pinned_market": True,
                                 "polls": polls, "timeout_s": timeout_s,
                                 "bounded": True}
            return last
        if now_fn() >= deadline or polls >= max_polls:
            raise FeedNotReady(
                "the PMUS book for %s did not become usable within %.3fs "
                "(%d polls); last reason: %s"
                % (pin.get("slug"), timeout_s, polls,
                   last.get("why", "unknown")))
        sleep_fn(poll_s)


class PmusWindowProvider:
    """At most `WINDOWS` DISTINCT eligible PMUS windows, one at a time."""

    def __init__(self, *, progress: Any, session_auth: Any,
                 feed: Any = None, recorder: Any = None,
                 selector_fn: Optional[Callable[[int], Dict[str, Any]]] = None,
                 now_fn: Any = None, sleep_fn: Any = None,
                 window_starts: Optional[List[int]] = None,
                 ws_ready_timeout_s: Optional[float] = None,
                 ws_ready_poll_s: Optional[float] = None,
                 order_state_reader: Any = None,
                 pending_order_ids: Any = None) -> None:
        self.progress = progress
        self.auth = session_auth
        self.recorder = recorder
        self.now_fn = now_fn or time.time
        self.sleep_fn = sleep_fn or time.sleep
        self.selector_fn = selector_fn or SEL.select_window
        self.window_starts = window_starts
        self.ws_ready_timeout_s = (WS_READY_TIMEOUT_S
                                   if ws_ready_timeout_s is None
                                   else ws_ready_timeout_s)
        self.ws_ready_poll_s = (WS_READY_POLL_S if ws_ready_poll_s is None
                                else ws_ready_poll_s)
        self.feed = feed if feed is not None else PBK.PmusDecisionFeed(
            clock=self.now_fn)
        # THE VENUE READER THAT FEEDS THE OUTSTANDING-ORDER INVARIANT.
        # `None` means "build the signed GET-only reader lazily", which is
        # what production does; the tests inject a callable. It is NEVER
        # replaced by a constant, which is the whole point of this field.
        self._order_state_reader = order_state_reader
        # locally recorded submission intents whose acknowledgement was
        # ambiguous. A list, shared by reference with the runner, so an
        # unconfirmed POST keeps refusing the next first leg.
        self.pending_order_ids: List[str] = (
            [] if pending_order_ids is None else pending_order_ids)
        self.last_order_state: Optional[Dict[str, Any]] = None
        self.current_pin: Optional[Dict[str, Any]] = None
        self.current_window_index: Optional[int] = None
        self.refusals: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ internals
    def order_state_reader(self) -> Any:
        if self._order_state_reader is None:
            self._order_state_reader = OS.venue_reader()
        return self._order_state_reader

    def order_state(self, pin: Dict[str, Any]) -> Dict[str, Any]:
        """The AUTHORITATIVE outstanding-order view for this window.

        Every failure -- an unreadable GET, an unparsable body, two
        endpoints disagreeing, a read that took too long -- comes back with
        `ok: False`, and `OS.outstanding_for_snapshot` then publishes a
        TRUTHY sentinel. There is no path from a failed read to a
        permissive zero. That asymmetry is the repair.
        """
        if self._order_state_reader is None and not OS.can_read_venue():
            # NO CREDENTIALS, NO NETWORK, NO GUESS. A process that cannot
            # authenticate cannot establish order state, so it publishes the
            # fail-closed sentinel instead of opening a socket to find out.
            # This is also why the offline tests make no network call.
            view = {"ok": False,
                    "reason": "ORDER_STATE_NO_CREDENTIALS",
                    "outstanding_sentinel": OS.UNKNOWN_OUTSTANDING,
                    "FAILS_CLOSED": True}
            self.last_order_state = view
            return view
        try:
            view = OS.read_order_state(
                self.order_state_reader(),
                slugs=[pin.get("slug")],
                outcome_up=pin.get("outcome_up"),
                now=self.now_fn(), now_fn=self.now_fn,
                pending_ids=list(self.pending_order_ids))
        except Exception as exc:                               # noqa: BLE001
            view = {"ok": False,
                    "reason": "ORDER_STATE_READER_RAISED:%s"
                              % type(exc).__name__,
                    "outstanding_sentinel": OS.UNKNOWN_OUTSTANDING,
                    "FAILS_CLOSED": True}
        self.last_order_state = view
        return view

    def _refuse(self, why: str, **extra: Any) -> None:
        rec = {"refused": why, "unix": self.now_fn(), **extra}
        self.refusals.append(rec)
        if self.recorder is not None:
            self.recorder.record_event(
                window=self.progress.next_window_index(), slot=-1,
                event="WINDOW_REFUSED", detail=rec)

    def _candidate_starts(self) -> List[int]:
        """Candidate window starts, with ALREADY-CONSUMED ONES REMOVED.

        The used-filter applies to an INJECTED list too. It used to apply
        only to the computed list, which meant a caller that supplied
        `window_starts` could re-open the same start over and over: the
        same slug opened twelve times, twelve distinct windows spent, one
        actual market observed. The budget is on DISTINCT windows, so the
        filter belongs on every path that produces candidates.
        """
        if self.window_starts is not None:
            out = [int(s) for s in self.window_starts]
        else:
            now = self.now_fn()
            out = [SEL.window_start_for(now)]
            out += [s for s in SEL.next_window_starts(now, 3)
                    if s not in out]
        used = set(int(s) for s in self.progress.window_starts_used())
        return [s for s in out if s not in used]

    def _window_is_live(self, pin: Dict[str, Any]) -> bool:
        return self.now_fn() < float(pin["window_end"])

    def _within_handover(self, pin: Dict[str, Any]) -> bool:
        return (float(pin["window_end"]) - self.now_fn()) > HANDOVER_TTE_S

    # ------------------------------------------------------------- windows
    def open_next_window(self) -> Optional[Dict[str, Any]]:
        if self.progress.windows_used() >= WINDOWS:
            self._refuse("COHORT_WINDOW_BUDGET_SPENT",
                         windows_used=self.progress.windows_used(),
                         windows_authorized=WINDOWS,
                         a_restart_grants_no_more=True)
            return None
        for start in self._candidate_starts():
            idx = self.progress.next_window_index()
            if idx >= WINDOWS:
                self._refuse("COHORT_WINDOW_BUDGET_SPENT")
                return None
            try:
                pin = self.selector_fn(start)
            except SEL.WindowRefused as exc:
                self._refuse("SELECTOR_REFUSED:%s" % exc,
                             window_start=start)
                continue
            # A DEAD WINDOW IS NOT A CANDIDATE. Opening one would spend a
            # window of the budget on a market that can never be traded.
            if not self._window_is_live(pin):
                self._refuse("WINDOW_ALREADY_ENDED", slug=pin.get("slug"),
                             window_start=start,
                             window_end=pin.get("window_end"),
                             now=self.now_fn())
                continue
            try:
                auth = self.auth.authorize_selected(pin)
            except Exception as exc:                           # noqa: BLE001
                self._refuse("NOT_AUTHORIZED:%s" % exc,
                             slug=pin.get("slug"), window_start=start)
                continue
            self.feed.start()
            try:
                health = await_pmus_book_ready(
                    self.feed, pin, now_fn=self.now_fn,
                    sleep_fn=self.sleep_fn,
                    timeout_s=self.ws_ready_timeout_s,
                    poll_s=self.ws_ready_poll_s)
            except FeedNotReady as exc:
                # TERMINAL. Not a market refusal.
                self._refuse("PMUS_FEED_NOT_READY:%s" % exc,
                             slug=pin.get("slug"),
                             terminal_for_this_sweep=True,
                             is_a_market_refusal=False,
                             is_a_feed_startup_failure=True,
                             candidates_not_enumerated=True)
                return None
            self.current_pin = pin
            self.current_window_index = idx
            self.progress.record_window_opened(
                window_index=idx, window_start=int(pin["window_start"]),
                slug=pin["slug"], market_id=str(pin.get("market_id")),
                outcome_up=pin.get("outcome_up"),
                outcome_down=pin.get("outcome_down"),
                selection_provenance=pin.get("selection_provenance"),
                authorization_result=auth.get("authorization_result"))
            if self.recorder is not None:
                self.recorder.record_event(
                    window=idx, slot=-1, event="WINDOW_AUTHORIZED",
                    detail={"slug": pin["slug"],
                            "market_id": pin.get("market_id"),
                            "outcome_up": pin.get("outcome_up"),
                            "outcome_down": pin.get("outcome_down"),
                            "identity": "slug_plus_outcome_side",
                            "ctf_token_ids_used": False,
                            "selection_provenance":
                                pin.get("selection_provenance"),
                            "pmus_book_health": health,
                            "window_start": pin["window_start"],
                            "window_end": pin["window_end"],
                            "authorization_result":
                                auth.get("authorization_result")})
            return pin
        self._refuse("NO_CANDIDATE_WINDOW_COULD_BE_OPENED")
        return None

    # ------------------------------------------------------------ snapshots
    def __call__(self) -> Optional[Dict[str, Any]]:
        pin = self.current_pin
        if pin is not None and not self._window_is_live(pin):
            self.progress.record_window_closed(
                window_index=self.current_window_index,
                reason="WINDOW_END_PASSED")
            self._refuse("STALE_WINDOW_ROLLED_OVER", slug=pin["slug"],
                         window_end=pin["window_end"])
            self.current_pin = None
            self.current_window_index = None
            pin = None
        if pin is None:
            pin = self.open_next_window()
            if pin is None:
                return None
        idx = self.current_window_index

        # STRICT AND IMMEDIATE. This is the no-stale-book guarantee, and it
        # is re-asked for every snapshot rather than trusted from opening.
        health = self.feed.health(pin)
        if not health.get("ok"):
            self._refuse("PMUS_BOOK_UNUSABLE_BEFORE_SUBMISSION:%s"
                         % health.get("why"), slug=pin["slug"])
            return None
        # REST IS NOT TIMING AUTHORITY, AND A CLAIM THAT IT IS FAILS
        # CLOSED. REST may identify a market and cross-check a price; it
        # may not be the feed a decision is timed off. A health record
        # naming any other authority -- or asserting REST is sufficient --
        # gets no snapshot, however fresh it claims to be.
        if str(health.get("timing_authority")) != PBK.TIMING_PMUS_AUTHENTICATED_WS:
            self._refuse("TIMING_AUTHORITY_IS_NOT_THE_PMUS_AUTHENTICATED_WS",
                         slug=pin["slug"],
                         got=health.get("timing_authority"),
                         want=PBK.TIMING_PMUS_AUTHENTICATED_WS)
            return None
        if health.get("rest_is_sufficient_timing_authority") is True:
            self._refuse("REST_CLAIMED_AS_TIMING_AUTHORITY",
                         slug=pin["slug"])
            return None

        book = self.feed.book(pin)
        if not book.get("ok"):
            self._refuse("PMUS_BOOK_NOT_READABLE:%s" % book.get("why"),
                         slug=pin["slug"])
            return None
        # THE IDENTITY ASSERTION THE WHOLE REBUILD EXISTS FOR.
        if str(book.get("market")) != str(pin["slug"]):
            self._refuse("BOOK_IS_FOR_ANOTHER_MARKET", slug=pin["slug"],
                         book_market=book.get("market"))
            return None
        up = dict(book.get("first_leg") or {})
        down = dict(book.get("complement") or {})
        for name, leg in (("FIRST_LEG", up), ("COMPLEMENT", down)):
            if str(leg.get("slug")) != str(pin["slug"]):
                self._refuse("LEG_SLUG_MISMATCH_%s" % name,
                             slug=pin["slug"], leg_slug=leg.get("slug"))
                return None
        # EITHER SIDE MAY OPEN, so the assertion is about the SET of the
        # two outcome sides rather than which one is in which slot: they
        # must be EXACTLY the pin's two, and they must be distinct. That is
        # strictly stronger than the old positional check, which could not
        # have caught both legs naming the same side.
        want_pair = {str(pin["outcome_up"]), str(pin["outcome_down"])}
        got_first = str(up.get("outcome_side"))
        got_comp = str(down.get("outcome_side"))
        if got_first == got_comp:
            self._refuse("BOTH_LEGS_NAME_THE_SAME_OUTCOME_SIDE",
                         slug=pin["slug"], got=got_first)
            return None
        if {got_first, got_comp} != want_pair:
            self._refuse("OUTCOME_SIDES_ARE_NOT_THE_AUTHORIZED_PAIR",
                         slug=pin["slug"],
                         got=sorted([got_first, got_comp]),
                         want=sorted(want_pair))
            return None

        try:
            price = first_leg_price_from_book(up)
        except WindowUnavailable as exc:
            self._refuse("NO_PRICE_FROM_THE_PMUS_BOOK:%s" % exc,
                         slug=pin["slug"])
            return None

        # ASKED ONCE PER SNAPSHOT, immediately before the snapshot is
        # built, for the same reason the book's freshness is re-asked here
        # rather than trusted from window-open: the only order state worth
        # anything is the state as of this decision.
        ostate = self.order_state(pin)

        return {
            "probe": PROBE,
            "venue": "polymarket_us",
            "market": pin["slug"],
            "pin": pin,
            "window": idx,
            "window_open": self._window_is_live(pin),
            "within_handover_tte": self._within_handover(pin),
            # THE TWO DEADLINES THE PACED DECISION LOOP NEEDS, carried
            # explicitly so `run_cohort` never has to reach into the pin or
            # re-derive a bound. `trading_cutoff_unix` is the last instant a
            # placement is permitted and is EXACTLY the instant
            # `within_handover_tte` flips, computed from the same two values,
            # so the pacing horizon and the eligibility rule can never
            # disagree. `window_end_unix` is when this window dies and the
            # provider rolls to the next one.
            "trading_cutoff_unix": float(pin["window_end"]) - HANDOVER_TTE_S,
            "window_end_unix": float(pin["window_end"]),
            # THE INVARIANT'S INPUT, FROM THE VENUE. These two fields
            # were the literals `0` and `None`, so `eligible()`'s E3 rule
            # read a constant and the declared one-outstanding-order bound
            # could not fire -- which is how the 1230z window on
            # 2026-10-08 came to hold twenty resting orders. They are now
            # derived from authoritative order state, count partial fills
            # as outstanding, count unconfirmed submissions as outstanding,
            # and publish a TRUTHY sentinel when state cannot be
            # established at all.
            "our_outstanding_orders": OS.outstanding_for_snapshot(ostate),
            "our_open_residual": OS.residual_for_snapshot(ostate),
            "order_state": ostate,
            # SHARED BY REFERENCE, deliberately. `submit_first_leg` appends
            # its submission intent to this very list, and the next
            # snapshot's order-state read sees it.
            "pending_order_ids": self.pending_order_ids,
            "aggregate_exposure_usd": ostate.get("aggregate_exposure_usd"),
            "qty": FIRST_LEG_QTY,
            "first_leg_price": str(price),
            # THE CHOSEN ORIENTATION, taken from the book rather than
            # hard-coded: `up` and `down` here are whichever legs the book
            # put in the first-leg and complement slots.
            "first_leg": {"side": up.get("side"),
                          "outcome_side": up.get("outcome_side"),
                          "slug": pin["slug"],
                          "best_bid": up.get("best_bid"),
                          "best_ask": up.get("best_ask"),
                          "size_at_our_price": up.get("size_at_our_price")},
            "complement": {"side": down.get("side"),
                           "outcome_side": down.get("outcome_side"),
                           "slug": pin["slug"],
                           "best_bid": down.get("best_bid"),
                           "best_ask": down.get("best_ask")},
            "first_leg_side_choice": book.get("first_leg_side_choice"),
            "fingerprint_pinned": None,
            "ws_state": {
                "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                "pinned_market_streaming": True,
                "book_unix": book.get("book_unix"),
                "market": pin["slug"],
                "identity": "slug_plus_outcome_side",
                "ctf_token_ids_used": False,
                "rest_is_sufficient_timing_authority": False,
                "health": health,
                "first_leg": {"best_bid": up.get("best_bid"),
                              "best_ask": up.get("best_ask")},
                "complement": {"best_bid": down.get("best_bid"),
                               "best_ask": down.get("best_ask")},
                "first_leg_side": up.get("side"),
            },
            "provider": ("analysis.l5.e1_cohort.pmus_provider:"
                         "PmusWindowProvider"),
            "LABEL": "SNAPSHOT -- eligibility is still eligible()'s to decide",
        }

    def report(self) -> Dict[str, Any]:
        return {"probe": PROBE,
                "venue": "polymarket_us",
                "windows_authorized": WINDOWS,
                "windows_used": self.progress.windows_used(),
                "windows_remaining": self.progress.windows_remaining(),
                "slugs_used": self.progress.slugs_used(),
                "current_slug": (self.current_pin or {}).get("slug"),
                "refusals": list(self.refusals),
                "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                "rest_is_sufficient": False,
                "identity": "slug_plus_outcome_side",
                "ctf_token_ids_used": False,
                "gamma_used": False,
                "clob_ws_used": False,
                "reuses_proven_infrastructure": [
                    "src.l5_pmus_transport:resolve_market (identity)",
                    "src.l5_pmus_auth_book:AuthBook (decision feed)"],
                "built_another_discovery_stack": False,
                "a_restart_grants_another_12_windows": False}
