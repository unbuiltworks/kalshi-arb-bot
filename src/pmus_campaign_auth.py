"""Runtime campaign authorization for the sports bot (pure decision).

Owner 2026-11-23: "Eliminating redeployments for every campaign is
essential." A campaign is opened by a SIGNED, EXPIRING file the running bot
reads, not by a settings change that restarts it.

THREE LAYERS, IN THIS ORDER.
  1. The emergency halt (watchdog latch). Checked before this on every
     order; nothing here can clear or override it, and a campaign that has
     seen a latched halt is ENDED for good -- it never resumes, even after
     the owner resets the halt.
  2. The hard ceilings: the bot's DEPLOYED settings. This file can only
     choose values inside them. A value above a ceiling refuses the WHOLE
     file (never clamped: an out-of-bounds file is a mistake or an attack,
     not a request for the maximum).
  3. The campaign file: window, competitions, market count, order size and
     inventory allocation.

FAIL CLOSED, WITH NO GRACE. The only path to "permitted" is a file that was
fetched successfully just now, parses, carries a valid signature over every
field, names this service and this exact build, is inside its own window,
is inside every ceiling, has not been ended or revoked, and is the one
campaign in force. Anything else -- including a fetch that failed -- is not
permitted. Owner: "A repository outage must never extend an expired or
revoked authorization."

RESTART-SAFE, NOT RESTARTABLE. The campaign in force is recorded in the
bot's durable book by id and by the digest of its exact bytes. The SAME
file resumes after a restart with its original start and expiry, so spend,
inventory and losses (all derived from the book since that start) carry
on. A DIFFERENT file under the same id is refused, and an id that has
ended (expired, revoked, halted, replaced) is never accepted again, so
nothing can come back with a fresh budget.

NOT IN THE FILE, BY CONSTRUCTION: the account limit, the campaign loss
stop, the daily halt, the pair cap, maker-only, the depth rule, the
international-price requirement, anything about a halt. An unknown field
makes the file invalid, so none of those can be smuggled in under a new
name and a typo cannot be silently ignored.

`evaluate` and `decide` fetch nothing, place nothing and write nothing.
`FileReader` is the one thing here that touches the network: it GETs the
file on its own thread and keeps the last attempt, successful or not.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

#: every field the signature covers, in a fixed order. `revokes` is signed
#: as the empty string when absent.
SIGNED_FIELDS = ("campaign_id", "service", "build", "not_before", "expires",
                 "competitions", "max_markets", "order_qty",
                 "campaign_spend_usd", "campaign_unpaired_usd",
                 "per_market_spend_usd", "per_market_unpaired_usd",
                 "slate", "revokes", "issued_utc")
REQUIRED = tuple(f for f in SIGNED_FIELDS if f != "revokes")
ALLOWED = frozenset(SIGNED_FIELDS) | {"hmac"}
SLATES = ("live", "pregame", "both")
#: a file dated further ahead than this is refused (forgery does not need
#: the future; clocks drift a little).
CLOCK_SKEW_S = 120.0
#: the shortest build id that may name a commit.
MIN_BUILD_LEN = 9
#: numeric ceilings: file field -> ceiling key
_USD = ("campaign_spend_usd", "campaign_unpaired_usd",
        "per_market_spend_usd", "per_market_unpaired_usd")


def canonical(doc: Mapping[str, Any]) -> bytes:
    body = {}
    for f in SIGNED_FIELDS:
        v = doc.get(f, "" if f == "revokes" else None)
        if f == "competitions" and isinstance(v, (list, tuple)):
            v = sorted(str(x) for x in v)
        body[f] = v
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(doc: Mapping[str, Any], secret: str) -> str:
    return hmac.new(str(secret).encode(), canonical(doc),
                    hashlib.sha256).hexdigest()


def digest(raw: Any) -> str:
    b = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode()
    return hashlib.sha256(bytes(b)).hexdigest()


def _t(value: Any) -> Optional[dt.datetime]:
    try:
        s = str(value).strip().replace("Z", "+00:00")
        t = dt.datetime.fromisoformat(s)
    except Exception:                                     # noqa: BLE001
        return None
    if t.tzinfo is None:
        return None                       # a time with no zone is not a time
    return t.astimezone(dt.timezone.utc)


def _dec(value: Any) -> Optional[D]:
    if isinstance(value, bool):
        return None
    try:
        d = D(str(value))
    except Exception:                                     # noqa: BLE001
        return None
    return d if d.is_finite() else None


def _no(reason: str, **kw: Any) -> Dict[str, Any]:
    return {"permit": False, "reason": reason, "campaign": None, **kw}


def evaluate(raw: Any, *, secret: str, service: str, build: Optional[str],
             ceilings: Mapping[str, Any], now: dt.datetime,
             ended_ids: Iterable[str] = (),
             active: Optional[Mapping[str, Any]] = None
             ) -> Dict[str, Any]:
    """Decide over the file's bytes. {"permit", "reason", "campaign"}.

    `ceilings`: max_window_s, max_markets, order_qty, campaign_spend_usd,
    campaign_unpaired_usd, per_market_spend_usd, per_market_unpaired_usd,
    competitions (the set that may ever be named).
    `ended_ids`: campaign ids that have ended, from the durable book.
    `active`: the durable record of the campaign in force, if any:
    {"id", "digest"}.
    `campaign` is returned whenever the file itself is VALID (even if the
    window has not opened or has closed), so the caller can show it and
    can record its end; `permit` is the only permission.
    """
    if not str(secret or "").strip():
        return _no("AUTH_SECRET_NOT_SET")
    if raw is None or (isinstance(raw, (bytes, bytearray, str))
                       and not str(raw if isinstance(raw, str)
                                   else bytes(raw).decode("utf-8",
                                                          "replace")).strip()):
        return _no("AUTH_FILE_EMPTY")
    try:
        doc = json.loads(raw)
    except Exception:                                     # noqa: BLE001
        return _no("AUTH_FILE_MALFORMED")
    if not isinstance(doc, Mapping):
        return _no("AUTH_FILE_MALFORMED")
    unknown = sorted(set(doc) - ALLOWED)
    if unknown:
        return _no("AUTH_UNKNOWN_FIELD", fields=unknown[:8])
    missing = [f for f in REQUIRED if f not in doc]
    if missing:
        return _no("AUTH_MISSING_FIELD", fields=missing)
    got = str(doc.get("hmac") or "")
    if not got or not hmac.compare_digest(got, sign(doc, secret)):
        return _no("AUTH_SIGNATURE_INVALID")
    cid = str(doc.get("campaign_id") or "").strip()
    if not cid:
        return _no("AUTH_NO_CAMPAIGN_ID")
    out_id = {"campaign_id": cid}
    if str(doc.get("service") or "").strip() != str(service).strip() or \
            not str(service).strip():
        return _no("AUTH_WRONG_SERVICE", **out_id)
    want_build = str(doc.get("build") or "").strip().lower()
    have = str(build or "").strip().lower()
    if not have:
        return _no("AUTH_RUNNING_BUILD_UNKNOWN", **out_id)
    if len(want_build) < MIN_BUILD_LEN or not have.startswith(want_build):
        return _no("AUTH_WRONG_BUILD", **out_id)
    t0, t1, ti = _t(doc.get("not_before")), _t(doc.get("expires")), \
        _t(doc.get("issued_utc"))
    if t0 is None or t1 is None or ti is None:
        return _no("AUTH_TIME_UNREADABLE", **out_id)
    if t1 <= t0:
        return _no("AUTH_WINDOW_EMPTY", **out_id)
    mw = _dec(ceilings.get("max_window_s"))
    if mw is None or mw <= 0:
        return _no("AUTH_CEILING_UNREADABLE", field="max_window_s", **out_id)
    if D(str((t1 - t0).total_seconds())) > mw:
        return _no("AUTH_ABOVE_CEILING", field="window", **out_id)
    if (ti - now).total_seconds() > CLOCK_SKEW_S:
        return _no("AUTH_FUTURE_DATED", **out_id)
    comps = doc.get("competitions")
    if not isinstance(comps, list) or not comps or \
            not all(isinstance(c, str) and c.strip() for c in comps):
        return _no("AUTH_COMPETITIONS_INVALID", **out_id)
    comps = sorted({c.strip().lower() for c in comps})
    may = {str(c).strip().lower() for c in (ceilings.get("competitions")
                                             or ())}
    outside = [c for c in comps if c not in may]
    if outside:
        return _no("AUTH_ABOVE_CEILING", field="competitions",
                   outside=outside[:8], **out_id)
    vals: Dict[str, D] = {}
    for f in ("max_markets", "order_qty") + _USD:
        v, c = _dec(doc.get(f)), _dec(ceilings.get(f))
        if c is None or c <= 0:
            return _no("AUTH_CEILING_UNREADABLE", field=f, **out_id)
        if v is None or v <= 0:
            return _no("AUTH_VALUE_INVALID", field=f, **out_id)
        if f in ("max_markets", "order_qty") and v != v.to_integral_value():
            return _no("AUTH_VALUE_INVALID", field=f, **out_id)
        if v > c:
            return _no("AUTH_ABOVE_CEILING", field=f, **out_id)
        vals[f] = v
    slate = str(doc.get("slate") or "").strip().lower()
    if slate not in SLATES:
        return _no("AUTH_VALUE_INVALID", field="slate", **out_id)
    revokes = str(doc.get("revokes") or "").strip()
    camp = {"campaign_id": cid, "not_before": t0.isoformat(),
            "expires": t1.isoformat(), "start": t0.timestamp(),
            "end": t1.timestamp(), "competitions": comps, "slate": slate,
            "revokes": revokes or None, "digest": digest(raw),
            **{k: str(v) for k, v in vals.items()}}
    ended = {str(x) for x in (ended_ids or ())}
    if cid in ended:
        return {"permit": False, "reason": "AUTH_CAMPAIGN_ENDED",
                "campaign": camp}
    if active and str(active.get("id") or ""):
        aid = str(active.get("id"))
        if aid == cid:
            if str(active.get("digest") or "") != camp["digest"]:
                # same name, different contents: not the campaign that
                # was authorized. Changes come as a NEW id that revokes.
                return {"permit": False,
                        "reason": "AUTH_CAMPAIGN_CONTENT_CHANGED",
                        "campaign": camp}
        elif revokes != aid:
            return {"permit": False, "reason": "AUTH_ANOTHER_CAMPAIGN_ACTIVE",
                    "campaign": camp, "active_id": aid}
    if now < t0:
        return {"permit": False, "reason": "AUTH_NOT_STARTED",
                "campaign": camp}
    if now >= t1:
        return {"permit": False, "reason": "AUTH_EXPIRED", "campaign": camp}
    return {"permit": True, "reason": None, "campaign": camp}


def decide(snapshot: Mapping[str, Any], *, max_age_s: float, **kw: Any
           ) -> Dict[str, Any]:
    """`evaluate` over the reader's last snapshot, with NO GRACE: the last
    fetch must have SUCCEEDED and be at most `max_age_s` old. `snapshot`:
    {"raw", "age_s", "err", "missing"}."""
    if snapshot.get("err"):
        return _no("AUTH_UNREACHABLE", error=str(snapshot.get("err"))[:80])
    age = snapshot.get("age_s")
    if age is None:
        return _no("AUTH_NOT_READ_YET")
    if float(age) > float(max_age_s):
        return _no("AUTH_SNAPSHOT_STALE", age_s=round(float(age), 1))
    if snapshot.get("missing"):
        return _no("AUTH_FILE_ABSENT")
    return evaluate(snapshot.get("raw"), **kw)


class FileReader:
    """Reads the campaign file off the trading loop. The loop only ever
    looks at the last ATTEMPT: a failed attempt replaces a good one, so a
    file that can no longer be refreshed stops permitting at once."""

    def __init__(self, url: str, *, bearer: str = "", accept: str = "",
                 every_s: float = 20.0, timeout_s: float = 5.0,
                 fetch: Optional[Callable[[], Optional[bytes]]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.url, self._bearer, self._accept = str(url or ""), bearer, accept
        self._every, self._timeout = max(1.0, float(every_s)), float(timeout_s)
        self._fetch, self._clock = fetch, clock
        self._snap: Dict[str, Any] = {"raw": None, "at": None, "err": None,
                                      "missing": False, "tries": 0}
        self.stop = False

    def _get(self) -> Optional[bytes]:
        """The file's bytes, or None when the host says it does not exist
        (a successful answer). Anything else raises."""
        import urllib.error
        import urllib.request
        h = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
        if self._bearer:
            h["Authorization"] = "Bearer " + self._bearer
        if self._accept:
            h["Accept"] = self._accept
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    self.url, headers=h), timeout=self._timeout) as r:
                if getattr(r, "status", 200) != 200:
                    raise RuntimeError("http_%s" % getattr(r, "status", "?"))
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def refresh_once(self) -> None:
        snap = dict(self._snap, tries=int(self._snap["tries"]) + 1)
        if not self.url.strip():
            snap.update(raw=None, missing=False, err="AUTH_URL_NOT_SET")
        else:
            try:
                raw = (self._fetch or self._get)()
                snap.update(raw=raw, missing=raw is None,
                            at=self._clock(), err=None)
            except BaseException as exc:                 # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # NO GRACE: the earlier bytes are dropped with the failure
                snap.update(raw=None, missing=False,
                            err=type(exc).__name__)
        self._snap = snap                    # one atomic reference swap

    def snapshot(self) -> Dict[str, Any]:
        s = self._snap
        age = None if s["at"] is None else max(0.0, self._clock() - s["at"])
        return {"raw": s["raw"], "age_s": age, "err": s["err"],
                "missing": s["missing"], "tries": s["tries"]}

    def start(self) -> "FileReader":
        import threading

        def run() -> None:
            while not self.stop:
                try:
                    self.refresh_once()
                except Exception:                        # noqa: BLE001
                    pass
                time.sleep(self._every)
        threading.Thread(target=run, daemon=True,
                         name="campaign-auth-reader").start()
        return self
