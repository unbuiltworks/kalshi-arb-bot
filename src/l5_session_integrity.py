"""The conditions that must STOP a continuous session, and nothing else.

A long unattended run is only worth having if it fails closed on the things
that would make its output untrustworthy or dangerous. Everything else --
a quiet market, a window with no L5 actions, a refused translation -- is
ordinary and must NOT stop the run, or the session becomes a series of
interruptions again.

Halting conditions, each of which means either "the evidence is not what it
claims" or "something moved that should not have":

  FROZEN_RULE_CHANGED     the frozen engine is not byte-identical to 030d8bf
  AUTH_FEED_STALE         no authenticated book update for too long
  AUTH_FEED_NEVER_STARTED  a subscribed market never produced a first book
  MARKET_IDENTITY_MISMATCH  a record's market is not the window's market
  UNEXPECTED_MUTATION     an order left this process while DISARMED
  CREDENTIALS_LOST        the credentials went away mid-session
  STATE_CORRUPT           the inventory store stopped being readable

`UNEXPECTED_MUTATION` is the one that matters most: DISARMED, submitted_live
must be 0 forever. A non-zero value means an assumption broke, and the run
stops immediately rather than continuing to produce records that look like
shadow evidence while real orders exist.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

#: sha256 prefixes at the freeze commit 030d8bf
FROZEN: Dict[str, str] = {
    "src/l5_faithful_translation.py": "e2f786e00c800dbf",
    "src/l5_fidelity_report.py": "c10f7884bb328dc7",
    "src/l5_execution_translations.py": "b043893ba983ec56",
}

#: no authenticated book update for this long means the feed is gone, not
#: that the market is quiet: the recorder holds hundreds of markets and a
#: healthy socket produces something.
FEED_STALE_S = 300.0

#: how long a newly subscribed market may go without its FIRST book before
#: the feed is declared never to have started.
#:
#: This bound exists because its absence was a hole. The staleness check
#: above measures `now - last_book_t`, so it could only run once a book had
#: arrived; with `last_book_t` still None it was skipped entirely, and a
#: market that never produced a single book was indistinguishable from one
#: not yet checked. On 2026-09-24 the authenticated book stopped at 09:59Z
#: and four consecutive windows ran to completion with zero book states --
#: 67 minutes -- without this module halting anything, because never-arrived
#: was precisely the case it could not see. Never-arrived IS the total
#: outage, so it is now the stricter of the two paths.
#:
#: 120s is generous against what a healthy subscription actually does: the
#: deterministic computed-slug subscription produced its first book between
#: +0.2s and +1.4s of window open across 31 healthy windows that day.
FEED_START_GRACE_S = 120.0


class Halt(RuntimeError):
    """Raised to stop the session. Carries the reason and the evidence."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def frozen_ok(root: Optional[Path] = None) -> Dict[str, Any]:
    """Byte-identity of the frozen engine, checked not assumed."""
    root = root or Path(__file__).resolve().parents[1]
    out, bad = {}, []
    for rel, want in FROZEN.items():
        p = root / rel
        try:
            got = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except OSError as exc:
            got = "UNREADABLE:%s" % type(exc).__name__
        out[rel] = got
        if got != want:
            bad.append({"file": rel, "expected": want, "got": got})
    return {"hashes": out, "ok": not bad, "mismatches": bad}


def check(*, window_market: str,
          last_book_t: Optional[float],
          submitted_live: int,
          records: Sequence[Mapping[str, Any]] = (),
          has_credentials: bool = True,
          store_readable: bool = True,
          subscribed_at: Optional[float] = None,
          now: Optional[float] = None,
          root: Optional[Path] = None) -> Dict[str, Any]:
    """Raise Halt on a genuine integrity problem; otherwise return the state.

    `subscribed_at` is when THIS PROCESS began observing this market. It
    anchors the startup grace, and it is deliberately the process's own
    anchor rather than the window's open time: a deploy that lands
    mid-window has not been waiting since the window opened, and charging
    it for that time would halt the session on every deploy for a feed that
    is in fact healthy.

    Deliberately does NOT halt on: no L5 actions, no trades, a refused
    translation, a one-sided book, or a window with nothing in it.
    """
    now = time.time() if now is None else now

    if submitted_live:
        raise Halt("UNEXPECTED_MUTATION",
                   {"submitted_live": submitted_live,
                    "note": "DISARMED: an order left this process"})

    fz = frozen_ok(root)
    if not fz["ok"]:
        raise Halt("FROZEN_RULE_CHANGED", fz["mismatches"])

    if not has_credentials:
        raise Halt("CREDENTIALS_LOST", {"note": "authenticated reads cannot "
                                                "be trusted without them"})
    if not store_readable:
        raise Halt("STATE_CORRUPT", {"note": "inventory store unreadable"})

    for r in records:
        m = r.get("market")
        if m and m != window_market:
            raise Halt("MARKET_IDENTITY_MISMATCH",
                       {"record_market": m, "window_market": window_market})

    age = None if last_book_t is None else round(now - last_book_t, 1)
    if age is None:
        # No book has EVER arrived for this market. Once the subscription
        # has had its grace, that is the total-outage case, not a pending
        # one, and it fails closed here rather than running on blind.
        waited = (None if subscribed_at is None
                  else round(now - subscribed_at, 1))
        if waited is not None and waited > FEED_START_GRACE_S:
            raise Halt("AUTH_FEED_NEVER_STARTED",
                       {"market": window_market,
                        "waited_s": waited,
                        "grace_s": FEED_START_GRACE_S,
                        "usable_book_states": 0,
                        "note": "subscribed, but the authenticated feed "
                                "never produced a first usable book"})
    elif age > FEED_STALE_S:
        raise Halt("AUTH_FEED_STALE", {"book_age_s": age,
                                       "limit_s": FEED_STALE_S})

    return {"ok": True, "frozen": fz["hashes"], "book_age_s": age,
            "feed_started": last_book_t is not None,
            "waited_for_first_book_s": (
                None if last_book_t is not None or subscribed_at is None
                else round(now - subscribed_at, 1)),
            "submitted_live": submitted_live}
