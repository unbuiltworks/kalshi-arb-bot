"""Continuous NO-POST shadow of the corrected R7 engine on live windows.

WHAT THIS IS FOR. R7 cannot arm while the deposit is held, and the out-of-
sample economics say not to advance the clip. What is still worth measuring
is whether the shipped execution fixes -- the R5 defect-2 resting-ownership
fix and the unconditional sweep -- restore the PERSISTENCE AND FREQUENCY
signature that L5's high-completion windows show. That needs live windows,
not a replay, and it needs no money.

NO-POST BY CONSTRUCTION, not by flag:
  * this module never constructs a transport, a money path, a mandate or an
    authorization -- there is no object here with a submit method;
  * the engine is driven directly, exactly as pmus_native_parity walks it,
    and fills are MODELLED by the frozen queue-aware replay venue;
  * an AST test asserts no order-path name appears in this file.

Consequently every fill and every P&L number it produces is SHADOW. None of
it is submitted, filled, settled or realized, and the labels say so on every
record.

WHAT IT RECORDS, per window, in the shape `l5_persistence_signature` defines
so it compares directly against the 501-window L5 cohorts:
  actions/minute, median dt_prev, no-intent tick fraction, matched and
  residual through time, last-action seconds-to-expiry, EXTEND/REBALANCE
  transition frequencies, same-side streaks -- plus the exact full-depth book
  rows the decisions used, so any window can be replayed exactly afterwards.

Frozen parameters only. Nothing here chooses a window selectively.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src import l5_persistence_signature as SIG
from src import l5_session_journal as JN
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P
from src import l5_window_actions as WA

POLL_S = 2.0
#: how much of a window must remain to be worth shadowing at all
MIN_REMAINING_S = 240.0
SHADOW = "SHADOW -- modelled fill, never submitted, never realized"


def clip_from_env() -> D:
    v = (os.getenv("L5_NATIVE_CLIP", "") or "").strip()
    return D(v) if v else D("10")


def shadow_window(market: str, deadline: float, *, feed, journal,
                  clock=time.time, sleep=time.sleep,
                  clip: Optional[D] = None) -> Dict[str, Any]:
    """Drive the corrected engine over one live window. Nothing is sent."""
    eng = E.NativeEngine(clip=(clip or clip_from_env()), prospective_cap=False)
    venue = P.ReplayVenue(fill_model="queue_aware")
    ladder = None
    seen_t = None

    acts: List[Dict[str, Any]] = []
    no_intent = 0
    ticks = 0
    w_start = WA.window_start(market)
    close = (None if w_start is None else w_start + WA.WINDOW_S)

    while clock() < deadline:
        book = feed.latest()
        if book is None or book.get("t") == seen_t:
            sleep(POLL_S)
            continue
        seen_t = book.get("t")
        ticks += 1
        lad = getattr(feed, "ladder", None)
        if lad:
            ladder = {"kind": "book", "t": seen_t,
                      "bids": lad.get("bids") or [],
                      "offers": lad.get("offers") or []}
            eng.ladder = ladder
            # THE EXACT DEPTH THE DECISION USED, so the window is replayable.
            try:
                journal.write("shadow_book_depth", {
                    "t": seen_t,
                    "bids": [[str(a), str(b)] for a, b in ladder["bids"]],
                    "offers": [[str(a), str(b)] for a, b in ladder["offers"]],
                    "labels": "venue-observed, never synthesized; " + SHADOW})
            except BaseException:                              # noqa: BLE001
                pass

        up_b, dn_b = float(eng.bk.q["up"]), float(eng.bk.q["down"])
        s_to_exp = (None if close is None else close - clock())

        def _execute_taker(it):
            # MODELLED. Nothing is sent; the frozen replay convention is that
            # a marketable completion fills at the walked ladder cost.
            return it["qty"], it["cost"], it["fee"]

        try:
            intents = eng.on_book(book, ladder, execute_taker=_execute_taker)
        except E.StaleBook:
            no_intent += 1
            continue

        emitted = []
        for it in (intents or []):
            side = str(it.get("side") or "").lower()
            if side not in ("up", "down"):
                continue
            _px = it.get("px")
            emitted.append({"t": clock(), "side": side, "up": up_b,
                            "down": dn_b,
                            # NOT str(None): the signature reads this back and
                            # the literal "None" is not a price.
                            "px": (None if _px is None else str(_px)),
                            "s_to_expiry": s_to_exp})
        if emitted:
            acts.extend(emitted)
        else:
            no_intent += 1

        if eng.locked_at is None:
            for side in ("up", "down"):
                st = eng.resting.get(side)
                if not st:
                    continue
                got = venue.fills_for(eng, book, side, D(str(st["px"])),
                                      D(str(st["qty"])), ladder)
                if got is None:
                    continue
                qty, fpx = got
                if not eng._cap_permits(side, qty):
                    continue
                eng.on_fill(side, qty, qty * fpx, maker=True)

        journal.write("shadow_tick", {
            "t": seen_t, "ticks": ticks,
            "intents": emitted,
            "book": {k: (None if book.get(k) is None else str(book.get(k)))
                     for k in ("bid", "ask", "state")},
            "up": str(eng.bk.q["up"]), "down": str(eng.bk.q["down"]),
            "s_to_expiry": s_to_exp, "labels": SHADOW})
        if eng.locked_at is not None:
            break

    sig = SIG.signature(acts, no_intent_ticks=no_intent,
                        window_s=float(WA.WINDOW_S), label="SHADOW")
    out = {"market": market, "clip": str(eng.bk and (clip or clip_from_env())),
           "ticks": ticks, "signature": sig,
           "shadow_inventory": {"up": str(eng.bk.q["up"]),
                                "down": str(eng.bk.q["down"])},
           "labels": SHADOW + "; economics NOT reported here on purpose"}
    journal.write("shadow_window_done", out)
    print("SHADOW_WINDOW " + json.dumps(out, default=str), flush=True)
    return out


# ---------------------------------------------------------------- continuous

#: journal for the continuous runner. Its own path: this process must never
#: write where a money-path journal lives.
JOURNAL_ENV = "L5_SHADOW_JOURNAL"
DEFAULT_JOURNAL = "/state/l5-shadow/shadow_journal.jsonl"


def run_forever(*, clock=time.time, sleep=time.sleep,
                feed_factory=None, journal=None, max_windows=None) -> None:
    """Shadow fresh BTC-15m windows, one after another, indefinitely.

    THE SLUG IS DERIVED ARITHMETICALLY, not from venue discovery, and that is
    a deliberate trade. The armed one-shot asks the venue which window exists,
    because a stale pin there costs a spent authorization. Here there is no
    authorization and no money, so the slug comes from `l5_window_actions`
    and the image can exclude the POST-capable transport ENTIRELY -- which is
    a stronger no-POST guarantee than any flag. The cost is that a window the
    venue has not published yields no frames; that is recorded as a window
    with zero ticks rather than papered over.

    Read-only, self-recovering: a failure in one window is journalled and the
    next window is attempted, because a shadow collector should behave like
    the 24/7 recorders rather than like a bounded money session.
    """
    from src import l5_live_book_feed as FEED
    from src import l5_session_journal as JN

    journal = journal or JN.Journal(
        os.getenv(JOURNAL_ENV, DEFAULT_JOURNAL))
    journal.write("shadow_runner_started", {
        "clip": str(clip_from_env()),
        "post_capable": False,
        "labels": ("NO-POST: this image contains no transport, money path, "
                   "mandate or authorization. " + SHADOW)})

    done = 0
    last_slug = None
    while max_windows is None or done < max_windows:
        slug = WA.current_slug(clock())
        start = WA.window_start(slug)
        if slug == last_slug or start is None:
            sleep(POLL_S)
            continue
        remaining = (start + WA.WINDOW_S) - clock()
        if remaining < MIN_REMAINING_S:
            # too little of this window left to say anything about cadence
            sleep(POLL_S)
            continue
        last_slug = slug
        deadline = start + WA.WINDOW_S
        feed = None
        try:
            feed = ((feed_factory(slug, deadline) if feed_factory
                     else FEED.AuthenticatedBookFeed(slug, clock=clock,
                                                     deadline=deadline)))
            shadow_window(slug, deadline, feed=feed, journal=journal,
                          clock=clock, sleep=sleep)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            journal.write("shadow_window_failed", {
                "market": slug, "error": type(exc).__name__,
                "detail": str(exc)[:300],
                "labels": "the next window is still attempted; " + SHADOW})
            print("SHADOW_WINDOW_FAILED " + json.dumps(
                {"market": slug, "error": type(exc).__name__}), flush=True)
        finally:
            for closer in ("stop", "close"):
                fn = getattr(feed, closer, None)
                if callable(fn):
                    try:
                        fn()
                    except BaseException:                      # noqa: BLE001
                        pass
            done += 1


if __name__ == "__main__":
    run_forever()
