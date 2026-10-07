"""TWO-TOKEN BOOK-STATE SHADOW COLLECTOR. READ-ONLY. NEVER TRADES.

WHY THIS EXISTS
---------------
One artifact is missing and cannot be reconstructed: **both outcome tokens'
book state at the moment L5 turns from EXTEND to COMPLETE**. The only
historical Polymarket L2 on disk (Zenodo 22084977, 2026-08-21..24) overlaps
ZERO canonical windows (`analysis/l5/BOOK_ACQUISITION.md`,
`analysis/l5/DECISIVE_VERDICT.md`), so the state has to be captured FORWARD,
starting now. That is the only purpose of this module.

THE EXACT FAILURE IT REFUSES TO REPEAT
--------------------------------------
Every book tape in this repo records ONE side and DERIVES the other, e.g.
`src/pmus_btc15m_l5_shadow.py`::

    # NO side is the complement of the YES book, as on every binary venue
    "no_bid": (1.0 - ask) ...

On a CLOB with two independently quoted outcome tokens that identity is an
ASSUMPTION, not an observation, and it destroys precisely the question being
asked: whether the OPPOSITE token's quote crossed a candidate complement
price before or after L5's fill. This collector therefore records each
outcome token's own book as its own row, from its own feed, and a market
whose tape carries only one token identity is reported as DEFECTIVE rather
than analysed (`token_coverage`, `defective_markets`).

NO TRADING, NOW OR LATER
------------------------
This process is an OBSERVATION ARTIFACT. It holds no order path, imports no
order-capable module, signs no mutation, and must never be given one. HTTP is
GET only (`ReadOnlyFetcher`); the only websocket frames it can emit are
subscribe/unsubscribe/heartbeat (`guard_outbound`). `boot_gate()` FAILS the
process if any order-capable module is even IMPORTABLE in the image, the same
"cannot outranks will not" rule as `scripts/l5_exec1_boot_preflight.py`.
Credentials are reported BY PRESENCE ONLY; no value is ever read into a row,
a log line or a print.

WORST-CASE REQUEST RATE (SAFETY REQUIREMENT, NOT A NICETY)
----------------------------------------------------------
An unpaced activity/program crawl in this repo once starved a live order
preflight into HTTP 429 and halted a campaign with a leg held. Every poll
here goes through one shared `Pacer`, whose ceiling is a configured
requests-per-minute budget, so the worst case is a CONSTANT that does not
grow with the slate:

    worst case = BOOK_SHADOW_MAX_RPM  (default 240 requests/minute total,
                 across every venue, every token and every feed, including
                 discovery and the L5 activity poll)

and the *scheduled* steady-state rate is::

    markets * tokens_per_market / poll_interval_s * 60      (book)
  + 60 / activity_interval_s                               (L5 activity)
  + 60 / discovery_interval_s * discovery_calls            (discovery)

With the shipped defaults (8 markets, 2 tokens, 1.0 s book interval, 2 s
activity, 120 s discovery) the schedule asks for ~990 rpm, so the pacer is
the binding constraint and the real rate is the 240 rpm ceiling: the pacer
degrades CADENCE, never silently drops a token. Raise `MAX_RPM` only after
checking that no armed probe is running on the same venue key.
HTTP 429 backs the whole process off multiplicatively (`Pacer.penalize`).

SELF-RECOVERING, 24/7
---------------------
Per CLAUDE.md read-only collectors self-recover: discovery failure keeps the
previous slate, a fetch error is written to the tape as an error row and the
loop continues, a websocket drop reconnects with backoff, and nothing but an
explicit `--minutes` budget or SIGINT ends the run. A heartbeat row carries
PER-STREAM row counts so a single stalled stream is visible even while the
others are healthy; silence is never a success state.

PROVENANCE
----------
Append-only JSONL, hourly parts, opened "a" and only ever "a"
(`TapeWriter`). Nothing is rewritten, cleaned or deduplicated in place --
the S3 cohort artifact in this repo was already destroyed once by a process
that treated its raw output as scratch. Derived views are SEPARATE files
computed from the raw tape (`derived/`), never written back over it. Every
row carries: schema version, receive wall clock, receive monotonic clock,
venue, market slug, token/outcome identity, the feed it came from, and the
selector that admitted the market.

Entrypoint: `scripts/l5_book_shadow_run.py` (see
`analysis/l5/book_shadow/RUNBOOK.md`).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

SCHEMA_VERSION = "l5_book_shadow/1"

# ----------------------------------------------------------------- venues
#: L5's own venue. Both outcome tokens are separately quoted here, which is
#: the whole reason this collector exists.
POLY_GAMMA = "https://gamma-api.polymarket.com"
POLY_CLOB = "https://clob.polymarket.com"
POLY_DATA = "https://data-api.polymarket.com"
#: Polymarket US gateway, public and keyless, used for the TENNIS/SOCCER
#: pair-cost sampling lane.
PMUS_GATEWAY = "https://gateway.polymarket.us"

VENUE_POLY = "polymarket"
VENUE_PMUS = "pmus"

#: data-api keys the activity feed on the PROXY WALLET, not the profile
#: handle. Passing the handle returns HTTP 400 {"error":"required query
#: param 'user' not provided"} -- the venue validates the parameter as an
#: address and reports a non-address as ABSENT, which is why the earlier
#: handle default looked like a missing parameter rather than a wrong one.
#: Same convention already recorded in src/l5_5m_coverage/run.py.
L5_ADDRESS_DEFAULT = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
#: Kept for labelling only. Never sent as `user`.
L5_HANDLE = "l5zn1bwom8etsk"


def is_wallet_address(value: str) -> bool:
    """A 0x-prefixed 20-byte hex address, and nothing else.

    The guard exists because the venue's own error for a handle is
    indistinguishable from a missing parameter, so a wrong identity is
    silent at the HTTP layer and must be refused here instead.
    """
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", value or ""))

UA = {"User-Agent": "l5-book-shadow (read-only research collector)"}

# ------------------------------------------------------------- environment
ENV_ROOT = "BOOK_SHADOW_ROOT"
ENV_VENUES = "BOOK_SHADOW_VENUES"
ENV_SELECTORS = "BOOK_SHADOW_SELECTORS"
ENV_SERIES = "BOOK_SHADOW_SERIES"
ENV_POLL_S = "BOOK_SHADOW_POLL_INTERVAL_S"
ENV_ACTIVITY_S = "BOOK_SHADOW_ACTIVITY_INTERVAL_S"
ENV_DISCOVERY_S = "BOOK_SHADOW_DISCOVERY_INTERVAL_S"
ENV_HEARTBEAT_S = "BOOK_SHADOW_HEARTBEAT_S"
ENV_MAX_MARKETS = "BOOK_SHADOW_MAX_MARKETS"
ENV_MAX_RPM = "BOOK_SHADOW_MAX_RPM"
ENV_L5_ADDRESS = "BOOK_SHADOW_L5_ADDRESS"
ENV_CA_BUNDLE = "PMUS_CA_BUNDLE"

#: Reported by PRESENCE ONLY, never read into a row or a log line. They are
#: optional: every feed this collector uses is public and keyless.
CREDENTIAL_ENVS = ("PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY",
                   "POLY_API_KEY", "POLY_API_SECRET", "POLY_PASSPHRASE",
                   "PMUS_PRIVATE_KEY", "POLY_PRIVATE_KEY")

#: Order-capable modules. If ANY of these is importable in this image the
#: boot gate fails and the process exits non-zero before a single request.
FORBIDDEN_MODULES = (
    "src.l5_pmus_transport",
    "src.pmus_q1_executor",
    "src.pmus_micro_executor",
    "analysis.l5.live_exec",
    "analysis.l5.live_exec.spec",
    "analysis.l5.live_exec2",
    "analysis.l5.live_micro",
)

DEFAULT_POLL_S = 1.0
DEFAULT_ACTIVITY_S = 2.0
DEFAULT_DISCOVERY_S = 120.0
DEFAULT_HEARTBEAT_S = 60.0
DEFAULT_MAX_MARKETS = 8
DEFAULT_MAX_RPM = 240.0

SELECTOR_L5_ACTIVE = "l5_active"
SELECTOR_SERIES = "series"
SELECTORS = (SELECTOR_L5_ACTIVE, SELECTOR_SERIES)

#: Requirement 5: the second purpose. Named series/categories, sampled at
#: scale, so the attractive tennis/soccer pair costs can be tested forward.
DEFAULT_SERIES = ("tennis", "soccer")


# ==================================================== boot gate (blocking)

def module_importable(name: str) -> bool:
    """True when `name` could be imported in THIS image.

    `find_spec` resolves the module without executing it, except that it must
    import parent packages; a parent that raises is reported as not
    importable, which is the conservative answer for a gate whose job is to
    prove absence.
    """
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:                                          # noqa: BLE001
        return False


def credential_presence() -> Dict[str, bool]:
    """PRESENCE ONLY. Values are never read, printed or logged."""
    return {name: bool(os.environ.get(name)) for name in CREDENTIAL_ENVS}


def boot_gate(forbidden: Iterable[str] = FORBIDDEN_MODULES,
              l5_address: str = L5_ADDRESS_DEFAULT) -> Dict[str, Any]:
    """Blocking boot gate, same rule as `scripts/l5_exec1_boot_preflight.py`:
    STRUCTURAL REFUSAL, not a flag. Returns a result dict; `ok` False means
    the process must exit non-zero without issuing a request.
    """
    importable = sorted(n for n in forbidden if module_importable(n))
    gates = {
        "mutation_incapable": {
            "pass": not importable,
            "detail": {"importable_order_modules": importable},
            "blocking": True,
        },
        "no_credential_values_logged": {
            "pass": True,
            "detail": {"reported": "presence_only",
                       "present": credential_presence()},
            "blocking": True,
        },
        "activity_identity_is_wallet_address": {
            "pass": is_wallet_address(l5_address),
            "detail": {"value_shape": ("0x40hex" if
                                       is_wallet_address(l5_address)
                                       else "NOT_AN_ADDRESS"),
                       "why": "data-api reports a non-address `user` as a "
                              "MISSING parameter (HTTP 400), so a wrong "
                              "identity is silent at the HTTP layer"},
            "blocking": True,
        },
        "role_is_observation_only": {
            "pass": True,
            "detail": {"orders_submitted": 0, "venue_mutations": 0,
                       "role": "BOOK_SHADOW_COLLECTOR"},
            "blocking": True,
        },
    }
    failures = [k for k, v in gates.items() if v["blocking"] and not v["pass"]]
    return {"role": "BOOK_SHADOW_COLLECTOR", "schema": SCHEMA_VERSION,
            "gates": gates, "blocking_failures": failures,
            "ok": not failures}


# ======================================================== clocks and rows

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(*, venue: str, market: str, token: Optional[str],
             outcome: Optional[str], feed: str, selector: str,
             kind: str) -> Dict[str, Any]:
    """The provenance envelope every row carries.

    `recv_monotonic_ns` is what makes TIME ORDER inside one process
    comparable: wall clock can step, the monotonic clock cannot.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "recv_utc": now_utc_iso(),
        "recv_unix": time.time(),
        "recv_monotonic_ns": time.monotonic_ns(),
        "venue": venue,
        "market": market,
        "token": token,
        "outcome": outcome,
        "feed": feed,
        "selector": selector,
        "labels": "BOOK/ACTIVITY OBSERVATION ONLY -- never a fill, "
                  "never realized, never an order",
    }


REQUIRED_PROVENANCE = ("schema_version", "recv_utc", "recv_unix",
                       "recv_monotonic_ns", "venue", "market", "token",
                       "outcome", "feed", "selector")


def has_provenance(row: Mapping[str, Any]) -> bool:
    return all(k in row for k in REQUIRED_PROVENANCE)


# ============================================================ append-only

class TapeWriter:
    """Append-only hourly JSONL parts. Opens "a" and only ever "a".

    There is no update, no rewrite, no truncate and no in-place clean. A
    derived view goes to `derived/` under the same root, computed FROM the
    tape; the raw parts are immutable once written.
    """

    MODE = "a"

    def __init__(self, root: str | Path, stream: str = "tape") -> None:
        self.root = Path(root)
        self.stream = stream
        self.raw_dir = self.root / "raw"
        self.derived_dir = self.root / "derived"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.derived_dir.mkdir(parents=True, exist_ok=True)
        self.rows_written = 0

    def part_path(self, when: Optional[datetime] = None) -> Path:
        when = when or datetime.now(timezone.utc)
        return self.raw_dir / ("%s_%s.jsonl" % (
            self.stream, when.strftime("%Y%m%dT%HZ")))

    def write(self, row: Mapping[str, Any]) -> Path:
        path = self.part_path()
        line = json.dumps(row, separators=(",", ":"), sort_keys=True)
        with open(path, self.MODE, encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        self.rows_written += 1
        return path

    def read_all(self) -> List[dict]:
        """Raw tape, chronological by part then by line. Reads only."""
        out: List[dict] = []
        for path in sorted(self.raw_dir.glob("%s_*.jsonl" % self.stream)):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def write_derived(self, name: str, obj: Any) -> Path:
        path = self.derived_dir / name
        path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        return path


# ================================================================= pacing

class Pacer:
    """One shared request budget for the whole process.

    The ceiling is requests-per-minute; `wait()` blocks until the next
    request is inside budget, so an added market slows CADENCE instead of
    raising the venue rate. `penalize()` is called on HTTP 429 and backs the
    whole process off multiplicatively, which is why one 429 can never turn
    into the starvation incident this pacer exists to prevent.
    """

    def __init__(self, max_rpm: float = DEFAULT_MAX_RPM,
                 min_interval_s: float = 0.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_rpm = float(max_rpm)
        self.base_interval_s = max(60.0 / self.max_rpm if self.max_rpm > 0
                                   else 0.0, float(min_interval_s))
        self.interval_s = self.base_interval_s
        self._sleep = sleep
        self._clock = clock
        self._last = None               # type: Optional[float]
        self.waits = 0
        self.penalties = 0

    def worst_case_rpm(self) -> float:
        return 60.0 / self.interval_s if self.interval_s > 0 else float("inf")

    def wait(self) -> float:
        """Block until the next request is allowed. Returns seconds slept."""
        now = self._clock()
        if self._last is None:
            self._last = now
            return 0.0
        due = self._last + self.interval_s
        slept = 0.0
        if due > now:
            slept = due - now
            self._sleep(slept)
            self.waits += 1
        self._last = max(due, now)
        return slept

    def penalize(self, factor: float = 2.0, max_interval_s: float = 30.0
                 ) -> float:
        self.penalties += 1
        self.interval_s = min(max(self.interval_s, 0.001) * factor,
                              max_interval_s)
        return self.interval_s

    def relax(self, factor: float = 0.9) -> float:
        """Drift back toward the configured interval after a clean period."""
        self.interval_s = max(self.base_interval_s,
                              self.interval_s * factor)
        return self.interval_s


# =============================================================== transport

class ReadOnlyFetcher:
    """GET only. Any other method raises. There is no POST path in this
    module, and this class is the only HTTP object in it."""

    def __init__(self, pacer: Pacer,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.pacer = pacer
        self._opener = opener or urllib.request.urlopen
        self.requests = 0
        self.errors = 0
        self.rate_limited = 0
        #: First-request monotonic stamp, so the heartbeat can report the
        #: rate ACTUALLY ISSUED rather than the ceiling the pacer was
        #: configured with. A cap is an intention; this is a measurement.
        self._first_request_mono: Optional[float] = None

    def observed_rpm(self) -> Optional[float]:
        """Requests actually issued per minute since the first request."""
        if self._first_request_mono is None or self.requests < 2:
            return None
        elapsed = time.monotonic() - self._first_request_mono
        if elapsed <= 0:
            return None
        return 60.0 * self.requests / elapsed

    @staticmethod
    def _ctx() -> ssl.SSLContext:
        bundle = os.environ.get(ENV_CA_BUNDLE)
        if bundle and os.path.exists(bundle):
            try:
                return ssl.create_default_context(cafile=bundle)
            except Exception:                                  # noqa: BLE001
                pass
        return ssl.create_default_context()

    def get(self, url: str, params: Optional[Mapping[str, Any]] = None,
            timeout: float = 15.0) -> Any:
        if params:
            url = "%s?%s" % (url, urllib.parse.urlencode(params))
        self.pacer.wait()
        req = urllib.request.Request(url, headers=dict(UA), method="GET")
        if self._first_request_mono is None:
            self._first_request_mono = time.monotonic()
        self.requests += 1
        try:
            with self._opener(req, timeout=timeout,
                              context=self._ctx()) as fh:
                return json.loads(fh.read().decode())
        except urllib.error.HTTPError as exc:
            self.errors += 1
            if exc.code == 429:
                self.rate_limited += 1
                self.pacer.penalize()
            raise
        except Exception:                                      # noqa: BLE001
            self.errors += 1
            raise


#: Response headers safe to record. Allowlist, not denylist: a header the
#: venue starts returning tomorrow is dropped until it is listed, which is
#: the direction that cannot leak.
ACTIVITY_UNAVAILABLE_LABEL = "BOOK_ONLY_ACTIVITY_UNAVAILABLE"

SAFE_RESPONSE_HEADERS = ("content-type", "content-length", "date",
                         "x-request-id", "retry-after", "server")


def describe_http_failure(exc: BaseException) -> dict:
    """Status, endpoint, sanitized body and exception class for a failed
    GET. There are no credentials in this process, so nothing here can
    carry one; the body is truncated and the headers allowlisted anyway."""
    out = {"error_class": type(exc).__name__, "status": None,
           "reason": None, "endpoint": None, "body": None,
           "headers_safe": {}}
    url = getattr(exc, "url", None) or getattr(exc, "filename", None)
    if isinstance(url, str):
        # path + QUERY PARAM NAMES only; values are dropped so a future
        # parameterisation cannot write anything sensitive into the tape.
        try:
            parts = urllib.parse.urlsplit(url)
            names = sorted({k for k, _ in urllib.parse.parse_qsl(parts.query)})
            out["endpoint"] = "%s://%s%s?%s" % (
                parts.scheme, parts.netloc, parts.path,
                "&".join("%s=<v>" % n for n in names))
        except ValueError:
            out["endpoint"] = "<unparseable>"
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        out["status"] = code
    reason = getattr(exc, "reason", None)
    if reason is not None:
        out["reason"] = str(reason)[:200]
    read = getattr(exc, "read", None)
    if callable(read):
        try:
            out["body"] = read()[:600].decode("utf8", "replace")
        except (OSError, ValueError):
            out["body"] = "<unreadable>"
    hdrs = getattr(exc, "headers", None)
    if hdrs is not None:
        try:
            out["headers_safe"] = {k.lower(): str(v)[:200]
                                   for k, v in hdrs.items()
                                   if k.lower() in SAFE_RESPONSE_HEADERS}
        except AttributeError:
            pass
    return out


OUTBOUND_FRAME_KEYS = frozenset({"subscribe", "unsubscribe", "heartbeat",
                                 "type", "assets_ids", "auth", "action"})
OUTBOUND_ACTIONS = frozenset({"subscribe", "unsubscribe", "heartbeat", "ping",
                              "market"})


def guard_outbound(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    """The only websocket frames this process can emit.

    Mirrors `src.pmus_ws_evidence_recorder.guard_outbound`: an unexpected key
    or an action that is not subscribe/unsubscribe/heartbeat raises, so an
    order frame cannot be sent even by accident.
    """
    bad = set(frame) - OUTBOUND_FRAME_KEYS
    if bad:
        raise PermissionError("outbound frame keys not permitted: %s"
                              % sorted(bad))
    for key in ("type", "action"):
        val = frame.get(key)
        if val is not None and str(val).lower() not in OUTBOUND_ACTIONS:
            raise PermissionError("outbound frame action not permitted: %r"
                                  % (val,))
    return frame


# =============================================================== selection

class Selection:
    """A market admitted to the tape, WITH THE REASON IT WAS ADMITTED.

    `selector` and `why` land on every row for the market, so a tape always
    says why a market was recorded (requirement 5).
    """

    def __init__(self, venue: str, market: str, tokens: List[dict],
                 selector: str, why: str,
                 complement_derived: bool = False) -> None:
        self.venue = venue
        self.market = market
        #: [{"token": <id or slug>, "outcome": "<label>"}, ...] -- the
        #: INDEPENDENT books to record. Two entries, or the market is
        #: recorded as single-token and reported DEFECTIVE.
        self.tokens = tokens
        self.selector = selector
        self.why = why
        #: True only when the venue does not quote the second outcome as its
        #: own book. Recorded explicitly so no reader can mistake a derived
        #: complement for an observed one.
        self.complement_derived = complement_derived

    def as_row(self) -> Dict[str, Any]:
        return {"venue": self.venue, "market": self.market,
                "tokens": self.tokens, "selector": self.selector,
                "why": self.why,
                "complement_derived": self.complement_derived,
                "token_count": len(self.tokens)}

    def __repr__(self) -> str:                                 # pragma: no cover
        return "Selection(%s/%s via %s)" % (self.venue, self.market,
                                            self.selector)


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def poly_tokens(market: Mapping[str, Any]) -> List[dict]:
    """Both outcome tokens of a Polymarket market, from `clobTokenIds`.

    Order is the venue's own outcome order and the labels come from the
    market's `outcomes`; neither is invented. Fewer than two ids yields what
    was found -- the caller records the shortfall rather than padding it.
    """
    ids = [str(t) for t in _json_list(market.get("clobTokenIds")) if t]
    labels = [str(o) for o in _json_list(market.get("outcomes"))]
    out = []
    for i, tid in enumerate(ids[:2]):
        label = labels[i] if i < len(labels) else ("outcome_%d" % i)
        out.append({"token": tid, "outcome": label, "outcome_index": i})
    return out


class PolymarketVenue:
    """L5's venue. Both outcome tokens are independently quoted CLOB books
    (`clob.polymarket.com/book?token_id=`), which is what makes the
    EXTEND->COMPLETE question answerable at all."""

    name = VENUE_POLY
    book_feed = "clob_rest_book"
    activity_feed = "data_api_activity"

    def __init__(self, fetcher: ReadOnlyFetcher,
                 l5_address: str = L5_ADDRESS_DEFAULT) -> None:
        self.http = fetcher
        self.l5_address = l5_address

    # -- discovery ------------------------------------------------------
    def l5_activity(self, limit: int = 100) -> List[dict]:
        rows = self.http.get("%s/activity" % POLY_DATA,
                             {"user": self.l5_address, "limit": limit,
                              "type": "TRADE"})
        return [r for r in rows if isinstance(r, Mapping)] \
            if isinstance(rows, list) else []

    def market_by_slug(self, slug: str) -> Optional[dict]:
        rows = self.http.get("%s/markets" % POLY_GAMMA, {"slug": slug})
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            return dict(rows[0])
        return None

    def markets_by_tag(self, tag: str, limit: int = 100) -> List[dict]:
        rows = self.http.get("%s/markets" % POLY_GAMMA,
                             {"tag_slug": tag, "closed": "false",
                              "active": "true", "limit": limit})
        return [dict(r) for r in rows if isinstance(r, Mapping)] \
            if isinstance(rows, list) else []

    # -- book -----------------------------------------------------------
    def book(self, token: str) -> dict:
        """One token's OWN ladder. Nothing here is derived from the other
        token: that derivation is the defect this collector exists to end."""
        bk = self.http.get("%s/book" % POLY_CLOB, {"token_id": token})
        return bk if isinstance(bk, Mapping) else {}


class PmusVenue:
    """Polymarket US, public and keyless. Used for the TENNIS/SOCCER
    pair-cost lane.

    On PMUS an event carries its outcome markets as separate slugs, each with
    its own `/book`; those are the two independent books. When an event
    exposes only ONE market the second outcome is NOT quoted separately, and
    the selection is marked `complement_derived=True` so the tape says so
    instead of implying an observation it does not have.
    """

    name = VENUE_PMUS
    book_feed = "pmus_gateway_book"

    def __init__(self, fetcher: ReadOnlyFetcher) -> None:
        self.http = fetcher

    def live_events(self, category: str, limit: int = 200) -> List[dict]:
        rows = self.http.get("%s/v1/events" % PMUS_GATEWAY,
                             {"live": "true", "active": "true",
                              "closed": "false", "categories": category,
                              "limit": limit})
        if isinstance(rows, Mapping):
            rows = rows.get("events") or rows.get("data") or []
        return [dict(r) for r in rows if isinstance(r, Mapping)] \
            if isinstance(rows, list) else []

    def book(self, slug: str) -> dict:
        bk = self.http.get("%s/v1/markets/%s/book" % (PMUS_GATEWAY, slug))
        if isinstance(bk, Mapping):
            return dict(bk.get("marketData") or bk)
        return {}


def select_l5_active(venue: PolymarketVenue, limit: int = 100,
                     max_markets: int = DEFAULT_MAX_MARKETS
                     ) -> tuple[List[Selection], List[dict]]:
    """Markets L5 is actively trading AND that we can identify in realtime.

    Identification is from L5's own public activity rows: the market slug
    (and `conditionId`) they carry resolve to the market object whose
    `clobTokenIds` name BOTH outcome tokens. A row we cannot resolve is
    reported, never guessed.
    """
    actions = venue.l5_activity(limit=limit)
    seen: Dict[str, dict] = {}
    for row in actions:
        slug = str(row.get("slug") or row.get("eventSlug") or "")
        if not slug or slug in seen:
            continue
        seen[slug] = row
    out: List[Selection] = []
    for slug, row in list(seen.items())[:max_markets]:
        market = venue.market_by_slug(slug)
        if not market:
            continue
        tokens = poly_tokens(market)
        ts = row.get("timestamp")
        out.append(Selection(
            VENUE_POLY, slug, tokens, SELECTOR_L5_ACTIVE,
            "l5 public activity row at ts=%s (condition=%s)"
            % (ts, row.get("conditionId")),
        ))
    return out, actions


def select_series(poly: Optional[PolymarketVenue], pmus: Optional[PmusVenue],
                  series: Iterable[str],
                  max_markets: int = DEFAULT_MAX_MARKETS) -> List[Selection]:
    """Requirement 5: a named list of series/categories -- TENNIS and SOCCER
    by default -- sampled at scale on whichever venues are enabled."""
    out: List[Selection] = []
    for name in series:
        if pmus is not None:
            for event in pmus.live_events(name):
                markets = [m for m in (event.get("markets") or [])
                           if isinstance(m, Mapping) and m.get("slug")]
                tokens = [{"token": str(m["slug"]),
                           "outcome": str(m.get("outcome")
                                          or m.get("title")
                                          or "market_%d" % i),
                           "outcome_index": i}
                          for i, m in enumerate(markets[:2])]
                if not tokens:
                    continue
                out.append(Selection(
                    VENUE_PMUS, str(event.get("slug") or tokens[0]["token"]),
                    tokens, SELECTOR_SERIES,
                    "pmus live event in configured category %r" % name,
                    complement_derived=len(tokens) < 2))
        if poly is not None:
            for market in poly.markets_by_tag(name):
                slug = str(market.get("slug") or "")
                if not slug:
                    continue
                tokens = poly_tokens(market)
                out.append(Selection(
                    VENUE_POLY, slug, tokens, SELECTOR_SERIES,
                    "polymarket tag_slug=%r active market" % name))
    return out[:max_markets * max(1, len(list(series)) or 1)]


# ======================================================== both-token audit

def token_coverage(rows: Iterable[Mapping[str, Any]]) -> Dict[str, dict]:
    """Per (venue, market): which token identities actually reached the tape.

    This is the audit that makes the artifact trustworthy. A market with one
    token identity is DEFECTIVE for the EXTEND->COMPLETE question, no matter
    how many rows it has.
    """
    acc: Dict[str, dict] = {}
    for row in rows:
        if row.get("kind") not in ("book", "book_error"):
            continue
        key = "%s/%s" % (row.get("venue"), row.get("market"))
        entry = acc.setdefault(key, {"venue": row.get("venue"),
                                     "market": row.get("market"),
                                     "tokens": {}, "book_rows": 0})
        token = row.get("token")
        if token is None:
            continue
        entry.setdefault("tokens_attempted", {})
        entry["tokens_attempted"][str(token)] = \
            entry["tokens_attempted"].get(str(token), 0) + 1
        if row.get("kind") == "book":
            # ONLY a successfully recorded book counts toward coverage. An
            # error row proves we tried, not that we have the state, and
            # letting it count would turn two failures into "both tokens".
            entry["tokens"][str(token)] = entry["tokens"].get(str(token), 0) + 1
            entry["book_rows"] += 1
    for entry in acc.values():
        entry.setdefault("tokens_attempted", {})
        entry["token_count"] = len(entry["tokens"])
        entry["both_tokens"] = entry["token_count"] >= 2
    return acc


def activity_join_proof(rows: Iterable[Mapping[str, Any]],
                        limit: int = 5) -> Dict[str, Any]:
    """Can a real L5 activity item be joined to a book we actually recorded?

    READ-ONLY over the tape. The join key is the venue's own token id, and
    the market slug is reported alongside as corroboration -- it is never
    the key, because a slug is a label and a token id is an identity.

    This answers the live-proof requirement directly: an activity row whose
    token appears in a book row we recorded is a joined observation; one
    whose token we never polled is honestly reported as unjoined rather
    than silently dropped.
    """
    book_tokens: Dict[str, str] = {}
    for r in rows:
        if r.get("kind") == "book" or r.get("feed") == "clob_rest_book":
            tok = r.get("token")
            if isinstance(tok, str) and tok:
                book_tokens[tok] = str(r.get("market") or "")
    joined: List[dict] = []
    unjoined = 0
    total = 0
    for r in rows:
        if r.get("kind") != "l5_activity":
            continue
        total += 1
        tok = r.get("token")
        if isinstance(tok, str) and tok in book_tokens:
            if len(joined) < limit:
                joined.append({
                    "activity_token": tok,
                    "activity_market": r.get("market"),
                    "book_market": book_tokens[tok],
                    "activity_outcome": r.get("outcome"),
                    "venue_event_time": r.get("venue_event_time"),
                    "activity_recv_utc": r.get("recv_utc"),
                    "market_slugs_agree": (str(r.get("market") or "")
                                           == book_tokens[tok]),
                })
        else:
            unjoined += 1
    return {
        "activity_rows": total,
        "joined_examples": joined,
        "joined_any": bool(joined),
        "activity_rows_without_a_recorded_book": unjoined,
        "join_key": "venue token id (asset); slug is corroboration only",
        "MEANING": "An unjoined activity row means we did not poll that "
                   "token's book, NOT that the book was unchanged.",
    }


def defective_markets(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    """Markets whose tape cannot answer the question: fewer than two token
    identities recorded."""
    return sorted(k for k, v in token_coverage(rows).items()
                  if not v["both_tokens"])


def verify_both_tokens(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    cov = token_coverage(rows)
    bad = sorted(k for k, v in cov.items() if not v["both_tokens"])
    return {"markets": len(cov), "both_tokens_ok": not bad,
            "defective_markets": bad, "coverage": cov}


# =================================================================== loop

class BookShadowCollector:
    """The collector. One process, one pacer, one append-only tape."""

    def __init__(self, root: str | Path, *,
                 venues: Iterable[str] = (VENUE_POLY,),
                 selectors: Iterable[str] = (SELECTOR_L5_ACTIVE,),
                 series: Iterable[str] = DEFAULT_SERIES,
                 poll_interval_s: float = DEFAULT_POLL_S,
                 activity_interval_s: float = DEFAULT_ACTIVITY_S,
                 discovery_interval_s: float = DEFAULT_DISCOVERY_S,
                 heartbeat_s: float = DEFAULT_HEARTBEAT_S,
                 max_markets: int = DEFAULT_MAX_MARKETS,
                 max_rpm: float = DEFAULT_MAX_RPM,
                 l5_address: str = L5_ADDRESS_DEFAULT,
                 fetcher: Optional[ReadOnlyFetcher] = None,
                 writer: Optional[TapeWriter] = None,
                 log: Callable[[str], None] = print) -> None:
        self.pacer = Pacer(max_rpm=max_rpm)
        self.http = fetcher or ReadOnlyFetcher(self.pacer)
        self.tape = writer or TapeWriter(root)
        self.venues = tuple(venues)
        self.selectors = tuple(selectors)
        self.series = tuple(series)
        self.poll_interval_s = float(poll_interval_s)
        self.activity_interval_s = float(activity_interval_s)
        self.discovery_interval_s = float(discovery_interval_s)
        self.heartbeat_s = float(heartbeat_s)
        self.max_markets = int(max_markets)
        self.log = log
        self.poly = (PolymarketVenue(self.http, l5_address)
                     if VENUE_POLY in self.venues else None)
        self.pmus = PmusVenue(self.http) if VENUE_PMUS in self.venues else None
        self.selections: List[Selection] = []
        #: PER-STREAM counters, so a stalled stream is visible while the
        #: others are healthy. Requirement 4.
        self.stream_rows: Dict[str, int] = {}
        self.seen_activity: set = set()
        self.cycles = 0
        self.started = time.time()
        #: BOOK_ONLY_ACTIVITY_UNAVAILABLE bookkeeping. A gap in L5 activity
        #: must never read downstream as "L5 did nothing"; the window is
        #: written ON THE TAPE with explicit start and end.
        self._activity_down_since: Optional[float] = None
        self._activity_failures = 0

    def _emit_activity_window(self, edge: str) -> None:
        """Mark the BOOK_ONLY period explicitly, at both edges."""
        row = envelope(venue=VENUE_POLY, market="-", token=None,
                       outcome=None, feed="supervisor",
                       selector=",".join(self.selectors),
                       kind="activity_availability")
        row["state"] = ACTIVITY_UNAVAILABLE_LABEL if edge == "OPEN" \
            else "ACTIVITY_RECOVERED"
        row["edge"] = edge
        row["since_utc"] = (
            None if self._activity_down_since is None
            else datetime.fromtimestamp(
                self._activity_down_since,
                tz=timezone.utc).isoformat())
        row["since_unix"] = self._activity_down_since
        row["consecutive_failures"] = self._activity_failures
        row["MEANING"] = (
            "between OPEN and CLOSE this tape carries BOOK rows only. "
            "Missing L5 activity in that interval is UNMEASURED, NOT a "
            "zero-fill observation, and must not be analysed as one.")
        try:
            self.emit(row)
        except Exception:                                      # noqa: BLE001
            pass

    def resume_activity_window(self) -> Optional[float]:
        """Carry an OPEN availability window across a process restart.

        READ-ONLY over the tape. Without this the window is per-process:
        a restart forgets that activity was down, so the CLOSE edge is
        never written and the interval has a start with no end -- which is
        exactly the silence that must never be mistaken for zero fills.
        The earliest OPEN since the last CLOSE is the true start, so a
        chain of restarts still yields ONE interval, not several.
        """
        since: Optional[float] = None
        for row in self.tape.read_all():
            if row.get("kind") != "activity_availability":
                continue
            if row.get("edge") == "CLOSE":
                since = None
            elif row.get("edge") == "OPEN" and since is None:
                v = row.get("since_unix")
                if isinstance(v, (int, float)):
                    since = float(v)
        if since is not None and self._activity_down_since is None:
            self._activity_down_since = since
            self._activity_failures = max(self._activity_failures, 1)
        return self._activity_down_since

    def emit_boot_inventory(self) -> Dict[str, Any]:
        """READ-ONLY inventory of what is already on the volume, logged at
        boot. Proves pre-restart parts coexist with post-restart ones
        without mutating the tape."""
        inv: Dict[str, Any] = {"root": str(self.tape.root)}
        try:
            raw = sorted(p.name for p in (self.tape.root / "raw").glob("*.jsonl"))
            inv["raw_parts_present_at_boot"] = raw
            inv["raw_parts_count"] = len(raw)
            inv["raw_bytes_at_boot"] = sum(
                (self.tape.root / "raw" / n).stat().st_size for n in raw)
        except OSError as exc:
            inv["raw_error"] = type(exc).__name__
        try:
            cov = self.tape.root / "derived" / "token_coverage.json"
            inv["token_coverage_present"] = cov.exists()
            if cov.exists():
                d = json.loads(cov.read_text() or "{}")
                inv["both_tokens_ok"] = d.get("both_tokens_ok")
                inv["defective_markets"] = d.get("defective_markets")
        except (OSError, ValueError) as exc:
            inv["token_coverage_error"] = type(exc).__name__
        self.log("BOOT INVENTORY " + json.dumps(inv, sort_keys=True,
                                                default=str))
        return inv

    # -- emission -------------------------------------------------------
    def emit(self, row: Dict[str, Any]) -> None:
        self.tape.write(row)
        feed = str(row.get("feed") or "unknown")
        self.stream_rows[feed] = self.stream_rows.get(feed, 0) + 1

    def scheduled_rpm(self) -> float:
        """The rate the SCHEDULE asks for, before the pacer caps it."""
        tokens = sum(len(s.tokens) for s in self.selections)
        rate = 0.0
        if self.poll_interval_s > 0:
            rate += tokens * 60.0 / self.poll_interval_s
        if self.activity_interval_s > 0 and self.poly is not None:
            rate += 60.0 / self.activity_interval_s
        if self.discovery_interval_s > 0:
            rate += 60.0 / self.discovery_interval_s
        return rate

    # -- discovery ------------------------------------------------------
    def discover(self) -> List[Selection]:
        """Re-resolve the slate. A failure KEEPS THE PREVIOUS SLATE: a
        collector that goes blind because discovery blipped is the failure
        mode requirement 4 forbids."""
        found: List[Selection] = []
        errors: List[str] = []
        if SELECTOR_L5_ACTIVE in self.selectors and self.poly is not None:
            try:
                sels, actions = select_l5_active(
                    self.poly, max_markets=self.max_markets)
                found.extend(sels)
                self._emit_activity(actions)
            except Exception as exc:                           # noqa: BLE001
                errors.append("l5_active:%s" % type(exc).__name__)
        if SELECTOR_SERIES in self.selectors:
            try:
                found.extend(select_series(self.poly, self.pmus, self.series,
                                           max_markets=self.max_markets))
            except Exception as exc:                           # noqa: BLE001
                errors.append("series:%s" % type(exc).__name__)
        if found:
            self.selections = found
        row = envelope(venue=",".join(self.venues), market="-", token=None,
                       outcome=None, feed="discovery",
                       selector=",".join(self.selectors), kind="discovery")
        row.update({"selections": [s.as_row() for s in self.selections],
                    "errors": errors,
                    "kept_previous_slate": bool(errors and not found),
                    "series_configured": list(self.series),
                    "scheduled_rpm": self.scheduled_rpm(),
                    "pacer_worst_case_rpm": self.pacer.worst_case_rpm()})
        self.emit(row)
        return self.selections

    def _emit_activity(self, actions: Iterable[Mapping[str, Any]]) -> None:
        """L5's public activity for the same markets, on the same tape and
        the same receive clock as the books -- which is the only way the two
        can be ordered against each other at all."""
        for action in actions:
            key = str(action.get("transactionHash") or "") + "|" + \
                str(action.get("timestamp") or "") + "|" + \
                str(action.get("asset") or "") + "|" + \
                str(action.get("size") or "")
            if key in self.seen_activity:
                continue
            self.seen_activity.add(key)
            row = envelope(venue=VENUE_POLY,
                           market=str(action.get("slug")
                                      or action.get("eventSlug") or ""),
                           token=(str(action["asset"])
                                  if action.get("asset") else None),
                           outcome=(str(action.get("outcome"))
                                    if action.get("outcome") is not None
                                    else None),
                           feed=PolymarketVenue.activity_feed,
                           selector=SELECTOR_L5_ACTIVE, kind="l5_activity")
            row["raw"] = dict(action)
            #: the feed's own event time, in ITS granularity -- see the
            #: sub-second honesty note in DESIGN.md. Never reconciled with
            #: recv_* and never used to backdate a book row.
            row["venue_event_time"] = action.get("timestamp")
            self.emit(row)

    # -- books ----------------------------------------------------------
    def poll_books(self) -> int:
        """One sweep: EVERY token of EVERY selected market.

        The sweep is per market and emits both tokens back to back, so the
        two books in a pair are as close in receive time as the pacer allows
        and their `recv_monotonic_ns` are directly comparable.
        """
        written = 0
        for sel in self.selections:
            for tok in sel.tokens:
                row = envelope(venue=sel.venue, market=sel.market,
                               token=str(tok.get("token")),
                               outcome=str(tok.get("outcome")),
                               feed=(PolymarketVenue.book_feed
                                     if sel.venue == VENUE_POLY
                                     else PmusVenue.book_feed),
                               selector=sel.selector, kind="book")
                row["selector_why"] = sel.why
                row["outcome_index"] = tok.get("outcome_index")
                row["complement_derived"] = sel.complement_derived
                row["sibling_tokens"] = [str(t.get("token"))
                                         for t in sel.tokens]
                try:
                    if sel.venue == VENUE_POLY and self.poly is not None:
                        book = self.poly.book(str(tok["token"]))
                    elif sel.venue == VENUE_PMUS and self.pmus is not None:
                        book = self.pmus.book(str(tok["token"]))
                    else:
                        raise RuntimeError("venue not enabled: %s" % sel.venue)
                    row["book"] = book
                except Exception as exc:                       # noqa: BLE001
                    row["kind"] = "book_error"
                    row["error"] = "%s:%s" % (type(exc).__name__,
                                              str(exc)[:160])
                self.emit(row)
                written += 1
        return written

    # -- heartbeat ------------------------------------------------------
    def heartbeat(self) -> Dict[str, Any]:
        row = envelope(venue=",".join(self.venues), market="-", token=None,
                       outcome=None, feed="heartbeat",
                       selector=",".join(self.selectors), kind="heartbeat")
        row.update({
            "uptime_s": round(time.time() - self.started, 1),
            "cycles": self.cycles,
            "rows_written_total": self.tape.rows_written,
            "rows_per_stream": dict(self.stream_rows),
            "markets": len(self.selections),
            "markets_detail": [
                {"venue": s.venue, "market": s.market,
                 "selector": s.selector, "tokens": len(s.tokens),
                 "token_ids": [str(t.get("token")) for t in s.tokens],
                 "complement_derived": s.complement_derived}
                for s in self.selections],
            "tokens_tracked": sum(len(s.tokens) for s in self.selections),
            "single_token_markets": [s.market for s in self.selections
                                     if len(s.tokens) < 2],
            "http_requests": self.http.requests,
            "http_errors": self.http.errors,
            "http_429": self.http.rate_limited,
            "pacer_interval_s": round(self.pacer.interval_s, 4),
            "pacer_worst_case_rpm": round(self.pacer.worst_case_rpm(), 1),
            "scheduled_rpm": round(self.scheduled_rpm(), 1),
            "observed_rpm": (None if self.http.observed_rpm() is None
                             else round(self.http.observed_rpm(), 1)),
            "requests_issued": self.http.requests,
            "credentials_present": credential_presence(),
            "orders_submitted": 0,
            "venue_mutations": 0,
        })
        self.emit(row)
        self.log("HEARTBEAT %s" % json.dumps(
            {k: row[k] for k in ("uptime_s", "cycles", "markets",
                                 "tokens_tracked", "rows_per_stream",
                                 "single_token_markets", "http_429",
                                 "scheduled_rpm", "observed_rpm",
                                 "requests_issued",
                                 "pacer_worst_case_rpm")},
            sort_keys=True))
        return row

    def write_coverage_view(self) -> Path:
        """A DERIVED view, in its own file, computed from the raw tape. The
        raw parts are never touched."""
        rows = self.tape.read_all()
        #: Two derived views, both computed FROM the raw tape, both in
        #: their own file. Nothing in raw/ is read for anything but reading.
        self.tape.write_derived("activity_join_proof.json",
                                activity_join_proof(rows))
        return self.tape.write_derived(
            "token_coverage.json", verify_both_tokens(rows))

    # -- run ------------------------------------------------------------
    def run(self, minutes: float = 0.0,
            sleep: Callable[[float], None] = time.sleep) -> int:
        """Forever when `minutes <= 0`, the convention every sibling
        collector in deploy/ uses. Never exits on a transient error."""
        end = float("inf") if minutes <= 0 else time.time() + minutes * 60.0
        # READ-ONLY, once, before the loop: what is already on the volume.
        # This is how a restart proves the earlier parts survived.
        self.emit_boot_inventory()
        # An interval that began before this process must still be
        # closed by it, or it has a start and no end.
        self.resume_activity_window()
        last_discovery = 0.0
        last_activity = 0.0
        last_heartbeat = 0.0
        while time.time() < end:
            self.cycles += 1
            now = time.time()
            try:
                if now - last_discovery >= self.discovery_interval_s:
                    self.discover()
                    last_discovery = now
                    last_activity = now
                elif (self.poly is not None
                      and SELECTOR_L5_ACTIVE in self.selectors
                      and now - last_activity >= self.activity_interval_s):
                    try:
                        self._emit_activity(self.poly.l5_activity())
                        if self._activity_down_since is not None:
                            self._emit_activity_window("CLOSE")
                            self._activity_down_since = None
                    except Exception as exc:                   # noqa: BLE001
                        # "HTTPError" alone cost a diagnosis once already
                        # (Attempt 5's 400). Record the STATUS, the
                        # ENDPOINT and the venue's own BODY, sanitized.
                        d = describe_http_failure(exc)
                        row = envelope(venue=VENUE_POLY, market="-",
                                       token=None, outcome=None,
                                       feed=PolymarketVenue.activity_feed,
                                       selector=SELECTOR_L5_ACTIVE,
                                       kind="activity_error")
                        row.update(d)
                        row["ANALYSIS_WARNING"] = ACTIVITY_UNAVAILABLE_LABEL
                        try:
                            self.emit(row)
                        except Exception:                      # noqa: BLE001
                            pass
                        self._activity_failures += 1
                        if self._activity_down_since is None:
                            self._activity_down_since = time.time()
                            self._emit_activity_window("OPEN")
                        self.log("activity poll failed: %s"
                                 % json.dumps(d, sort_keys=True))
                    last_activity = now
                self.poll_books()
                if now - last_heartbeat >= self.heartbeat_s:
                    self.heartbeat()
                    self.write_coverage_view()
                    last_heartbeat = now
                    self.pacer.relax()
            except KeyboardInterrupt:                          # pragma: no cover
                self.log("interrupted; tape is intact and append-only")
                return 0
            except Exception as exc:                           # noqa: BLE001
                # NEVER exit on a transient error, and never fail closed
                # into silence: the failure goes ON THE TAPE.
                row = envelope(venue=",".join(self.venues), market="-",
                               token=None, outcome=None, feed="supervisor",
                               selector=",".join(self.selectors),
                               kind="cycle_error")
                row["error"] = "%s:%s" % (type(exc).__name__, str(exc)[:200])
                try:
                    self.emit(row)
                except Exception:                              # noqa: BLE001
                    pass
                self.log("cycle error (continuing): %s" % type(exc).__name__)
                sleep(min(5.0, max(0.5, self.poll_interval_s)))
                continue
            sleep(self.poll_interval_s)
        self.heartbeat()
        self.write_coverage_view()
        return 0


# ================================================================== entry

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: Iterable[str]) -> tuple:
    raw = os.environ.get(name)
    if not raw:
        return tuple(default)
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def from_env(**overrides: Any) -> BookShadowCollector:
    kwargs: Dict[str, Any] = dict(
        root=os.environ.get(ENV_ROOT, "/state/l5-book-shadow"),
        venues=_env_list(ENV_VENUES, (VENUE_POLY,)),
        selectors=_env_list(ENV_SELECTORS, (SELECTOR_L5_ACTIVE,)),
        series=_env_list(ENV_SERIES, DEFAULT_SERIES),
        poll_interval_s=_env_float(ENV_POLL_S, DEFAULT_POLL_S),
        activity_interval_s=_env_float(ENV_ACTIVITY_S, DEFAULT_ACTIVITY_S),
        discovery_interval_s=_env_float(ENV_DISCOVERY_S, DEFAULT_DISCOVERY_S),
        heartbeat_s=_env_float(ENV_HEARTBEAT_S, DEFAULT_HEARTBEAT_S),
        max_markets=int(_env_float(ENV_MAX_MARKETS, DEFAULT_MAX_MARKETS)),
        max_rpm=_env_float(ENV_MAX_RPM, DEFAULT_MAX_RPM),
        l5_address=os.environ.get(ENV_L5_ADDRESS, L5_ADDRESS_DEFAULT),
    )
    root = kwargs.pop("root")
    kwargs.update(overrides)
    return BookShadowCollector(root, **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="0 or less = run forever (deployment default)")
    ap.add_argument("--gate-only", action="store_true",
                    help="evaluate the boot gate and exit")
    args = ap.parse_args(argv)

    gate = boot_gate(l5_address=os.environ.get(
        ENV_L5_ADDRESS, L5_ADDRESS_DEFAULT))
    print(json.dumps(gate, sort_keys=True), flush=True)
    if not gate["ok"]:
        print("BOOT GATE FAILED: %s -- refusing to start"
              % ",".join(gate["blocking_failures"]), flush=True)
        return 2
    if args.gate_only:
        return 0
    collector = from_env()
    print("BOOK SHADOW START venues=%s selectors=%s series=%s "
          "worst_case_rpm=%.1f root=%s"
          % (collector.venues, collector.selectors, collector.series,
             collector.pacer.worst_case_rpm(), collector.tape.root),
          flush=True)
    return collector.run(minutes=args.minutes)


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
