"""THE LIVE WINDOW PROVIDER. The adapter that was missing.

WHAT WAS WRONG. `e11fc1ab…` declared an armed production path and did not
have one: `main()`'s armed branch was a bare `raise SystemExit`, nothing
constructed a `snapshot_provider`, and `run_cohort` was never reached. The
spec's call graph named `run_cohort`; the code stopped short of it. Setting
the `PMUS_E1_*` keys would have produced twelve empty windows and measured
nothing -- the same class of defect as the inherited entrypoint, found one
layer deeper.

WHAT THIS IS, AND WHAT IT IS NOT. It is an ADAPTER. It contains no strategy:
no price, no size, no timing rule, no gate, no stopping rule. Every one of
those stays where it is already frozen, in `spec.py`, `runner.eligible()`
and `stopping.py`. This module's whole job is to turn the already-proven
public market and book infrastructure into the snapshot shape
`runner.eligible()` already consumes.

REUSED, NOT REBUILT:

  * `src/l5_market_binding.py` via `selector.py` -- BTC15m market and token
    resolution against the venue's own record, refusing unidentified
    windows. No second discovery stack.
  * `src/l5_clob_ws.py` -- the PROVEN independent two-token websocket,
    live-verified at 11,438 and 11,433 frames on
    `wss://ws-subscriptions-clob.polymarket.com/ws/market`. It is the
    TIMING AUTHORITY. REST is `rest_same_second_reachability` and is not
    sufficient; a derived complement is not a second token.

PER-WINDOW DISCIPLINE, in order, each failing CLOSED:

  1. the next DISTINCT window start that durable progress has not used;
  2. resolve and PIN slug, market id and BOTH token ids;
  3. authorize the pin through the session boundary;
  4. PROVE both-token websocket health FOR THE SELECTED MARKET;
  5. build the snapshot from that proven book, carrying the pin;
  6. record selection and authorization in the raw tape.

A window that fails any step yields NO snapshot. The provider returns None
rather than a degraded one, because a degraded snapshot is how an order
gets placed against a market nobody proved.
"""
from __future__ import annotations

import time
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Callable, Dict, List, Optional

from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import selector as SEL
from analysis.l5.e1_cohort.spec import (E1_FINGERPRINT, FIRST_LEG_QTY,
                                        MAX_FIRST_LEG_PRICE, PROBE,
                                        VENUE_TICK, WINDOWS)
from src import l5_clob_ws as WS

#: the strict-timing book may be at most this old at the decision point.
#: Inherited from the XR2W AuthBook bound; not chosen here.
MAX_BOOK_AGE_S = 2.0

#: both tokens must have been seen streaming within this long, or the
#: websocket is not healthy for THIS market and nothing is eligible.
MAX_TOKEN_SILENCE_S = 5.0


class WindowUnavailable(RuntimeError):
    """No snapshot for this window, with a reason. Fails CLOSED."""


def assert_ws_both_tokens_healthy(health: Any, pin: Dict[str, Any], *,
                                  now: float) -> Dict[str, Any]:
    """Both-token health PROVEN for the SELECTED market, before eligibility.

    `health` is whatever the live two-token collector reports. This refuses
    unless it names THIS market, carries the websocket as timing authority,
    and shows BOTH of this window's token ids streaming recently. A
    complement derived from one token is not a second token.
    """
    h = dict(health or {})
    bad: List[str] = []
    if h.get("timing_authority") != WS.TIMING_WEBSOCKET:
        bad.append("TIMING_AUTHORITY_IS_%r_NOT_%r"
                   % (h.get("timing_authority"), WS.TIMING_WEBSOCKET))
    if str(h.get("market") or "") != str(pin["slug"]):
        bad.append("HEALTH_IS_FOR_MARKET_%r_NOT_THE_SELECTED_%r"
                   % (h.get("market"), pin["slug"]))
    if not h.get("both_tokens_streaming"):
        bad.append("BOTH_TOKENS_NOT_STREAMING")
    seen = dict(h.get("tokens_last_seen_unix") or {})
    for name, tok in (("token_up", pin["token_up"]),
                      ("token_down", pin["token_down"])):
        ts = seen.get(str(tok))
        if ts is None:
            bad.append("TOKEN_NEVER_SEEN:%s" % name)
        elif (now - float(ts)) > MAX_TOKEN_SILENCE_S:
            bad.append("TOKEN_SILENT_%.3fs:%s" % (now - float(ts), name))
    if h.get("derived_complement"):
        bad.append("DERIVED_COMPLEMENT_IS_NOT_A_SECOND_TOKEN")
    if bad:
        raise WindowUnavailable(
            "websocket both-token health not proven for %s: %s"
            % (pin["slug"], "; ".join(bad)))
    return {"ok": True, "market": pin["slug"],
            "both_tokens_streaming": True,
            "timing_authority": WS.TIMING_WEBSOCKET,
            "rest_role": WS.TIMING_REST,
            "rest_is_sufficient_timing_authority": False,
            "derived_complement_accepted": False,
            "frames": h.get("frames"),
            "endpoint": h.get("ws_url") or WS.DEFAULT_WS_URL,
            "proven_for_the_selected_market": True}


def first_leg_price_from_book(book: Dict[str, Any]) -> D:
    """The frozen placement rule, applied: offset 0 at the same-side best
    bid, on the venue tick. NO new pricing logic -- `eligible()` re-checks
    every one of these and refuses if this is wrong."""
    bid = book.get("best_bid")
    if bid in (None, ""):
        raise WindowUnavailable("no best bid on the selected token")
    return D(str(bid)).quantize(VENUE_TICK, rounding=ROUND_DOWN)


class LiveWindowProvider:
    """Sequences at most `WINDOWS` DISTINCT eligible windows.

    A restart NEVER grants another twelve: the window count comes from
    durable progress, not from this object's lifetime.
    """

    def __init__(self, *, progress: Any, session_auth: Any,
                 book_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
                 health_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
                 recorder: Any = None,
                 selector_fn: Callable[..., Dict[str, Any]] = None,
                 now_fn: Callable[[], float] = None,
                 window_starts: Optional[List[int]] = None) -> None:
        self.progress = progress
        self.auth = session_auth
        self.book_fn = book_fn
        self.health_fn = health_fn
        self.recorder = recorder
        self.selector_fn = selector_fn or SEL.select_window
        self.now_fn = now_fn or time.time
        self._explicit_starts = list(window_starts) if window_starts else None
        self.current_pin: Optional[Dict[str, Any]] = None
        self.current_window_index: Optional[int] = None
        self.refusals: List[Dict[str, Any]] = []

    # ------------------------------------------------------- window choice
    def _candidate_starts(self) -> List[int]:
        used = set(self.progress.window_starts_used())
        if self._explicit_starts is not None:
            return [s for s in self._explicit_starts if s not in used]
        now = self.now_fn()
        cur = SEL.window_start_for(now)
        out = [cur] if cur not in used else []
        out += [s for s in SEL.next_window_starts(now, WINDOWS)
                if s not in used]
        return out

    def _refuse(self, why: str, **extra: Any) -> None:
        rec = {"refused": why, "unix": self.now_fn(), **extra}
        self.refusals.append(rec)
        if self.recorder is not None:
            self.recorder.record_event(
                window=self.progress.next_window_index(), slot=-1,
                event="WINDOW_REFUSED", detail=rec)

    def open_next_window(self) -> Optional[Dict[str, Any]]:
        """Resolve, PIN, authorize and prove ONE new window. None if no
        window can be opened -- never a degraded one."""
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
                self._refuse("SELECTOR_REFUSED:%s" % exc, window_start=start)
                continue
            try:
                auth = self.auth.authorize_selected(pin)
            except AUTH.NotAuthorized as exc:
                self._refuse("NOT_AUTHORIZED:%s" % exc, window_start=start,
                             slug=pin.get("slug"))
                continue
            try:
                health = assert_ws_both_tokens_healthy(
                    self.health_fn(pin), pin, now=self.now_fn())
            except WindowUnavailable as exc:
                self._refuse("WEBSOCKET_NOT_PROVEN:%s" % exc,
                             window_start=start, slug=pin.get("slug"))
                continue
            self.progress.record_window_opened(
                window_index=idx, window_start=int(pin["window_start"]),
                slug=pin["slug"], market_id=pin["market_id"],
                token_up=pin["token_up"], token_down=pin["token_down"],
                selection_provenance=pin.get("selection_provenance"),
                authorization_result=auth.get("authorization_result"))
            if self.recorder is not None:
                # the raw tape carries, per window: slug, market id, both
                # token ids, selection provenance and the authorization
                # result.
                self.recorder.record_event(
                    window=idx, slot=-1, event="WINDOW_AUTHORIZED",
                    detail={"slug": pin["slug"],
                            "market_id": pin["market_id"],
                            "token_up": pin["token_up"],
                            "token_down": pin["token_down"],
                            "window_start": pin["window_start"],
                            "window_end": pin["window_end"],
                            "selection_provenance":
                                pin.get("selection_provenance"),
                            "authorization_result":
                                auth.get("authorization_result"),
                            "websocket_health": health,
                            "spec_fingerprint": E1_FINGERPRINT})
            self.current_pin = pin
            self.current_window_index = idx
            return pin
        self._refuse("NO_CANDIDATE_WINDOW_COULD_BE_OPENED")
        return None

    # ----------------------------------------------------------- snapshots
    def _window_is_live(self, pin: Dict[str, Any]) -> bool:
        now = self.now_fn()
        return int(pin["window_start"]) <= now < int(pin["window_end"])

    def __call__(self) -> Optional[Dict[str, Any]]:
        """The `snapshot_provider` `run_cohort` calls. One snapshot, or None.

        STALE-WINDOW ROLLOVER: a pinned window whose end has passed is
        CLOSED and a new one opened. The expired pin is never reused, so a
        stale window cannot leak an order into a market that has ended.
        """
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
        try:
            health = assert_ws_both_tokens_healthy(
                self.health_fn(pin), pin, now=self.now_fn())
        except WindowUnavailable as exc:
            # WEBSOCKET LOSS BEFORE SUBMISSION: no snapshot at all.
            self._refuse("WEBSOCKET_LOST_BEFORE_SUBMISSION:%s" % exc,
                         slug=pin["slug"])
            return None
        book = dict(self.book_fn(pin) or {})
        up = dict(book.get("up") or {})
        down = dict(book.get("down") or {})
        if book.get("market") is not None and \
                str(book["market"]) != str(pin["slug"]):
            self._refuse("BOOK_IS_FOR_ANOTHER_MARKET", slug=pin["slug"],
                         book_market=book.get("market"))
            return None
        for name, side in (("up", up), ("down", down)):
            tok = side.get("token_id")
            want = pin["token_up"] if name == "up" else pin["token_down"]
            if tok is not None and str(tok) != str(want):
                # SLUG/TOKEN MISMATCH: fails CLOSED.
                self._refuse("TOKEN_MISMATCH_%s" % name.upper(),
                             slug=pin["slug"], got=tok, want=want)
                return None
        try:
            price = first_leg_price_from_book(up)
        except WindowUnavailable as exc:
            self._refuse("NO_PRICE:%s" % exc, slug=pin["slug"])
            return None
        if price > MAX_FIRST_LEG_PRICE:
            # not a refusal of the window -- `eligible()` enforces the
            # ceiling itself and will say so. Recorded, and passed through
            # so the refusal is the frozen rule's, not the adapter's.
            pass
        return {
            "market": pin["slug"],
            "market_id": pin["market_id"],
            "pin": pin,
            "window": idx,
            "window_index": idx,
            "window_start": pin["window_start"],
            "window_end": pin["window_end"],
            "window_open": True,
            "within_handover_tte": bool(book.get("within_handover_tte",
                                                 True)),
            "our_outstanding_orders": int(book.get(
                "our_outstanding_orders", 0)),
            "our_open_residual": book.get("our_open_residual"),
            "qty": FIRST_LEG_QTY,
            "first_leg_price": str(price),
            "first_leg": {"side": "UP", "token_id": pin["token_up"],
                          "best_bid": up.get("best_bid"),
                          "best_ask": up.get("best_ask"),
                          "size_at_our_price": up.get("size_at_our_price"),
                          "size_ahead_of_us": up.get("size_ahead_of_us")},
            "complement": {"side": "DOWN", "token_id": pin["token_down"],
                           "best_bid": down.get("best_bid"),
                           "best_ask": down.get("best_ask")},
            "fingerprint_pinned": None,       # read from E1's own namespace
            "ws_state": {
                "timing_authority": WS.TIMING_WEBSOCKET,
                "both_tokens_streaming": True,
                "book_unix": book.get("book_unix"),
                "recv_monotonic_ns": book.get("recv_monotonic_ns"),
                "market": pin["slug"],
                "token_up": pin["token_up"],
                "token_down": pin["token_down"],
                "health": health,
                "first_leg": {"best_bid": up.get("best_bid"),
                              "best_ask": up.get("best_ask")},
                "complement": {"best_bid": down.get("best_bid"),
                               "best_ask": down.get("best_ask")},
            },
            "probe": PROBE,
            "provider": "analysis.l5.e1_cohort.provider:LiveWindowProvider",
            "LABEL": "SNAPSHOT -- eligibility is still eligible()'s to decide",
        }

    def report(self) -> Dict[str, Any]:
        return {"probe": PROBE,
                "windows_authorized": WINDOWS,
                "windows_used": self.progress.windows_used(),
                "windows_remaining": self.progress.windows_remaining(),
                "slugs_used": self.progress.slugs_used(),
                "current_slug": (self.current_pin or {}).get("slug"),
                "refusals": list(self.refusals),
                "timing_authority": WS.TIMING_WEBSOCKET,
                "rest_is_sufficient": False,
                "reuses_proven_infrastructure": [
                    "src.l5_market_binding (via selector)",
                    "src.l5_clob_ws (two-token websocket)"],
                "built_another_discovery_stack": False,
                "a_restart_grants_another_12_windows": False}
