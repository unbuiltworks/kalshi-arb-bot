"""The BTC 5-minute live session: ONE approved window, then stop.

WHAT IT DOES, in order, for the one market named in `L5_BTC5M_MARKET`:

  1. refuses to start a market it has already run (a durable marker);
  2. reads the venue market and runs the EXISTING transport's preflight for
     the 5-minute series, then arms the transport only if the owner's arm
     token is present (`pmus_auth.armed()`) -- exactly as BTC15m arms;
  3. installs the adapter's `owns_venue_order_id` as the transport's
     cancel-ownership test, so a cancel can only name an order this ledger
     recorded;
  4. from the window start to `CANCEL_LEAD_S` before its end, every
     `STEP_S`: reads the shared halt (`L5_HALT_URL`, the same record the
     independent watchdog asserts), the operator halt, and the account-wide
     loss state the BTC15m service publishes; if any refuses or cannot be
     read it cancels its own orders and places nothing; otherwise one
     adapter step, on the public book and the venue-index probability;
  5. cancels every order of its own by exact venue id, re-reading each to a
     terminal state, and disarms the transport;
  6. waits for the venue's own settlement, records it once, reads each of
     its orders back for the venue's commission, compares its inventory
     with the venue's position rows, and emits one session report;
  7. writes the marker and idles. It never chooses another window.

EVERY ORDER STILL SPENDS `claim_btc5m_submission` inside the adapter, so the
owner's approval document for this exact window is required as well.

LABELS. The report states submitted, filled, cancelled, settled. Settlement
P&L is settlement arithmetic on the venue's confirmed fills minus the
venue's own commission; it is "realized" only once the venue has credited
the redemption, and the report says so. An order the transport refused
because it was disarmed is reported as WOULD_SUBMIT and never as a trade.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_live_authorization as AUTH

D = Decimal
UTC = dt.timezone.utc
WINDOW_S = 300.0
STEP_S = 5.0
#: stop placing and start cancelling this long before the window ends
CANCEL_LEAD_S = 30.0
#: how long after the window end the cancel loop may keep re-reading
CANCEL_GRACE_S = 60.0
#: how long to wait for the venue's settlement before reporting without it
#: PREFLIGHT RETRY INSIDE THE WINDOW. A 5-minute market reads CLOSED until
#: the venue flips it open, and the flip is not instantaneous at the window
#: start: the 03:45Z window of 2026-11-12 preflighted 0.6 ms after its start,
#: failed, and -- with a single attempt -- never armed. The session now
#: retries with the BTC15m runner's backoff until PREFLIGHT_UNTIL_S into the
#: window. Arming still happens only through `authorize_arming`, only after
#: a PASSED preflight, and never after the deadline.
PREFLIGHT_BACKOFF_S = (3.0, 6.0, 12.0, 24.0)
PREFLIGHT_UNTIL_S = 90.0
SETTLE_WAIT_S = 1800.0
SETTLE_POLL_S = 15.0
#: the account-wide risk document must be at most this old
RISK_MAX_AGE_S = 30.0
GATEWAY = "https://gateway.polymarket.us"

LEDGER_ENV = "L5_BTC5M_LEDGER"
RISK_URL_ENV = "L5_ACCOUNT_RISK_URL"
HALT_URL_ENV = "L5_HALT_URL"
FILLS_PORT_ENV = "L5_BTC5M_FILLS_PORT"
SESSION_DIR = "btc5m_sessions"
#: OPT-IN to running every window the owner's approval ENUMERATES, instead
#: of the single window named in `L5_BTC5M_MARKET`. Anything but exactly
#: "ON" keeps the one-window behaviour, so a deployment changes nothing
#: until the owner asks. This GRANTS NOTHING: the list of windows comes
#: from the owner's own approval document and this runner can neither add
#: to it nor invent one.
CAMPAIGN_ENV = "L5_BTC5M_CAMPAIGN"
#: a hard ceiling on a ROLLING campaign, independent of the document. The
#: approval layer already caps `max_windows` at 8, and this refuses to plan
#: past that even if a future document were to carry more.
CA_MAX_ROLLING_WINDOWS = 8


def emit(kind: str, doc: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(dict(doc), sort_keys=True,
                                      default=str)), flush=True)


def _dec(v: Any) -> Optional[Decimal]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or isinstance(v, bool):
        return None
    try:
        d = D(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def window_start(market: str) -> Optional[dt.datetime]:
    try:
        d = dt.datetime.strptime(str(market)[-16:], "%Y-%m-%d-%H%Mz")
    except ValueError:
        return None
    return d.replace(tzinfo=UTC)


# ------------------------------------------------------------ the inputs

def public_book(market: str, *, fetch: Optional[Callable[[str], Any]] = None
                ) -> Dict[str, Any]:
    """YES best bid/ask from the venue's public market record. GET only."""
    def get(url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept":
                                                   "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as r:     # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    t0 = time.time()
    doc = (fetch or get)("%s/v1/market/slug/%s" % (GATEWAY, market))
    m = doc.get("market") if isinstance(doc, Mapping) and isinstance(
        doc.get("market"), Mapping) else doc
    if not isinstance(m, Mapping) or str(m.get("slug")) != str(market):
        return {"bid": None, "ask": None, "age_s": None,
                "reason": "BOOK_FOR_ANOTHER_MARKET"}
    return {"bid": _dec(m.get("bestBidQuote")),
            "ask": _dec(m.get("bestAskQuote")),
            "age_s": max(0.0, time.time() - t0),
            "closed": bool(m.get("closed"))}


def account_risk(*, url: str, now: dt.datetime, own_unaccounted: Decimal,
                 fetch: Optional[Callable[[str], Any]] = None
                 ) -> Dict[str, Any]:
    """The ACCOUNT-WIDE loss state, as the BTC15m service computes it, plus
    this strategy's settled results that service has not yet booked.

    Refuses (ok False) on: no URL, an unreadable or stale document, a
    document that says it is unreadable, either halt already tripped, the
    operator halt, or the combined figure reaching either limit. Only this
    strategy's LOSSES are added (`own_unaccounted` is <= 0 by construction),
    so the combination can only bind earlier, never later.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason)

    if not url:
        return no("ACCOUNT_RISK_URL_NOT_SET", env=RISK_URL_ENV)
    try:
        if fetch is not None:
            doc = fetch(url)
        else:
            with urllib.request.urlopen(url, timeout=3.0) as r:  # noqa: S310
                doc = json.loads(r.read().decode("utf-8"))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return no("ACCOUNT_RISK_UNREADABLE:%s" % type(exc).__name__)
    risk = doc.get("account_risk") if isinstance(doc, Mapping) else None
    if not isinstance(risk, Mapping):
        return no("ACCOUNT_RISK_NOT_PUBLISHED")
    try:
        as_of = dt.datetime.fromisoformat(
            str(doc.get("as_of_utc")).replace("Z", "+00:00"))
        age = (now - as_of).total_seconds()
    except (TypeError, ValueError):
        return no("ACCOUNT_RISK_UNDATED")
    if age > RISK_MAX_AGE_S or age < -5:
        return no("ACCOUNT_RISK_STALE", age_s=round(age, 1))
    if not risk.get("readable"):
        return no("ACCOUNT_RISK_SAYS_UNREADABLE", why=risk.get("reason"))
    # THE OTHER SERVICE'S OPERATOR HALT IS ITS OWN KILL SWITCH, NOT AN
    # ACCOUNT LIMIT. `L5_LIVE_HALT` is a per-service variable: the BTC15m
    # service's says "BTC15m may not trade" and is deliberately ON while it
    # stays disarmed. Reading it here would stop BTC5m for as long as BTC15m
    # is off. What binds across services is the LOSS state below and the
    # watchdog's halt record (`shared_halt`); BTC5m's own `L5_LIVE_HALT` is
    # read by the session itself. The value is reported, never acted on.
    other_halt = bool(risk.get("operator_halt"))
    day, halt = (_dec(risk.get("utc_day_realized_usd")),
                 _dec(risk.get("daily_halt_usd")))
    life, cum = (_dec(risk.get("lifetime_realized_usd")),
                 _dec(risk.get("cumulative_loss_ceiling_usd")))
    if None in (day, halt, life, cum):
        return no("ACCOUNT_RISK_FIGURES_UNREADABLE")
    if str(risk.get("utc_day")) != now.strftime("%Y-%m-%d"):
        return no("ACCOUNT_RISK_FOR_ANOTHER_DAY", utc_day=risk.get("utc_day"))
    add = min(D(0), own_unaccounted)
    day_all, life_all = day + add, life + add
    room = min(halt + day_all, cum + life_all)
    out = {"utc_day_realized_usd": str(day_all), "daily_halt_usd": str(halt),
           "lifetime_realized_usd": str(life_all),
           "cumulative_loss_ceiling_usd": str(cum),
           "btc5m_losses_not_yet_in_account_ledger_usd": str(add),
           # what may still be LOST before either account-wide limit binds
           "loss_room_usd": str(room),
           "btc15m_operator_halt": other_halt,
           "age_s": round(age, 1)}
    if day_all <= -halt:
        return no("ACCOUNT_DAILY_LOSS_HALT", **out)
    if -life_all >= cum:
        return no("ACCOUNT_CUMULATIVE_LOSS_CEILING", **out)
    return dict(out, ok=True, reason=None)


def submission_budget() -> Dict[str, Any]:
    """What the owner's per-class submission budgets allow and what is
    already spent. READ-ONLY: it composes `AUTH.max_of_class` with
    `AUTH.class_spent`, both of which only read, and claims nothing.

    WHY IT EXISTS. Accumulation requotes on every tick of drift, so
    `_order_class` charges most of its orders to REPLACE rather than OPEN.
    If `L5_LIVE_AUTH_MAX_REPLACE` is sized for the one-entry pair strategy,
    a live window stalls after its first requote and the step records say
    `AUTHORIZATION:...` -- which reads like a strategy failure and is not
    one. Reporting the budget at the start of the session and again in its
    report makes that distinguishable from the outside, before a window is
    spent finding out.

    `max` None means the owner configured no budget for that class, which
    does not bind. A directory we cannot read reports `spent` None rather
    than 0: an absence we cannot see is not an absence.
    """
    out: Dict[str, Any] = {}
    try:
        exp = AUTH.experiment_id()
        path = AUTH.ledger_path()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"readable": False, "why": type(exc).__name__}
    for k in AUTH.SUBMISSION_CLASSES:
        try:
            cap = AUTH.max_of_class(k)
        except BaseException:                                  # noqa: BLE001
            cap = None
        try:
            spent: Optional[int] = len(AUTH.class_spent(path, exp, k))
        except BaseException:                                  # noqa: BLE001
            spent = None
        out[k] = {
            "max": cap, "spent": spent,
            "remaining": (None if cap is None or spent is None
                          else max(0, cap - spent)),
            "binds": cap is not None,
        }
    out["readable"] = True
    out["experiment"] = exp
    out["note"] = ("accumulation charges most orders to REPLACE, not OPEN; "
                   "a REPLACE budget sized for the one-entry pair strategy "
                   "stalls a window after its first requote")
    return out


def shared_halt(url: str) -> Dict[str, Any]:
    """The halt record the independent watchdog asserts. Unreadable or
    unconfigured refuses: a live session needs the watchdog's hand on it."""
    if not url:
        return {"ok": False, "reason": "HALT_URL_NOT_SET", "env": HALT_URL_ENV}
    try:
        from src import l5_halt_gate as HG
        st = HG.HaltGate(HG.HttpHaltStore(url)).state()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "reason": "HALT_UNREADABLE:%s" % type(exc).__name__}
    # `HaltGate.state()`: `permitted` True only for a present, fresh,
    # parseable, NOT-halted record; every other case names its `why`
    if not isinstance(st, Mapping) or st.get("permitted") is not True:
        return {"ok": False, "reason": "HALT:%s" % (
            (st or {}).get("why") if isinstance(st, Mapping) else "UNREADABLE")}
    return {"ok": True, "reason": None}


# ------------------------------------------------------- the fills feed

def fills_document(ex: "X.Btc5mExecutor") -> Dict[str, Any]:
    """This ledger's own orders and fills, for the account-wide ledger to
    check against the venue. READ-ONLY; publishes no credential."""
    try:
        rows = ex.orders()
    except BaseException:                                      # noqa: BLE001
        return {"readable": False, "orders": []}
    if not ex._ledger_ok:                                      # noqa: SLF001
        return {"readable": False, "orders": []}
    return {"readable": True, "strategy": X.STRATEGY,
            "as_of_utc": dt.datetime.now(UTC).isoformat(),
            "orders": [{"client_order_id": c, "venue_order_id":
                        r.get("venue_order_id"), "market": r.get("market"),
                        "side": r.get("side"), "price": r.get("price"),
                        "filled_qty": r.get("filled_qty"),
                        "filled_cost": r.get("filled_cost"),
                        "state": r.get("state")}
                       for c, r in sorted(rows.items())]}


def serve_fills(ex: "X.Btc5mExecutor", port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:                              # noqa: N802
            if self.path.split("?", 1)[0].lstrip("/") != "btc5m_fills":
                body, code = b'{"error":"no such key"}', 404
            else:
                body, code = json.dumps(fills_document(ex),
                                        default=str).encode(), 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a) -> None:
            return
    httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ----------------------------------------------- the one arming boundary

class ArmingView:
    """The 5-minute ledger as `authorize_arming` reads a store: whether any
    order's state is uncertain, what is live, what holds cost, what is
    settled. READ-ONLY; it computes nothing the ledger does not state."""

    def __init__(self, ex: "X.Btc5mExecutor") -> None:
        self.ex = ex

    def orders_uncertain(self) -> Optional[str]:
        if not self.ex._ledger_ok:                             # noqa: SLF001
            return "BTC5M_LEDGER_UNREADABLE"
        return self.ex.halted_reason

    def live_orders(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for c, r in self.ex.live_orders().items():
            out[c] = dict(r, remaining=str(D(r["qty"]) - D(r["filled_qty"])))
        return out

    def committed(self) -> Dict[str, Decimal]:
        out: Dict[str, Decimal] = {}
        for r in self.ex.orders().values():
            if D(r["filled_cost"]) > 0:
                out[r["market"]] = out.get(r["market"], D(0)) + D(
                    r["filled_cost"])
        return out

    @property
    def state(self) -> Dict[str, Any]:
        return {"retired_markets": dict(self.ex._ledger["settled"])}  # noqa


class RunningLimits:
    """The session's capital scope in the shape the boundary reads: the
    same three attributes the BTC15m service's limits object carries, and
    `as_dict()` for the record. Values are the owner-approved figures, so
    the boundary's own CAMPAIGN_LIMITS_WIDER_THAN_APPROVED check compares
    like with like."""

    def __init__(self, *, max_usd_per_market: Decimal,
                 max_total_exposure_usd: Decimal,
                 daily_realized_loss_halt_usd: Decimal) -> None:
        self.max_usd_per_market = max_usd_per_market
        self.max_total_exposure_usd = max_total_exposure_usd
        self.daily_realized_loss_halt_usd = daily_realized_loss_halt_usd

    def as_dict(self) -> Dict[str, str]:
        return {"max_usd_per_market": str(self.max_usd_per_market),
                "max_total_exposure_usd": str(self.max_total_exposure_usd),
                "daily_realized_loss_halt_usd":
                    str(self.daily_realized_loss_halt_usd)}


def venue_window_bounds(market: str, *,
                        fetch: Optional[Callable[[str], Any]] = None
                        ) -> Optional[Dict[str, Any]]:
    """This market's window AS THE VENUE STATES IT (`assetPriceTerms`),
    offered only when it confirms the slug's own start and is five minutes
    long. None otherwise; the boundary then refuses WINDOW_BOUNDS_UNKNOWN."""
    def get(url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept":
                                                   "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as r:     # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    try:
        doc = (fetch or get)("%s/v1/market/slug/%s" % (GATEWAY, market))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    m = doc.get("market") if isinstance(doc, Mapping) and isinstance(
        doc.get("market"), Mapping) else doc
    if not isinstance(m, Mapping) or str(m.get("slug")) != str(market):
        return None
    terms = m.get("assetPriceTerms") or {}
    try:
        st = dt.datetime.fromisoformat(
            str(terms.get("windowStart")).replace("Z", "+00:00"))
        en = dt.datetime.fromisoformat(
            str(terms.get("windowEnd")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    ws = window_start(market)
    if ws is None or st != ws or (en - st).total_seconds() != WINDOW_S:
        return None
    return {"start": st.isoformat(), "end": en.isoformat(),
            "source": AUTH.WINDOW_SOURCE_VENUE}


def approved_markets(now: dt.datetime) -> Dict[str, Any]:
    """The BTC5m windows the OWNER's approval document enumerates, oldest
    first, as market slugs. READ-ONLY and it reserves nothing.

    The approval layer already carries `window_starts` and `max_windows`
    and validates both against the code ceilings, the document's own
    expiry and its last-open bound; this only reads the result. A window
    that is not in that list can never be returned, so the runner cannot
    widen what the owner authorized -- it can only consume it.
    """
    from src import l5_campaign_approval as CA
    out: Dict[str, Any] = {"markets": [], "max_windows": None,
                           "readable": False, "why": None}
    try:
        v = CA.campaign_decision(None, now=now, reserve=False)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out["why"] = "APPROVAL_UNREADABLE:%s" % type(exc).__name__
        return out
    if not isinstance(v, Mapping) or not v.get("present"):
        out["why"] = "NO_APPROVAL_DOCUMENT"
        return out
    scope = dict(v.get("scope") or {})
    if str(scope.get("series") or "") != X.MARKET_PREFIX:
        out["why"] = "APPROVAL_IS_FOR_ANOTHER_SERIES"
        return out
    mw = scope.get("max_windows")
    cap = int(mw) if isinstance(mw, int) else None

    def to_slug(t: dt.datetime) -> str:
        t = t.astimezone(UTC)
        return "%s%s-%sz" % (X.MARKET_PREFIX, t.strftime("%Y-%m-%d"),
                             t.strftime("%H%M"))

    starts = scope.get("window_starts")
    if isinstance(starts, (list, tuple)) and starts:
        # ENUMERATED: the owner named each window.
        slugs = []
        for raw in starts:
            try:
                t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                out["why"] = "APPROVAL_WINDOW_START_UNREADABLE"
                return out
            slugs.append(to_slug(t))
        out.update(markets=sorted(set(slugs)), readable=True, mode="ENUMERATED",
                   max_windows=(cap if cap is not None else len(slugs)))
        return out

    # ROLLING: the owner bounded the campaign with `last_open_utc` instead
    # of naming each window -- the approval layer's own alternative, and
    # the same shape the 15-minute lane uses. We take the NEXT windows in
    # order, and the bound is whichever of `last_open_utc` and
    # `max_windows` binds first. Absent BOTH forms the approval layer has
    # already refused the document, so there is no permissive fallback here.
    last_open = scope.get("last_open_utc")
    if not last_open:
        out["why"] = "APPROVAL_ENUMERATES_NO_WINDOWS"
        return out
    try:
        lo = dt.datetime.fromisoformat(str(last_open).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        out["why"] = "APPROVAL_LAST_OPEN_UNREADABLE"
        return out
    step = dt.timedelta(seconds=WINDOW_S)
    t = now.astimezone(UTC).replace(second=0, microsecond=0)
    t -= dt.timedelta(minutes=t.minute % 5)
    if t <= now:                       # never a window already under way
        t += step
    rolled = []
    limit = cap if cap is not None else CA_MAX_ROLLING_WINDOWS
    while t <= lo and len(rolled) < min(limit, CA_MAX_ROLLING_WINDOWS):
        rolled.append(to_slug(t))
        t += step
    out.update(markets=rolled, readable=True, mode="ROLLING",
               last_open_utc=lo.isoformat(),
               max_windows=(cap if cap is not None else len(rolled)))
    if not rolled:
        out["why"] = "NO_WINDOW_OPENS_BEFORE_LAST_OPEN_UTC"
    return out


def arm_through_boundary(session: "Session") -> Dict[str, Any]:
    """ARM ONLY THROUGH `authorize_arming`, the one shared boundary every
    arming path in src/ uses. Every input is this session's own reading:
    the owner's campaign verdict for this exact window, the preflight that
    just ran, the 5-minute ledger, the watchdog's halt and this service's
    own operator halt, a reconciled book, venue-confirmed window bounds,
    the owner's arm token, the shadow lock and the approved capital scope.
    A refusal raises `ArmingRefusal` and the transport stays disarmed."""
    from src import l5_campaign_approval as CA
    from src import pmus_auth as Q1
    now = session._now()                                       # noqa: SLF001
    market = session.market
    h = session.halt_fn()
    scope = session.approved_scope()
    cap = session.exposure_ceiling()
    loss = _dec(scope.get("max_daily_loss_usd"))
    limits = None
    if cap is not None and loss is not None:
        limits = RunningLimits(max_usd_per_market=cap,
                               max_total_exposure_usd=cap,
                               daily_realized_loss_halt_usd=loss)
    rec = session.reconcile()
    claim = AUTH.authorize_arming(
        campaign=CA.campaign_decision(market, now=now, reserve=False),
        market=market,
        preflight=session.last_preflight,
        store=ArmingView(session.ex),
        halted=bool(not h.get("ok") or AUTH.operator_halt_engaged()),
        halt_gate_present=bool((os.getenv(HALT_URL_ENV) or "").strip()),
        reconciled=bool(rec.get("read") and rec.get("agrees")),
        unresolved=(),
        transport_armable=Q1.armed(),
        limits=limits,
        window_bounds=session.bounds_fn(market),
        nominal_window_s=WINDOW_S,
        series=X.MARKET_PREFIX,
        # the owner's own figure, clamped by the approval layer against the
        # code ceiling. One when the document names one.
        max_windows=max(1, int(session.max_windows or 1)),
        now=now,
        shadow_locked=AUTH.shadow_lock_engaged())
    session.t.arm()
    return {"claim": claim}


# ------------------------------------------------------------ the session

class Session:
    """One window. Every collaborator is injectable; nothing is global."""

    def __init__(self, *, market: str, executor: "X.Btc5mExecutor",
                 transport: Any, root: str,
                 book_fn: Callable[[str], Mapping[str, Any]],
                 p_fn: Callable[[str, float, float], Mapping[str, Any]],
                 risk_fn: Callable[[dt.datetime, Decimal], Mapping[str, Any]],
                 halt_fn: Callable[[], Mapping[str, Any]],
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 armed_fn: Optional[Callable[[], bool]] = None,
                 arm_fn: Optional[Callable[[Any], Any]] = None,
                 bounds_fn: Optional[Callable[[str], Any]] = None,
                 max_windows: int = 1) -> None:
        self.market, self.ex, self.t = market, executor, transport
        self.root = root
        self.book_fn, self.p_fn = book_fn, p_fn
        self.risk_fn, self.halt_fn = risk_fn, halt_fn
        self.clock, self.sleep = clock, sleep
        self.armed_fn = armed_fn
        #: WHO MAY ARM THE TRANSPORT: `arm_through_boundary`, i.e. only
        #: through `l5_live_authorization.authorize_arming`, the one shared
        #: boundary every arming path in src/ uses (three guard tests pin
        #: it). `arm_fn` exists so a test can substitute the venue double's
        #: own arm; production never passes one.
        self.arm_fn = arm_fn if arm_fn is not None else (
            lambda t: arm_through_boundary(self))
        self.bounds_fn = bounds_fn or venue_window_bounds
        #: how many windows the OWNER's approval enumerates. It is passed
        #: to `authorize_arming` so the boundary checks this session against
        #: the figure the owner actually wrote, not a hardcoded 1. It
        #: authorizes nothing by itself.
        self.max_windows = int(max_windows or 1)
        self.last_preflight: Any = None
        self.events: List[Dict[str, Any]] = []
        self.would_submit = 0

    def _now(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.clock(), UTC)

    def _log(self, kind: str, doc: Mapping[str, Any]) -> None:
        rec = dict(doc, market=self.market, utc=self._now().isoformat())
        self.events.append(dict(rec, kind=kind))
        emit(kind, rec)

    def marker(self) -> str:
        return os.path.join(self.root, SESSION_DIR, "%s.done" % self.market)

    def _own_unaccounted(self) -> Decimal:
        """This strategy's settled result today, losses only. The BTC15m
        ledger books BTC5m results only if it ever adopted the position, so
        none of this is assumed to be inside its figure already."""
        got = self.ex.day_settled_pnl(self._now())
        if got is None:
            raise ValueError("own settled result unreadable")
        return min(D(0), got)

    def gates(self) -> Dict[str, Any]:
        h = self.halt_fn()
        if not h.get("ok"):
            return {"ok": False, "reason": "SHARED_HALT:%s" % h.get("reason")}
        if AUTH.operator_halt_engaged():
            return {"ok": False, "reason": "OPERATOR_HALT"}
        try:
            own = self._own_unaccounted()
        except ValueError:
            return {"ok": False, "reason": "OWN_SETTLED_RESULT_UNREADABLE"}
        r = self.risk_fn(self._now(), own)
        if not r.get("ok"):
            return {"ok": False, "reason": "ACCOUNT_RISK:%s" % r.get("reason"),
                    "risk": dict(r)}
        # THE WORST CASE MUST FIT INSIDE WHAT THE ACCOUNT MAY STILL LOSE.
        # The account-wide halts are judged on SETTLED results, so a position
        # opened with less room than it could lose would overshoot them
        # after the fact. The bound used is this strategy's whole exposure
        # ceiling -- the most it can have at risk at once -- so nothing it
        # is allowed to open can take the account past either limit.
        room = _dec(r.get("loss_room_usd"))
        cap = self.exposure_ceiling()
        if room is None or cap is None:
            return {"ok": False, "reason": "LOSS_ROOM_UNREADABLE",
                    "risk": dict(r)}
        if cap > room:
            return {"ok": False, "reason": "EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM",
                    "exposure_ceiling_usd": str(cap),
                    "loss_room_usd": str(room), "risk": dict(r)}
        return {"ok": True, "risk": dict(r)}

    def approved_scope(self) -> Dict[str, Any]:
        """The owner's approval for THIS window as the campaign layer reads
        it (already clamped to the code ceilings), or {} when there is no
        valid approval naming it. Read-only: reserves nothing. The halts are
        NOT applied here, so a refusal downstream is named for its cause."""
        from src import l5_campaign_approval as CA
        try:
            v = CA.campaign_decision(self.market, now=self._now(),
                                     reserve=False)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {}
        if not (v.get("present") and v.get("ok")):
            return {}
        scope = dict(v.get("scope") or {})
        if str(scope.get("series") or "") != X.MARKET_PREFIX:
            return {}
        return scope

    def exposure_ceiling(self) -> Optional[Decimal]:
        """The smaller of the adapter's own ceiling and the owner's approved
        exposure for this window. None when the approval cannot be read."""
        scope = self.approved_scope()
        approved = _dec(scope.get("max_total_exposure_usd"))
        own = _dec(self.ex.params.max_exposure_usd)
        if own is None:
            return None
        if approved is None:
            # no readable approval: the claim refuses every order anyway;
            # the adapter's own ceiling is the bound that applies
            return own
        return min(own, approved)

    def start(self) -> Dict[str, Any]:
        """Preflight and arm, AT THE WINDOW START. Never raises.

        A 5-minute market reads MARKET_STATUS_CLOSED until its window opens
        (observed on the venue 2026-11-12), and preflight requires an open
        market, so a preflight run at launch could never pass. The session
        therefore waits for the window, then preflights and arms. A window
        already past its last trading second is refused, never chased."""
        if os.path.exists(self.marker()):
            return {"ok": False, "reason": "MARKET_ALREADY_RUN",
                    "marker": self.marker()}
        ws = window_start(self.market)
        if ws is None or not self.market.startswith(X.MARKET_PREFIX):
            return {"ok": False, "reason": "NOT_A_BTC5M_MARKET"}
        if self.clock() >= ws.timestamp() + WINDOW_S - CANCEL_LEAD_S:
            return {"ok": False, "reason": "WINDOW_ALREADY_PAST"}
        while self.clock() < ws.timestamp():
            self.sleep(min(STEP_S, ws.timestamp() - self.clock()))
        attempts: List[Dict[str, Any]] = []
        deadline = ws.timestamp() + PREFLIGHT_UNTIL_S
        while True:
            pf = self._preflight_once()
            attempts.append({
                "t_in_window_s": round(self.clock() - ws.timestamp(), 2),
                "preflight": (pf or {}).get("preflight"),
                "failed": self._failed_checks(pf),
                "error": (pf or {}).get("error")})
            if (pf or {}).get("preflight") == "PASSED":
                break
            wait = PREFLIGHT_BACKOFF_S[min(len(attempts) - 1,
                                           len(PREFLIGHT_BACKOFF_S) - 1)]
            if self.clock() + wait > deadline:
                break
            self.sleep(wait)
        self.t.cancel_owner_oracle = self.ex.owns_venue_order_id
        self.last_preflight = pf
        armed = False
        arm_error = None
        if (pf or {}).get("preflight") == "PASSED":
            try:
                self.arm_fn(self.t)
                armed = bool(getattr(self.t, "armed", False))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                arm_error = "%s: %s" % (
                    getattr(exc, "reason", None) or type(exc).__name__,
                    str(getattr(exc, "detail", "") or exc)[:200])
        rep = {"ok": True, "preflight": (pf or {}).get("preflight"),
               "preflight_failed": self._failed_checks(pf),
               "preflight_attempts": attempts,
               "transport_armed": armed, "arm_error": arm_error,
               # the owner's per-class submission budgets, BEFORE the
               # window is spent finding out that one of them binds
               "submission_budget": submission_budget(),
               "window_start_utc": ws.isoformat()}
        self._log("L5_BTC5M_START", rep)
        return rep

    def _preflight_once(self) -> Dict[str, Any]:
        """One preflight. The transport RAISES on any failed gate and keeps
        the report on `preflight_report`; that report is returned so the
        failing check is named instead of lost."""
        try:
            return self.t.preflight()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            rep = getattr(self.t, "preflight_report", None)
            out = dict(rep) if isinstance(rep, Mapping) else {}
            out["preflight"] = "FAILED"
            out["error"] = type(exc).__name__
            return out

    @staticmethod
    def _failed_checks(pf: Any) -> List[str]:
        return [str(c.get("check")) for c in (pf or {}).get("checks") or ()
                if isinstance(c, Mapping) and not c.get("passed")
                and c.get("blocking", True)]

    def trade(self) -> None:
        ws = window_start(self.market).timestamp()
        stop_at = ws + WINDOW_S - CANCEL_LEAD_S
        while self.clock() < ws:
            self.sleep(min(STEP_S, ws - self.clock()))
        while self.clock() < stop_at:
            now = self.clock()
            g = self.gates()
            if not g["ok"]:
                rep = self.ex.end_of_window(self.market)
                self._log("L5_BTC5M_BLOCKED", {
                    "reason": g["reason"],
                    "cancelled": [c["venue_order_id"]
                                  for c in rep.get("cancelled") or ()]})
            else:
                book = self.book_fn(self.market)
                est = self.p_fn(self.market, ws, now) or {}
                p_up = est.get("p_up") if est.get("ok") else None
                rep = self.ex.step(
                    market=self.market,
                    book={"bid": book.get("bid"), "ask": book.get("ask"),
                          "age_s": book.get("age_s")},
                    t_in_window_s=now - ws, p_up=p_up,
                    p_source=("VENUE_INDEX" if p_up is not None else None),
                    exposure_ceiling_usd=self.exposure_ceiling())
                self.would_submit += len(rep.get("would_submit") or ())
                self._log("L5_BTC5M_STEP", {
                    "t_in_window_s": round(now - ws, 2),
                    "bid": book.get("bid"), "ask": book.get("ask"),
                    "p_up": p_up, "p_reason": est.get("reason"),
                    "action": rep.get("action"), "why": rep.get("why"),
                    "placed": [(p["side"], p["price"], p["qty"],
                                p["venue_order_id"])
                               for p in rep.get("placed") or ()],
                    "cancelled": [c.get("venue_order_id")
                                  for c in rep.get("cancelled") or ()],
                    "fills": [(f["side"], str(f["qty"]), str(f["price"]),
                               f["venue_order_id"])
                              for f in rep.get("fills") or ()],
                    "refused": [r.get("reason")
                                for r in rep.get("refused") or ()],
                    "would_submit_not_sent": len(rep.get("would_submit")
                                                 or ()),
                    "halted_reason": rep.get("halted_reason"),
                    "arming_refusal": rep.get("arming_refusal"),
                    "entry": {k: (rep.get("entry_eval") or {}).get(k) for k in
                              ("why", "cap_usd", "completion_reserve_usd",
                               "room_usd", "price", "qty")},
                    "hedge": {k: (rep.get("hedge_eval") or {}).get(k) for k in
                              ("why", "price", "qty", "pair_cost", "room_usd")}})
            self.sleep(max(0.0, min(STEP_S, stop_at - self.clock())))

    def close(self) -> Dict[str, Any]:
        """Cancel every own order by exact id until each reads terminal."""
        ws = window_start(self.market).timestamp()
        deadline = ws + WINDOW_S + CANCEL_GRACE_S
        while True:
            rep = self.ex.end_of_window(self.market)
            left = (rep.get("still_open") or []) + (rep.get("unresolved") or [])
            self._log("L5_BTC5M_CLOSE", {
                "cancelled": [c["venue_order_id"]
                              for c in rep.get("cancelled") or ()],
                "filled_while_cancelling": [c["venue_order_id"] for c in
                                            rep.get("filled") or ()],
                "still_open": [c.get("venue_order_id") for c in left]})
            if not left or self.clock() >= deadline:
                break
            self.sleep(2.0)
        try:
            self.t.disarm("BTC5M_SESSION_CLOSED")
        except BaseException:                                  # noqa: BLE001
            pass
        return {"clean": not left,
                "unresolved": [c.get("venue_order_id") for c in left]}

    def settle(self) -> Dict[str, Any]:
        ws = window_start(self.market).timestamp()
        until = ws + WINDOW_S + SETTLE_WAIT_S
        verdict = None
        while self.clock() < until:
            try:
                verdict = self.t.settlement(self.market)
            except BaseException:                              # noqa: BLE001
                verdict = None
            if verdict and verdict.get("terminal"):
                break
            self.sleep(SETTLE_POLL_S)
        if not (verdict and verdict.get("terminal")):
            return {"settled": False, "reason": "VENUE_SETTLEMENT_NOT_SEEN",
                    "last": verdict}
        px = _dec(verdict.get("settlement_px"))
        if px not in (D(0), D(1)):
            return {"settled": False, "reason": "SETTLEMENT_NOT_BINARY",
                    "settlement_px": str(px)}
        return dict(self.ex.settle(self.market, px == D(1)), settled=True,
                    settlement_px=str(px))

    def reconcile(self) -> Dict[str, Any]:
        """Our inventory beside the venue's own position row for the
        market. Agreement is stated, never assumed."""
        inv = self.ex.inventory(self.market)
        try:
            rows = [r for r in (self.t.positions() or ())
                    if isinstance(r, Mapping) and str(
                        r.get("marketSlug") or r.get("market")) == self.market]
        except BaseException as exc:                           # noqa: BLE001
            return {"read": False, "error": type(exc).__name__}
        net_ours = inv[C.UP]["qty"] - inv[C.DOWN]["qty"]
        venue_net = None
        for r in rows:
            venue_net = _dec(r.get("netPositionDecimal")
                             if r.get("netPositionDecimal") is not None
                             else r.get("netPosition"))
        return {"read": True, "venue_rows": rows,
                "ours": {k: {"qty": str(v["qty"]), "cost": str(v["cost"])}
                         for k, v in inv.items()},
                "ours_net_up_minus_down": str(net_ours),
                "venue_net": None if venue_net is None else str(venue_net),
                "agrees": (venue_net == net_ours) if venue_net is not None
                else (net_ours == 0 and not rows),
                "note": ("the venue nets the two outcome tokens; a matched "
                         "pair reads as zero there")}

    def fees(self) -> Dict[str, Any]:
        """The venue's own commission on each of our orders, read back."""
        total, per, unread = D(0), {}, []
        for coid, r in sorted(self.ex.orders().items()):
            vid = r.get("venue_order_id")
            if not vid or r.get("market") != self.market:
                continue
            try:
                row = self.t.order(vid) or {}
            except BaseException:                              # noqa: BLE001
                unread.append(vid)
                continue
            fee = _dec(row.get("commissionNotionalTotalCollected"))
            if fee is None:
                unread.append(vid)
                continue
            per[vid] = str(fee)
            total += fee
        return {"total_usd": str(total), "per_order": per,
                "unreadable": unread, "complete": not unread}

    def run(self) -> Dict[str, Any]:
        st = self.start()
        if not st.get("ok"):
            self._log("L5_BTC5M_NOT_STARTED", st)
            return {"started": False, **st}
        self.trade()
        closed = self.close()
        settled = self.settle()
        recon = self.reconcile()
        fees = self.fees()
        orders = self.ex.orders()
        mine = {c: r for c, r in orders.items() if r["market"] == self.market}
        pnl = _dec(settled.get("settlement_pnl")) if settled.get(
            "settled") else None
        fee_total = _dec(fees["total_usd"])
        report = {
            "market": self.market,
            "transport_armed": st.get("transport_armed"),
            "submitted": sum(1 for r in mine.values()
                             if r.get("venue_order_id")),
            "submit_uncertain": sum(1 for r in mine.values()
                                    if r["state"] == X.UNCERTAIN),
            "filled_orders": sum(1 for r in mine.values()
                                 if D(r["filled_qty"]) > 0),
            "fills_qty": str(sum((D(r["filled_qty"]) for r in mine.values()),
                                 D(0))),
            "fills_cost_usd": str(sum((D(r["filled_cost"])
                                       for r in mine.values()), D(0))),
            "cancelled": sum(1 for r in mine.values()
                             if r["state"] == X.CANCELLED),
            "would_submit_not_sent": self.would_submit,
            "submission_budget": submission_budget(),
            "close": closed, "settlement": settled, "reconcile": recon,
            "venue_fees": fees,
            "settled_net_usd": (None if pnl is None or not fees["complete"]
                                else str(pnl - fee_total)),
            "labels": (
                "venue-confirmed orders and fills only. settled_net_usd is "
                "settlement arithmetic on confirmed fills minus the venue's "
                "own commission; it is REALIZED once the venue credits the "
                "redemption. would_submit_not_sent counts decisions a "
                "disarmed transport did not send: they are not trades."),
        }
        os.makedirs(os.path.dirname(self.marker()), exist_ok=True)
        with open(self.marker(), "w") as fh:
            json.dump(report, fh, default=str, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        self._log("L5_BTC5M_SESSION_REPORT", report)
        return report


def main() -> int:            # pragma: no cover - the service entrypoint
    from src import l5_btc_reference as REF
    from src import l5_pmus_transport as T
    market = (os.getenv(X.MARKET_ENV) or "").strip()
    root = (os.getenv(AUTH.LEDGER_ENV) or "/state/btc5m/auth.jsonl")
    root = os.path.dirname(root)
    ledger = (os.getenv(LEDGER_ENV) or os.path.join(root, "btc5m_ledger.json"))
    # The executor's live-order bound follows the entry rule: 2 as before,
    # and in ACCUMULATE whatever `L5_BTC5M_MAX_RESTING_ENTRIES` allows, so
    # the controller's plan is not silently clipped by a smaller bound.
    ex = X.Btc5mExecutor(None, ledger,
                         max_live_orders=X.live_order_bound())
    port = (os.getenv(FILLS_PORT_ENV) or "").strip()
    if port:
        serve_fills(ex, int(port))
    ref = REF.Reference(window_s=WINDOW_S)

    def one_window(slug: str, max_windows: int) -> Dict[str, Any]:
        # THE EXECUTOR'S OWN LOCK NAMES THIS WINDOW. `arming_refusal`
        # refuses every submission unless `L5_BTC5M_MARKET` is set AND
        # equals the market being traded -- an ADDITIONAL lock in front of
        # the authorization claim. A campaign deliberately leaves that
        # variable empty so the runner can take its windows from the
        # owner's approval, which made the two incompatible: observed live
        # 2026-11-12 15:42Z, the controller chose OPEN_FAVORITE at 0.76,
        # 0.80, 0.82, 0.85 and every one was refused MARKET_NOT_CONFIGURED.
        #
        # Naming the current window here keeps the lock's meaning exactly:
        # at any instant it names ONE market, the one this session is
        # running, and it is narrowed per window rather than widened. What
        # AUTHORIZES the window is unchanged -- the owner's approval
        # enumerating it, and the per-order claim.
        prev = os.environ.get(X.MARKET_ENV)
        os.environ[X.MARKET_ENV] = slug
        try:
            return _run_one(slug, max_windows)
        finally:
            if prev is None:
                os.environ.pop(X.MARKET_ENV, None)
            else:
                os.environ[X.MARKET_ENV] = prev

    def _run_one(slug: str, max_windows: int) -> Dict[str, Any]:
        t = T.PmusTransport(market_slug=slug, series="BTC5M")
        ex.transport = t
        sess = Session(
            market=slug, executor=ex, transport=t, root=root,
            book_fn=public_book,
            p_fn=lambda m, ws, now: ref.estimate(m, ws, now),
            risk_fn=lambda now, own: account_risk(
                url=(os.getenv(RISK_URL_ENV) or "").strip(), now=now,
                own_unaccounted=own),
            halt_fn=lambda: shared_halt(
                (os.getenv(HALT_URL_ENV) or "").strip()),
            max_windows=max_windows)
        return sess.run()

    # WHICH WINDOWS. Either the ONE the owner named, which is the original
    # behaviour and the default, or -- only when the owner opts in -- every
    # window the owner's own approval document ENUMERATES. This runner can
    # neither add to that list nor invent a window.
    plan = {"markets": [market] if market else [], "max_windows": 1,
            "source": "L5_BTC5M_MARKET"}
    if not market and (os.getenv(CAMPAIGN_ENV) or "").strip() == "ON":
        got = approved_markets(dt.datetime.now(UTC))
        plan = {"markets": got["markets"],
                "max_windows": got["max_windows"] or 1,
                "source": "OWNER_APPROVAL_WINDOW_STARTS",
                "why": got["why"]}
    emit("L5_BTC5M_PLAN", dict(plan, utc=dt.datetime.now(UTC).isoformat()))

    if not plan["markets"]:
        emit("L5_BTC5M_IDLE", {"reason": "NO_MARKET_CONFIGURED",
                               "why": plan.get("why")})
    else:
        ref.start()
        # NEVER MORE THAN THE OWNER APPROVED, whatever the list says.
        for slug in plan["markets"][:max(1, int(plan["max_windows"]))]:
            rep = one_window(slug, plan["max_windows"])
            # ALREADY RUN OR ALREADY OVER IS A SKIP, NOT A STOP. A restart
            # inside a campaign re-plans the whole approved list and so meets
            # the windows it already finished; treating that as a refusal
            # ended the campaign on its FIRST entry and never reached the
            # windows still ahead of it (observed live, 2026-11-12 15:20Z:
            # the 15:10z marker stopped a campaign that still had 15:20z and
            # 15:25z to run). The durable marker still guarantees no window
            # is traded twice -- that is exactly what lets this be a skip.
            skip = {"MARKET_ALREADY_RUN", "WINDOW_ALREADY_PAST"}
            # A halt, a refusing account-risk state or an unresolved order
            # ENDS THE CAMPAIGN. It never marches on to the next window.
            stop = None
            if not rep.get("started"):
                reason = rep.get("reason")
                if reason in skip:
                    emit("L5_BTC5M_WINDOW_SKIPPED",
                         {"market": slug, "reason": reason,
                          "utc": dt.datetime.now(UTC).isoformat()})
                    continue
                stop = reason
            elif not (rep.get("close") or {}).get("clean", True):
                stop = "CLOSE_NOT_CLEAN"
            elif ex.halted_reason:
                stop = "LEDGER_HALTED:%s" % ex.halted_reason
            if stop:
                emit("L5_BTC5M_CAMPAIGN_STOPPED",
                     {"after": slug, "reason": stop,
                      "utc": dt.datetime.now(UTC).isoformat()})
                break
    # NEVER CHOOSES A WINDOW OF ITS OWN. It idles until the owner changes
    # the configuration; a restart finds each marker and does not re-trade.
    while True:
        emit("L5_BTC5M_IDLE", {"market": market or None,
                               "planned": len(plan["markets"]),
                               "utc": dt.datetime.now(UTC).isoformat()})
        time.sleep(60.0)


if __name__ == "__main__":    # pragma: no cover
    raise SystemExit(main())
