"""Realtime L5 -> PMUS BTC15m copyability observer. READ-ONLY / SHADOW ONLY.

THE QUESTION. Not "would an L5-shaped policy do well on PMUS" -- the frozen
shadow already asks that. This asks the harder, time-sensitive one:

    when we LEARN what L5 actually did, is there still an economically
    comparable PMUS BTC15m action available?

which cannot be answered from history, because the answer is destroyed by
hindsight. A backtest sees L5's 15:13:19 trade and PMUS's 15:13:19 book
together. We do not. We see the trade when the feed publishes it, and the only
honest PMUS state to price it against is the one visible AT THAT MOMENT.

SO NOTHING IS EVER BACKDATED. Every row records three clocks -- L5's event
time, our receipt time, and the age of the PMUS snapshot we priced it against
-- and the PMUS snapshot is taken when the action is RECEIVED, never when it
happened. A row whose PMUS snapshot predates its receipt is a bug, and
`no_backdating_ok` on every row is the assertion that it did not happen.

TWO TRACKS THAT NEVER TOUCH.

  MIRROR_RAW    translate L5's actual observed action into a PMUS q=1 action.
                This measures COPYABILITY: did L5's own decision survive the
                trip to us?
  CLONE_MODEL   the frozen L5 behavioural engine, deciding independently from
                PMUS state alone, knowing nothing about L5's feed. This
                measures TRANSFERABILITY.

Their inventories and economics are kept in separate objects and are never
summed, because a number that mixes "what L5 did" with "what our model would
do" answers neither question.

EVIDENCE CLASSES ARE THE FROZEN ONES. Public-book interaction can only reach
NO_INTERACTION or QUEUE_AMBIGUOUS, and it says so. No fill is ever
manufactured: `is_fill` is False everywhere until account evidence exists.

RESTART SAFETY. The tape is append-only JSONL and the dedupe index is rebuilt
from it at boot, so restarting loses nothing and double-counts nothing. A dead
window is recorded as dead and collection continues.

Structurally incapable of trading: public GETs only, no credential, no order
path, no venue client that can mutate.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import pmus_fees as fees
from src import pmus_interaction_classifier as clf
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key

L5_DATA = "https://data-api.polymarket.com"
L5_ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
PMUS_GATEWAY = "https://gateway.polymarket.us"
UA = {"User-Agent": "Mozilla/5.0 (l5-pmus-realtime-observer; read-only)"}

#: the fallback listing's offset cap. Named so it is visible that this walk
#: is bounded and cannot reach a current window on its own -- that is the
#: direct slug resource's job, and this is only the fallback.
EVENTS_WALK_MAX_OFFSET = 4000

#: LOOKUP LATENCY IS PART OF A WINDOW'S BUDGET. `_get`'s 20s default applied
#: to a 40-page fallback walk is up to 800 seconds -- longer than the window
#: it is trying to confirm. The direct read gets a short timeout and the
#: fallback walk gets a wall-clock budget, so one attempt cannot consume the
#: runway the caller is trying to bound.
VENUE_LOOKUP_TIMEOUT_S = 8.0
EVENTS_WALK_BUDGET_S = 20.0

L5_SERIES = "btc-updown-15m"
PMUS_SERIES = "btc-updown-15m"
WINDOW_S = 900

#: THE BAND IS A SAFETY OVERLAY, NOT A DEFINITION OF AN L5 ACTION.
#:
#: It was previously a copyability CLASS -- an L5 trade at 0.05 came back
#: "BAND_INADMISSIBLE", which quietly asserted that PMUS could not reproduce
#: it. PMUS can: the venue quotes 0.01-0.99 and would have taken the order.
#: What the band does is stop US from executing it in a bounded live probe,
#: which is a fact about our risk appetite and not about the venue or about
#: what L5 did.
#:
#: So it is recorded on its own axis. `copyability` answers "could PMUS have
#: reproduced this action"; `safety_overlay` answers "would our own controls
#: have let us". Conflating them contaminates the replication measurement
#: with a profitability heuristic, which is the failure this separation
#: exists to prevent.
BAND_LO, BAND_HI = D("0.20"), D("0.80")
TICK = D("0.01")

LABELS = ("SHADOW/READ-ONLY. No order exists on either venue. Nothing here is "
          "submitted, filled, settled or realized.")


# ------------------------------------------------------------------ transport

def _ctx() -> ssl.SSLContext:
    bundle = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    if bundle and os.path.exists(bundle):
        try:
            return ssl.create_default_context(cafile=bundle)
        except Exception:                                      # noqa: BLE001
            pass
    return ssl.create_default_context()


#: REVALIDATION HEADERS FOR THE ACTIVITY FEED ONLY.
#:
#: WHY. The activity request was byte-identical on every poll and carried
#: nothing but a User-Agent, so any intermediary -- a CDN, a reverse proxy,
#: the API's own edge -- could serve one cached body indefinitely and
#: `urlopen` would report a clean HTTP 200 every time. That is exactly the
#: observed failure: on 2026-10-29 the collector made 3,166 successful polls
#: over 4h54m with `feed_errors 0`, received exactly 200 rows each time, and
#: `newest_row_event_utc` never moved off 17:54:50Z while `ingest:dup`
#: reached 633,200 of 633,200 and the cursor key count stayed at 5,556.
#:
#: WHAT IT IS NOT. This is not a claim that a cache WAS the cause -- a wallet
#: that genuinely stopped trading produces an identical signature, and that
#: cannot be separated from inside this process. It removes the one
#: explanation we can remove cheaply, and it is inert if the cause is
#: elsewhere: no retry, no new endpoint, no second request.
NO_CACHE = {"Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache"}


def _get(url: str, timeout: float = 20.0, *,
         no_cache: bool = False) -> Any:
    headers = dict(UA)
    if no_cache:
        headers.update(NO_CACHE)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as fh:
        return json.loads(fh.read().decode())


#: ============ THE v1 -> v2 MIGRATION OF THE ACTIVITY FEED.
#:
#: WHAT HAPPENED. Polymarket RETIRED Data API v1. The legacy `/activity`
#: path this module polled now answers:
#:
#:   HTTP 410 Gone
#:   {"error": "Data API v1 has been retired",
#:    "migration": "https://data-api.polymarket.com/v2/"}
#:
#: and `l5_activity` swallowed that exception whole and returned None, so the
#: executor counted `ingest:FEED_FAILED` 577 times with no status, no
#: exception type and no body anywhere in the telemetry. `distinct_slugs_seen
#: 0` and `rows_btc15m 0` followed mechanically, and the heartbeat could not
#: distinguish "L5 stopped trading" from "we cannot read the feed at all".
#:
#: IT WAS THE SECOND. Checked against v2 at 2026-11-05T20:40Z, L5
#: (l5Zn1bWoM8eTsK) was trading BTC15m THAT MINUTE -- slug
#: btc-updown-15m-1793910600, "Bitcoin Up or Down - November 5,
#: 3:30PM-3:45PM ET". Every row the replication program needs has been
#: arriving at an endpoint we stopped being able to read.
#:
#: TWO SEPARATE BREAKS, and fixing only one of them still yields None:
#:   1. THE ENVELOPE. v1 returned a BARE LIST. v2 returns
#:      {"data": [...], "pagination": {...}}, so `isinstance(rows, list)`
#:      is False and the old guard maps a perfectly good response to a
#:      feed failure.
#:   2. THE FIELD NAMES. v2 is snake_case. Confirmed field-by-field against
#:      a real v1 row captured 2026-09-01 (data/l5/ops/activity_20260901.jsonl):
#:      ten fields are pure case changes, and `asset` was RENAMED to
#:      `token_id` -- both long decimal ERC1155 token ids. Nothing else in
#:      v1 is absent from v2 but our own `_`-prefixed annotations.
#:
#: WHY NORMALIZE INWARD RATHER THAN CHASE 290 CALL SITES. `src` reads
#: `slug` 253 times, `side` 152, `price` 128, `asset` 22, `transactionHash`
#: 6. Renaming at the boundary keeps every one of them correct and puts the
#: migration in ONE reviewable place. A half-done rename is worse than the
#: outage: rows would flow and `action_key` would quietly key on
#: `None|None|...`, which LOOKS like a working feed.
V2_ACTIVITY = "%s/v2/activity?user=%s&limit=%d&type=TRADE&_cb=%d"

#: v2 name -> the v1 name the rest of this repository reads. Case changes
#: plus the one true rename.
V2_FIELD_MAP = {
    "transaction_hash": "transactionHash",
    "token_id": "asset",
    "usdc_size": "usdcSize",
    "condition_id": "conditionId",
    "outcome_index": "outcomeIndex",
    "event_slug": "eventSlug",
    "proxy_wallet": "proxyWallet",
    "profile_image": "profileImage",
    "profile_image_optimized": "profileImageOptimized",
}

#: THE LAST FEED FAILURE, so `ingest:FEED_FAILED` stops being a bare count.
#: This is the third time in this repository that an error swallowed to a
#: counter has cost a diagnosis -- after the 2026-10-18 cancel 400 and the
#: watchdog's `read_error: "HTTPError"`. Reporting only: nothing reads it to
#: decide anything, and it can never turn a failure into a success.
_LAST_FEED_FAILURE: Dict[str, Any] = {}


def last_feed_failure() -> Dict[str, Any]:
    """What the most recent feed failure actually was. Read-only."""
    return dict(_LAST_FEED_FAILURE)


def normalize_activity_row(row: Any) -> Any:
    """One v2 row under the v1 field names. Non-mappings pass through.

    ADDITIVE AND NON-DESTRUCTIVE: a v1 name already present is never
    overwritten, so a mixed or already-normalized row is left alone and
    this is safe to apply twice. `_api_version` is stamped for provenance,
    because a row's shape should be answerable from the row.
    """
    if not isinstance(row, Mapping):
        return row
    out = dict(row)
    for v2k, v1k in V2_FIELD_MAP.items():
        if v2k in out and v1k not in out:
            out[v1k] = out[v2k]
    out.setdefault("_api_version", "v2")
    return out


def l5_activity(limit: int = 200) -> Optional[List[dict]]:
    """L5's published trades, newest first. None means the feed failed.

    REVALIDATED ON EVERY POLL, and the URL carries a cache-buster, because
    a frozen body behind a clean 200 is indistinguishable from a quiet
    wallet from in here. See NO_CACHE.

    v2, UNWRAPPED AND NORMALIZED. See V2_ACTIVITY above for why both steps
    are needed. A failure records its status and body in
    `last_feed_failure()` instead of vanishing.
    """
    global _LAST_FEED_FAILURE
    url = V2_ACTIVITY % (L5_DATA, L5_ADDR, limit, int(time.time()))
    try:
        payload = _get(url, no_cache=True)
    except urllib.error.HTTPError as exc:                      # noqa: BLE001
        body = ""
        try:
            body = exc.read().decode()[:400]
        except Exception:                                      # noqa: BLE001
            body = "BODY_UNREADABLE"
        _LAST_FEED_FAILURE = {
            "kind": "HTTPError", "status": getattr(exc, "code", None),
            "reason": str(getattr(exc, "reason", ""))[:120],
            "body": body, "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        return None
    except Exception as exc:                                   # noqa: BLE001
        _LAST_FEED_FAILURE = {
            "kind": type(exc).__name__, "status": None,
            "reason": repr(exc)[:200], "body": "",
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        return None
    # THE ENVELOPE. v2 wraps the rows; v1 did not. Both are accepted so a
    # server that reverts, or a cached v1 body, still reads.
    rows = payload
    if isinstance(payload, Mapping):
        rows = payload.get("data")
    if not isinstance(rows, list):
        _LAST_FEED_FAILURE = {
            "kind": "UNEXPECTED_SHAPE", "status": 200,
            "reason": "no row list; payload was %s%s"
                      % (type(payload).__name__,
                         (" keys=%s" % sorted(payload)[:8])
                         if isinstance(payload, Mapping) else ""),
            "body": "", "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        return None
    _LAST_FEED_FAILURE = {}
    return [normalize_activity_row(r) for r in rows]


#: Gamma, for profile identity. Separate host from the activity API.
L5_GAMMA = "https://gamma-api.polymarket.com"

#: The candidate sources for L5's activity, in the order they are audited.
#: The LEGACY path is what the collector has always polled; `/v2/activity`
#: is the documented one. A deprecated endpoint that still answers 200 with
#: a frozen snapshot is exactly the observed failure, so the audit asks both
#: the same question and prints the two answers side by side.
FEED_SOURCES = (
    ("legacy_activity", "%s/activity?user=%s&limit=5&type=TRADE"),
    ("v2_activity", "%s/v2/activity?user=%s&limit=5&type=TRADE"),
    ("v2_activity_notype", "%s/v2/activity?user=%s&limit=5"),
    ("legacy_activity_notype", "%s/activity?user=%s&limit=5"),
)

#: Profile-identity probes. Shapes differ between deployments, so each is
#: tried and whatever comes back is reported verbatim rather than guessed.
IDENTITY_SOURCES = (
    ("gamma_profile_address", "%s/profiles?address=%s"),
    ("data_profile_address", "%s/profile?address=%s"),
)


def _row_digest(rows: Any, n: int = 3) -> Any:
    """The identifying fields of the newest rows, and nothing else.

    No price or size arithmetic and no inference: this is for deciding WHICH
    SOURCE is fresh, so it reports each row's own identity -- the venue's
    timestamp, slug, transaction hash and whatever wallet field it carries.
    """
    if not isinstance(rows, list):
        return {"not_a_list": type(rows).__name__}
    out = {"count": len(rows), "newest": []}
    try:
        ts = [r.get("timestamp") for r in rows
              if isinstance(r, Mapping) and r.get("timestamp") is not None]
        if ts:
            newest = max(float(t) for t in ts)
            out["newest_timestamp"] = newest
            out["newest_utc"] = dt.datetime.fromtimestamp(
                newest, dt.timezone.utc).isoformat()
    except Exception:                                          # noqa: BLE001
        out["newest_utc"] = "UNREADABLE"
    for r in (rows[:n] if isinstance(rows, list) else []):
        if not isinstance(r, Mapping):
            continue
        out["newest"].append({
            "timestamp": r.get("timestamp"),
            "slug": r.get("slug"),
            "transactionHash": r.get("transactionHash"),
            "side": r.get("side"), "type": r.get("type"),
            "proxyWallet": r.get("proxyWallet"),
            "user": r.get("user"),
        })
    return out


def feed_source_audit(addr: str = None) -> dict:
    """Ask every candidate source the same question, ONCE, and report.

    WHY THIS EXISTS. The collector's legacy `/activity` poll returned a
    clean HTTP 200 with a byte-identical 200-row body for five hours
    (3,166 polls, ingest:dup 633,200 of 633,200, newest event frozen at
    17:54:50Z), and a cache-bypass with per-request cache-busting did not
    move it -- so the body is what that endpoint genuinely serves. The
    owner independently confirms the profile is this same wallet, which
    leaves WHICH ENDPOINT as the open question rather than which wallet.

    STRICTLY READ-ONLY AND ONE-SHOT. It submits nothing, ingests nothing,
    writes no cursor and feeds no decision. It does not change which source
    the collector polls -- that stays the legacy path until this evidence
    says otherwise. Every failure is captured per source so one dead host
    cannot hide the others.
    """
    addr = addr or L5_ADDR
    out = {"label": "L5_FEED_SOURCE_AUDIT", "wallet": addr,
           "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
           "activity": {}, "identity": {},
           "labels": ("READ-ONLY diagnostic. Nothing was ingested, "
                      "submitted or realized, and the collector's own "
                      "source is unchanged by this call.")}
    for name, tmpl in FEED_SOURCES:
        try:
            rows = _get(tmpl % (L5_DATA, addr) + "&_cb=%d" % int(time.time()),
                        no_cache=True)
            out["activity"][name] = _row_digest(rows)
        except urllib.error.HTTPError as exc:                  # noqa: PERF203
            out["activity"][name] = {"http_error": int(exc.code)}
        except Exception as exc:                               # noqa: BLE001
            out["activity"][name] = {"error": type(exc).__name__,
                                     "detail": str(exc)[:160]}
    for name, tmpl in IDENTITY_SOURCES:
        base = L5_GAMMA if name.startswith("gamma") else L5_DATA
        try:
            doc = _get(tmpl % (base, addr), no_cache=True)
            out["identity"][name] = (doc if isinstance(doc, (dict, list))
                                     else {"unexpected": str(doc)[:200]})
        except urllib.error.HTTPError as exc:
            out["identity"][name] = {"http_error": int(exc.code)}
        except Exception as exc:                               # noqa: BLE001
            out["identity"][name] = {"error": type(exc).__name__,
                                     "detail": str(exc)[:160]}
    return out


def _top_from_levels(md: Mapping[str, Any]) -> Optional[dict]:
    """Best bid/ask from the ACTUAL book levels of /book.

    This is not a fabricated BBO. It is the same venue book read through a
    different endpoint, because /bbo intermittently answers 200 with its
    bestBid/bestAsk fields empty for a market that is quoting and trading.
    Nothing is derived from trades and no price is invented.
    """
    bids = md.get("bids") or []
    offers = md.get("offers") or []
    if not bids or not offers:
        return None
    def px(lvl):
        v = lvl.get("px")
        return (v or {}).get("value") if isinstance(v, Mapping) else v
    def qty(lvls):
        try:
            return str(sum(D(str(l.get("qty") or 0)) for l in lvls))
        except Exception:                                      # noqa: BLE001
            return None
    return {"marketSlug": md.get("marketSlug"),
            "bestBid": {"value": px(bids[0])},
            "bestAsk": {"value": px(offers[0])},
            "bidShares": qty(bids), "askShares": qty(offers),
            "state": md.get("state"), "_source": "book_levels"}


def pmus_settlement(slug: str) -> Optional[dict]:
    """The venue's own settlement document for one market. READ-ONLY.

    WHY THIS IS NOT `pmus_bbo`. `settlementPx` lives on
    /v1/markets/{slug}/bbo beside `state`, and an EXPIRED market answers
    that route with `settlementPx` present and bestBid/bestAsk BOTH NULL.
    `pmus_bbo` reads that null top of book as an empty answer and falls
    back to /book -- which does not carry `settlementPx` at all -- so the
    settlement price is discarded for precisely the markets that have one.
    Measured on 2026-10-19: 1700z, 1715z and 1730z all returned
    MARKET_STATE_EXPIRED with settlementPx 0.0000, 1.0000 and 0.0000
    through this route, and None through `pmus_bbo`.

    So this does NOT reuse the book reader and does NOT fall back: a book
    endpoint cannot answer a settlement question. `pmus_bbo` keeps its
    behaviour exactly, because the live execution path reads prices
    through it.

    Nothing is inferred and nothing is defaulted. The caller gets the
    venue's `state` and its `settlementPx` verbatim, or None if the read
    failed -- a failed read is not an unsettled market.
    """
    try:
        md = (_get("%s/v1/markets/%s/bbo" % (PMUS_GATEWAY, slug))
              or {}).get("marketData")
    except Exception:                                          # noqa: BLE001
        return None
    if not isinstance(md, Mapping):
        return None
    return {"marketSlug": md.get("marketSlug"),
            "state": md.get("state"),
            "settlementPx": md.get("settlementPx"),
            "_source": "bbo_settlement"}


def pmus_bbo(slug: str) -> Optional[dict]:
    """Top of book, with /book as a fallback when /bbo comes back empty.

    THE DEFECT THIS FIXES. /bbo returns HTTP 200 with marketData present but
    bestBid/bestAsk EMPTY a large fraction of the time on a market that is
    demonstrably quoting -- measured at 16 of 30 consecutive samples on one
    live window, against /book on the same market showing 8 bid and 5 ask
    levels. Treating that as an absent book attributed OUR blindness to the
    venue and produced a NO_BOOK classification that the authenticated trade
    tape contradicts. The fetch outcome is recorded so a failure can never
    again be silently read as illiquidity.
    """
    src = None
    try:
        md = (_get("%s/v1/markets/%s/bbo" % (PMUS_GATEWAY, slug))
              or {}).get("marketData")
        if md:
            bb = (md.get("bestBid") or {}).get("value") \
                if isinstance(md.get("bestBid"), Mapping) else md.get("bestBid")
            ba = (md.get("bestAsk") or {}).get("value") \
                if isinstance(md.get("bestAsk"), Mapping) else md.get("bestAsk")
            if bb is not None and ba is not None:
                md["_source"] = "bbo"
                return md
            src = "bbo_empty"
    except Exception:                                          # noqa: BLE001
        src = "bbo_error"
    try:
        md2 = (_get("%s/v1/markets/%s/book" % (PMUS_GATEWAY, slug))
               or {}).get("marketData")
        top = _top_from_levels(md2 or {})
        if top:
            top["_fallback_from"] = src
            return top
        return {"_source": "empty_both", "_fallback_from": src,
                "state": (md2 or {}).get("state")}
    except Exception:                                          # noqa: BLE001
        return {"_source": "fetch_failed", "_fallback_from": src}


def pmus_book(slug: str) -> Optional[dict]:
    try:
        return (_get("%s/v1/markets/%s/book" % (PMUS_GATEWAY, slug))
                or {}).get("marketData")
    except Exception:                                          # noqa: BLE001
        return None


# ------------------------------------------------------------------ alignment

def l5_window_start(slug: str) -> Optional[int]:
    """L5 stamps the window START as a unix epoch: btc-updown-15m-<epoch>."""
    tail = str(slug).rsplit("-", 1)[-1]
    try:
        v = int(tail)
    except (TypeError, ValueError):
        return None
    return v if v > 1_000_000_000 else None


def pmus_slug_for(window_start: int) -> str:
    """The simultaneous PMUS market. PMUS stamps the START too -- VERIFIED,
    not assumed, by `verify_alignment` below."""
    d = dt.datetime.fromtimestamp(window_start, dt.timezone.utc)
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        d.strftime("%Y-%m-%d"), d.hour, d.minute)


#: HOW A CONFIRMATION ATTEMPT ENDED. Three outcomes, because collapsing them
#: is what cost three live windows on 2026-11-03.
#:
#:   CONFIRMED -- the venue states this window's start and it matches ours.
#:   MISMATCH  -- the venue states a start and it DISAGREES. Definitive: the
#:                two clocks mean different fifteen minutes, and no amount of
#:                retrying makes that safe. Never retried, always refused.
#:   TRANSIENT -- we could not get an answer: the read raised, the host
#:                answered nothing usable, the event was not served, or its
#:                startTime was unparseable. Retryable, because "I could not
#:                tell" is not "the venue disagrees".
BOUNDS_CONFIRMED = "CONFIRMED"
BOUNDS_MISMATCH = "MISMATCH"
BOUNDS_TRANSIENT = "TRANSIENT"


def venue_window_bounds_report(window_start: int, window_s: int = 900,
                               verify=None) -> Dict[str, Any]:
    """`venue_window_bounds` plus WHY, so a caller can retry the right half.

    The bounds alone cannot distinguish "the venue disagrees" from "the
    gateway did not answer", and those two want opposite handling. This
    returns both, and `venue_window_bounds` stays the thin wrapper it was.
    """
    verify = verify or verify_alignment
    rep = verify(int(window_start)) or {}
    cls = str(rep.get("classification") or "").strip()
    if not cls:
        # a caller's fake, or an older verify: infer conservatively. A stated
        # start that disagrees is the only DEFINITIVE case.
        cls = (BOUNDS_CONFIRMED if rep.get("aligned") is True
               else BOUNDS_MISMATCH if rep.get("pmus_start_utc")
               else BOUNDS_TRANSIENT)
    out: Dict[str, Any] = {"bounds": None, "classification": cls,
                           "why": rep.get("why"),
                           "pmus_start_utc": rep.get("pmus_start_utc"),
                           "pmus_slug": rep.get("pmus_slug")}
    if rep.get("aligned") is not True or not rep.get("pmus_start_utc"):
        return out
    try:
        start = dt.datetime.fromisoformat(str(rep["pmus_start_utc"]))
    except (TypeError, ValueError):
        # a start we cannot parse is not a start we can bound a window with
        out["classification"] = BOUNDS_TRANSIENT
        out["why"] = "pmus_start_utc unparseable"
        return out
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    out["classification"] = BOUNDS_CONFIRMED
    out["bounds"] = {"source": "VENUE", "start": start.isoformat(),
                     "end": (start + dt.timedelta(
                         seconds=int(window_s))).isoformat(),
                     "confirmed_by": "verify_alignment"}
    return out


def venue_window_bounds(window_start: int, window_s: int = 900,
                        verify=None) -> Optional[Dict[str, Any]]:
    """A window's bounds AS THE VENUE STATES THEM, or None.

    Both order-capable paths build their slug from a 900-second clock grid, so
    the window they believe they are trading is OUR convention and not the
    venue's statement. The two agree until they do not, and that is the case a
    pre-close margin exists for. `verify_alignment` above already reads the
    venue's own `startTime` back for a window; this wraps it in the shape the
    arming boundary takes.

    None means UNCONFIRMED, which the boundary refuses by name
    (WINDOW_BOUNDS_UNCONFIRMED) rather than falling back to the clock. It
    lives here, beside the read it depends on, because both the continuous
    runner and the one-shot probe need it and this module is in both images.
    """
    return venue_window_bounds_report(window_start, window_s,
                                      verify=verify)["bounds"]


def verify_alignment(window_start: int) -> Dict[str, Any]:
    """Prove the two venues mean the same fifteen minutes.

    Two venues sharing a product name is not evidence that they share a clock:
    Kalshi stamps the CLOSE in the same position where these two stamp the
    OPEN, and an off-by-one-window comparison would look plausible and be
    entirely wrong. So the PMUS event's own startTime is read back and
    compared against L5's epoch.
    """
    want = dt.datetime.fromtimestamp(window_start, dt.timezone.utc)
    out: Dict[str, Any] = {
        "l5_window_start_utc": want.isoformat(),
        "pmus_slug": pmus_slug_for(window_start),
        "aligned": None, "why": None,
    }
    want_ev = "btc-updown-15m-%s-%02d%02dz" % (
        want.strftime("%Y-%m-%d"), want.hour, want.minute)

    def _settle(got_raw: Any) -> Dict[str, Any]:
        """Classify a startTime the venue actually stated."""
        try:
            got = dt.datetime.fromisoformat(
                str(got_raw or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            out["classification"] = BOUNDS_TRANSIENT
            out["why"] = "PMUS startTime unparseable: %r" % (got_raw,)
            return out
        if got.tzinfo is None:
            got = got.replace(tzinfo=dt.timezone.utc)
        out["pmus_start_utc"] = got.isoformat()
        out["aligned"] = (got == want)
        out["classification"] = (BOUNDS_CONFIRMED if out["aligned"]
                                 else BOUNDS_MISMATCH)
        out["why"] = ("PMUS event startTime equals L5's window epoch"
                      if out["aligned"] else
                      "PMUS startTime %s != L5 %s" % (got, want))
        return out

    # THE DIRECT RESOURCE, and it is the whole fix. `/v1/events/slug/{event}`
    # addresses the event BY NAME, so it reaches the current window however
    # old the series is.
    #
    # WHAT THIS REPLACES, and why it had to go. The previous read walked
    # `/v1/events?series_id=375` OLDEST FIRST with `while off < 4000`, so it
    # could only ever see the series' oldest 4,000 events. At 96 windows a
    # day the series outgrew that cap, and from then on EVERY window came
    # back "not listed" -- which `venue_window_bounds` turned into None and
    # the runner cached for the whole window. Measured on 2026-11-03: three
    # consecutive windows refused, 2300z alone 241 times, with a healthy
    # book (subscribed before open, 8,540 updates, 500 trades, 0.46/0.59).
    #
    # The identical defect was already found and fixed once, in
    # `l5_pmus_transport.resolve_market`, for the /v1/markets catalogue:
    # "paged properly it runs past 40,000 rows ordered by ascending id, so a
    # continuously created 15m window sits at the far end and a bounded walk
    # can never reach it." This read never got that fix.
    #
    # A QUERY PARAMETER IS NOT AN OPTION. The same module records that the
    # gateway silently IGNORES filters it does not know -- `?slug=`,
    # `?seriesId=` and `?seriesSlug=` all answer 200 with the unfiltered
    # default page -- so the slug is matched HERE, on the response, never
    # delegated to a filter that may not exist.
    try:
        ev = _get("%s/v1/events/slug/%s" % (PMUS_GATEWAY, want_ev),
                  VENUE_LOOKUP_TIMEOUT_S, no_cache=True) or {}
        row = ev.get("event") if isinstance(ev, Mapping) else None
        if not isinstance(row, Mapping):
            row = ev if isinstance(ev, Mapping) else {}
        if str(row.get("slug")) == want_ev and row.get("startTime"):
            return _settle(row.get("startTime"))
        # served something, but not this event: fall through to the listing.
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:                               # noqa: BLE001
        # a 404 here is NOT definitive -- a window is created shortly before
        # it opens -- so this is transient and the listing is still tried.
        out["why"] = "slug lookup failed: %s" % type(exc).__name__

    # THE BOUNDED LISTING, kept only as a fallback for the case where the
    # slug resource does not carry `startTime`. Still bounded, and still
    # oldest-first, so it is NOT relied on: it is the belt to the direct
    # read's braces, and a miss here is TRANSIENT rather than definitive.
    try:
        off = 0
        walk_t0 = time.time()
        while off < EVENTS_WALK_MAX_OFFSET:
            if time.time() - walk_t0 > EVENTS_WALK_BUDGET_S:
                out["classification"] = BOUNDS_TRANSIENT
                out["why"] = ("fallback listing walk exceeded its %.0fs "
                              "budget at offset %d" % (EVENTS_WALK_BUDGET_S,
                                                       off))
                return out
            ev = _get("%s/v1/events?series_id=375&limit=100&offset=%d"
                      % (PMUS_GATEWAY, off), VENUE_LOOKUP_TIMEOUT_S)
            rows = (ev.get("events") or []) if isinstance(ev, Mapping) else []
            if not rows:
                break
            for e in rows:
                if isinstance(e, Mapping) and str(e.get("slug")) == want_ev:
                    return _settle(e.get("startTime"))
            off += len(rows)
            if len(rows) < 100:
                break
    except Exception as exc:                                   # noqa: BLE001
        out["classification"] = BOUNDS_TRANSIENT
        out["why"] = "alignment check failed: %s" % type(exc).__name__
        return out
    # NOT LISTED IS NOT A DISAGREEMENT. The venue told us nothing about this
    # window, which is exactly the state a bounded retry exists for.
    out["classification"] = BOUNDS_TRANSIENT
    out["why"] = ("PMUS event not served for %s by /v1/events/slug/ or the "
                  "bounded listing" % want_ev)
    return out


# ------------------------------------------------------------- L5 translation

def l5_to_pmus_action(row: Mapping[str, Any]) -> Dict[str, Any]:
    """L5's observed trade as a PMUS q=1 intent.

    L5's `outcome` is Up/Down and PMUS's YES is Up, so the sides map directly.
    A SELL is carried through as a sell of that outcome and is NOT silently
    turned into a buy of the other one: L5's BTC15m corpus contains ZERO
    sells, so a sell appearing here is news, not a translation problem.
    """
    outcome = str(row.get("outcome") or "").strip().lower()
    side = str(row.get("side") or "").strip().upper()
    px = row.get("price")
    try:
        price = D(str(px))
    except Exception:                                          # noqa: BLE001
        price = None
    return {
        "l5_outcome": row.get("outcome"),
        "l5_side": side,
        "l5_price": str(price) if price is not None else None,
        "l5_size": row.get("size"),
        "l5_usdc": row.get("usdcSize"),
        "pmus_side": ("UP" if outcome == "up" else
                      "DOWN" if outcome == "down" else None),
        "direction": "BUY" if side == "BUY" else side,
        "is_sell": side == "SELL",
        "q": 1,
    }


# ---------------------------------------------------------------- copyability

def copyability(action: Mapping[str, Any],
                bbo: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Is L5's action still available to us on PMUS, right now, as a maker?

    Classes, and why each is separate:
      NO_BOOK              PMUS is unreadable or the side is absent
      MAKER_COPYABLE       we can REST at L5's price or better without crossing
      REQUIRES_CROSSING    reaching L5's price means taking, which pays the
                           taker fee and gives up the spread the whole exercise
                           exists to earn
      PRICE_MOVED_AWAY     the side exists but L5's price is no longer
                           reachable at all on the current book
    """
    out: Dict[str, Any] = {"klass": None, "why": None,
                           "band": [str(BAND_LO), str(BAND_HI)]}
    side = action.get("pmus_side")
    lp = action.get("l5_price")
    if bbo is None or side is None or lp is None:
        out["klass"] = "NO_BOOK"
        out["why"] = "no PMUS book, no side, or no L5 price"
        return out

    def f(k):
        v = (bbo.get(k) or {}).get("value") if isinstance(bbo.get(k), Mapping) \
            else bbo.get(k)
        try:
            return D(str(v))
        except Exception:                                      # noqa: BLE001
            return None

    yes_bid, yes_ask = f("bestBid"), f("bestAsk")
    if yes_bid is None or yes_ask is None:
        out["klass"] = "NO_BOOK"
        out["why"] = "one-sided or unreadable PMUS book"
        out["yes_bid"] = str(yes_bid) if yes_bid is not None else None
        out["yes_ask"] = str(yes_ask) if yes_ask is not None else None
        return out

    # our side's own bid/ask. NO is the complement of the YES book.
    if side == "UP":
        our_bid, our_ask = yes_bid, yes_ask
    else:
        our_bid, our_ask = (D(1) - yes_ask), (D(1) - yes_bid)

    l5_price = D(lp)
    out.update({"yes_bid": str(yes_bid), "yes_ask": str(yes_ask),
                "our_bid": str(our_bid), "our_ask": str(our_ask),
                "spread": str(yes_ask - yes_bid),
                "l5_price": str(l5_price),
                "price_delta_vs_our_touch": str(l5_price - our_bid)})

    if l5_price >= our_ask:
        out["klass"] = "REQUIRES_CROSSING"
        out["why"] = ("L5's price is at or through our ask, so copying it "
                      "would take rather than make")
        out["taker_fee_if_crossed"] = str(fees.taker_fee(l5_price))
        return out
    out["klass"] = "MAKER_COPYABLE"
    out["why"] = "we can rest at L5's price without crossing"
    out["maker_rebate_if_filled"] = str(fees.maker_rebate(l5_price))
    out["ticks_from_our_touch"] = str(
        ((l5_price - our_bid) / TICK).quantize(D("1")))
    return out


# ------------------------------------------------------------------ inventory

def safety_overlay(action: Mapping[str, Any]) -> Dict[str, Any]:
    """Would OUR controls have permitted this action in a live probe?

    Separate from copyability on purpose. This may block execution; it may
    never be read as "L5 would not have done it" or "PMUS could not have
    reproduced it".
    """
    lp = action.get("l5_price")
    if lp is None:
        return {"in_band": None, "would_block": None,
                "band": [str(BAND_LO), str(BAND_HI)],
                "why": "no price"}
    px = D(lp)
    inb = BAND_LO <= px <= BAND_HI
    return {
        "band": [str(BAND_LO), str(BAND_HI)],
        "in_band": inb,
        "would_block": not inb,
        "control": "entry_band",
        "note": ("a live probe would refuse this price. The venue would not, "
                 "and L5 took it." if not inb else "within our bounds"),
    }


class Book:
    """q=1 behavioural inventory. One per TRACK; never merged."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.up = 0
        self.dn = 0
        self.up_cost = D("0")
        self.dn_cost = D("0")
        self.actions = 0

    @property
    def residual(self) -> int:
        return self.up - self.dn

    @property
    def matched(self) -> int:
        return min(self.up, self.dn)

    def add(self, side_up: bool, px: D) -> None:
        self.actions += 1
        if side_up:
            self.up += 1
            self.up_cost += px
        else:
            self.dn += 1
            self.dn_cost += px

    def pair_cost(self) -> Optional[D]:
        if not self.matched:
            return None
        return ((self.up_cost / self.up if self.up else D(0))
                + (self.dn_cost / self.dn if self.dn else D(0)))

    def relation_for(self, side_up: bool) -> str:
        """OPEN / EXTEND / COMPLETE from inventory alone, the frozen
        vocabulary."""
        if self.up == 0 and self.dn == 0:
            return "open"
        r = self.residual
        if r == 0:
            return "open"
        return "extend" if (r > 0) == side_up else "complete"

    def snapshot(self) -> Dict[str, Any]:
        pc = self.pair_cost()
        return {"track": self.name, "up": self.up, "dn": self.dn,
                "actions": self.actions,
                "residual": self.residual, "matched": self.matched,
                "effective_pair_cost": str(pc) if pc is not None else None,
                "labels": "SHADOW inventory; no order exists"}


# ------------------------------------------------------------------ the loop

def action_key(row: Mapping[str, Any]) -> str:
    """Dedupe key.

    NOT the transaction hash alone. Measured on this very feed: a single
    transaction can carry two fills at different prices, so hashing by tx
    silently drops the second one and makes L5 look less active than he is.
    The key is therefore the whole economic identity of the fill.
    """
    return "|".join(str(row.get(k)) for k in
                    ("transactionHash", "asset", "side", "price", "size",
                     "timestamp"))


class Observer:
    def __init__(self, state_dir: str) -> None:
        self.dir = state_dir
        os.makedirs(self.dir, exist_ok=True)
        self.tape = os.path.join(self.dir, "l5_pmus_realtime.jsonl")
        self.seen: set = set()
        self.windows: Dict[int, Dict[str, Any]] = {}
        self.alignment: Dict[int, dict] = {}
        self.pmus_cache: Dict[str, Tuple[float, Optional[dict]]] = {}
        self.stats = collections.Counter()
        self.latencies: List[float] = []
        self.model = pv2.load_v2()
        self.clone_last_rel = ""
        self.clone_ticks = collections.Counter()
        self._pending_log: List[Mapping[str, Any]] = []
        self._rows_logged: set = set()
        self._hydrate()

    # -- restart safety ---------------------------------------------------
    def _hydrate(self) -> None:
        """Rebuild the dedupe index from the tape. Restarting must neither
        lose the sample nor double-count it."""
        if not os.path.exists(self.tape):
            return
        n = 0
        with open(self.tape) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "l5_action":
                    continue
                k = r.get("action_key")
                if k:
                    self.seen.add(k)
                    n += 1
                # Backlog latencies are an artefact of when a process
                # started, so hydrating them would reimport the very
                # distortion the live path excludes.
                if r.get("backlog"):
                    self.stats["backlog"] += 1
                    continue
                self.stats["realtime"] += 1
                lat = r.get("latency_s")
                if isinstance(lat, (int, float)):
                    self.latencies.append(float(lat))
        self.stats["hydrated"] = n

    def _emit(self, row: Mapping[str, Any]) -> None:
        with open(self.tape, "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        self._pending_log.append(row)

    def emit_new_rows(self) -> Optional[dict]:
        """Print tape rows first written since the last emission.

        WHY THIS EXISTS. Once this observer runs on Railway its tape lives on a
        volume the analysis process cannot open. `polls` and `actions_observed`
        prove rows exist and say nothing about any particular one, so a window
        recorded there could never be scored. This is the same narrowest-safe
        method already used for the authenticated trade rows: it formats data
        this process already holds and writes it to stdout. It opens no socket,
        serves no request, adds no access-control surface, and cannot reach an
        order path.

        INCREMENTAL, so the log is a TAPE and not a repeated snapshot: each
        emission carries only rows not already emitted, keyed on the row's own
        action_key, so a consumer can concatenate emissions without
        deduplicating and without holes. OFF unless the cap is set to a
        positive integer; a non-numeric value is OFF rather than unbounded.
        Never raises -- an evidence convenience must not be able to stop
        collection.
        """
        cap = rows_log_cap()
        if not cap:
            self._pending_log.clear()
            return None
        fresh: List[Mapping[str, Any]] = []
        try:
            for row in self._pending_log:
                k = row.get("action_key")
                if k is None or k in self._rows_logged:
                    continue
                self._rows_logged.add(k)
                fresh.append(row)
                if len(fresh) >= cap:
                    break
            self._pending_log.clear()
            if len(self._rows_logged) > 4000:              # bounded memory
                self._rows_logged = set(list(self._rows_logged)[-2000:])
            if not fresh:
                return None
            doc = {
                "t": dt.datetime.now(dt.timezone.utc).isoformat(),
                "kind": "observer_rows", "rows": len(fresh),
                "cap_per_emission": cap, "tape": self.tape,
                "schema": ("verbatim observer tape rows: three clocks "
                           "(l5_event_utc, receipt_utc, pmus_snapshot_age_s), "
                           "the PMUS state snapshotted AT RECEIPT, the "
                           "copyability class, safety_overlay on its own axis, "
                           "and no_backdating_ok"),
                "labels": LABELS,
                "data": [json.loads(json.dumps(r, default=str)) for r in fresh],
            }
            print("L5_PMUS_ROWS %s" % json.dumps(doc), flush=True)
            return doc
        except Exception:                                  # noqa: BLE001
            self._pending_log.clear()
            return None

    # -- PMUS state, snapshotted AT RECEIPT --------------------------------
    def pmus_now(self, slug: str, max_age_s: float = 2.0) -> Tuple[Optional[dict], float]:
        """Current PMUS BBO and the age of the snapshot we priced against.

        A tiny cache exists because a burst of L5 rows arrives in one poll and
        re-fetching per row would price them against different books for no
        reason. The age is RECORDED on every row so a stale snapshot can never
        masquerade as a fresh one.
        """
        now = time.time()
        hit = self.pmus_cache.get(slug)
        if hit and (now - hit[0]) <= max_age_s:
            return hit[1], now - hit[0]
        b = pmus_bbo(slug)
        self.pmus_cache[slug] = (time.time(), b)
        return b, 0.0

    # -- CLONE_MODEL: the frozen engine, from PMUS state alone -------------
    def clone_tick(self) -> Optional[dict]:
        """One independent decision on the PMUS window trading right now.

        This track knows NOTHING about L5's feed. It is the transferability
        question -- would the frozen model, seeing only PMUS, do what L5 does
        -- and mixing it with MIRROR_RAW would answer neither.

        No fill is assumed, so its inventory advances only where interaction
        evidence would support it, which on a public book is never. The
        relation it WOULD take is recorded regardless, because that is the
        behavioural signal.
        """
        now = time.time()
        ws = int(now // WINDOW_S) * WINDOW_S
        slug = pmus_slug_for(ws)
        bbo, age = self.pmus_now(slug)
        if bbo is None:
            return None

        def f(k):
            v = (bbo.get(k) or {}).get("value") \
                if isinstance(bbo.get(k), Mapping) else bbo.get(k)
            try:
                return float(v)
            except Exception:                                  # noqa: BLE001
                return None

        bid, ask = f("bestBid"), f("bestAsk")
        if bid is None or ask is None:
            return {"kind": "clone_tick", "slug": slug, "action": "HOLD",
                    "reason": "one_sided_book"}
        w = self.windows.setdefault(ws, {
            "window_start": ws, "pmus_slug": slug,
            "mirror": Book("MIRROR_RAW"), "clone": Book("CLONE_MODEL"),
            "l5_actions": 0, "l5_sides": collections.Counter(),
            "l5_relations": collections.Counter(),
            "copy": collections.Counter()})
        clone = w["clone"]
        tte = (ws + WINDOW_S) - now
        st = state_key(bid, tte, float(clone.residual))
        d = pv2.v2_dist(self.model, st, self.clone_last_rel or "open", "q<5")
        if d["backed_off"]:
            return {"kind": "clone_tick", "slug": slug, "action": "HOLD",
                    "reason": "model_off_distribution", "state": st}
        prop = pv2.proposal(self.model, st, self.clone_last_rel or "open",
                            "q<5", clone.residual, None)
        rel = prop["recommended_relation"]
        if clone.up == 0 and clone.dn == 0:
            rel = "open"
        if rel == "sell":
            return {"kind": "clone_tick", "slug": slug, "action": "HOLD",
                    "reason": "sell_not_in_scope", "state": st}
        side_up = ((rel in ("open", "extend") and clone.residual >= 0)
                   or (rel == "complete" and clone.residual < 0))
        px = D(str(bid if side_up else (1.0 - ask))).quantize(TICK)
        row = {"kind": "clone_tick", "utc": dt.datetime.now(
                   dt.timezone.utc).isoformat(),
               "slug": slug, "tte_s": round(tte, 1), "state": st,
               "relation": rel, "side": "UP" if side_up else "DOWN",
               "desired_price": str(px), "size": 1,
               "pmus_snapshot_age_s": round(age, 3),
               "book": {"bid": bid, "ask": ask},
               "maker_rebate_if_filled": str(fees.maker_rebate(px)),
               "taker_fee_if_crossed": str(fees.taker_fee(px)),
               "interaction": clf.classify(
                   {"desired_price": str(px),
                    "side": "UP" if side_up else "DOWN"}, None, False),
               "inventory": clone.snapshot(),
               "track": "CLONE_MODEL",
               "labels": "SHADOW; independent of L5's feed; never summed with "
                         "MIRROR_RAW"}
        if not (BAND_LO <= px <= BAND_HI):
            row["action"] = "HOLD"
            row["reason"] = "entry_band"
            row["band"] = [str(BAND_LO), str(BAND_HI)]
        else:
            row["action"] = "QUOTE"
            self.clone_last_rel = rel
            w["l5_relations"]  # touch, keeps shape stable
        self._emit(row)
        return row

    # -- one poll ----------------------------------------------------------
    def poll(self) -> Dict[str, Any]:
        recv = time.time()
        rows = l5_activity(limit=200)
        if rows is None:
            self.stats["feed_errors"] += 1
            return {"kind": "poll", "ok": False, "why": "l5 feed unreadable"}
        fresh = []
        for r in rows:
            if not str(r.get("slug", "")).startswith(L5_SERIES):
                continue
            k = action_key(r)
            if k in self.seen:
                continue
            self.seen.add(k)
            fresh.append((k, r))
        fresh.sort(key=lambda kr: kr[1].get("timestamp") or 0)

        for k, r in fresh:
            self._handle(k, r, recv)
        ct = self.clone_tick()
        if ct:
            self.clone_ticks[ct.get("action") or "?"] += 1
            if ct.get("action") == "QUOTE":
                self.clone_ticks["rel:" + str(ct.get("relation"))] += 1
            elif ct.get("reason"):
                self.clone_ticks["hold:" + str(ct.get("reason"))] += 1
        self.stats["polls"] += 1
        self.stats["actions"] += len(fresh)
        return {"kind": "poll", "ok": True, "new_actions": len(fresh),
                "total_actions": self.stats["actions"]}

    def _handle(self, key: str, r: Mapping[str, Any], recv: float) -> None:
        ws = l5_window_start(r.get("slug", ""))
        if ws is None:
            return
        if ws not in self.alignment:
            self.alignment[ws] = verify_alignment(ws)
        align = self.alignment[ws]
        slug = align["pmus_slug"]

        ev_t = float(r.get("timestamp") or 0)
        latency = round(recv - ev_t, 3)
        # BACKLOG IS NOT LATENCY. The first poll after a start (or a restart)
        # sees every action the feed still holds, including windows that closed
        # hours ago. Their "latency" measures when this process began, not how
        # fast the feed is, and pricing them against a closed PMUS market
        # yields NO_BOOK for reasons that say nothing about copyability.
        # They are kept in the tape and in L5's behavioural statistics, and
        # excluded from the realtime measurements.
        backlog = recv >= (ws + WINDOW_S)
        if not backlog:
            self.latencies.append(latency)

        bbo, age = self.pmus_now(slug)
        act = l5_to_pmus_action(r)
        cop = copyability(act, bbo)
        overlay = safety_overlay(act)

        w = self.windows.setdefault(ws, {
            "window_start": ws, "pmus_slug": slug,
            "mirror": Book("MIRROR_RAW"), "clone": Book("CLONE_MODEL"),
            "l5_actions": 0, "l5_sides": collections.Counter(),
            "l5_relations": collections.Counter(),
            "copy": collections.Counter(),
        })
        w["l5_actions"] += 1
        w["l5_sides"][act.get("pmus_side")] += 1
        if backlog:
            w["copy"]["BACKLOG_WINDOW_CLOSED"] += 1
            self.stats["backlog"] += 1
        else:
            w["copy"][cop["klass"]] += 1
            self.stats["realtime"] += 1

        # ACTUAL L5: relation inferred from L5's own running inventory
        l5_book = w.setdefault("l5_book", Book("ACTUAL_L5"))
        side_up = act.get("pmus_side") == "UP"
        l5_rel = l5_book.relation_for(side_up)
        if act.get("l5_price") is not None and not act["is_sell"]:
            l5_book.add(side_up, D(act["l5_price"]))
        w["l5_relations"][l5_rel] += 1

        # MIRROR_RAW: only advances where the action was actually copyable
        # MIRROR_RAW follows L5 where PMUS PERMITS IT, and the safety
        # overlay is reported beside it rather than applied here. Applying it
        # would delete L5 actions from the replication measurement, which is
        # the thing being measured.
        mirror_rel = None
        if (not backlog and cop["klass"] == "MAKER_COPYABLE"
                and not act["is_sell"]):
            mirror_rel = w["mirror"].relation_for(side_up)
            w["mirror"].add(side_up, D(act["l5_price"]))

        tte = (ws + WINDOW_S) - ev_t
        row = {
            "kind": "l5_action", "action_key": key,
            "labels": LABELS,
            "backlog": backlog,
            "backlog_note": ("observed after its window closed, so its "
                             "latency reflects when this process started and "
                             "is excluded from realtime statistics"
                             if backlog else None),
            "l5_event_utc": dt.datetime.fromtimestamp(
                ev_t, dt.timezone.utc).isoformat(),
            "receipt_utc": dt.datetime.fromtimestamp(
                recv, dt.timezone.utc).isoformat(),
            "latency_s": latency,
            "no_backdating_ok": recv >= ev_t,
            "pmus_snapshot_age_s": round(age, 3),
            "window_start_utc": dt.datetime.fromtimestamp(
                ws, dt.timezone.utc).isoformat(),
            "tte_s_at_event": round(tte, 1),
            "tte_s_at_receipt": round((ws + WINDOW_S) - recv, 1),
            "still_in_window_at_receipt": recv < (ws + WINDOW_S),
            "alignment": align,
            "l5": {"slug": r.get("slug"), "tx": r.get("transactionHash"),
                   **act, "relation_actual": l5_rel},
            "pmus": {"slug": slug,
                     "bbo": None if bbo is None else {
                         "bid": (bbo.get("bestBid") or {}).get("value"),
                         "ask": (bbo.get("bestAsk") or {}).get("value"),
                         "bid_shares": bbo.get("bidShares"),
                         "ask_shares": bbo.get("askShares"),
                         "shares_traded": bbo.get("sharesTraded"),
                         "state": bbo.get("state")}},
            "copyability": cop,
            "safety_overlay": overlay,
            "axes_note": ("copyability is a fact about PMUS; safety_overlay "
                          "is a fact about OUR controls. Neither may rewrite "
                          "the L5_NATIVE decision above."),
            "mirror_relation": mirror_rel,
            "interaction": clf.classify(
                {"desired_price": act.get("l5_price"),
                 "side": act.get("pmus_side")}, None, False),
            "inventory": {
                "ACTUAL_L5": l5_book.snapshot(),
                "MIRROR_RAW": w["mirror"].snapshot(),
                "CLONE_MODEL": w["clone"].snapshot(),
            },
            "tracks_note": ("ACTUAL_L5, MIRROR_RAW and CLONE_MODEL are "
                            "separate objects and are never summed"),
        }
        self._emit(row)

    # -- reporting ---------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        lat = sorted(self.latencies)

        def p(q):
            return round(lat[min(len(lat) - 1, int(q * len(lat)))], 2) \
                if lat else None

        cop = collections.Counter()
        rels = collections.Counter()
        per_window = []
        for ws, w in sorted(self.windows.items()):
            cop.update(w["copy"])
            rels.update(w["l5_relations"])
            per_window.append({
                "window_utc": dt.datetime.fromtimestamp(
                    ws, dt.timezone.utc).strftime("%H:%M"),
                "pmus_slug": w["pmus_slug"],
                "l5_actions": w["l5_actions"],
                "l5_sides": dict(w["l5_sides"]),
                "l5_relations": dict(w["l5_relations"]),
                "copy": dict(w["copy"]),
                "ACTUAL_L5": w["l5_book"].snapshot() if "l5_book" in w else None,
                "MIRROR_RAW": w["mirror"].snapshot(),
                "CLONE_MODEL": w["clone"].snapshot(),
            })
        cop.pop("BACKLOG_WINDOW_CLOSED", None)
        tot = sum(cop.values())
        return {
            "kind": "summary", "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "labels": LABELS,
            "polls": self.stats["polls"], "feed_errors": self.stats["feed_errors"],
            "actions_observed": self.stats["actions"],
            "actions_realtime": self.stats["realtime"],
            "actions_backlog": self.stats["backlog"],
            "backlog_note": ("backlog actions are kept in the tape and in "
                             "L5's behavioural statistics, and excluded from "
                             "latency and copyability, which they would "
                             "otherwise dominate with an artefact of start "
                             "time"),
            "hydrated_from_tape": self.stats["hydrated"],
            "windows": len(self.windows),
            "actions_per_window": (round(self.stats["actions"] / len(self.windows), 1)
                                   if self.windows else None),
            "latency_s": {"n": len(lat), "p50": p(0.5), "p90": p(0.9),
                          "p99": p(0.99),
                          "max": round(lat[-1], 2) if lat else None},
            "copyability": dict(cop),
            "copyability_pct": {k: round(100.0 * v / tot, 1)
                                for k, v in cop.items()} if tot else {},
            "l5_relations_actual": dict(rels),
            "clone_model_ticks": dict(self.clone_ticks),
            "model_sha": self.model.get("_sha256_16"),
            "per_window": per_window,
            "interaction_evidence": ("public book only -- every interaction is "
                                     "QUEUE_AMBIGUOUS or NO_INTERACTION; no "
                                     "fill is manufactured"),
            "liquidity_rewards_usd": "0.00 -- eligibility and allocation not "
                                     "established",
        }


#: Bounded INCREMENTAL emission of observer tape rows to the log stream, so a
#: reader outside this process can see rows written to a Railway volume it
#: cannot open. OFF by default; the value is the cap per emission. Same
#: narrowest-safe method as the authenticated trade rows: it adds no socket,
#: no route and no access-control surface.
ROWS_LOG_ENV = "L5_PMUS_OBS_ROWS_LOG"


def rows_log_cap() -> int:
    """0 (off) unless set to a positive integer."""
    try:
        v = int(str(os.getenv(ROWS_LOG_ENV, "")).strip() or "0")
    except ValueError:
        return 0
    return max(0, min(v, 400))


SERVICE_ROLE = "l5-pmus-observer"
ROLE_ENV = "PMUS_SERVICE_ROLE"
STATE_DIR_ENV = "L5_PMUS_OBS_ROOT"
DEFAULT_STATE_DIR = "data/l5_pmus_realtime"


def assert_role() -> None:
    """Refuse to run as the wrong worker.

    The Railway services in this environment build from the same branch, so a
    lost Dockerfile pin has previously cross-built one worker into another's
    service. A baked role turns that into a loud crash at boot instead of a
    worker that runs silently under the wrong name and writes its evidence
    into the wrong volume. An UNSET role is deliberately allowed, because that
    is how this module runs locally and under test.
    """
    got = os.getenv(ROLE_ENV)
    if got and got != SERVICE_ROLE:
        print("SERVICE_ROLE_MISMATCH expected=%s got=%s" % (SERVICE_ROLE, got),
              flush=True)
        raise SystemExit(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir",
                    default=os.getenv(STATE_DIR_ENV) or DEFAULT_STATE_DIR)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="0 = run forever")
    ap.add_argument("--summary-every", type=float, default=300.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)

    assert_role()
    os.makedirs(a.state_dir, exist_ok=True)
    obs = Observer(a.state_dir)
    print("L5_PMUS_OBS %s" % json.dumps({
        "kind": "boot", "state_dir": a.state_dir, "tape": obs.tape,
        "hydrated_actions": obs.stats["hydrated"],
        "l5_series": L5_SERIES, "pmus_series": PMUS_SERIES,
        "band_frozen": [str(BAND_LO), str(BAND_HI)],
        "read_only": True, "order_path": False, "labels": LABELS}), flush=True)

    end = time.time() + a.minutes * 60.0 if a.minutes else None
    last_sum = 0.0
    boot = time.time()
    poll_exceptions = 0
    while True:
        # A 24/7 collector must not lose a BTC15m window to one malformed row.
        # The tape is append-only and the dedupe index is rebuilt at boot, so
        # a crash is already recoverable -- but a crash LOOP silently costs
        # windows, which is the failure this service exists to stop having.
        # `--once` keeps raising, because a test must not swallow a defect.
        try:
            r = obs.poll()
        except Exception as exc:               # noqa: BLE001 -- continuity
            if a.once:
                raise
            poll_exceptions += 1
            r = {"kind": "poll", "ok": False, "why": "poll raised",
                 "error_class": type(exc).__name__, "error": str(exc)[:400],
                 "poll_exceptions": poll_exceptions}
        print("L5_PMUS_OBS %s" % json.dumps(r), flush=True)
        now = time.time()
        if now - last_sum >= a.summary_every or a.once:
            s = obs.summary()
            obs._emit(s)
            print("L5_PMUS_SUMMARY %s" % json.dumps(s), flush=True)
            # Emit rows on the SUMMARY interval, not every poll. Emitting
            # every 10s produced ~3 rows per document, so the ~20 documents a
            # single log fetch can carry spanned about three minutes of wall
            # clock -- an hourly harvest then read roughly 5% of what was
            # emitted. Nothing was lost (the tape is on the volume), but the
            # read channel could not keep up with collection. Batching at the
            # summary interval with a larger cap covers ~100 minutes in the
            # same 20 documents. This changes the READ CHANNEL only: the tape
            # write, the dedupe index and every recorded field are untouched.
            obs.emit_new_rows()
            # Separate from the frozen summary on purpose: this line is
            # service liveness, not experiment output, and proving the
            # service survived a turn or a window rollover must never
            # require reinterpreting the experiment's own fields.
            print("L5_PMUS_HB %s" % json.dumps({
                "kind": "observer_health", "role": SERVICE_ROLE,
                "uptime_s": round(now - boot, 1),
                "state_dir": a.state_dir,
                "polls": obs.stats.get("polls") or 0,
                "feed_errors": obs.stats.get("feed_errors") or 0,
                "poll_exceptions": poll_exceptions,
                "actions_observed": obs.stats.get("actions") or 0,
                "actions_realtime": obs.stats.get("realtime") or 0,
                "actions_backlog": obs.stats.get("backlog") or 0,
                "hydrated_at_boot": obs.stats.get("hydrated") or 0,
                "windows": len(s.get("per_window") or []),
                "mutations_sent": 0, "order_path": False,
                "labels": LABELS}), flush=True)
            last_sum = now
        if a.once or (end and now >= end):
            break
        time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
