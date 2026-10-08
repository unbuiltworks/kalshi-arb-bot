"""E1's decision feed: the PMUS authenticated book, for the slug being ordered.

WHAT THIS REPLACES, AND WHY. E1 used to decide on
`ws-subscriptions-clob.polymarket.com` -- Polymarket.com's CLOB, keyed by
CTF token ids -- and then order on `api.polymarket.us`. The venue said
"Market not found", and it was right: those are two venues. The book that
causes a decision must belong to the market being ordered, so the feed is
now PMUS's own.

THE IMPLEMENTATION IS NOT REWRITTEN HERE. `src.l5_pmus_auth_book.AuthBook`
is imported and used unmodified. It already carries the stall and
resubscribe discipline that a real incident taught it: on 2026-09-24 1215z,
after the venue's post-maintenance restarts, our socket stayed OPEN while
delivering 26 book updates in a window where the independent recorder saw
1,747, and 115 L5 actions were refused STALE. Hence `STALL_S = 10.0` on a
connected-but-silent socket and `RESUB_SLOW_S = 30.0` for an open window
with no book. Reimplementing that would throw the lesson away.

ONE BOOK, TWO SIDES, BY THE VENUE'S OWN CONVENTION. PMUS quotes a market
once, as the YES side, and the NO side is its complement:

    NO bid = 1 - YES ask        NO ask = 1 - YES bid

That is not an invention of ours. `pmus_q1_executor.would_cross` -- the
guard in the executor that has actually filled orders on this venue --
states it: "BUY NO at p_no crosses when p_no >= NO ask = 1 - YES bid".

So E1's two-token model becomes one PMUS market with two outcome sides,
and `both_tokens_streaming` becomes a single question: is THIS slug's book
connected, two-sided and fresh? `AuthBook.snapshot` returns None unless
the socket is connected and both sides are present, so the failure is
already closed; this module adds the freshness bound and the identity
assertion on top.

REST IS NEVER THE TIMING AUTHORITY. `pmus_identity.bbo`/`book` exist for
discovery and cross-checks. Nothing in this module falls back to them, and
`health()` reports `timing_authority` as the authenticated websocket or
refuses -- there is no third value.
"""
from __future__ import annotations

import threading
import time
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort.spec import MAX_FIRST_LEG_PRICE

#: the frozen name of the only acceptable timing authority.
TIMING_PMUS_AUTHENTICATED_WS = "pmus_authenticated_ws_strict"

#: a book older than this is not a decision input. Deliberately the same
#: 5.0s E1 already used for the CLOB feed, so the freshness bar does not
#: move with the venue.
#: THE PROVEN BOUND, taken from the only order path that has produced an
#: evidenced fill. XR2W Attempt 6 (analysis/l5/live_exec5/XR2W_ATTEMPT_6.md)
#: read AuthBook two-sided at age 0.084 s against a 2.0 s BOUND, priced
#: DOWN at the own-side touch 0.6700 = 1 - 0.3300, sent wire
#: price.value 0.3300, and the venue took it: ORDER_STATE_NEW 02:00:01Z ->
#: ORDER_STATE_FILLED 02:00:15.69Z, cumQuantity 0.01.
#:
#: E1 carried 5.0 s, which is LOOSER than the bound that has actually
#: filled. There is no evidence for 5.0 and there is evidence for 2.0, so
#: E1 now uses 2.0.
MAX_BOOK_AGE_S = 2.0

ONE = D("1")


class PmusDecisionFeed:
    """One AuthBook, wrapped so a decision cannot read another market.

    The wrapper owns exactly two responsibilities: start/stop the proven
    book, and refuse to answer about any slug other than the one pinned.
    Every price it returns is PMUS's own, for the PMUS slug that will be
    serialized into the order.
    """

    def __init__(self, *, clock: Any = time.time, book: Any = None) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        self._book = book            # injected in tests; built on start()
        self._started = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "PmusDecisionFeed":
        with self._lock:
            if self._book is None:
                from src import l5_pmus_auth_book as AB
                self._book = AB.AuthBook(clock=self.clock)
            if not self._started:
                starter = getattr(self._book, "start", None)
                if callable(starter):
                    starter()
                self._started = True
        return self

    def stop(self) -> None:
        with self._lock:
            stopper = getattr(self._book, "stop", None)
            if self._started and callable(stopper):
                stopper()
            self._started = False

    # -------------------------------------------------------------- reading
    def _snapshot_for(self, slug: str) -> Optional[Dict[str, Any]]:
        PI.assert_pmus_slug(slug)
        with self._lock:
            if self._book is None:
                return None
            snap = self._book.snapshot(slug)
        return dict(snap) if snap else None

    def health(self, pin: Mapping[str, Any]) -> Dict[str, Any]:
        """Is THIS pinned market's book usable as a decision input?

        Never raises. `ok` False with a named reason is the answer; a
        caller that wants an exception uses `assert_fresh_for`.
        """
        slug = str((pin or {}).get("slug") or "")
        out: Dict[str, Any] = {
            "timing_authority": TIMING_PMUS_AUTHENTICATED_WS,
            "rest_is_sufficient_timing_authority": False,
            "rest_role": "discovery_and_cross_check_only",
            "market": slug,
            "venue": "polymarket_us",
            "identity": "slug_plus_outcome_side",
            "ctf_token_ids_used": False,
            "max_book_age_s": MAX_BOOK_AGE_S,
            "started": self._started,
        }
        if not PI.is_pmus_btc15m_slug(slug):
            out.update({"ok": False, "why": "PIN_IS_NOT_A_PMUS_SLUG"})
            return out
        if not self._started:
            out.update({"ok": False, "why": "FEED_NOT_STARTED"})
            return out
        snap = None
        try:
            snap = self._snapshot_for(slug)
        except Exception as exc:                               # noqa: BLE001
            out.update({"ok": False,
                        "why": "FEED_ERROR:%s" % type(exc).__name__})
            return out
        if snap is None:
            # AuthBook returns None unless connected AND two-sided.
            out.update({"ok": False,
                        "why": "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG"})
            return out
        age = snap.get("stream_age_s")
        out.update({"source": snap.get("source"),
                    "market_state": snap.get("market_state"),
                    "book_updates": snap.get("book_updates"),
                    "stream_age_s": age,
                    "last_update_t": snap.get("last_update_t")})
        if snap.get("source") != "authenticated_ws":
            out.update({"ok": False,
                        "why": "SOURCE_IS_NOT_THE_AUTHENTICATED_WS:%r"
                               % (snap.get("source"),)})
            return out
        if age is None:
            out.update({"ok": False, "why": "STREAM_AGE_UNKNOWN"})
            return out
        try:
            fresh = float(age) <= MAX_BOOK_AGE_S
        except (TypeError, ValueError):
            out.update({"ok": False, "why": "STREAM_AGE_NOT_NUMERIC"})
            return out
        if not fresh:
            out.update({"ok": False,
                        "why": "BOOK_STALE_%.3fs" % float(age)})
            return out
        out.update({"ok": True, "fresh": True,
                    "proven_for_the_selected_market": True})
        return out

    def book(self, pin: Mapping[str, Any]) -> Dict[str, Any]:
        """The decision book for the pinned slug, in E1's snapshot shape.

        The returned record NAMES the slug it came from, so a caller can
        assert -- and `pmus_provider` does -- that the book that caused a
        decision is the market that gets serialized.
        """
        slug = str((pin or {}).get("slug") or "")
        PI.assert_pmus_slug(slug)
        snap = self._snapshot_for(slug)
        if snap is None:
            return {"market": slug, "ok": False,
                    "why": "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG",
                    "first_leg": {}, "complement": {}}
        yes_bid, yes_ask = snap.get("bid"), snap.get("ask")
        comp = complement_of(yes_bid, yes_ask)
        sides = PI.outcome_sides({})
        yes_leg = {"side": "UP", "outcome_side": sides["up_is"],
                   "slug": slug,
                   "best_bid": _s(yes_bid), "best_ask": _s(yes_ask),
                   "size_at_our_price": _s(snap.get("bid_shares")),
                   "size_at_best_ask": _s(snap.get("ask_shares")),
                   "quoted_directly": True}
        no_leg = {"side": "DOWN", "outcome_side": sides["down_is"],
                  "slug": slug,
                  "best_bid": _s(comp.get("bid")),
                  "best_ask": _s(comp.get("ask")),
                  "derived_from_the_same_quoted_book": True}
        # WHICH SIDE OPENS. See `choose_first_leg`: side-agnostic for a
        # matched pair, so this is about which price fits the ceiling.
        choice = choose_first_leg(yes_bid, yes_ask,
                                  max_price=MAX_FIRST_LEG_PRICE)
        first, comp_leg = ((yes_leg, no_leg) if choice["side"] == "UP"
                           else (no_leg, yes_leg))
        return {
            "market": slug,
            "ok": True,
            "venue": "polymarket_us",
            "source": snap.get("source"),
            "stream_age_s": snap.get("stream_age_s"),
            "book_unix": self.clock() - float(snap.get("stream_age_s") or 0),
            "market_state": snap.get("market_state"),
            # EITHER SIDE MAY OPEN. UP is the venue's YES side, quoted
            # directly; DOWN is its NO side, the arithmetic complement of
            # the one quoted book per the venue's own convention. Both are
            # always published under their own names as well, so a reader
            # can see the whole book regardless of which one opened.
            "first_leg": first,
            "complement": comp_leg,
            "yes": yes_leg,
            "no": no_leg,
            "first_leg_side_choice": choice,
            "COMPLEMENT_IS_ARITHMETIC_NOT_A_SECOND_FEED": True,
        }


def choose_first_leg(yes_bid: Any, yes_ask: Any, *,
                     max_price: Any) -> Dict[str, Any]:
    """WHICH SIDE OPENS. Deterministic from the book, and NEVER a forecast.

    E1 used to open on YES unconditionally, and E9 requires the opening
    price to be at or below the frozen $0.60 ceiling. So whenever the market
    leaned more than 60% to UP, E1 was locked out of the whole window -- as
    it was on 2026-10-08, when the YES bid was 0.62 and NO was sitting at
    about 0.36: cheaper, fully tradeable, and giving the SAME matched pair.
    We refused a trade we could have made, on the wrong side of a symmetric
    bet. The lockout was also CORRELATED: while BTC trends, consecutive
    windows all lean the same way.

    THIS IS NOT A DIRECTIONAL VIEW AND CARRIES NONE. A matched pair is
    side-agnostic -- YES 0.59 then NO 0.37 and NO 0.37 then YES 0.59 are the
    same $0.96 position -- so choosing which leg to open first is a question
    about which price fits the ceiling, not about where BTC is going. No
    predictive skill is claimed or used.

    THE RULE:
      * a side is a candidate only if its OWN-SIDE best bid is at or below
        `max_price`;
      * of the candidates, the CHEAPER one opens, because a cheaper first
        leg leaves more headroom under the fee-inclusive pair ceiling;
      * ties go to UP, so the choice is deterministic and replayable;
      * if neither qualifies, UP is reported with
        `eligible_side_exists: False` and E9 refuses it downstream in the
        usual way. Nothing here decides eligibility.

    Note the arithmetic: NO bid = 1 - YES ask, so
    `yes_bid + no_bid = 1 - spread`. Both sides can therefore only exceed
    0.60 if the book is CROSSED by more than 0.20, which is not a market.
    In any sane book at least one side qualifies.
    """
    comp = complement_of(yes_bid, yes_ask)
    ceiling = _fin(max_price)
    yb = _fin(yes_bid)
    nb = _fin(comp.get("bid"))
    cands = []
    if ceiling is not None:
        if yb is not None and yb <= ceiling:
            cands.append(("UP", yb))
        if nb is not None and nb <= ceiling:
            cands.append(("DOWN", nb))
    # cheaper first; UP wins a tie (False sorts before True)
    cands.sort(key=lambda c: (c[1], c[0] != "UP"))
    out = {"rule": "cheaper own-side bid at or below the ceiling opens",
           "is_a_forecast": False,
           "carries_no_directional_view": True,
           "ceiling_usd": _s(ceiling),
           "up_own_side_bid": _s(yb),
           "down_own_side_bid": _s(nb),
           "candidates": [c[0] for c in cands],
           "both_sides_qualified": len(cands) == 2}
    if not cands:
        out.update({"side": "UP", "eligible_side_exists": False,
                    "why": ("neither own-side bid is at or below the "
                            "ceiling; E9 refuses this downstream")})
        return out
    out.update({"side": cands[0][0], "eligible_side_exists": True,
                "own_side_bid": _s(cands[0][1])})
    return out


def _s(x: Any) -> Optional[str]:
    return None if x is None else str(x)


def complement_of(yes_bid: Any, yes_ask: Any) -> Dict[str, Any]:
    """NO bid = 1 - YES ask; NO ask = 1 - YES bid.

    The venue's convention, as `pmus_q1_executor.would_cross` states it.
    A value that is absent or non-finite yields None rather than a guess,
    so E8 refuses it downstream instead of pricing against nonsense.
    """
    out: Dict[str, Any] = {"bid": None, "ask": None,
                           "rule": "NO = 1 - YES, opposite side"}
    b = _fin(yes_bid)
    a = _fin(yes_ask)
    if a is not None:
        out["bid"] = ONE - a
    if b is not None:
        out["ask"] = ONE - b
    return out


def _fin(x: Any) -> Optional[D]:
    if x is None or (isinstance(x, str) and not x.strip()):
        return None
    try:
        d = D(str(x).strip())
    except Exception:                                          # noqa: BLE001
        return None
    return d if d.is_finite() else None
