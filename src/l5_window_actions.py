"""The BTC15m window, and L5's actions in it — one implementation.

The continuous recorder has run this code across hundreds of windows and
tens of thousands of L5 actions. The bounded one-shot probe had its own
copy of it, and the copy is what broke Probe 02: it handed the raw venue
activity row to the frozen rule, which reads `row["l5"]` and returns None
for that shape, so every action was unreadable. Two implementations of the
same thing is how that happens, so there is now one.

Everything here already existed in `scripts/l5_pmus_realtime_probe.py` and
is moved rather than rewritten. Both runners import it.

What is deliberately NOT here: the decision (frozen 030d8bf), the
translation, the safety overlay, the order body, the preview/echo gate, the
signing and the reconciliation. Those have their own proven homes and this
module does not touch them.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src import l5_authenticated_observation as OBS

#: the series' window length, from the observation module that defines it
WINDOW_S = OBS.WINDOW_S

SLUG_PREFIX = "cpc-btc-updown-15m-"


def current_slug(now: Optional[float] = None) -> str:
    """The slug of the BTC15m window open at `now`."""
    now = time.time() if now is None else now
    t = dt.datetime.fromtimestamp(int(now // WINDOW_S) * WINDOW_S,
                                  dt.timezone.utc)
    return "%s%s-%02d%02dz" % (SLUG_PREFIX, t.strftime("%Y-%m-%d"),
                               t.hour, t.minute)


def window_start(slug: str) -> Optional[float]:
    """Epoch seconds of the window this slug names, or None.

    None rather than an exception: a caller that cannot establish the window
    must fail closed on its own terms, and the one-shot's private copy of
    this raised instead -- outside its own try block, where it would have
    killed the process after the authorization had already been spent.
    """
    return OBS.window_start_epoch(slug)


def book_row(*, t: float, bid: Any, ask: Any,
             bid_qty: Any, ask_qty: Any) -> Dict[str, Any]:
    """One book state in the shape the decision path reads.

    `l5_pmus_live_wiring.book_at` selects on `status == OBS.USABLE` and maps
    bid_qty/ask_qty to bid_shares/ask_shares, so the status vocabulary is
    part of that contract rather than a local convention.
    """
    return {"t": t, "bid": bid, "ask": ask,
            "bid_qty": bid_qty, "ask_qty": ask_qty,
            "status": (OBS.USABLE if bid is not None and ask is not None
                       else "NO_BOOK" if bid is None and ask is None
                       else "ONE_SIDED")}


class FeedHealth:
    """What the L5 activity feed actually did, countable.

    Probe 02 reported `l5_actions: 0` and there was no way to tell a broken
    feed from a quiet market, or either from rows that arrived and were
    filtered out. These counters make those three different answers.
    """

    def __init__(self) -> None:
        self.polls = 0
        self.polls_ok = 0
        self.polls_failed = 0
        self.raw_rows = 0
        self.in_window = 0
        self.new_actions = 0
        self.last_ok_t: Optional[float] = None
        self.last_error: Optional[str] = None
        # rows that were in the window by TIME and still did not belong to
        # this market. Probe 05 had six and traded two of them.
        self.identity_refused = 0
        self.identity_reasons: Dict[str, int] = {}
        self.admitted_tokens: Dict[str, int] = {}
        self.rejected_tokens: Dict[str, int] = {}
        self.binding: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"polls": self.polls, "polls_ok": self.polls_ok,
                "polls_failed": self.polls_failed,
                "raw_rows": self.raw_rows,
                "rows_in_window": self.in_window,
                "new_actions": self.new_actions,
                "last_ok_utc": (None if self.last_ok_t is None else
                                dt.datetime.fromtimestamp(
                                    self.last_ok_t,
                                    dt.timezone.utc).isoformat()),
                "last_error": self.last_error,
                "identity_refused": self.identity_refused,
                "identity_reasons": dict(self.identity_reasons),
                "admitted_tokens": dict(self.admitted_tokens),
                "rejected_tokens": dict(self.rejected_tokens),
                "distinct_admitted": sum(self.admitted_tokens.values()),
                "distinct_identity_refused": self.identity_refused,
                "market_binding": self.binding,
                "foreign_tokens_admitted": 0,
                "healthy": self.polls_ok > 0 and self.polls_failed == 0}


from src import l5_market_binding as MB

_BINDINGS: Dict[int, Any] = {}

#: action keys already refused on identity, per market slug. `seen` holds the
#: ones we HANDLED; a refused action is in neither, so without this it is
#: re-judged on every poll and every counter becomes a poll count.
_REFUSED: Dict[str, Set[str]] = {}


def _binding_for(window_start_epoch: int, *, getter=None):
    """The venue's own record for this window, fetched once and kept.

    A miss is NOT cached: a transient failure must not disable identity for
    the rest of the session, and an uncached miss simply admits nothing
    until the venue answers.
    """
    b = _BINDINGS.get(window_start_epoch)
    if b is None:
        b = MB.fetch_binding(window_start_epoch, getter=getter)
        if b is not None:
            _BINDINGS[window_start_epoch] = b
    return b


def new_l5_actions(slug: str, seen: Set[str],
                   activity=None,
                   health: Optional["FeedHealth"] = None
                   ) -> Tuple[List[dict], bool]:
    """L5's trades in THIS window that we have not handled yet.

    Returns (actions, feed_ok). `l5_activity` returns None when the FEED
    FAILED, and an empty list when L5 simply has not traded. Collapsing
    those with `or []` makes a broken feed report as a quiet market -- a
    window saying l5_actions 0 would then be evidence of nothing at all
    while looking like evidence of inactivity. They are kept apart.

    The row SHAPE is the frozen rule's input contract: `l5_to_pmus_action`
    under an "l5" key, keyed by the whole economic identity of the fill.

    `activity` is injectable so a test can drive this without the network.
    Production passes nothing and gets the observer's own feed.
    """
    from src import l5_pmus_realtime_observer as O
    if activity is None:
        activity = O.l5_activity
    ws = window_start(slug)
    # one authoritative lookup per window, cached. No binding -> nothing is
    # admitted: a window we cannot identify is a window we do not trade.
    binding = None if ws is None else _binding_for(int(ws))
    if health is not None:
        health.polls += 1
    rows = activity(limit=100)
    if rows is None:
        if health is not None:
            health.polls_failed += 1
            health.last_error = "activity feed returned None"
        return [], False
    if health is not None:
        health.polls_ok += 1
        health.last_ok_t = time.time()
        health.raw_rows += len(rows)
    out: List[dict] = []
    refused_keys = _REFUSED.setdefault(slug, set())
    for r in rows:
        try:
            t = float(r.get("timestamp"))
        except (TypeError, ValueError):
            continue
        # fail CLOSED on an unknown window: an unparseable slug must not
        # admit every action L5 has ever taken.
        if ws is None or not (ws <= t <= ws + WINDOW_S):
            continue
        if health is not None:
            health.in_window += 1
        # the whole economic identity, not the transaction hash. One
        # transaction can carry two fills at different prices, and hashing
        # by tx silently drops the second.
        #
        # Identity is judged per DISTINCT action. The feed re-serves the same
        # rows every poll, so counting each time a row is seen answers "how
        # often did we look", not "how many of L5's actions were foreign" --
        # the 1615z window counted 147 refusals and 5,070 admissions against
        # 97 distinct actions. Every counter below is keyed on the action.
        key = O.action_key(r)
        if key in seen or key in refused_keys:
            continue
        # IDENTITY, not time. Probe 05 traded two L5 actions from OTHER
        # markets because this filter stopped at the timestamp. An action may
        # enter only if its own CTF token is one of the two outcome tokens
        # the venue publishes for this exact window; anything else is refused
        # and never reaches L5_NATIVE or execution.
        verdict = MB.admit(r, binding)
        if verdict["verdict"] != MB.ADMITTED:
            refused_keys.add(key)
            if health is not None:
                health.identity_refused += 1
                health.identity_reasons[verdict["verdict"]] = (
                    health.identity_reasons.get(verdict["verdict"], 0) + 1)
                tok = verdict.get("token")
                if tok:
                    health.rejected_tokens[str(tok)] = (
                        health.rejected_tokens.get(str(tok), 0) + 1)
            continue
        if health is not None:
            tok = str(verdict.get("token"))
            health.admitted_tokens[tok] = (
                health.admitted_tokens.get(tok, 0) + 1)
            if health.binding is None and binding is not None:
                health.binding = binding.as_dict()
        act = O.l5_to_pmus_action(r)
        if not act.get("pmus_side"):
            continue
        seen.add(key)
        now = time.time()
        out.append({"action_key": key,
                    "receipt_utc": dt.datetime.fromtimestamp(
                        now, dt.timezone.utc).isoformat(),
                    "l5_event_utc": dt.datetime.fromtimestamp(
                        t, dt.timezone.utc).isoformat(),
                    "observation_latency_s": round(now - t, 2),
                    "l5": act, "pmus": {"slug": slug}})
    if health is not None:
        health.new_actions += len(out)
    return out, True
