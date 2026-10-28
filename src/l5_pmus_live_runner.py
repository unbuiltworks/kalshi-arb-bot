"""THE production path for the faithful L5 probe on Polymarket US BTC15m.

    L5 realtime activity -> authenticated PMUS BTC15m book -> L5_NATIVE (frozen)
      -> PMUS_TRANSLATION (frozen) -> SAFETY_OVERLAY -> PmusTransport

This is not another shadow. It is the one process that will place the probe's
orders once the owner authorizes it, running today exactly as it will run
then. The ONLY thing that differs between now and an authorized run is whether
`PmusTransport.arm()` succeeds, and that needs BOTH a preflight that PASSED in
this process for the current market AND the owner's arm token. Until then
every intent reaches the transport and is refused as NOT_ARMED, and the refusal
is recorded on the action's provenance record.

WHAT IT DOES, EVERY WINDOW
  * the authenticated WS book subscribes the window BEFORE it opens and
    reports when its first book and first trade arrived (observability)
  * the authenticated preflight runs against the window's market, retried
    until it passes or the window ends, and its report is logged verbatim
  * each new L5 fill in that window is driven through FaithfulExecutor with
    the authenticated book AT RECEIPT attached -- nothing later, nothing public
  * at window end, a per-window summary: decisions by layer, observability,
    and (if armed) the venue's own position

WHAT IT DOES NOT DO
  * change L5_NATIVE or the frozen translation (030d8bf); both are imported,
    not copied, and the freeze guard hashes them
  * add a band, ceiling, maker-only rule or predictor. The only refusals it
    adds are account protection in SAFETY_OVERLAY, each named.

Logs are single-line JSON documents prefixed L5_LIVE_*, so they can be read
back from the platform's log stream without a volume.

    L5_LIVE_ROOT        state dir (inventory, decisions tape)
    L5_LIVE_POLL_S      L5 activity poll interval (default 3)
    L5_LIVE_LAMBDA      sizing factor (default: the manifest's lambda)
    L5_LIVE_EXPECT_ACCOUNT  optional: refuse unless the venue reports this id
    L5_LIVE_HALT        any non-empty value: deadman now, never arm
    PMUS_Q1_LIVE / PMUS_Q1_SESSION_ID / PMUS_Q1_ARM   the OWNER's arm token
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import signal
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Set

from src import l5_faithful_translation as FT
from src import l5_live_authorization as AUTH
from src import l5_decision_role as DROLE
from src import l5_live_execution as LX
from src import l5_maker_fill_model as FM
from src import l5_two_sided_shadow as TS
from src import l5_campaign_approval as CAMP
from src import l5_runtime_config_verify as CFGV
from src import pmus_fill_horizon_probe as FHP
from src import l5_cancel_mechanism as MECH
from src import l5_pmus_auth_book as AB
from src import l5_pmus_realtime_observer as OBS
from src import l5_pmus_transport as T
from src import l5_fill_calibration as CALIB
from src import l5_intent_gate as GATE
from src import l5_shadow_controller as SHADOW
from src import pmus_q1_executor as Q1

SERVICE_ROLE = "l5-pmus-live"
ROLE_ENV = "PMUS_SERVICE_ROLE"

#: P38 manifest: 1 / median(l5_size) over the preserved corpus
MANIFEST_LAMBDA = D("0.04518752824220514525")

#: P38 manifest limits -- account protection only
#: ONE OUTSTANDING ORDER, and it is now configurable because it had to be.
#: This was hardcoded at 8, so the bounded pilot's "one outstanding order"
#: restriction was not enforceable at all: the $1 cash cap bounds DOLLARS, so
#: at a 3c quote eight clips could rest at once and still total under a
#: dollar. The dollar bound was never the problem; the count bound did not
#: exist. The pilot default is 1; a deployment that wants the old behaviour
#: must say 8 out loud.
def max_live_orders() -> int:
    raw = (os.getenv("L5_MAX_LIVE_ORDERS") or "").strip()
    try:
        n = int(raw) if raw else 1
    except ValueError:
        n = 1                      # an unreadable limit is the tightest one
    return max(1, n)               # zero or negative would refuse everything


# An explicit owner-set ceiling, not a new authorization to trade. The
# production default remains $5 until the ledger's verified L5-only basis is
# established. Invalid or out-of-range values fail back to the tighter $5.
def daily_realized_loss_halt() -> D:
    raw = (os.getenv("L5_DAILY_REALIZED_LOSS_HALT_USD") or "").strip()
    try:
        value = D(raw) if raw else D("5")
        if not value.is_finite() or value < D("5") or value > D("50"):
            return D("5")
        return value
    except (ValueError, ArithmeticError):
        return D("5")


#: RAISED 5 -> 20, in lockstep with l5_campaign_approval.MAX_TOTAL_EXPOSURE_USD.
#: THEY MUST MOVE TOGETHER. `authorize_arming` records the approved envelope
#: into the claim and `_looser()` refuses if the RUNNING limits exceed the
#: APPROVED ones, so raising this file alone would not half-enable a larger
#: envelope -- it would refuse arming outright. max_abs_residual stays at 10
#: contracts by owner decision, and max_concurrent_markets stays at 1.
#: THE ACCOUNT-WIDE INSTANTANEOUS CEILING, NOW SETTABLE. It was a bare
#: D("20") and there was no way to configure it, so an owner who approved a
#: $2 instantaneous envelope could not get one: the approval document could
#: say 2, step 14 would then refuse CAMPAIGN_LIMITS_WIDER_THAN_APPROVED
#: because the RUNNING limit was 20, and putting 20 in the document to make
#: arming succeed left the real ceiling at 20. That is a risk-control
#: defect, not a configuration error, and the $1.65 cash envelope does NOT
#: substitute for it -- that bounds ONE order's obligation, while this
#: bounds worst-case loss across every market holding a position or a
#: resting order, manual trades included.
#:
#: DEFAULT IS UNCHANGED AT 20, so an unset variable changes nothing. Only
#: LOWERING is useful: step 14 refuses when running > approved, so a value
#: above the approval's figure cannot arm, and a value at or below it can.
#:
#: WHERE IT BINDS, and it is the same number in all three places:
#:   * step 14, as the running limit compared against the approval;
#:   * `account_exposure_lines`, as capacity_usd = cap - total;
#:   * the SIZING path, where capacity narrows cash_headroom, so a proposed
#:     order is reserved against what inventory and resting orders already
#:     consume rather than sized as if the book were empty.
MAX_TOTAL_EXPOSURE_ENV = "L5_MAX_TOTAL_EXPOSURE_USD"
DEFAULT_MAX_TOTAL_EXPOSURE_USD = D("20")


def max_total_exposure_usd() -> Optional[D]:
    """The account-wide instantaneous ceiling, or None meaning REFUSE.

    UNSET AND UNREADABLE ARE NOT THE SAME THING, and collapsing them is how
    an owner who typed a ceiling gets a looser one than they asked for:

      * UNSET -> the default. Nothing was configured, so nothing changes.
      * SET BUT UNREADABLE, non-finite, zero or negative -> **None**, which
        every consumer treats as a named refusal. An owner who deliberately
        configured a $2 ceiling and fat-fingered it must get a live refusal,
        NOT a silent $20. Returning the default here would mean a typo
        QUADRUPLED the approved envelope, which is exactly the shape of the
        defect this whole change exists to remove.

    None is not a quiet failure. It surfaces as
    CAMPAIGN_RUNNING_LIMITS_UNREADABLE at authorize_arming step 14 (limits
    that cannot be read cannot be shown to be inside the approval), as
    ACCOUNT_EXPOSURE_CEILING_UNKNOWN in the completion path, and as
    MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE in SafetyLimits.refuse -- three
    independent refusals, so no single missed call site can let an order
    through.
    """
    raw = (os.getenv(MAX_TOTAL_EXPOSURE_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_TOTAL_EXPOSURE_USD
    try:
        v = D(raw)
    except (ValueError, TypeError, ArithmeticError):
        # decimal.InvalidOperation subclasses ArithmeticError, so it is
        # caught here without importing it into this module.
        return None
    if not v.is_finite() or v <= 0:
        return None
    return v


PROBE_LIMITS = dict(max_usd_per_market=D("20"), max_abs_residual=D("10"),
                    max_total_exposure_usd=max_total_exposure_usd(),
                    max_concurrent_markets=1,
                    max_live_orders=max_live_orders(),
                    daily_realized_loss_halt_usd=daily_realized_loss_halt())

#: retried quickly: until it passes, an armed runner cannot act on the
#: window, and L5 opens within seconds (0045z: first action at +7.0s).
#: Five GETs per attempt, only until the first PASS of each window.
PREFLIGHT_RETRY_S = 3.0
#: and how it grows while the venue simply has not listed the next market
#: yet. 2300z on 2026-09-26 spent 126 seconds in that state and spent 80
#: venue requests on it -- 0.63 req/s, disarmed, with no orders. Retrying at
#: a fixed 3s against a market that does not exist yet is not urgency, it is
#: a busy-wait against a rate limit.
PREFLIGHT_BACKOFF_S = (3.0, 6.0, 12.0, 24.0)

#: a value no preflight report can produce, so the first outcome of a
#: window always logs. `None` cannot serve: it is exactly what the
#: fatal-exception report yields for blocking_failures.
_BLOCKERS_UNSET = object()

#: while the book is uncertain, how often we may re-ask the venue. Not every
#: tick: hammering a venue that is already erroring turns a transient fault
#: into a rate limit.
RECONCILE_RETRY_S = 15.0
#: a locally-live order that produces no venue progress for this long is not
#: trusted to be live. 1700z sat on eight of them: the venue had finished
#: with all eight, we never learned it, and MAX_LIVE_ORDERS refused 276
#: actions against orders that did not exist. Uncertainty was never set, so
#: the uncertainty path could not rescue it either.
STALLED_ORDER_S = 90.0
HB_EVERY_S = 60.0
#: how old an authoritative buying-power reading may be before it is
#: re-read. The reconcile path alone is NOT a cadence: it short-circuits
#: on a confirmed book, so a reading taken at startup would sit there for
#: the rest of the run and be reported as though it were current. One GET
#: a minute against a 1.5 rps budget is negligible.
CASH_REFRESH_S = 60.0
#: how often ONE order may be re-read. The tick is 3s and the old code
#: polled every live order on every tick: with a full envelope that is 2.7
#: GETs a second from this path alone, which is most of how we rate-limited
#: ourselves into the 2045z defect. A fill is still seen within seconds, and
#: the authenticated websocket book -- not this poll -- is the fast path.
ORDER_POLL_S = 6.0
#: THE AGGREGATE CEILING for order progress, which is what actually matters.
#: A per-order interval alone scales the request rate with the size of the
#: envelope: eight orders at one read per 6s is 1.333 req/s from this path
#: alone, most of the 1.5 the venue tolerates, before preflight or
#: settlement or reconciliation have asked for anything. The per-order
#: interval is therefore a FLOOR and this is the cap: with few orders
#: nothing changes, and a full envelope stretches instead of overflowing.
ORDER_POLL_RATE = 0.5
#: how often ONE market's settlement may be re-read. A market that has not
#: expired will not have expired 3 seconds later either.
SETTLEMENT_POLL_S = 10.0
#: how often the shadow controller may REPEAT an unchanged decision. Every
#: tick is still evaluated -- the modelled fill rule has to see every book
#: change -- but 300 identical NO_ACTION lines per window bury the record
#: instead of making it. Suppressed evaluations are counted, so nothing is
#: lost; a modelled fill is never suppressed.
SHADOW_EMIT_S = 60.0
#: how long after a window's stated end the shadow may ask the venue for a
#: settlement price. The venue publishes `settlementPx` after expiry, not at
#: it -- that gap is exactly what the money path's SETTLEMENT_POLL_S loop
#: exists for -- so asking at the boundary reads NOT_TERMINAL every time.
SHADOW_SETTLE_GRACE_S = 60.0

def settle_error_class(exc: BaseException) -> str:
    """Why a settlement read failed, in terms that distinguish the cases
    that converge from the ones that do not.

    NOT_FOUND and the 5xx/timeout family are transient: the market has not
    published a resolution yet, or the venue was briefly unreachable, and the
    next poll may answer. AUTH and FORBIDDEN are not transient and mean the
    read will never answer until a credential is fixed. UNPARSEABLE means the
    venue answered something we cannot read, which is the one class that must
    never be retried silently forever. Classification changes nothing about
    what is booked: every class leaves the window PENDING.
    """
    name = type(exc).__name__
    text = str(exc)[:300]
    code = ""
    for tok in ("401", "403", "404", "429", "500", "502", "503", "504"):
        if tok in text:
            code = tok
            break
    if code in ("401",):
        return "AUTH_401"
    if code in ("403",):
        return "FORBIDDEN_403"
    if code in ("404",):
        return "NOT_FOUND_404"
    if code in ("429",):
        return "RATE_LIMITED_429"
    if code in ("500", "502", "503", "504"):
        return "VENUE_5XX_%s" % code
    low = text.lower()
    if "timed out" in low or "timeout" in low or name.endswith("Timeout"):
        return "TIMEOUT"
    if "connection" in low or name.endswith("ConnectionError"):
        return "CONNECTION"
    if name in ("JSONDecodeError", "ValueError", "KeyError", "TypeError"):
        return "UNPARSEABLE_%s" % name
    return "OTHER_%s" % name

#: how often a vanished order may be re-asked about. These resolve on the
#: first or second attempt; the interval exists so a genuinely unreadable
#: order cannot become a hot loop against a venue that is already refusing.
#: SERIAL, and globally. Parked orders are the highest-priority read in the
#: system -- each one owes the ledger a fill and the book is uncertain until
#: every one of them is resolved -- but they are also the easiest to
#: multiply: eight parked orders at one read each per 5s is 1.6 req/s from a
#: path that only exists because the venue was already refusing us. One
#: request per interval, oldest first, however many are parked. Serial
#: resolution is slower and is not less safe: the book stays uncertain and
#: every submission stays refused for the whole of it, which is the same
#: state it would be in anyway.
PENDING_RESOLVE_S = 3.0
#: SOAK INSTRUMENTATION, off unless set, and ignored while armed.
#:
#: A disarmed process with no live orders reconciles once at boot and then
#: never again -- nothing sets uncertainty, nothing stalls, so the venue is
#: never re-read. That is correct for production and useless for proving
#: that the reconciliation path works against the real venue rather than
#: against a fake one, which is the whole point of a disarmed soak.
#:
#: When set, and ONLY while disarmed, the runner takes a periodic
#: authoritative read: two GETs, positions and open orders, through the
#: same rate limiter and the same InventoryStore.reconcile_from_venue that
#: an armed process uses. It creates nothing, cancels nothing and submits
#: nothing. Unset (the default) the behaviour is bit-for-bit what it was.
SOAK_RECONCILE_S = float(os.getenv("L5_LIVE_SOAK_RECONCILE_S", "0") or 0)


def _dec(v: Any) -> Optional[D]:
    """Decimal or None. NEVER zero: a field we cannot read is not a zero.

    Added because `D(str(None))` raises InvalidOperation, which surfaced as a
    bare CASH_HEADROOM_UNREADABLE and said nothing about which field was
    wrong.
    """
    if v is None:
        return None
    try:
        return D(str(v))
    except (ArithmeticError, ValueError, TypeError):
        return None


#: every check `Runner.budget_selfcheck` must produce when budgets are
#: configured. A missing one FAILS the report rather than being absent
#: from it: the first production run reported all_passed true with two
#: of these silently skipped, and a self-check that passes by not
#: running is worse than no self-check.
EXPECTED_SELFCHECKS = (
    "budgets_configured",
    "open_exhausts_at_its_ceiling",
    "complete_survives_exhausted_open",
    "backstop_binds_a_non_reducing_order",
    "backstop_exempts_a_reducing_order",
    "open_refused_without_completion_capacity",
    "overshoot_is_refused",
    "a_balance_inside_the_need_is_allowed",
    "complement_classified_as_COMPLETE",
    "a_reprice_classified_as_REPLACE",
)


def emit(kind: str, doc: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(doc, default=str,
                                      separators=(",", ":"))), flush=True)


#: how many markets ONE settlement sweep may read. None is unbounded, which
#: is what the engine has always done. See `settlement_sweep_cap`.
SETTLEMENT_SWEEP_CAP_ENV = "PMUS_SETTLEMENT_SWEEP_CAP"


#: a tick stage slower than this makes the pass worth a record. The loop
#: runs every ~3s, so a per-tick line would bury the log; the delay being
#: hunted is ~110s, so anything near it is far above this.
TICK_STAGE_REPORT_S = 5.0


class _StageClock:
    """Elapsed time between the tick's existing calls. REPORTING ONLY.

    WHY IT MEASURES RATHER THAN INFERS. The pre-preflight delay was
    attributed to `sweep_settlements` from a transitive source scan that
    matched the NAME inside two docstrings -- `two_sided_settle` and
    `two_sided_close` mention it and neither calls it. The real call sites
    are AFTER `maybe_preflight`, so the cap could not have helped, and the
    measured pre-preflight time stayed at 109.8s against a 92.4-110.9s
    baseline. This records which call actually holds the tick, from the
    running process.

    It never wraps a call. `mark` is invoked BETWEEN the existing
    statements, so every call expression in `tick` stays byte-identical --
    which matters because several tests drive `tick` against a stub that
    records method calls by name, and one asserts their order.
    """

    __slots__ = ("t0", "last", "stages")

    def __init__(self) -> None:
        self.t0 = self.last = time.monotonic()
        self.stages = []

    def mark(self, name: str) -> None:
        try:
            now = time.monotonic()
            self.stages.append((str(name), now - self.last))
            self.last = now
        except BaseException:                                  # noqa: BLE001
            pass

    def total_s(self) -> float:
        try:
            return self.last - self.t0
        except BaseException:                                  # noqa: BLE001
            return 0.0

    def report(self) -> Dict[str, Any]:
        """Slowest first, so the holder of the tick is the first thing read."""
        ranked = sorted(self.stages, key=lambda kv: -kv[1])
        return {
            "total_s": round(self.total_s(), 3),
            "slowest": ranked[0][0] if ranked else None,
            "slowest_s": round(ranked[0][1], 3) if ranked else None,
            "stages_s": {n: round(v, 3) for n, v in ranked},
            "labels": ("elapsed between the tick's EXISTING calls, before "
                       "maybe_preflight. Reporting only: it wraps nothing, "
                       "changes no order and cannot refuse anything."),
        }


def settlement_sweep_cap() -> Optional[int]:
    """How many markets one settlement sweep may read. None is unbounded.

    MEASURED 2026-10-27 on the running executor (deployment 4fc4e25b).
    `markets_with_exposure` had grown to **86** BTC15m markets reaching back
    to 2026-10-12, none of which can retire: they hold unmatched inventory,
    so `retire_settled_market` refuses and they stay in the book forever.
    The sweep reads one `/v1/markets/{slug}/bbo` per market, serially,
    through the shared 1.5 rps limiter -- and it runs BEFORE
    `maybe_preflight` and before the decision path. The egress trace for
    the 2130z window sits at exactly 1.5 flows/second from 21:31:26 to
    21:32:18, and the tick that began at window+48.8s did not reach
    preflight until window+142.8s. Preflight itself then PASSED on attempt
    1 in 1.574s against a two-sided websocket book 0.083s old.

    So the engine was never slow to decide. It spent the first ninety
    seconds of every window asleep in the limiter, re-reading the
    settlement of markets that settled a fortnight ago, and the decision
    path sat behind that. Worse, the sweep takes ~55s where
    SETTLEMENT_POLL_S is 10s, so every market is due again the instant the
    pass ends: a treadmill, not a backlog.

    A cap defers reads, it never drops them. The deferred markets are
    chosen by OLDEST READ FIRST, and a market never read sorts first of
    all, so the window that just closed -- the settlement we actually want
    -- is read on the next tick rather than behind a fortnight of backlog.

    Unset, blank, unparseable and non-positive all mean UNBOUNDED rather
    than some default cap, so a typo cannot silently throttle settlement
    detection. Unbounded keeps the iteration order the engine has always
    used, which is what makes the default byte-for-byte the frozen
    behaviour.
    """
    raw = (os.getenv(SETTLEMENT_SWEEP_CAP_ENV) or "").strip()
    if not raw:
        return None
    try:
        cap = int(raw)
    except Exception:                                          # noqa: BLE001
        return None
    return cap if cap > 0 else None


def residual_cap_k() -> Optional[D]:
    """The k for RESIDUAL_CAP, read from PMUS_RESIDUAL_CAP_K. None is OFF.

    Unset, blank, unparseable and non-positive all mean OFF rather than some
    default k. This selects a policy, and a policy a typo can switch on is
    not selectable. The value is echoed in the heartbeat, so which policy ran
    is read off the record and never inferred from what happened.
    """
    raw = (os.getenv("PMUS_RESIDUAL_CAP_K") or "").strip()
    if not raw:
        return None
    try:
        k = D(raw)
    except Exception:                                          # noqa: BLE001
        return None
    return k if k > 0 else None


def largest_recorded_clip(root: Optional[str] = None) -> Optional[D]:
    """The biggest single filled quantity the durable store has ever written.

    READ-ONLY, and for TELEMETRY only: the applied residual ceiling is always
    computed per market inside the gate. This answers the operator's
    question -- "at the clip this account has actually produced, which half
    of min(k * clip, absolute) decides?" -- which the per-market value cannot,
    because the market that will trade next has no clip yet.

    None when the store cannot be read, and None is reported as such rather
    than as zero: a zero clip would make the relative rule look tighter than
    anything the account has done.
    """
    try:
        base = root or os.getenv("L5_LIVE_ROOT", "/state/l5-live")
        path = os.path.join(base, "inventory.json")
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        vals = [D(str(v)) for v in (state.get("max_clip") or {}).values()]
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    return max(vals) if vals else D(0)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def engine_row(r: Mapping[str, Any], book: Optional[Mapping[str, Any]],
               recv: float) -> Dict[str, Any]:
    """One L5 fill in the shape the frozen rule reads, with the AUTHENTICATED
    book at our receipt instant attached.

    `pmus_snapshot_age_s` is the age of the book KNOWLEDGE: on a connected
    push stream that is the time since the socket last proved itself live.
    A dead or silent stream therefore ages past the frozen rule's own 2.0s
    bound and is refused as STALE; it is never repriced from anything later.
    """
    act = OBS.l5_to_pmus_action(r)
    ev_t = float(r.get("timestamp") or 0)
    bbo = None
    age = None
    if book:
        bbo = {"bid": book.get("bid"), "ask": book.get("ask"),
               "bid_shares": book.get("bid_shares"),
               "ask_shares": book.get("ask_shares")}
        sa = book.get("stream_age_s")
        age = 0.0 if sa is not None and sa <= FT.STALE_MAX_S else sa
    return {
        "action_key": OBS.action_key(r),
        "l5_event_utc": dt.datetime.fromtimestamp(
            ev_t, dt.timezone.utc).isoformat(),
        "receipt_utc": dt.datetime.fromtimestamp(
            recv, dt.timezone.utc).isoformat(),
        "latency_s": round(recv - ev_t, 3),
        "l5": {"slug": r.get("slug"), "tx": r.get("transactionHash"), **act},
        "copyability": {"l5_price": act.get("l5_price")},
        "pmus": {"bbo": bbo, "book_source": (book or {}).get("source"),
                 "stream_age_s": (book or {}).get("stream_age_s"),
                 "book_last_update_t": (book or {}).get("last_update_t")},
        "pmus_snapshot_age_s": age,
    }


class FrameFanout:
    """Deliver one authenticated book frame to SEVERAL observers.

    `AuthBook.frame_observer` is a single slot, already held by the
    executor's own `FM.ModelSession.ingest_frame` whenever `model_fills()`
    is on -- which is the default. The shadow policies need the same
    ladder, and the ladder is the only honest source of queue position.

    THE MONEY PATH'S OBSERVER KEEPS ITS EXACT BEHAVIOUR. It is held
    separately from the rest, called FIRST, and its exceptions propagate to
    `AuthBook` precisely as they did when it was the only observer -- so
    `observer_errors` still counts what it always counted. The additional
    observers are each wrapped individually, so one of them raising can
    neither stop the others nor reach the money path's.

    Order is not an implementation detail here: a shadow observer that
    somehow consumed time or raised must never come between the book and
    the modelling the executor depends on.
    """

    __slots__ = ("primary", "extra", "errors")

    def __init__(self, primary: Optional[Callable[[Any, float], None]] = None
                 ) -> None:
        self.primary = primary
        self.extra: List[Callable[[Any, float], None]] = []
        self.errors: Dict[str, int] = {}

    def add(self, fn: Callable[[Any, float], None]) -> None:
        self.extra.append(fn)

    def __call__(self, msg: Any, now: float) -> None:
        if self.primary is not None:
            self.primary(msg, now)          # unwrapped, exactly as before
        for fn in self.extra:
            try:
                fn(msg, now)
            except BaseException as exc:                        # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                k = type(exc).__name__
                self.errors[k] = self.errors.get(k, 0) + 1

    def status(self) -> Dict[str, Any]:
        return {"primary": self.primary is not None,
                "extra_observers": len(self.extra),
                "extra_errors": dict(self.errors),
                "labels": ("the money path's observer is called first and "
                           "unwrapped, so its behaviour is unchanged; the "
                           "shadow observers are isolated from it and from "
                           "each other")}


def _evidence_depth_ticks() -> Optional[int]:
    """How far behind the touch the probe rests, from the same env var it
    reads (`L5_TS_DEPTH_TICKS`, default 2). Reporting only: this feeds the
    book record so a reader can compare against the engine's own quote, and
    nothing here changes what the engine does. None if unreadable, which the
    record then reports as an absent behind-touch figure rather than zero.
    """
    try:
        return int(os.getenv("L5_TS_DEPTH_TICKS", "2"))
    except (ValueError, TypeError):
        return None


def _window_submits(runner: Any, market: str) -> List[Dict[str, Any]]:
    """The pre-submit records for one market, in submission order.

    Read from `FillCalibration.orders`, which already folds one slot per
    client order id -- so this adds no new bookkeeping and cannot disagree
    with what was published. Never raises into the reporting path.
    """
    out: List[Dict[str, Any]] = []
    try:
        orders = getattr(getattr(runner, "fill_calibration", None),
                         "orders", None) or {}
        for coid, row in orders.items():
            ps = (row or {}).get("pre_submit") or {}
            if str(ps.get("slug") or "") != str(market):
                continue
            if not str(coid).startswith("l5f-"):
                continue              # shadow and calibration ids are not ours
            out.append(dict(ps, coid=str(coid)))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return out
    out.sort(key=lambda r: (r.get("t") or 0.0, str(r.get("coid"))))
    return out


def _gates_realized(runner: Any) -> Any:
    """The account's realized figure for the campaign-budget telemetry block,
    or None. REPORTING ONLY, and it must never break a heartbeat: an
    unreadable store yields None, which simply omits the derived field."""
    try:
        return runner.store.realized_today()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def acc_stale_by_distance(runner: Any, now: float, slug: Any,
                          live: Mapping[str, Any],
                          *, skip: Any = ()) -> List[str]:
    """Which resting legs the book has LEFT far enough to recover.

    READ-ONLY: it reads the durable rows, the authenticated socket book and
    the submission budget, and returns client order ids. Cancelling is the
    caller's business and goes through the one deadline route.

    A FUNCTION, NOT A METHOD, and `runner` is read through getattr. Every
    duck-typed runner stub in the suite grafts `maybe_acc_deadline` onto an
    object carrying only what that method touched; a new attribute on `self`
    would break all of them, and worse, it would mean the recovery depended
    on a shape no caller declares. Anything missing simply yields no
    recovery.

    FAIL CLOSED EVERYWHERE. No book, a stale book, an unreadable tick, an
    unreadable budget or an unreadable row all decline the recovery and leave
    the order to its age deadline -- which is the behaviour that existed
    before this, so a fault here can only restore it.
    """
    from src import l5_two_sided_probe as TSP
    out: List[str] = []
    stats = getattr(runner, "stats", None)

    def count(key: str) -> None:
        if stats is not None:
            try:
                stats[key] += 1
            except BaseException:                              # noqa: BLE001
                pass

    try:
        t = getattr(runner, "transport", None)
        tick = getattr(t, "tick_size", None)
        if tick is None:
            return out
        book = getattr(runner, "book", None)
        snap = (book.snapshot(str(slug)) or {}) if book is not None else {}
        if not snap:
            return out
        # THE OPEN BUDGET, read once. A recovery that cannot be replaced
        # is strictly worse than one not taken, so an unreadable budget
        # declines rather than proceeds.
        try:
            rep = AUTH.class_budget_report(AUTH.ledger_path(),
                                           AUTH.experiment_id())
            obr = (rep.get("OPEN") or {}).get("remaining")
        except BaseException as exc:                       # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            obr = "UNREADABLE"
        used = int((getattr(runner, "acc_recoveries", None)
                    or {}).get(str(slug), 0))
        for coid, row in sorted(live.items()):
            if coid in set(skip or ()):
                continue
            if str(row.get("market") or "") != str(slug):
                continue
            sub = None
            try:
                st = dt.datetime.fromisoformat(str(row.get("t")))
                if st.tzinfo is not None:
                    sub = st.timestamp()
            except BaseException:                          # noqa: BLE001
                sub = None
            if sub is None:
                continue       # the time path halts on this; not ours
            v = TSP.recover_stale_leg(
                side=str(row.get("side")), own_px=row.get("price"),
                qty=row.get("qty"), remaining=row.get("remaining"),
                state=row.get("state"), submitted_t=sub, now=float(now),
                bid=snap.get("bid"), ask=snap.get("ask"), tick=tick,
                depth_ticks=TSP.depth_ticks(),
                book_age_s=snap.get("stream_age_s"),
                recoveries_used=used + len(out),
                open_budget_remaining=obr)
            count("acc_recover:%s" % (v.get("reason") or "RECOVER"))
            # LOG-ONLY. A leg refused RECOVERY_BUDGET_SPENT passed every
            # other test and was declined by the cap alone, which is the
            # one quantity the existing records cannot show: `stale_by_
            # distance: []` reads identically whether nothing drifted or
            # the budget was gone. Counting these says whether a second
            # recovery is ever even wanted. Nothing is cancelled, replaced,
            # priced or gated by this branch; `out` is untouched.
            if str(v.get("reason")) == "RECOVERY_BUDGET_SPENT":
                try:
                    _rnd = getattr(getattr(runner, "executor", None),
                                   "probe_round", lambda _m: None)(str(slug))
                except BaseException as exc:               # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    _rnd = None
                emit("L5_ACC_RECOVER_REFUSED", dict(
                    v, market=str(slug), round=_rnd, coid=coid,
                    venue_order_id=row.get("venue_order_id"),
                    labels=("REPORTING ONLY. This leg qualified for a "
                            "stale-by-distance recovery on every other "
                            "test and was refused solely because the "
                            "per-market recovery budget was spent. No "
                            "order, price, cancellation, risk gate or "
                            "durable state is affected by this record.")))
            if v.get("recover"):
                out.append(coid)
                emit("L5_ACC_RECOVER", dict(
                    v, market=str(slug), coid=coid,
                    venue_order_id=row.get("venue_order_id"),
                    labels=("PROPOSED. Nothing is cancelled by this "
                            "record: the deadline route polls the venue, "
                            "cancels by exact venue order id and proves "
                            "the cancel terminal before any "
                            "replacement is priced.")))
    except BaseException as exc:                           # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        # recovery is an OPTIMISATION. It must never be able to take down the
        # deadline that bounds real exposure.
        count("acc_recover:LAYER_FAILED:%s" % type(exc).__name__)
        return []
    return out


#: HOW OLD AN L5 ACTION MAY BE AND STILL REACH THE EXECUTOR, in seconds.
#: One BTC15m window. The durable cursor means a restart no longer throws
#: away the page it comes back to, which is the point -- but it also means a
#: process that was down must not now act on an hour-old action as though it
#: had just happened. This bound can only refuse.
MAX_ACTION_AGE_ENV = "L5_LIVE_MAX_ACTION_AGE_S"
DEFAULT_MAX_ACTION_AGE_S = 900.0
#: how much dedupe history the cursor keeps. The feed serves the newest 200
#: rows, so anything older than this cannot come back round to be
#: double-counted; the cap is a second bound so the file cannot grow without
#: limit on a volume whose exhaustion already cost this system 17 days.
CURSOR_HORIZON_S = 86400.0
CURSOR_MAX_KEYS = 20000


def _max_action_age_s() -> float:
    """The staleness bound, from the environment, floored at one tick.

    A misconfigured value must not widen the bound into "accept anything":
    an unparseable or non-positive setting falls back to the default.
    """
    raw = str(os.getenv(MAX_ACTION_AGE_ENV, "")).strip()
    if not raw:
        return DEFAULT_MAX_ACTION_AGE_S
    try:
        v = float(raw)
    except ValueError:
        return DEFAULT_MAX_ACTION_AGE_S
    return v if v > 0 else DEFAULT_MAX_ACTION_AGE_S


class IngestCursor:
    """Durable dedupe + seed state for the L5 activity feed.

    WHAT BROKE WITHOUT IT. `seen` was a bare in-memory set and `seeded` a
    bare in-memory bool. The first poll of every process marked the entire
    200-row page as history and returned, by design -- "the first read is
    history, not a signal" -- and that design is right exactly once. Held in
    memory it re-ran on every boot, so each restart silently discarded up to
    200 of L5's most recent actions, and a redeploy during a live window
    dropped whatever L5 did around it with no counter and no log line.

    So the state goes on the mounted volume: seeded once ever, dedupe keys
    bounded by age and count, written atomically. A restart RESUMES. Rows
    already handled are still refused as duplicates, so resuming cannot
    produce a second executable decision for one L5 action -- the two
    properties have to hold together, and the regression tests assert both.

    It holds no transport, decides nothing about an order, and every method
    is failure-tolerant: a cursor that cannot be read starts empty and
    UNSEEDED, which re-seeds (discarding history) rather than replaying it.
    That is the safe direction: a missed action costs an opportunity, a
    replayed one costs money.
    """

    def __init__(self, path: str, *, horizon_s: float = CURSOR_HORIZON_S,
                 max_keys: int = CURSOR_MAX_KEYS) -> None:
        self.path = path
        self.horizon_s = float(horizon_s)
        self.max_keys = int(max_keys)
        self.seeded = False
        self.seeded_at: Optional[float] = None
        self.seeded_count: Optional[int] = None
        #: action_key -> the row's own timestamp (never our receipt time)
        self._keys: Dict[str, float] = {}
        self._dirty = False
        self.load_error: Optional[str] = None
        self.save_error: Optional[str] = None
        self.loaded = False

    # ---------------------------------------------------------------- io
    def load(self) -> "IngestCursor":
        try:
            with open(self.path) as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            self.loaded = True
            return self
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # UNREADABLE IS UNSEEDED. Never "seeded with no keys", which
            # would admit the whole page as fresh and replay history live.
            self.load_error = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            self.loaded = True
            return self
        if isinstance(doc, Mapping):
            self.seeded = bool(doc.get("seeded"))
            sa, sc = doc.get("seeded_at"), doc.get("seeded_count")
            self.seeded_at = float(sa) if isinstance(sa, (int, float)) else None
            self.seeded_count = int(sc) if isinstance(sc, int) else None
            keys = doc.get("keys")
            if isinstance(keys, Mapping):
                for k, v in keys.items():
                    try:
                        self._keys[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
        self.loaded = True
        return self

    def save(self) -> None:
        if not self._dirty:
            return
        self._prune()
        doc = {"schema": "l5_ingest_cursor/v1",
               "seeded": self.seeded, "seeded_at": self.seeded_at,
               "seeded_count": self.seeded_count,
               "written_utc": now_utc().isoformat(),
               "keys": self._keys,
               "labels": ("dedupe keys are l5_pmus_realtime_observer."
                          "action_key values -- the whole economic identity "
                          "of a fill, not its transaction hash. seeded is "
                          "set ONCE EVER; a restart resumes.")}
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(doc, fh, default=str)
            os.replace(tmp, self.path)
            self._dirty = False
            self.save_error = None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # A CURSOR WE CANNOT PERSIST MUST NOT STOP US TRADING, and must
            # not pretend it persisted. In-memory dedupe still holds for
            # this process; the next restart re-seeds.
            self.save_error = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _prune(self) -> None:
        if not self._keys:
            return
        newest = max(self._keys.values())
        cut = newest - self.horizon_s
        if cut > 0:
            self._keys = {k: v for k, v in self._keys.items() if v >= cut}
        if len(self._keys) > self.max_keys:
            keep = sorted(self._keys.items(), key=lambda kv: kv[1],
                          reverse=True)[:self.max_keys]
            self._keys = dict(keep)

    # ------------------------------------------------------------ state
    def known(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str, timestamp: Any) -> None:
        try:
            t = float(timestamp)
        except (TypeError, ValueError):
            # an unparseable timestamp still has to be deduped, or it is
            # re-handled on every single poll forever
            t = time.time()
        self._keys[str(key)] = t
        self._dirty = True

    def mark_seeded(self, count: int, now: Optional[float] = None) -> None:
        self.seeded = True
        self.seeded_at = float(now if now is not None else time.time())
        self.seeded_count = int(count)
        self._dirty = True

    def report(self) -> Dict[str, Any]:
        return {"path": self.path, "seeded": self.seeded,
                "seeded_at": self.seeded_at,
                "seeded_count": self.seeded_count,
                "keys": len(self._keys), "loaded": self.loaded,
                "load_error": self.load_error, "save_error": self.save_error,
                "durable": self.save_error is None and self.load_error is None,
                "labels": ("seeded true with a seeded_at from a PREVIOUS "
                           "boot is the proof that a restart resumed "
                           "instead of discarding the feed page.")}


class Runner:
    def __init__(self, *, root: str, book: AB.AuthBook,
                 http: Optional[Any] = None, lam: D = MANIFEST_LAMBDA,
                 feed=OBS.l5_activity, clock=time.time) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.book = book
        self.http = http
        #: venue-confirmed bounds per window start. None means the venue
        #: did not confirm, which refuses arming rather than falling back.
        self.window_bounds: Dict[int, Optional[Dict[str, Any]]] = {}
        self.arm_claim: Optional[Dict[str, Any]] = None
        #: fans one book frame out to the money path's observer and the
        #: shadow policies' ladder; see FrameFanout.
        self.fanout: Optional[FrameFanout] = None
        #: the authenticated ladder the shadow policies price queue
        #: position against. None means unknown queue, which the fill
        #: model refuses to fill except on a sweep.
        self.shadow_ladder: Optional[Any] = None
        #: the shared intent-level boundary. Holds nothing of its own: every
        #: safety fact it reads comes from this runner's own objects.
        self.intent_gate = GATE.IntentGate(self)
        #: ORDER-ID-LINKED CALIBRATION LEDGER. Observational: holds no
        #: transport and cannot refuse a submission. Built here so the
        #: executor can be handed it, and so the shadow path can exercise
        #: the capture mechanism against REAL venue data before any live
        #: order depends on it.
        self.fill_calibration = None
        try:
            self.fill_calibration = CALIB.CalibrationRecorder(
                self.root, emit=emit)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["fill_calib_init_error:%s" % type(exc).__name__] += 1
        # THE INDEPENDENT SHADOW DECISION SOURCE. Built lazily, holds no
        # transport, and is consulted by nothing on the money path -- see
        # `shadow_tick`. Named with a leading underscore so no existing
        # code path can reach it by guessing an attribute name.
        #: ONE CONTROLLER PER POLICY, each with its own inventory in its own
        #: state file. The baseline is kept running beside any candidate so
        #: the comparison is live rather than remembered, and so a candidate
        #: cannot be credited with the baseline's fills.
        self._shadow: Dict[str, SHADOW.ShadowController] = {}
        self._shadow_off = False
        self._shadow_last_key: Dict[str, Any] = {}
        self._shadow_last_emit_t: Dict[str, float] = {}
        self._shadow_settle_t: Dict[str, float] = {}
        #: shadow calibration records this process opened, by coid
        self._calib_shadow_seen: Dict[str, str] = {}
        self._shadow_last_rec: Optional[Dict[str, Any]] = None
        self._shadow_last_rec_t = 0.0
        self.last_orphan_sweep_t = 0.0
        self.feed = feed
        self.clock = clock
        self.store = LX.InventoryStore(os.path.join(root, "inventory.json"))
        repair_orphaned_keys(self.store)
        repair_ledger_from_venue(self.store)
        # settlement deltas for markets already retired. Separate from
        # the repair above because it must never re-settle a market.
        apply_ledger_adjustments(self.store)
        rebase_contaminated_accounting(self.store)
        # ONE MARKET, ONCE. Not a migration and not a recurring rebase: the
        # list below holds exactly one entry, it is keyed by correction_id in
        # `settlement_corrections`, it refuses unless the stored row still
        # matches that entry's expected contents field for field, and it
        # refuses any delta that would move realized toward zero. Every boot
        # after the first therefore logs a refusal and changes nothing.
        apply_settlement_corrections(self.store)
        # OWNERSHIP, PUBLISHED READ-ONLY. The watchdog cannot cancel one of
        # our orders without a durable record that it is ours -- account-wide
        # open orders say what is resting, not whose it is, and this account
        # is SHARED. This serves `live_orders` over GET and nothing else; it
        # is off entirely unless L5_ORDER_REGISTRY_PORT is set, so the default
        # changes no behaviour. It never writes.
        self.registry_server = None
        try:
            from src import l5_order_registry_server as REG
            self.registry_server = REG.serve_from_env(self.store)
            if self.registry_server is not None:
                emit("L5_LIVE_ORDER_REGISTRY", {
                    "serving": "/" + REG.REGISTRY_KEY,
                    "port": os.getenv(REG.PORT_ENV),
                    "labels": ("GET-only. The watchdog reads this to learn "
                               "which venue order ids are ours; a claim here "
                               "is necessary and NOT sufficient, because the "
                               "watchdog also requires the venue's own row "
                               "to report AUTOMATIC and the same market.")})
        except Exception as exc:                               # noqa: BLE001
            emit("L5_LIVE_ALARM", {
                "kind": "order_registry_unavailable",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
                "labels": ("the watchdog cannot establish ownership, so it "
                           "cannot cancel. It can still latch the halt.")})
        self.limits = LX.SafetyLimits(**PROBE_LIMITS)
        self.lam = lam
        #: DURABLE INGEST STATE, on the mounted volume beside the ledger.
        #: `seen`/`seeded` remain as read-only views below so nothing that
        #: already reads those names changes meaning.
        self.cursor = IngestCursor(
            os.path.join(root, "l5_ingest_cursor.json")).load()
        self.max_action_age_s = _max_action_age_s()
        #: every slug the feed has served this process, admitted or not
        self.ingest_slugs: "collections.Counter[str]" = collections.Counter()
        #: the three clocks `ingest_report` publishes. None means NEVER.
        self.last_row_t: Optional[float] = None
        self.last_btc15m_t: Optional[float] = None
        self.last_admitted_t: Optional[float] = None
        #: the newest L5 EVENT time the feed has ever served, by L5's clock
        #: rather than ours -- a stale feed is a page whose newest event
        #: stops advancing while our own poll clock keeps ticking.
        self.newest_row_event_t: Optional[float] = None
        emit("L5_INGEST_CURSOR", dict(
            self.cursor.report(),
            max_action_age_s=self.max_action_age_s,
            resumed=bool(self.cursor.seeded),
            labels=("resumed true means this boot did NOT discard the feed "
                    "page as history. Emitted at construction so a restart "
                    "that silently re-seeded is visible in the log that "
                    "starts the run.")))
        self.ws: Optional[int] = None
        self.transport: Optional[T.PmusTransport] = None
        self.executor: Optional[LX.FaithfulExecutor] = None
        self.reconciled = False        # venue has not confirmed our book
        #: authoritative buying power, and when it was read. None means
        #: no reading -- never unlimited.
        self.available_cash_usd = None
        self.available_cash_t = None
        #: MODELLED maker-fill instrumentation. Read-only: it observes the
        #: book and the executor's own intents and books NOTHING real.
        self.model: Optional[FM.ModelSession] = None
        self.last_reconcile_t = 0.0
        self.last_progress_t = 0.0
        self.preflight_status = "NOT_RUN"
        self.last_preflight_t = 0.0
        #: _BLOCKERS_UNSET, not None, because a preflight that dies in the
        #: fatal-exception path produces a report with NO
        #: blocking_failures key at all. Initialised to None, the
        #: "only log when what blocks it CHANGES" test read None != None
        #: and stayed quiet forever: 1730z failed preflight five times
        #: and emitted not one L5_LIVE_PREFLIGHT line, so the reason was
        #: unobservable. A sentinel makes the FIRST outcome always differ,
        #: and a repeat of the same blockers still says nothing.
        self.last_blockers: Any = _BLOCKERS_UNSET
        self.win = collections.Counter()
        self.first_action_t: Optional[float] = None
        self.stats = TimedCounter(clock=clock)
        self.tape = os.path.join(root, "decisions.jsonl")
        #: the tick's clock; the executor's staleness is judged against it
        self.now_t = clock()
        #: per-key last-read clocks, so cadence is bounded per THING rather
        #: than per tick. Without these the request rate scales with the
        #: number of live orders and open markets, which is exactly
        #: backwards: the busier we are, the harder we hit the venue.
        self.last_order_poll: Dict[str, float] = {}
        self.last_settlement_poll: Dict[str, float] = {}
        self.last_pending_resolve: Dict[str, float] = {}
        #: the single global slot the parked-order reads take turns in
        self.last_pending_resolve_t = 0.0
        #: preflight's escalating retry index, reset on every window roll
        self.preflight_attempts = 0
        #: how many stale-by-distance first-leg recoveries each market has
        #: spent, PER MARKET and never reset -- each one costs an OPEN
        #: submission and surrenders queue priority, so the ceiling is for
        #: the market's whole life rather than per attempt. Keyed by slug, so
        #: a new window starts at zero by construction.
        self.acc_recoveries: Dict[str, int] = {}
        #: per-market lifecycle rows, for the window accounting report. Bounded
        #: so a long-running process cannot grow it without limit; the only
        #: consumer is `l5_window_accounting`, which cannot trade.
        self.win_lifecycle: Dict[str, List[Dict[str, Any]]] = {}
        #: the latest report per market, provisional then confirmed. One entry
        #: per window, overwritten in place, so a window is never double
        #: counted in the rolling totals.
        self.window_reports: Dict[str, Dict[str, Any]] = {}
        #: last time the independent latch was consulted for the
        #: CANCEL decision. 0.0, so the first tick with exposure
        #: always asks.
        self.last_halt_sweep_t = 0.0
        #: SHADOW-ONLY two-sided completion evaluator for this window, and
        #: the finished ones still waiting for an authoritative settlement
        #: to score their residual. Reads the book and the trade prints;
        #: touches no store, no transport, no limit and no gate.
        self.two_sided: Optional[TS.TwoSidedShadow] = None
        self.last_two_sided_report_t = 0.0
        self.last_ts_completion_t = 0.0
        self.last_two_sided_settle_t = 0.0
        #: consecutive settlement-read failures per slug. A counter of
        #: ATTEMPTS says nothing about convergence; this says which slug.
        self.two_sided_settle_attempts: Dict[str, int] = {}
        #: DURABLE and shadow-only. Scoring a parked window must not depend on
        #: the live ledger having exposure in it -- in shadow it never does --
        #: and must survive a restart without scoring anything twice.
        self.two_sided_ledger = TS.ShadowLedger(
            os.path.join(root, "two_sided_shadow.json"))

    # ------------------------------------------------------------ window
    def halted(self) -> bool:
        """The LOCAL env halt only. Not the effective gate -- see
        `submission_gate`, which is what refuses a submission."""
        return bool(os.getenv("L5_LIVE_HALT", "").strip())

    def account_exposure_lines(self) -> Dict[str, Any]:
        """Account-wide worst-case exposure and the capacity left under it.

        READ-ONLY and reported for verification. MISSING IS NEVER ZERO: if
        the figure cannot be computed the reason is reported and capacity is
        None, never a comfortable number.
        """
        try:
            out = self.store.account_wide_risk_by_market()
        except BaseException as exc:                           # noqa: BLE001
            return {"ok": False,
                    "reason": "EXPOSURE_CALL_FAILED:%s" % type(exc).__name__,
                    "capacity_usd": None}
        if not out.get("ok"):
            return {"ok": False, "reason": out.get("reason"),
                    "capacity_usd": None,
                    "labels": ("exposure is UNKNOWN, not zero; the gate "
                               "refuses in this state")}
        total = D(str(out["total_usd"]))
        cap = self.limits.max_total_exposure_usd
        if cap is None:
            # A CONFIGURED-BUT-UNREADABLE CEILING IS NOT AN ABSENT ONE.
            # Reporting capacity against a ceiling nobody can read would
            # invent headroom; the gates refuse independently.
            return {"ok": False,
                    "reason": "ACCOUNT_EXPOSURE_CEILING_UNREADABLE",
                    "capacity_usd": None,
                    "account_total_worst_case_usd": str(total),
                    "labels": ("L5_MAX_TOTAL_EXPOSURE_USD is set to a value "
                               "that cannot be used. Submissions refuse; the "
                               "ceiling is NOT defaulted.")}
        per_market = {k: str(v) for k, v in out["risk_by_market"].items()}
        worst = max(out["risk_by_market"].items(),
                    key=lambda kv: kv[1], default=(None, D(0)))
        return {
            "ok": True,
            "markets_counted": out["markets"],
            "per_market_worst_case_usd": per_market,
            "account_total_worst_case_usd": str(total),
            "max_total_exposure_usd": str(cap),
            "capacity_usd": str(cap - total),
            "largest_market": worst[0],
            "largest_market_usd": str(worst[1]),
            "max_usd_per_market": str(self.limits.max_usd_per_market),
            "labels": ("worst-case cash loss over both settlements, every "
                       "market with a position OR a resting order, manual "
                       "and L5 alike. Each market counted once. capacity_usd "
                       "is what remains under the account-wide ceiling."),
        }

    def submission_gate(self) -> Dict[str, Any]:
        """Can this process put an order on the venue right now, and if not,
        which gate says no -- in the order the decision path applies them.

        READ-ONLY and never cached: a latch that was clear a minute ago is
        not evidence. A read that fails is reported BLOCKED, matching the
        fail-closed behaviour of the gate itself rather than contradicting
        it.
        """
        out: Dict[str, Any] = {"blocked": True, "reason": "UNKNOWN",
                               "shadow_lock": None, "halt_latch": None,
                               "owner_arm_token": None, "armed": None}
        try:
            out["shadow_lock"] = shadow_only()
            out["owner_arm_token"] = bool(Q1.armed())
            out["armed"] = bool(self.transport and self.transport.armed)
            hg = getattr(self.executor, "halt_gate", None)
            if hg is None:
                out["halt_latch"] = "NO_GATE"
            else:
                try:
                    # the gate's OWN reading, so the heartbeat and the
                    # refusal can never disagree: `refuse_reason` is the
                    # string the submission path logs, and None is the only
                    # permitted answer.
                    why = hg.refuse_reason()
                    out["halt_latch"] = "CLEAR" if why is None else "HALT"
                    out["halt_why"] = why
                    # THE LATCH'S DIGEST, FREE. refuse_reason() just read the
                    # latch over HTTP; telemetry() reports the sha256 of those
                    # same bytes and a few labels, never the raw contents and
                    # never `evidence`. This is how the owner obtains the
                    # digest that the reset's gate 2 demands WITHOUT stopping
                    # the watchdog -- the failure of 2026-10-19, which cost
                    # 5m53s of independent cancellation and produced no
                    # digest at all.
                    try:
                        out["halt_latch_telemetry"] = hg.telemetry()
                    except Exception:                          # noqa: BLE001
                        out["halt_latch_telemetry"] = None
                except Exception as exc:                       # noqa: BLE001
                    out["halt_latch"] = "UNREADABLE"
                    out["halt_error"] = "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:80])
            if out["shadow_lock"]:
                out["reason"] = "SHADOW_ONLY_LOCK"
            elif out["halt_latch"] in ("HALT", "UNREADABLE", "NO_GATE"):
                out["reason"] = "HALT_GATE:%s:%s" % (
                    out["halt_latch"], out.get("halt_why") or "")
            elif not out["owner_arm_token"]:
                out["reason"] = "NO_OWNER_ARM_TOKEN"
            elif not out["armed"]:
                out["reason"] = "NOT_ARMED"
            else:
                out["blocked"] = False
                out["reason"] = "OPEN"
        except Exception as exc:                               # noqa: BLE001
            out["reason"] = "GATE_UNREADABLE:%s" % type(exc).__name__
        out["labels"] = ("the EFFECTIVE submission gate, read fresh. blocked "
                         "true means no order can reach the venue. An "
                         "unreadable latch is BLOCKED, as the gate itself "
                         "fails closed. Cancellation is NOT gated by any of "
                         "these and stays available while blocked.")
        return out

    def effective_cash_gate(self) -> Dict[str, Any]:
        """The cash bound actually enforced, and how it differs from the
        per-market worst-case ceiling.

        These are two different limits and were being read as one. The $5 is
        `max_usd_per_market`, a WORST-CASE LOSS ceiling on SafetyLimits. The
        $1 mandate is `max_cash_deployed_usd` on ValidationGates, a
        run-scoped bound on GROSS CASH OUT with NO_RECYCLING. Reported from
        the gate object, with None meaning the gates are not active at all --
        which is a refusal to arm, not a default.
        """
        vg = getattr(self.executor, "validation_gates", None)
        val = getattr(vg, "max_cash_deployed_usd", None)
        return {
            "max_cash_deployed_usd": (None if val is None else str(val)),
            "source": ("ValidationGates object" if val is not None
                       else "GATES NOT ACTIVE"),
            "cash_policy": str(getattr(vg, "cash_policy", None) or ""),
            "max_unmatched_contracts": str(getattr(
                vg, "max_unmatched_contracts", "") or ""),
            "max_usd_per_market": str(self.limits.max_usd_per_market),
            "distinction": ("max_cash_deployed_usd is the run-scoped GROSS "
                            "CASH OUT bound (the mandate). max_usd_per_market "
                            "is a per-market WORST-CASE LOSS ceiling. They are "
                            "different quantities and the smaller one binds; "
                            "neither is the other's default."),
        }

    def roll(self, now: float) -> None:
        ws = AB.window_start(now)
        if ws == self.ws:
            return
        if self.ws is not None:
            self.model_close()
            self.close_window()
            # the shadow's own window close. After close_window, which must
            # not be delayed by it, and before self.ws moves.
            self.shadow_close()
        # a claim is for ONE window; it does not carry into the next one.
        self.arm_claim = None
        self.ws = ws
        slug = AB.slug_for(ws)
        # THE OUTGOING WINDOW'S PROVISIONAL REPORT, before anything about the
        # new one is built. Fill counts and provisional inventory are knowable
        # now; realized P&L is not, and is deliberately absent until the venue
        # settles the market -- at which point `emit_window_accounting` is
        # called again for the SAME market and rewrites that one entry.
        prev = getattr(self, "last_rolled_slug", None)
        if getattr(self, "window_reports", None) is None:
            self.window_reports = {}
        if prev and str(prev) != str(slug):
            if (self.window_reports.get(str(prev)) or {}
                    ).get("phase") != "CONFIRMED":
                self.emit_window_accounting(str(prev), settlement=None)
        self.last_rolled_slug = str(slug)
        self.two_sided_close()
        self.two_sided = TS.TwoSidedShadow(slug, window_start=float(ws))
        # MODELLED fill session for this window. Attaching the ladder
        # observer here is what gives a maker quote a queue position; it is
        # detached-by-default and feeds nothing but this model.
        if model_fills():
            # the MODELLED book is held to the EFFECTIVE BINDING live limit,
            # which is the TIGHTER of the two independent constraints:
            #
            #   SafetyLimits.max_abs_residual          10  (PROBE_LIMITS)
            #   ValidationGates.max_unmatched_contracts 5  (additive gate)
            #
            # ValidationGates is consulted AFTER SafetyLimits and can only
            # ADD refusals, so live is bounded by min(10, 5) = 5. The first
            # version passed SafetyLimits alone and so modelled sequences
            # were LOOSER than live would permit -- the exact defect the cap
            # was added to remove. Derived, never hardcoded, so it tracks
            # either limit if either is reconfigured.
            self.model = FM.ModelSession(
                slug, max_abs_residual=self.effective_residual_cap())
            primary = self.model.ingest_frame
        else:
            self.model = None
            primary = None
        # THE FAN-OUT. The money path's observer above is handed to the
        # fanout as its PRIMARY and is called first and unwrapped, so its
        # behaviour is byte-for-byte what it was when it owned the slot.
        # The shadow policies then attach their own ladder observer, which
        # is what gives a shadow quote a real queue position instead of a
        # touch-through guess. A fanout with no observers at all behaves
        # like the None it replaces.
        self.fanout = FrameFanout(primary)
        self.shadow_attach_ladder(slug)
        self.evidence_attach(slug)
        self.book.frame_observer = (self.fanout
                                    if (primary is not None
                                        or self.fanout.extra) else None)
        # THE SAME LIMITER, explicitly. PmusTransport would reach for the
        # process-wide one anyway; naming it here is the point -- this line
        # is where the old code silently created a fresh token bucket and a
        # fresh 429 cool-off every fifteen minutes.
        self.transport = T.PmusTransport(
            market_slug=slug, http=self.http, limiter=T.shared_limiter())
        # OWNERSHIP FOR CANCELLATION, from the durable ledger. Installed here,
        # before the transport is given to anything, so cancel can establish
        # that an order is ours whether or not this process is armed -- which
        # is the state every orphan path runs in. Without it the transport
        # refuses to cancel at all: absent is not permission.
        self.transport.cancel_owner_oracle = self.store.owns_venue_order_id
        # THE SOCKET, AS PREFLIGHT'S FALLBACK EVIDENCE OF A READABLE BOOK.
        # The venue does not serve a 15m market's REST book for its first
        # minutes; the authenticated socket serves it from under a second.
        # Installed here, on the transport this window will use, so the
        # fallback is for THIS market and no other. It is read-only and the
        # transport consults it only after a 404 has exhausted the REST
        # retries -- see PmusTransport._ws_book_substitute.
        self.transport.ws_book = self.book
        # STRUCTURAL DISARM, applied before the executor is given the
        # transport, so there is no window in which it holds a
        # submit-capable object.
        if shadow_only():
            self.transport = ShadowOnlyTransport(self.transport)
            emit("L5_LIVE_SHADOW_LOCK", {
                "locked": True, "env": SHADOW_ONLY_ENV,
                "blocked": list(ShadowOnlyTransport.BLOCKED),
                "cancel_still_permitted": True,
                "labels": ("submit() raises NotArmed regardless of "
                           "PMUS_Q1_LIVE/SESSION_ID/ARM. The owner's "
                           "live authorization is unchanged; this "
                           "lock sits in front of it.")})
        # THE $1/SHARE PLACEHOLDER AUDIT, READ-ONLY and OFF by default.
        # `leg_cost_basis_unknown` legs arrived as a venue NET position this
        # executor never booked, so no order of ours exists to ask about and
        # settlement priced them at the $1/share upper bound -- which is what
        # makes realized_attributed an ESTIMATE rather than a measurement. It
        # reads the account activity feed ONCE and reports what the fills
        # actually were, split L5-owned vs OWNER_MANUAL by our own persisted
        # client order ids. It WRITES NOTHING and cannot: applying its finding
        # would move realized toward zero, which correct_settled_market
        # refuses as CORRECTION_WOULD_RELAX_LOSS_HALT and which needs its own
        # owner decision. Gated so a normal boot does not spend the feed walk.
        # 1 TICK vs 2 TICKS, from the ACTUAL order and fill records on this
        # volume. READ-ONLY and OFF by default. It exists because a deeper
        # quote always shows a larger QUOTED edge -- 0.95 against 0.97 on a
        # one-tick book -- so comparing quoted edges would always "prove"
        # that deeper is better and prove nothing. It reports per depth the
        # measured single-leg and both-leg fill rates, the realized edge from
        # recorded prices and commissions, and withholds any verdict below
        # the sample thresholds. It WRITES NOTHING.
        if str(os.getenv("L5_DEPTH_COMPARISON", "")).strip().upper() == "YES":
            try:
                from src import l5_depth_comparison as DC
                emit("L5_DEPTH_COMPARISON", DC.from_tape(self.tape))
            except BaseException as exc:                       # noqa: BLE001
                emit("L5_DEPTH_COMPARISON_FAILED", {
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    "labels": ("the comparison is read-only; a failure "
                               "changes nothing and never alters the "
                               "ledger")})
        if str(os.getenv("L5_COST_BASIS_AUDIT", "")).strip().upper() == "YES":
            try:
                from src import l5_cost_basis_audit as CBA
                result = CBA.audit(self.store, self.transport)
                emit("L5_COST_BASIS_AUDIT", result)
                # THE MEASURED-COST CORRECTION, from the walk that JUST ran.
                # INSPECT is the default and writes nothing; a batch id in
                # L5_COST_BASIS_APPLY is the owner's authorization to write.
                # Boot only, so it cannot run twice in a session, and each
                # leg still passes all ten guards inside the store's verb.
                mode = CBA.apply_mode()
                emit("L5_COST_BASIS_APPLY", CBA.apply_cost_basis_corrections(
                    self.store, result,
                    batch_id=(None if mode == CBA.APPLY_INSPECT else mode),
                    manifest=CBA.batch_manifest()))
            except BaseException as exc:                       # noqa: BLE001
                emit("L5_COST_BASIS_AUDIT_FAILED", {
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    "labels": ("the audit is read-only. The correction is "
                               "guarded leg by leg and INSPECT by default, so "
                               "a failure here leaves the ledger as it was")})
        # THE L5 BASELINE EPOCH, struck ONCE and only from authoritative
        # reads (OWNER 2026-10-16). It is NOT a claim that historical L5
        # losses were zero: establish_l5_baseline snapshots and preserves
        # every pre-T figure, including the unknown-cost provenance keys. It
        # is the instant from which ATTESTED L5 settlements are counted.
        #
        # Every read is required and none may be inferred. Cash comes from
        # /v1/account/balances, the positions from /v1/portfolio/positions,
        # L5-owned open orders from our OWN durable live-order record (the
        # account's open orders say what is resting, not whose it is, and
        # this account is SHARED), and the outstanding obligation from the
        # same helper the cash gate uses. A failure strikes NO baseline, so
        # the scoped ceilings keep refusing L5_SCOPED_REALIZED_UNAVAILABLE --
        # the safe direction.
        if str(os.getenv("L5_ESTABLISH_BASELINE", "")).strip().upper() == "YES":
            try:
                acct = self.transport.account() or {}
                # THE DEFECT THIS REPLACES. /v1/account/balances returns
                # `balances` as a LIST of entries carrying buyingPower; the
                # first version read availableBalance/cash/etc off the
                # TOP-LEVEL dict, never looked inside balances[], and so
                # produced cash=None and refused BASELINE_READS_UNAVAILABLE
                # at 02:39:36Z with venue_positions_read=true. The runner's
                # own cash refresh already does this correctly, and this now
                # uses the identical proven parser rather than guessing key
                # names. Still None on a genuinely unreadable balance, which
                # still refuses -- the validation is unchanged.
                cash = T.PmusTransport.buying_power(acct)
                pos = self.transport.positions()
                # from the store's OWN committed-cash record, not from the
                # executor (which is constructed below and would therefore
                # have reported a silent zero -- missing is never zero).
                oblig = sum((D(str(v)) for v in
                             (self.store.committed() or {}).values()), D(0))
                mine = sorted(str((r or {}).get("venue_order_id") or "")
                              for r in (self.store.live_orders() or {}).values()
                              if (r or {}).get("venue_order_id"))
                res = self.store.establish_l5_baseline(
                    cash_usd=cash, l5_open_orders=mine,
                    positions={str(k): str(v) for k, v in
                               (self.store.state.get("positions") or {}).items()},
                    outstanding_obligations_usd=oblig,
                    source=self.store.L5_BASELINE_SOURCE)
                emit("L5_BASELINE_ESTABLISHED" if res.get("established")
                     else "L5_BASELINE_REFUSED",
                     dict(res, venue_positions_read=(pos is not None)))
            except BaseException as exc:                       # noqa: BLE001
                emit("L5_BASELINE_REFUSED", {
                    "established": False,
                    "reason": "BASELINE_READ_FAILED",
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    "labels": ("no baseline was struck, so the L5-scoped "
                               "ceilings keep refusing -- the safe direction")})
        self.executor = LX.FaithfulExecutor(
            self.store, self.limits, transport=self.transport, lam=self.lam,
            now_fn=lambda: dt.datetime.fromtimestamp(self.now_t,
                                                     dt.timezone.utc),
            # the wiring Probes 04-08 actually traded through
            maker_treatment=True, maker_only=True,
            # BASELINE: no Probe 08 treatment, no Probe 09. Native.
            suppress_imbalance_increase=False,
            # RESIDUAL_CAP: OFF unless PMUS_RESIDUAL_CAP_K names a k. Absent
            # or unreadable is OFF, so a typo cannot silently half-enable a
            # policy, and OFF is byte-for-byte the frozen behaviour.
            residual_cap_k=residual_cap_k())
        # THE CALIBRATION LEDGER, attached after construction for the same
        # reason the validation gates are: the executor's own signature
        # stays unchanged, and an executor built without it simply has no
        # recorder (`getattr(..., None)` at the call site) rather than a
        # different constructor.
        self.executor.fill_calibration = self.fill_calibration
        # AND THE STORE, because `apply_order_progress` is where a venue fill
        # is actually booked and is reached by four call sites -- this
        # reconcile, the executor's terminal self-call, and the deadman
        # cancel verifier. Attaching only to the executor would leave a fill
        # booked by the deadman path invisible to calibration, which is the
        # fill most worth seeing: it is the one that happened while we were
        # trying to cancel.
        try:
            self.executor.store.fill_calibration = self.fill_calibration
        except BaseException as _exc:                          # noqa: BLE001
            if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                raise
        # THE MIRROR ITSELF, not a snapshot of it. A snapshot refreshed in
        # `shadow_tick` -- which runs LAST in the tick -- left the money
        # path's first intents after any start with `book: null` and
        # `queue: no_ladder`, and thereafter with a book one tick old that
        # the record could not distinguish from a book read at the wire.
        # The recorder snapshots the mirror at the submission instant.
        #
        # Set HERE rather than at `__init__` because `roll` rebuilds both
        # the executor and the window's ladder, in that order, so both
        # already exist and neither reference can go stale mid-window.
        self.executor.calib_book = self.book
        self.executor.calib_ladder = self.shadow_ladder
        # AND THE SAME MIRROR FOR THE TWO-LEG PLANNER. `handle(row, {}, ...)`
        # passes an empty book on purpose -- the L5-following path rests at
        # L5's own price and needs none -- but `probe_plan` prices both legs
        # from that argument, so the two-leg engine refused BOOK_STALE on
        # every decision. The mirror, not a snapshot: it is read at the
        # moment of the decision and nothing caches it.
        self.executor.ts_book = self.book
        # DISARMED-SHADOW VALIDATION PROTECTIONS, attached after construction
        # so the executor's own signature is unchanged.
        #
        # The validation gates are ON by default. They are refuse-only, so
        # the worst they can do is decline an action the deployed limits
        # already allowed -- and in shadow nothing is submitted anyway, so
        # turning them on here is how we find out they bind.
        self.executor.validation_gates = validation_gates()
        # The independent halt is INERT unless L5_HALT_URL names the guard's
        # read-only endpoint, so this deployment changes nothing by itself.
        # Once named, it FAILS CLOSED: absent, stale or unreachable all
        # refuse, because a halt that fails open is not a halt.
        self.executor.halt_gate = halt_gate()
        # authoritative cash, when a reading exists. None means the cash gate
        # binds on its configured ceiling alone and require_cash_evidence()
        # refuses to arm -- never that cash is unlimited.
        self.executor.available_cash_usd = None
        emit("L5_LIVE_GATES", {
            "validation_gates": (
                self.executor.validation_gates.as_dict(
                    realized_usd=_gates_realized(self))
                if self.executor.validation_gates else None),
            "halt_gate_configured": self.executor.halt_gate is not None,
            "halt_url_env": HALT_URL_ENV,
            "available_cash_usd": None,
            "labels": ("additive refuse-only gates. They cannot raise any "
                       "pre-existing limit and cannot arm anything.")})
        self.preflight_status = "NOT_RUN"
        self.last_preflight_t = 0.0
        self.preflight_attempts = 0
        self.last_blockers = _BLOCKERS_UNSET
        self.win = collections.Counter()
        self.first_action_t = None

    def emit_preflight_attempt(self, now: float, rep: Mapping[str, Any],
                               dur_s: float) -> None:
        """One line per preflight attempt: when, how long, what blocked it.

        REPORTING ONLY, and it must never break a preflight. Every read is
        defensive and the whole body is wrapped, because an attempt that
        failed is exactly the state where a telemetry helper is most likely
        to meet a None it did not expect -- and losing the preflight to
        describe it would be the wrong trade.

        The three things the record has to separate, which no existing line
        does:

          * WHICH GATE blocked. `first_blocking` is the first check with
            passed false and blocking not false, in the order preflight
            evaluates them, so a ladder of attempts shows whether the
            blocker moved or stood still.
          * HOW LONG the attempt itself took, so an attempt that is slow is
            distinguishable from an attempt that is merely late.
          * WHETHER THE SOCKET COULD HAVE SUBSTITUTED. The REST book route
            does not serve a 15m market for its first minutes and the
            authenticated socket does; `_ws_book_substitute` requires a
            two-sided snapshot, `subscribed_before_open` true and a fresh
            frame. All three are recorded as the attempt saw them, so
            "the socket was not ready" and "the socket was ready and the
            fallback still did not fire" stop being the same observation.
        """
        try:
            checks = list(rep.get("checks") or [])
            first_blocking = None
            for c in checks:
                if (not c.get("passed")) and c.get("blocking") is not False:
                    first_blocking = c.get("check")
                    break
            book_attempts = None
            for c in checks:
                if c.get("check") == "book_read_attempts":
                    book_attempts = c.get("detail")
                    break
            ws: Dict[str, Any] = {"snapshot": False}
            try:
                snap = self.book.snapshot(str(rep.get("market_slug") or ""))
            except BaseException:                              # noqa: BLE001
                snap = None
            if snap:
                ws = {"snapshot": True,
                      "bid": str(snap.get("bid")),
                      "ask": str(snap.get("ask")),
                      "two_sided": snap.get("bid") is not None
                      and snap.get("ask") is not None,
                      "subscribed_before_open": snap.get(
                          "subscribed_before_open"),
                      "stream_age_s": snap.get("stream_age_s"),
                      "book_updates": snap.get("book_updates"),
                      "market_state": snap.get("market_state")}
            ws["fallback_max_age_s"] = getattr(
                T, "WS_BOOK_FALLBACK_MAX_AGE_S", None)
            lim = getattr(self.transport, "limiter", None)
            emit("L5_LIVE_PREFLIGHT_ATTEMPT", {
                "market": rep.get("market_slug"),
                "attempt": self.preflight_attempts,
                "verdict": rep.get("preflight"),
                "t_in_window_s": (None if self.ws is None
                                  else round(now - float(self.ws), 2)),
                "attempt_duration_s": round(float(dur_s), 3),
                "next_attempt_in_s": PREFLIGHT_BACKOFF_S[
                    min(self.preflight_attempts,
                        len(PREFLIGHT_BACKOFF_S) - 1)],
                "first_blocking": first_blocking,
                "blocking_failures": rep.get("blocking_failures"),
                "fatal": rep.get("fatal"),
                "book_read_attempts": book_attempts,
                "ws_book": ws,
                "tick_wait_s": (None if lim is None
                                else getattr(lim, "tick_wait_s", None)),
                "labels": ("reporting only. One line per attempt including "
                           "the failures L5_LIVE_PREFLIGHT suppresses when "
                           "the blockers have not changed.")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["preflight_attempt_emit_error:%s"
                       % type(exc).__name__] += 1

    def maybe_preflight(self, now: float) -> None:
        t = self.transport
        if t is None:
            return
        if self.preflight_status == "PASSED":
            # ARMING IS RETRIED; PREFLIGHT IS NOT. A PASSED preflight is a
            # fact about this market and is never re-run, but try_arm can
            # refuse for reasons that clear on their own a few seconds
            # later -- an unconfirmed book, or an order in an UNCERTAIN
            # state. Reaching try_arm only through the not-yet-PASSED branch
            # gave arming exactly ONE attempt per market window.
            #
            # 1545z was lost to precisely that: the one attempt at 15:45:23
            # hit a terminal order that was not yet booked, the uncertainty
            # cleared at 15:45:35, and nothing in the process ever asked
            # again. The whole window ran disarmed with owner_arm_token true
            # and preflight PASSED.
            #
            # Retrying costs nothing: try_arm returns at once when already
            # armed or halted, reconcile_if_needed returns at once on a
            # confirmed book, and transport.arm() is a local gate check that
            # touches no venue. The refusal is still logged only on its
            # first occurrence in the window, so a long refusal does not
            # spam; its duration shows in the window counters.
            self.try_arm()
            return
        # escalating while it keeps failing: 3, 6, 12, then 24 seconds.
        # The common failure is simply that the venue has not listed the
        # next market yet, and asking four times as often does not list it
        # any sooner -- it only spends the budget that the orders and the
        # reconciliation need.
        idx = min(self.preflight_attempts, len(PREFLIGHT_BACKOFF_S) - 1)
        if now - self.last_preflight_t < PREFLIGHT_BACKOFF_S[idx]:
            return
        self.last_preflight_t = now
        self.preflight_attempts += 1
        t0 = time.monotonic()
        try:
            rep = t.preflight(
                expect_account=os.getenv("L5_LIVE_EXPECT_ACCOUNT", "").strip()
                or None)
        except T.PreflightFailed as exc:
            rep = json.loads(str(exc))
        except Exception as exc:                               # noqa: BLE001
            rep = {"preflight": "FAILED", "market_slug": t.market_slug,
                   "fatal": T.redact("%s: %s" % (type(exc).__name__, exc))}
        dur_s = time.monotonic() - t0
        self.preflight_status = rep.get("preflight", "FAILED")
        blockers = rep.get("blocking_failures")
        # log every PASS, and a FAIL only when what blocks it changes
        if self.preflight_status == "PASSED" or blockers != self.last_blockers:
            emit("L5_LIVE_PREFLIGHT", rep)
        self.last_blockers = blockers
        # EVERY ATTEMPT, INCLUDING THE SILENT FAILURES. The line above is the
        # reason eighty seconds of this engine's most expensive interval has
        # no record: a FAILED attempt whose blockers match the previous one
        # emits nothing at all, and at a window roll every early attempt
        # fails the same way. MEASURED 2026-10-27: the venue flips a new
        # window to MARKET_STATUS_OPEN at +0.6s and serves its metadata from
        # -5.9s, while preflight passed at +80.1s to +107.8s across nine
        # windows -- so the delay is ours and attempts 1..5 are exactly what
        # is unaccounted. One compact line per attempt, at most seven per
        # window, and it decides nothing.
        #
        # getattr, in the house idiom used wherever reporting hangs off the
        # money path ("TELEMETRY MUST NOT BE ABLE TO KILL A RECONCILE"): a
        # host without this helper -- a harness exercising maybe_preflight in
        # isolation, or a partially constructed runner -- still gets its
        # preflight. The helper itself also never raises.
        _rec = getattr(self, "emit_preflight_attempt", None)
        if _rec is not None:
            _rec(now, rep, dur_s)
        if self.preflight_status == "PASSED":
            # preflight reads tick and quantity increment off THIS market
            # and stores them on the transport; None means "not established"
            # and is never defaulted. The executor asks the transport.
            if t.tick_size is None:
                emit("L5_LIVE_TICK_UNKNOWN", {"market": t.market_slug})
            self.try_arm()

    def sweep_settlements(self) -> None:
        """Retire every market the venue has finished with.

        Without this the executor trades exactly one window. `committed` is
        incremented on every fill and was never retired, so `open_markets`
        kept naming the first market forever and MAX_CONCURRENT_MARKETS
        refused every window after it. The probes never saw it because they
        ran one window and exited.

        Only the venue may declare a market finished, and only a terminal
        state WITH a settlement price counts -- MARKET_STATE_CLOSED shows up
        at the 900s boundary before the price is final, and retiring against
        a price that is not final closes the book at the wrong number. A
        market that has closed but not yet settled therefore keeps holding
        its slot, which is the safe direction to be wrong in: it delays the
        next window rather than letting two markets' exposure overlap.
        """
        t = self.transport
        if t is None:
            return
        # WHAT IS DUE, before anything is read. The loop used to read as it
        # walked, so the number of venue reads one sweep could make was
        # whatever `markets_with_exposure` happened to return -- 86 of them
        # by 2026-10-27, which is 55s asleep in the limiter ahead of
        # preflight and ahead of the decision path. Collecting first is what
        # makes a per-tick cap possible at all.
        due = []
        for market in self.store.markets_with_exposure():
            if market == t.market_slug:
                continue                       # the one we are working now
            # ONE READ PER MARKET PER SETTLEMENT_POLL_S. This loop used to
            # run on every 3s tick against every market still in the book,
            # and with 2115z stuck there by the retirement defect that was a
            # settlement GET every 3 seconds forever.
            last = self.last_settlement_poll.get(market, 0.0)
            if self.now_t - last < SETTLEMENT_POLL_S:
                continue
            due.append((last, market))
        cap = settlement_sweep_cap()
        if cap is None:
            # UNBOUNDED, and in the order `markets_with_exposure` returned
            # them, which is the order this loop has always read in.
            markets = [m for _last, m in due]
        else:
            # OLDEST READ FIRST. A market never read has last == 0.0 and so
            # sorts ahead of everything, which is the property that matters:
            # the window that just closed is the settlement we are waiting
            # for, and it must not queue behind a fortnight of markets that
            # will never retire. Deferred, never dropped -- the rest are due
            # again on the next pass.
            due.sort()
            if len(due) > cap:
                self.stats["settlement_sweep_deferred"] += len(due) - cap
                self.win["settlement_sweep_deferred"] += len(due) - cap
            markets = [m for _last, m in due[:cap]]
        self.stats["settlement_sweep_read"] += len(markets)
        for market in markets:
            self.last_settlement_poll[market] = self.now_t
            try:
                rep = t.settlement(market)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["settlement_read_error"] += 1
                if T.is_rate_limited(exc):
                    self.stats["settlement_read_429"] += 1
                continue
            if not rep.get("terminal"):
                self.stats["awaiting_settlement"] += 1
                # a non-binary settlementPx is not merely "not yet": it is a
                # BOOK PRICE on an expired market, which is exactly what
                # Stage A booked as a payout. Name it so it is visible
                # rather than indistinguishable from waiting.
                why = rep.get("not_terminal_reason")
                if why and why != "STATE_NOT_TERMINAL":
                    self.stats["awaiting_settlement:%s" % why] += 1
                continue
            try:
                out = self.store.retire_settled_market(
                    market, yes_value=rep["settlement_px"],
                    resolution=rep.get("resolution"))
            except LX.IncoherentBook as exc:
                # the book cannot be settled because it is not a book a
                # trade could have produced -- quantity with no cost, or
                # cost with no quantity. Settling it anyway is what posted
                # 2045z at zero. It stays open, uncertain and unretired
                # until reconciliation or a pending order resolves it.
                self.stats["retire_refused:INCOHERENT_BOOK"] += 1
                emit("L5_LIVE_SETTLE_REFUSED",
                     {"market": market, "reason": "INCOHERENT_BOOK",
                      "detail": T.redact("%s" % exc)[:400],
                      "venue_state": rep.get("state"),
                      "submissions_allowed": False,
                      "why": ("a settlement computed against an incoherent "
                              "book posts a wrong realized P&L and moves "
                              "the loss halt with it")})
                continue
            if out.get("retired"):
                settled = dict(
                    out, venue_state=rep["state"],
                    cash_deployed=self.store.cash_deployed_usd(market))
                emit("L5_LIVE_SETTLED", settled)
                self.emit_window_accounting(market, settlement=settled)
            else:
                self.stats["retire_refused:%s"
                           % out.get("reason", "?")] += 1

    def resolve_pending_terminal(self, now: float) -> List[Dict[str, Any]]:
        """Book the final fill of every order that left the venue's open list.

        THE REPAIR FOR 2045z. An order that disappears is no longer released
        unbooked -- reconcile parks it, and this asks the venue what it
        finally did. `/v1/order/{id}` answers for a filled order long after
        it has left the open list (the read-only recovery service reads a
        day-old one), so this resolves on the first attempt in the ordinary
        case. When it does not, the order stays parked and the book stays
        uncertain, which refuses submissions until it is settled one way or
        the other. That is the correct direction to be stuck in.
        """
        t = self.transport
        out: List[Dict[str, Any]] = []
        if t is None:
            return out
        parked = self.store.pending_terminal()
        # ONE GLOBAL SLOT, oldest attempt first. Eight parked orders used to
        # mean eight reads every PENDING_RESOLVE_S; now they mean one read
        # per interval and eight intervals to clear, with the book uncertain
        # and every submission refused for all of it -- which is exactly the
        # state it was in before the first of them resolved.
        if now - self.last_pending_resolve_t < PENDING_RESOLVE_S:
            return out
        askable = sorted(
            ((c, r) for c, r in parked.items() if r.get("venue_order_id")),
            key=lambda cr: self.last_pending_resolve.get(cr[0], 0.0))
        for coid, row in parked.items():
            if not row.get("venue_order_id"):
                # nothing to ask by. It cannot be resolved automatically and
                # must not be silently dropped: it stays parked and loud.
                self.stats["pending_unresolvable"] += 1
                emit("L5_LIVE_PENDING_UNRESOLVABLE",
                     dict(row, why=("no venue order id, so the venue cannot "
                                    "be asked what this order finally did"),
                          submissions_allowed=False))
        for coid, row in askable[:1]:
            vid = row.get("venue_order_id")
            self.last_pending_resolve[coid] = now
            self.last_pending_resolve_t = now
            try:
                prog = LX.order_progress(t.order(str(vid)))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.store.note_pending_attempt(coid, type(exc).__name__)
                self.stats["pending_resolve_error"] += 1
                out.append({"coid": coid, "event": "PENDING_RESOLVE_ERROR",
                            "error": type(exc).__name__,
                            "rate_limited": T.is_rate_limited(exc),
                            "market": row.get("market")})
                continue
            res = self.store.resolve_pending_terminal(coid, prog)
            res.setdefault("event", "PENDING_TERMINAL_RESOLVED")
            res["venue_order_id"] = str(vid)
            res.setdefault("market", row.get("market"))
            for k in ("avg_px", "commission", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))
            out.append(res)
        if out:
            self.lifecycle(out)
        return out

    def reconcile_if_needed(self, now: float) -> None:
        """Reconcile against the venue when our book is unconfirmed OR uncertain.

        ONCE PER PROCESS WAS A LATCH, and it shut the executor down live on
        2026-09-26. Three order-progress GETs returned HTTPError at 16:12:06,
        poll_orders marked the book uncertain -- correctly, an order whose
        state we cannot read might be live and consuming the envelope -- and
        the SAFETY_OVERLAY then refused every action with
        ORDER_STATE_UNCERTAIN. Submissions stopped dead at 43. Uncertainty is
        cleared ONLY by a full authoritative reconciliation, and this method
        would not run a second one, so nothing in the process could ever
        clear it. A transient GET failure cost the whole session.

        So: reconcile when we have never confirmed a book, and AGAIN whenever
        the book goes uncertain. Not on every tick -- at most once every
        RECONCILE_RETRY_S, because hammering the venue while it is already
        returning errors is how a transient fault becomes a rate limit.

        WHAT DOES NOT CHANGE. A GET that merely succeeds clears nothing. Only
        InventoryStore.reconcile_from_venue clears the flag, and it does so
        at the END, after the venue's positions have replaced ours by the
        net = long - short identity, its open orders have been adopted or
        released, and the divergence has been reported. If either read
        raises, or the reconcile itself raises on a venue shape we do not
        recognise, the flag stays set, no order may be submitted, and we try
        again later. Fail-closed is the resting state, not the exception.

        Adoption, not duplication: orders are keyed by our deterministic
        client_order_id, so an order the venue lists that we had lost is
        adopted under the same id rather than re-sent as a second order.

        `positions()`, not `available_positions()`: the venue serves both and
        the settlement auditor treats the latter as explicitly NOT held, so
        merging them would inflate what we think we own.
        """
        t = self.transport
        if t is None:
            return
        uncertain = self.store.orders_uncertain()
        stalled = self.orders_stalled_for_s(now)
        # disarmed soak only: a periodic authoritative read so the real
        # reconciliation path is exercised against the real venue. Never
        # while armed -- there it would adopt and park live orders on a
        # timer nobody asked for.
        soak_due = (SOAK_RECONCILE_S > 0 and not (t is not None and t.armed)
                    and (now - self.last_reconcile_t) >= SOAK_RECONCILE_S)
        if (self.reconciled and not uncertain
                and stalled < STALLED_ORDER_S and not soak_due):
            return
        if stalled >= STALLED_ORDER_S and not uncertain:
            # not an error, so nothing marked it: the book simply stopped
            # agreeing with the venue. Fail closed until the venue answers.
            self.store.mark_uncertain(
                "no venue progress on %d live order(s) for %.0fs"
                % (self.store.live_order_count(), stalled))
            uncertain = self.store.orders_uncertain()
            emit("L5_LIVE_ORDERS_STALLED",
                 {"live_orders": self.store.live_order_count(),
                  "stalled_s": round(stalled, 1),
                  "submissions_allowed": False})
        if now - self.last_reconcile_t < RECONCILE_RETRY_S:
            return
        self.last_reconcile_t = now
        try:
            pos = t.positions()
            oo = t.open_orders()
            out = self.store.reconcile_from_venue(pos, oo)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # still unknowable. Keep the flag, keep refusing, come back.
            self.store.mark_uncertain(
                "reconcile raised %s" % type(exc).__name__)
            self.reconciled = False
            emit("L5_LIVE_RECONCILE_FAILED",
                 {"error": type(exc).__name__,
                  "detail": T.redact("%s" % exc)[:300],
                  "was_uncertain": str(uncertain or ""),
                  "submissions_allowed": False,
                  "retry_in_s": RECONCILE_RETRY_S})
            return
        self.reconciled = True
        self.last_progress_t = now
        # The authoritative buying-power read is deliberately NOT called
        # from here. Reconciliation establishes the BOOK, and a balance read
        # that errors must never take the book's confirmation down with it.
        # It runs on the main loop instead, self-throttled by
        # CASH_REFRESH_S and with its own error counter.
        emit("L5_LIVE_RECONCILE",
             dict(out, market=t.market_slug,
                  cleared_uncertainty=str(uncertain or ""),
                  trigger=("SOAK_PERIODIC" if soak_due and not uncertain
                           else "UNCERTAIN_OR_UNCONFIRMED"),
                  armed=bool(t.armed),
                  # getattr, because TELEMETRY MUST NOT BE ABLE TO KILL A
                  # RECONCILE. By the time this line runs the venue has
                  # already answered and the book is already saved; an
                  # AttributeError here would throw that away and report it
                  # as a reconcile failure, which is a lie about the venue.
                  venue_requests=(lim.metrics()
                                  if (lim := getattr(t, "limiter", None))
                                  is not None else None),
                  realized_today=str(self.store.realized_today())))

    def try_arm(self) -> None:
        t = self.transport
        if t is None or t.armed or self.halted():
            return
        # UNRESOLVED ACCOUNTING. orders_uncertain already refuses while the
        # row is incoherent -- proven in production, arm_refused_orders_
        # uncertain hit 221 in 1715z -- so this is NOT a second copy of that
        # refusal. It exists for the case orders_uncertain cannot cover: it
        # is RECOMPUTED every reconcile, so a book that stopped failing
        # coherence would silently clear it and arming would resume with the
        # loss still unaccounted. The registry is declared in code and does
        # not clear, so a market it names that the ledger no longer reports
        # as unresolved is a DISCREPANCY, not an all-clear.
        #
        # Scoped to a ledger that actually holds the market: a registry
        # entry must not make arming unreachable on an unrelated book, which
        # would turn the whole arming path into untestable dead code.
        stale = unresolved_discrepancies(self.store)
        if stale:
            self.win["arm_refused_unresolved_accounting"] += 1
            if self.win["arm_refused_unresolved_accounting"] == 1:
                emit("L5_LIVE_DISARMED", {
                    "market": t.market_slug,
                    "why": ("UNRESOLVED ACCOUNTING silently cleared from "
                            "the ledger; refusing to arm"),
                    "discrepancies": stale,
                    "labels": ("the registry names a market whose row the "
                               "ledger no longer reports as unresolved. A "
                               "possible loss of unknown size was on the "
                               "books and its disappearance is not "
                               "evidence that it was resolved.")})
            return
        # never arm on a book the venue has not confirmed
        self.reconcile_if_needed(self.now_t)
        if not self.reconciled:
            self.win["arm_refused_unreconciled"] += 1
            return
        # ONE SHARED AUTHORIZATION BOUNDARY. The checks above are kept
        # because they are cheap, they produce this service's own named
        # counters, and a refusal here should name the local reason. But the
        # DECISION is no longer taken here: authorize_arming is the union of
        # this path's preconditions and the one-shot probe's, so neither can
        # be weaker than the other, and it is the only thing that claims the
        # durable single-use authorization. This path previously claimed
        # nothing at all, which on a continuous ALWAYS-restart service is a
        # standing licence to arm every window.
        #
        # shadow_only() is passed in rather than inferred: while the lock is
        # on -- which is the DEFAULT -- the boundary refuses by name, so the
        # logs say why this service is not live instead of going quiet.
        try:
            # THE CAMPAIGN VERDICT, established here and passed in as a
            # FACT, exactly as `reconciled` and `halted` are. A runner that
            # cannot establish it has to say so and be refused rather than
            # omit it -- which is why this is computed before the call and
            # never defaulted inside it.
            #
            # The bounded campaign is what stops the owner editing Railway
            # for every fifteen-minute window: one approval document covers
            # the run, the durable per-nonce ledger counts the windows, and
            # an absent document leaves the existing single-window handshake
            # exactly as it was.
            try:
                # DO NOT SPEND A WINDOW THAT CANNOT TRADE. While the shadow
                # lock is on, submit() is structurally blocked and arming
                # would change nothing -- so reserving the window costs one
                # of the owner's four and buys no protection. The verdict is
                # still computed, which is what the activation sequence
                # verifies before the lock comes off.
                # ALWAYS READ-ONLY HERE. The window is spent further
                # down, once `authorize_arming` has RETURNED -- see
                # `spend_campaign_window`.
                campaign = CAMP.campaign_decision(
                    str(t.market_slug), root=self.root, reserve=False)
            except BaseException as exc:                      # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # UNREADABLE IS PRESENT-AND-REFUSING. A fault in this layer
                # must never read as "no campaign, carry on".
                campaign = {"present": True, "ok": False,
                            "reason": "CAMPAIGN_LAYER_FAILED",
                            "detail": {"error": type(exc).__name__}}
            # TELEMETRY NEVER DECIDES ARMING. The verdict above is already
            # fixed; publishing it must not be able to raise out of the
            # arming path and turn an ordinary refusal into a crashed tick.
            if campaign.get("present"):
                try:
                    self.stats["campaign:%s" % (campaign.get("reason")
                                                or "OK")] += 1
                except BaseException as exc:                  # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
            # ============ USABILITY IS DECIDED BEFORE THE CLAIM IS TAKEN
            #
            # `authorize_arming` writes the irreversible single-use claim
            # lock. Asking "is this window worth anything?" AFTERWARDS means
            # an unusable window burns the lock on its way to refusing
            # itself, and every later tick in that window is then refused
            # AUTHORIZATION_ALREADY_USED against a lock nothing can release.
            #
            # 2045z on 2026-10-23 is the whole case. The claim was written at
            # 20:49:16; usability refused seven seconds later with 284s of
            # entry runway against the 300s floor; the remaining 180 ticks of
            # that window were refused AUTHORIZATION_ALREADY_USED. The
            # campaign counter stayed honest -- `reserve_window` was never
            # reached, so none of the owner's windows was consumed -- but the
            # MARKET was unreachable for the rest of its life.
            #
            # This check is read-only: venue window bounds plus a store read,
            # writing nothing and reserving nothing, so moving it earlier can
            # only ever add a refusal. The post-claim re-check below is KEPT:
            # runway shrinks monotonically, so the two can disagree only in
            # the sub-second between them, and a window that became unusable
            # in that gap must still not trade.
            if campaign.get("present") and campaign.get("ok"):
                pre = self.campaign_window_is_usable(t.market_slug)
                if not pre.get("usable"):
                    self.win["arm_refused_window_not_usable"] += 1
                    if self.win["arm_refused_window_not_usable"] == 1:
                        emit("L5_LIVE_DISARMED", {
                            "market": t.market_slug,
                            "why": "CAMPAIGN_WINDOW_NOT_USABLE",
                            "detail": pre,
                            "boundary": "Runner.try_arm/pre_claim",
                            "claim_taken": False,
                            "labels": ("refused BEFORE the single-use claim "
                                       "was taken: no campaign window is "
                                       "spent and the authorization is "
                                       "still available to a later window")})
                    return
            claim = AUTH.authorize_arming(
                campaign=campaign,
                market=t.market_slug,
                # getattr: a caller without a recorded preflight has not
                # passed one, and the boundary must refuse that rather than
                # raise out of the arming path.
                preflight=getattr(self, "preflight_status", None),
                store=self.store, halted=self.halted(),
                # getattr, and `is not None`: a runner with no executor, or
                # an executor whose halt_gate was never built because
                # L5_HALT_URL is unset, has NO independent halt -- and the
                # boundary must refuse that by name rather than arm a process
                # nothing outside it could stop.
                halt_gate_present=getattr(
                    getattr(self, "executor", None), "halt_gate", None)
                is not None,
                reconciled=self.reconciled,
                unresolved=unresolved_discrepancies(self.store),
                # getattr on SELF too: a runner without an executor has no
                # capital envelope at all, and the boundary must refuse that
                # with CAPITAL_SCOPE_UNKNOWN rather than raise out of the
                # arming path.
                limits=getattr(getattr(self, "executor", None),
                               "limits", None),
                # the venue's own window, passed in as a FACT. None here is
                # a refusal (WINDOW_BOUNDS_UNKNOWN /
                # WINDOW_BOUNDS_UNCONFIRMED), never a fallback to our clock.
                window_bounds=self.venue_window_bounds(),
                nominal_window_s=float(AB.WINDOW_S),
                # THIS RUNNER'S CLOCK, so the window the claim is bounded by
                # is the window the tick is deciding in.
                now=dt.datetime.fromtimestamp(self.now_t, dt.timezone.utc),
                shadow_locked=shadow_only())
            # ================= THE WINDOW IS SPENT HERE, AND NOWHERE ELSE
            #
            # Every refusal is now behind us: the shadow lock is off, an
            # owner authorization (campaign or legacy handshake) was
            # established, the halt latch is present and clear, preflight
            # passed, the book is reconciled, no order is uncertain, the
            # accounting is resolved, both deadlines hold, the capital scope
            # is known, the market is inside the approved series, the
            # mandate's own scope digest matched and the durable claim was
            # taken. A window spent before any of that was a window that
            # could never trade -- 1600z went to a verification run under
            # the shadow lock and 1630z to an arm that MANDATE_SCOPE_CHANGED
            # then refused.
            #
            # AND IT MUST STILL BE WORTH SPENDING. A window whose entry
            # cutoff has passed cannot take a first leg, so unless we are
            # already working inventory in it there is nothing for it to do.
            #
            # THE SAME CHECK RAN BEFORE THE CLAIM. This one closes the
            # sub-second gap in which runway can cross the floor between the
            # two calls; it is reached only when the pre-claim check passed,
            # so a refusal here means the window expired mid-arm rather than
            # that it was never usable.
            if campaign.get("present") and campaign.get("ok"):
                worth = self.campaign_window_is_usable(t.market_slug)
                if not worth.get("usable"):
                    self.win["arm_refused_window_not_usable"] += 1
                    if self.win["arm_refused_window_not_usable"] == 1:
                        emit("L5_LIVE_DISARMED", {
                            "market": t.market_slug,
                            "why": "CAMPAIGN_WINDOW_NOT_USABLE",
                            "detail": worth,
                            "boundary": "Runner.try_arm/post_claim",
                            "claim_taken": True,
                            "labels": ("the window crossed the entry floor "
                                       "between the pre-claim check and the "
                                       "claim. No campaign window is spent; "
                                       "the claim for this market is")})
                    return
                spend = CAMP.reserve_window(str(t.market_slug),
                                            root=self.root)
                try:
                    self.stats["campaign_window:%s"
                               % (spend.get("reason") or "RESERVED")] += 1
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                if not spend.get("ok"):
                    self.win["arm_refused_%s"
                             % str(spend.get("reason")).lower()] += 1
                    emit("L5_LIVE_DISARMED", {
                        "market": t.market_slug,
                        "why": str(spend.get("reason")),
                        "detail": spend, "boundary": "Runner.try_arm",
                        "labels": ("the campaign window could not be "
                                   "reserved, so nothing is armed")})
                    return
        except AUTH.Refused as exc:
            # THE HISTORICAL COUNTER NAMES SURVIVE. These are operator-facing
            # signals -- the heartbeat publishes them and this branch's own
            # commit history cites "arm_refused_orders_uncertain hit 221 in
            # 1715z" -- so moving the decision into the shared boundary must
            # not silently rename them out from under whoever reads the logs.
            key = {
                "ORDER_STATE_UNCERTAIN": "arm_refused_orders_uncertain",
                "NOT_RECONCILED": "arm_refused_unreconciled",
                "UNRESOLVED_ACCOUNTING": "arm_refused_unresolved_accounting",
            }.get(str(exc.reason),
                  "arm_refused_%s" % str(exc.reason).lower())
            self.win[key] += 1
            if self.win[key] == 1:
                emit("L5_LIVE_DISARMED",
                     {"market": t.market_slug, "why": exc.reason,
                      "detail": exc.detail,
                      "boundary": "l5_live_authorization.authorize_arming"})
            return
        try:
            res = t.arm()
            self.arm_claim = claim
            # THE CAMPAIGN'S STARTING POINT, CAPTURED ONCE AND PERSISTED.
            #
            # HERE rather than inside `authorize_arming` because this is the
            # only place that holds all four figures at once: the authoritative
            # buying power from /v1/account/balances, the executor's store, and
            # the approval's own nonce. Immediately AFTER the arm succeeds, so
            # a refused window never moves the zero point.
            #
            # IT IS IDEMPOTENT, so being on the per-window path is safe: the
            # first window of a campaign writes the baseline and every later
            # one reads it back unchanged. A failure here cannot create risk --
            # with the bound configured and no baseline, `ValidationGates`
            # refuses every order with CAMPAIGN_BASELINE_ABSENT -- so this
            # reports rather than raises.
            emit("L5_CAMPAIGN_BASELINE", capture_campaign_baseline(self))
            emit("L5_LIVE_ARMED", dict(res, claim=claim))
        except LX.NotArmed as exc:
            self.win["arm_refused"] += 1
            if self.win["arm_refused"] == 1:
                emit("L5_LIVE_DISARMED", {"market": t.market_slug,
                                          "why": str(exc)[:300]})

    def close_window(self) -> None:
        t = self.transport
        slug = t.market_slug if t else None
        out: Dict[str, Any] = {
            "market": slug, "decisions": dict(self.win),
            "preflight": self.preflight_status,
            "armed": bool(t and t.armed),
            "observability": self.book.window_report(slug) if slug else None,
        }
        if slug and self.first_action_t is not None:
            obs = out["observability"]
            ws = AB.slug_start(slug)
            out["first_l5_action_offset_s"] = round(self.first_action_t - ws, 1)
            fb = obs.get("first_book_offset_s")
            out["book_before_first_l5_action"] = (
                fb is not None and fb <= out["first_l5_action_offset_s"])
        # A final progress read BEFORE the deadman, so an order that filled
        # between the last poll and window close is recorded as a fill rather
        # than disappearing into a cancel.
        if self.store.live_order_count():
            out["final_progress"] = self.poll_orders()
        if t is not None and t.armed:
            out["deadman"] = LX.deadman(self.store, t, reason="window_end")
            try:
                # positions() returns ROWS; .get() on a list silently raised
                # AttributeError into the except below, so this field was
                # never populated in any window.
                rows = [LX.position_row(p) for p in t.positions()]
                out["venue_position"] = {
                    r["side"]: r["qty"] for r in rows if r["market"] == slug}
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["venue_position_error"] = type(exc).__name__
            t.disarm("window_end")
        # OBSERVABILITY OF THE OBSERVABILITY, and it is not decoration.
        # L5_FILL_BOOK_EVIDENCE needs a fill and L5_RESIDUAL_BOOK_SAMPLE
        # needs unmatched inventory, so with an exhausted campaign and a
        # flat book NEITHER can fire -- and their silence is then
        # indistinguishable from the module being absent from the image or
        # failing to attach. This report fires every window regardless of
        # arming, and says whether the ring is actually taking frames off
        # the authenticated feed. Reporting only; wrapped, because a
        # self-report must never be why a window summary is lost.
        try:
            ring = getattr(self, "book_ring", None)
            samp = getattr(self, "residual_sampler", None)
            out["book_evidence"] = {
                "attached": ring is not None,
                "ring": None if ring is None else ring.report(),
                "sampler": None if samp is None else samp.report(),
                "shadow_sampler": (
                    None if getattr(self, "shadow_residual_sampler", None)
                    is None else self.shadow_residual_sampler.report()),
                "attach_errors": {k: v for k, v in self.stats.items()
                                  if "evidence" in k},
                "labels": ("ring counts frames taken off the authenticated "
                           "feed. A fill record needs a fill and a residual "
                           "sample needs unmatched inventory, so zero of "
                           "either is expected on a flat window and is NOT "
                           "evidence of a fault."),
            }
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["book_evidence"] = {"error": type(exc).__name__}
        emit("L5_LIVE_WINDOW", out)

    # ------------------------------------------------------------ actions
    @property
    def seen(self) -> Set[str]:
        """Read-only view of the durable dedupe keys."""
        return set(self.cursor._keys)

    @property
    def seeded(self) -> bool:
        return self.cursor.seeded

    @staticmethod
    def _iso(t: Optional[float]) -> Optional[str]:
        """A stored epoch as UTC, or None. None means NEVER, not zero."""
        if t is None:
            return None
        try:
            return dt.datetime.fromtimestamp(
                float(t), dt.timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def _action_age_s(r: Mapping[str, Any], now: float) -> Optional[float]:
        """How long ago L5's action happened, by L5'S OWN clock.

        None when the row carries no usable timestamp. Deliberately the
        row's `timestamp` and never our receipt time: the receipt time is
        always ~now, so measuring staleness against it would make every
        action look fresh and the bound would never bind. A negative age (a
        row stamped in the future) is clamped to 0 rather than refused --
        clock skew at the source is not the action's fault.
        """
        try:
            t = float(r.get("timestamp"))
        except (TypeError, ValueError):
            return None
        return max(0.0, float(now) - t)

    def poll(self, now: float) -> None:
        """One read of L5's published trades -> the executor, or a refusal.

        WHY THIS IS INSTRUMENTED THE WAY IT IS. For the 2026-10-26 campaign
        the whole of this stage's telemetry was a single `seeded: 200`, and
        the campaign's finding was reported as "approximately 200 seeded
        historical wallet events and apparently no new matching L5 events".
        The word doing the work there is "apparently": with no counter
        between the feed and `handle`, four completely different states
        produced byte-identical logs --

          * L5 traded nothing;
          * L5 traded, but only markets other than BTC15m;
          * L5 traded BTC15m and the slug no longer matched our prefix;
          * the feed served the same cached page forever.

        None of them is distinguishable from the others without the
        `ingest:*` counters and the observed-slug histogram below, so the
        campaign could not be diagnosed from its own logs at all. Every
        refusal now carries a machine-readable reason and the slugs that
        earned it, which is what makes "L5 is currently trading only other
        markets" a statement that can be established rather than assumed.
        """
        rows = self.feed()
        if rows is None:
            self.stats["feed_errors"] += 1
            self.stats["ingest:FEED_FAILED"] += 1
            return
        self.stats["ingest:polls_ok"] += 1
        self.stats["ingest:rows"] += len(rows)
        if rows:
            self.last_row_t = now
        fresh = []
        for r in rows:
            k = OBS.action_key(r)
            # L5'S OWN CLOCK, not ours, and over EVERY row rather than the
            # fresh ones: a feed serving the same page forever still looks
            # busy by our poll clock, and only the newest event time on the
            # page shows that it has stopped moving.
            try:
                et = float(r.get("timestamp"))
            except (TypeError, ValueError):
                et = None
            if et is not None and (self.newest_row_event_t is None
                                   or et > self.newest_row_event_t):
                self.newest_row_event_t = et
            if str(r.get("slug") or "").startswith("btc-updown-15m-"):
                self.last_btc15m_t = now
            # THE SLUG OF EVERY ROW WE SEE, admitted or not. This is the one
            # observation that answers "is L5 trading BTC15m right now", and
            # it is taken before any filter can discard the evidence.
            self.ingest_slugs[str(r.get("slug") or "?")] += 1
            if self.cursor.known(k):
                self.stats["ingest:dup"] += 1
                continue
            self.cursor.add(k, r.get("timestamp"))
            fresh.append(r)
        self.stats["ingest:new"] += len(fresh)
        if not self.cursor.seeded:
            # THE FIRST READ IS HISTORY, NOT A SIGNAL -- and it happens ONCE
            # EVER, not once per boot. It used to be in-process state, so
            # every restart re-seeded the whole 200-row page as history and
            # discarded it: an L5 action arriving in the seconds around a
            # redeploy was dropped permanently and silently. The cursor is
            # on the mounted volume, so a restart RESUMES instead.
            self.cursor.mark_seeded(len(fresh), now)
            self.stats["seeded"] = len(fresh)
            self.cursor.save()
            return
        self.cursor.save()
        # CHRONOLOGICAL, ALWAYS. The feed serves newest-first and a delayed
        # row can arrive out of order behind a newer one; the executor's
        # inventory reasoning is sequential, so it must see L5's actions in
        # the order L5 took them and not the order the API listed them.
        for r in sorted(fresh, key=lambda x: float(x.get("timestamp") or 0)):
            if not str(r.get("slug") or "").startswith("btc-updown-15m-"):
                self.stats["ingest:refused:NOT_BTC15M_SLUG"] += 1
                continue
            # STALENESS, AND IT IS A SAFETY BOUND RATHER THAN A FILTER.
            #
            # Now that the seed survives a restart, a process that was down
            # for an hour comes back holding a page of L5 trades it has
            # never seen -- and acting on an hour-old action live would be
            # backdating with real money. An action older than the bound is
            # recorded and refused. It can only ever refuse.
            age = self._action_age_s(r, now)
            if age is None:
                self.stats["ingest:refused:NO_TIMESTAMP"] += 1
                continue
            if age > self.max_action_age_s:
                self.stats["ingest:refused:STALE_ACTION"] += 1
                self.stats["ingest:stale_max_age_s"] = max(
                    int(age), int(self.stats.get("ingest:stale_max_age_s", 0)))
                continue
            self.stats["ingest:admitted"] += 1
            self.last_admitted_t = now
            self.handle(r, now)
            # ADDITIVE, AFTER, AND UNABLE TO AFFECT IT. A raise here
            # would abandon the remaining rows of this batch, so the
            # wrap is total -- the comparison is a record, and a record
            # must never cost the strategy an action.
            try:
                self.shadow_compare(r, now)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["shadow_compare_error:%s"
                           % type(exc).__name__] += 1

    def handle(self, r: Mapping[str, Any], recv: float) -> None:
        ws = OBS.l5_window_start(r.get("slug", ""))
        t, ex = self.transport, self.executor
        slug = OBS.pmus_slug_for(ws) if ws else None
        book = self.book.snapshot(slug) if slug else None
        row = engine_row(r, book, recv)
        act = row["l5"]
        # THE SHARED BOUNDARY, in front of an unchanged executor.
        #
        # These three refusals are the ones this method has always made, in
        # the order it always made them, producing the same layer, the same
        # reason and the same record shape -- the tape, the stats keys and
        # the dashboards all read those strings. What changed is only that
        # the DECISION about them now comes from `l5_intent_gate`, so a
        # controller-generated intent is subject to the identical chain
        # instead of to a second copy of it that could drift.
        #
        # L5's path below still calls `ex.handle(row, {}, ...)` with the
        # same arguments. The executor is not refactored and not wrapped.
        intent = GATE.Intent(provenance=GATE.L5_FOLLOWED, market=slug,
                             window=None if ws is None else str(ws),
                             side=act.get("pmus_side"),
                             row={"is_sell": act.get("is_sell")},
                             source_ref=row["action_key"])
        pre = self.intent_gate.evaluate_prefix(
            intent, upto="OPERATOR_HALT")
        if not pre["cleared_here"]:
            rec = {"action_key": row["action_key"], "market": slug,
                   "refused_by_layer": pre["layer"],
                   "refused_reason": pre["reason"],
                   "refused_by_gate": pre["blocked_by"],
                   "intent_provenance": GATE.L5_FOLLOWED,
                   "L5_NATIVE": FT.l5_native(row)}
        else:
            if self.first_action_t is None:
                self.first_action_t = float(r.get("timestamp") or recv)
            # NO intra-window cancel-and-replace. The P39 harness ended
            # every resting order that L5's next action no longer matched,
            # which the canonical core has no method for -- deliberately.
            # The lifecycle Probes 04-08 actually traded is: rest at L5's
            # price, and let the venue answer; orders that never fill are
            # cancelled by the deadman at window end, which close_window
            # already calls. Probe 08's three cheapest repairs rested to
            # the end exactly that way, and that is the evidence we have.
            # Re-pricing a resting order toward L5's newer price is
            # chasing under another name, and it has never been tested
            # with money. The 8-live-order cap and the $5 exposure bound
            # are what keep unfilled orders from accumulating.
            rec = ex.handle(row, {}, market=slug, window=str(ws),
                            open_markets=self.open_markets())
        layer = rec.get("refused_by_layer")
        order = rec.get("ORDER") or {}
        key = ("ORDER:%s" % (order.get("reason") or
                             ("SUBMITTED" if order.get("submitted") else "?"))
               if not layer else "%s:%s" % (layer, rec.get("refused_reason")))
        self.win[key] += 1
        self.stats[key] += 1
        # WHAT THIS DECISION WOULD DO TO THE IMBALANCE, on every row.
        #
        # `two_sided.legs_claimed` cannot answer this: it resets when a round
        # retires, so a complement attempt against inventory from an earlier
        # round reads as an opening. On the preserved tapes all 156
        # COMPLEMENT_PAIR_ABOVE_CAP rows -- complement attempts by the
        # refusal's own name -- carry it all-False or None.
        #
        # `inventory_class` already answers it correctly but is noted AFTER
        # translation, so it is absent from exactly the early refusals that
        # needed it. This is derived from realized inventory and the side
        # alone, so it is available on every row.
        #
        # LOG-ONLY AND FAIL-OPEN, like `two_sided` and `two_sided_round`
        # before it: an unreadable store yields UNKNOWN and never a guess,
        # and nothing here is consulted by any gate.
        try:
            _role = DROLE.decision_role(
                residual=self.executor.store.residual(slug),
                side=act.get("pmus_side"))
        except BaseException:                                  # noqa: BLE001
            _role = DROLE.UNKNOWN

        doc = {"t": now_utc().isoformat(), "row": row, "record": rec,
               "armed": bool(t and t.armed), "preflight": self.preflight_status,
               "decision_role": _role}
        with open(self.tape, "a") as fh:
            fh.write(json.dumps(doc, default=str) + "\n")
        emit("L5_LIVE_DECISION", {
            "market": slug, "key": key, "side": act.get("pmus_side"),
            "l5_price": act.get("l5_price"), "l5_size": act.get("l5_size"),
            "latency_s": row["latency_s"],
            "book": row["pmus"]["bbo"], "book_source": row["pmus"]["book_source"],
            "stream_age_s": row["pmus"]["stream_age_s"],
            "translation": rec.get("PMUS_TRANSLATION"),
            "safety": (rec.get("SAFETY_OVERLAY") or {}).get("reason"),
            # THE PAIR, NOT JUST THE LEG. The two-sided plan -- both legs'
            # prices, the common quantity and the combined pair cost -- is
            # staged into `rec` and written to the tape, but the emitted line
            # carried only the single leg that was being placed, so neither
            # the proposed complement nor the pair economics were observable
            # from the logs at all. Log-only and additive: the plan is what
            # the engine already decided, reported rather than recomputed.
            "two_sided": rec.get("TWO_SIDED_PROBE"),
            "two_sided_queue": self.ts_queue_view(
                slug, rec.get("TWO_SIDED_PROBE")),
            # THE LIFECYCLE'S OWN DECISIONS. Both are staged into `rec` and
            # reach the durable tape, and both were missing here -- so the
            # two things the lifecycle release exists to do, retiring a round
            # the book has left and deriving a complement from the current
            # book, were invisible in the logs and the release could not be
            # validated from them at all. Same omission as `two_sided`
            # itself, one release later.
            "two_sided_round": rec.get("TWO_SIDED_ROUND"),
            "two_sided_complement": rec.get("TWO_SIDED_COMPLEMENT"),
            "order": {k: order.get(k) for k in
                      ("client_order_id", "submitted", "reason", "venue_order_id",
                       "state", "error")} if order else None,
            "intent": order.get("intent"),
            "decision_role": _role,
            "armed": bool(t and t.armed)})
        # recv, not a fresh clock read: the placement time is when we
        # OBSERVED the action, which is what the queue model must date.
        self.model_place(order.get("intent"), row, recv, rec=rec,
                         refused_reason=rec.get("refused_reason"))

    def ts_queue_view(self, slug: str,
                      plan: Any) -> Optional[Dict[str, Any]]:
        """The ladder at BOTH proposed leg prices, for the fill scenarios.

        WHY. The three fill scenarios are not computable from a BBO:
        CONSERVATIVE needs the same-rung queue plus our own size cleared,
        BASE needs the queue, and OPTIMISTIC needs a print THROUGH our price.
        The emitted record carried only the touch, so no scenario could be
        evaluated from the logs for either leg -- including the complement,
        which is never submitted and therefore appears nowhere else.

        Read-only, log-only, and it never raises: an unreadable ladder
        reports why rather than a number, because an unknown queue is an
        unknown fill and not a zero.
        """
        legs = (plan or {}).get("legs") if isinstance(plan, Mapping) else None
        if not legs:
            return None
        lad = self.shadow_ladder
        out: Dict[str, Any] = {
            "labels": ("same_rung_fifo is a QUEUE that volume must clear; "
                       "cross_price_depth is a PRICE DISTANCE and no fill "
                       "may be claimed from it. A null is UNKNOWN, never 0.")}
        for leg in legs:
            try:
                side = str(leg.get("side"))
                own = D(str(leg.get("price")))
                qty = D(str(leg.get("qty")))
                wire = own if side == FM.UP else (D(1) - own)
                resting = FM.BID if side == FM.UP else FM.ASK
                row: Dict[str, Any] = {"own_px": str(own), "wire_px": str(wire),
                                       "qty": str(qty),
                                       "resting_side": resting}
                if lad is None:
                    row["why"] = "no_ladder"
                else:
                    q = lad.ahead_of_us(slug, resting, wire)
                    dep = lad.depth_at(slug, resting, wire)
                    row["queue_ahead"] = None if q is None else str(q)
                    row["level_depth"] = None if dep is None else str(dep)
                    row["bracket"] = lad.cross_price_bracket(
                        slug, resting, wire, qty)
                out[side] = row
            except BaseException as exc:                      # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["ts_queue_view_error:%s"
                           % type(exc).__name__] += 1
        return out

    def observable_despite_refusal(self, rec: Mapping[str, Any],
                                   refused_reason: Optional[str]
                                   ) -> Optional[Mapping[str, Any]]:
        """A MAKER translation worth MODELLING although live refused it.

        Only for ORDER_STATE_UNCERTAIN, and only when the uncertainty is
        about a DIFFERENT market. The purpose is narrow: an unresolved cost
        basis on an EXPIRED market must not cost us the shadow evidence from
        every later independent window, and the model touches no ledger, no
        cash and no order path.

        It is NOT extended to any other refusal. MAKER_ONLY_NO_TAKER,
        RESIDUAL_ABOVE_CAP and the rest are policy decisions about what this
        engine would actually do, and modelling past them would describe a
        different engine. Uncertainty about THIS market is also excluded:
        a book we cannot explain is not one to model against.
        """
        if not refused_reason:
            return None
        if "ORDER_STATE_UNCERTAIN" not in str(refused_reason):
            return None
        m = self.model
        if m is None:
            return None
        # never observe a window whose OWN book is the unresolved one
        if m.slug in unresolved_markets():
            return None
        try:
            if self.store.book_violations(m.slug):
                return None
        except Exception:                                      # noqa: BLE001
            return None
        tr = rec.get("MAKER_TREATMENT") or {}
        px, qty = tr.get("rested_at_l5_price"), tr.get("qty")
        if px is None or qty is None:
            return None
        side = str((rec.get("L5_NATIVE") or {}).get("side")
                   or (rec.get("row") or {}).get("side") or "").upper()
        return {"price": px, "qty": qty, "mode": "MAKER", "side": side,
                "client_order_id": rec.get("action_key")}

    def effective_residual_cap(self) -> Optional[D]:
        """The tighter of the live residual constraints, or None if neither.

        ValidationGates can only ADD refusals on top of SafetyLimits, so the
        unmatched inventory a live run could actually reach is bounded by
        whichever is smaller. Derived from both rather than hardcoded: if
        either is reconfigured the model follows it without a code change.
        """
        caps: List[D] = []
        lim = getattr(self.limits, "max_abs_residual", None)
        if lim is not None:
            caps.append(D(str(lim)))
        vg = getattr(self.executor, "validation_gates", None) \
            if self.executor is not None else None
        if vg is None:
            vg = validation_gates()
        unmatched = getattr(vg, "max_unmatched_contracts", None)
        if unmatched is not None:
            caps.append(D(str(unmatched)))
        return min(caps) if caps else None

    def model_place(self, intent: Optional[Mapping[str, Any]],
                    row: Mapping[str, Any], now: float,
                    rec: Optional[Mapping[str, Any]] = None,
                    refused_reason: Optional[str] = None) -> None:
        """Record one MAKER intent as a MODELLED resting order.

        MODELLED THROUGHOUT. The intent was already refused NOT_ARMED by the
        shadow transport; this asks only what would have become of it had it
        rested. Nothing here can submit, and a failure is counted rather
        than raised, because the modelling must never disturb the executor.
        """
        m = self.model
        if m is None:
            return
        observed_despite_refusal = False
        if not intent and rec is not None:
            intent = self.observable_despite_refusal(rec, refused_reason)
            observed_despite_refusal = intent is not None
        if not intent:
            return
        if str(intent.get("mode") or "").upper() != "MAKER":
            return                      # a taker intent never rests
        try:
            bbo = (row.get("pmus") or {}).get("bbo") or {}
            side = str(intent.get("side") or "").upper()
            if side not in (FM.UP, FM.DOWN):
                return
            out = m.place(side, D(str(intent["price"])),
                          D(str(intent["qty"])), now,
                          FM._d(bbo.get("bid")), FM._d(bbo.get("ask")),
                          coid=intent.get("client_order_id"))
            out["observed_despite_live_refusal"] = observed_despite_refusal
            if observed_despite_refusal:
                out["blocked_live_by"] = "ORDER_STATE_UNCERTAIN"
                out["labels"] = (
                    "MODELLED ONLY, and the live engine REFUSED this action "
                    "for ORDER_STATE_UNCERTAIN arising from a DIFFERENT, "
                    "expired market. It is observation of an independent "
                    "window, not a claim the engine would have traded. "
                    + str(out.get("labels") or ""))
            emit("L5_MODEL_INTENT", out)
        except Exception as exc:                               # noqa: BLE001
            self.stats["model_place_errors"] += 1
            emit("L5_MODEL_ERROR", {"where": "place", "error": T.redact(
                "%s: %s" % (type(exc).__name__, exc))})

    def model_advance(self, now: float) -> None:
        """Fold new trade prints into every MODELLED resting order."""
        m = self.model
        if m is None:
            return
        try:
            for ev in m.advance(self.book.trades(m.slug)):
                emit("L5_MODEL_FILL", ev)
        except Exception as exc:                               # noqa: BLE001
            self.stats["model_advance_errors"] += 1
            emit("L5_MODEL_ERROR", {"where": "advance", "error": T.redact(
                "%s: %s" % (type(exc).__name__, exc))})

    def two_sided_advance(self, now: float) -> None:
        """Step the SHADOW two-sided evaluator. Nothing here can submit.

        It is given the same authenticated snapshot and the same rolling
        trade list the modelled fill path already consumes, so it adds no
        venue request and no new data source.
        """
        ev = self.two_sided
        if ev is None:
            return
        try:
            ev.step(self.book.snapshot(ev.slug), self.book.trades(ev.slug), now)
        except Exception as exc:                               # noqa: BLE001
            self.stats["two_sided_errors"] += 1
            emit("L5_TWO_SIDED_ERROR", {"where": "step", "error": T.redact(
                "%s: %s" % (type(exc).__name__, exc))})
            return
        if now - self.last_two_sided_report_t >= self.TWO_SIDED_REPORT_S:
            self.last_two_sided_report_t = now
            emit("L5_TWO_SIDED_SHADOW", dict(ev.report(None), interim=True))

    def two_sided_close(self) -> None:
        """Window roll: emit the window's unscored report and hold it.

        The residual is NOT scored here. Scoring it needs the venue's own
        binary resolution, and this programme has already booked one phantom
        credit from a book price standing in for a settlement. The evaluator
        is parked until `sweep_settlements` retires the market against an
        authoritative 0 or 1, and scored then.
        """
        ev = self.two_sided
        self.two_sided = None
        if ev is None:
            return
        emit("L5_TWO_SIDED_SHADOW",
             dict(self.two_sided_ledger.park(ev.report(None), now=self.now_t),
                  interim=False))

    def two_sided_settle(self, now: float) -> None:
        """Finalize parked shadow windows from the venue's own resolution.

        INDEPENDENT OF LIVE EXPOSURE, which is the defect this replaces. The
        first version hung scoring off `sweep_settlements`, and that only ever
        runs for markets the REAL ledger holds inventory in -- in shadow it
        holds none, so a window carrying residual would have waited for a
        settlement that was never read. The 0130z window hid it: its residual
        was zero, so the unscored net happened to equal the scored one.

        The read is the same GET-only `transport.settlement`, which is already
        binary-guarded: it reports terminal only for a terminal state with a
        settlement price of exactly 0 or 1. A window that cannot be settled
        stays PENDING and contributes nothing; one that waits past the deadline
        becomes ERROR. Neither is ever counted as a flat result.
        """
        t = self.transport
        if t is None or now - self.last_two_sided_settle_t < SETTLEMENT_POLL_S:
            return
        self.last_two_sided_settle_t = now
        for bad in self.two_sided_ledger.expire(now):
            self.stats["two_sided_settle_expired"] += 1
            emit("L5_TWO_SIDED_SHADOW", bad)
        for slug in self.two_sided_ledger.pending_slugs():
            if slug == getattr(t, "market_slug", None) and self.two_sided:
                continue                     # still being collected
            try:
                rep = t.settlement(slug)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # CLASSIFIED, not merely counted. The bare counter reached 892
                # and could not be diagnosed from telemetry at all, because
                # the cause was discarded here: one slug failing every 10s
                # poll and a broad outage are the same number. The class, the
                # per-slug attempt count and a rate-limited line make
                # convergence observable without changing what is booked --
                # a read that fails still settles NOTHING.
                self.stats["two_sided_settle_read_error"] += 1
                cls = settle_error_class(exc)
                self.stats["two_sided_settle_error:%s" % cls] += 1
                n = self.two_sided_settle_attempts.get(slug, 0) + 1
                self.two_sided_settle_attempts[slug] = n
                if n in (1, 5, 25) or n % 100 == 0:
                    emit("L5_TWO_SIDED_SETTLE_ERROR", {
                        "slug": slug, "class": cls, "attempt": n,
                        "error": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160]),
                        "labels": ("a settlement READ failed. The window stays "
                                   "PENDING with null P&L and is never scored, "
                                   "never flat, never realized. attempt counts "
                                   "consecutive failures for THIS slug, so a "
                                   "rising number is a slug that is not "
                                   "converging rather than a busy poller.")})
                continue
            # the read answered, so this slug is converging: its failure
            # streak is history and must not be reported as current.
            self.two_sided_settle_attempts.pop(slug, None)
            if not rep.get("terminal"):
                self.stats["two_sided_awaiting_settlement"] += 1
                why = rep.get("not_terminal_reason")
                if why and why != "STATE_NOT_TERMINAL":
                    self.stats["two_sided_awaiting:%s" % why] += 1
                continue
            px = rep.get("settlement_px")
            scored = self.two_sided_ledger.finalize(slug, px == D(1))
            if scored is not None:
                emit("L5_TWO_SIDED_SHADOW", dict(scored, yes_value=str(px),
                                                 venue_state=rep.get("state")))

    def model_close(self) -> None:
        """Window close: settle the MODELLED book and report it.

        The outcome is taken from the venue's own last two-sided book: a
        BTC15m market settles UP iff the wire closes above 0.5. When the
        book is unreadable the outcome is left None and NOTHING is settled,
        rather than guessed.
        """
        m = self.model
        if m is None:
            return
        self.model = None
        # DETACH ITS OWN OBSERVER ONLY. This used to null the whole slot,
        # which was right when the slot held exactly this session; with the
        # fanout in place it would also silently detach the shadow
        # policies' ladder. The money-path semantics are unchanged: the
        # closed session stops receiving frames the instant it closes.
        fan = getattr(self, "fanout", None)
        if fan is None:
            self.book.frame_observer = None
        else:
            fan.primary = None
            if not fan.extra:
                self.book.frame_observer = None
        try:
            snap = self.book.snapshot(m.slug) or {}
            bid, ask = FM._d(snap.get("bid")), FM._d(snap.get("ask"))
            wire = None if (bid is None or ask is None) else (bid + ask) / 2
            up_won = None if wire is None else bool(wire > D("0.5"))
            rep = m.close(up_won, wire)
            rep["outcome_source"] = (
                "last two-sided authenticated book mid; None means the "
                "outcome was not established and nothing was settled")
            rep["ladder"] = m.ladder.status()
            emit("L5_MODEL_WINDOW", rep)
        except Exception as exc:                               # noqa: BLE001
            self.stats["model_close_errors"] += 1
            emit("L5_MODEL_ERROR", {"where": "close", "error": T.redact(
                "%s: %s" % (type(exc).__name__, exc))})

    def refresh_available_cash(self, t, now: float) -> None:
        """Feed the cash gate the venue's own buying power.

        The pieces already existed and were simply never connected:
        `transport.account()` GETs /v1/account/balances and
        `PmusTransport.buying_power()` reads it out of the LIST-shaped
        response. Until now available_cash_usd was hardcoded None, so the
        cash gate bound on its configured ceiling alone while the venue was
        reporting 243.15505.

        THIS CANNOT WEAKEN THE GATE, and that is the whole design:

          * a SUCCESSFUL read installs a number, and a number can only ADD
            a refusal -- the gate refuses when committed exceeds available,
            on top of the ceiling it already enforces;
          * a FAILED or unreadable read installs None, which is exactly
            today's production state, so it is not a regression -- and
            require_cash_evidence() still refuses to ARM on None;
          * a STALE value is never reused. Freshness is recorded and
            published so staleness is observable rather than assumed.

        Read-only throughout. No order path, no credential handling here.
        """
        if (self.available_cash_t is not None
                and now - self.available_cash_t < CASH_REFRESH_S):
            return                     # the reading in hand is still fresh
        value = None
        err = None
        try:
            bal = t.account() or {}
            value = T.PmusTransport.buying_power(bal)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            err = "%s: %s" % (type(exc).__name__,
                              T.redact("%s" % exc)[:120])
        self.available_cash_usd = value
        self.available_cash_t = now if value is not None else None
        if self.executor is not None:
            # None means "no authoritative reading", never "unlimited"
            self.executor.available_cash_usd = value
        emit("L5_LIVE_CASH", {
            "available_cash_usd": (None if value is None else str(value)),
            "source": "/v1/account/balances via transport.account()",
            "read_ok": value is not None, "error": err,
            "age_s": 0.0 if value is not None else None,
            "refresh_s": CASH_REFRESH_S,
            "labels": ("authoritative buying power. None is NOT unlimited: "
                       "the cash gate then binds on its configured ceiling "
                       "alone and require_cash_evidence() refuses to arm.")})

    def orders_stalled_for_s(self, now: float) -> float:
        """How long our live orders have gone without any venue progress.

        0.0 when nothing is live, or when progress moved. This is the signal
        that says a live-order book has drifted from the venue WITHOUT the
        uncertainty flag being set -- an order we cannot query (no venue id)
        or one the venue quietly finished never raises an error, it just
        stops changing while it holds a slot in MAX_LIVE_ORDERS.
        """
        if not self.store.live_order_count():
            self.last_progress_t = now
            return 0.0
        if self.last_progress_t <= 0:
            self.last_progress_t = now
            return 0.0
        return max(0.0, now - self.last_progress_t)

    def poll_orders(self) -> List[Dict[str, Any]]:
        """Ask the venue what each live order actually did.

        THE ACK IS A RECEIPT, NOT A FINAL STATE. Probe 05 took an ACK for a
        resting order, never asked again, and carried it as live for the rest
        of the window; 29 later actions were refused MAX_LIVE_ORDERS against
        an order that no longer existed. So every live order is re-read from
        the venue, and this is the only thing that moves a fill into
        inventory -- without it residual stays 0 and every exposure number
        downstream is wrong.

        An order whose state cannot be established stays UNCERTAIN and
        nothing is released: unknowable is not the same as flat.
        """
        t = self.transport
        out: List[Dict[str, Any]] = []
        if t is None:
            return out
        live = list(self.store.live_orders().items())
        # the per-order interval is a FLOOR; the aggregate rate is the cap.
        # One or two orders poll every 6s exactly as before; eight stretch
        # to 16s each so this path never exceeds ORDER_POLL_RATE.
        interval = max(ORDER_POLL_S,
                       len(live) / ORDER_POLL_RATE if ORDER_POLL_RATE else 0)
        # oldest-read first, so a stretched envelope still round-robins
        # fairly instead of starving whichever order sorts last.
        live.sort(key=lambda kv: self.last_order_poll.get(kv[0], 0.0))
        for coid, o in live:
            vid = o.get("venue_order_id")
            if not vid:
                continue
            if self.now_t - self.last_order_poll.get(coid, 0.0) < interval:
                continue
            self.last_order_poll[coid] = self.now_t
            try:
                row = t.order(str(vid))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.store.mark_uncertain(
                    "order query raised %s for %s" % (type(exc).__name__,
                                                      coid))
                out.append({"coid": coid, "event": "PROGRESS_ERROR",
                            "error": type(exc).__name__,
                            "rate_limited": T.is_rate_limited(exc)})
                continue
            prog = LX.order_progress(row)
            # THE ORDER'S OWN MARKET, not the transport's current one. The
            # transport follows the window; an order placed in 2045z that is
            # still live when 2100z opens would have had its fill booked
            # into 2100z -- a whole market's inventory attributed to the
            # wrong book. Visible in the 21:01:21Z lifecycle events, which
            # report two 2045z orders under market 2100z. They had filled
            # nothing, so nothing was misbooked. That was luck.
            res = self.store.apply_order_progress(
                coid, prog, market=str(o.get("market") or t.market_slug),
                side=str(o.get("side") or ""))
            res["venue_order_id"] = str(vid)
            res["market"] = str(o.get("market") or t.market_slug)
            res.setdefault("event", "ORDER_PROGRESS")
            # the venue's own numbers, carried with the delta rather than
            # discarded: without them a per-order audit cannot state an
            # average fill price or a commission after the fact.
            for k in ("avg_px", "commission", "leaves", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))
            # THE VENUE'S OWN EVENT TIME, additively and with no fallback.
            # Without it a fill can only ever be placed against the book as
            # WE later read it; with it, against the book as the venue had
            # it. `venue_event_t` returns None rather than substituting our
            # receipt, so an absent timestamp reads as absent. Reporting
            # only: no gate, deadline, price or order path reads either key.
            try:
                from src import l5_book_evidence as _BE
                vt, vk = _BE.venue_event_t(row)
                res["venue_event_t"] = vt
                res["venue_event_t_key"] = vk
                res["side_tracked"] = str(o.get("side") or "") or None
            except BaseException as _exc:                      # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["venue_event_t_error:%s"
                           % type(_exc).__name__] += 1
            out.append(res)
        if out:
            self.lifecycle(out)
            self.last_progress_t = self.now_t
        return out

    def lifecycle(self, events: List[Dict[str, Any]]) -> None:
        for e in events:
            self.stats["LIFECYCLE:%s" % e.get("event", "ERROR")] += 1
            self.win["LIFECYCLE:%s" % e.get("event", "ERROR")] += 1
            # NO FALLBACK. This used to emit `e.get("market") or
            # transport_slug`, and `"" or slug` is the slug -- so the two
            # orders booked under "" logged as 0115z and the defect was
            # invisible in the one place it should have been obvious.
            # Missing identity stays missing, and is named.
            m = e.get("market")
            # ===== THE EMITTER'S OWN CLOCK, because the log's is not ours.
            #
            # Railway stamps a line when it INGESTS it, and that lag is
            # bimodal: across the eleven live orders of the 20261024a
            # campaign it ran 0.029, 0.047, 0.069, 0.106, 0.129, 2.905,
            # 3.675, 5.337, 7.588, 8.925 and 9.641 seconds. Six under a
            # sixth of a second, five between three and ten.
            #
            # `L5_FILL_CALIB_PRESUBMIT` already carries `utc`, so a SUBMIT
            # time is knowable. This record did not, so no submit-to-terminal
            # latency could be measured from the stream: an interval computed
            # from two log stamps carries an unknown error at each end, which
            # is how a 16.785s rest came to be read as 9.142s. `rest_s` was
            # right all along because the engine computes it internally.
            #
            # Additive telemetry only. No gate, limit, price or order path
            # reads this field; it exists so a latency question can be
            # answered without the on-volume tape.
            rec = dict(
                e, market=(m if m else None),
                market_identity=("PRESENT" if m else "MISSING"),
                t=self.now_t,
                utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                clock="emitter")
            emit("L5_LIVE_LIFECYCLE", rec)
            # kept for the per-window accounting report. A missing market is
            # NOT filed under the transport slug -- that fallback is exactly
            # the defect the identity rule above exists to prevent -- so an
            # unidentified row is simply not accumulated and the report says
            # so by counting fewer orders than were submitted.
            if m:
                # LAZILY INITIALISED ON PURPOSE. Several construction paths
                # build a Runner without running the __init__ that declares
                # this, and a REPORTING accumulator must never be the reason
                # a poll raises. Found by the continuous-lifecycle suite.
                if getattr(self, "win_lifecycle", None) is None:
                    self.win_lifecycle = {}
                rows = self.win_lifecycle.setdefault(str(m), [])
                if len(rows) < 4096:
                    rows.append(rec)
            # one book record per POSITIVE delta: a partial fill is several
            # deltas and each one saw its own book, while a zero-delta
            # progress poll saw nothing new and is not a fill.
            try:
                if D(str(e.get("delta") or 0)) > 0:
                    self.emit_fill_evidence(rec)
            except (ArithmeticError, ValueError, TypeError):
                pass

    def emit_window_accounting(self, market: str, *,
                               settlement: Optional[Mapping[str, Any]] = None
                               ) -> None:
        """One window's fill, inventory and incentive report.

        Emitted TWICE for the same window and never as two windows: once
        PROVISIONAL when the window's orders are done, and again CONFIRMED
        once the venue has settled it, carrying the venue's own lines.

        REPORTING ONLY. `l5_window_accounting` imports nothing that can reach
        a venue and exposes no submit, cancel or refuse. A failure here is
        swallowed and counted: an accounting report must never be able to stop
        trading or break a settlement path.
        """
        try:
            from src import l5_window_accounting as WA
            rec = WA.window_record(
                market=str(market),
                submits=_window_submits(self, str(market)),
                lifecycle=(getattr(self, "win_lifecycle", None)
                           or {}).get(str(market)) or (),
                settlement=settlement,
                # ONLY ACTUALLY CREDITED rebates, and we hold none: no venue
                # credit record is reachable from this process, so the field
                # stays empty rather than being filled with the estimate.
                rebate_credits=(),
                rebate_eligibility="UNVERIFIED_NO_VENUE_CREDIT_RECORD")
            if getattr(self, "window_reports", None) is None:
                self.window_reports = {}
            self.window_reports[str(market)] = rec
            emit("L5_WINDOW_ACCOUNTING", rec)
            emit("L5_CAMPAIGN_ROLLING",
                 WA.campaign_rolling(list(self.window_reports.values())))
            if settlement is not None:
                (getattr(self, "win_lifecycle", None)
                 or {}).pop(str(market), None)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["window_accounting_failed:%s"
                       % type(exc).__name__] += 1

    def order_progress_latency_s(self, now: float) -> Dict[str, Any]:
        """How stale our knowledge of any live order is, right now.

        THE NUMBER PACING CAN HIDE. A limiter makes the transmitted request
        rate look safe by construction -- it is the rate we chose -- while
        the thing that actually degrades is how long an order's true state
        goes unread. This reports it directly: the oldest live order's age
        since its last authoritative read, and how far past its own
        scheduled interval that is.
        """
        live = self.store.live_orders()
        interval = max(ORDER_POLL_S,
                       len(live) / ORDER_POLL_RATE if ORDER_POLL_RATE else 0)
        ages = [now - self.last_order_poll.get(c, now) for c in live]
        oldest = max(ages) if ages else 0.0
        return {"live_orders": len(live),
                "poll_interval_s": round(interval, 2),
                "oldest_unread_s": round(oldest, 2),
                "overdue_s": round(max(0.0, oldest - interval), 2),
                "parked_orders": len(self.store.pending_terminal()),
                "labels": ("oldest_unread_s is how stale our knowledge of "
                           "any live order is. overdue_s above zero means "
                           "the schedule is not keeping up, which a "
                           "transmitted request rate cannot show.")}

    def open_markets(self) -> List[str]:
        return [m for m, v in self.store.committed().items() if v > 0]

    # ================================================= SHADOW CONTROLLER
    #
    # An INDEPENDENT decision source. The money path above translates L5's
    # observed trades -- side, price and size all come from L5's action --
    # so it cannot answer what our own policy would do from our own
    # inventory. This block does, and reaches no order path at all:
    # `l5_shadow_controller` holds no transport, no credentials and no
    # executor, and nothing on the money path reads what it emits.
    #
    # It is NOT gated on the safety chain, and that is deliberate rather
    # than an omission: the chain exists to stop submissions, and there is
    # no submission here to stop. Gating it on HALT would only stop the
    # record, which is the one thing Phase A is for.

    def shadow_attach_ladder(self, slug: str) -> None:
        """Give the shadow policies the authenticated LADDER for this window.

        ONE ladder, shared by every policy and every scenario: it is the
        venue's book, so there is nothing to keep separate, and feeding it
        once keeps the fan-out at a single extra observer.

        Built here, on the window roll, for the same reason the executor's
        own model session is: queue position is only meaningful for the
        market we are actually watching. Failure to build it is not fatal
        -- a missing ladder leaves queue_ahead None, and the fill model
        never fills an unknown queue except on a sweep.
        """
        self.shadow_ladder = None
        if self._shadow_off:
            return
        try:
            self.shadow_ladder = FM.Ladder()
            if self.fanout is not None:
                self.fanout.add(self.shadow_ladder.ingest)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.shadow_ladder = None
            self.stats["shadow_ladder_error:%s" % type(exc).__name__] += 1

    def evidence_attach(self, slug: str) -> None:
        """Book-evidence recorders for this window. REPORTING ONLY.

        ITS OWN LADDER, not the shadow one. `shadow_attach_ladder` returns
        early when shadow is off, so hanging the evidence off it would make
        the answer to the owner's question depend on an unrelated switch.
        One extra `Ladder` costs one parse per frame -- a few per second --
        and the fanout wraps extra observers individually, so neither the
        ladder nor the ring can reach the money path's observer.

        A failure here leaves both recorders None and is counted. Every
        caller treats None as "no evidence this window", never as an error.
        """
        self.book_ring = None
        self.residual_sampler = None
        self.shadow_residual_sampler = None
        self.evidence_ladder = None
        try:
            from src import l5_book_evidence as BE
            self.book_ring = BE.BookRing()
            self.residual_sampler = BE.ResidualSampler(
                every_s=float(os.getenv("L5_EVIDENCE_SAMPLE_S") or 5.0))
            # A SEPARATE instance for shadow. Sharing one would let shadow
            # traffic advance the money path's interval and counters, and
            # then neither number would describe anything.
            self.shadow_residual_sampler = BE.ResidualSampler(
                every_s=float(os.getenv("L5_EVIDENCE_SAMPLE_S") or 5.0))
            lad = FM.Ladder()
            self.evidence_ladder = lad
            ring = self.book_ring

            def _observe(msg: Any, now: float, _l=lad, _r=ring) -> None:
                _l.ingest(msg, now)
                _r.observe_ladder(_l, now)

            if self.fanout is not None:
                self.fanout.add(_observe)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.book_ring = None
            self.residual_sampler = None
            self.shadow_residual_sampler = None
            self.evidence_ladder = None
            self.stats["evidence_attach_error:%s" % type(exc).__name__] += 1

    def shadow_book_evidence(self, variant: str, rec: Mapping[str, Any], *,
                             slug: str, now: float) -> None:
        """SHADOW validation of the book-evidence FILL path, on live books.

        WHY THIS EXISTS. The money path's fill record needs a fill, and the
        campaign is exhausted, so that path would never run again before a
        new authorization -- leaving it deployed and unexercised. The shadow
        controller models fills continuously against the same authenticated
        ladder, so pointing the same record builder at those exercises every
        branch (`as_of`, the reachability arithmetic, the taker walk, the
        pair terms) on real book rows with nothing at risk.

        A MODELLED FILL IS NOT A FILL, and three things say so: the label,
        an explicit `basis`, and `venue_event_t=None` -- a modelled fill has
        no venue timestamp and must not borrow one, so the event-time book
        correctly reads NO_VENUE_EVENT_TIME_SUPPLIED rather than being
        quietly satisfied by our own clock.
        """
        try:
            from src import l5_book_evidence as BE
            ring = getattr(self, "book_ring", None)
            if ring is None:
                return
            f = rec.get("modelled_fill") or {}
            inv = f.get("inventory_after") or rec.get("inventory") or {}
            resid = inv.get("residual") if isinstance(inv, Mapping) else None
            out = BE.fill_evidence(
                slug=str(slug), coid="shadow|%s" % variant,
                side=f.get("side"), fill_qty=f.get("qty"),
                fill_own_px=f.get("price"),
                venue_event_t=None,
                processing_t=now,
                residual_after=resid,
                first_leg_own_px=f.get("price"),
                qty_needed=resid,
                ring=ring, order_resting=False,
                depth_ticks=_evidence_depth_ticks())
            out["label"] = "L5_SHADOW_FILL_BOOK_EVIDENCE"
            out["shadow_variant"] = str(variant)
            out["basis"] = (
                "MODELLED fill from the queue-aware shadow model, NOT a "
                "venue fill and NOT realized P&L. Emitted to exercise the "
                "book-evidence fill path on live books while no live "
                "authorization exists.")
            out["modelled_fill"] = {
                k: f.get(k) for k in
                ("filled_by", "scenario", "relation", "swept",
                 "ticks_behind_touch", "queue_ahead_at_placement",
                 "volume_through_level")}
            emit("L5_SHADOW_FILL_BOOK_EVIDENCE", out)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_fill_evidence_error:%s"
                       % type(exc).__name__] += 1

    def shadow_residual_book(self, variant: str, rec: Mapping[str, Any], *,
                             slug: str, now: float) -> None:
        """SHADOW validation of the UNMATCHED-INVENTORY path.

        Driven by the modelled residual being non-zero, exactly as the money
        path's is driven by the real one, and on its OWN sampler instance so
        the two sets of counters stay separable.
        """
        try:
            s = getattr(self, "shadow_residual_sampler", None)
            ring = getattr(self, "book_ring", None)
            if s is None or ring is None:
                return
            inv = rec.get("inventory")
            if not isinstance(inv, Mapping):
                return
            out = s.sample(
                slug="%s|%s" % (slug, variant), now=now,
                residual=inv.get("residual"),
                first_leg_own_px=inv.get("pair_unit_cost"),
                order_resting=bool((rec.get("resting") or {}) or False),
                book=ring.latest(str(slug)),
                depth_ticks=_evidence_depth_ticks())
            if out is None:
                return
            out["label"] = "L5_SHADOW_RESIDUAL_BOOK_SAMPLE"
            out["shadow_variant"] = str(variant)
            out["basis"] = ("MODELLED inventory, not a venue position. "
                            "Exercises the unmatched-inventory path on live "
                            "books with nothing at risk.")
            emit("L5_SHADOW_RESIDUAL_BOOK_SAMPLE", out)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_residual_book_error:%s"
                       % type(exc).__name__] += 1

    def emit_fill_evidence(self, ev: Mapping[str, Any]) -> None:
        """The opposite side at one fill, on both time bases.

        REPORTING ONLY, and wrapped: a book record must never be the reason
        a fill fails to be processed.
        """
        try:
            from src import l5_book_evidence as BE
            ring = getattr(self, "book_ring", None)
            if ring is None:
                return
            m = ev.get("market")
            if not m:
                return                      # unidentified rows are not filed
            coid = str(ev.get("coid") or "")
            orders = getattr(getattr(self, "fill_calibration", None),
                             "orders", None) or {}
            sub = ((orders.get(coid) or {}).get("pre_submit")) or {}
            side = ev.get("side_tracked") or sub.get("side")
            rec = BE.fill_evidence(
                slug=str(m), coid=coid, side=side,
                fill_qty=ev.get("delta"), fill_own_px=ev.get("avg_px"),
                venue_event_t=ev.get("venue_event_t"),
                processing_t=self.now_t,
                residual_after=self.store.residual(str(m)),
                first_leg_own_px=sub.get("own_px") or ev.get("avg_px"),
                qty_needed=self.store.residual(str(m)),
                ring=ring, order_resting=None,
                depth_ticks=_evidence_depth_ticks())
            rec["venue_event_t_key"] = ev.get("venue_event_t_key")
            emit("L5_FILL_BOOK_EVIDENCE", rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["fill_evidence_error:%s" % type(exc).__name__] += 1

    def sample_residual_book(self, market: Optional[str]) -> None:
        """Book snapshots while inventory is unmatched, order or no order.

        Driven by the residual, NOT by an order existing, because the
        intervals with nothing resting are exactly the ones no existing
        record covers. REPORTING ONLY and wrapped.
        """
        try:
            s = getattr(self, "residual_sampler", None)
            ring = getattr(self, "book_ring", None)
            if s is None or ring is None or not market:
                return
            resid = self.store.residual(str(market))
            live = 0
            try:
                for _c, _o in (self.store.live_orders() or {}).items():
                    if str((_o or {}).get("market") or "") == str(market):
                        live += 1
            except BaseException:                              # noqa: BLE001
                live = 0
            rec = s.sample(
                slug=str(market), now=self.now_t, residual=resid,
                order_resting=bool(live), ring=ring,
                depth_ticks=_evidence_depth_ticks())
            if rec is not None:
                emit("L5_RESIDUAL_BOOK_SAMPLE", rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["residual_sample_error:%s" % type(exc).__name__] += 1

    def shadow_controller(self) -> Dict[str, Any]:
        """Built once, lazily, and never allowed to fail the tick.

        ONE PER POLICY. The baseline (the argmax the money path would have
        used) runs alongside the candidate, each over its own simulated
        inventory in its own file, sharing one loaded model. A difference in
        their records is therefore a difference in the policy choice alone.

        A missing or unreadable v2 artifact must stop THIS and nothing else,
        so a construction failure disables the whole block for the life of
        the process, is reported by name, and leaves every other path
        exactly as it was.
        """
        if self._shadow_off or self._shadow:
            return self._shadow
        try:
            model = None
            built: Dict[str, Any] = {}
            # THREE POLICIES AT THE TOUCH, plus TWO placement arms.
            #
            # The fourth is the pre-registered AT_TOUCH versus
            # ONE_TICK_BEHIND comparison
            # (analysis/l5/TICK_DEPTH_PREREGISTRATION.md), and the fifth is
            # TWO_SIDED at that same depth. Each is a SEPARATE controller
            # with its own durable state, deliberately not a new axis inside
            # the other three: that way the nine original simulations keep
            # their exact state layout and behaviour, and either arm can be
            # removed by deleting one entry.
            #
            # Both run the BASELINE policy, so ARGMAX vs ARGMAX@ONE_TICK_BEHIND
            # isolates RESTING DEPTH and ARGMAX@ONE_TICK_BEHIND vs
            # ARGMAX@TWO_SIDED isolates TWO-SIDEDNESS.
            for label, name, placement in SHADOW.SHADOW_VARIANTS:
                c = SHADOW.ShadowController(
                    state_path=os.path.join(
                        self.root, SHADOW.variant_state_filename(label)),
                    model=model, policy=name, placement=placement,
                    emit=self._shadow_emit)
                model = c.model               # load once, fail once, share
                built[label] = c
            self._shadow = built
            for name in sorted(built):
                emit("L5_SHADOW_READY", built[name].report())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._shadow, self._shadow_off = {}, True
            emit("L5_SHADOW_DISABLED",
                 {"why": "%s" % type(exc).__name__,
                  "detail": T.redact("%s" % exc)[:300],
                  "labels": ("the independent shadow decision source is "
                             "OFF for this process. Nothing else is "
                             "affected: it gates nothing and no money "
                             "path reads it.")})
        return self._shadow

    def _shadow_emit(self, kind: str, doc: Mapping[str, Any]) -> None:
        """Print a decision when it SAYS something new, and at least once a
        minute regardless.

        Every tick is evaluated; only repetition is suppressed, and the
        suppressed count is carried in `stats` and in the controller's own
        report so the record is complete without being 300 lines a window.
        A modelled fill is never suppressed -- it is the only event here
        that changes state.
        """
        # KEYED PER VARIANT, not per policy. Three arms share the policy
        # name ARGMAX, and two of them now share an offset as well, so their
        # (decision, relation, side, target_price, reason) keys are
        # identical tick after tick -- whichever decided first muted the
        # other entirely and the controlled comparison silently stopped
        # existing. It did not bite while the two ARGMAX arms differed in
        # target_price; adding a second arm at the same depth exposed it.
        pol = "%s|%s" % (doc.get("variant") or doc.get("policy") or "?",
                         doc.get("scenario"))
        key = (doc.get("decision"), doc.get("relation"), doc.get("side"),
               doc.get("target_price"), doc.get("reason"))
        t = float(self.now_t or 0.0)
        if (key == self._shadow_last_key.get(pol)
                and (t - self._shadow_last_emit_t.get(pol, 0.0)) < SHADOW_EMIT_S
                and not doc.get("modelled_fill")):
            self.stats["shadow_repeat_suppressed:%s" % pol] += 1
            return
        self._shadow_last_key[pol] = key
        self._shadow_last_emit_t[pol] = t
        emit(kind, doc)

    def shadow_window(self) -> Dict[str, Any]:
        """The window clock a shadow decision is dated against, and its
        source.

        PREFERS THE VENUE, and reads it ONLY from the cache the money path
        has already filled. `venue_window_bounds()` performs a venue read
        and caches a failure as permanent for that window, so calling it
        from here could change what the arming boundary later sees -- and
        shadow code must not be able to move a money-path gate. Falling
        back to our own 900s grid is right for a decision that cannot
        place an order, where the arming boundary refuses outright; the
        source is carried on every record so the two are never confused.
        """
        ws = self.ws
        if ws is None:
            return {"window_end_ts": None, "window_source": "NO_WINDOW"}
        cached = self.window_bounds.get(ws)
        if cached and cached.get("end"):
            try:
                end = dt.datetime.fromisoformat(str(cached["end"]))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=dt.timezone.utc)
                return {"window_end_ts": end.timestamp(),
                        "window_source": MECH.VENUE_SOURCE}
            except BaseException:                              # noqa: BLE001
                pass
        return {"window_end_ts": float(ws) + float(AB.WINDOW_S),
                "window_source": "CLOCK_GRID"}

    def shadow_tick(self, now: float) -> Optional[Dict[str, Any]]:
        """ONE independent inventory-driven decision, every tick.

        Runs whatever the money path is doing -- cooling off, halted,
        disarmed, shadow-locked -- because it costs the venue nothing: the
        book it reads is the websocket mirror already running, and the only
        thing it writes is its own state file. Continuous emission is the
        deliverable, so any failure here degrades to a counted error and
        never to a raise.
        """
        cs = self.shadow_controller()
        if not cs or self.ws is None:
            return None
        slug = AB.slug_for(int(self.ws))
        win = self.shadow_window()
        # ONE snapshot for all policies: they must decide on the SAME book
        # and the SAME instant or the comparison means nothing.
        book = self.book.snapshot(slug)
        # ONE read of each, shared by every policy: they must decide on the
        # same book, the same prints and the same ladder or the comparison
        # means nothing. `trades` returns a copy under the book's lock.
        try:
            prints = self.book.trades(slug)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            prints = []
            self.stats["shadow_prints_error:%s" % type(exc).__name__] += 1
        # THE EXECUTOR'S PRE-SUBMIT HOOK READS THESE. The MIRROR and the
        # ladder, not this tick's snapshot: the hook fires on the money
        # path earlier in the tick than this, so a snapshot left here is
        # always at best one tick stale and, on the first tick of a
        # process, absent. `roll` sets both too; this re-points the ladder
        # if a window attached one after the executor was built.
        if self.executor is not None:
            self.executor.calib_book = self.book
            self.executor.calib_ladder = self.shadow_ladder
        out = None
        for name in sorted(cs):
            try:
                rec = cs[name].step(now, slug=slug, book=book,
                                    window_end_ts=win["window_end_ts"],
                                    window_source=win["window_source"],
                                    prints=prints,
                                    ladder=self.shadow_ladder)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["shadow_error:%s:%s"
                           % (name, type(exc).__name__)] += 1
                continue
            self.stats["shadow_decisions:%s" % name] += 1
            self.stats["shadow:%s:%s" % (name, str(rec.get("relation")
                                                   or rec.get("decision")
                                                   or "?"))] += 1
            if rec.get("modelled_fill"):
                self.stats["shadow_modelled_fills:%s" % name] += 1
                self.shadow_book_evidence(name, rec, slug=slug, now=now)
            self.shadow_residual_book(name, rec, slug=slug, now=now)
            if name == SHADOW.BASELINE_POLICY:
                # L5's observed action is compared against the BASELINE,
                # which is what the money path would have done.
                self._shadow_last_rec, self._shadow_last_rec_t = rec, now
            out = rec
        # AFTER the variants have decided, so the resting quote this tick
        # placed exists to be captured -- and captured against the SAME
        # book snapshot the decision used, which is what makes it a
        # pre-submission observation rather than a later re-read.
        self.calibration_shadow_tick(now, slug=slug, book=book,
                                     prints=prints)
        return out

    def shadow_compare(self, r: Mapping[str, Any], now: float) -> None:
        """What L5 just did, beside what WE had decided at that moment.

        This is the programme's validation standard -- "at this exact market
        state, what did L5 do next, and what would our clone have done" --
        and it is the one comparison the money path cannot make, because
        there our action IS L5's action restated.

        OUR DECISION IS THE ONE FROM BEFORE THIS ACTION ARRIVED, and its age
        is reported. `shadow_tick` runs at the end of the tick, so the
        decision in hand predates this row by at most one poll interval.
        That is the honest direction: a decision taken AFTER seeing L5's
        trade would be hindsight, which is the leak this programme has
        guarded against throughout.

        NOT a score. One row is one coincidence; the aggregate is what the
        record is for, and agreement on side is not agreement on policy.
        """
        rec = self._shadow_last_rec
        if rec is None:
            return
        act = OBS.l5_to_pmus_action(r)
        ours = rec.get("side")
        theirs = ("YES" if act.get("pmus_side") == "UP"
                  else "NO" if act.get("pmus_side") == "DOWN" else None)
        emit("L5_SHADOW_COMPARE", {
            "provenance": "%s_VS_L5_OBSERVED" % SHADOW.CONTROLLER_PROVENANCE,
            "market": rec.get("market"),
            "l5": {"side": theirs, "price": act.get("l5_price"),
                   "size": act.get("l5_size"), "is_sell": act.get("is_sell"),
                   "tx": r.get("transactionHash")},
            "ours": {"decision": rec.get("decision"),
                     "relation": rec.get("relation"), "side": ours,
                     "price": rec.get("target_price"),
                     "state": rec.get("state"), "tte_s": rec.get("tte_s"),
                     "inventory": rec.get("inventory"),
                     "decision_age_s": round(now - self._shadow_last_rec_t, 3)},
            "compared_policy": rec.get("policy"),
            "same_side": (None if (ours is None or theirs is None)
                          else ours == theirs),
            "we_would_have_acted": rec.get("decision") == "quote",
            "labels": ("our decision PREDATES this action by decision_age_s; "
                       "nothing of L5's informed it. Side agreement is not "
                       "policy agreement and a single row is not evidence.")})

    def shadow_close(self) -> None:
        """Report what a rolling window leaves the shadow holding."""
        if not self._shadow or self.ws is None:
            return
        slug = AB.slug_for(int(self.ws))
        for name in sorted(self._shadow):
            try:
                emit("L5_SHADOW_WINDOW_CLOSE",
                     self._shadow[name].window_close(slug))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["shadow_error:%s:%s"
                           % (name, type(exc).__name__)] += 1

    def calibration_shadow_tick(self, now: float, *, slug: str,
                                book: Any = None,
                                prints: Any = None) -> None:
        """Exercise the capture mechanism against REAL venue data, with no
        order and no venue call.

        WHY THIS EXISTS AND WHAT IT CANNOT PROVE. The recorder's purpose is
        to make a live order calibratable, and that cannot be verified by
        first discovering a problem during a money-moving probe. So the
        BASELINE shadow variant's modelled resting quotes are run through
        the same pre_submit / observe path, on the same authenticated
        ladder, book and prints the executor's hook would see.

        What this verifies: the ladder read, the queue bound and its
        provenance, the per-level print attribution, lifetime coverage
        accounting, durability across restarts, and the completeness
        verdict.

        What it CANNOT verify, by construction: the ack, fill and terminal
        halves, because there is no venue order. Every shadow record
        therefore reports `usable_for_calibration: false` with
        `terminal_state` missing -- and that is the anti-false-completeness
        guarantee showing itself in production rather than only in a test.
        """
        cal = self.fill_calibration
        if cal is None or self._shadow_off:
            return
        try:
            c = (self._shadow or {}).get(SHADOW.BASELINE_POLICY)
            if c is None:
                return
            sc = ((c.state.get("markets") or {}).get(slug)
                  or {}).get("scen") or {}
            rest = (sc.get(SHADOW.HEADLINE_SCENARIO) or {}).get("resting")
            seen = self._calib_shadow_seen
            if rest:
                coid = "shadow|%s|%s|%s" % (SHADOW.BASELINE_POLICY, slug,
                                            rest.get("t"))
                if coid not in seen:
                    seen[coid] = slug
                    cal.pre_submit(
                        coid, slug=slug,
                        side=(FM.UP if str(rest.get("side")) == "YES"
                              else FM.DOWN),
                        own_px=rest.get("price"), qty="1", book=book,
                        ladder=self.shadow_ladder, now=now)
                cal.observe(now, slug=slug, book=book,
                            ladder=self.shadow_ladder, prints=prints,
                            open_coids=[coid])
            else:
                # the modelled quote is gone: close every open shadow record
                # for this market, NEVER venue-confirmed, because no venue
                # was asked.
                for coid, s2 in list(seen.items()):
                    if s2 != slug:
                        continue
                    cal.on_terminal(coid, state="SHADOW_QUOTE_WITHDRAWN",
                                    reason="modelled_only",
                                    venue_confirmed=False, now=now)
                    seen.pop(coid, None)
            self.stats["calib_shadow_records"] = len(cal.orders)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["calib_shadow_error:%s" % type(exc).__name__] += 1

    def shadow_settle(self, now: float) -> None:
        """Settle a closed shadow window at the VENUE's own price.

        A PUBLIC read, with no credentials and no authenticated quota:
        /v1/markets/{slug}/bbo carries `settlementPx` beside `state`.
        Terminal state AND a binary price, or it is not settled -- an
        expired book's last price reaching a ledger as a payout is a defect
        this venue has already produced here once, and `settlement_decimal`
        plus TERMINAL_MARKET_STATES are the existing tested reads for it.

        THE READ IS `pmus_settlement`, NOT `pmus_bbo`, AND THAT WAS A BUG
        FOR 201 POLLS. `pmus_bbo` is a BOOK reader: an expired market
        answers /bbo with `settlementPx` present and bestBid/bestAsk both
        null, which it treats as an empty answer, falling back to /book --
        a route that carries no `settlementPx` at all. So the price was
        discarded for exactly the markets that had one, every closed window
        stayed stranded, and `shadow_awaiting_settlement` counted up
        against a venue that had already published the number. Measured on
        2026-10-19: 1700z, 1715z and 1730z were EXPIRED with settlementPx
        0.0000, 1.0000 and 0.0000, and this path read None for all three.

        Nothing is inferred. Without the venue's own number the window
        stays unsettled and is reported as stranded inventory.
        """
        if not self._shadow:
            return
        # the union across policies, so ONE venue read settles every
        # policy's copy of the same window at the same price
        want = set()
        for c in self._shadow.values():
            want.update(c.pending_settlement(now,
                                             grace_s=SHADOW_SETTLE_GRACE_S))
        for slug in sorted(want):
            if now - self._shadow_settle_t.get(slug, 0.0) < SETTLEMENT_POLL_S:
                continue
            self._shadow_settle_t[slug] = now
            try:
                md = OBS.pmus_settlement(slug) or {}
                state = str(md.get("state") or "")
                px = T.settlement_decimal(md.get("settlementPx"))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["shadow_settle_read_error"] += 1
                continue
            if state not in T.TERMINAL_MARKET_STATES or px not in (D(0), D(1)):
                self.stats["shadow_awaiting_settlement"] += 1
                continue
            for name in sorted(self._shadow):
                try:
                    emit("L5_SHADOW_SETTLED",
                         dict(self._shadow[name].settle(slug, px),
                              venue_state=state))
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    self.stats["shadow_error:%s:%s"
                               % (name, type(exc).__name__)] += 1

    #: every check `budget_selfcheck` must produce when budgets are
    #: configured. A missing one fails the report rather than being absent
    #: from it -- the first production run passed with two of these skipped.
    #: Read off the MODULE constant, never off `self`, so a caller cannot
    #: lose the completeness check by not carrying the attribute.
    EXPECTED_SELFCHECKS = EXPECTED_SELFCHECKS

    def budget_selfcheck(self) -> Dict[str, Any]:
        """Run the REAL budget enforcement in THIS process, on a throwaway
        ledger, and report what it did.

        WHY THIS EXISTS. While the shadow lock is on, the arming boundary
        refuses at SHADOW_ONLY_LOCK and `handle` never reaches a claim -- so
        the class budgets, the backstop exemption and the completion reserve
        are READ in production (the heartbeat reports them) but never
        EXERCISED there. Unit tests prove the logic; they do not prove that
        the deployed image, with the deployed variables, on the deployed
        volume, enforces it. This closes exactly that gap.

        IT CANNOT TOUCH THE REAL BUDGETS. Everything runs under a
        throwaway experiment id in its own directory, which is removed
        afterwards. The real ledger path is never passed in, no real claim
        or ticket is consumed, no transport exists in this call, and nothing
        is submitted or cancelled. It is a pure read-and-write-to-scratch
        check whose only output is this report.

        A FAILURE HERE IS A FAILURE TO REPORT, NOT TO WORK AROUND. If a
        check comes back false, the enforcement an operator believes is in
        place is not in place, and that is the finding.
        """
        import shutil
        import tempfile
        out: Dict[str, Any] = {"ran": False, "checks": {}, "error": None,
                               "labels": ("the REAL enforcement functions, "
                                          "run in this process against a "
                                          "throwaway ledger. No real claim "
                                          "or ticket is consumed and nothing "
                                          "reaches a venue.")}
        base = None
        try:
            root = self.root or tempfile.gettempdir()
            base = tempfile.mkdtemp(prefix="budget_selfcheck_", dir=root)
            led = os.path.join(base, "scratch_auth.jsonl")
            exp = "selfcheck-%d" % os.getpid()
            o_cap = AUTH.max_of_class("OPEN")
            c_cap = AUTH.max_of_class("COMPLETE")
            t_cap = AUTH.max_submissions()
            ck = out["checks"]
            ck["budgets_configured"] = bool(
                o_cap is not None and c_cap is not None)
            if not ck["budgets_configured"]:
                out["ran"] = True
                out["note"] = ("no class budgets configured, so there is "
                               "nothing to enforce and nothing to check")
                return out

            # 1. OPEN exhausts at its own ceiling, and the ceiling is the
            #    deployed value rather than a default.
            n = 0
            while True:
                try:
                    AUTH.claim_class_ticket(led, exp, "OPEN")
                    n += 1
                except AUTH.Refused:
                    break
                if n > int(o_cap) + 4:
                    break
            ck["open_exhausts_at_its_ceiling"] = (n <= int(o_cap))
            ck["open_tickets_taken"] = n

            # 2. COMPLETE is UNAFFECTED by an exhausted OPEN budget. This is
            #    the whole reason the budget was split.
            try:
                AUTH.claim_class_ticket(led, exp, "COMPLETE")
                ck["complete_survives_exhausted_open"] = True
            except AUTH.Refused as exc:
                ck["complete_survives_exhausted_open"] = False
                ck["complete_refusal"] = str(exc.reason)

            # 3. THE BACKSTOP EXEMPTION. Spend the flat total with
            #    non-reducing submissions, then show a reducing one still
            #    goes. Uses a second experiment id so the counts above do
            #    not interfere.
            if t_cap is not None:
                for i in range(int(t_cap) + 2):
                    try:
                        AUTH.claim_accumulation_leg(
                            "selfcheck-market", i, "UP", {}, path=led,
                            klass="REPLACE", attempt=1, reducing=False)
                    except AUTH.Refused:
                        break
                blocked = False
                try:
                    AUTH.claim_accumulation_leg(
                        "selfcheck-market", 900, "UP", {}, path=led,
                        klass="REPLACE", attempt=1, reducing=False)
                except AUTH.Refused:
                    blocked = True
                ck["backstop_binds_a_non_reducing_order"] = blocked
                exempt = False
                try:
                    AUTH.claim_accumulation_leg(
                        "selfcheck-market", 901, "DOWN", {}, path=led,
                        klass="COMPLETE", reducing=True)
                    exempt = True
                except AUTH.Refused as exc:
                    ck["exemption_refusal"] = str(exc.reason)
                ck["backstop_exempts_a_reducing_order"] = exempt

            # 4. THE COMPLETION RESERVE. Spend COMPLETE down below the
            #    reserve, then show OPEN is refused.
            #
            #    ITS OWN DIRECTORY, AND THE REAL EXPERIMENT ID. The claim
            #    paths read `experiment_id()` themselves -- they cannot be
            #    told to use another one -- so the tickets have to be
            #    written under that same id or the reserve would be
            #    computed for an experiment with nothing spent and the
            #    check would pass vacuously. Isolation therefore comes from
            #    the DIRECTORY, which is this scratch tree, never the real
            #    ledger's.
            sub3 = os.path.join(base, "reserve")
            os.makedirs(sub3, exist_ok=True)
            led3 = os.path.join(sub3, "scratch3.jsonl")
            exp3 = AUTH.experiment_id() or exp
            for _ in range(int(c_cap)):
                try:
                    AUTH.claim_class_ticket(led3, exp3, "COMPLETE")
                except AUTH.Refused:
                    break
            ck["reserve_setup_complete_spent"] = len(
                AUTH.class_spent(led3, exp3, "COMPLETE"))
            reserved = False
            why = None
            try:
                AUTH.claim_accumulation_leg(
                    "selfcheck-market", 0, "UP", {}, path=led3, klass="OPEN")
            except AUTH.Refused as exc:
                why = str(exc.reason)
                reserved = (why == "NO_COMPLETION_CAPACITY_RESERVED")
            ck["open_refused_without_completion_capacity"] = reserved
            ck["reserve_refusal"] = why

            # 5. THE BALANCING SAFEGUARD, from the DEPLOYED EXECUTOR
            #    CLASS, bound to a stub book.
            #
            # WHY THE CLASS AND NOT THE INSTANCE. `Runner.__init__` sets
            # `self.executor = None`; the real FaithfulExecutor is built in
            # `roll()`, on the first market roll. The first production run
            # of this self-check therefore found `executor is None`,
            # skipped these two checks, and still reported all_passed TRUE
            # -- a self-check that passes by not running. Binding the
            # class's own method to a stub store tests the same code at
            # startup, before any roll, and does not depend on
            # construction order.
            #
            # `executor_live` records whether an instance exists yet, for
            # the operator; it is reported and never a condition.
            # REPORTED, NOT CHECKED. At startup there is legitimately no
            # instance yet, so this must not sit in `checks`, where every
            # bool has to be true for the verdict to pass.
            out["executor_live"] = getattr(self, "executor", None) is not None
            out["executor_type"] = type(
                getattr(self, "executor", None)).__name__
            # this one IS a check: the deployed class must carry the method.
            ck["executor_has_balancing"] = hasattr(
                LX.FaithfulExecutor, "balancing_refusal")

            class _Probe:
                balancing_refusal = LX.FaithfulExecutor.balancing_refusal
                prospective_residual = LX.FaithfulExecutor.prospective_residual
                submission_class = LX.FaithfulExecutor.submission_class

                class _Book:
                    def residual(self, m):
                        return D("2.46")

                    def outstanding_residual(self, m):
                        return D(0)

                def __init__(self):
                    self.store = self._Book()

            probe = _Probe()
            over = probe.balancing_refusal(
                "selfcheck-market", {"side": "DOWN"},
                {"qty": D("5.00"), "price": D("0.90")}, "COMPLETE")
            inside = probe.balancing_refusal(
                "selfcheck-market", {"side": "DOWN"},
                {"qty": D("2.00"), "price": D("0.90")}, "COMPLETE")
            ck["overshoot_is_refused"] = bool(
                over and over.get("reason") == "BALANCING_WOULD_OVERSHOOT")
            ck["a_balance_inside_the_need_is_allowed"] = (inside is None)
            # and the CLASSIFIER, which decides which budget pays
            ck["complement_classified_as_COMPLETE"] = (
                probe.submission_class("selfcheck-market", {"side": "DOWN"},
                                       {"qty": D("1.00"),
                                        "price": D("0.90")}) == "COMPLETE")
            ck["a_reprice_classified_as_REPLACE"] = (
                probe.submission_class("selfcheck-market", {"side": "DOWN"},
                                       {"qty": D("1.00"),
                                        "price": D("0.90")},
                                       attempt=1) == "REPLACE")
            out["ran"] = True
            # EVERY EXPECTED CHECK MUST BE PRESENT. A check that did not run
            # is a failure, not a pass: the enforcement it was meant to
            # demonstrate is undemonstrated either way.
            missing = [k for k in EXPECTED_SELFCHECKS
                       if k not in ck]
            out["missing_checks"] = missing
            out["all_passed"] = (
                not missing
                and all(v for k, v in ck.items() if isinstance(v, bool)))
            return out
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            out["all_passed"] = False
            return out
        finally:
            if base:
                try:
                    shutil.rmtree(base, ignore_errors=True)
                except BaseException:                           # noqa: BLE001
                    pass

    def campaign_window_is_usable(self, market: Any) -> Dict[str, Any]:
        """Could this window still do anything? Spend one only if so.

        TWO WAYS A WINDOW EARNS ITS PLACE IN THE BUDGET: a first leg can
        still be opened in it, or we are already working inventory in it and
        completion is still permitted. Past the entry cutoff with nothing
        held, there is nothing it can do, and reserving it would turn four
        approved windows into three for no trade -- which is what happened
        to 1630z, reserved at 16:39 when entry had closed at 16:38:59.

        FAIL CLOSED means NOT USABLE here. If the venue bounds cannot be
        read, the window cannot be shown to be worth spending, and the safe
        direction is to keep the budget rather than to spend it on a window
        we cannot reason about.
        """
        try:
            b = self.venue_window_bounds() or {}
            start = dt.datetime.fromisoformat(str(b["start"]))
            end = dt.datetime.fromisoformat(str(b["end"]))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"usable": False, "reason": "WINDOW_BOUNDS_UNREADABLE",
                    "error": type(exc).__name__}
        from src import l5_two_sided_probe as TS
        t0, t1 = start.timestamp(), end.timestamp()
        now = float(self.now_t)
        enter = TS.may_enter(now, t0, t1)
        # ENOUGH RUNWAY, NOT MERELY AN OPEN DOOR. This used to accept any
        # window where `may_enter` returned None, which is a question about
        # THIS INSTANT and says nothing about how much entry time is left.
        # On 2026-10-23 that let a deployment finishing at T+441s claim
        # 0000z: entry was still permitted, 159s of runway remained,
        # `may_enter` began refusing TOO_LATE_TO_WITHDRAW at T+565s, the
        # book collapsed to 0.0100/0.0100, and the single approved window
        # was spent for zero submissions.
        #
        # `window_eligible` measures the runway by asking `may_enter` where
        # it stops -- `t1` is the VENUE's close and can be earlier than
        # start + WINDOW_S implies, so the bound cannot be recomputed from
        # constants. It writes nothing, so asking is free.
        #
        # THE INVENTORY BRANCH BELOW IS DELIBERATELY NOT GATED ON RUNWAY.
        # Runway bounds OPENING a leg; a position already held must stay
        # completable for as long as `may_complete` allows, because that
        # path REDUCES risk. Gating it would strand inventory to save a
        # budget entry, which is the wrong trade.
        if enter is None:
            elig = TS.window_eligible(now, t0, t1)
            if elig.get("ok"):
                return {"usable": True,
                        "because": "A_FIRST_LEG_MAY_STILL_OPEN",
                        "seconds_into_window": round(now - t0, 1),
                        "entry_runway_s": elig.get("runway_s"),
                        "entry_runway_required_s": elig.get("required_s")}
            # entry is open but too little of it is left to be worth an
            # irreversible claim. Fall through to the inventory branch: if
            # we hold something here the window still earns its place.
            enter = "INSUFFICIENT_ENTRY_RUNWAY:%s<%s" % (
                elig.get("runway_s"), elig.get("required_s"))
        complete = TS.may_complete(now, t0, t1)
        held = None
        try:
            held = self.store.residual(str(market))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"usable": False, "reason": "INVENTORY_UNREADABLE",
                    "error": type(exc).__name__, "entry": enter}
        if held is not None and held != 0 and complete is None:
            return {"usable": True, "because": "INVENTORY_STILL_TO_COMPLETE",
                    "residual": str(held), "entry": enter}
        return {"usable": False, "reason": "PAST_ENTRY_WITH_NO_INVENTORY",
                "entry": enter, "completion": complete,
                "residual": None if held is None else str(held),
                "seconds_into_window": round(now - t0, 1)}

    def campaign_telemetry(self) -> Dict[str, Any]:
        """What the bounded campaign currently permits. READ-ONLY.

        THE NONCE IS NEVER PUBLISHED. The document identifies itself here by
        sha256 alone -- the same rule `HaltGate` telemetry follows for the
        latch -- so an operator can confirm WHICH approval is in force
        without the log carrying something that bounds a live run.

        Reporting is fail-closed in the same direction as the gate: a figure
        this cannot establish is reported as None with its reason, never as a
        comfortable number.
        """
        out: Dict[str, Any] = {
            "approval_present": None, "approval_state": None,
            "document_sha256": None, "permits_this_window": None,
            "reason": None, "windows_spent": None,
            "windows_remaining": None, "max_windows": None,
            "submissions_spent": None, "max_submissions": None,
            "class_budgets": None, "completion_reserve": None,
            "exact_market_binding": None, "blocks_advancement": None,
            "labels": ("the campaign BOUNDS a run; it does not authorize "
                       "one. The owner handshake and the shadow lock still "
                       "decide that. The nonce is identified by digest "
                       "only and never emitted.")}
        # THE SUBMISSION BUDGET, counted from the durable claim locks rather
        # than from a counter in memory, so a redeploy cannot hand any back.
        try:
            cap = AUTH.max_submissions()
            spent = AUTH.submissions_spent(AUTH.ledger_path(),
                                           AUTH.experiment_id())
            out["max_submissions"] = cap
            out["submissions_spent"] = len(spent)
            # AND THE THREE CLASS BUDGETS, which are the operative limits.
            # `max_submissions` is the finite backstop below their sum; an
            # operator reading only the total cannot tell whether the
            # budget that is about to run out is the one that OPENS
            # exposure or the one that UNWINDS it.
            out["class_budgets"] = AUTH.class_budget_report(
                AUTH.ledger_path(), AUTH.experiment_id())
            # THE COMPLETION RESERVE, reported as a verdict and not just as
            # numbers. "Could a new exposure-increasing order still be
            # closed?" is the question that matters, and an operator should
            # not have to do the arithmetic to answer it.
            _short = AUTH.completion_capacity_refusal(
                AUTH.ledger_path(), AUTH.experiment_id())
            out["completion_reserve"] = {
                "submissions_per_obligation":
                    AUTH.COMPLETION_SUBMISSIONS_PER_OBLIGATION,
                "may_open": _short is None,
                "why_not": None if _short is None else _short.get("reason"),
                "detail": _short,
                "labels": ("a risk-reducing order is NEVER refused by the "
                           "flat backstop; a new exposure-increasing order "
                           "is refused unless the capacity to close it is "
                           "still reserved")}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["submissions_spent"] = "UNREADABLE:%s" % type(exc).__name__
        # THE APPROVAL ITSELF, read WITHOUT reserving a window. `state()` is
        # the document's own verdict; `authorize_window` is what spends, and
        # telemetry must never spend.
        try:
            # THE CANONICAL GATE, so the status reported here is the
            # verdict try_arm actually enforces rather than a stricter one.
            g = CAMP.approval_gate()
            st = g.state()
            out["approval_state"] = g.last_state
            out["document_sha256"] = g.last_sha256
            out["approval_present"] = (g.last_state != "approval_absent")
            scope = dict(st.get("scope") or {})
            if scope:
                out["max_windows"] = scope.get("max_windows")
                out["series"] = scope.get("series")
                out["expires_utc"] = scope.get("expires_utc")
                out["clamped"] = scope.get("clamped")
                led = CAMP.WindowLedger(self.root).read(
                    str(scope.get("nonce_sid")))
                spent_w = list(led.get("windows") or ())
                out["windows_spent"] = len(spent_w)
                out["windows"] = spent_w
                out["windows_remaining"] = max(
                    0, int(scope.get("max_windows") or 0) - len(spent_w))
                slug = (self.transport.market_slug
                        if self.transport else None)
                if slug:
                    # an already-reserved window still permits: re-arming
                    # inside it resumes rather than spending a second.
                    out["permits_this_window"] = bool(
                        slug in spent_w or out["windows_remaining"] > 0)
            else:
                out["reason"] = st.get("why")
            # A STALE EXACT-MARKET BINDING STOPS ADVANCEMENT DEAD.
            # `L5_LIVE_PROBE_MARKET` pins authorization to ONE slug and wins
            # over the series mandate by design, so a value left over from a
            # single-window test would let window one arm and refuse windows
            # two to four with AUTHORIZATION_BOUND_TO_MARKET. That is a
            # tightening, not a hole -- which is exactly why it must be
            # VISIBLE rather than discovered from a refusal counter at
            # 1615z.
            _pin = (os.getenv(AUTH.MARKET_ENV) or "").strip()
            out["exact_market_binding"] = _pin or None
            if scope and _pin:
                _slug = (self.transport.market_slug
                         if self.transport else None)
                out["blocks_advancement"] = True
                out["advancement_note"] = (
                    "%s pins authorization to one market; the other windows "
                    "of this campaign will refuse "
                    "AUTHORIZATION_BOUND_TO_MARKET%s"
                    % (AUTH.MARKET_ENV,
                       "" if not _slug or _slug == _pin
                       else " -- and THIS window (%s) is not the pinned one"
                            % _slug))
            elif scope:
                out["blocks_advancement"] = False
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["reason"] = "CAMPAIGN_TELEMETRY_FAILED:%s" % type(exc).__name__
        return out

    def day_realized_line(self) -> Dict[str, Any]:
        """The figure the DAILY loss halt actually reads, published.

        `realized_today()` -- the all-time total the cumulative gates read --
        was the only realized number on the heartbeat, and it is NOT what
        the daily halt evaluates. An operator checking whether the daily
        ceiling has room was reading the wrong line. None here means the day
        could not be computed, which is exactly what REFUSES at the gate.
        """
        try:
            day = self.store.realized_current_utc_day()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"utc_day_realized_usd": None,
                    "reason": "DAY_READ_FAILED:%s" % type(exc).__name__,
                    "halt_usd": str(self.limits.daily_realized_loss_halt_usd),
                    "room_usd": None}
        halt = self.limits.daily_realized_loss_halt_usd
        return {
            "utc_day": now_utc().strftime("%Y-%m-%d"),
            "utc_day_realized_usd": None if day is None else str(day),
            "halt_usd": str(halt),
            "would_halt": None if day is None else bool(day <= -halt),
            "room_usd": None if day is None else str(day + halt),
            "all_time_realized_usd": str(self.store.realized_today()),
            # WHY IT MIGHT BE UNKNOWN, not just that it is. `realized_
            # current_utc_day` returns None whenever an UNATTRIBUTED
            # residual is nonzero -- a figure that belongs to no day cannot
            # be excluded from today -- or when any settlement row is
            # undated. Both refuse at the gate, and an operator seeing only
            # `None` cannot tell which, nor what to reconcile.
            "unattributed_usd": str(
                self.store.state.get("realized_unattributed") or 0),
            "undated_settlements": [
                m for m, row in (self.store.state.get("retired_markets")
                                 or {}).items()
                if isinstance(row, Mapping) and not str(row.get("t") or "")
            ][:8],
            "labels": ("the DAILY ceiling reads utc_day_realized_usd; the "
                       "separate cumulative ceilings read "
                       "all_time_realized_usd. None is UNKNOWN and refuses "
                       "at the gate -- it is never read as zero.")}

    def _ingest_report_safe(self) -> Dict[str, Any]:
        """`ingest_report` behind a total wrap.

        It is REPORTING. A raise here would take the whole heartbeat with
        it, and the heartbeat is how an operator sees the halt gate, the
        campaign budget and the day's realized total -- losing those to a
        counter would be a worse fault than the one this telemetry exists
        to expose.
        """
        try:
            return self.ingest_report()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"error": type(exc).__name__,
                    "labels": "ingest telemetry unavailable; it decides "
                              "nothing and refuses nothing"}

    def ingest_report(self) -> Dict[str, Any]:
        """Every stage between L5's published trade and `handle`.

        Compact by construction: the slug histogram is the top few only,
        because the whole point is that this survives the log line limit.
        """
        ing = {k: v for k, v in self.stats.items()
               if isinstance(k, str) and k.startswith("ingest:")}
        # A COUNT WITH NO CLOCK ON IT CANNOT ANSWER THE QUESTION BEING ASKED.
        #
        # "zero new matching events" and "947 matching events, the last one
        # 40 minutes ago" are different diagnoses, and a process-lifetime
        # total reports them identically -- the same ambiguity `TimedCounter`
        # was built for after the heartbeat printed `halt: false` through
        # 2,939 refusals. So every ingest counter is published WITH the age
        # of its last movement, and the two clocks that decide the question
        # get their own fields.
        ages = {}
        try:
            allages = self.stats.ages()
            ages = {k: round(v, 1) for k, v in allages.items()
                    if isinstance(k, str) and k.startswith("ingest:")}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        top = collections.Counter(self.ingest_slugs).most_common(6)
        btc = sum(n for sl, n in self.ingest_slugs.items()
                  if sl.startswith("btc-updown-15m-"))
        return {
            "cursor": self.cursor.report(),
            "max_action_age_s": self.max_action_age_s,
            "counters": ing,
            "counter_age_s": ages,
            "feed_errors": self.stats.get("feed_errors", 0),
            "distinct_slugs_seen": len(self.ingest_slugs),
            "rows_btc15m": btc,
            "top_slugs_seen": [{"slug": sl, "rows": n} for sl, n in top],
            # THE THREE CLOCKS THAT SEPARATE THE THREE DIAGNOSES.
            #   last_row_utc      -- the feed served us SOMETHING
            #   last_btc15m_utc   -- a BTC15m-slugged row existed
            #   last_admitted_utc -- one reached handle()
            # absent at the source: no last_row/last_btc15m advance;
            # silently filtered: last_btc15m advances, last_admitted does not;
            # never reaching handle: last_admitted stale with refusals rising.
            "last_row_utc": self._iso(self.last_row_t),
            "last_btc15m_utc": self._iso(self.last_btc15m_t),
            "last_admitted_utc": self._iso(self.last_admitted_t),
            "newest_row_event_utc": self._iso(self.newest_row_event_t),
            "labels": ("rows_btc15m counts rows of L5's OWN feed whose slug "
                       "is a BTC15m window, before any other filter. Zero "
                       "of it with distinct_slugs_seen > 0 establishes that "
                       "L5 is trading other markets -- which is a finding, "
                       "not a fault. Zero of BOTH with polls_ok rising "
                       "means the feed is serving an empty or unchanged "
                       "page. An observed action is not a fill of ours and "
                       "admitted does not mean submitted."),
        }

    def heartbeat(self) -> None:
        t = self.transport
        emit("L5_LIVE_HB", {
            "utc": now_utc().isoformat(), "role": SERVICE_ROLE,
            "market": t.market_slug if t else None,
            "armed": bool(t and t.armed), "preflight": self.preflight_status,
            "owner_arm_token": Q1.armed(),
            # THE WALLET INGESTION STAGE, EARLY IN THE LINE AND ON PURPOSE.
            #
            # The heartbeat exceeds the log transport's 81,920-byte line
            # limit and its tail is truncated in production -- `stats` sits
            # past that cut, which is why the 2026-10-26 campaign could not
            # be diagnosed from its own heartbeats. This block is placed
            # ahead of everything large so the question "did an L5 action
            # reach us, and if not what refused it" survives truncation.
            "l5_ingest": self._ingest_report_safe(),
            # THE CONTRADICTION THIS REPLACES. `halt` was `self.halted()`,
            # which reads the LOCAL env override L5_LIVE_HALT alone. The gate
            # that actually refuses reads the DURABLE watchdog latch over
            # HTTP -- so the heartbeat printed "halt": false through 2,939
            # consecutive refusals on
            # HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER. The field
            # an operator reads now reports the EFFECTIVE submission gate,
            # and `halt_local_env` keeps the old meaning under its own name.
            "halt": self.submission_gate().get("blocked"),
            "halt_local_env": self.halted(),
            "submission_gate": self.submission_gate(),
            # THE BOUNDED CAMPAIGN and THE DAILY CEILING, on every
            # heartbeat. Both were decidable only by reading code; a budget
            # an operator cannot see is one nobody notices binding.
            "campaign": self.campaign_telemetry(),
            "day_realized": self.day_realized_line(),
            "auth_book": self.book.status(),
            "fill_calibration": (None if self.fill_calibration is None
                                 else self.fill_calibration.report()),
            "frame_fanout": (self.fanout.status() if self.fanout else None),
            # THE INDEPENDENT DECISION SOURCE, on every heartbeat. Phase A's
            # deliverable is that it emits continuously, and a counter an
            # operator has to go looking for does not demonstrate that.
            # COMPACT. The per-window lists are 96% of a full report, so
            # six arms once a minute is 1.28 MB a heartbeat -- about 1.8 GB
            # of logs a day -- for rows already durable in the state files
            # and already aggregated into L5_SHADOW_TWO_SIDED. The omitted
            # COUNT is reported, so this can never read as an arm that has
            # settled nothing.
            "shadow_controller": ({n: c.report(compact=True)
                                   for n, c in self._shadow.items()}
                                  if self._shadow
                                  else {"off": self._shadow_off}),
            "book_now": self.book.snapshot(t.market_slug) if t else None,
            "stats": dict(self.stats),
            # SECONDS SINCE EACH COUNT LAST MOVED. Without this a
            # lifetime counter reads as a live condition; see
            # TimedCounter for the 2026-10-18 case that is exactly
            # why it is here.
            "stats_age_s": self.stats.ages(),
            "committed_usd": {k: str(v) for k, v in self.store.committed().items()},
            # GROSS CASH OUT, beside the gate and never instead of it. The
            # gate is worst-case loss and is unchanged at $5; this is what
            # actually left the account, which on 2100z was $7.4871 under a
            # $1.8171 worst case. Reported so the difference is visible.
            "cash_deployed": [self.store.cash_deployed_usd(m)
                              for m in self.store.markets_with_exposure()],
            # UNRESOLVED ACCOUNTING, on EVERY heartbeat. A record that is
            # only visible when something goes looking for it is one that
            # can be forgotten.
            "unresolved_accounting": {
                "count": len(unresolved_accounting()),
                "records": unresolved_accounting(),
                "blocks_live_authorization": bool(unresolved_accounting()),
                "labels": ("a possible loss of UNKNOWN size is on the "
                           "books. realized P&L is therefore unknown and "
                           "the daily and cumulative loss limits cannot be "
                           "evaluated, so live authorization is refused. "
                           "This is NOT reconciled and NOT repaired."),
            },
            "available_cash": {
                "usd": (None if self.available_cash_usd is None
                        else str(self.available_cash_usd)),
                "age_s": (None if self.available_cash_t is None
                          else round(self.now_t - self.available_cash_t, 1)),
                "source": "/v1/account/balances",
                "labels": ("None is no reading, never unlimited. A number "
                           "only ADDS a refusal to the cash gate.")},
            "risk_gate": {
                "measures": "worst-case loss over both settlements",
                "max_usd_per_market": str(self.limits.max_usd_per_market),
                "note": ("cash_deployed is reported, NOT gated. Changing "
                         "which of the two the gate reads is an explicit "
                         "decision and has not been made."),
            },
            # SELF-INFLICTED RATE LIMIT TELEMETRY. 2026-09-26 ran >1 req/s
            # sustained and the venue answered 429; the fallback from that
            # is what lost a cost basis. These say whether it is fixed.
            "venue_requests": t.limiter.metrics() if t else None,
            "order_progress": self.order_progress_latency_s(self.now_t),
            "preflight_attempts": self.preflight_attempts,
            "pending_terminal": sorted(self.store.pending_terminal()),
            "quarantined": sorted(self.store.quarantined()),
            "cooloff_remaining_s": round(self.venue_cooling(), 2),
            "retired_markets": sorted(
                self.store.state.get("retired_markets") or {}),
            "realized": self.store.realized_lines(),
            # THE FOUR POPULATIONS, APART. account-wide, verified L5, manual
            # and unknown, with the identity stated and never a guessed
            # bucket. Reported so the -218.99 cannot be read as verified L5
            # or as the owner's measured manual losses.
            "accounting": self.store.four_bucket_accounting(),
            "l5_baseline": self.store.l5_baseline_lines(),
            # ACCOUNT-WIDE EXPOSURE, reported independently of the decision
            # path. The aggregation itself runs inside the gate, which the
            # account-wide loss check refuses BEFORE reaching -- so without
            # this line the new figure could not be verified in production at
            # all. Read-only, gates nothing, and reports its own failure
            # rather than a zero.
            "account_exposure": self.account_exposure_lines(),
            "limits": self.limits.as_dict(),
            # THE $1 MANDATE WAS INVISIBLE HERE. max_cash_deployed_usd lives
            # on ValidationGates, not on SafetyLimits, so the limits block
            # showed only the $5 worst-case ceilings and the mandate was a
            # recollection rather than an observation. Reported from the
            # OBJECT THAT ENFORCES IT, never from the environment, so a
            # heartbeat cannot claim a bound the decision path is not using.
            "effective_cash_gate": self.effective_cash_gate(),
            "lambda": str(self.lam),
            # WHICH POLICY RAN. k is echoed from the executor that is
            # actually wired, not from the environment, so a heartbeat can
            # never claim a treatment the decision path is not applying.
            "policy": {
                "name": ("L5_NATIVE" if getattr(self.executor,
                                                "residual_cap_k", None) is None
                         else "RESIDUAL_CAP_k%s" % getattr(
                             self.executor, "residual_cap_k")),
                "residual_cap_k": (None if getattr(
                    self.executor, "residual_cap_k", None) is None
                    else str(self.executor.residual_cap_k)),
                "residual_cap_binds_session": int(self.stats.get(
                    "SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP", 0)),
                "residual_cap_binds_this_market": int(self.win.get(
                    "SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP", 0)),
                "max_clip_filled": {k: str(v) for k, v in
                                    (self.store.state.get("max_clip")
                                     or {}).items()},
                "basis": ("PROSPECTIVE_NET: prospective = realized residual + "
                          "outstanding orders in this market as-if-filled. "
                          "Refuse iff the prospective book is NOT flat AND the "
                          "order increases |prospective| AND |prospective "
                          "after| > k * max(largest filled clip, this order's "
                          "qty). Contracts. A leg that shrinks |prospective| "
                          "is never refused at any depth.")},
            "frozen_rule": "030d8bf"})
        self.emit_two_sided_comparison()

    def emit_two_sided_comparison(self) -> None:
        """The two-sided comparison, as ONE SHORT LINE.

        The arm reports already ride in the heartbeat, but that document is
        now a quarter of a megabyte and the log transport splits it into
        four fragments, so the numbers that answer the question are present
        and unreadable. This emits just the comparison -- a few kilobytes,
        one line, one fragment.

        It computes NOTHING of its own: `compare_all` is the same
        function `analysis/l5/two_sided_comparison.py` calls, so the
        production line and an offline run of the same reports cannot
        disagree. It carries BOTH pairings against the one control --
        permission to rest a second side, and actually quoting both --
        with the requirement-8 mechanical check hoisted to the top.

        REPORTING ONLY. It gates nothing, no money path reads it, and a
        failure here must not cost the heartbeat that has already been
        emitted above.
        """
        if not self._shadow:
            return
        try:
            emit("L5_SHADOW_TWO_SIDED", SHADOW.compare_all(
                {n: c.report() for n, c in self._shadow.items()}))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["two_sided_compare_error:%s"
                       % type(exc).__name__] += 1

    def venue_cooling(self) -> float:
        """Seconds of venue cool-off remaining, or 0.

        THE P1 FIX, and it is in the CALLER rather than the limiter. The
        limiter's contract is unchanged and still proven: shared bucket,
        shared cool-off, raises VenueThrottled without sending. What was
        wrong is that every path kept calling it anyway -- 2,601 refusals
        inside one ~3s cool-off on the first occurrence, 19,420 by the end
        of the run. None reached the venue, but each one is a caller asking
        a question it already knows the answer to.

        So the tick asks ONCE and skips the venue-touching work while the
        answer is no. Nothing here decides an order is safe, terminal or
        flat: skipping a read leaves every piece of state exactly as
        unknown as it was, which is the direction that fails closed.
        """
        t = self.transport
        lim = getattr(t, "limiter", None) if t is not None else None
        if lim is None:
            return 0.0
        m = lim.metrics()
        return float(m["cooloff_remaining_s"]) if m.get("in_cooloff") else 0.0

    #: how often the independent latch is consulted for the CANCEL decision.
    #: The submission path reads it on every action already; this is the
    #: separate question of whether orders ALREADY RESTING must come in, and
    #: it only needs asking while something is resting.
    HALT_SWEEP_EVERY_S = 10.0

    def venue_window_bounds(self) -> Optional[Dict[str, Any]]:
        """This window's bounds AS THE VENUE STATES THEM, or None.

        Our slug is built from a 900-second clock grid (`AB.slug_for`), so the
        window we think we are trading is OUR convention, not the venue's
        statement. The two agree until they do not, and the case where they
        disagree is exactly the one a pre-close margin exists for. So the
        venue's own `startTime` is read back for this slug and the bounds are
        only offered when it CONFIRMS -- `OBS.verify_alignment` is the
        existing, tested read that does it, and it is reused rather than
        reimplemented.

        Cached per window: one confirmation per fifteen minutes, not one per
        tick. A failure caches as None for this window, so arming refuses
        WINDOW_BOUNDS_UNCONFIRMED by name instead of silently falling back to
        the clock-derived window.
        """
        ws = self.ws
        if ws is None:
            return None
        if ws in self.window_bounds:
            return self.window_bounds[ws]
        out: Optional[Dict[str, Any]] = None
        try:
            out = OBS.venue_window_bounds(int(ws), AB.WINDOW_S)
            if out is None:
                emit("L5_LIVE_WINDOW_UNCONFIRMED",
                     {"market": AB.slug_for(int(ws)),
                      "labels": ("the venue did not confirm this window's "
                                 "start, so no venue-bounded authorization "
                                 "can be issued for it")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out = None
            emit("L5_LIVE_WINDOW_UNCONFIRMED",
                 {"market": AB.slug_for(int(ws)),
                  "why": "%s" % type(exc).__name__})
        # cached either way: an unconfirmed window must not be re-read on
        # every tick, and must not become confirmed by retrying.
        self.window_bounds[ws] = out
        return out

    def maybe_expire_authorization(self, now: float) -> Optional[Dict[str, Any]]:
        """A CLAIM THAT HAS EXPIRED MUST ALSO CANCEL, not merely stop arming.

        The claim's expiry is now the EARLIER of its TTL and the venue's close
        less the margin, so this is what makes the pre-close margin mean
        something: at that instant any resting order is cancelled by VENUE id
        and proved terminal by re-reading it. `deadman` is reused exactly as
        the halt path reuses it, so `still_open` and `unverified` mark the
        store UNCERTAIN -- which `authorize_arming` then refuses on -- instead
        of being reported as success.
        """
        t = self.transport
        claim = getattr(self, "arm_claim", None)
        if t is None or not t.armed or not claim:
            return None
        why = AUTH.still_valid(
            claim, limits=getattr(getattr(self, "executor", None),
                                  "limits", None))
        if not why:
            return None
        rec = LX.deadman(self.store, t, reason="authorization_expired:%s" % why)
        emit("L5_LIVE_HALT", dict(
            rec, source="AUTHORIZATION_EXPIRY", why=why,
            expires_utc=claim.get("expires_utc"),
            window=claim.get("window"),
            labels=("the authorization no longer covers this instant, so "
                    "resting orders were cancelled by VENUE id and the "
                    "transport disarmed. `cancelled` means the venue "
                    "reported terminal on re-read; anything else marks the "
                    "store UNCERTAIN rather than claiming success.")))
        t.disarm("authorization_expired")
        self.arm_claim = None
        return rec

    #: how often an UNARMED process re-attempts to withdraw an order it owns.
    #: Longer than the halt sweep: this is a recovery path, not a hot path.
    ORPHAN_SWEEP_EVERY_S = 30.0

    #: how often the SHADOW two-sided evaluator prints an interim row. Low
    #: enough to see a window develop, high enough not to bury the heartbeat.
    TWO_SIDED_REPORT_S = 180.0
    #: the completion decision is re-evaluated this often inside the
    #: completion window. Often enough to catch a book that improves,
    #: rarely enough not to spend the venue budget on a read per tick.
    TS_COMPLETION_EVERY_S = 10.0

    def maybe_cancel_orphans(self, now: float) -> Optional[Dict[str, Any]]:
        """WITHDRAW AN ORDER WE OWN BUT CAN NO LONGER MANAGE.

        THE DEFECT THIS EXISTS FOR. Every cancellation path in this runner was
        gated on `transport.armed`: the window-end deadman, the authorization
        expiry, the halt sweep and the operator halt all return early when the
        transport is not armed. That is correct for an orderly session -- but
        the dangerous case is the DISORDERLY one. If this process dies with an
        order resting, the restarted process cannot arm (a new pid is not the
        recorded holder, and the shadow lock refuses first anyway), so NOTHING
        cancelled it. Reconciliation adopted the order and marked the book
        uncertain, which correctly stopped all further trading -- and left the
        order resting at the venue until the venue itself expired it, which is
        exactly the orphan risk, and venue-side expiry is UNKNOWN.
        Independent monitoring cannot close it either: the watchdog holds no
        credentials by design, so it can latch a halt but cannot withdraw
        anything.

        CANCELLATION DOES NOT NEED AUTHORIZATION, and that is the whole
        resolution. Both locks gate SUBMISSION: `ShadowOnlyTransport` blocks
        `submit` alone and says so ("CANCEL IS DELIBERATELY NOT BLOCKED.
        Cancelling only ever reduces exposure, and a lock that prevented it
        could strand a resting order"), and the owner token gates arming.
        Withdrawing an order we already own only ever reduces exposure, so an
        unarmed process may -- and must -- do it.

        ONLY OUR OWN ORDERS. `deadman` iterates this ledger's `live_orders`
        and cancels by VENUE id, so an order we never recorded is untouched: a
        manual order placed by the owner is not in this book and is never
        cancelled by this path.

        PROOF, NOT ACKNOWLEDGEMENT. `deadman` re-reads every id afterwards and
        classifies by the venue's own state, so `cancelled` means terminal on
        re-read; `still_open`, `unverified` and `no_venue_id` mark the store
        UNCERTAIN and are retried on the next sweep rather than reported as
        success.
        """
        t = self.transport
        if t is None or t.armed:
            return None                 # the armed paths own this
        if self.store.live_order_count() <= 0:
            return None
        # NEVER on a book the venue has not confirmed: cancelling by an id we
        # are not sure is current is how a fill gets lost.
        if not self.reconciled:
            return None
        if (now - getattr(self, "last_orphan_sweep_t", 0.0)
                < self.ORPHAN_SWEEP_EVERY_S):
            return None
        self.last_orphan_sweep_t = now
        # a final progress read first, so an order that filled since the last
        # poll is booked as a fill rather than disappearing into a cancel.
        progress = self.poll_orders()
        if self.store.live_order_count() <= 0:
            return None
        rec = LX.deadman(self.store, t, reason="orphan_unarmed")
        emit("L5_LIVE_ORPHAN_CANCEL", dict(
            rec, final_progress=len(progress or []),
            labels=("an order this ledger owns was resting while this process "
                    "was NOT armed, which is the state a crash leaves behind. "
                    "Cancellation needs no authorization -- both locks gate "
                    "submission -- so it was withdrawn by VENUE id and proved "
                    "by re-reading. Only orders in this ledger are touched; a "
                    "manual order is never one of them. `cancelled` means the "
                    "venue reported terminal; anything else marks the store "
                    "UNCERTAIN and is retried on the next sweep.")))
        return rec

    def maybe_halt_deadman(self, now: float) -> Optional[Dict[str, Any]]:
        """A LATCHED HALT MUST ALSO CANCEL, not merely stop submitting.

        `authorize_arming`'s HALT_GATE_ABSENT stops this process ARMING
        without an independent halt, and the gate in
        `FaithfulExecutor.handle` stops the NEXT order once one is latched.
        Neither does anything about an order already resting at the venue --
        and a halt that leaves live exposure unattended is most of the way to
        being no halt at all, because the watchdog latches precisely when it
        believes nobody is managing that order.

        The watchdog cannot cancel it: its image holds no credentials and no
        signing module by design (57d02628). So the cancellation belongs
        here, under this process's own authorization, and this is the line
        that connects the two. `deadman` cancels by VENUE id and proves it by
        re-reading, so an unconfirmed cancel marks the store UNCERTAIN rather
        than reporting success.

        FAIL CLOSED, DELIBERATELY: an unreachable or stale latch cancels and
        disarms, exactly as it refuses a submission. A transient network
        failure therefore ends the bounded session rather than risking an
        unmanaged resting order.
        """
        hg = getattr(self.executor, "halt_gate", None)
        if hg is None or self.transport is None or not self.transport.armed:
            return None
        # Nothing resting means nothing to cancel. Asking anyway would put an
        # HTTP read in front of every tick of an idle session for no
        # decision -- and the submission path is already gated.
        if self.store.live_order_count() <= 0:
            return None
        if (now - getattr(self, "last_halt_sweep_t", 0.0)
                < self.HALT_SWEEP_EVERY_S):
            return None
        self.last_halt_sweep_t = now
        try:
            # ONE read, not two: `submissions_permitted()` and
            # `refuse_reason()` each perform their own GET, so asking both
            # doubles the request rate against the latch endpoint for a
            # single decision.
            if hasattr(hg, "state"):
                snap = hg.state() or {}
                permitted = bool(snap.get("permitted"))
                why = None if permitted else str(snap.get("why"))
            else:
                permitted = bool(hg.submissions_permitted())
                why = hg.refuse_reason()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            permitted = False
            why = "HALT_GATE_UNREADABLE:%s" % type(exc).__name__
        if permitted:
            return None
        reason = "independent_halt:%s" % why
        rec = LX.deadman(self.store, self.transport, reason=reason)
        emit("L5_LIVE_HALT", dict(
            rec, source="HALT_GATE", halt_reason=why,
            labels=("the independent latch is asserted, stale or "
                    "unreachable, so resting orders were cancelled by "
                    "VENUE id and the transport disarmed. `cancelled` "
                    "means the venue reported terminal on re-read; "
                    "`still_open` and `unverified` mark the store "
                    "UNCERTAIN instead of claiming success.")))
        self.transport.disarm(reason)
        return rec

    def qm_store(self) -> Any:
        """The measurement's durable ack/deadline record, beside the ledger.

        Created lazily and cached, so a process that never runs a measurement
        never touches the file.
        """
        st = getattr(self, "_qm_store", None)
        if st is None:
            from src import l5_queue_measurement as QM
            st = QM.DeadlineStore(
                os.path.join(self.root, "queue_measurement.json"))
            self._qm_store = st
        return st

    #: crossing to complete is a SEPARATE live authorization from resting a
    #: maker quote. Unset means the decision is computed, logged and NOT
    #: acted on -- which is what shadow observation is for.
    TS_COMPLETION_CROSS_ENV = "L5_TS_COMPLETION_CROSS"

    def ts_completion_cross_authorized(self) -> bool:
        return str(os.getenv(self.TS_COMPLETION_CROSS_ENV, "")
                   ).strip().upper() == "YES"

    def ts_completion_inputs(self, slug: str) -> Dict[str, Any]:
        """Gather the completion economics inputs, or say what is missing.

        FAIL CLOSED ON EVERY FIELD. A missing blended cost, an unattested
        leg, an unreadable book, an unknown tick and an unknown cash
        headroom are each a refusal; none of them is substituted with zero,
        because a worst-case comparison fed a zero cost basis reports an
        improvement that does not exist.
        """
        from src import l5_two_sided_probe as TSP
        out: Dict[str, Any] = {"market": slug}
        try:
            g = self.store.gross_inventory(slug)
            up_q, dn_q = D(str(g.get("up") or 0)), D(str(g.get("dn") or 0))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return dict(out, ok=False, reason="INVENTORY_UNREADABLE",
                        error=type(exc).__name__)
        # THE BLENDED COST PER SHARE, and only from an ATTESTED leg. A leg
        # whose quantity was adopted from the venue's net has no basis we
        # can name, and `UNKNOWN_COST_BASIS` is the honest answer rather
        # than pricing it at zero or at a dollar.
        px: Dict[str, Any] = {}
        for side, qty in (("UP", up_q), ("DOWN", dn_q)):
            if qty <= 0:
                px[side] = D(0)
                continue
            if not self.store.cost_attested(slug, side):
                return dict(out, ok=False, reason="UNKNOWN_COST_BASIS",
                            side=side, qty=str(qty),
                            labels=("this leg's quantity came from the "
                                    "venue's net, not from a fill of ours, "
                                    "so its cost basis is unknown. It is "
                                    "NOT priced at zero."))
            try:
                total = self.store.leg_cost(slug)[side]
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return dict(out, ok=False, reason="LEG_COST_UNREADABLE",
                            side=side, error=type(exc).__name__)
            px[side] = D(str(total)) / qty
        snap = self.book.snapshot(slug) or {}
        bid, ask = snap.get("bid"), snap.get("ask")
        # OWN-OUTCOME CROSSING PRICE. Buying UP crosses the UP ask; buying
        # DOWN crosses the DOWN ask, which is 1 - the UP BID. Passing the UP
        # ask for a DOWN completion is the arithmetic error this names.
        side = "DOWN" if up_q > dn_q else "UP"
        if side == "UP":
            cross_px = None if ask is None else D(str(ask))
            depth = snap.get("ask_shares")
        else:
            cross_px = None if bid is None else (D(1) - D(str(bid)))
            depth = snap.get("bid_shares")
        # CASH AND CEILING, from the gates already in force. The smaller of
        # the run-scoped cash bound's remaining headroom and the
        # account-wide exposure capacity binds, recomputed NOW rather than
        # carried from an earlier reading.
        vg = getattr(self.executor, "validation_gates", None)
        cash_h = None
        try:
            if vg is not None and vg.max_cash_deployed_usd is not None:
                spent = sum((D(str(v)) for v in
                             (self.store.committed() or {}).values()), D(0))
                cash_h = D(str(vg.max_cash_deployed_usd)) - spent
            exp = self.store.account_wide_risk_by_market()
            if not exp.get("ok"):
                return dict(out, ok=False, reason="EXPOSURE_UNREADABLE",
                            detail=str(exp.get("reason"))[:120])
            # CAPACITY IS DERIVED HERE, from the ceiling and the aggregate,
            # because account_wide_risk_by_market returns `total_usd` and
            # does NOT carry a capacity figure. Reading a `capacity_usd` key
            # off it got None, D("None") raised InvalidOperation, and the
            # whole completion path refused CASH_HEADROOM_UNREADABLE in
            # production on the 1130z window -- a fail-closed refusal, but a
            # refusal for a reason that had nothing to do with the economics.
            # Same arithmetic as account_exposure_lines, one source.
            total = _dec(exp.get("total_usd"))
            if total is None:
                return dict(out, ok=False, reason="EXPOSURE_TOTAL_UNREADABLE",
                            total_usd=str(exp.get("total_usd"))[:40])
            ceil_total = getattr(self.limits, "max_total_exposure_usd", None)
            if ceil_total is None:
                return dict(out, ok=False,
                            reason="ACCOUNT_EXPOSURE_CEILING_UNKNOWN")
            ceil_dec = _dec(ceil_total)
            if ceil_dec is None:
                return dict(out, ok=False,
                            reason="ACCOUNT_EXPOSURE_CEILING_UNREADABLE")
            capacity = ceil_dec - total
            cash_h = capacity if cash_h is None else min(cash_h, capacity)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return dict(out, ok=False, reason="CASH_HEADROOM_UNREADABLE",
                        error=type(exc).__name__)
        ceiling = None
        if vg is not None:
            ceiling = vg.max_worst_case_deficit_usd
        return dict(out, ok=True, side=side,
                    up_qty=up_q, dn_qty=dn_q,
                    up_cost_px=px["UP"], dn_cost_px=px["DOWN"],
                    taker_price=cross_px, available_depth=depth,
                    tick=getattr(self.transport, "tick_size", None),
                    min_qty=getattr(self.transport, "quantity_increment",
                                    None),
                    cash_headroom=cash_h, worst_case_ceiling=ceiling,
                    book_age_s=snap.get("stream_age_s"),
                    gap_suspected=bool(snap.get("gap_suspected")))

    def maybe_ts_completion(self, now: float) -> Optional[Dict[str, Any]]:
        """Decide, on ECONOMICS, whether to cross to complete the pair.

        PATIENT. Nothing happens in the MAKER phase: the resting quote is
        still the better price and crossing early throws away the edge it
        was placed for. In the COMPLETION phase -- the last two minutes
        before the terminal deadline -- the actual executable price, the
        displayed depth, the real taker fee and the resulting worst-case
        settlement are computed and compared against holding.

        THE DEADLINE IS NEVER THE REASON. `completion_decision` returns ok
        True only when crossing STRICTLY improves the worst of the two
        settlement outcomes and fits the cash headroom and worst-case
        ceiling already in force. On any refusal this does nothing, and
        `maybe_ts_deadline` cancels the unfilled order at the cutoff as it
        always has -- the residual is then carried under the existing
        controls.

        CROSSING IS ITS OWN AUTHORIZATION. Even when the economics say yes,
        no order is placed unless L5_TS_COMPLETION_CROSS is YES and the
        transport is armed. Unset, this is a logged proposal and nothing
        more, which is what the shadow deployment runs.
        """
        from src import l5_two_sided_probe as TSP
        t = self.transport
        if t is None:
            return None
        slug = getattr(t, "market_slug", None)
        if not slug or not TSP.active(slug):
            return None
        ws = AB.slug_start(slug)
        if ws is None:
            return None            # the deadline path owns an unknown window
        phase = TSP.completion_phase(now, float(ws))
        # PATIENCE PROTECTS A RESTING QUOTE. WITH NOTHING RESTING IT PROTECTS
        # NOTHING, AND THAT COST US $0.5204 ON 1945z.
        #
        # The MAKER-phase bail exists because "the resting quote is still the
        # better price and crossing early throws away the edge it was placed
        # for". True -- while a quote is actually resting. On
        # cpc-btc-updown-15m-2026-10-25-1945z it was not: the maker complement
        # route had refused twelve times on COMPLEMENT_PAIR_ABOVE_CAP, so
        # nothing was ever posted, and all 121 L5_RESIDUAL_BOOK_SAMPLE rows of
        # that window carry `order_resting: false`. The patience rationale was
        # guarding an edge that did not exist.
        #
        # What it cost, measured on that window's own books: the executable
        # exit was worth -$0.0144 at 204s (pair 0.99) and the engine's first
        # legal opportunity was at 600s, by which time the pair had decayed to
        # 1.33 and the best exit was -$0.5348.
        #
        # SO THE PHASE GATE IS RELAXED ONLY WHERE ITS OWN REASON DOES NOT
        # APPLY, and the relaxation is evaluate-only: `completion_decision`
        # still has to find a STRICT improvement in the worse settlement
        # outcome, and L5_TS_COMPLETION_CROSS plus an armed transport still
        # gate every submission. With crossing unauthorized this changes no
        # order, only what the log can see.
        #
        # FAIL CLOSED: `still_fillable_order_count` charges an unreadable,
        # parked or quarantined row as live, so "nothing is resting" has to be
        # positively established. If we cannot tell, we stay patient.
        resting = None
        try:
            resting = int(self.store.still_fillable_order_count())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["ts_completion_resting_unreadable:%s"
                       % type(exc).__name__] += 1
        no_quote_resting = resting == 0
        if phase == "LATE":
            return None
        if phase != "COMPLETION" and not no_quote_resting:
            return None
        if now - self.last_ts_completion_t < self.TS_COMPLETION_EVERY_S:
            return None
        self.last_ts_completion_t = now
        src = self.ts_completion_inputs(slug)
        if not src.get("ok"):
            # RETURN WHAT WAS EMITTED. Emitting one dict and returning
            # another let a caller miss `crossed` on exactly the paths where
            # it matters most -- the fail-closed ones.
            rec = dict(src, phase=phase, cross=False, crossed=False,
                       still_fillable_orders=resting,
                       evaluated_early_no_quote_resting=(
                           phase != "COMPLETION" and no_quote_resting),
                       authorized_to_cross=(
                           self.ts_completion_cross_authorized()))
            emit("L5_TS_COMPLETION", rec)
            self.stats["ts_completion:%s" % (src.get("reason")
                                             or "INPUTS")] += 1
            return rec
        dec = TSP.completion_decision(
            up_qty=src["up_qty"], dn_qty=src["dn_qty"],
            up_cost_px=src["up_cost_px"], dn_cost_px=src["dn_cost_px"],
            taker_price=src["taker_price"],
            available_depth=src["available_depth"],
            tick=src["tick"], min_qty=src["min_qty"],
            cash_headroom=src["cash_headroom"],
            worst_case_ceiling=src["worst_case_ceiling"],
            book_age_s=src["book_age_s"],
            gap_suspected=src["gap_suspected"])
        authorized = self.ts_completion_cross_authorized()
        rec = dict(dec, market=slug, phase=phase,
                   still_fillable_orders=resting,
                   evaluated_early_no_quote_resting=(
                       phase != "COMPLETION" and no_quote_resting),
                   authorized_to_cross=authorized,
                   armed=bool(t.armed),
                   up_qty=str(src["up_qty"]), dn_qty=str(src["dn_qty"]),
                   up_cost_px=str(src["up_cost_px"]),
                   dn_cost_px=str(src["dn_cost_px"]),
                   cash_headroom=str(src["cash_headroom"]),
                   worst_case_ceiling=str(src["worst_case_ceiling"]),
                   crossed=False,
                   labels=("PROPOSED. A crossing completion is a separate "
                           "live authorization from resting a maker quote, "
                           "so an economically positive decision is logged "
                           "and NOT acted on unless "
                           "L5_TS_COMPLETION_CROSS is YES and the "
                           "transport is armed. Nothing here is a fill and "
                           "nothing here is realized."))
        if dec.get("ok") and authorized and t.armed:
            rec["crossed"] = "WOULD_SUBMIT"
            rec["submission_route"] = ("deliberately not wired: crossing "
                                       "requires the owner's separate "
                                       "explicit authorization for live "
                                       "taker execution")
        emit("L5_TS_COMPLETION", rec)
        self.stats["ts_completion:%s" % (dec.get("reason") or "OK")] += 1
        return rec

    def maybe_ts_deadline(self, now: float) -> Optional[Dict[str, Any]]:
        """Cancel the two-sided probe's resting legs at the TERMINAL deadline.

        WHY THIS EXISTS. The probe module defined cancel_cutoff(),
        place_cutoff() and may_place() and NOTHING CALLED THEM -- an audit
        found three constants and no enforcement, which is worse than no
        deadline at all because it reads like one. This is the call site.

        It is inert unless the probe's experiment AND its market are both
        named, it fires on the WINDOW (not on any order's age), so a startup
        delay, a restart or a stalled transport cannot postpone it, and it
        cancels by VENUE id through the existing deadman with proof by
        re-read. A one-sided fill is exactly the case it must handle: the
        unfilled leg is withdrawn and the filled one is left to settle,
        which is the bounded outcome the risk envelope already prices.
        """
        from src import l5_two_sided_probe as TSP
        from src import l5_defensive_completion as DEFC
        t = self.transport
        if t is None:
            return None
        slug = getattr(t, "market_slug", None)
        if not slug or not TSP.active(slug):
            return None
        ws = AB.slug_start(slug)
        if ws is None:
            # UNKNOWN WINDOW TIMING IS ALREADY OVERDUE. A deadline we cannot
            # compute is not a deadline, so the legs come in.
            out = {"reason": "TS_WINDOW_UNKNOWN", "cancelled": True}
        elif now <= TSP.complement_retire_from_start(
                float(ws), DEFC.DEFENSIVE_MIN_EXECUTION_S):
            return None
        else:
            # FROM THE START, NOT THE END. AB.slug_start names the window
            # START; cancel_cutoff takes the END. Passing the start gave
            # start - 180, a deadline already expired before the window
            # opened, which cancelled the legs on the first tick. Found by
            # independent review; the helper now owns the arithmetic.
            # THE CUTOFF IS NOW THE TERMINAL LESS THE DEFENSIVE RUNWAY, so a
            # complement can never be retired at the instant the defensive
            # exit's own window has already closed. This is the 0300z fix; see
            # `TSP.complement_retire_from_start`.
            out = {"reason": "TS_TERMINAL_DEADLINE",
                   "window_start": float(ws),
                   "window_end": TSP.window_end(float(ws)),
                   "terminal": TSP.cancel_cutoff_from_start(float(ws)),
                   "defensive_runway_s": DEFC.DEFENSIVE_MIN_EXECUTION_S,
                   "cutoff": TSP.complement_retire_from_start(
                       float(ws), DEFC.DEFENSIVE_MIN_EXECUTION_S)}
        if not (self.store.live_orders() or {}):
            return None
        res = LX.deadman(self.store, t, reason="ts_terminal_deadline")
        emit("L5_TS_DEADLINE", dict(out, slug=slug, now=now, result=res))
        return res

    def maybe_completion_requote(self, now: float
                                 ) -> Optional[Dict[str, Any]]:
        """Cancel a completion that has drifted off the touch, so the
        existing complement path can re-place it against the current book.

        WHAT THIS DOES NOT DO. It does not submit anything. The replacement
        is placed by the ordinary complement path on a later tick, as the
        next ATTEMPT of the same leg of the same round, which `acc_leg_attempt`
        charges to the REPLACE budget and which it refuses outright while any
        order is live. That is what makes two competing completion orders
        structurally impossible rather than merely avoided: the only way to a
        replacement is through a cancellation this method has already proved.

        WHY A CANCEL IS THE WHOLE CHANGE. `complement_order` already prices
        against the CURRENT book from the store's realised leg costs, so a
        complement re-placed after a cancel is automatically repriced and
        resized on the inventory that remains. The defect was never the
        pricing of a new complement; it was that a resting one was left
        frozen while the market walked 12 to 18 ticks away from it.

        DEFAULT OFF. Without L5_COMPLETION_REQUOTE this is inert, so the
        deployment carries the code and changes no behaviour until an owner
        turns it on.
        """
        from src import l5_two_sided_probe as TSP
        if not completion_requote_enabled():
            return None
        try:
            t = self.transport
            if t is None or not getattr(t, "armed", False):
                return None
            slug = getattr(t, "market_slug", None)
            if not slug or not TSP.active(slug):
                return None
            # ONE IDENTIFIABLE RESTING ORDER IN THIS MARKET. Zero is nothing
            # to requote; more than one is a state this engine does not
            # create, and guessing which to cancel is how a requote becomes
            # an unhedged leg.
            live = {c: o for c, o in (self.store.live_orders() or {}).items()
                    if str((o or {}).get("market") or "") == str(slug)}
            if len(live) != 1:
                return None
            coid, row = next(iter(live.items()))
            if not str(row.get("venue_order_id") or "").strip():
                # cannot cancel what the venue cannot name, so do not start
                self.stats["requote_refused:NO_VENUE_ID"] += 1
                return None
            side = str(row.get("side") or "")
            # THE RESTING ORDER MUST BE THE REDUCING ONE, JUDGED ON THE
            # REALISED RESIDUAL.
            #
            # NOT `probe_reducing_side`, and the end-to-end test is what
            # found that. That helper reads the PROSPECTIVE residual --
            # realised inventory PLUS every outstanding order as if it
            # filled -- which is exactly the right basis for choosing which
            # leg to offer next, and exactly the wrong one here: a
            # completion that fully covers its residual drives the
            # prospective figure to ZERO, so the helper reported no reducing
            # side and the requote could never fire in the normal case.
            #
            # Here the question is about the order we are about to CANCEL, so
            # the residual to read is the one that would remain without it:
            # the realised position.
            try:
                realised = D(str(self.store.residual(str(slug))))
            except BaseException:                              # noqa: BLE001
                self.stats["requote_refused:RESIDUAL_UNREADABLE"] += 1
                return None
            if realised == 0:
                self.stats["requote_refused:NOTHING_TO_COMPLETE"] += 1
                return None
            reducing = "DOWN" if realised > 0 else "UP"
            if side != reducing:
                # a first leg that has not filled is not a completion, and is
                # owned by the TTL and terminal deadlines rather than by this
                self.stats["requote_refused:NOT_THE_REDUCING_LEG"] += 1
                return None
            held = "DOWN" if side == "UP" else "UP"
            legs = self.store.leg_cost(str(slug))
            ws = AB.slug_start(str(slug))
            if ws is None:
                self.stats["requote_refused:WINDOW_UNKNOWN"] += 1
                return None
            snap = self.book.snapshot(str(slug)) or {}
            working = LX.InventoryStore.worst_case_remaining(row)
            qty = row.get("qty")
            filled = ((D(str(qty)) - working)
                      if qty is not None else D(0))
            vg = getattr(self.executor, "validation_gates", None)
            res = TSP.completion_requote(
                side=side, resting_own_px=row.get("price"),
                resting_qty=qty, resting_filled_qty=filled,
                bid=snap.get("bid"), ask=snap.get("ask"),
                tick=getattr(t, "tick_size", None),
                min_qty=getattr(t, "quantity_increment", None),
                held_qty=self.store.position(str(slug), held),
                held_cost_usd=legs.get(held),
                deficient_qty=self.store.position(str(slug), side),
                deficient_cost_usd=legs.get(side),
                residual_budget_usd=getattr(
                    vg, "max_worst_case_deficit_usd", None),
                now_s=now, window_start_s=float(ws),
                window_end_s=TSP.window_end(float(ws)),
                book_age_s=snap.get("stream_age_s"),
                gap_suspected=bool(snap.get("gap_suspected")),
                orders_uncertain=self.store.orders_uncertain())
            if not res.get("act"):
                self.stats["requote_refused:%s" % res.get("reason")] += 1
                return None
            # CANCEL EXACTLY THIS ORDER, by venue id, proof by re-read, and
            # any fill found while cancelling is BOOKED with its cost basis
            # rather than discarded. An ambiguous outcome marks the store
            # UNCERTAIN, which refuses every subsequent submission until an
            # authoritative read agrees -- so a failed requote cannot leave a
            # replacement racing a live order.
            out = LX.deadman(self.store, t, reason="completion_requote",
                             only=[coid])
            self.stats["requote_cancelled"] += 1
            rec = dict(res, slug=str(slug), client_order_id=coid,
                       cancel=out, now=now,
                       labels=("the CANCEL half only. The replacement is "
                               "placed by the ordinary complement path as "
                               "the next attempt of this leg, charged to "
                               "REPLACE, and cannot be sent while this "
                               "order is live. No fill is implied by "
                               "either half."))
            emit("L5_TS_REQUOTE", rec)
            return rec
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["requote_error:%s" % type(exc).__name__] += 1
            return None

    def maybe_acc_deadline(self, now: float) -> Optional[Dict[str, Any]]:
        """Cancel each accumulation order at min(submit + TTL, terminal).

        WHY THIS EXISTS. `maybe_qm_deadline` is the only per-order age
        deadline in this runner and it is scoped to the queue measurement, so
        an accumulation leg's rest was bounded only by the window and by
        whoever chose the moment the lock came off. That is what forced
        `rest = terminal - placement` and collapsed a full-window strategy
        into a two-minute band. With this, an order's life stops being the
        strategy's life: the engine may quote from the window's open and
        still never hold anything near the watchdog's 300s.

        INERT BY DEFAULT. Returns immediately unless the two-sided engine is
        active for this market, so a shadow-locked service runs none of it.

        SCOPED CANCELS, THROUGH THE SAME ROUTE. Each overdue order is
        withdrawn by `deadman(..., only=[coid])`, so it inherits
        cancel-by-venue-id, proof by re-read, fill-booking and the UNCERTAIN
        marking on `still_open`/`unverified` -- and a complement placed
        seconds ago is NOT swept with it.

        FAIL CLOSED. An order whose submit time cannot be parsed cannot be
        timed, and an order that cannot be timed may be resting at the venue
        past its deadline, so it is marked UNCERTAIN and HALTED rather than
        left. Restart recovery is automatic: `live_orders` is durable and
        carries each order's own submit time, so ages survive a restart.
        """
        from src import l5_two_sided_probe as TSP
        t = self.transport
        if t is None:
            return None
        slug = getattr(t, "market_slug", None)
        if not slug or not TSP.active(slug):
            return None
        ws = AB.slug_start(slug)
        if ws is None:
            return None
        live = self.store.live_orders()
        if not live:
            return None
        overdue, unusable = [], []
        for coid, row in sorted(live.items()):
            if str(row.get("market") or "") != str(slug):
                continue
            raw = row.get("t")
            try:
                st = dt.datetime.fromisoformat(str(raw))
                if st.tzinfo is None:
                    raise ValueError("naive submit time")
                sub = st.timestamp()
            except BaseException:                              # noqa: BLE001
                unusable.append(coid)
                continue
            if TSP.order_due(now, sub, float(ws), float(ws) + TSP.WINDOW_S):
                overdue.append(coid)
        if unusable:
            why = ("accumulation order submit time unparseable: %s"
                   % ",".join(unusable[:4]))
            self.store.mark_uncertain(why)
            emit("L5_ACC_DEADLINE_HALT", {
                "coids": unusable, "market": slug, "reason": why,
                "labels": ("an order that cannot be timed may be resting past "
                           "its deadline. Reconcile against the venue; never "
                           "assume it was cancelled.")})
            if t.armed:
                t.disarm("acc_deadline_record_unusable")
            return {"halt": True, "reason": "ACC_SUBMIT_TIME_UNPARSEABLE",
                    "coids": unusable}
        # ============ AND THE SAME DEADLINE, TRIGGERED BY DISTANCE
        #
        # A leg the book has LEFT cannot fill however long it rests, and
        # until now only this clock could rescue it: `retire_blocked_by`
        # refuses to retire a round while an order is live, so 2200z sat 56
        # seconds unfillable out of a 540-second entry budget. Drift is now a
        # second way to become overdue, and it routes through exactly the
        # same cancel: `poll_orders()` first so a racing fill is BOOKED
        # rather than cancelled, then `deadman(only=...)` by exact venue id
        # with proof by re-read.
        #
        # NOTHING HERE PRICES THE REPLACEMENT. The recovered leg is re-quoted
        # by the ordinary plan path, two ticks behind the CURRENT touch, with
        # the pair cap re-tested. This only decides that the stale order
        # should stop resting.
        stale = acc_stale_by_distance(self, now, slug, live,
                                      skip=set(overdue))
        if stale:
            overdue = overdue + [c for c in stale if c not in overdue]
        if not overdue:
            return None
        progress = self.poll_orders()      # a fill is booked, not cancelled
        # RE-CHECK AFTER THE POLL, for the distance-triggered ones only. The
        # authoritative read may have just booked a fill, and a leg that
        # filled must NEVER be abandoned -- it becomes inventory for the
        # complement path. A leg whose row has gone is terminal already.
        raced = []
        if stale:
            fresh = self.store.live_orders()
            for coid in list(stale):
                if not TSP.row_is_zero_fill(fresh.get(coid)):
                    raced.append(coid)
                    stale.remove(coid)
                    if coid in overdue:
                        overdue.remove(coid)
        if raced:
            self.win["acc_recover_raced_a_fill"] += len(raced)
            emit("L5_ACC_RECOVER_RACED", {
                "market": slug, "coids": raced,
                "labels": ("the authoritative poll booked a fill on a leg "
                           "this was about to recover, so it was NOT "
                           "cancelled. It is inventory now and the "
                           "complement path owns it.")})
        if not overdue:
            return None
        if stale:
            self.win["acc_recovered_stale_first_leg"] += len(stale)
            acc = getattr(self, "acc_recoveries", None)
            if acc is not None:
                acc[str(slug)] = acc.get(str(slug), 0) + len(stale)
        rec = LX.deadman(self.store, t, reason="acc_order_deadline",
                         only=overdue)
        out = dict(rec, market=slug, overdue=overdue,
                   stale_by_distance=stale,
                   raced_a_fill=raced,
                   recoveries_used=(getattr(self, "acc_recoveries", None)
                                    or {}).get(str(slug), 0),
                   ttl_s=TSP.order_ttl_s(),
                   polled=len(progress or []),
                   labels=("per-order age deadline. `cancelled` means the "
                           "VENUE reported terminal on re-read; anything "
                           "else marks the store UNCERTAIN. A leg cancelled "
                           "with ZERO fill may be re-placed within its "
                           "attempt budget; a leg that filled may not."))
        emit("L5_ACC_DEADLINE", out)
        return out

    def maybe_qm_deadline(self, now: float) -> Optional[Dict[str, Any]]:
        """Cancel the measurement order at min(T_ack + 120s, T_end - 180s).

        WHY THIS EXISTS. Every other cancellation route in this runner fires
        on a WINDOW or a STATE: window_end, authorization expiry, a latched
        halt, an unarmed orphan sweep. None of them is a per-order age
        deadline, so the measurement's defining control -- 120 seconds of
        bounded exposure from the venue's own acknowledgement -- was not
        enforced by the engine at all. This is that deadline.

        INERT BY DEFAULT. It returns immediately unless an experiment and its
        market are both named, so the shadow-locked service running today
        does not execute a line of it.

        RECOVERY FIRST. After a restart the durable record is consulted
        before anything else: an unreadable record, a missing venue id or a
        missing ack time all HALT rather than read as "no order", because an
        order may be live at the venue and we can neither name nor time it.

        PROOF, NOT A REQUEST. The cancel goes through `deadman`, which
        cancels by VENUE id and re-reads; `still_open`/`unverified` mark the
        store UNCERTAIN and are retried. The record is only retired by a
        terminal state with ZERO remaining -- an accepted cancel request is
        never treated as a cancellation.
        """
        from src import l5_queue_measurement as QM
        t = self.transport
        if t is None or not QM.active(getattr(t, "market_slug", None)):
            return None
        store = self.qm_store()
        rec = store.order()
        bad = QM.recovery_refusal(store)
        if bad and bad.get("halt"):
            self.store.mark_uncertain(bad["reason"]) if hasattr(
                self.store, "mark_uncertain") else None
            emit("L5_QM_HALT", dict(bad, labels=(
                "the measurement's durable ack record cannot be trusted, so "
                "an order may be resting at the venue that we can neither "
                "name nor time. Reconcile against the venue; never assume "
                "it was cancelled.")))
            if t.armed:
                t.disarm(bad["reason"])
            return bad
        if not QM.due(now, rec):
            return None
        progress = self.poll_orders()          # a fill is booked, not cancelled
        out = LX.deadman(self.store, t, reason="qm_deadline")
        confirmed = False
        for row in (progress or []):
            if str(row.get("venue_order_id") or "") == str(
                    rec.get("venue_order_id") or ""):
                confirmed = store.confirm_cancel(
                    terminal=bool(row.get("terminal")),
                    remaining=row.get("remaining"))
        emit("L5_QM_DEADLINE_CANCEL", dict(
            out, venue_order_id=rec.get("venue_order_id"),
            t_cancel=rec.get("t_cancel"), confirmed=confirmed,
            labels=("cancelled at the per-order deadline by VENUE id. "
                    "`confirmed` is true only for a terminal venue state "
                    "with ZERO remaining; anything else leaves the cancel "
                    "owed and the book UNCERTAIN.")))
        return out

    def tick(self, now: float) -> None:
        self.now_t = now
        _sc = _StageClock()
        self.roll(now)
        _sc.mark("roll")
        if self.halted() and self.transport is not None and self.transport.armed:
            emit("L5_LIVE_HALT", LX.deadman(self.store, self.transport,
                                            reason="operator_halt"))
            self.transport.disarm("operator_halt")
        # the INDEPENDENT latch, which the env var above is not: it is
        # written by a process we do not control and survives our restart.
        self.maybe_halt_deadman(now)
        _sc.mark("maybe_halt_deadman")
        # and the claim's own venue-bounded expiry, same cancellation route
        self.maybe_expire_authorization(now)
        _sc.mark("maybe_expire_authorization")
        # and, when NOT armed, withdraw anything this ledger still owns: a
        # crash leaves exactly that state and no other path covers it.
        self.maybe_cancel_orphans(now)
        _sc.mark("maybe_cancel_orphans")
        # the queue measurement's own per-order deadline. Inert unless an
        # experiment AND its market are both named -- see maybe_qm_deadline.
        self.maybe_qm_deadline(now)
        _sc.mark("maybe_qm_deadline")
        # THE ACCUMULATION ENGINE'S PER-ORDER AGE DEADLINE. Before the
        # completion decision and before the window sweep, because an
        # overdue leg must be withdrawn before anything new is priced
        # against the inventory it would have left.
        self.maybe_acc_deadline(now)
        _sc.mark("maybe_acc_deadline")
        # REPORTING ONLY: the opposite side while we hold unmatched
        # inventory, sampled whether or not an order is resting.
        self.sample_residual_book(
            getattr(self.transport, "market_slug", None)
            if self.transport is not None else None)
        # BEFORE the deadline sweep, deliberately. The completion
        # decision needs the order still resting to be worth making;
        # after the cutoff there is nothing left to complete against.
        _sc.mark("sample_residual_book")
        self.maybe_ts_completion(now)
        _sc.mark("maybe_ts_completion")
        # AFTER the completion decision and BEFORE the terminal sweep. After,
        # because a completion that can act on the book as it stands should
        # do so rather than be cancelled first; before, because a quote the
        # market has walked away from is worth replacing while runway
        # remains. Inert unless L5_COMPLETION_REQUOTE is set.
        self.maybe_completion_requote(now)
        _sc.mark("maybe_completion_requote")
        self.maybe_ts_deadline(now)
        _sc.mark("maybe_ts_deadline")
        # MODELLED fills advance BEFORE the cool-off check, for the same
        # reason the cool-off branch still calls poll(): this is local
        # bookkeeping over the websocket feed and costs the venue nothing.
        # Below the check it would stall during a cool-off, and the
        # recorder's trade list is a rolling 500 -- a long enough stall
        # drops prints the model never sees.
        self.model_advance(now)
        _sc.mark("model_advance")
        self.two_sided_advance(now)
        _sc.mark("two_sided_advance")
        self.two_sided_settle(now)
        _sc.mark("two_sided_settle")
        # one question per tick instead of one per caller
        cooling = self.venue_cooling()
        _sc.mark("venue_cooling")
        if cooling > 0:
            self.stats["ticks_skipped_in_cooloff"] += 1
            self.win["ticks_skipped_in_cooloff"] += 1
            self.poll(now)          # the websocket feed costs the venue nothing
            # SAME REASON, and it must not stall in a cool-off: the shadow
            # reads the running websocket book and writes one local file.
            self.shadow_tick(now)
            return
        if _sc.total_s() >= TICK_STAGE_REPORT_S:
            try:
                emit("L5_LIVE_TICK_STAGES", dict(
                    _sc.report(),
                    market=getattr(self.transport, "market_slug", None),
                    t_in_window_s=(round(now - self.ws, 2)
                                   if self.ws else None)))
            except BaseException as _exc:              # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["tick_stage_emit_error"] += 1
        self.maybe_preflight(now)
        if self.store.live_order_count():
            self.poll_orders()
        # ORDER MATTERS, and it is the opposite of what it looks like.
        #
        # Settle FIRST, reconcile second. Once a market expires the venue
        # reports no position in it at all -- 1600z settled at 16:15Z and
        # /v1/portfolio/positions came back {} -- so the venue cannot tell us
        # what we held. Our own book is the ONLY surviving record of that
        # market's economics. Reconciling first and settling second would
        # compute the settlement against an emptied book and post a P&L of
        # zero, silently erasing a real result and, with it, the realized
        # total the -$5 halt is measured on.
        #
        # Retirement is still gated on the VENUE: it happens only for a
        # market the venue reports terminal WITH a settlement price, and only
        # when no order is live in it. On a restart the first pass may refuse
        # on stale live orders; the reconcile below releases those, and the
        # next tick retires it properly. One tick late is fine. Erased
        # accounting is not.
        # BEFORE settling. A market whose last order vanished still owes
        # the ledger that order's fill, and retire_settled_market now
        # refuses while anything is parked -- so resolving first is what
        # lets an ordinary window settle on the same tick it expires.
        if self.store.pending_terminal():
            self.resolve_pending_terminal(now)
        self.sweep_settlements()
        # a book that has gone uncertain gets another authoritative read,
        # rather than latching the session shut until a redeploy
        self.reconcile_if_needed(now)
        self.poll(now)
        # LAST, and gating nothing. An independent decision source: it reads
        # our own simulated inventory, the live book and the real clock, and
        # it is structurally unable to submit -- see the block above.
        self.shadow_tick(now)
        self.shadow_settle(now)

    # ------------------------------------------------------------------
    # THE BOUNDED CANCELLATION-MECHANISM MODE
    # ------------------------------------------------------------------
    def mechanism_tick(self, now: float, mech: Any) -> None:
        """`tick` with the strategy removed and the two CANCELLERS removed.

        WHY A SEPARATE TICK AT ALL. The experiment is "does the INDEPENDENT
        watchdog withdraw a real resting order". Run under the ordinary tick,
        three of our own code paths would reach that order first and we would
        have measured ourselves:

          * `maybe_cancel_orphans` withdraws anything this ledger owns while
            NOT armed -- which is precisely the state the halt puts us in;
          * `maybe_ts_deadline` withdraws at the executor's own cancellation
            margin;
          * the halt branch calls `LX.deadman`, which cancels every resting
            order by venue id -- and the watchdog ASSERTS THE LATCH BEFORE IT
            CANCELS, so the executor would read that latch and race the very
            cancel it is supposed to be observing. Both processes would send a
            cancel for the same id within seconds and neither result would
            mean anything.

        SO THE ONE TEST ORDER IS EXEMPT FROM OUR OWN CANCELLERS, DELIBERATELY,
        AND THE EXEMPTION IS BOUNDED. `Mechanism.SELF_CANCEL_AFTER_S` is the
        backstop: if the watchdog has not cancelled by then this process
        withdraws the order itself and reports SELF_FALLBACK, which the report
        explicitly refuses to count as proof. Stranding a real order to keep a
        measurement clean would be the wrong trade.

        WHAT IS NOT REMOVED, because the owner requires it preserved:

          * AUTOMATIC HALT. The latch still disarms the transport, so no
            further order can be submitted. Only the CANCELLATION side of the
            halt response is withheld, and only for this one id.
          * RECONCILIATION. `poll_orders`, `resolve_pending_terminal`,
            `sweep_settlements` and `reconcile_if_needed` all still run, so a
            fill while the order rests is booked with its cost basis instead
            of becoming quantity without cost.

        AND `poll` IS NOT CALLED, which is the correction this docstring
        previously got wrong: `poll` -> `handle` is the strategy itself, not a
        feed refresh. See the comment at the call site for the live evidence.
          * the authorization's own venue-bounded expiry.
        """
        self.now_t = now
        self.roll(now)
        # HALT STILL DISARMS. The deadman's CANCEL is what is withheld here,
        # not the halt itself: a halted executor must stop submitting, and
        # this does stop it.
        if self.halted() and self.transport is not None and self.transport.armed:
            emit("L5_MECH_HALT_DISARM", {
                "reason": "operator_or_independent_halt",
                "note": ("disarmed, so nothing further can be submitted. The "
                         "resting mechanism order is NOT cancelled here: it "
                         "is the subject of the experiment and the watchdog "
                         "is the intended canceller. Mechanism's own "
                         "self_cancel_after_s remains the backstop."),
                "mechanism_state": getattr(mech, "state", None),
                "venue_order_id": getattr(mech, "venue_order_id", None)})
            self.transport.disarm("operator_halt")
        self.maybe_expire_authorization(now)
        cooling = self.venue_cooling()
        if cooling > 0:
            self.stats["ticks_skipped_in_cooloff"] += 1
            return
        self.maybe_preflight(now)
        # RECONCILIATION, unchanged. A fill while it rests must be booked.
        if self.store.live_order_count():
            self.poll_orders()
            self.resolve_pending_terminal(now)
        self.sweep_settlements()
        self.reconcile_if_needed(now)
        # NO self.poll(). THIS IS THE WHOLE POINT OF THE MODE AND I GOT IT
        # WRONG THE FIRST TIME.
        #
        # `poll` reads the L5 action feed and hands every fresh action to
        # `handle`, which IS the strategy: it builds an intent, runs the
        # frozen decision path and calls the executor's submit. It was copied
        # in from `tick` on the assumption that it merely refreshed a feed,
        # and the docstring above claimed the strategy was removed while this
        # line kept it.
        #
        # OBSERVED LIVE at 18:34:36Z with the shadow lock LIFTED: two
        # L5_LIVE_DECISION records, UP 0.06 @ 0.31 and DOWN 0.9 @ 0.6, both
        # ORDER:NOT_ARMED. Only the pending arming stopped them. Had arming
        # completed, the strategy would have submitted alongside the ONE
        # order this test is authorized for, raced it for the single-use
        # submission claim, and amounted to the recurring live trading the
        # owner explicitly prohibited.
        #
        # The mechanism needs NO action feed: its order is predetermined. The
        # authenticated book it prices against is a separate thread and is
        # unaffected.
        #
        # AND NO `shadow_tick` EITHER, deliberately rather than by omission.
        # This mode is a bounded one-shot experiment on one predetermined
        # order; the shadow controller is the CONTINUOUS operating mode and
        # runs in `tick`. A mechanism session is minutes long, so the gap in
        # the shadow record is minutes long and is visible as such in the
        # heartbeat's decision count. Nothing here is gated on it.
        mech.step(now)


#: THE STAGE A SETTLEMENT CORRECTION. ONE ENTRY, ONE MARKET, ONCE.
#:
#: Owner-approved 2026-10-15 for this market alone. This list is NOT a
#: migration framework: nothing may be added to it without the same explicit
#: approval, and the application is idempotent three ways so a restart
#: re-reads it, refuses, and changes nothing.
#:
#: The evidence. The executor's own L5_LIVE_SETTLED at 2026-10-14T22:45:31Z
#: retired 2230z against yes_value 0.0100 -- the price the EXPIRED BOOK still
#: carried, not a resolution. 2.90 UP shares "redeemed" 0.029000 that no
#: cashflow corresponds to. Both /v1/account/balances reads are flat (zero
#: positions, zero open orders, zero unsettled):
#:     22:30:07Z pre-arm   242.97505
#:     23:35:56Z post-settle 242.38505
#:     delta -0.59000, which equals the five fills' acquisition cost exactly
#:     and leaves nothing for a settlement payout. UP settled worthless: 0.
SETTLEMENT_CORRECTIONS = [
    {
        "id": "stage-a-2230z-book-price-as-settlement",
        "market": "cpc-btc-updown-15m-2026-10-14-2230z",
        # the row must STILL read exactly this, or the correction refuses
        "expect": {"yes_value": "0.0100", "settlement_pnl": "-0.561000",
                   "up_qty": "2.90", "dn_qty": "0",
                   "cost_total": "0.590000"},
        "corrected_yes_value": D("0"),
        "evidence": {
            "defect": ("the venue's expired book price 0.0100 was passed as "
                       "yes_value and the [0,1] range guard admitted it; "
                       "2.90 x 0.0100 = 0.029000 of redemption nobody paid"),
            "venue_state": "MARKET_STATE_EXPIRED",
            "settlement_record": ("UP settled worthless. The binary guard "
                                  "shipped in fd8ca2a4 now refuses 0.0100 "
                                  "as SETTLEMENT_NOT_BINARY"),
            "cash_anchor": {
                "pre_arm_utc": "2026-10-14T22:30:07Z",
                "pre_arm_balance": "242.97505",
                "post_settlement_utc": "2026-10-14T23:35:56Z",
                "post_settlement_balance": "242.38505",
                "delta": "-0.59000",
                "flat_at_each_read": ("zero positions, zero open orders, "
                                      "zero unsettled, armed false"),
                "source": "/v1/account/balances via transport.account()",
            },
            "fills": ("five maker fills, 2.90 UP, 0.590000 total, "
                      "commission 0.0000 on every row"),
            "rebates_confirmed": ("none. A zero commission is not a rebate "
                                  "and nothing was credited as one."),
            "excluded": ("the pre-existing 2026-10-12 drift of -0.100000 is "
                         "a separate line and is not touched"),
        },
    },
]


def apply_settlement_corrections(store) -> List[Dict[str, Any]]:
    """Apply the prepared settlement correction. Owner-approved call site.

    Called once from Runner.__init__, beside the other one-time repairs. It is
    SAFE to call on every boot and is NOT a recurring rebase: the correction
    applies at most once in the life of the ledger (by correction_id, by the
    expected-contents match, and by the superseded marker on the row), and a
    correction that would IMPROVE realized is refused outright, so this call
    site can never relax the daily loss halt.
    """
    out = []
    for spec in SETTLEMENT_CORRECTIONS:
        res = store.correct_settled_market(
            spec["market"], correction_id=spec["id"], expect=spec["expect"],
            corrected_yes_value=spec["corrected_yes_value"],
            evidence=spec["evidence"])
        emit("L5_LIVE_SETTLEMENT_CORRECTED" if res.get("corrected")
             else "L5_LIVE_SETTLEMENT_CORRECTION_REFUSED", res)
        out.append(res)
    return out


#: Known contaminated realized totals and what the authoritative evidence
#: says they should have been. Each applies AT MOST ONCE, keyed by id, and
#: only when the stored total still matches the contaminated figure.
#:
#: Neither is "make realized equal the balance". Each carries an attributed
#: line reconstructed from venue records and an explicit unattributed
#: remainder against a flat-account cash anchor. The remainder is named, not
#: absorbed into the trading line.
ACCOUNTING_REBASES = [
    {
        "id": "wire-priced-down-legs",
        "matches_realized_total": D("4.766200"),
        # 1600z, recomputed in own-outcome terms: Sum(1-p)q = Sum q - Sum pq,
        # so 10.46 - 4.4399 = 6.0201 exactly.
        "attributed": D("3.4442"),
        "cash_delta": D("1.90920"),          # 63.60685 - 61.69765
        "anchor": {
            "pre_arm_utc": "2026-09-26T15:49:54Z",
            "pre_arm_balance": "61.69765",
            "at_disarm_utc": "2026-09-26T17:11:40Z",
            "at_disarm_balance": "63.60685",
            "flat_at_each_read": "zero positions, zero open orders, zero unsettled",
        },
        "reason": ("realized accumulated from DOWN legs priced at the "
                   "venue's WIRE number instead of own-outcome cash"),
    },
    {
        "id": "deleted-down-quantity-1815z",
        "matches_realized_total": D("-2.43330000"),
        # 1815z settled on a book whose DOWN quantity had been deleted while
        # its cost survived: the record read up 1.81 / dn 0 / cost 4.3425 and
        # posted -4.3425. The seven venue order rows say the gross book was
        # UP 0.49 at 0.0931 and DOWN 5.73 at 4.2494 -- BOTH COSTS MATCH THE
        # RECORD EXACTLY, so leg_cost was right and only the quantities were
        # lost. Settled at 0.0000 (DOWN won) that book redeems 5.73 against
        # 4.3425, a GAIN of 1.3875. The error is exactly 5.73: the deleted
        # DOWN shares, each paying $1.
        #
        # Cumulative attributed across both arms: 3.4442 + 1.3875 = 4.8317.
        "attributed": D("4.8317"),
        "cash_delta": D("2.63770"),          # 64.33535 - 61.69765
        "anchor": {
            "pre_arm_utc": "2026-09-26T15:49:54Z",
            "pre_arm_balance": "61.69765",
            "rearm_utc": "2026-09-26T18:14:18Z", "rearm_balance": "63.60685",
            "at_disarm_utc": "2026-09-26T18:55:54Z",
            "at_disarm_balance": "64.33535",
            "flat_at_each_read": "zero positions, zero open orders, zero unsettled",
            "attributed_detail": ("1600z +3.4442 and 1815z +1.3875, both "
                                  "recomputed in own-outcome terms from "
                                  "venue order rows"),
            "commissions_observed": "0.0000 on every 1815z row",
        },
        "reason": ("1815z settled against a book whose DOWN quantity had "
                   "been deleted by reconcile while its cost survived"),
    },
    {
        "id": "costless-venue-net-2045z-2115z",
        "matches_realized_total": D("0.82060000"),
        # Two windows settled against inventory that had NO cost beside it.
        #
        # 2115z is reconstructed from venue order rows and is a real line.
        # Four adopted orders filled, all DOWN (the venue's ORDER_SIDE_SELL
        # / OUTCOME_SIDE_NO / ORDER_INTENT_BUY_SHORT triple), commission
        # 0.0000 on every one:
        #     0.91 @ wire 0.79 -> own 0.21 = 0.1911
        #     0.53 @ wire 0.80 -> own 0.20 = 0.1060
        #     1.72 @ wire 0.80 -> own 0.20 = 0.3440
        #     0.09 @ wire 0.79 -> own 0.21 = 0.0189
        #     3.25 shares, 0.6600 of cost -- and 3.25 is exactly what the
        #     book held under `...2115z|ORDER_SIDE_SELL`.
        # UP won (yes_value 1.0000), so those DOWN shares redeemed 0 and the
        # window's residual P&L is -0.6600.
        #
        # 2045z is NOT reconstructed. Its 2.25 DOWN shares came from order
        # CR157F2WJYC3, which filled, left the open list, and whose
        # /v1/order read raised before anything was booked; no fill row for
        # it survives in what we hold. The cash anchor says the remainder is
        # -0.43230 (2.25 shares at ~0.192 own terms, consistent with the
        # 0.79-0.80 wire regime of the adjacent window) -- but derived by
        # difference is precisely what `unattributed` means, so that is
        # where it stays. It is NOT called a trading line, a fee or a rebate.
        #
        # attributed = 4.8317 (1600z +3.4442, 1815z +1.3875)
        #              - 1.81710 (2100z, settled from a coherent gross book)
        #              - 0.66000 (2115z, from four venue fill rows)
        #            = 2.35460
        "attributed": D("2.35460"),
        "cash_delta": D("-0.27170"),         # 61.42595 - 61.69765
        "anchor": {
            # pre_arm_* is the anchor the cash_delta is measured FROM, and
            # for this rebase that is the START OF THE PROGRAMME, because
            # the attributed line spans every window since it (1600z, 1815z,
            # 2100z, 2115z). The third arm's own pre-arm read is recorded
            # below as third_arm_*, which is evidence, not the anchor.
            "pre_arm_utc": "2026-09-26T15:49:54Z",
            "pre_arm_balance": "61.69765",
            "third_arm_utc": "2026-09-26T20:49:29Z",
            "third_arm_balance": "64.33535",
            "at_disarm_utc": "2026-09-26T21:35:58Z",
            "at_disarm_balance": "61.42595",
            "flat_at_each_read": "zero positions, zero open orders, zero unsettled",
            "api_key_fingerprint": "d51b44a3fa83",
            "attributed_detail": ("1600z +3.4442, 1815z +1.3875, "
                                  "2100z -1.81710, 2115z -0.66000"),
            "unattributed_detail": ("-2.19400 carried forward, plus -0.43230 "
                                    "for 2045z whose 2.25 DOWN shares have "
                                    "no surviving fill row"),
            "superseded_total": "+0.82060 , overstated by 1.09230",
            "commissions_observed": "0.0000 on every 2115z row",
            "rebates_confirmed": ("none. A zero commission is not a rebate "
                                  "and nothing was credited as one."),
        },
        "reason": ("2045z and 2115z settled against quantity that had no "
                   "cost basis: an order released unbooked after a failed "
                   "query, and an adopted order filed under the venue's "
                   "raw side word"),
        # AND the book those defects left behind. The live volume still
        # holds `...2115z|ORDER_SIDE_SELL`, which the canonical-side rules
        # now treat as a permanent gross/net disagreement -- an uncertain
        # book that never submits again. The 21:35:58Z read says the venue
        # holds nothing, owes nothing and has nothing outstanding, and the
        # attributed/unattributed lines above already account for every
        # window in it, so those markets are CLOSED with no P&L posted
        # rather than settled a second time.
        "flat_account": {
            "utc": "2026-09-26T21:35:58Z", "balance": "61.42595",
            "open_orders": 0, "positions": {}, "unsettled_funds": 0,
            "api_key_fingerprint": "d51b44a3fa83",
            "source": ("read-only recovery service 247304c7, GET-only "
                       "transport, deployment 6d9e19ca"),
        },
    },
]


#: The one orphaned key this deployment must repair, and the authoritative
#: evidence for it. Matched on CONTENTS, so a store that never carried the
#: defect is untouched and one already repaired cannot be repaired twice.
ORPHAN_REPAIRS = [
    {
        "id": "blank-market-key-0115z",
        "key": "",
        "to_market": "cpc-btc-updown-15m-2026-09-27-0115z",
        "side": "UP", "qty": D("0.7"), "cost": D("0.09700"),
        # DOWN won 0115z, so these UP shares redeemed nothing.
        "settlement_pnl": D("-0.09700"),
        "matches_cost": D("0.09700"),
        "evidence": {
            "source": "/v1/order rows read by the GET-only recovery service",
            "orders": ["CR52P8GZ4YAQ", "CR52QM4S6YBY"],
            "detail": ("CR52P8GZ4YAQ 0.10 UP avgPx 0.1300 commission 0.0000; "
                       "CR52QM4S6YBY 0.60 UP avgPx 0.1400 commission 0.0000; "
                       "both marketSlug cpc-btc-updown-15m-2026-09-27-0115z"),
            "cash_anchor": ("59.18945 confirmed 01:03:02Z and 59.69195 "
                            "confirmed 02:01:19Z span this; their delta "
                            "+0.50250 = 0115z booked +0.599500 less 0.097000"),
            "api_key_fingerprint": "d51b44a3fa83",
            "why_not_folded_into_0115z": ("0115z is already retired and a "
                                          "settled market cannot settle "
                                          "again; this is its own line"),
        },
    },
]


#: Legs whose fills are known from authoritative venue order rows but which
#: the automatic path could not book. Applied once, at boot, and only when the
#: ledger still holds the broken state each entry describes.
LEDGER_REPAIRS = [
    {
        "id": "1900z-up-1.0-cost-from-attested-order-D294PDYNPZ7Z",
        "market": "cpc-btc-updown-15m-2026-10-12-1900z",
        "side": "UP",
        # the venue's own order row: 1 UP bought at 0.1800, commission 0.0000
        "qty": D("1.00"), "cost": D("0.18000"),
        # what the broken book holds, and must still hold for this repair to
        # be the right one: the quantity with no cost at all
        "expect_qty": D("1.0000"), "expect_cost": D("0"),
        "evidence": {
            "source": ("/v1/portfolio/activities walk + /v1/order/{id}, read "
                       "by the GET-only pmus-readonly-recovery image "
                       "(post_blocked true), run 21:19:14Z-21:23:24Z"),
            "orders": ["D294PDYNPZ7Z"],
            "detail": ("D294PDYNPZ7Z marketSlug "
                       "cpc-btc-updown-15m-2026-10-12-1900z, "
                       "ORDER_SIDE_BUY OUTCOME_SIDE_YES "
                       "ORDER_INTENT_BUY_LONG, ORDER_TYPE_LIMIT, "
                       "limit_price 0.18, quantity 1, cumQuantity 1, "
                       "leavesQuantity 0, state ORDER_STATE_FILLED, "
                       "avgPx 0.1800, commission 0.0000, "
                       "makerCommissionsBasisPoints 0, createTime "
                       "2026-10-12T19:10:17.232593835Z, lastTransactTime "
                       "2026-10-12T19:10:17.713864582Z (rested 0.481s), "
                       "manualOrderIndicator MANUAL_ORDER_INDICATOR_MANUAL "
                       "-- the executor never placed it, which is why no "
                       "fill record and therefore no cost basis existed"),
            "fill_role": ("trade D29DZRQ3RZ6Y, aggressor false, so the fill "
                          "was PASSIVE: a real PMUS BTC15m MAKER fill at "
                          "wire 0.1800 with commission 0.0000"),
            "position_resolution": (
                "beforePosition netPosition 1 qtyBought 1 cost 0.1800 "
                "realized 0.0000 avgPx 0.1800; afterPosition netPosition 0 "
                "qtyBought 1 qtySold 1 cost 0.0000 realized -0.1800 at "
                "2026-10-12T19:15:15.201040938Z, side "
                "POSITION_RESOLUTION_SIDE_SHORT. The venue's own realized "
                "line agrees with cost 0.18 against a zero settlement."),
            "settlement": ("/v1/markets/.../bbo settlementPx 0.0000, state "
                           "MARKET_STATE_EXPIRED, openInterest 0: the 1.00 "
                           "UP redeemed nothing, so settling this leg posts "
                           "-0.18000"),
            "not_used_as_evidence": (
                "the account balance moving 243.15505 -> 242.97505. That "
                "-0.18 is account-wide and merely CONSISTENT with this "
                "cost; the cost comes from the order row's avgPx. Inferring "
                "it from the balance was explicitly forbidden and would "
                "have been unsound even though the numbers agree."),
            "api_key_fingerprint": "d51b44a3fa83",
            "what_went_wrong": ("a MANUAL order filled into a market the "
                                "executor was reconciling, so venue "
                                "reconciliation adopted the quantity while "
                                "no fill record carried its price"),
        },
    },
    {
        "id": "1300z-incoherent-resolver-zero-fill",
        "market": "cpc-btc-updown-15m-2026-09-27-1300z",
        "side": "UP",
        # what the venue's own order rows say was actually bought
        "qty": D("1.80"), "cost": D("0.54000"),
        # what the broken book held instead, and must still hold for this
        # repair to be the right one to apply
        "expect_qty": D("0.9000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order rows read by the GET-only recovery service",
            "orders": ["CREX7ZDP2YC6", "CREWYQ75RYCA"],
            "detail": ("CREX7ZDP2YC6 ORDER_STATE_FILLED quantity 0.9 "
                       "cumQuantity 0.9 avgPx 0.3100 commission 0.0000; "
                       "CREWYQ75RYCA ORDER_STATE_FILLED quantity 0.9 "
                       "cumQuantity 0.9 avgPx 0.2900 commission 0.0000; "
                       "both marketSlug cpc-btc-updown-15m-2026-09-27-1300z, "
                       "OUTCOME_SIDE_YES, ORDER_INTENT_BUY_LONG"),
            "settlement": ("/v1/markets/.../bbo settlementPx 0.0000, state "
                           "MARKET_STATE_EXPIRED: the 1.80 UP redeemed "
                           "nothing, so settling this leg posts -0.54000"),
            "cash_anchor": ("60.59505 confirmed 12:52:03Z and 60.05505 "
                            "confirmed by /v1/account/balances 14:26:31Z; "
                            "their delta -0.54000 IS this leg's cost"),
            "positions_at_repair": "/v1/portfolio/positions empty; openOrders 0",
            "api_key_fingerprint": "d51b44a3fa83",
            "what_went_wrong": ("the parked-order resolver accepted a venue "
                                "row reading state ORDER_STATE_NEW, quantity "
                                "0.9, cumQuantity 0, leavesQuantity 0 and "
                                "booked a zero fill as final. order_progress "
                                "now raises IncoherentOrderRow on a row whose "
                                "cum + leaves != quantity"),
            "original_bad_record": ("positions 1300z|UP = 0.9000 with "
                                    "leg_cost 0 and fill_count 0, which "
                                    "QUANTITY_WITHOUT_COST refused to settle "
                                    "91+ times. It is replaced, not erased: "
                                    "cost_reconstructions keeps the write and "
                                    "this entry keeps what it replaced"),
        },
    },
    {
        "id": "1245z-manual-yes-fill-no-cost-basis",
        "market": "cpc-btc-updown-15m-2026-10-12-1245z",
        "side": "UP",
        "qty": D("2"), "cost": D("1.6800"),
        "expect_qty": D("2.0000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order read by the GET-only pmus-readonly-recovery",
            "orders": ["D23N485QAZ89"],
            "detail": ("D23N485QAZ89 ORDER_STATE_FILLED quantity 2 "
                       "cumQuantity 2 leavesQuantity 0 avgPx 0.8400 "
                       "commission 0.0000, OUTCOME_SIDE_YES, "
                       "ORDER_INTENT_BUY_LONG, limit_price 0.84, "
                       "resting_s 10.274, "
                       "manualOrderIndicator MANUAL_ORDER_INDICATOR_MANUAL"),
            "settlement": ("/v1/markets/.../bbo state MARKET_STATE_EXPIRED, "
                           "settlementPx 0.0000: the 2 UP redeemed nothing, "
                           "so settling this leg posts -1.6800"),
            "positions_at_repair": ("/v1/portfolio/positions {} with "
                                    "unsettledFunds 0, openOrders 0 and "
                                    "buyingPower 243.15505 -- the venue holds "
                                    "nothing, because it already settled"),
            "what_went_wrong": ("the order was MANUAL, so the executor never "
                                "recorded a fill for it and took the quantity "
                                "from venue reconciliation with no cost. "
                                "QUANTITY_WITHOUT_COST then refused to settle "
                                "it 4,107+ times"),
            "why_not_dropped": ("on settlement price alone this looks like a "
                                "phantom: cost 0, cash_deployed 0, "
                                "fills_booked 0, venue net 0, settlementPx "
                                "0.0000. It is NOT. $1.68 of cash genuinely "
                                "left the account. A settlement price says "
                                "what a share PAID; only the order record "
                                "says what it COST. Dropping this quantity "
                                "would have erased a real loss"),
        },
    },
    {
        "id": "nfl-bal-atl-manual-yes-legs-undercounted",
        "market": "aec-nfl-bal-atl-2026-10-11",
        "side": "UP",
        "qty": D("4"), "cost": D("1.7700"),
        "expect_qty": D("2"), "expect_cost": D("0.8800"),
        "evidence": {
            "source": "/v1/order reads by the GET-only pmus-readonly-recovery",
            "orders": ["D1SA9RRSTZ73", "D1S42Z0APZ73"],
            "detail": ("D1SA9RRSTZ73 FILLED qty 2 cumQuantity 2 avgPx 0.4400 "
                       "commission -0.0100; D1S42Z0APZ73 FILLED qty 2 "
                       "cumQuantity 2 avgPx 0.4450 commission +0.0300; both "
                       "OUTCOME_SIDE_YES ORDER_INTENT_BUY_LONG and MANUAL. "
                       "Own-outcome UP cost = 2*0.4400 + 2*0.4450 = 1.7700"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 1.0000: "
                           "4 UP redeem 4.0000 against cost 1.7700"),
            "what_went_wrong": ("the ledger held only ONE of the two YES "
                                "orders (2 @ 0.4400 = 0.8800); the second was "
                                "never booked because it was MANUAL"),
        },
    },
    {
        # RUNTIME-DISCOVERED, deployment 583ced9b: this market was ALREADY
        # RETIRED before the repair shipped, so retire_settled_market never
        # ran for it here -- and retirement is what pops the position rows.
        # Its DOWN row survived with no cost, book_violations kept reporting
        # QUANTITY_WITHOUT_COST, and reconcile therefore RE-SET
        # orders_uncertain on every pass. SafetyLimits.refuse() returns
        # ORDER_STATE_UNCERTAIN before any other check, so every MAKER
        # action in the window was refused. Observed in the 1715z window at
        # 17:16-17:18Z with "incoherent book ['...0030z']".
        #
        # This books ONLY the cost basis the venue established, which makes
        # the book coherent. It posts NO P&L: reconstruct_cost_basis does
        # not touch realized, and the market is already retired so
        # ALREADY_RETIRED makes a second settlement impossible. The +1.7000
        # for this market is already recognised via the labelled adjustment.
        "id": "0030z-retired-row-missing-cost-basis",
        "market": "cpc-btc-updown-15m-2026-10-12-0030z",
        "side": "DOWN",
        "qty": D("2"), "cost": D("0.3000"),
        "expect_qty": D("2.0000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order read by the GET-only pmus-readonly-recovery",
            "orders": ["D1SCCX4F8Z84"],
            "detail": ("D1SCCX4F8Z84 ORDER_STATE_FILLED quantity 2 "
                       "cumQuantity 2 avgPx 0.8500 commission 0.0000, "
                       "OUTCOME_SIDE_NO ORDER_INTENT_BUY_SHORT, MANUAL. The "
                       "venue quotes the LONG price, so the own-outcome DOWN "
                       "cost is 2 * (1 - 0.8500) = 0.3000"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 0.0000. "
                           "Already retired, and its +1.7000 is already "
                           "recognised through the labelled 0.0200 "
                           "adjustment, so this books a cost basis ONLY and "
                           "posts no P&L"),
            "what_went_wrong": ("the ledger repair retired the two UNretired "
                                "markets and corrected this one's realized "
                                "figure, but left this position row in "
                                "place. A retired market's stale row still "
                                "fails book coherence, which kept "
                                "orders_uncertain set and refused every "
                                "MAKER action"),
        },
    },
    {
        "id": "nfl-bal-atl-manual-no-legs-no-cost-basis",
        "market": "aec-nfl-bal-atl-2026-10-11",
        "side": "DOWN",
        "qty": D("4"), "cost": D("2.1700"),
        "expect_qty": D("2.0000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order reads by the GET-only pmus-readonly-recovery",
            "orders": ["D1SA22CA2Z7Z", "D1S4RM12JZ7T"],
            "detail": ("D1SA22CA2Z7Z FILLED qty 2 cumQuantity 2 avgPx 0.4650 "
                       "commission +0.0300; D1S4RM12JZ7T FILLED qty 2 "
                       "cumQuantity 2 avgPx 0.4500 commission -0.0100; both "
                       "OUTCOME_SIDE_NO ORDER_INTENT_BUY_SHORT and MANUAL. "
                       "The venue quotes ONE number per market, the LONG "
                       "price, so a NO leg's own-outcome cost is 1 - wire: "
                       "2*(1-0.4650) + 2*(1-0.4500) = 1.0700 + 1.1000 "
                       "= 2.1700 for 4 DOWN"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 1.0000: "
                           "4 DOWN redeem 0.0000 against cost 2.1700, so the "
                           "market's own-outcome settlement is "
                           "(4.0000-1.7700) + (0-2.1700) = +0.0600 gross, "
                           "and +0.0200 net of the 0.0400 of commissions "
                           "which this ledger keeps as a separate line"),
            "what_went_wrong": ("both NO legs were MANUAL, so the ledger held "
                                "a DOWN quantity with no cost at all"),
        },
    },
]


#: SETTLEMENT ADJUSTMENTS -- for a market already retired, whose recognised
#: settlement is provably wrong by a known amount.
#:
#: SEPARATE FROM LEDGER_REPAIRS ON PURPOSE. A repair fixes a cost basis
#: BEFORE settlement. This corrects a figure recognised AFTER it. It must
#: never retire the market again, never rewrite the original
#: `retired_markets` entry, and never bypass ALREADY_RETIRED -- that guard
#: exists because 2115z re-emitted its settlement 40+ times on 2026-09-26,
#: and a second pass would post a second P&L and move the loss halt with it.
#:
#: So this adds a DELTA to `realized_attributed` and nothing else, keyed by a
#: unique id in its own applied-list, and it verifies the market's recorded
#: settlement_pnl is exactly the wrong value it was written for. If the
#: ledger says anything else, the adjustment refuses rather than guesses.
LEDGER_ADJUSTMENTS = [
    {
        "id": "0030z-settlement-understated-by-two-cents",
        "market": "cpc-btc-updown-15m-2026-10-12-0030z",
        "expect_recorded_pnl": D("1.6800"),
        "authoritative_pnl": D("1.7000"),
        "delta": D("0.0200"),
        "evidence": {
            "source": "/v1/order and /v1/markets/.../bbo via the GET-only "
                      "pmus-readonly-recovery service",
            "orders": ["D1SCCX4F8Z84"],
            "detail": ("D1SCCX4F8Z84 ORDER_STATE_FILLED quantity 2 "
                       "cumQuantity 2 avgPx 0.8500 commission 0.0000, "
                       "OUTCOME_SIDE_NO ORDER_INTENT_BUY_SHORT, MANUAL. "
                       "Own-outcome DOWN cost = 2*(1-0.8500) = 0.3000"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 0.0000: "
                           "2 DOWN redeem 2.0000 against cost 0.3000, so the "
                           "authoritative settlement is +1.7000. The venue "
                           "independently reported realized 1.7000 for this "
                           "position, which this computation reproduces from "
                           "the order alone"),
            "before": "retired_markets settlement_pnl 1.6800",
            "after": "realized_attributed increased by 0.0200; the "
                     "retired_markets entry is left EXACTLY as written",
            "why_an_adjustment_and_not_a_repair": (
                "the market is already retired. Re-settling it would post a "
                "second P&L, so ALREADY_RETIRED correctly forbids it. The "
                "discrepancy is real but can only be corrected as a labelled "
                "delta, never by replaying the settlement"),
        },
    },
]


def apply_ledger_adjustments(store) -> Optional[Dict[str, Any]]:
    """Add labelled settlement deltas to already-retired markets.

    Idempotent by id in `ledger_adjustments_applied`, which persists in the
    state file, so a restart cannot apply one twice. Refuses unless the
    market is retired AND its recorded settlement_pnl is exactly the value
    the adjustment was written for.
    """
    done = store.state.setdefault("ledger_adjustments_applied", [])
    applied: List[Dict[str, Any]] = []
    for a in LEDGER_ADJUSTMENTS:
        if a["id"] in done:
            continue
        retired = (store.state.get("retired_markets") or {}).get(a["market"])
        if not retired:
            emit("L5_LIVE_ADJUST_REFUSED", {
                "id": a["id"], "market": a["market"],
                "why": ("the market is not retired, so there is no recognised "
                        "settlement to adjust. A cost-basis repair, not an "
                        "adjustment, is what an unsettled market needs")})
            continue
        recorded = D(str(retired.get("settlement_pnl") or 0))
        if recorded != a["expect_recorded_pnl"]:
            emit("L5_LIVE_ADJUST_REFUSED", {
                "id": a["id"], "market": a["market"],
                "recorded_pnl": str(recorded),
                "expected_recorded_pnl": str(a["expect_recorded_pnl"]),
                "why": ("the ledger does not hold the figure this adjustment "
                        "was written for; adjusting blind is manufacturing "
                        "P&L")})
            continue
        before = D(str(store.state.get("realized_attributed") or 0))
        after = before + D(str(a["delta"]))
        store.state["realized_attributed"] = str(after)
        # the legacy scalar is kept moving in step, as settlement does
        store.state["realized_today"] = str(
            D(str(store.state.get("realized_today") or 0))
            + D(str(a["delta"])))
        log = store.state.setdefault("settlement_adjustments", [])
        log.append({
            "id": a["id"], "market": a["market"],
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "recorded_pnl": str(recorded),
            "authoritative_pnl": str(a["authoritative_pnl"]),
            "delta": str(a["delta"]),
            "realized_attributed_before": str(before),
            "realized_attributed_after": str(after),
            "evidence": a["evidence"],
            "retired_entry_untouched": True,
        })
        done.append(a["id"])
        store.save()
        rec = {"id": a["id"], "market": a["market"],
               "delta": str(a["delta"]),
               "realized_attributed_before": str(before),
               "realized_attributed_after": str(after),
               "retired_entry_untouched": True,
               "orders": a["evidence"]["orders"]}
        emit("L5_LIVE_ADJUSTED", rec)
        applied.append(rec)
    return {"applied": applied} if applied else None


#: SHADOW-ONLY LOCK. "0" releases it; anything else (including unset) locks.
def mandate_store_probe() -> Dict[str, Any]:
    """Fetch the watchdog's replay floor and revocation list. READ-ONLY.

    Nothing here arms, submits or authorizes. It reports whether the two
    documents the signature gate depends on are actually reachable, fresh
    and well-formed, using the gate's own clients so the answer is the
    gate's answer rather than a separate opinion.

    ABSENT CONFIGURATION IS REPORTED, NOT FAILED. With no store URL the gate
    refuses MANDATE_STORE_UNCONFIGURED only when a trusted key exists, and
    none does yet, so an unconfigured probe is the expected state rather
    than a fault.
    """
    from src import l5_mandate_signature as MS
    url = (os.getenv(MS.STORE_URL_ENV) or "").strip()
    out: Dict[str, Any] = {
        "ran": True, "env": MS.STORE_URL_ENV, "configured": bool(url),
        "serves": [MS.FLOOR_KEY, MS.REVOCATION_KEY],
        "max_age_s": MS.DEFAULT_MAX_AGE_S,
        "trusted_key_ids": sorted(MS.MANDATE_PUBKEYS) or None,
        "gate_binds_when": "MANDATE_PUBKEYS is non-empty",
        "labels": ("READ-ONLY. Authorizes nothing. A success means the "
                   "documents exist, parse, carry a usable asof_utc and sit "
                   "inside the staleness bound -- the same checks the "
                   "arming gate makes, through the same GET-only clients.")}
    if not url:
        out["verdict"] = "STORE_UNCONFIGURED"
        return out
    # the host only. Not a secret -- the order registry URL is already in
    # these logs -- but there is no reason to echo a path or a query.
    out["host"] = url.split("//")[-1].split("/")[0]
    for label, call in (
            ("floor", lambda: MS.HttpSeqFloorStore(url).floor("probe")),
            ("revocations", lambda: sorted(
                MS.HttpRevocationStore(url).revoked()))):
        try:
            out[label] = {"ok": True, "value": call()}
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out[label] = {"ok": False,
                          "refusal": type(exc).__name__,
                          "detail": ("%s" % exc)[:200]}
    out["verdict"] = ("BOTH_DOCUMENTS_FRESH"
                      if out.get("floor", {}).get("ok")
                      and out.get("revocations", {}).get("ok")
                      else "DOCUMENT_UNAVAILABLE")
    return out


SHADOW_ONLY_ENV = "L5_SHADOW_ONLY"


class ShadowOnlyTransport:
    """Everything the real transport does, except it cannot submit an order.

    WHY A PROXY AND NOT A FLAG. `armed()` is three environment variables that
    must agree, and all three are already present on this service. A flag
    checked somewhere in the decision path can be bypassed by any code path
    that forgets to check it; a transport that has no working submit cannot
    be talked into one. The disarmed state becomes a property of the object
    graph rather than an observation about a heartbeat.

    `submit` raises `NotArmed`, which is the SAME exception the disarmed
    transport already raises, so it lands in the executor's existing,
    already-tested handler: the order reservation is undone and the decision
    is recorded as NOT_ARMED. No new code path runs on the money side.

    CANCEL IS DELIBERATELY NOT BLOCKED. Cancelling only ever reduces
    exposure, and a lock that prevented it could strand a resting order.
    Reads pass through untouched so reconciliation, settlement and the
    shadow all keep working.

    This does NOT alter the owner's live authorization. It sits in front of
    it, and the owner releases it explicitly with L5_SHADOW_ONLY=0.
    """

    #: every name that could put an order on the venue
    BLOCKED = ("submit",)

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def inner(self) -> Any:
        return self._inner

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        raise LX.NotArmed(
            "SHADOW_ONLY: this deployment cannot submit orders. The lock is "
            "structural, not a flag, and is released only by setting "
            "%s=0." % SHADOW_ONLY_ENV)

    def __getattr__(self, name: str) -> Any:
        # anything not named above delegates. BLOCKED names are defined
        # explicitly on the class, so they never reach here.
        if name in self.BLOCKED:                       # pragma: no cover
            raise AttributeError(name)
        return getattr(self._inner, name)


#: UNRESOLVED ACCOUNTING, declared in code so it is durable across restarts
#: and cannot be cleared by a reconcile that merely stops noticing.
#:
#: 1900z: the venue itself reported net UP 1.0000 at 19:15:10Z and our GROSS
#: book AGREED (gross_net_agrees true), so the QUANTITY is venue-confirmed
#: and must never be zeroed. The market expired at 19:15:15Z and positions
#: cleared, so no venue read can now recover the COST. /v1/orders answers
#: 501, /v1/mytrades and /v1/trades answer 404, and the venue exposes order
#: detail only at /v1/order/{id} -- so with no discoverable id the cost
#: basis is unobtainable and the possible loss stays possible.
#:
#: This is NOT a repair. It records that the row is unresolved, keeps it
#: unresolved, and blocks live authorization while it stands.
UNRESOLVED_ACCOUNTING: List[Dict[str, Any]] = [
    {
        "id": "1900z-up-1.0-unknown-cost-basis",
        "market": "cpc-btc-updown-15m-2026-10-12-1900z",
        #: the LEDGER this applies to, matched on the store's own path.
        #: Scoping to the ledger rather than to the row's PRESENCE is what
        #: stops a deletion from removing the protection: the entry applies
        #: whether or not the row is still there.
        "ledger": "l5-live-20261012z",
        "side": "UP",
        "qty": "1.0000",
        "cost_basis": "UNKNOWN",
        "discovered_utc": "2026-10-12T19:15:30Z",
        "venue_confirmed_quantity": True,
        "venue_evidence": (
            "L5_LIVE_RECONCILE 19:15:10Z venue_net "
            "{cpc-btc-updown-15m-2026-10-12-1900z: 1.0000}, "
            "gross_net_agrees true"),
        "missing_evidence": (
            "the 1900z venue order id. /v1/orders is 501, /v1/mytrades and "
            "/v1/trades are 404, and order detail exists only at "
            "/v1/order/{id}. Needs the id from the owner's order history, "
            "an account statement or a trade confirmation."),
        "may_not": (
            "book a cost basis from the balance delta, or zero the "
            "venue-confirmed quantity. Either falsifies the record."),
        #: RESOLVED 2026-10-12T21:23Z from the venue's own order row, which
        #: is exactly the evidence `missing_evidence` above named. The record
        #: is kept rather than deleted: how a possible loss was resolved is
        #: audit trail, and a registry that forgets its closed cases cannot
        #: be checked. `unresolved_accounting()` filters on this key, so the
        #: arming block lifts for this entry and for no other reason.
        "resolved_utc": "2026-10-12T21:23:24Z",
        "resolved_evidence": (
            "order D294PDYNPZ7Z, /v1/order: state ORDER_STATE_FILLED, "
            "cumQuantity 1, avgPx 0.1800, commission 0.0000, PASSIVE fill "
            "(trade D29DZRQ3RZ6Y, aggressor false). POSITION_RESOLUTION "
            "afterPosition realized -0.1800 against settlementPx 0.0000. "
            "Cost basis $0.18, realized loss -$0.18, booked by the repair "
            "1900z-up-1.0-cost-from-attested-order-D294PDYNPZ7Z through "
            "reconstruct_cost_basis, which records its source."),
        "resolved_not_by": (
            "the -0.18 account balance move, which was never used and is "
            "only consistent with the answer"),
    },
]


class TimedCounter(collections.Counter):
    """A Counter that remembers WHEN each key last moved.

    WHY THIS EXISTS. `heartbeat` emits `dict(self.stats)`, and a
    process-lifetime count with no clock on it cannot distinguish a
    condition firing NOW from one that stopped firing ten hours ago. On
    2026-10-18 the heartbeat carried

        HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER   65
        halt_latch  CLEAR          open_orders_at_venue  0

    and all three were true at once: the 65 were counted between the
    process start at 02:51 and the owner's 03:23:40 reset of the latch that
    order `D4EFZNW00ZZ0` had set on 2026-10-16, and the counter never moved
    again. Read live, it looks like an unmanaged order right now.

    The same ambiguity already misled an operator once -- see `heartbeat`,
    where `halt` printed false through 2,939 consecutive refusals. That fix
    corrected the halt FIELD and left the counts ambiguous.

    Counting is not the problem, so nothing about the counts changes and no
    gate is touched: each key simply carries the moment it last advanced.
    Every existing `self.stats[...] += 1` site is unmodified, because
    Counter's in-place add routes through __setitem__.
    """

    def __init__(self, *a: Any, clock=time.time, **kw: Any) -> None:
        self._clock = clock
        self.last_t: Dict[Any, float] = {}
        super().__init__(*a, **kw)

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self.last_t[key] = self._clock()

    def ages(self, now: Optional[float] = None) -> Dict[Any, float]:
        """Seconds since each key last advanced. A frozen counter says so."""
        t = self._clock() if now is None else now
        return {k: round(t - v, 1) for k, v in self.last_t.items()}


def unresolved_accounting() -> List[Dict[str, Any]]:
    """Every STANDING unresolved-accounting record.

    A resolved entry keeps its row -- deleting the history of a possible
    loss is the thing this registry exists to prevent -- but stops standing,
    because `resolved_utc` names the authoritative evidence that answered
    it. Nothing else lifts the block: there is no flag, no override and no
    env var that makes an unresolved entry stop counting.
    """
    return [dict(r) for r in UNRESOLVED_ACCOUNTING
            if not str(r.get("resolved_utc") or "").strip()]


def resolved_accounting() -> List[Dict[str, Any]]:
    """The closed cases, kept visible on purpose."""
    return [dict(r) for r in UNRESOLVED_ACCOUNTING
            if str(r.get("resolved_utc") or "").strip()]


def unresolved_discrepancies(store: Any) -> List[Dict[str, Any]]:
    """Registry entries the LEDGER has stopped reporting as unresolved.

    A module function over the store, NOT a Runner method: try_arm is
    grafted onto stubs in the arm-retry suite, so a new instance attribute
    there is the same duck-typing hazard the graft closure test exists to
    catch -- and it bit this change once already.

    Empty is normal in two very different situations, and both are correct:
      * the ledger still holds the unresolved row -- orders_uncertain
        refuses on its own and this adds nothing;
      * the ledger does not hold that market at all (a different or fresh
        book) -- the entry is about a specific row in a specific ledger and
        must not block an unrelated one, which would make the whole arming
        path unreachable dead code.
    Non-empty means the row was cleared WITHOUT the missing evidence, which
    is the one thing orders_uncertain cannot notice.
    """
    out: List[Dict[str, Any]] = []
    # the store's own path identifies the LEDGER. Read with a default
    # because the arm-retry suite's partial stub has none -- InventoryStore
    # declares self.path, so this is a partial stub rather than an
    # undeclared attribute, and both branches are tested.
    path = str(getattr(store, "path", "") or "")
    for rec in unresolved_accounting():
        m = str(rec.get("market") or "")
        ledger = str(rec.get("ledger") or "")
        if not m or not ledger:
            continue
        if ledger not in path:
            continue                 # a different ledger: never blocked
        try:
            keys = set(store.state.get("positions") or {})
            present = any(k.rsplit("|", 1)[0] == m for k in keys)
            violating = bool(store.book_violations(m)) if present else False
        except Exception as exc:                               # noqa: BLE001
            # FAIL CLOSED. This is the named ledger and we cannot read the
            # book we are responsible for, so we must not conclude all-clear.
            out.append({"id": rec.get("id"), "market": m,
                        "was": "QUANTITY_WITHOUT_COST",
                        "now": "ledger UNREADABLE: %s" % type(exc).__name__,
                        "missing_evidence": rec.get("missing_evidence")})
            continue
        if present and violating:
            continue                 # orders_uncertain refuses on its own
        out.append({
            "id": rec.get("id"), "market": m,
            "was": "QUANTITY_WITHOUT_COST",
            # an ABSENT row is the dangerous case: deleting or losing it
            # must never read as resolution.
            "now": ("row ABSENT from the ledger" if not present
                    else "row present but no violation reported"),
            "missing_evidence": rec.get("missing_evidence"),
            "labels": ("the loss was never accounted. Its row "
                       "disappearing is not evidence it was resolved."),
        })
    return out


def unresolved_markets() -> set:
    return {str(r.get("market")) for r in UNRESOLVED_ACCOUNTING}


MODEL_FILLS_ENV = "L5_MODEL_FILLS"


def model_fills() -> bool:
    """MODELLED maker-fill instrumentation, ON by default.

    It cannot submit, cancel, arm, or alter any gate: it only reads the book
    and the intents the executor already produces. Set L5_MODEL_FILLS=0 to
    silence it.
    """
    return str(os.getenv(MODEL_FILLS_ENV, "1")).strip() != "0"


def shadow_only() -> bool:
    return str(os.getenv(SHADOW_ONLY_ENV, "1")).strip() != "0"


#: completion repricing. DEFAULT OFF, so the code can be deployed and
#: reviewed in place before it is ever allowed to cancel an order. It adds a
#: cancellation, never a capability: the replacement goes through the same
#: complement path, the same gates and the same REPLACE budget as today.
COMPLETION_REQUOTE_ENV = "L5_COMPLETION_REQUOTE"


def completion_requote_enabled() -> bool:
    return (str(os.getenv(COMPLETION_REQUOTE_ENV, "")).strip().upper()
            in ("1", "YES", "TRUE", "ON"))


#: names the guard service's READ-ONLY halt endpoint. Unset = inert.
HALT_URL_ENV = "L5_HALT_URL"
#: "0" disables the validation gates; anything else (including unset) is ON
GATES_ENV = "L5_VALIDATION_GATES"


#: the worst-case settlement deficit bound. Unset or blank = gate disabled.
DEFICIT_ENV = "L5_MAX_WORST_CASE_DEFICIT_USD"


def _campaign_nonce_sid():
    """The approval's own campaign id, or None. Never raises.

    Read from the document rather than from any variable an operator could
    set independently: the budget must be owned by the APPROVAL, so that
    issuing a new approval is exactly what the budget ledger notices and
    refuses, rather than something it can be told to ignore.
    """
    try:
        # THE CANONICAL GATE. This used to build its own with the
        # 1-hour default, so the budget's campaign id vanished an hour
        # after issue and gate 3b skipped itself while arming stayed
        # authorized for four hours.
        st = CAMP.approval_gate().state()
        return str((st.get("scope") or {}).get("nonce_sid") or "") or None
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def capture_campaign_baseline(runner: Any) -> Dict[str, Any]:
    """Persist this campaign's starting cash, inventory, open orders and
    realized loss. Read-only with respect to every existing figure.

    WHAT IT DOES NOT DO: it does not reset, net off or reclassify any realized
    loss. It records what the account-wide realized total WAS at the campaign's
    start, so the $5 additional-loss bound has a fixed point to measure from.
    The lifetime ceiling goes on reading the full figure on every evaluation,
    unchanged.

    MODULE-LEVEL, AND READING THE RUNNER DEFENSIVELY, for a reason the test
    suite made plain: the arm path is exercised with partial runner doubles
    that implement only what arming needs, so a `self.`-method here raised
    AttributeError and took the whole arm down with it. A baseline capture must
    never be able to do that. Every field is read with getattr and every read
    is individually guarded.

    NEVER RAISES, AND A FAILURE CANNOT CREATE RISK. With the bound configured
    and no baseline stored, `ValidationGates` refuses every order with
    CAMPAIGN_BASELINE_ABSENT -- so a failed capture stops trading rather than
    permitting it, and reporting is the right response here.
    """
    try:
        from src import l5_campaign_baseline as CBL
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "reason": "CAPTURE_IMPORT_FAILED:%s"
                % type(exc).__name__}
    try:
        cid = _campaign_nonce_sid()
        if not cid:
            return {"ok": False, "reason": "NO_CAMPAIGN_ID",
                    "note": "no valid approval, so there is no campaign to "
                            "measure; the bound refuses on its own"}
        ex = getattr(runner, "executor", None)
        store = getattr(ex, "store", None)
        cash = getattr(runner, "available_cash_usd", None)
        inv: Any = None
        opens: Any = None
        realized: Any = None
        if store is not None:
            try:
                inv = {m: str(store.residual(m))
                       for m in (store.markets_with_exposure() or ())}
            except BaseException:                              # noqa: BLE001
                inv = "UNREADABLE"
            try:
                opens = sorted((store.live_orders() or {}).keys())
            except BaseException:                              # noqa: BLE001
                opens = "UNREADABLE"
            try:
                realized = store.realized_today()
            except BaseException:                              # noqa: BLE001
                realized = None
        res = dict(CBL.CampaignBaseline().capture(
            cid,
            account_wide_realized=realized,
            cash_usd=cash, inventory=inv, open_orders=opens,
            now_iso=dt.datetime.now(dt.timezone.utc).isoformat()))
        res["cash_source"] = ("/v1/account/balances" if cash is not None
                              else "UNAVAILABLE")
        lim = CBL.limit_from_env()
        res["limit_in_force_usd"] = None if lim is None else str(lim)
        return res
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False,
                "reason": "CAPTURE_ERROR:%s" % type(exc).__name__}


def validation_gates():
    """The run-scoped additive gates, or None when explicitly disabled.

    A MISCONFIGURED DEFICIT BOUND IS FATAL, NOT A FALLBACK. Everything else
    here degrades to None on error, which is correct for a gate that was
    never requested -- the pre-existing limits still stand. But a deficit
    bound someone TYPED and we cannot parse is the opposite case: returning
    None would drop every additive gate while the operator believes a bound
    is in force. So that one error is re-raised.
    """
    if str(os.getenv(GATES_ENV, "1")).strip() == "0":
        return None
    from src import l5_live_validation_gates as VG
    from src import l5_campaign_baseline as CBL
    # parsed FIRST and outside the swallow below, so it cannot be absorbed
    deficit = VG.parse_deficit_bound(os.getenv(DEFICIT_ENV))
    try:
        return VG.ValidationGates(
            max_cash_deployed_usd=D(str(os.getenv(
                "L5_MAX_CASH_DEPLOYED_USD", "10"))),
            max_unmatched_contracts=D(str(os.getenv(
                "L5_MAX_UNMATCHED_CONTRACTS", "5"))),
            max_cumulative_loss_usd=D(str(os.getenv(
                "L5_MAX_CUMULATIVE_LOSS_USD", "20"))),
            # Separately opt-in to the verified L5-only after-fee loss gate.
            # Without an audited net this refuses, never defaults to $0.
            max_strategy_loss_usd=(
                None if not os.getenv("L5_STRATEGY_LOSS_LIMIT_USD", "").strip()
                else D(os.getenv("L5_STRATEGY_LOSS_LIMIT_USD").strip())),
            max_worst_case_deficit_usd=deficit,
            # THE OWNER'S EXISTING FIGURES, RESCOPED -- not raised. They
            # measure attested L5 settlements since the baseline epoch, and
            # they sit BESIDE the account-wide realized-loss check, which is
            # unchanged and still runs first.
            max_l5_daily_loss_usd=(
                None if not os.getenv("L5_MAX_L5_DAILY_LOSS_USD", "").strip()
                else D(os.getenv("L5_MAX_L5_DAILY_LOSS_USD").strip())),
            max_l5_cumulative_loss_usd=(
                None if not os.getenv("L5_MAX_L5_CUMULATIVE_LOSS_USD",
                                      "").strip()
                else D(os.getenv("L5_MAX_L5_CUMULATIVE_LOSS_USD").strip())),
            # THE INDEPENDENT ACCOUNT-WIDE BACKSTOP. Additive: it can only
            # add a refusal. It is the precondition any future L5-scoped
            # rescoping must satisfy alongside it, because an L5-scoped
            # ceiling alone would permit trading with material account-wide
            # losses unresolved.
            max_unresolved_unknown_usd=(
                None if not os.getenv("L5_MAX_UNRESOLVED_UNKNOWN_USD",
                                      "").strip()
                else D(os.getenv("L5_MAX_UNRESOLVED_UNKNOWN_USD").strip())),
            # THE ACCOUNT-WIDE DELTA CEILING, bounding what the account can
            # lose AFTER the immutable baseline epoch. UNSET BY DEFAULT, which
            # makes the check inert: it is DEVELOPED, not activated, and
            # activating it is a separate owner decision. It is additive and
            # does NOT replace max_cumulative_loss_usd, which stays
            # configured above and keeps gating the lifetime figure.
            max_account_wide_delta_loss_usd=(
                None if not os.getenv("L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD",
                                      "").strip()
                else D(os.getenv(
                    "L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD").strip())),
            # THE CAMPAIGN WORST-CASE BUDGET. Both halves come from the same
            # place so they cannot disagree: the ledger is built from
            # L5_CAMPAIGN_BUDGET_USD (unset = off, no behaviour change) and
            # the campaign id is the APPROVAL's own nonce_sid, never an
            # operator-supplied string. If the approval is absent or invalid
            # the id is None and the gate stays inert -- an unauthorized run
            # is already refused by the campaign layer itself.
            campaign_budget=AUTH.campaign_budget_ledger(),
            campaign_id=_campaign_nonce_sid(),
            # THE CAMPAIGN'S OWN ADDITIONAL-LOSS BOUND, measured from a
            # persisted starting point rather than from account inception.
            #
            # BOTH HALVES COME FROM THE SAME PLACE, as with the budget above:
            # the store is on the volume and the campaign id is the approval's
            # own nonce_sid, so the bound is owned by the APPROVAL and a new
            # approval is exactly what moves the zero point.
            #
            # UNSET IS OFF, so this changes nothing until the owner configures
            # it. A value that was TYPED and cannot be parsed resolves to $0 in
            # `limit_from_env`, which refuses everything -- the tightest
            # reading, never the default. That is why it needs no special
            # handling outside the swallow below the way the deficit bound
            # does: it cannot raise, and its error case is already fail-closed.
            campaign_baseline=CBL.CampaignBaseline(),
            max_campaign_additional_loss_usd=CBL.limit_from_env(),
            cash_policy=VG.NO_RECYCLING)
    except Exception as exc:                                   # noqa: BLE001
        # ============ A GATE WE CANNOT BUILD REFUSES. IT DOES NOT VANISH.
        #
        # THE FAIL-OPEN THIS REPLACES. This used to `return None`, and the
        # submission path reads `if vg is not None:` -- so one malformed
        # value among the ten env reads above dropped the WHOLE additive
        # layer: the $20 cumulative loss halt, the $1.65 cash envelope, the
        # 5-contract unmatched cap and the campaign budget, all at once,
        # while the operator believed they were in force. The old label
        # said "Pre-existing limits are unaffected", which is true of
        # SafetyLimits and beside the point: `max_cumulative_loss_usd`
        # defaults to 20, so it is ALWAYS requested and can never correctly
        # be absent.
        #
        # None still means one thing only -- L5_VALIDATION_GATES=0, an
        # explicit operator choice made above this try block and never
        # reachable from an error.
        emit("L5_LIVE_ALARM", {
            "kind": "validation_gates_unreadable",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
            "labels": ("REQUIRED risk configuration could not be parsed, so "
                       "every live submission now REFUSES. The $20 "
                       "cumulative halt, the cash envelope and the unmatched "
                       "cap are not evaluable, and an unevaluable limit is "
                       "never permission to trade. Cancellation and "
                       "reconciliation are unaffected.")})
        from src import l5_live_validation_gates as VG2
        return VG2.RefuseAllGates(
            "%s: %s" % (type(exc).__name__, str(exc)[:120]))


def halt_gate():
    """The independent halt, or None when no endpoint is configured."""
    url = str(os.getenv(HALT_URL_ENV, "")).strip()
    if not url:
        return None
    try:
        from src import l5_halt_gate as HG
        return HG.HaltGate(HG.HttpHaltStore(url))
    except Exception as exc:                                   # noqa: BLE001
        emit("L5_LIVE_ALARM", {
            "kind": "halt_gate_unavailable",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
            "labels": ("L5_HALT_URL is set but the gate could not be built, "
                       "so the independent halt is ABSENT")})
        return None


def repair_ledger_from_venue(store) -> Optional[Dict[str, Any]]:
    """Book a leg the automatic path could not, from named venue evidence.

    Idempotent by id. It refuses unless the ledger still holds exactly the
    broken state the entry describes, because a repair applied to a book that
    has moved on is not a repair, it is a second corruption. It writes
    quantity and cost together through reconstruct_cost_basis, which requires
    a source and records it, and it never touches realized P&L -- the leg is
    left for the ordinary settlement path to retire, so the loss is posted by
    the same code that posts every other one.
    """
    done = store.state.setdefault("ledger_repairs_applied", [])
    out = []
    for r in LEDGER_REPAIRS:
        if r["id"] in done:
            continue
        legs = store.leg_cost(r["market"])
        pos = D(str((store.state.get("positions") or {}).get(
            "%s|%s" % (r["market"], r["side"]), 0)))
        cost = legs.get(r["side"], D(0))
        if pos != r["expect_qty"] or cost != r["expect_cost"]:
            emit("L5_LIVE_REPAIR_REFUSED", {
                "id": r["id"], "market": r["market"], "side": r["side"],
                "ledger_qty": str(pos), "ledger_cost": str(cost),
                "expected_qty": str(r["expect_qty"]),
                "expected_cost": str(r["expect_cost"]),
                "why": ("the ledger does not hold the state this repair was "
                        "written for; repairing blind is manufacturing P&L")})
            continue
        res = store.reconstruct_cost_basis(
            r["market"], r["side"], qty=r["qty"], cost=r["cost"],
            source=json.dumps(r["evidence"], sort_keys=True))
        done.append(r["id"])
        store.save()
        rec = {"id": r["id"], "market": r["market"], "side": r["side"],
               "replaced": {"qty": str(pos), "cost": str(cost)},
               "booked": {"qty": str(r["qty"]), "cost": str(r["cost"])},
               "evidence": r["evidence"], "result": res,
               "note": ("realized is NOT touched here; the ordinary "
                        "settlement sweep retires this market and posts the "
                        "loss through the same path as every other market")}
        emit("L5_LIVE_LEDGER_REPAIRED", rec)
        out.append(rec)
    return {"repairs": out} if out else None


def repair_orphaned_keys(store) -> Optional[Dict[str, Any]]:
    """Retire known corrupt ledger keys against authoritative evidence, once.

    The identity guard stops new corruption; it cannot clean up what is
    already on the volume, and on 2026-09-27 that left every reconcile
    dying on `leg_cost("")` -- correctly refusing, and wedged.
    """
    done = {r.get("id") for r in (store.state.get("orphan_repairs") or [])}
    for spec in ORPHAN_REPAIRS:
        if spec["id"] in done:
            continue
        if spec["key"] not in store.corrupt_market_keys() and not any(
                str(k).rsplit("|", 1)[0] == spec["key"]
                for k in (store.state.get("leg_cost") or {})):
            continue
        held = sum(D(str(v)) for k, v in
                   (store.state.get("leg_cost") or {}).items()
                   if str(k).rsplit("|", 1)[0] == spec["key"])
        if held != spec["matches_cost"]:
            emit("L5_LIVE_ORPHAN_REPAIR_REFUSED",
                 {"id": spec["id"], "expected": str(spec["matches_cost"]),
                  "found": str(held),
                  "why": ("the ledger does not hold what the evidence "
                          "describes; refusing to repair it blind")})
            continue
        out = store.repair_orphaned_market_key(
            key=spec["key"], to_market=spec["to_market"], qty=spec["qty"],
            cost=spec["cost"], side=spec["side"],
            settlement_pnl=spec["settlement_pnl"], evidence=spec["evidence"])
        log = store.state.setdefault("orphan_repairs", [])
        log[-1]["id"] = spec["id"]
        store.save()
        emit("L5_LIVE_ORPHAN_REPAIRED", dict(out, repair_id=spec["id"]))
        return out
    return None


def rebase_contaminated_accounting(store) -> Optional[Dict[str, Any]]:
    """Retire a known-contaminated realized total, once per contamination.

    Matches on the stored total, so a store that never carried the defect is
    untouched and a store already rebased past it cannot be rebased again.
    """
    done = {r.get("id") for r in (store.state.get("accounting_rebase_log")
                                  or [])}
    for spec in ACCOUNTING_REBASES:
        if spec["id"] in done:
            continue
        if store.realized_today() != spec["matches_realized_total"]:
            continue
        # the KEYS first, so a book written under a raw venue side word is
        # readable before anything decides what to do with it
        keys = store.canonicalize_position_keys()
        if keys["moved"] or keys["unmapped"]:
            emit("L5_LIVE_KEYS_CANONICALIZED", keys)
        out = store.rebase_session_accounting(
            attributed=spec["attributed"], cash_delta=spec["cash_delta"],
            anchor=spec["anchor"], reason=spec["reason"])
        flat = spec.get("flat_account")
        if flat:
            emit("L5_LIVE_FLAT_ACCOUNT_ADOPTED",
                 dict(store.adopt_flat_account(anchor=flat,
                                               reason=spec["reason"]),
                      rebase_id=spec["id"]))
        log = store.state.setdefault("accounting_rebase_log", [])
        if log:
            log[-1]["id"] = spec["id"]
            store.save()
        emit("L5_LIVE_ACCOUNTING_REBASED", dict(out, rebase_id=spec["id"]))
        return out
    return None


def assert_role() -> None:
    role = os.getenv(ROLE_ENV, "").strip()
    if role and role != SERVICE_ROLE:
        emit("SERVICE_ROLE_MISMATCH", {"expected": SERVICE_ROLE, "got": role})
        raise SystemExit(3)


def main() -> int:
    assert_role()
    root = os.getenv("L5_LIVE_ROOT", "/state/l5-live")
    poll_s = float(os.getenv("L5_LIVE_POLL_S", "3"))
    lam = D(os.getenv("L5_LIVE_LAMBDA", str(MANIFEST_LAMBDA)))
    book = AB.AuthBook().start()
    runner = Runner(root=root, book=book, lam=lam)
    stop = {"now": False}

    def _sig(*_a):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    # AUTHORIZATION-LEDGER DURABILITY, REPORTED AT BOOT AND NOT BEFORE NOW.
    # `authorize_arming` checks this at step 10, which is unreachable while
    # either trading lock is engaged -- SHADOW_ONLY_LOCK is step 1 and owner
    # authorization is step 2 -- so the one fact that decides whether a claim
    # could survive a restart was unobservable from outside the container
    # without lifting a lock. It is observable here, at startup, with both
    # locks on.
    #
    # THE MOUNT, NOT THE PATH, AND NEVER THE CONTENTS. What matters is
    # whether the ledger sits on a mounted volume; the directory name is
    # reported because it is needed to act on a failure, and nothing inside
    # the ledger -- no session id, no claim, no authorization value -- is
    # read or emitted. This is a diagnostic: it decides nothing and gates
    # nothing, and `durability_refusal` still refuses at step 10 regardless.
    try:
        _auth_path = AUTH.ledger_path()
        _auth_mount = AUTH.nearest_mount(_auth_path)
        _auth_durable = AUTH.durability_refusal(_auth_path) is None
        _auth_dir = os.path.dirname(_auth_path) or "/"
    except BaseException as exc:                               # noqa: BLE001
        _auth_path = _auth_mount = _auth_dir = None
        _auth_durable = None
        emit("L5_LIVE_ALARM", {"kind": "auth_ledger_unreadable",
                               "error": type(exc).__name__})
    emit("L5_LIVE_START", {"utc": now_utc().isoformat(), "root": root,
                           "credentials_present": Q1.has_credentials(),
                           "owner_arm_token": Q1.armed(),
                           "auth_ledger": {
                               "dir": _auth_dir,
                               "nearest_mount": _auth_mount,
                               "durable": _auth_durable,
                               "env": AUTH.LEDGER_ENV,
                               "labels": ("durable means the claim lock can "
                                          "survive a restart. The ledger's "
                                          "CONTENTS are never read or "
                                          "emitted. Diagnostic only: "
                                          "durability_refusal still decides "
                                          "at authorize_arming step 10.")},
                           # WHETHER THE HOLDER CAN BE IDENTIFIED AT ALL.
                           # PRESENCE ONLY, never the values: an executor
                           # that cannot name its deployment or read its
                           # boot id refuses to arm
                           # (DEPLOYMENT_IDENTITY_UNKNOWN), and that must be
                           # visible from the logs before a pilot rather
                           # than discovered at the arming attempt.
                           "holder_identity": {
                               k: bool(v) for k, v in
                               sorted(AUTH.holder_identity().items())},
                           # THE OWNER'S ABSOLUTE DEADLINE, as a STATUS and
                           # never as a value. authorize_arming evaluates it
                           # at step 8, which the shadow lock makes
                           # unreachable, so whether the authorization window
                           # is still open was not observable from outside
                           # the container. Same blind spot, same remedy, as
                           # the auth_ledger line above.
                           "auth_not_after": AUTH.not_after_status(),
                           "close_margin_s": AUTH.close_margin_s(),
                           "lambda": str(lam), "limits": runner.limits.as_dict(),
                           "worst_case_usd": str(runner.limits.worst_case_loss_usd()),
                           "frozen_rule": "030d8bf"})
    # THE BUDGET ENFORCEMENT, EXERCISED IN THIS CONTAINER, ONCE AT STARTUP.
    #
    # The heartbeat REPORTS the three class budgets, the backstop and the
    # completion reserve. Reporting is not enforcing: while the shadow lock
    # is on, the arming boundary refuses at SHADOW_ONLY_LOCK and `handle`
    # never reaches a claim, so nothing in production had ever executed the
    # enforcement path. This runs the real functions here, under a
    # throwaway experiment id in a directory it then deletes, so a failure
    # is visible BEFORE a live authorization rather than discovered by an
    # order that should have been refused.
    #
    # Emitted as its own line because the heartbeat already exceeds the log
    # transport's per-line limit and its tail is truncated in production.
    # EVERY CONFIG-LEVEL ARMING BLOCKER, ONCE AT STARTUP, BEFORE A WINDOW.
    #
    # authorize_arming raises on the FIRST refusal, so preparing a campaign
    # meant discovering them one market window and one redeploy at a time --
    # on 2026-10-23 that cost three updates and four unused approved
    # windows. This reports all of them together, from the same accessors
    # that enforce, reserving nothing and emitting no configured value.
    #
    # Its own line because the heartbeat already exceeds the log transport's
    # per-line limit and its tail is truncated in production.
    try:
        emit("L5_AUTHORIZATION_PREFLIGHT",
             AUTH.authorization_preflight(
                 str(getattr(getattr(runner, "transport", None),
                             "market_slug", "") or ""), root=root))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_LIVE_ALARM", {
            "kind": "authorization_preflight_unavailable",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
            "labels": ("diagnostic only -- authorize_arming still decides "
                       "and still refuses independently")})

    try:
        emit("L5_BUDGET_SELFCHECK", runner.budget_selfcheck())
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_BUDGET_SELFCHECK", {"ran": False, "all_passed": False,
                                     "error": type(_exc).__name__})
    # THE WATCHDOG'S MANDATE DOCUMENTS, FETCHED OVER THE REAL ENDPOINTS.
    #
    # READ-ONLY, AND IT AUTHORIZES NOTHING. It exists because those documents
    # are served on private networking and the watchdog has no public domain
    # -- correctly, since one would expose the halt latch -- so the executor
    # is the only client that can reach them at all. Without this, "the
    # endpoints work" could only be inferred from the watchdog's own log.
    #
    # It uses the SAME GET-only clients the arming gate uses, so a success
    # here means the gate's own reads would succeed: the documents exist,
    # parse, carry a usable asof_utc and are inside the staleness bound. A
    # failure names the refusal the gate would have raised.
    try:
        emit("L5_MANDATE_STORE_PROBE", mandate_store_probe())
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_MANDATE_STORE_PROBE",
             {"ran": False, "error": type(_exc).__name__,
              "detail": T.redact("%s" % _exc)[:200]})
    # THE EFFECTIVE RUNTIME CONFIGURATION, once, at boot.
    #
    # `l5_runtime_config_verify` was written to be run INSIDE the deployed
    # container -- "the only configuration that matters is the one the
    # running process actually has" -- and then nothing ever ran it there.
    # So the effective limits could only be established by reading
    # variables, which is both the wrong answer (a name can hold a value the
    # accessor clamps, floors or ignores, and several approved figures are
    # code constants with no variable at all) and the one answer that cannot
    # be obtained without handling secrets.
    #
    # It reads configuration and returns a verdict. It arms nothing, submits
    # nothing and touches no venue, and it is secret-aware by construction:
    # values are echoed only for allowlisted names and one row exists purely
    # to assert no private key is present in the runtime at all.
    # THE DEPLOYED SIDE-SELECTION LOGIC, asked of the running process.
    #
    # The wrong-side fix is inert while flat by design, and a shadow-locked
    # executor can never hold a residual (shadow blocks submission, so no fill,
    # so no inventory). No shadow window can therefore distinguish the fixed
    # code from the old code, which made "observe one correct shadow residual
    # decision" unsatisfiable. This answers it the same way the config verifier
    # answers configuration: by exercising the deployed methods here.
    #
    # It submits nothing and lifts nothing: the stub it runs against holds only
    # a two-reader store, and an unreadable case FAILS rather than passing.
    try:
        emit("L5_RESIDUAL_SIDE_SELFCHECK", LX.residual_side_selfcheck())
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_RESIDUAL_SIDE_SELFCHECK",
             {"label": "L5_RESIDUAL_SIDE_SELFCHECK", "PASS": False,
              "ran": False, "error": type(_exc).__name__,
              "detail": T.redact("%s" % _exc)[:200]})
    try:
        emit("L5_EFFECTIVE_RUNTIME_CONFIG", CFGV.verify())
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        # A CONFIGURATION WE CANNOT ESTABLISH IS A FAILED CHECK, never a
        # silent pass -- the same rule the module applies to its own rows.
        emit("L5_EFFECTIVE_RUNTIME_CONFIG",
             {"label": "L5_EFFECTIVE_RUNTIME_CONFIG", "PASS": False,
              "ran": False, "error": type(_exc).__name__,
              "detail": T.redact("%s" % _exc)[:200]})
    # THE FILL-HORIZON PROBE'S UNARMED PREFLIGHT, asked of the running process.
    #
    # Same reason as the configuration verifier above: the only arming state
    # that matters is the one this process actually has, and the variables that
    # decide it cannot be read from outside without handling secrets. So the
    # process reports its own verdict, here, at boot.
    #
    # It is READ-ONLY and ARMS NOTHING. `preflight()` reads three environment
    # flags, recomputes the probe's own worst-case bound from module constants,
    # and returns why it is not armed. It holds no venue handle and cannot
    # reach an order path; `main()` in that module deliberately never calls
    # `run_one_window`. No secret is echoed -- the flags it reports are an arm
    # flag and an authorization label, not credentials.
    #
    # Expect "armed": false until an owner installs the matching flag. The
    # authorization identifier is a constant in this build precisely so that
    # neither half of the control can be supplied by the same party.
    try:
        emit("PMUS_FILL_HORIZON_PREFLIGHT", FHP.preflight())
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        # An arming state we cannot establish is a FAILED check, never a
        # silent pass -- the same rule every other boot row here follows.
        emit("PMUS_FILL_HORIZON_PREFLIGHT",
             {"label": "PMUS_FILL_HORIZON_PREFLIGHT", "ran": False,
              "armed": False, "error": type(_exc).__name__,
              "detail": T.redact("%s" % _exc)[:200]})
    # THE BOUNDED CANCELLATION-MECHANISM MODE, chosen once and announced.
    # It REPLACES the trading tick: see Runner.mechanism_tick for why the
    # strategy and our own two cancellers must be out of the way for the
    # measurement to be of the watchdog rather than of ourselves. It lifts no
    # lock -- an unarmed transport simply refuses at `transport_armed`.
    mech = None
    if MECH.enabled():
        mech = MECH.Mechanism(
            store=runner.store, transport=None, book=book,
            market=AB.slug_for(AB.window_start()),
            emit=emit, window_start=AB.slug_start,
            window_s=float(AB.WINDOW_S),
            # THE PLACEMENT BAND'S ONLY CLOCK. The same venue-confirmed
            # bounds the arming boundary already refuses without
            # (WINDOW_BOUNDS_UNCONFIRMED), reused rather than re-read: one
            # confirmation per window, cached, and never confirmed by
            # retrying. Passing the bound method rather than its result is
            # what lets the mechanism re-evaluate the clock on every pass.
            window_bounds=runner.venue_window_bounds,
            session_id=os.getenv("PMUS_Q1_SESSION_ID", "").strip() or None,
            # the latch as a READ-ONLY FACT. Waiting for arming means waiting
            # with the shadow lock lifted, so the mechanism must be able to
            # see a halt engage mid-wait and stop. It is handed the predicate
            # rather than the gate: it can refuse on a halt and can neither
            # assert nor clear one.
            halted_fn=runner.halted)
        emit("L5_MECH_MODE", {
            "mode": MECH.MODE_ENV, "enabled": True,
            "market": mech.market,
            "already_ran": mech.already_ran(),
            "marker": mech.marker(),
            # ANNOUNCED AT BOOT, before anything is armed, so a spent
            # authorization is visible in the log that starts the run rather
            # than in the refusal that ends it.
            "submission_claim": mech.submission_claim_state(),
            "placement_band": mech.placement_band(),
            "max_total_risk_usd": str(MECH.MAX_TOTAL_RISK_USD),
            "self_cancel_after_s": MECH.SELF_CANCEL_AFTER_S,
            "labels": ("ONE post-only order at the venue minimum, left to "
                       "rest so the INDEPENDENT watchdog cancels it. The "
                       "trading tick is REPLACED, not supplemented. This "
                       "mode lifts no lock and clears no halt.")})
    last_hb = 0.0
    while not stop["now"]:
        now = time.time()
        try:
            if mech is not None:
                # the transport is built by preflight inside the tick, so it
                # is handed over on every pass rather than captured once
                mech.transport = runner.transport
                # THE MARKET FOLLOWS THE TRANSPORT UNTIL THE ORDER IS SENT,
                # and is frozen the instant it is. Re-reading the slug after
                # placing would point the re-read at a market the order is
                # not in; before placing, the window may legitimately roll.
                _slug = getattr(runner.transport, "market_slug", None)
                if (_slug and mech.state == MECH.Mechanism.INIT
                        and mech.market != _slug):
                    mech.market = _slug
                runner.mechanism_tick(now, mech)
                if mech.finished and not mech.report.get("announced"):
                    mech.report["announced"] = True
                    emit("L5_MECH_FINAL", dict(mech.report))
            else:
                runner.tick(now)
        except Exception as exc:                               # noqa: BLE001
            runner.stats["tick_errors"] += 1
            emit("L5_LIVE_ERROR", {"error": T.redact("%s: %s" % (
                type(exc).__name__, exc))})
        if runner.transport is not None:
            # read-only, and self-throttled by CASH_REFRESH_S. On the main
            # loop because reconcile_if_needed returns early on a confirmed
            # book: without this, a reading taken once at startup would be
            # published for the whole run as if it were current.
            try:
                runner.refresh_available_cash(runner.transport, now)
            except Exception as exc:                           # noqa: BLE001
                runner.stats["cash_refresh_errors"] += 1
                emit("L5_LIVE_ERROR", {"error": T.redact("%s: %s" % (
                    type(exc).__name__, exc))})
        if now - last_hb >= HB_EVERY_S:
            runner.heartbeat()
            last_hb = now
        time.sleep(poll_s)
    if runner.transport is not None and runner.transport.armed:
        emit("L5_LIVE_SHUTDOWN", LX.deadman(runner.store, runner.transport,
                                            reason="shutdown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
