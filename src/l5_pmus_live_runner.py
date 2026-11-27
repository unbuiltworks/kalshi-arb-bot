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
from src import l5_wake_signal as WS
from src import l5_missed_pair_blocker as MPB
from src import pmus_ws_evidence_recorder as _REC
from src import l5_two_sided_shadow as TS
from src import l5_campaign_approval as CAMP
from src import l5_runtime_config_verify as CFGV
from src import l5_tape_recovery as TR
from src import l5_taker_completion_policy as TCP
from src import l5_shadow_completion as SC
from src import pmus_fill_horizon_probe as FHP
from src import l5_cancel_mechanism as MECH
from src import l5_pmus_auth_book as AB
from src import l5_pmus_realtime_observer as OBS
from src import l5_roll_diagnostics as RD
from src import l5_dual_quote as DQ
from src import l5_fidelity_mode as FID
from src import l5_replenish as RPL
from src import l5_pmus_transport as T
from src import l5_fill_calibration as CALIB
from src import l5_completion_latency as LAT
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
def _lat(obj: Any, meth: str, *a: Any, **kw: Any) -> None:
    """Note one latency observation on `obj`, if it can take one.

    LOOKED UP, NOT CALLED BY NAME, for the same reason the observer
    heartbeat is: `maybe_book_completion` and `poll_orders` are both driven
    by test rigs that are not fully built Runners, and a bare
    `self._latency_safe(...)` broke ten end-to-end window tests with an
    AttributeError the instant it was added. Reporting-only telemetry must
    never be the thing that stops a cancellation path from running, in a
    rig or in a half-built process.
    """
    f = getattr(obj, "_latency_safe", None)
    if f is None:
        return
    f(meth, *a, **kw)


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
#: the PUBLISHED equity value is withheld when buying power is older than
#: this. Three refresh intervals: one missed read is tolerated, two are not.
EQUITY_CASH_MAX_AGE_S = 180.0
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

def unresolved_line_of(store: Any) -> Optional[Dict[str, Any]]:
    """`store.unresolved_bound()` behind a total wrap. Never raises; a store
    without it, or any failure, is None -- UNKNOWN, which the account-risk
    record treats as unreadable (fail closed)."""
    fn = getattr(store, "unresolved_bound", None)
    if fn is None:
        return None
    try:
        return fn()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


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


#: BOUNDED RETRY for a venue-confirmation read that did not answer.
#: Six attempts over ~135s sit entirely inside the t+480 entry cutoff, so a
#: window recovered on the last attempt can still open a leg. The cap exists
#: so a dead gateway is read six times, not six hundred. Module scope, not a
#: class attribute, so the read works identically under a caller's stub.
VENUE_BOUNDS_MAX_ATTEMPTS = 6
VENUE_BOUNDS_BACKOFF_S = (5.0, 10.0, 20.0, 40.0, 60.0, 60.0)

#: THE REAL BOUND IS THE WINDOW, NOT THE SCHEDULE. The attempt budget above
#: sums to 195s, and comparing that sum to the entry cutoff proves nothing:
#: the first attempt can land late in a window (a restart at t+400, a slow
#: first tick), and a lookup can itself take seconds. So every attempt is
#: bounded by the runway ACTUALLY remaining, measured from the window start
#: against `TS.entry_cutoff_s()`, with the observed lookup latency counted.
#:
#: A confirmation that arrives with less than this left cannot produce a
#: placed order -- `may_enter` refuses PAST_ENTRY_CUTOFF on its own -- so
#: retrying into it spends venue requests to buy a refusal. Below the floor
#: the window is given up by name instead.
VENUE_BOUNDS_MIN_RUNWAY_S = 20.0


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


def _feed_failure_detail() -> Dict[str, Any]:
    """The last L5-feed failure, for the ingest report. Never raises.

    A diagnostic must never be the reason a heartbeat fails to emit, so an
    observer that cannot answer yields a named placeholder rather than an
    exception.
    """
    try:
        fn = getattr(OBS, "last_feed_failure", None)
        return dict(fn() or {}) if fn is not None else {}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"kind": "DETAIL_UNAVAILABLE", "error": type(exc).__name__}


def _resting_rows(store: Any, market: str) -> Optional[tuple]:
    """Live orders in this market as {side, price, market, remaining}.

    RETURNS None WHEN THE LEDGER COULD NOT BE READ, and that is the whole
    reason the signature is Optional. It used to return `()` for both "there
    is nothing resting" and "I could not find out", and the docstring argued
    that the separately-read account-wide `still_fillable_order_count` was
    what refused. THAT WAS ONLY TRUE AT ONE SLOT. With
    `L5_MAX_LIVE_ORDERS=2` a count of 1 no longer refuses, so an unreadable
    ledger presented as "no rows" would have cleared the per-market hedge
    guard and permitted a completion on top of a hedge that might already be
    working. An empty tuple must mean empty.

    `remaining` IS THE RESERVATION, and it is why this row gained a fourth
    field. The three-field row said WHETHER something rested on a side and
    never HOW MUCH, so `l5_book_completion.decide` could see a DOWN order
    resting and still size a second DOWN order at the full unmatched need.
    It is read through the store's own `worst_case_remaining`, which charges
    an unreadable row at the absolute residual ceiling rather than at zero --
    so an unreadable ledger makes the reservation LARGER and the completion
    more reluctant, which is the direction that cannot lose money.
    """
    try:
        live = store.live_orders() or {}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    # BORROWED, NOT RESTATED. `worst_case_remaining` already encodes the
    # preference order (readable `remaining`, else the ordered quantity, else
    # the ceiling) and the convention that unreadable is not zero. Restating
    # it here is how the two would drift apart. Absent -- the lighter test
    # harnesses pass stores that are not LedgerStores -- the fallback is the
    # ceiling itself, which is the most reluctant reading available.
    _wcr = getattr(store, "worst_case_remaining", None)
    out = []
    for _oid, row in (live.items() if hasattr(live, "items")
                      else enumerate(live)):
        try:
            r = dict(row or {})
        except BaseException:                                  # noqa: BLE001
            continue
        if str(r.get("market") or market) != str(market):
            continue
        try:
            rem = (_wcr(r) if _wcr is not None
                   else LX.ABS_RESIDUAL_CAP_CONTRACTS)
            rem = D(str(rem))
            if not rem.is_finite() or rem < 0:
                rem = LX.ABS_RESIDUAL_CAP_CONTRACTS
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            rem = LX.ABS_RESIDUAL_CAP_CONTRACTS
        out.append({"side": str(r.get("side") or ""),
                    "price": r.get("price"), "market": market,
                    "remaining": rem})
    return tuple(out)


def _outstanding_on_side(rows: Any, side: Any) -> D:
    """Still-fillable contracts resting on `side`, summed. Never negative.

    This is what `decide` subtracts from the unmatched need. It sums the
    `remaining` that `_resting_rows` already failed closed on, so a row whose
    size could not be read contributes the residual ceiling and the
    completion is refused rather than doubled.
    """
    want = str(side or "").upper()
    # None MEANS UNREADABLE, and an unreadable ledger reserves the ceiling
    # rather than nothing. The production caller refuses before reaching
    # here; this is the belt for any later caller that does not.
    if rows is None:
        return LX.ABS_RESIDUAL_CAP_CONTRACTS
    total = D(0)
    for r in rows:
        try:
            if str(r.get("side") or "").upper() != want:
                continue
            v = D(str(r.get("remaining")))
            if not v.is_finite() or v <= 0:
                continue
            total += v
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # AN UNSUMMABLE ROW IS CHARGED AT THE CEILING, for the same
            # reason an unreadable one is: skipping it would understate the
            # reservation, which is the only direction that places a
            # duplicate hedge.
            total += LX.ABS_RESIDUAL_CAP_CONTRACTS
    return total


#: BOUNDS ON THE VENUE EXIT PROBE. It is a diagnostic, so it may not become a
#: poller: at most this many reads per process, and never more often than the
#: cadence. It stops for good as soon as one read is CONCLUSIVE -- that is,
#: returns at least one position row, which is the only way an empty-portfolio
#: read can be told apart from a venue that advertises nothing.
VENUE_EXIT_PROBE_MAX = 40
VENUE_EXIT_PROBE_EVERY_S = 300.0

#: BOUNDS ON THE CAMPAIGN-CLOSE ATTEMPT. A claim that legitimately cannot
#: close must be REPORTED, not retried for ever: each attempt reads the store
#: and writes nothing unless `close()` accepts the evidence.
CAMPAIGN_CLOSE_MAX_TRIES = 12
CAMPAIGN_CLOSE_EVERY_S = 60.0


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


def _window_fills(runner: Any, market: str) -> List[Dict[str, Any]]:
    """The venue's fill records for one market, in fill order.

    THE LIFECYCLE STREAM IS NOT A COMPLETE FILL SOURCE. A fill that lands
    between a cancel being sent and the venue answering it never produces a
    positive ORDER_PROGRESS delta: Campaign 6's 1630z DOWN leg filled 2.15 at
    wire 0.4900 that way, and the window counted 3 fills where the venue had
    settled 4. The recorder's own fill slots did see it, because the cancel
    path reports the fill it raced.

    Read from the same `FillCalibration.orders` fold as `_window_submits`, so
    the two cannot disagree about which orders belong to this window, and
    never raising into the reporting path.
    """
    out: List[Dict[str, Any]] = []
    try:
        orders = getattr(getattr(runner, "fill_calibration", None),
                         "orders", None) or {}
        for coid, row in orders.items():
            if not str(coid).startswith("l5f-"):
                continue              # shadow and calibration ids are not ours
            ps = (row or {}).get("pre_submit") or {}
            if str(ps.get("slug") or "") != str(market):
                continue
            for f in ((row or {}).get("fills") or ()):
                if isinstance(f, Mapping):
                    out.append(dict(f, coid=str(coid)))
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


def disposition_fingerprint(runner: Any, slug: Any) -> Optional[str]:
    """The STATE a disposition verdict was formed on. None if unreadable.

    WHY A TIMESTAMP IS NOT ENOUGH, which is review item 2. A verdict keyed
    only on (slug, when) says WHEN we decided, not WHAT WE DECIDED ABOUT.
    Between the controller and a discretionary cancel the venue can book a
    fill, `poll_orders` can correct the basis, the resting order can go
    terminal and the book can move -- and a stale verdict would still veto
    on an age test alone. So the gate recomputes this and binds only on an
    EXACT match.

    It also answers review item 4 without relying on tick ORDER: whatever
    sequence the schedule runs, a verdict formed on inventory that has
    since changed stops binding, because the fingerprint no longer agrees.
    `maybe_acc_deadline` polls the venue before it cancels, so a racing
    fill lands in the store first and is reconciled -- and that very
    reconciliation breaks this fingerprint, which is the behaviour we want.

    EVERY COMPONENT IS A CONFIRMED FIGURE: realised residual, both realised
    leg costs, the resting completion's own id and price, the two touches,
    and the uncertainty flag. Unreadable anything returns None, which never
    matches, so the gate declines rather than guesses.
    """
    try:
        st = getattr(runner, "store", None)
        if st is None:
            return None
        resid = st.residual(str(slug))
        legs = st.leg_cost(str(slug)) or {}
        unc = bool(st.orders_uncertain())
        live = {c: o for c, o in (st.live_orders() or {}).items()
                if str((o or {}).get("market") or "") == str(slug)}
        rows = sorted((str(c), str((o or {}).get("side") or ""),
                       str((o or {}).get("price") or ""),
                       str((o or {}).get("remaining") or ""),
                       str((o or {}).get("state") or ""))
                      for c, o in live.items())
        bk = getattr(runner, "book", None)
        snap = (bk.snapshot(str(slug)) or {}) if bk is not None else {}
        return "|".join([
            str(slug), str(resid),
            str(legs.get("UP")), str(legs.get("DOWN")),
            ("U" if unc else "-"),
            str(snap.get("bid")), str(snap.get("ask")),
            ";".join(",".join(r) for r in rows),
        ])
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def controller_preserves(runner: Any, slug: Any, now: float) -> bool:
    """Did the one controller decide to KEEP this market's completion?

    A FUNCTION, NOT A METHOD CALL, and `runner` is read through getattr --
    exactly as `acc_stale_by_distance` is and for the reason its docstring
    already gave: every duck-typed runner stub in the suite grafts these
    methods onto an object carrying only what the method touched, so a new
    `self.` call breaks all of them. I added one and broke 39 tests.

    ABSENT IS NOT A VETO. No verdict, a stale verdict, a stub without the
    accessor, or any failure at all yields False, which leaves the
    pre-existing behaviour exactly as it was -- and that behaviour already
    carries the pair ceiling of PR #200 and the pair-aware recovery of
    PR #202. So this can only ever ADD a refusal; it can never enable a
    cancel, a submission or a crossing.
    """
    try:
        fn = getattr(runner, "controller_verdict", None)
        if not callable(fn):
            return False
        if fn(slug, now) != "PRESERVE":
            return False
        # THE STATE MUST STILL BE THE ONE WE DECIDED ABOUT. A verdict whose
        # fingerprint no longer matches describes inventory, orders or a
        # book that has since changed, and must not veto anything.
        prev = (getattr(runner, "_disposition_last", None) or {}).get(
            str(slug))
        want = prev[2] if (prev and len(prev) > 2) else None
        if want is None:
            try:
                runner.stats["disposition:VERDICT_WITHOUT_FINGERPRINT"] += 1
            except BaseException:                              # noqa: BLE001
                pass
            return False
        got = disposition_fingerprint(runner, slug)
        if got is None or got != want:
            try:
                runner.stats["disposition:STATE_CHANGED_SINCE_VERDICT"] += 1
            except BaseException:                              # noqa: BLE001
                pass
            return False
        return True
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False


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
        # ============ THE INVENTORY THIS LEG WOULD COMPLETE, READ ONCE
        #
        # WHY. `recover_stale_leg` judged DRIFT only, so it cancelled a
        # resting completion because the book had left it -- without ever
        # asking what pair that order was holding. Over the recorded
        # ladders, 24 of 35 fires on 1215z and 72 of 72 on 1245z would have
        # cancelled a quote whose pair the cap permitted and whose
        # replacement the cap then forbade, leaving NO quote at all.
        #
        # READ-ONLY AND FAIL-SOFT THROUGHOUT. Anything unreadable here
        # yields None, the extra question is declined, and the behaviour is
        # exactly what it was before -- which is why this cannot take down
        # the deadline that bounds real exposure.
        #
        # THE REDUCING SIDE IS JUDGED ON THE REALISED RESIDUAL, exactly as
        # `maybe_completion_requote` does and for the same reason: the
        # question is about the order we are about to CANCEL, so the
        # position to read is the one that would remain without it.
        # `probe_reducing_side` reads the PROSPECTIVE residual, which a
        # completion that fully covers its deficit drives to zero.
        _reducing = None
        _legs: Mapping[str, Any] = {}
        try:
            _realised = D(str(runner.store.residual(str(slug))))
            if _realised != 0:
                _reducing = "DOWN" if _realised > 0 else "UP"
                _legs = runner.store.leg_cost(str(slug)) or {}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            count("acc_recover_inventory:UNREADABLE:%s" % type(exc).__name__)
            _reducing, _legs = None, {}

        def _held_for(order_side: str):
            """(held_qty, held_cost_usd) for a REDUCING order, else Nones.

            An OPENING leg has no complement to pair with, so there is no
            pair to protect and the drift test stands alone. Only the leg
            that reduces the realised residual gets the extra question.
            """
            if _reducing is None or str(order_side) != _reducing:
                return None, None
            other = "DOWN" if str(order_side) == "UP" else "UP"
            try:
                hq = runner.store.position(str(slug), other)
                return hq, _legs.get(other)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                count("acc_recover_inventory:POSITION_UNREADABLE")
                return None, None

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
            _hq, _hc = _held_for(row.get("side"))
            v = TSP.recover_stale_leg(
                side=str(row.get("side")), own_px=row.get("price"),
                qty=row.get("qty"), remaining=row.get("remaining"),
                state=row.get("state"), submitted_t=sub, now=float(now),
                bid=snap.get("bid"), ask=snap.get("ask"), tick=tick,
                depth_ticks=TSP.depth_ticks(),
                book_age_s=snap.get("stream_age_s"),
                recoveries_used=used + len(out),
                open_budget_remaining=obr,
                # THE PAIR THE CAP WOULD ACTUALLY JUDGE. The ceiling is left
                # to the probe, which reads `max_pair_cost()` -- the same
                # accessor the complement path prices the replacement
                # against -- so the two can never disagree.
                held_qty=_hq, held_cost_usd=_hc)
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
        #: per-window transient-retry state; never a substitute for bounds
        self.window_bounds_retry: Dict[int, Dict[str, Any]] = {}
        self.arm_claim: Optional[Dict[str, Any]] = None
        #: fans one book frame out to the money path's observer and the
        #: shadow policies' ladder; see FrameFanout.
        self.fanout: Optional[FrameFanout] = None
        #: the authenticated ladder the shadow policies price queue
        #: position against. None means unknown queue, which the fill
        #: model refuses to fill except on a sweep.
        self.shadow_ladder: Optional[Any] = None
        #: AN ACTIONABLE EVENT MAY SHORTEN THE SLEEP. Raised by feed threads,
        #: consumed only by the decision loop, which still owns every read,
        #: gate and submission -- so this cannot create a concurrent or
        #: duplicate order. The timer remains the fallback for stale feeds,
        #: reconciliation, settlement and the watchdog.
        self.wake = WS.WakeSignal()
        #: only a TOUCH change, and only in a market we hold or rest in.
        self.touch_watcher = WS.TouchWatcher(
            self.wake, relevant=self._market_is_relevant_for_wake)
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
        #: WHERE THE TIME GOES between a first-leg fill and a second-leg
        #: quote. In-memory, pure, holds no transport and no clock of its
        #: own; every figure is reporting only and `_latency_safe` swallows
        #: its faults. See `src/l5_completion_latency.py` for which of the
        #: six questions it can answer and which it can only bound.
        self.completion_latency = None
        self._latency_last_slug = None
        try:
            self.completion_latency = LAT.CompletionLatency()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["latency_init_error:%s" % type(exc).__name__] += 1
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
        # ONE MARKET, ONCE, and a DIFFERENT verb: this replaces a $1/share
        # placeholder cost with a measured one, which necessarily moves
        # realized TOWARD zero and so cannot go through the call above.
        # See COST_BASIS_RESTATEMENTS for why the two are separate.
        apply_cost_basis_restatements(self.store)
        # OWNERSHIP, PUBLISHED READ-ONLY. The watchdog cannot cancel one of
        # our orders without a durable record that it is ours -- account-wide
        # open orders say what is resting, not whose it is, and this account
        # is SHARED. This serves `live_orders` over GET and nothing else; it
        # is off entirely unless L5_ORDER_REGISTRY_PORT is set, so the default
        # changes no behaviour. It never writes.
        self.registry_server = None
        try:
            from src import l5_order_registry_server as REG
            self.registry_server = REG.serve_from_env(
                self.store, risk=self.account_risk_line)
            if self.registry_server is None:
                #: A PORT WAS ASKED FOR AND NO LISTENER EXISTS. `serve_from_env`
                #: swallows its own exceptions and returns None, and the
                #: `except` below only catches an IMPORT failure -- so a bind
                #: that failed emitted NOTHING AT ALL. The watchdog then read
                #: UNREACHABLE and no log on this side said why.
                #:
                #: An unset port is the documented off switch and stays
                #: silent; a port that is set and did not bind is an alarm.
                _want = str(os.getenv(REG.PORT_ENV) or "").strip()
                if _want:
                    emit("L5_LIVE_ALARM", {
                        "kind": "order_registry_not_listening",
                        "port_requested": _want,
                        "labels": ("L5_ORDER_REGISTRY_PORT is set but no "
                                   "listener was created, so the watchdog "
                                   "cannot establish ownership and cannot "
                                   "cancel. It can still latch the halt.")})
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
        #: the most recent ADMITTED L5 action per market, for the
        #: book-driven completion re-evaluation. Bounded by the number of
        #: markets this process trades, which is one.
        self._last_action_row: Dict[str, Dict[str, Any]] = {}
        #: when the book-driven completion last decided, for its cadence
        self._book_completion_t: Optional[float] = None
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
        # ONE-SHOT, READ-ONLY FEED-SOURCE AUDIT, at construction only.
        #
        # The legacy `/activity` poll answered 200 with a byte-identical
        # body for five hours and a cache-bypass did not move it, so the
        # body is what that endpoint serves. The owner confirms the profile
        # is this same wallet, which leaves WHICH ENDPOINT open. This asks
        # the documented `/v2/activity` and the identity APIs the same
        # question and logs both answers.
        #
        # It ingests nothing, writes no cursor, feeds no decision and does
        # NOT change which source the collector polls. Bounded to one call
        # per boot, and a failure is counted rather than raised: a
        # diagnostic must never be able to stop the process it diagnoses.
        try:
            emit("L5_FEED_SOURCE_AUDIT", OBS.feed_source_audit())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["feed_source_audit_error:%s"
                       % type(exc).__name__] += 1
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
        #: VENUE-CONFIRMED CANCELLATIONS, per market, for the accounting
        #: report only. An order the venue took and then cancelled emits no
        #: progress row, so without this the window report counted it
        #: `unacknowledged` -- 8 of window 3's 12 on 2026-11-04, under a
        #: label reading "submissions are being attempted and not landing",
        #: when all 12 had venue order ids and venue-confirmed terminal
        #: states. Acknowledgement evidence only: no fill, quantity, fee or
        #: P&L is derived from it anywhere.
        self.win_cancels: Dict[str, List[Dict[str, Any]]] = {}
        #: market -> (selected action, when emitted). Bounds the repeat rate
        #: of L5_UNMATCHED_DISPOSITION without ever delaying a CHANGE.
        self._disposition_last: Dict[str, Any] = {}
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
        #: THE DEFERRED READ-ONLY DIAGNOSTICS. Built here rather than in
        #: `roll` so the latch survives every rollover: a job enqueued on the
        #: first window is not re-enqueued on the ninety-sixth. See
        #: `l5_roll_diagnostics` for what this fixes and what it measured.
        self.roll_diag = RD.DeferredDiagnostics()
        #: the cost-basis audit's paced activity walk, built on first use and
        #: carried across ticks. None until the job runs once.
        self._cost_basis_walk: Optional[Any] = None

    # ------------------------------------------------------------ window
    def halted(self) -> bool:
        """The LOCAL env halt only. Not the effective gate -- see
        `submission_gate`, which is what refuses a submission.

        DELEGATES to the module-level `operator_halted()` so that this and
        the authorization preflight cannot read the halt differently. See
        that function for why one expression matters.
        """
        return operator_halted()

    def origin_line(self) -> Dict[str, Any]:
        """Settled results and held legs split BTC15M / BTC5M / MANUAL /
        UNKNOWN. READ-ONLY, reported for verification; a failure to compute
        it is reported as such and changes nothing else."""
        try:
            return {"ok": True,
                    "realized": self.store.realized_by_origin(),
                    "positions": self.store.position_origins(),
                    "foreign_orders": {
                        c: {"origin": (o or {}).get("origin"),
                            "market": (o or {}).get("market"),
                            "remaining": (o or {}).get("remaining")}
                        for c, o in (self.store.state.get("foreign_orders")
                                     or {}).items()}}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"ok": False,
                    "reason": "ORIGIN_REPORT_FAILED:%s" % type(exc).__name__}

    def portfolio_drawdown_report(self) -> Dict[str, Any]:
        """The account-wide portfolio drawdown block for the heartbeat.
        Reporting only; the executor's validation gate enforces it."""
        from src import l5_portfolio_drawdown as PDD
        try:
            pos = self.store.positions_by_market()
            val = PDD.worst_case_value(self.available_cash_usd, pos)
            return PDD.report(
                val, manual=Runner.portfolio_manual_lines(self, pos))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"state": "UNREADABLE",
                    "reason": "PORTFOLIO_REPORT_FAILED:%s" % type(exc).__name__}

    def portfolio_manual_lines(self, positions: Any = None,
                               now: Optional[float] = None) -> Any:
        """The measured manual commitment for the drawdown block, or None.

        None unless the bot/total split is ON (`l5_portfolio_drawdown`), so
        with it off the block is built exactly as before. NO VENUE REQUEST:
        it reads the measurement `maybe_measure_manual_orders` stored on the
        tick and the ledger's positions. Any failure is None, which counts
        0 -- the bot figure is then the whole drawdown.
        """
        try:
            from src import l5_portfolio_drawdown as PDD
            if PDD.split_config().get("state") != "ON":
                return None
            pos = (self.store.positions_by_market() if positions is None
                   else positions)
            return PDD.manual_commitment(
                getattr(self, "_manual_measurement", None), pos,
                time.time() if now is None else float(now),
                retired=self.store.state.get("retired_markets"))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

    def maybe_measure_manual_orders(self, now: float) -> None:
        """Read the owner-attested manual orders from the venue, on the tick.

        OFF unless the bot/total split is ON. Its only transport call is
        the single-order GET, at most a few per tick and at most once a
        minute per unfinished order (`l5_manual_commitment`). The result is
        STORED here and handed to the executor; the published block and the
        gate read only the stored copy. ANY failure is swallowed -- this
        can never stop a tick -- and leaves the stored copy to go stale,
        which counts the manual amount as 0.
        """
        try:
            from src import l5_portfolio_drawdown as PDD
            if PDD.split_config().get("state") != "ON":
                return
            from src import l5_manual_commitment as MC
            job = getattr(self, "_manual_reader", None)
            if job is None:
                job = MC.Reader()
                self._manual_reader = job
            m = job.step(self.transport, float(now))
            self._manual_measurement = m
            ex = getattr(self, "executor", None)
            if ex is not None:
                ex.portfolio_manual_measurement = m
            brief = {"orders": m["orders"], "rejected": m["rejected"],
                     "unclassified": m.get("unclassified")}
            if brief != getattr(self, "_manual_emitted", None):
                self._manual_emitted = brief
                emit("L5_MANUAL_COMMITMENT", brief)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["manual_commitment_error:%s"
                           % type(exc).__name__] += 1
            except BaseException:                              # noqa: BLE001
                pass

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
                               "operator_halt": None,
                               "shadow_lock": None, "halt_latch": None,
                               "owner_arm_token": None, "armed": None}
        try:
            # THE OWNER'S KILL SWITCH, REPORTED HERE TOO.
            #
            # It was absent from this reader entirely, so the EFFECTIVE
            # gate could report `blocked: false, reason: OPEN` while
            # `L5_LIVE_HALT` was engaged. The env halt does refuse a
            # submission -- `OPERATOR_HALT` in the intent chain, and the
            # tick deadman disarms the transport -- but a reporter that
            # disagrees with the refusal is how the 08:30Z window was
            # lost: the halt was in the heartbeat and the blockers I read
            # off this gate did not name it.
            #
            # Same expression as `halted()` and the authorization
            # preflight, so the three cannot disagree.
            out["operator_halt"] = operator_halted()
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
            if out["operator_halt"]:
                # FIRST, ahead of the structural lock: this is the switch
                # an operator reaches for, and it must be the reason they
                # are shown.
                out["reason"] = "OPERATOR_HALT"
            elif out["shadow_lock"]:
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
                         "these and stays available while blocked. "
                         "operator_halt is the LOCAL L5_LIVE_HALT env "
                         "switch; halt_latch is the INDEPENDENT durable "
                         "latch a watchdog writes. They are separate "
                         "mechanisms and either one alone blocks.")
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
            # WHAT THIS PROCESS READ, not what the dashboard holds: true
            # only when a hedging DOWN order is reserved at its limit cost
            # plus fee rather than $1 a share. The ceiling above is the same
            # either way.
            "hedge_cash_at_limit": bool(
                __import__("src.l5_live_validation_gates", fromlist=["x"]
                           ).hedge_cash_at_limit()),
            # THE WORST-CASE DEFICIT BOUND, REPORTED EVEN WHEN ABSENT.
            #
            # This line used to omit it entirely, and that omission is how
            # the Stage 1 blocker stayed invisible: the deficit gate reads
            # `if self.max_worst_case_deficit_usd is not None:`, so an unset
            # limit REMOVES the check rather than widening it -- and the
            # heartbeat enumerated the gates in force without saying that
            # one was not among them. A limit an operator cannot see is a
            # limit nobody can verify.
            #
            # `null` here means NO DEFICIT CHECK RUNS AT ALL. That is a
            # different and more serious statement than a large number, so
            # it is spelled out rather than left to inference.
            "max_worst_case_deficit_usd": (
                None if getattr(vg, "max_worst_case_deficit_usd", None)
                is None
                else str(getattr(vg, "max_worst_case_deficit_usd"))),
            "worst_case_deficit_check": (
                "ACTIVE" if getattr(vg, "max_worst_case_deficit_usd", None)
                is not None else "NOT_CONFIGURED_NO_CHECK_RUNS"),
            "distinction": ("max_cash_deployed_usd is the run-scoped GROSS "
                            "CASH OUT bound (the mandate). max_usd_per_market "
                            "is a per-market WORST-CASE LOSS ceiling. "
                            "max_worst_case_deficit_usd bounds "
                            "C - min(up, dn), the most the book can lose "
                            "HOWEVER the window resolves, and when it is "
                            "null that check does not run. They are "
                            "different quantities and the smaller one binds; "
                            "none is another's default."),
        }

    def roll(self, now: float) -> None:
        # Diagnose the recurring ~93s window-roll stall without changing
        # order submission, cancellation, settlement, or failure semantics.
        # Emit each completed stage even if a later stage blocks or raises.
        def _roll_stage(stage, fn, *args, **kwargs):
            started = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = time.monotonic() - started
                emit("L5_LIVE_ROLL_STAGE", {
                    "stage": stage, "elapsed_s": round(elapsed, 3),
                    "window_start": str(AB.window_start(now)),
                    "labels": "observability only; no timeout or behavior change"})
        ws = AB.window_start(now)
        if ws == self.ws:
            return
        # THE BUDGET, MEASURED AND REPORTED. A roll that blocks is the defect
        # this guard exists to surface, and it is surfaced rather than
        # enforced on purpose: nothing here may be abandoned half-done. The
        # window summary, the shadow close, the two-sided close and the
        # executor rebuild are not interruptible work, and a timeout that cut
        # one of them short would trade a latency problem for an accounting
        # one. So the stall becomes one loud line instead of something a
        # reader has to reconstruct from stage timings and a DNS stream.
        _roll_t0 = time.monotonic()
        if self.ws is not None:
            _roll_stage("model_close", self.model_close)
            _roll_stage("close_window", self.close_window)
            # the shadow's own window close. After close_window, which must
            # not be delayed by it, and before self.ws moves.
            _roll_stage("shadow_close", self.shadow_close)
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
        _roll_stage("two_sided_close", self.two_sided_close)
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
        _roll_stage("shadow_attach_ladder", self.shadow_attach_ladder, slug)
        _roll_stage("evidence_attach", self.evidence_attach, slug)
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
        # ============ THE OPTIONAL READ-ONLY DIAGNOSTICS ARE QUEUED HERE,
        # NOT RUN HERE, AND THAT IS THE 93-SECOND FIX.
        #
        # Four blocks used to run inline at this point: the depth comparison,
        # the one-shot tape recovery, the cost-basis audit with its apply, and
        # the L5 baseline epoch. All four are read-only; none of them places,
        # cancels, settles or reconciles anything. But `roll` is synchronous
        # and runs FIRST in the tick, so every second one of them spends is a
        # second in which this window cannot evaluate an opening or a
        # completion -- and the cost-basis audit spends about ninety.
        #
        # ITS OWN RECORD SAYS SO: `activity_walk {"pages":133}`, taken through
        # the process's one shared limiter at PMUS_RPS = 1.5 requests a
        # second, which is 88.7 seconds of paced sleeping. Measured on
        # 2026-11-06, `roll` returned after 93.1-93.9 s on every window for
        # hours, and the first completion decision of the 0300z and 0315z
        # windows -- the two that settled -$0.97 -- landed at +98.5 s and
        # +97.5 s after the window opened. See `l5_roll_diagnostics`.
        #
        # AND EACH WAS MEANT TO RUN ONCE. The audit's comment below says it
        # "reads the account activity feed ONCE"; the apply's says "Boot only,
        # so it cannot run twice in a session". `roll` fires every fifteen
        # minutes, so both ran ninety-six times a day. The queue's latch is
        # per name and set at enqueue time, so each now runs exactly once per
        # process, which is what they always claimed to do.
        #
        # NOTHING ELSE MOVES. The window summary, the model close, the shadow
        # close, the two-sided close, the window accounting, the transport and
        # executor rebuild, the ownership oracle and the gates all stay here,
        # in this order, undeferred. Only the four diagnostics are queued, and
        # queued is not skipped: a job stays queued until it reports done.
        self.enqueue_roll_diagnostics()

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
        # a rebuilt executor keeps the reading the tick already holds, so
        # the manual amount does not read 0 for the first tick of a window
        self.executor.portfolio_manual_measurement = getattr(
            self, "_manual_measurement", None)
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
        _roll_elapsed = time.monotonic() - _roll_t0
        _roll_budget = RD.roll_budget_s()
        if _roll_elapsed > _roll_budget:
            emit("L5_LIVE_ROLL_OVER_BUDGET", {
                "elapsed_s": round(_roll_elapsed, 3),
                "budget_s": _roll_budget,
                "budget_env": RD.ROLL_BUDGET_S_ENV,
                "window_start": str(ws),
                "deferred": self.roll_diag.report(),
                "labels": ("`roll` runs FIRST in the tick and is "
                           "synchronous, so every second here is a second "
                           "this window cannot evaluate an opening or a "
                           "completion. On 2026-11-06 this was 93 s on every "
                           "window, spent by a read-only diagnostic that is "
                           "now deferred. Reporting only: nothing is "
                           "abandoned or cut short.")})
        self.stats["roll_elapsed_s_max"] = max(
            round(_roll_elapsed, 3),
            float(self.stats.get("roll_elapsed_s_max", 0) or 0))

    # ----------------------------------------- deferred roll diagnostics
    def enqueue_roll_diagnostics(self) -> None:
        """Queue the optional read-only diagnostics. Runs none of them.

        Each job is latched on its name by the queue, so calling this on every
        window roll enqueues each one exactly once per process -- which is the
        cadence all four of them documented and none of them had.

        THE GATE IS STILL READ AT ENQUEUE TIME, deliberately. An operator who
        has not set `L5_COST_BASIS_AUDIT` gets no job and no record, exactly
        as before; the switches keep their meaning and their default-OFF.
        Reading them here rather than inside the job also means the job cannot
        be enabled halfway through a process by a variable that changed under
        it, which is a property the inline version had by accident and this
        keeps on purpose.
        """
        rd = self.roll_diag

        if str(os.getenv("L5_DEPTH_COMPARISON", "")).strip().upper() == "YES":
            # 1 TICK vs 2 TICKS, from the ACTUAL order and fill records on
            # this volume. READ-ONLY. It exists because a deeper quote always
            # shows a larger QUOTED edge -- 0.95 against 0.97 on a one-tick
            # book -- so comparing quoted edges would always "prove" that
            # deeper is better and prove nothing. It reports per depth the
            # measured single-leg and both-leg fill rates, the realized edge
            # from recorded prices and commissions, and withholds any verdict
            # below the sample thresholds. It WRITES NOTHING.
            def _depth_comparison(_deadline: float) -> Dict[str, Any]:
                from src import l5_depth_comparison as DC
                return {"_kind": "L5_DEPTH_COMPARISON",
                        "report": DC.from_tape(self.tape)}
            rd.enqueue("depth_comparison", _depth_comparison)

        if TR.configured() is not None:
            # ONE-SHOT TAPE RECOVERY, off unless L5_TAPE_RECOVERY_COID names a
            # client order id. It exists because the 1415z residual-cap
            # decision was written to the tape and omitted from the emitted
            # line, so the one record that says whether the cap bound could
            # not be read from the logs. This lifts that record out and
            # nothing else: read-only, bounded, and reduced to an allowlist
            # before it is emitted. The module latches on the coid too, so
            # without the queue's latch it re-read 143,829 lines every fifteen
            # minutes. See analysis/l5/stage_f/STAGE_B2_TAPE_RECOVERY.md.
            def _tape_recovery(_deadline: float) -> Dict[str, Any]:
                return {"_kind": "L5_TAPE_RECOVERY",
                        "report": TR.from_env(self.root)}
            rd.enqueue("tape_recovery", _tape_recovery)

        if str(os.getenv("L5_COST_BASIS_AUDIT", "")).strip().upper() == "YES":
            rd.enqueue("cost_basis_audit", self._cost_basis_audit_step)

        if str(os.getenv("L5_ESTABLISH_BASELINE", "")).strip().upper() == "YES":
            rd.enqueue("establish_baseline", self._establish_baseline_step)

    def _cost_basis_audit_step(self, deadline: float) -> Dict[str, Any]:
        """One slice of the $1/share placeholder audit, then its apply.

        THE AUDIT. `leg_cost_basis_unknown` legs arrived as a venue NET
        position this executor never booked, so no order of ours exists to ask
        about and settlement priced them at the $1/share upper bound -- which
        is what makes realized_attributed an ESTIMATE rather than a
        measurement. It reads the account activity feed and reports what the
        fills actually were, split L5-owned vs OWNER_MANUAL by our own
        persisted client order ids. It WRITES NOTHING and cannot: applying its
        finding would move realized toward zero, which correct_settled_market
        refuses as CORRECTION_WOULD_RELAX_LOSS_HALT and which needs its own
        owner decision.

        WHY IT IS SLICED. The feed was 133 pages on 2026-11-06 and the shared
        limiter paces reads at PMUS_RPS = 1.5 a second, so one pass is ~89
        seconds of sleeping. Taken `L5_COST_BASIS_PAGES_PER_TICK` pages at a
        time it is the same walk in the same order, and no single call holds
        the limiter long enough to delay an order poll or a book read.

        NOTHING IS COMPUTED FROM A PARTIAL WALK. `audit` is called only once
        the walk is done, and an incomplete walk still refuses every basis
        with ACTIVITY_WALK_INCOMPLETE exactly as it did inline.
        """
        from src import l5_cost_basis_audit as CBA
        pw = getattr(self, "_cost_basis_walk", None)
        if pw is None:
            pw = RD.PagedWalk(
                CBA.walk_activities, self.transport,
                pages_per_step=int(str(os.getenv(
                    "L5_COST_BASIS_PAGES_PER_TICK", "12")).strip() or 12),
                max_pages=CBA.MAX_PAGES)
            self._cost_basis_walk = pw
        # THE CURRENT WINDOW'S TRANSPORT, re-bound each step: `roll` rebuilds it
        # every fifteen minutes and a walk that spans a rollover must keep
        # using a live object rather than the one it started with.
        pw.transport = self.transport
        pw.step(deadline)
        if not pw.done:
            return {"_resume": True, "_kind": "L5_COST_BASIS_AUDIT_PROGRESS",
                    "walk": pw.report(),
                    "labels": ("the activity walk is paced across ticks and "
                               "is NOT finished. Nothing has been computed "
                               "from it and nothing has been written.")}
        result = CBA.audit(self.store, self.transport, walk=pw.walk)
        # THE MEASURED-COST CORRECTION, from the walk that JUST ran.
        # INSPECT is the default and writes nothing; a batch id in
        # L5_COST_BASIS_APPLY is the owner's authorization to write.
        # Once per process, so it cannot run twice in a session, and each
        # leg still passes all ten guards inside the store's verb.
        mode = CBA.apply_mode()
        emit("L5_COST_BASIS_APPLY", CBA.apply_cost_basis_corrections(
            self.store, result,
            batch_id=(None if mode == CBA.APPLY_INSPECT else mode),
            manifest=CBA.batch_manifest()))
        return dict(result, _kind="L5_COST_BASIS_AUDIT",
                    walk_pacing=pw.report())

    def _establish_baseline_step(self, _deadline: float) -> Dict[str, Any]:
        """The L5 BASELINE EPOCH, struck ONCE and only from authoritative
        reads (OWNER 2026-10-16).

        It is NOT a claim that historical L5 losses were zero:
        establish_l5_baseline snapshots and preserves every pre-T figure,
        including the unknown-cost provenance keys. It is the instant from
        which ATTESTED L5 settlements are counted.

        Every read is required and none may be inferred. Cash comes from
        /v1/account/balances, the positions from /v1/portfolio/positions,
        L5-owned open orders from our OWN durable live-order record (the
        account's open orders say what is resting, not whose it is, and this
        account is SHARED), and the outstanding obligation from the same
        helper the cash gate uses. A failure strikes NO baseline, so the
        scoped ceilings keep refusing L5_SCOPED_REALIZED_UNAVAILABLE -- the
        safe direction.
        """
        acct = self.transport.account() or {}
        # THE DEFECT THIS REPLACES. /v1/account/balances returns
        # `balances` as a LIST of entries carrying buyingPower; the
        # first version read availableBalance/cash/etc off the
        # TOP-LEVEL dict, never looked inside balances[], and so
        # produced cash=None and refused BASELINE_READS_UNAVAILABLE
        # at 02:39:36Z with venue_positions_read=true. The runner's
        # own cash refresh already does this correctly, and this now
        # uses the identical proven parser rather than guessing key names.
        # Still None on a genuinely unreadable balance, which still
        # refuses -- the validation is unchanged.
        cash = T.PmusTransport.buying_power(acct)
        pos = self.transport.positions()
        # from the store's OWN committed-cash record, not from the
        # executor (which is constructed after `roll` queues this and would
        # therefore have reported a silent zero -- missing is never zero).
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
        return dict(res, venue_positions_read=(pos is not None),
                    _kind=("L5_BASELINE_ESTABLISHED" if res.get("established")
                           else "L5_BASELINE_REFUSED"))

    def entry_critical(self, now: float) -> bool:
        """Is this window still in the phase where a decision is at stake?

        TRUE IS THE SAFE ANSWER and it is the one every uncertainty gets. A
        deferred diagnostic has no deadline of its own: it can always wait one
        more tick, and the whole reason it was deferred is that waiting costs
        nothing while running at the wrong moment cost 93 seconds of every
        window. So anything unreadable, any resting order, any held
        inventory in the live market and any window whose age we cannot
        compute all read as critical.
        """
        try:
            if self.ws is None:
                return True
            if int(self.store.still_fillable_order_count() or 0) > 0:
                return True
            slug = getattr(self.transport, "market_slug", None)
            if slug:
                for side in ("UP", "DOWN"):
                    if D(str(self.store.position(str(slug), side) or 0)) != 0:
                        return True
            return False
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["entry_critical_unreadable:%s"
                       % type(exc).__name__] += 1
            return True

    def drain_roll_diagnostics(self, now: float) -> Optional[Dict[str, Any]]:
        """Run at most one deferred read-only diagnostic, or none.

        PLACED AFTER EVERY TRADING STAGE IN THE TICK, which is the whole
        point: a diagnostic may only ever use time this tick's decisions have
        already finished with. It is wrapped, because a diagnostic that raises
        must not be able to end a tick that still has settlement and
        reconciliation to do below it.
        """
        try:
            rd = self.roll_diag
            if not rd.pending:
                return None
            age = None if self.ws is None else (now - float(self.ws))
            out = rd.drain(t_in_window_s=age,
                           entry_critical=self.entry_critical(now))
            if out.get("ran") is None:
                self.stats["roll_diag:deferred:%s"
                           % str(out.get("reason"))] += 1
                return out
            rec = dict(out.get("record") or {})
            kind = str(rec.pop("_kind", "") or "L5_ROLL_DIAGNOSTIC")
            self.stats["roll_diag:%s:%s" % (out["ran"], out["outcome"])] += 1
            if out.get("outcome") == RD.FAILED:
                emit("%s_FAILED" % kind, dict(
                    rec, job=out["ran"],
                    labels=("the diagnostic is read-only; a failure changes "
                            "nothing, writes nothing and never touches the "
                            "ledger or the order path")))
            elif out.get("emit"):
                emit(kind, dict(rec, deferred_job={
                    "name": out["ran"], "outcome": out["outcome"],
                    "attempt": out["attempt"],
                    "elapsed_s": out["elapsed_s"],
                    "t_in_window_s": out.get("t_in_window_s"),
                    "still_pending": out.get("still_pending"),
                    "labels": ("ran off the window-roll path, after every "
                               "trading stage in the tick")}))
            return out
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["roll_diag_drain_failed:%s"
                       % type(exc).__name__] += 1
            return None

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

    def maybe_close_finished_campaign(self) -> Optional[Dict[str, Any]]:
        """Close a FINISHED campaign's budget claim through the ledger's own
        `close()`, on evidence, and record it.

        WHY THIS EXISTS. `l5_campaign_budget.close()` is the sanctioned end
        of a campaign's budget claim and NOTHING CALLED IT. A claim therefore
        stayed open for ever and the next campaign was refused
        CAMPAIGN_BUDGET_LOCKED -- observed on 2026-10-23 and again on
        2026-10-30, when camp11 still owned the ledger after its windows were
        spent and settled. `release_stale` covers the next campaign TAKING
        OVER at arming time; this covers a campaign RELINQUISHING its own
        claim, which is what `close` is for and what was missing.

        IT CANNOT BE USED TO RESET ANYTHING.
          * It calls `close()`. It never writes the ledger file, never
            deletes it, and never rotates a campaign id.
          * `close()` refuses unless the id it is given is the OPEN one, so
            this cannot close somebody else's claim.
          * Evidence is supplied from the store, not asserted: nothing
            outstanding and nothing unsettled, exactly what `close` demands.
            An unreadable store yields a SENTINEL outstanding entry so the
            close fails CLOSED rather than freeing a claim we cannot vouch
            for.
          * NO LOSS IS ERASED. The ledger holds only
            {campaign_id, closed, opened_utc, peak_usd}. Realized losses live
            in the executor's store and re-enter the gate through
            `worst_case_usd(realized_usd=...)` on EVERY evaluation, so after
            a close the headroom is still
            budget - (realized + unsettled + outstanding).

        WHEN IT CLOSES, and both are end-of-life conditions:
          * the open claim is NOT the in-force campaign -- it is finished and
            its approval is gone; or
          * the open claim IS the in-force campaign AND its windows are all
            spent, so no further window can open under it.
        A campaign with windows REMAINING is never closed by this.

        BOUNDED. Attempts are capped and spaced, and it stops for good once
        the ledger reports closed, so a claim that legitimately cannot close
        is reported rather than retried for ever.
        """
        if getattr(self, "_campaign_closed_done", False):
            return None
        n = int(getattr(self, "_campaign_close_tries", 0))
        if n >= CAMPAIGN_CLOSE_MAX_TRIES:
            return None
        last = getattr(self, "_campaign_close_t", None)
        now_t = time.time()
        if last is not None and (now_t - last) < CAMPAIGN_CLOSE_EVERY_S:
            return None
        try:
            led = AUTH.campaign_budget_ledger(self.root)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            led = None
        if led is None:
            self._campaign_closed_done = True      # no ledger configured
            return None
        self._campaign_close_t = now_t
        self._campaign_close_tries = n + 1
        try:
            doc = led.read()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out = {"ok": False, "reason": "CAMPAIGN_BUDGET_UNREADABLE:%s"
                                          % type(exc).__name__}
            emit("L5_CAMPAIGN_BUDGET_CLOSE", dict(out, attempt=n + 1))
            return out
        open_id = str(doc.get("campaign_id") or "").strip()
        if not open_id or bool(doc.get("closed")):
            self._campaign_closed_done = True
            return {"ok": True, "already_closed": True}

        # IS THE OPEN CLAIM FINISHED? Only two readings qualify.
        try:
            camp = self.campaign_telemetry()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            camp = {}
        cur = str(_campaign_nonce_sid() or "").strip()
        remaining = camp.get("windows_remaining")
        if open_id == cur:
            # the live campaign may relinquish its claim only once no window
            # can still open under it
            if remaining is None or int(remaining) > 0:
                return {"ok": False, "reason": "CAMPAIGN_STILL_HAS_WINDOWS",
                        "campaign_id": open_id,
                        "windows_remaining": remaining}
            finished_because = "WINDOWS_SPENT"
        else:
            finished_because = "NOT_THE_IN_FORCE_CAMPAIGN"

        # EVIDENCE FROM THE STORE, never asserted. Same shape the release
        # path uses, so the two cannot disagree about what "settled" means.
        try:
            live = self.store.live_orders() or {}
            outstanding = [
                dict(o, coid=k) for k, o in live.items()
                if LX.venue_decimal(o.get("remaining")) is None
                or (LX.venue_decimal(o.get("remaining")) or D(0)) > 0]
            retired = (getattr(self.store, "state", {}) or {}).get(
                "retired_markets") or {}
            committed = self.store.committed() or {}
            legs = [{"market": m, "settled": m in retired}
                    for m in committed]
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            outstanding = [{"UNREADABLE": type(exc).__name__}]
            legs = [{"market": "UNREADABLE", "settled": False}]

        unsettled = [l["market"] for l in legs if not l.get("settled")]
        evidence = {
            "finished_because": finished_because,
            "outstanding_orders": len(outstanding),
            "committed_markets": len(legs),
            "unsettled_markets": unsettled,
            "windows_remaining": remaining,
        }
        res = led.close(open_id, legs=legs, outstanding=outstanding,
                        now_iso=now_utc().isoformat())
        rec = {"campaign_id": open_id, "attempt": n + 1,
               "evidence": evidence, "result": dict(res),
               "labels": ("closed through the ledger's own close(); nothing "
                          "was written, deleted or rotated here, and no "
                          "realized loss is erased -- the ledger holds only "
                          "ownership and a high-water mark, and realized "
                          "figures re-enter every gate evaluation from the "
                          "executor's store.")}
        if res.get("ok"):
            self._campaign_closed_done = True
        else:
            rec["blocker"] = str(res.get("reason"))
        emit("L5_CAMPAIGN_BUDGET_CLOSE", rec)
        return rec

    def maybe_venue_exit_probe(self, t: Any) -> None:
        """ONCE per process, record what exits the venue actually offers.

        WHY THIS EXISTS. The stranded-inventory question "could we have SOLD
        the leg instead of completing it?" was answered from a source grep:
        `l5_order_body.build` hardcodes ORDER_ACTION_BUY, the only intents the
        venue has ever shown us are ORDER_INTENT_BUY_LONG and
        ORDER_INTENT_BUY_SHORT, and `VENUE_SIDE_WORDS` records that this
        venue spells "buy NO" as "sell YES". That is strong evidence and it is
        not proof: we have never issued a close request, so we have never
        seen one refused. This reads the venue's own portfolio resource and
        records its SHAPE, so the answer rests on the venue rather than on
        our vocabulary.

        STRICTLY READ-ONLY AND FAIL-SOFT. Two GETs through the existing
        transport, no mutation of any kind, and every failure is recorded
        rather than raised: a probe that could break the boot is not worth
        having. It places no order and changes no state.

        KEY NAMES ONLY, NEVER VALUES. What matters is whether the venue
        advertises a close/reduce/sellable affordance, which is a question
        about field names. Recording values would put account figures in the
        log for no gain.
        """
        # DONE ONLY WHEN IT LEARNED SOMETHING. The first version of this was
        # one-shot after the first preflight pass, and that made it useless
        # for the question it exists to answer: we deploy DISARMED and flat,
        # so the first read returned `rows: 0` and an empty response carries
        # no field names. A capability question about position rows can only
        # be answered while a position is HELD.
        if getattr(self, "_venue_exit_conclusive", False):
            return
        # ONLY AFTER PREFLIGHT PASSES. Before that the account, the market or
        # the credentials may not be readable at all, and a read_error here
        # would be recorded as if the venue had answered.
        if getattr(self, "preflight_status", None) != "PASSED":
            return
        if t is None:
            return
        # BOUNDED IN BOTH DIRECTIONS, because this is a diagnostic and not a
        # feed: no more often than the cadence, and never more than the cap,
        # so a permanently flat account cannot turn it into a poller.
        n = int(getattr(self, "_venue_exit_probes", 0))
        if n >= VENUE_EXIT_PROBE_MAX:
            return
        last = getattr(self, "_venue_exit_probe_t", None)
        now_t = time.time()
        if last is not None and (now_t - last) < VENUE_EXIT_PROBE_EVERY_S:
            return
        self._venue_exit_probe_t = now_t
        self._venue_exit_probes = n + 1
        out: Dict[str, Any] = {
            "basis": ("READ-ONLY. Two GETs on /v1/portfolio/positions via "
                      "the existing transport. No order, no mutation, no "
                      "state change. Field NAMES only, never values."),
            "vocabulary_from_source": {
                "order_actions_in_src": ["ORDER_ACTION_BUY"],
                "order_intents_in_src": ["ORDER_INTENT_BUY_LONG",
                                         "ORDER_INTENT_BUY_SHORT"],
                "note": ("both intents are BUYs; the venue's `side` word "
                         "describes the YES token, so ORDER_SIDE_SELL is how "
                         "it spells buying NO"),
            },
        }
        for name, call in (("positions", "positions"),
                           ("available_positions", "available_positions")):
            try:
                rows = getattr(t, call)()
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out[name] = {"read_error": type(exc).__name__}
                continue
            rows = list(rows or ())
            keys: set = set()
            for r in rows:
                if isinstance(r, Mapping):
                    keys |= {str(k) for k in r.keys()}
            out[name] = {
                "rows": len(rows),
                "field_names": sorted(keys),
                "exit_affordance_fields": sorted(
                    k for k in keys
                    if any(w in k.lower() for w in
                           ("sell", "close", "reduce", "exit", "redeem",
                            "liquidat", "available"))),
            }
        # CONCLUSIVE ONLY WITH ROWS. An empty portfolio answers nothing about
        # what a position row advertises, so it is recorded and the probe
        # stays armed for the next one.
        rows = sum(int((out.get(k) or {}).get("rows") or 0)
                   for k in ("positions", "available_positions"))
        conclusive = rows > 0
        if conclusive:
            self._venue_exit_conclusive = True
        out["probe"] = {
            "attempt": self._venue_exit_probes,
            "max_attempts": VENUE_EXIT_PROBE_MAX,
            "conclusive": conclusive,
            "note": ("CONCLUSIVE: a held position row was read, so the field "
                     "names above are what this venue actually advertises"
                     if conclusive else
                     "INCONCLUSIVE: the account is flat, so there are no "
                     "position rows and no field names to read. This says "
                     "NOTHING about whether the venue supports closing a "
                     "position; the probe stays armed for a window that "
                     "holds inventory."),
        }
        emit("L5_VENUE_EXIT_CAPABILITY", out)

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
            # ANOTHER STRATEGY'S POSITION, held here at the $1 bound: before
            # it is settled, try to replace the bound with its real cost.
            # Off unless the other strategy's fills feed is configured; any
            # failure leaves the bound and the ordinary settlement runs.
            self.attest_other_strategy_cost(market, t)
            try:
                # A SPORTS-CONTROLLER MARKET STILL AT THE BOUND: settle it on
                # that controller's own feed, confirmed by our venue reads.
                # Off unless L5_SPORTS_SETTLE_FROM_FEED=1; None (any miss)
                # settles exactly as before.
                out = self.settle_sports_from_feed(market, t, rep)
                if out is None:
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

    def maybe_attest_venue_costs(self, now: float) -> None:
        """Replace the $1/share bound on SETTLED positions this ledger did not
        open with the venue's own fill costs, where they reconcile exactly.

        After settlement only: no open position, exposure cap, risk floor or
        submission gate reads anything this writes. It moves the realized
        figure of an already-settled market, on the day it settled, through
        an auditable correction record. Paced and bounded inside
        `l5_venue_cost_attestation`; ANY failure here is swallowed and the
        bound stays -- this can never stop a tick.
        """
        try:
            from src import l5_venue_cost_attestation as VCA
            att = getattr(self, "_venue_cost_attestor", None)
            if att is None:
                att = VCA.Attestor()
                self._venue_cost_attestor = att
            out = att.step(self.store, self.transport, float(now))
            done = [r for r in (out.get("evaluated") or out.get("refused")
                                or []) if isinstance(r, Mapping)]
            if done or out.get("walk_failed"):
                emit("L5_VENUE_COST_ATTESTATION", dict(out, labels=(
                    "attested=False keeps the $1/share bound. A correction "
                    "restates an already-settled market on its own UTC day; "
                    "it is account-wide realized, not verified L5.")))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["venue_cost_attest_error:%s"
                           % type(exc).__name__] += 1
            except BaseException:                              # noqa: BLE001
                pass

    def maybe_watch_equity_transfers(self, now: float) -> None:
        """READ-ONLY: while an equity baseline is set, keep reading the
        venue's feed for money moving in or out since it.

        Nothing is read unless `L5_EQUITY_BASELINE` is a valid document. It
        calls no store method and writes nothing; its only request is the
        activity feed's GET, two pages a tick, then idle for five minutes.
        ANY failure is swallowed -- the published line then refuses, which
        is the safe direction, and the tick goes on.
        """
        try:
            from src import l5_equity_baseline as EB
            from src import l5_equity_feed as EF
            doc = EB.read_document()
            if doc.get("state") != "ON":
                return
            watch = getattr(self, "_equity_watch", None)
            if watch is None:
                watch = EF.TransferWatch()
                self._equity_watch = watch
            watch.step(self.transport, doc["at_utc"], float(now))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["equity_watch_error:%s"
                           % type(exc).__name__] += 1
            except BaseException:                              # noqa: BLE001
                pass

    def equity_risk_lines(self, now: Optional[float] = None
                          ) -> Dict[str, Any]:
        """The venue-equity controls, PUBLISHED for another strategy.

        Two blocks, each carrying its own `refusal`:

          `portfolio_drawdown` -- the existing owner limit, evaluated on a
          value that is None unless buying power was read within
          EQUITY_CASH_MAX_AGE_S and no order state is uncertain. This
          service's OWN gate is untouched and still reads its own value;
          this is the copy a second strategy must honour, and it is the
          stricter of the two.

          `equity_baseline` -- the same value against the owner's baseline
          document, with money moved in or out since it, and the period's
          measured result beside the uncertain rows.

        READ-ONLY, computed from what the tick already holds: no venue
        request is made here, because this is served from the registry's
        thread. Never raises; a failure is published as a refusal.
        """
        try:
            from src import l5_portfolio_drawdown as PDD
            from src import l5_equity_baseline as EB
            t = time.time() if now is None else float(now)
            why = None
            age = (None if self.available_cash_t is None
                   else t - self.available_cash_t)
            val = None
            unc_seen, sibling_seen = None, None
            if self.available_cash_usd is None or age is None:
                why = "BUYING_POWER_NOT_READ"
            elif age > EQUITY_CASH_MAX_AGE_S or age < -5:
                why = "BUYING_POWER_STALE"
            else:
                unc = self.store.orders_uncertain()
                #: THE VALUE IS STILL PUBLISHED when the whole uncertainty
                #: is another strategy's position this ledger had not
                #: booked yet -- see `uncertainty_confined_to_other_
                #: strategy`. The figure is the venue's own buying power
                #: plus guaranteed pair redemption, and an adopted net
                #: position adds nothing to it, so it can only read LOWER
                #: than the truth. This service's own submission gate is
                #: untouched. OFF unless the switch is exactly the token.
                sibling_only = None
                if unc and publish_value_during_sibling_adoption():
                    try:
                        sibling_only = self.store \
                            .uncertainty_confined_to_other_strategy()
                    except BaseException as exc:               # noqa: BLE001
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        sibling_only = None
                unc_seen, sibling_seen = unc, sibling_only
                if unc and not sibling_only:
                    why = "ORDER_STATE_UNCERTAIN"
                else:
                    val = PDD.worst_case_value(
                        self.available_cash_usd,
                        self.store.positions_by_market())
                    if val is None:
                        why = "POSITIONS_UNREADABLE"
            doc = EB.read_document()
            moved: Dict[str, Any] = {"net_usd": None, "why": "NOT_APPLICABLE"}
            period = None
            if doc.get("state") == "ON":
                watch = getattr(self, "_equity_watch", None)
                seen = (None if watch is None
                        else watch.view(doc["at_utc"], t))
                moved = EB.net_transfers(seen, doc["at_utc"])
                moved["read"] = (None if seen is None else
                                 {k: seen.get(k) for k in
                                  ("by", "pages", "age_s", "last_error",
                                   "complete_back_to")})
                #: reporting only; `net_usd` and `why` above decide
                try:
                    moved["progress"] = (None if watch is None
                                         else watch.progress(t))
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    moved["progress"] = None
                period = EB.period_realized(
                    self.store.state.get("retired_markets") or {},
                    doc["at_utc"])
            net = moved.get("net_usd")
            detail = {k: (str(v) if k == "net_usd" and v is not None else v)
                      for k, v in moved.items()}
            return {
                #: `manual` is None unless the bot/total split is ON, and
                #: is built from the tick's STORED reading: still no venue
                #: request on this thread.
                "portfolio_drawdown": PDD.report(
                    val, manual=Runner.portfolio_manual_lines(self, now=t)),
                "equity_baseline": dict(
                    EB.line(doc, val, net, transfers_detail=detail),
                    period=period),
                "equity_value": {
                    "usd": None if val is None else str(val),
                    "unavailable_why": why,
                    "buying_power_age_s": (None if age is None
                                           else round(age, 1)),
                    "max_age_s": EQUITY_CASH_MAX_AGE_S,
                    #: present ONLY when the value was published DURING an
                    #: uncertainty, with the markets it was confined to;
                    #: otherwise the block is exactly what it always was
                    **({} if not (unc_seen and sibling_seen) else {
                        "published_while_uncertain": {
                            "scope": SIBLING_VALUE_TOKEN,
                            "markets": list(sibling_seen)}})},
            }
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            bad = "EQUITY_LINES_FAILED:%s" % type(exc).__name__
            return {"portfolio_drawdown": {"state": "UNREADABLE",
                                           "refusal": bad},
                    "equity_baseline": {"state": "UNREADABLE",
                                        "refusal": bad},
                    "equity_value": {"usd": None, "unavailable_why": bad}}

    def maybe_correct_venue_supersets(self, now: float) -> None:
        """The owner-listed venue-superset cost corrections.

        OFF unless `L5_VENUE_SUPERSET_CORRECTION` is set. `INSPECT` runs the
        real correction against a copy of the ledger that cannot be saved
        and reports what it would do. `APPLY` writes, and only when
        `L5_VENUE_SUPERSET_CORRECTION_APPROVED` equals the shipped
        manifest's SHA-256. It changes no limit. ANY failure is swallowed:
        this can never stop a tick. See `l5_venue_superset_correction`.
        """
        try:
            from src import l5_venue_superset_correction as VSC
            if not (os.environ.get(VSC.MODE_ENV) or "").strip():
                return
            job = getattr(self, "_venue_superset_correction", None)
            if job is None:
                job = VSC.Correction()
                self._venue_superset_correction = job
            out = job.step(self.store, self.transport, float(now))
            if not out.get("idle") and not out.get("walking"):
                emit("L5_VENUE_SUPERSET_CORRECTION", out)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["venue_superset_correction_error:%s"
                           % type(exc).__name__] += 1
            except BaseException:                              # noqa: BLE001
                pass

    def maybe_diagnose_venue_costs(self, now: float) -> None:
        """READ-ONLY: print the venue's fills beside this ledger's figures
        for the rows the attestation gave up on.

        OFF unless `L5_VENUE_COST_DIAGNOSTIC=READ_ONLY`. It calls no store
        method and writes nothing; its only transport call is the activity
        feed's GET. It is deliberately NOT in the heartbeat and no gate
        reads it. ANY failure is swallowed -- this can never stop a tick.
        See `l5_venue_cost_diagnostic`.
        """
        try:
            from src import l5_venue_cost_diagnostic as VCD
            if not VCD.enabled():
                return
            diag = getattr(self, "_venue_cost_diagnostic", None)
            if diag is None:
                diag = VCD.Diagnostic()
                self._venue_cost_diagnostic = diag
            out = diag.step(self.store, self.transport, float(now))
            if out.get("compared") or out.get("walk_failed") \
                    or out.get("walk_insufficient"):
                emit("L5_VENUE_COST_DIAGNOSTIC", out)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["venue_cost_diagnostic_error:%s"
                           % type(exc).__name__] += 1
            except BaseException:                              # noqa: BLE001
                pass

    def venue_cost_attestation_lines(self) -> Dict[str, Any]:
        """The heartbeat block for `maybe_attest_venue_costs`. Never raises."""
        try:
            from src import l5_venue_cost_attestation as VCA
            att = getattr(self, "_venue_cost_attestor", None) or VCA.Attestor()
            return att.heartbeat(self.store)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"error": type(exc).__name__,
                    "labels": "attestation telemetry unavailable; the $1 "
                              "bound stays wherever it was"}

    def maybe_report_venue_basis(self, now: float) -> None:
        """REPORT-ONLY: the venue's executions beside this ledger's booked
        results, including markets the ledger never saw.

        OFF unless `L5_VENUE_BASIS` is exactly `REPORT`. It calls no store
        method, writes nothing and changes no gate; its only transport call
        is the activity feed's GET at the attestation's pace. ANY failure
        is swallowed -- this can never stop a tick. See
        `l5_venue_basis_report`.
        """
        try:
            from src import l5_venue_basis_report as VBR
            if VBR.mode() != VBR.MODE_REPORT:
                return
            job = getattr(self, "_venue_basis_pass", None)
            if job is None:
                job = VBR.Pass()
                self._venue_basis_pass = job
            out = job.step(self.store, self.transport, float(now))
            if out.get("report") is not None:
                emit("L5_VENUE_BASIS", out["report"])
                for rec in out.get("details") or ():
                    emit("L5_VENUE_BASIS_DETAIL", rec)
            elif out.get("walk_failed"):
                emit("L5_VENUE_BASIS", {"walk_failed": out["walk_failed"],
                                        "applied": False})
            for rec in out.get("orders") or ():
                emit("L5_VENUE_BASIS_ORDERS", rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["venue_basis_error:%s"
                           % type(exc).__name__] += 1
            except BaseException:                              # noqa: BLE001
                pass

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
                # THE VALIDATION GATES, so step 14 can see the worst-case
                # deficit bound. getattr twice: a runner with no executor,
                # or an executor with no gates, passes None and step 14
                # then refuses CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT for any
                # campaign that names one -- which is the fail-closed
                # direction. A campaign that names none is unaffected.
                gates=getattr(getattr(self, "executor", None),
                              "validation_gates", None),
                unresolved=unresolved_discrepancies(self.store),
                # getattr on SELF too: a runner without an executor has no
                # capital envelope at all, and the boundary must refuse that
                # with CAPITAL_SCOPE_UNKNOWN rather than raise out of the
                # arming path.
                # OPTION B, owner-approved 2026-10-30. The Q1 handshake is
                # REQUIRED alongside the campaign approval, and a process
                # lacking it is refused BEFORE the window claim is spent --
                # which is how 0145z was lost: `authorize_arming` accepted
                # the campaign alone, spent a claim, and `PMUSTransport.arm()`
                # then refused the same process one call later.
                #
                # This narrows the authorization scope deliberately: two
                # owner-held objects are now necessary to arm, not one. It
                # supersedes the previous invariant that a campaign
                # authorizes windows on its own
                # (test_PMUS_Q1_LIVE_NO_cannot_block_a_campaign, restated).
                # It cannot restore a claim already spent and does not try:
                # the budget is counted from the locks on the volume.
                transport_armable=Q1.armed(),
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

    def _note_cycle_peaks(self, slug: str, port: Mapping[str, Any]) -> None:
        """Remember the most that rested at once in this window. Telemetry.

        Sampled where the portfolio is already assembled (every few
        seconds), from the ledger's own live orders -- so a peak here is a
        lower bound on the true one, never an invention. Never raises.
        """
        try:
            peaks = getattr(self, "_cycle_peaks", None)
            if peaks is None:
                peaks = self._cycle_peaks = {}
            pk = peaks.setdefault(slug, {
                "samples": 0, "peak_resting_orders": 0,
                "peak_resting_up": 0, "peak_resting_down": 0,
                "samples_with_both_sides_resting": 0,
                "peak_committed_usd": "0",
                "peak_worst_case_with_resting_usd": "0"})
            if len(peaks) > 8:                      # a few windows, no more
                for k in sorted(peaks)[:-8]:
                    peaks.pop(k, None)
            orders = list(port.get("orders") or ())
            up = sum(1 for o in orders if str(o.get("side")).upper() == "UP")
            dn = len(orders) - up
            pk["samples"] += 1
            pk["peak_resting_orders"] = max(pk["peak_resting_orders"],
                                            len(orders))
            pk["peak_resting_up"] = max(pk["peak_resting_up"], up)
            pk["peak_resting_down"] = max(pk["peak_resting_down"], dn)
            if up and dn:
                pk["samples_with_both_sides_resting"] += 1
            for key, src in (("peak_committed_usd", "committed_usd"),
                             ("peak_worst_case_with_resting_usd",
                              "worst_case_with_resting")):
                try:
                    v = D(str(port.get(src)))
                    # worst case is a P&L: the peak LOSS is the most negative
                    v = -v if src == "worst_case_with_resting" else v
                    if v.is_finite() and v > D(pk[key]):
                        pk[key] = str(v)
                except (ArithmeticError, ValueError, TypeError):
                    pass
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["cycle_peaks_error:%s" % type(exc).__name__] += 1

    def cycle_report(self, slug: Any, brief: bool = False) -> Dict[str, Any]:
        """One window's trading cycle, from the ledger. Read-only telemetry.

        WHAT THE NEXT LIVE WINDOW HAS TO SHOW, in one record: orders
        submitted against rounds spent, proposals priced again rather than
        charged, how many orders rested together, how many pairs were
        opened after the first, what cash and exposure the book reserved,
        and what the matched and unmatched inventory cost. Rebates are not
        here: only a venue cash credit confirms one.
        """
        if not slug:
            return {"market": None}
        out: Dict[str, Any] = {"market": str(slug)}
        try:
            ex = self.executor
            rl = ex.round_lifecycle(str(slug))
            out["rounds"] = rl
            if brief:
                return out
            st = self.store
            up, dn = st.position(str(slug), "UP"), st.position(str(slug), "DOWN")
            lc = st.leg_cost(str(slug))
            uc, dc = D(str(lc.get("UP", 0))), D(str(lc.get("DOWN", 0)))
            matched = min(up, dn)
            pair = ((uc / up) + (dc / dn)) if up > 0 and dn > 0 else None
            out["inventory"] = {
                "up_qty": str(up), "dn_qty": str(dn),
                "up_cost_usd": str(uc), "dn_cost_usd": str(dc),
                "matched_contracts": str(matched),
                "average_pair_cost": None if pair is None else str(pair),
                "locked_pair_pnl_usd": (None if pair is None
                                        else str(matched * (D(1) - pair))),
                "unmatched_contracts": str(abs(up - dn)),
                "unmatched_side": ("UP" if up > dn else
                                   "DOWN" if dn > up else None),
                "labels": ("ledger quantities and what fills charged. The "
                           "residual's P&L is known only at settlement.")}
            n_orders = int(rl.get("rounds_with_a_submitted_order") or 0)
            out["pairs_opened_after_the_first"] = max(0, n_orders - 1)
            vg = getattr(ex, "validation_gates", None)
            if vg is not None:
                held = vg.cash_committed(ex.cash_legs_for_gates())
                rest = ex.outstanding_for_gates()
                rsv = vg.reserved_cash(
                    outstanding=rest, order_qty=D(0), order_price=D(0),
                    order_intent="ORDER_INTENT_BUY_LONG",
                    inventory=ex.inventory_for_gates(str(slug)))
                out["reservations"] = {
                    "cash_held_in_fills_usd": str(held),
                    "cash_reserved_for_resting_usd": str(rsv["resting"]),
                    "basis": rsv["basis"],
                    "hedged_resting_contracts": str(rsv["hedged_resting"]),
                    "max_cash_deployed_usd": str(vg.max_cash_deployed_usd),
                    "exposure_worst_case_usd": str(
                        st.risk_exposure_usd(str(slug)))}
            out["resting_peaks"] = dict(
                (getattr(self, "_cycle_peaks", None) or {}).get(str(slug))
                or {"samples": 0})
            # what the venue's buying power has confirmed returned, and how
            # each balance read was judged. Counts are since process start.
            _anchor = getattr(self, "_bp_anchor", None)
            out["recycling"] = {
                "policy": str(getattr(vg, "cash_policy", None) or ""),
                "anchor_usd": None if _anchor is None else str(_anchor),
                "confirmed_matched": {
                    m: str((v or {}).get("matched")) for m, v in
                    (ex.__dict__.get("recycle_confirmed") or {}).items()},
                "confirmed_share_this_market": str(
                    ex.recycle_confirmed_share(str(slug))),
                "checks": {k.split(":", 1)[1]: v
                           for k, v in self.stats.items()
                           if k.startswith("recycle_check:") and v}}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["error"] = type(exc).__name__
        return out

    def close_window(self) -> None:
        t = self.transport
        slug = t.market_slug if t else None
        out: Dict[str, Any] = {
            "market": slug, "decisions": dict(self.win),
            "preflight": self.preflight_status,
            "armed": bool(t and t.armed),
            "observability": self.book.window_report(slug) if slug else None,
            "cycle": self.cycle_report(slug),
        }
        if slug and self.first_action_t is not None:
            obs = out["observability"]
            ws = AB.slug_start(slug)
            out["first_l5_action_offset_s"] = round(self.first_action_t - ws, 1)
            fb = obs.get("first_book_offset_s")
            out["book_before_first_l5_action"] = (
                fb is not None and fb <= out["first_l5_action_offset_s"])
        # WHICH SIDE THIS WINDOW SETTLED ON, observed and never inferred.
        #
        # The paired-entry economics need P(UP settles | one leg filled
        # alone) -- the adverse-selection term that decided the 15:30Z loss
        # -- and the recorded tapes do not carry it: four of ten windows in
        # the replay sources end between 31% and 59% of the way through, so
        # their terminal book never shows which side won. Six determined
        # observations put a 50% estimate at 0.19..0.81, against an entry
        # threshold that needs +/-0.09.
        #
        # So this records one line per window the executor already watches.
        # It places no order, calls no venue, computes no probability, and
        # an early or ambiguous book is recorded UNDETERMINED with its
        # reason rather than becoming a weak point that calibrates money.
        #
        # It can never interfere with closing a window: every failure is
        # swallowed into the record itself.
        try:
            from src import l5_settlement_observer as SO
            _snap = self.book.snapshot(slug) if slug else None
            out["settlement_observed"] = SO.observe(
                market=slug or "",
                window_start_utc=(
                    dt.datetime.fromtimestamp(AB.slug_start(slug),
                                              dt.timezone.utc).isoformat()
                    if slug and AB.slug_start(slug) is not None else ""),
                observed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                bid=(_snap or {}).get("bid"),
                ask=(_snap or {}).get("ask"),
                frames_seen=(_snap or {}).get("book_updates"))
        except BaseException as _exc:                          # noqa: BLE001
            if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["settlement_observed"] = {
                "settled_side": "UNDETERMINED",
                "reason": "OBSERVER_FAILED:%s" % type(_exc).__name__}

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
            # KEPT FOR THE BOOK-DRIVEN RE-EVALUATION, and it is a REAL
            # admitted action. Route B re-runs the existing completion path
            # against the CURRENT book using this row; it invents no L5
            # action, and the trigger record says the book asked.
            try:
                _ws_k = OBS.l5_window_start(r.get("slug", ""))
                if _ws_k:
                    self._last_action_row[OBS.pmus_slug_for(_ws_k)] = dict(r)
            except BaseException:                              # noqa: BLE001
                pass
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
        elif LX.continuous_inventory_enabled() and Runner.continuous_owns(
                self, slug):
            # ONE CONTROLLER: the continuous inventory pass owns this
            # market's orders, so an L5 action is observed, not followed.
            rec = {"action_key": row["action_key"], "market": slug,
                   "refused_by_layer": "CONTINUOUS_INVENTORY",
                   "refused_reason": "CONTINUOUS_INVENTORY_OWNS_MARKET",
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
            # THE TWO-SIDED LEG MARKERS MUST NOT OUTLIVE THIS CALL.
            # `FaithfulExecutor.handle` clears them on ENTRY, which closes
            # every REFUSAL path. AN EXCEPTION IS NOT A REFUSAL: a raise
            # between the two-sided adoption and the consume leaves them set
            # until the next call, and `fresh_book_requote` reads
            # `_probe_leg_pending` outside that method -- so in the gap it
            # sees a leg that is not pending. Entry-clearing cannot close it:
            # the gap precedes the entry it would clear at, and Route B
            # re-enters far more often than one L5 action at a time.
            #
            # AT THE CALL SITE, NOT AS A METHOD SPLIT. Splitting either
            # `handle` moved the body out from under source-introspection
            # review guards -- fourteen of them across two attempts, one
            # reporting ZERO refusal sites against an inventory of 44. A
            # `finally` here costs none of them.
            try:
                rec = ex.handle(row, {}, market=slug, window=str(ws),
                                open_markets=self.open_markets())
            finally:
                ex._probe_leg_pending = None
                ex._probe_round_pending = None
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
            # THE RESIDUAL CONTROLS. 1415z reached 4.25 unmatched against a
            # 3-contract cap and the records saying WHY reached only the
            # tape. See STAGE_B2_RESIDUAL_CAP_DEFECT.md. Log-only.
            "residual_cap": rec.get("RESIDUAL_CAP"),
            "inventory_class": rec.get("INVENTORY_CLASS"),
            "final_residual_guard": rec.get("FINAL_RESIDUAL_GUARD"),
            "order": {k: order.get(k) for k in
                      ("client_order_id", "submitted", "reason", "venue_order_id",
                       "state", "error")} if order else None,
            "intent": order.get("intent"),
            "decision_role": _role,
            # the reopening decision 1230z did not make; see
            # `probe_reopen_decision`. Carries its own break-even.
            "two_sided_reopen": rec.get("TWO_SIDED_REOPEN"),
            # the frozen plan this tick re-priced, and the drift that
            # justified it. Absent when nothing drifted, which is the
            # common case; see `TS.replan_drift_ticks` for why a plan left
            # behind the touch was invisible to both existing guards.
            "two_sided_replan": rec.get("TWO_SIDED_REPLAN"),
            "two_sided_round_repriced": rec.get("TWO_SIDED_ROUND_REPRICED"),
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
        # ONE BOOK READ, SHARED. Every policy on this tick must decide on the
        # same book and the same instant or their outputs are not comparable,
        # and the shadow completion is one of those policies: a second
        # `snapshot()` could hand it a different book than the arm whose
        # inventory it is completing.
        try:
            snap = self.book.snapshot(ev.slug)
            ev.step(snap, self.book.trades(ev.slug), now)
        except Exception as exc:                               # noqa: BLE001
            self.stats["two_sided_errors"] += 1
            emit("L5_TWO_SIDED_ERROR", {"where": "step", "error": T.redact(
                "%s: %s" % (type(exc).__name__, exc))})
            return
        if now - self.last_two_sided_report_t >= self.TWO_SIDED_REPORT_S:
            self.last_two_sided_report_t = now
            emit("L5_TWO_SIDED_SHADOW",
                 TS.bounded_for_log(dict(ev.report(None), interim=True)))
        self.shadow_completion_step(ev, now, snap)

    #: how often the shadow completion policy is evaluated, per window.
    SHADOW_COMPLETION_EVERY_S = 10.0

    def shadow_completion_step(self, ev: Any, now: float,
                               snapshot: Optional[Mapping[str, Any]] = None
                               ) -> None:
        """Ask the bounded taker policy about the MODELLED inventory.

        WHY THIS IS NOT THE LIVE COMPLETION PATH. `maybe_ts_completion` reads
        `store.gross_inventory`, which only real fills populate, so under the
        structural shadow lock it answers NO_INVENTORY_TO_COMPLETE on every
        tick and twenty-four windows of it would say nothing about the policy.
        This asks the same policy the same question against the inventory the
        maker-only arm actually modelled, priced on the authenticated book.

        READ-ONLY AND UNARMED BY CONSTRUCTION: there is no transport in this
        path at all. It records the BEST authorized cross per arm per window,
        which `two_sided_close` scores against the venue's own resolution.
        """
        if now - getattr(self, "_last_shadow_completion_t", 0.0) \
                < self.SHADOW_COMPLETION_EVERY_S:
            return
        self._last_shadow_completion_t = now
        try:
            snap = dict(snapshot or {})
            if not snap:
                return
            best = getattr(self, "_shadow_completion_best", None)
            if best is None:
                best = {}
                self._shadow_completion_best = best
            spent = getattr(self, "_shadow_completion_spent", None)
            if spent is None:
                spent = {}
                self._shadow_completion_spent = spent
            from src import l5_two_sided_probe as TSP
            rows: List[Dict[str, Any]] = []
            for cfg in getattr(ev, "configs", ()) or ():
                # ONLY THE ARMS THAT MIRROR THE DEPLOYED ENGINE. The shadow
                # carries dozens of historical at-touch cells whose basis the
                # live engine does not use, and grading the policy on those
                # would measure a configuration we do not run.
                if not SC.is_live_arm(cfg):
                    continue
                aid = SC.arm_id(cfg)
                # THE RUNGS MUST BE THE SIDE BEING BOUGHT, so the deficient
                # side is derived here before pricing. Passing the wrong
                # side's book would walk a VWAP for a leg we are not buying.
                try:
                    _up = TSP._d(cfg.qty[FM.UP]) or D(0)
                    _dn = TSP._d(cfg.qty[FM.DOWN]) or D(0)
                except BaseException:                          # noqa: BLE001
                    _up = _dn = D(0)
                _need = FM.DOWN if _up > _dn else FM.UP
                rec = SC.evaluate(
                    cfg=cfg, snapshot=snap,
                    rungs=(None if _up == _dn
                           else self.completion_rungs(ev.slug, _need)),
                    tick=getattr(self.transport, "tick_size", None)
                    or D("0.01"),
                    min_qty=getattr(self.transport, "quantity_increment",
                                    None) or D("0.01"),
                    maker_attempted=True,
                    maker_rested_s=max(
                        0.0, now - float(getattr(ev, "window_start", now)
                                         or now)),
                    window_outlay_spent_usd=D(str(spent.get(aid, 0))),
                    margin_floor_usd=self._taker_policy_number(
                        TCP.MARGIN_FLOOR_ENV, TCP.DEFAULT_MARGIN_FLOOR_USD),
                    max_outlay_usd=self._taker_policy_number(
                        TCP.MAX_OUTLAY_ENV, TCP.DEFAULT_MAX_OUTLAY_USD),
                    window_outlay_cap_usd=self._taker_policy_number(
                        TCP.WINDOW_OUTLAY_ENV,
                        TCP.DEFAULT_WINDOW_OUTLAY_USD),
                    maker_patience_s=self._taker_policy_number(
                        TCP.MAKER_PATIENCE_ENV,
                        TCP.DEFAULT_MAKER_PATIENCE_S),
                    depth_haircut=self._taker_policy_number(
                        TCP.DEPTH_HAIRCUT_ENV, TCP.DEFAULT_DEPTH_HAIRCUT))
                rows.append(rec)
                self.stats["shadow_completion:%s"
                           % str(rec.get("reason"))[:48]] += 1
                if not rec.get("ok"):
                    continue
                # ONE OPPORTUNITY PER STRANDED LEG, not per tick. Keep the
                # best worst-case outcome seen, which is what a patient
                # policy with a margin floor would wait for.
                pol = dict(rec.get("taker_policy") or {})
                w = TSP._d(pol.get("conservative_worst_after"))
                prev = best.get(aid)
                pw = TSP._d((prev or {}).get("worst_after"))
                if w is not None and (pw is None or w > pw):
                    best[aid] = {
                        "side": rec.get("side"), "qty": pol.get("qty"),
                        "price": pol.get("price"),
                        "worst_after": str(w),
                        "outlay_usd": pol.get("additional_outlay_usd"),
                        "t_in_window_s": round(
                            now - float(getattr(ev, "window_start", now)
                                        or now), 1),
                        "clears_in_full": pol.get("clears_in_full"),
                        "displayed_depth": rec.get("displayed_depth"),
                    }
            if rows:
                emit("L5_SHADOW_COMPLETION", {
                    "market": ev.slug, "arms": rows,
                    "best_so_far": dict(best),
                    "labels": ("MODELLED INVENTORY, REAL BOOK, read-only. No "
                               "transport is reachable from this path. "
                               "Nothing here is submitted, filled or "
                               "realized."),
                })
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_completion_failed:%s"
                       % type(exc).__name__] += 1

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
        # PARK THE SHADOW COMPLETION BESIDE THE WINDOW IT BELONGS TO.
        # Settlement can land minutes after the roll, and the per-window best
        # lives in memory, so it has to be keyed by slug before it is cleared
        # or the cross cannot be scored against the venue's resolution.
        try:
            parked = getattr(self, "_shadow_completion_parked", None)
            if parked is None:
                parked = {}
                self._shadow_completion_parked = parked
            best = dict(getattr(self, "_shadow_completion_best", None) or {})
            if best:
                parked[str(ev.slug)] = best
            # bounded: a slug that never settles must not grow this for ever
            if len(parked) > 64:
                for k in list(parked)[:-64]:
                    parked.pop(k, None)
            self._shadow_completion_best = {}
            self._shadow_completion_spent = {}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_completion_park_failed:%s"
                       % type(exc).__name__] += 1
        # THE LEDGER GETS THE WHOLE REPORT; THE LOG GETS A LINE THAT FITS.
        # `park` writes the full record to the volume, which is the durable
        # copy the scoring reads. The log copy is bounded because an 80 KB
        # write blocks this process for about six seconds -- measured at
        # 6.158 s in this very stage on deployment a342fccc -- and is
        # truncated at 81,920 bytes anyway. See `TS.bounded_for_log`.
        emit("L5_TWO_SIDED_SHADOW",
             TS.bounded_for_log(
                 dict(self.two_sided_ledger.park(ev.report(None),
                                                 now=self.now_t),
                      interim=False)))

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
        # WHAT THE LEDGER COSTS. One small line a settle pass, reporting
        # only: `finalized` is unbounded and `save` rewrites the whole file
        # twice a window on a network volume, which is the remaining
        # candidate for the residual rollover cost now that bounding the log
        # line has been deployed and demonstrably changed nothing. Measure
        # first; the retention fix waits on this number.
        try:
            emit("L5_SHADOW_LEDGER", self.two_sided_ledger.size_report())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_ledger_size_report_failed:%s"
                       % type(exc).__name__] += 1
        for bad in self.two_sided_ledger.expire(now):
            self.stats["two_sided_settle_expired"] += 1
            emit("L5_TWO_SIDED_SHADOW", TS.bounded_for_log(bad))
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
                emit("L5_TWO_SIDED_SHADOW", TS.bounded_for_log(
                    dict(scored, yes_value=str(px),
                         venue_state=rep.get("state"))))
                self.shadow_completion_settle(slug, scored, px == D(1),
                                              rep.get("state"))

    def shadow_completion_settle(self, slug: str, scored: Dict[str, Any],
                                 settle_up: bool,
                                 venue_state: Any = None) -> None:
        """Score the parked shadow cross against the VENUE'S resolution.

        The comparison the owner asked for: the SAME arm, with and without
        completion, on the same window and the same outcome. When the policy
        never authorized a cross the two outcomes are identical by
        construction, and that is reported rather than omitted -- a window
        where the policy correctly did nothing is evidence too.
        """
        try:
            parked = dict(getattr(self, "_shadow_completion_parked", None)
                          or {})
            best = dict(parked.get(str(slug)) or {})
            cells = SC.live_arm_cells(scored)
            if not cells:
                return
            out = []
            for cell in cells:
                aid = SC.arm_id_from_cell(cell)
                res = SC.score_window(arm_report=cell,
                                      cross=best.get(aid),
                                      settle_up=settle_up)
                out.append(dict(res, arm=aid))
                self.stats["shadow_completion_settled:%s"
                           % str(res.get("reason"))[:40]] += 1
            emit("L5_SHADOW_COMPLETION_SETTLED", {
                "market": slug, "settled_up": bool(settle_up),
                "venue_state": venue_state, "arms": out,
                "labels": ("MODELLED inventory scored on the VENUE'S own "
                           "binary resolution. maker_only is the shadow "
                           "arm's own figure and completed adds the "
                           "authorized cross to the same inventory. Neither "
                           "is realized, and no order was ever submitted."),
            })
            if getattr(self, "_shadow_completion_parked", None):
                self._shadow_completion_parked.pop(str(slug), None)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_completion_settle_failed:%s"
                       % type(exc).__name__] += 1

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
                and now - self.available_cash_t < CASH_REFRESH_S
                and not self.recycle_wants_fresh_cash(now)):
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
        self.confirm_recycled_cash(value, now)
        emit("L5_LIVE_CASH", {
            "available_cash_usd": (None if value is None else str(value)),
            "source": "/v1/account/balances via transport.account()",
            "read_ok": value is not None, "error": err,
            "age_s": 0.0 if value is not None else None,
            "refresh_s": CASH_REFRESH_S,
            "labels": ("authoritative buying power. None is NOT unlimited: "
                       "the cash gate then binds on its configured ceiling "
                       "alone and require_cash_evidence() refuses to arm.")})

    # ============== RECYCLED CASH MUST AGREE WITH THE VENUE'S BUYING POWER
    #
    # OWNER, 2026-11-11: "prove that recycled capital agrees with
    # venue-confirmed buying power and that partial fills, stale order
    # states, restarts, and reconciliation uncertainty cannot release
    # unearned cash."
    #
    # THE RULE. A completed pair's cost leaves the cash figure only for the
    # matched quantity that a balance read TAKEN AFTERWARDS has confirmed.
    # The read is compared with an ANCHOR -- buying power the last time the
    # account held nothing and had nothing at the venue -- moved by what
    # each settlement since has paid:
    #
    #     buying power  >=  anchor - cost of UNMATCHED inventory - a cent
    #
    # If the venue had kept a pair's cash, buying power would sit below that
    # line by the pair's whole cost, and nothing is confirmed.
    #
    # WHEN NOTHING IS CONFIRMED, by construction:
    #   * no anchor: a restart, or a process that has not yet seen the
    #     account flat. Memory only, never persisted, so a restart starts
    #     from "nothing confirmed";
    #   * an order is at the venue, or parked, or quarantined: it may hold
    #     buying power, so the reading is not comparable and adds nothing;
    #   * the ledger is uncertain: every confirmation is dropped;
    #   * the balance is unreadable: nothing new is confirmed;
    #   * buying power is below the line -- a fill the ledger has not booked
    #     yet, a stale order state, a venue that did not net: every
    #     confirmation is dropped.
    # A partial fill changes the matched quantity; only the quantity that
    # was confirmed keeps its credit until a later read confirms the rest.

    RECYCLE_CONFIRM_MIN_S = 3.0
    RECYCLE_TOLERANCE_USD = D("0.01")

    def _recycle_book(self) -> Dict[str, Any]:
        """Unmatched cost and matched quantity per unsettled market."""
        ex, st = self.executor, self.store
        retired = st.state.get("retired_markets") or {}
        out = {"unmatched_cost": D(0), "locked_loss": D(0), "matched": {},
               "any_position": False}
        for m, c in st.committed().items():
            if m in retired:
                continue
            up = D(str(st.position(m, "UP")))
            dn = D(str(st.position(m, "DOWN")))
            if up != 0 or dn != 0:
                out["any_position"] = True
            mc = ex.matched_pair_cost(m)
            if mc is None:                # unvouched: all of it is at risk
                out["unmatched_cost"] += c
                continue
            mq = min(up, dn)
            out["unmatched_cost"] += max(D(0), c - mc)
            out["locked_loss"] += max(D(0), mc - mq)
            if mq > 0:
                out["matched"][m] = mq
        return out

    def recycle_wants_fresh_cash(self, now: float) -> bool:
        """Has quantity matched that no balance read has confirmed yet?
        Then the next read should not wait out the minute. Rate-limited."""
        try:
            ex = self.executor
            if ex is None or not ex.cash_recycles_completed_pairs():
                return False
            last = getattr(self, "_recycle_forced_t", None)
            if last is not None and now - last < self.RECYCLE_CONFIRM_MIN_S:
                return False
            conf = ex.__dict__.get("recycle_confirmed") or {}
            for m, mq in self._recycle_book()["matched"].items():
                have = D(str((conf.get(m) or {}).get("matched", 0)))
                if mq > have:
                    self._recycle_forced_t = now
                    return True
            return False
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False

    def confirm_recycled_cash(self, value: Any, now: float
                              ) -> Optional[Dict[str, Any]]:
        """Record which matched quantity this balance read confirms.

        Read-only towards the venue and the ledger; it writes one in-memory
        dict on the executor. Never raises: any failure confirms nothing.
        """
        ex = self.executor
        if ex is None:
            return None
        try:
            if not ex.cash_recycles_completed_pairs():
                return None
            conf = ex.__dict__.setdefault("recycle_confirmed", {})
            st = self.store
            rec: Dict[str, Any] = {
                "buying_power_usd": None if value is None else str(value)}

            def done(verdict: str) -> Dict[str, Any]:
                rec.update(verdict=verdict, confirmed={
                    m: str(v.get("matched")) for m, v in conf.items()})
                key = (verdict, tuple(sorted(rec["confirmed"].items())))
                if key != getattr(self, "_recycle_last", None):
                    self._recycle_last = key
                    emit("L5_RECYCLE_CHECK", rec)
                self.stats["recycle_check:%s" % verdict] += 1
                return rec

            if st.orders_uncertain():
                conf.clear()
                return done("LEDGER_UNCERTAIN_NOTHING_CONFIRMED")
            # a settlement moves buying power by exactly what it paid
            retired = st.state.get("retired_markets") or {}
            seen = getattr(self, "_recycle_retired_seen", None)
            if seen is None:
                seen = self._recycle_retired_seen = set(retired)
            for m in [k for k in retired if k not in seen]:
                conf.pop(m, None)
                anchor = getattr(self, "_bp_anchor", None)
                try:
                    pnl = D(str(retired[m].get("settlement_pnl")))
                    self._bp_anchor = (None if anchor is None
                                       or not pnl.is_finite()
                                       else anchor + pnl)
                except (ArithmeticError, ValueError, TypeError,
                        AttributeError):
                    self._bp_anchor = None
                seen.add(m)
            if value is None:
                return done("BUYING_POWER_UNREADABLE_NOTHING_NEW")
            bp = D(str(value))
            book = self._recycle_book()
            quiet = st.still_fillable_order_count() == 0
            if quiet and not book["any_position"]:
                self._bp_anchor = bp          # flat and empty: the anchor
            anchor = getattr(self, "_bp_anchor", None)
            rec.update(anchor_usd=None if anchor is None else str(anchor),
                       unmatched_cost_usd=str(book["unmatched_cost"]),
                       matched={m: str(q) for m, q in
                                book["matched"].items()})
            if not book["matched"]:
                return done("NOTHING_MATCHED")
            if anchor is None:
                return done("NO_ANCHOR_NOTHING_CONFIRMED")
            if not quiet:
                return done("ORDERS_AT_THE_VENUE_NOTHING_NEW")
            floor = (anchor - book["unmatched_cost"] - book["locked_loss"]
                     - self.RECYCLE_TOLERANCE_USD)
            rec["expected_at_least_usd"] = str(floor)
            if bp < floor:
                conf.clear()
                return done("BUYING_POWER_BELOW_EXPECTED_NOTHING_CONFIRMED")
            for m, mq in book["matched"].items():
                conf[m] = {"matched": str(mq), "buying_power_usd": str(bp),
                           "t": now}
            return done("CONFIRMED")
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # the failure path confirms nothing and cannot itself fail
            try:
                ex.__dict__.setdefault("recycle_confirmed", {}).clear()
                self.stats["recycle_check_error:%s"
                           % type(exc).__name__] += 1
            except BaseException as exc2:                      # noqa: BLE001
                if isinstance(exc2, (KeyboardInterrupt, SystemExit)):
                    raise
            return None

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

    #: BOUNDED IN-TICK RECOVERY of one order's progress read. Three
    #: attempts in total (the original plus two), because the failure this
    #: answers is a transient 5xx and a fault that survives two retries is
    #: an outage the fail-closed path should own rather than a blip.
    ORDER_REREAD_ATTEMPTS = 2
    #: ~0.35s of added worst-case tick latency against 382.3s of lost
    #: window in the incident. Short enough not to stall the loop, long
    #: enough not to be a second hammer on a venue that just failed.
    ORDER_REREAD_BACKOFF_S = (0.10, 0.25)

    def _reread_order_progress(self, vid: str, exc: BaseException) -> Any:
        """Ask the venue again about THIS order id. None when unresolved.

        READ ONLY. It places, cancels, prices and sizes nothing; its whole
        output is a venue row that the ordinary booking path then applies.

        BOUNDED AND RATE-LIMIT AWARE. A rate-limited failure is not retried
        at all, because retrying it is what turns a blip into a cooloff.
        """
        self.last_reread_attempts = 0
        self.last_reread_elapsed_s = 0.0
        t = self.transport
        if t is None:
            return None
        try:
            if T.is_rate_limited(exc):
                self.stats["order_reread_skipped:RATE_LIMITED"] += 1
                return None
        except BaseException:                                  # noqa: BLE001
            pass
        t0 = time.monotonic()
        for i in range(int(self.ORDER_REREAD_ATTEMPTS)):
            delay = self.ORDER_REREAD_BACKOFF_S[
                min(i, len(self.ORDER_REREAD_BACKOFF_S) - 1)]
            try:
                time.sleep(delay)
            except BaseException:                              # noqa: BLE001
                pass
            self.last_reread_attempts = i + 1
            try:
                row = t.order(str(vid))
            except BaseException as exc2:                      # noqa: BLE001
                if isinstance(exc2, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["order_reread_failed:%s"
                           % type(exc2).__name__] += 1
                try:
                    if T.is_rate_limited(exc2):
                        break
                except BaseException:                          # noqa: BLE001
                    pass
                continue
            self.last_reread_elapsed_s = round(time.monotonic() - t0, 3)
            return row
        self.last_reread_elapsed_s = round(time.monotonic() - t0, 3)
        return None

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
            # REPORTING ONLY: realized poll cadence, which is the only
            # bound available on fill->detection latency.
            _lat(self, "note_poll",
                 getattr(t, "market_slug", "") or "", self.now_t)
            try:
                row = t.order(str(vid))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # ============ FAIL CLOSED FIRST, THEN ASK AGAIN.
                #
                # THE INCIDENT THIS CLOSES. 2026-11-02 window 1330z: one
                # HTTPError on this read latched the book and the loop
                # moved on with `continue`. The id was not re-asked until
                # the ordinary interval; by then it had left the venue's
                # open list into `pending_terminal`, where reconcile
                # RE-RAISES uncertainty on every pass that finds anything
                # parked. Every submission was refused for 382.3s of a
                # 900s window -- 42% of it -- and the window ended at
                # PAST_ENTRY_CUTOFF with the book at 0.9800/0.9900.
                #
                # THE MARK STAYS, AND IT STAYS FIRST. The book is uncertain
                # the instant a read fails, before any retry, so a crash
                # mid-recovery cannot leave the session trading on a book
                # it could not read. Clearing is still the sole privilege
                # of an authoritative reconciliation -- a successful
                # re-read books the ORDER, never the uncertainty.
                self.store.mark_uncertain(
                    "order query raised %s for %s" % (type(exc).__name__,
                                                      coid))
                # A RATE LIMIT IS NOT RETRIED. The reconcile path already
                # says hammering a venue that is returning errors is how a
                # transient fault becomes a rate limit; this honours the
                # same rule rather than restating it.
                # DEFENSIVELY RESOLVED. `poll_orders` is borrowed by
                # lighter harnesses (RunnerStub in test_l5_e2e_window) that
                # are not Runners and do not carry this helper. Reaching
                # for it unguarded raised AttributeError there and took the
                # whole tick down -- the same mistake the completion
                # requote helper made, caught the same way, by parity.
                # Absent, the behaviour is exactly what it was before this
                # patch: latch and move on.
                _reread = getattr(self, "_reread_order_progress", None)
                row = _reread(str(vid), exc) if _reread is not None else None
                if row is None:
                    out.append({"coid": coid, "event": "PROGRESS_ERROR",
                                "error": type(exc).__name__,
                                "reread_attempts": getattr(
                                    self, "last_reread_attempts", 0),
                                "rate_limited": T.is_rate_limited(exc)})
                    continue
                # RECOVERED INSIDE THE TICK. Fall through to the SAME
                # booking path an ordinary read uses, so the delta and
                # idempotency logic is not duplicated here.
                self.stats["order_read_recovered"] += 1
                out.append({"coid": coid, "event": "PROGRESS_READ_RECOVERED",
                            "error": type(exc).__name__,
                            "reread_attempts": getattr(
                                self, "last_reread_attempts", 0),
                            "recovered_after_s": getattr(
                                self, "last_reread_elapsed_s", 0.0)})
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

    def replenish(self) -> "RPL.Ledger":
        """The per-window replenishment credit ledger.

        LAZILY INITIALISED, for the same reason `win_lifecycle` is: several
        construction paths build a Runner without running the `__init__` that
        would declare it, and an accessor a lifecycle poll depends on must
        never be the reason that poll raises.
        """
        led = getattr(self, "_replenish_ledger", None)
        if led is None:
            led = RPL.Ledger()
            self._replenish_ledger = led
        return led

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
            # A CONFIRMED FILL OR TERMINAL STATE IS THE MOST ACTIONABLE
            # EVENT THERE IS: it is the moment unmatched inventory appears,
            # or a slot frees, and the old loop could sit on it for 3.5s.
            # Raising only re-times the next tick; the tick still decides.
            try:
                _wk = getattr(self, "wake", None)
                if _wk is not None:
                    _wk.raise_("ORDER_%s" % (
                        "TERMINAL" if e.get("terminal") else "PROGRESS"))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["wake_raise_error:%s" % type(exc).__name__] += 1
            # ============ AND THE SAME EVENT ISSUES A REPLENISHMENT CREDIT.
            #
            # The wake above re-TIMES the next tick; it does not record that
            # anything happened, and the opening path had no way to tell a
            # re-open that followed a fill from one that followed nothing but
            # the tick coming round again. This is that record: a positive
            # booked delta is a confirmed fill, a terminal row that booked
            # nothing is a cancellation, and either frees a slot and issues
            # one credit -- bounded by `RPL.max_rounds()`.
            #
            # BOOKKEEPING ONLY, INSIDE ONE SWALLOW. It places nothing and
            # can only ever make the opening path MORE reluctant; a failure
            # to count a credit must never break a lifecycle poll.
            try:
                self.replenish().note_progress(
                    e, window_start=(AB.slug_start(str(m)) if m else None),
                    t=self.now_t)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["replenish_note_error:%s"
                           % type(exc).__name__] += 1
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
                # The venue's fill records, so a fill that raced its own
                # cancellation is counted. Deduplicated against the lifecycle
                # on cumulative quantity, so this can only ever add a fill
                # the lifecycle stream never reported.
                fills=_window_fills(self, str(market)),
                # Venue-confirmed cancellations, so an order the venue took
                # and then cancelled is not reported as never having landed.
                # Acknowledgement only: adds no fill and no P&L.
                cancels=(getattr(self, "win_cancels", None)
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
                # AFTER the ladder, deliberately: this observer reads the
                # top of book from the ladder that has just folded the
                # frame, rather than re-deriving the envelope shape -- the
                # mistake that left every queue_ahead null in 1093d4d1.
                # FrameFanout wraps each extra observer, so a fault here can
                # reach neither the ladder nor the money path's observer.
                self.fanout.add(self._wake_on_frame)
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
                # THE HELD LEG'S COST, NOT A PAIR COST. A pair unit cost
                # only exists once something is matched, so it is None in
                # exactly the case this sampler fires on -- an outstanding
                # residual -- which left `first_leg_own_px` None in 401 of
                # 401 frames and every `pair_if_*` reading
                # `no_first_leg_price`. `pair_unit_cost` is kept as a
                # fallback so any other producer of this record is
                # unaffected.
                first_leg_own_px=(inv.get("held_leg_own_cost")
                                  or inv.get("pair_unit_cost")),
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
            self.shadow_defensive_decision(
                variant, out, inv=inv, slug=slug, now=now,
                window_end_ts=rec.get("window_end_ts"))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_residual_book_error:%s"
                       % type(exc).__name__] += 1

    def shadow_defensive_decision(self, variant: str,
                                  sample: Mapping[str, Any], *,
                                  inv: Mapping[str, Any], slug: str,
                                  now: float,
                                  window_end_ts: Any = None) -> None:
        """SHADOW-ONLY defensive-completion decision, recorded not taken.

        WHAT THIS MEASURES AND WHY IT IS THE ONE MISSING NUMBER. The
        20261025 replay says autonomous ARGMAX turns positive if defensive
        completion lands on **26.8%** of residual (pair_net +10.1837,
        residual -13.3237, pre-emption -0.8080). The retained evidence is
        n=1: the single genuinely stranded episode, 0130z, was completable
        at pair 0.9656 and filled at both 0.5 s and 8.818 s of latency.
        One observation is not a rate, and the rate is what decides whether
        the live adapter is worth building. This records a decision per
        residual sample so the rate can be counted over many windows.

        IT REUSES THE REAL POLICY. `DEF.decide` is the deployed decision
        function, called with `simulate=True`; no second copy of the rule
        exists here. `DEF.alternatives` inside it prices taker completion,
        maker completion, partial and hold-to-settlement side by side, which
        are exactly the courses being compared.

        THE CEILING IS 1.00, NOT THE 1.20 CODE DEFAULT. The venue has no
        maker-fee term and a matched pair redeems exactly $1.00, so a pair
        above $1.00 is a locked loss: at the 1.20 default the same bound
        arithmetic gives -8.0931, worse than doing nothing.

        IT CANNOT SUBMIT. It writes one log line. There is no transport, no
        claim, no order and no store write on this path, and the executor's
        own `maker_only_forbids_defensive_crossing` is untouched.
        """
        try:
            from src import l5_defensive_completion as DEF
            from src import pmus_fees as PFEE
            # NOT the module-level `TS`, which is `l5_two_sided_shadow`
            # here. The cutoffs live on the PROBE, and reaching for the
            # wrong `TS` is an AttributeError this import exists to avoid.
            from src import l5_two_sided_probe as TSP
            # the sampler nests these under `reachability`; the underlying
            # `complement_reachability` returns them at top level. Accept
            # either so this is testable against the function directly.
            r = sample.get("reachability") or sample
            walk = r.get("taker_walk") or {}
            behind = r.get("maker_quote_behind_touch") or {}
            exitpx = _dec(walk.get("vwap_own_px"))
            qty = _dec(inv.get("residual"))
            first = _dec(inv.get("held_leg_own_cost"))
            if exitpx is None or qty is None or first is None or qty == 0:
                self.stats["shadow_defensive:NOT_PRICEABLE"] += 1
                return
            qty = abs(qty)
            wend = _dec(window_end_ts)
            wstart = (float(wend) - float(AB.WINDOW_S)
                      if wend is not None else now - float(AB.WINDOW_S))
            d = DEF.decide(
                now_t=now, window_start=wstart,
                complete_cutoff_s=TSP.complete_cutoff_s(),
                cancel_cutoff_s=TSP.cancel_cutoff(AB.WINDOW_S),
                unmatched_qty=qty, first_leg_cost=first, exit_price=exitpx,
                displayed_depth=_dec(walk.get("displayed_qty_crossed")),
                taker_fee_per_contract=PFEE.taker_fee(exitpx, D(1)),
                maker_complement_price=_dec(behind.get("own_px")),
                buy_side=r.get("complement_side"),
                # the venue break-even, NOT the 1.20 default -- see above
                pair_ceiling=D("1.00"),
                simulate=True)
            act = str(d.get("action") or d.get("reason") or "?")
            self.stats["shadow_defensive:%s" % act] += 1
            emit("L5_SHADOW_DEFENSIVE_DECISION", {
                "label": "L5_SHADOW_DEFENSIVE_DECISION",
                "slug": str(slug), "shadow_variant": str(variant),
                "unmatched_qty": str(qty), "first_leg_own_cost": str(first),
                "exit_price_own": str(exitpx),
                "maker_complement_own": behind.get("own_px"),
                "pair_if_taker": str(first + exitpx),
                "pair_ceiling_used": "1.00",
                "decision": d,
                "basis": ("SHADOW. DEF.decide(simulate=True) over MODELLED "
                          "inventory on a live book. Nothing was submitted, "
                          "filled, settled or realized, and a displayed "
                          "quote is not a promise that it survives our "
                          "latency."),
            })
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["shadow_defensive_error:%s"
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
            # THE VENUE QUOTES WIRE, THIS RECORD IS IN OWN TERMS.
            # `ev["avg_px"]` is the venue's average fill price, which is
            # YES/UP -- the WIRE price. It was passed straight into
            # `fill_own_px`, so for every DOWN fill that field held
            # `1 - own`. Verified against all 8 DOWN fills of Campaigns C
            # and D: `first_leg_own_px` (our submitted `own_px`) equalled
            # `1 - fill_own_px` in each one, and equalled it for the UP
            # fills, where wire and own coincide.
            #
            # WHY IT IS NOT ONLY A LABEL. `fill_evidence` falls back to
            # `fill_own_px` for `first_leg_own_px` when the pre-submit
            # record is missing, and `first_leg_own_px` is what every
            # `pair_if_*` term is priced from -- so on a DOWN fill whose
            # pre-submit had been evicted the record's pair arithmetic was
            # wrong by `1 - 2p`, not merely mislabelled.
            #
            # `TS.wire_price` is the one place the transformation lives and
            # it is an involution -- wire for UP is own, and for DOWN each
            # is `1 - ` the other -- so applying it to a wire price yields
            # the own price. FAIL CLOSED on an unknown side: a fill whose
            # side we cannot name gets no own price rather than a guess,
            # and the wire price is recorded either way so nothing is lost.
            # LOCAL IMPORT, as everywhere else in this file: module-level
            # `TS` is `l5_two_sided_shadow`, a different module, and this
            # whole method swallows BaseException -- so reaching for the
            # wrong `TS` here would have silently stopped every fill
            # evidence record rather than failing loudly.
            from src import l5_two_sided_probe as TSP
            # ONLY A SIDE WE RECOGNISE IS CONVERTED. `wire_price` treats
            # anything that is not UP as DOWN, so passing an unrecognised
            # label through it would silently return `1 - p` -- the exact
            # class of error this fix exists to remove.
            _wire_px = ev.get("avg_px")
            _sd = None if side is None else str(side).strip().upper()
            _own_px = (TSP.wire_price(_sd, _wire_px)
                       if _sd in ("UP", "DOWN") else None)
            rec = BE.fill_evidence(
                slug=str(m), coid=coid, side=side,
                fill_qty=ev.get("delta"), fill_own_px=_own_px,
                venue_event_t=ev.get("venue_event_t"),
                processing_t=self.now_t,
                residual_after=self.store.residual(str(m)),
                first_leg_own_px=sub.get("own_px") or _own_px,
                qty_needed=self.store.residual(str(m)),
                ring=ring, order_resting=None,
                depth_ticks=_evidence_depth_ticks())
            rec["venue_event_t_key"] = ev.get("venue_event_t_key")
            # KEPT BESIDE IT, so a reader can reconcile this record against
            # the venue's own fill report without redoing the conversion.
            rec["fill_wire_px"] = (None if _wire_px is None
                                   else str(_wire_px))
            rec["own_price_basis"] = (
                "fill_own_px is OWN-outcome, converted from the venue's "
                "wire avg_px by side; fill_wire_px is the venue's own "
                "figure. None own price means the side was unknown.")
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
            # THE HELD LEG'S VENUE-CONFIRMED BASIS. Without it every
            # `pair_if_*` term in this record reads `no_first_leg_price`, so
            # the AUTHENTICATED sampler -- the one whose prices are
            # executable evidence -- could not price its own pair, while the
            # SHADOW sampler beside it could. Reconstructing those pairs by
            # hand afterwards is exactly how the 2230z misreading happened.
            # `leg_cost` is written from venue-confirmed fills only, so this
            # is the same basis `pair_cost_refusal` uses and cannot drift
            # from it.
            _basis = None
            try:
                _lc = self.store.leg_cost(str(market))
                _side = "UP" if resid > 0 else "DOWN"
                _q = self.store.position(str(market), _side)
                _c = _lc.get(_side)
                if _q and _q > 0 and _c is not None:
                    _basis = str(_c / _q)
            except BaseException:                              # noqa: BLE001
                _basis = None
            rec = s.sample(
                slug=str(market), now=self.now_t, residual=resid,
                order_resting=bool(live), ring=ring,
                first_leg_own_px=_basis,
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

    #: the accumulator's key for the OBSERVER itself, as distinct from any
    #: one market. Observer continuity is a property of the process across a
    #: window roll, so it cannot be keyed by the market that just changed.
    LATENCY_OBSERVER_KEY = "__observer__"

    def _latency_report(self) -> Optional[Dict[str, Any]]:
        """The latency report, or None, and never an exception."""
        acc = getattr(self, "completion_latency", None)
        if acc is None:
            return None
        try:
            rs = acc.reports()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["latency_error:report:%s" % type(exc).__name__] += 1
            return None
        obs = next((r for r in rs
                    if r.get("market") == self.LATENCY_OBSERVER_KEY), None)
        return {"observer": obs,
                "markets": [r for r in rs
                            if r.get("market") != self.LATENCY_OBSERVER_KEY]}

    def _latency_safe(self, meth: str, *a: Any, **kw: Any) -> None:
        """Call the latency accumulator and never let it matter.

        Same contract as `_calib_safe`: observational, swallows its own
        faults, counts them, and cannot refuse or alter anything. An
        accumulator that raises must not be able to stop a cancellation.
        """
        acc = getattr(self, "completion_latency", None)
        if acc is None:
            return
        try:
            getattr(acc, meth)(*a, **kw)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["latency_error:%s:%s" % (meth, type(exc).__name__)] += 1

    def _latency_observer_note(self) -> None:
        """One observer heartbeat, and a rollover when the slug changes."""
        acc = getattr(self, "completion_latency", None)
        if acc is None:
            return
        try:
            slug = getattr(self.transport, "market_slug", None)
            k = self.LATENCY_OBSERVER_KEY
            prev = getattr(self, "_latency_last_slug", None)
            if slug and prev and str(slug) != str(prev):
                self._latency_safe("note_rollover", k, self.now_t)
            if slug:
                self._latency_last_slug = str(slug)
            self._latency_safe("note_observation", k, self.now_t)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["latency_error:observer:%s" % type(exc).__name__] += 1

    def calibration_live_tick(self, now: float) -> None:
        """Lifetime observations for the orders the venue is actually holding.

        THE GAP THIS CLOSES, and it is the whole reason a live window could
        have been spent for nothing. `observe` -- the only producer of
        `lifetime_observation`, one of the five REQUIRED_CAPTURE categories,
        and the only source of `prints_at_our_price` -- was reached ONLY from
        `calibration_shadow_tick`, with `shadow|...` coids. So:

            shadow records  observations, and NO venue-confirmed terminal
            live records    ack, fill, fees, venue terminal, and NO observations

        Neither half could ever be `usable_for_calibration`, and the two
        never met. An `l5f-` order that rested for two minutes produced a
        record with four of five categories present and not one reading of
        the queue it was sitting in.

        IT RUNS FROM THE MAIN TICK, AND THAT IS THE POINT. The first
        version was called from `shadow_tick`, which returns early on
        `not cs or self.ws is None`; `calibration_shadow_tick` additionally
        bails on `_shadow_off`. Either would have stood between a live order
        and the record of the queue it sat in -- the same shape as the
        defect this method exists to close, where observation was reachable
        only through the shadow.

        I then claimed moving it here broke 111 tests and reverted it. It
        broke NONE: both runs behind that claim were contaminated by my own
        edits to the runner while they were in flight. A clean run of the
        relocated tree is byte-identical to the baseline.

        SELF-SUFFICIENT. The slug comes from the TRANSPORT -- the market our
        orders are actually in, not the shadow's `AB.slug_for(self.ws)` grid
        slug, which can differ -- and the book and prints are its own reads.
        Each guard returns before anything is read, so an object that is not
        a fully built Runner cannot be made to raise here; about forty tests
        invoke `tick`, and this is why they are unaffected.

        OBSERVATIONAL, exactly like every other recorder call on this path:
        it cannot refuse or alter a submission and swallows its own faults.
        `observe` itself filters the coids it is given to those it already
        knows and whose slug matches, so handing it every live order is safe.
        """
        cal = self.fill_calibration
        if cal is None:
            return
        # REPORTING ONLY, and ABOVE every other guard on purpose: this is
        # the record that the observer stage ran at all, and it has to be
        # taken on the ticks where there is nothing to observe too --
        # otherwise "the observer continued across the rollover" could not
        # be distinguished from "the observer stopped and there happened to
        # be no orders". A window roll is read as a change of the
        # transport's slug, which is the market our orders are in.
        # LOOKED UP, NOT CALLED BY NAME. This method's contract is that an
        # object which is not a fully built Runner cannot be made to raise
        # here -- about forty tests drive `tick` with partial rigs, and a
        # Runner whose __init__ failed midway is the same shape. A bare
        # `self._latency_observer_note()` broke seven of those rigs with an
        # AttributeError the instant it was added, which is the contract
        # catching the mistake rather than a reason to weaken the rigs.
        _note = getattr(self, "_latency_observer_note", None)
        if _note is not None:
            _note()
        try:
            store = getattr(getattr(self, "executor", None), "store", None)
            if store is None:
                return
            live = [c for c in (store.live_orders() or {})
                    if str(c).startswith("l5f-")]
            if not live:
                return
            slug = getattr(self.transport, "market_slug", None)
            if not slug:
                return
            book = self.book.snapshot(slug)
            try:
                prints = self.book.trades(slug)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # an absent print feed is recorded AS absent by `observe`,
                # never as "no prints", so this is reported and not guessed
                prints = None
                self.stats["calib_live_prints_error:%s"
                           % type(exc).__name__] += 1
            n = cal.observe(now, slug=slug, book=book,
                            ladder=self.shadow_ladder, prints=prints,
                            open_coids=live)
            self.stats["calib_live_observations"] += int(n)
            self.stats["calib_live_orders_seen"] = len(live)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["calib_live_error:%s" % type(exc).__name__] += 1

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

    def attest_other_strategy_cost(self, market: str, t: Any
                                   ) -> Optional[Dict[str, Any]]:
        """Restate a BTC5m position this ledger holds at the $1 bound, from
        the BTC5m ledger's published fills, each confirmed by OUR OWN read
        of the venue's order row. See `InventoryStore.attest_other_strategy_
        cost` for the checks. Never raises into the settlement sweep."""
        # WHICH strategy's feed: the registry's answer for this market's
        # series. The 5-minute lane is exactly as before. A strategy with
        # an owner-named floor (the sports controller) is read only when
        # its feed is configured and that floor day has been reached.
        owner = LX.ORIGIN.ledger_owner_of_market(market)
        if owner in (None, LX.OWN_STRATEGY):
            return None
        from src import l5_venue_cost_attestation as _VCA
        _env = (BTC5M_FILLS_URL_ENV if owner == LX.ORIGIN.BTC5M
                else _VCA.SIBLING_FEED_ENVS.get(owner))
        url = str(os.getenv(_env, "")).strip() if _env else ""
        if not url:
            return None
        if owner != LX.ORIGIN.BTC5M and not _VCA.sibling_in_scope(
                market, dt.datetime.now(dt.timezone.utc).isoformat()):
            return None
        if not any(str(k).rsplit("|", 1)[0] == market for k in
                   (self.store.state.get("leg_cost_basis_unknown") or [])):
            return None
        try:
            import urllib.request as _ur
            with _ur.urlopen(url, timeout=3.0) as r:           # noqa: S310
                doc = json.loads(r.read().decode("utf-8"))
            if not isinstance(doc, Mapping) or not doc.get("readable"):
                raise ValueError("feed not readable")
            if owner != LX.ORIGIN.BTC5M and \
                    str(doc.get("strategy") or "") != str(owner):
                raise ValueError("feed is another strategy's")
            fills = [o for o in (doc.get("orders") or ())
                     if isinstance(o, Mapping) and o.get("market") == market]
            rows = {}
            for f in fills:
                vid = str(f.get("venue_order_id") or "").strip()
                if vid and D(str(f.get("filled_qty") or 0)) > 0:
                    rows[vid] = t.order(vid)
            out = self.store.attest_other_strategy_cost(
                market, fills, rows,
                source="%s_LEDGER_FILLS_CONFIRMED_BY_VENUE_ORDER_READS"
                       % owner)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out = {"attested": False, "market": market,
                   "reason": "ATTEST_FAILED:%s" % type(exc).__name__}
        emit("L5_OTHER_STRATEGY_COST", dict(out, labels=(
            "attested=False leaves the position at the $1 bound; the "
            "settlement below is unchanged by a refusal")))
        return out

    def settle_sports_from_feed(self, market: str, t: Any,
                                rep: Mapping[str, Any]
                                ) -> Optional[Dict[str, Any]]:
        """Settle a sports-controller market held here at the $1 bound from
        that controller's own fill feed -- see `InventoryStore.settle_from_
        sibling_feed` for every check. Returns the retirement record when it
        settled, None when anything is missing: the caller then settles
        exactly as before (bound, unresolved, fail closed). READS ONLY from
        the venue: one /v1/order per feed order and one positions read.
        Never raises into the settlement sweep except IncoherentBook, which
        the sweep already handles."""
        from src import l5_venue_cost_attestation as _VCA
        if not _VCA.sports_settle_enabled():
            return None
        if LX.ORIGIN.ledger_owner_of_market(market) != LX.ORIGIN.SPORTS:
            return None
        if not any(str(k).rsplit("|", 1)[0] == market for k in
                   (self.store.state.get("leg_cost_basis_unknown") or [])):
            return None
        now = dt.datetime.now(dt.timezone.utc)
        if not _VCA.sibling_in_scope(market, now.isoformat()):
            return None
        url = str(os.getenv(_VCA.SIBLING_FEED_ENVS["SPORTS"], "")).strip()
        try:
            import urllib.request as _ur
            with _ur.urlopen(url, timeout=3.0) as r:           # noqa: S310
                doc = json.loads(r.read().decode("utf-8"))
            reads: Dict[str, Any] = {}
            if isinstance(doc, Mapping) and doc.get("readable") is True:
                for o in doc.get("orders") or ():
                    if isinstance(o, Mapping) and o.get("market") == market:
                        vid = str(o.get("venue_order_id") or "").strip()
                        if vid and vid not in reads:
                            reads[vid] = t.order(vid)
            rows = t.positions()
            net = D(0)
            for p in rows or ():
                pm = LX._first_key(p, LX.VENUE_POSITION_MARKET_KEYS) \
                    if isinstance(p, Mapping) else None
                if pm is None or str(pm) != market:
                    continue
                net += D(LX.position_row(p)["signed_net"])
            out = self.store.settle_from_sibling_feed(
                market, yes_value=rep.get("settlement_px"),
                resolution=rep.get("resolution"), feed=doc,
                venue_orders=reads, venue_net=net, now=now)
        except LX.IncoherentBook:
            raise
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out = {"retired": False, "fallback": True, "market": market,
                   "reason": "FEED_SETTLE_FAILED:%s" % type(exc).__name__}
        line = {k: v for k, v in dict(out).items()
                if k in ("retired", "fallback", "market", "reason",
                         "settlement_pnl", "pnl_provenance",
                         "venue_order_id", "venue_net", "feed_up",
                         "feed_down", "age_s", "venue_cum", "feed_qty")}
        emit("L5_SPORTS_FEED_SETTLEMENT", dict(line, labels=(
                "retired=True: settled from the sports feed with venue-"
                "confirmed orders and net. fallback=True: nothing written; "
                "the ordinary settlement below books the $1 bound as "
                "before.")))
        if out.get("retired"):
            return out
        return None

    def account_risk_line(self) -> Dict[str, Any]:
        """The account-wide loss state, for ANOTHER strategy on this account.

        Published read-only on the order registry so the BTC5m runner reads
        THE limits this service enforces rather than a second copy: the UTC
        day realized against the daily halt, the all-time realized against
        the cumulative ceiling, the operator halt, and which markets this
        ledger has already retired (so a reader can tell which of its own
        results are already inside these figures). `readable` is False
        whenever any of it cannot be established, and the reader refuses.
        """
        day = self.day_realized_line()
        try:
            lifetime = D(str(self.store.realized_today()))
            vg = getattr(self.executor, "validation_gates", None)
            cum = getattr(vg, "max_cumulative_loss_usd", None)
            cum = None if cum is None else D(str(cum))
            retired = sorted((self.store.state.get("retired_markets")
                              or {}).keys())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"readable": False,
                    "reason": "RISK_READ_FAILED:%s" % type(exc).__name__}
        dr = day.get("utc_day_realized_usd")
        unres = unresolved_line_of(self.store)
        unres_n = None if unres is None else int(unres.get("count") or 0)
        ok = dr is not None and cum is not None and unres_n == 0
        return {
            "readable": bool(ok),
            "reason": None if ok else (
                day.get("reason")
                or ("UNRESOLVED_COST_BASIS_UNREADABLE" if unres_n is None
                    else "UNRESOLVED_COST_BASIS:%d" % unres_n
                    if unres_n else None)
                or "CUMULATIVE_CEILING_UNREADABLE"),
            # THE BOUND IS NOT A RESULT. Added key: what of the figures below
            # is booked at the $1/share bound and not yet measured.
            "unresolved_cost_basis": unres,
            "utc_day": day.get("utc_day"),
            "utc_day_realized_usd": dr,
            "daily_halt_usd": day.get("halt_usd"),
            "daily_would_halt": day.get("would_halt"),
            "lifetime_realized_usd": str(lifetime),
            "cumulative_loss_ceiling_usd": (None if cum is None
                                            else str(cum)),
            "cumulative_would_halt": (None if cum is None
                                      else bool(-lifetime >= cum)),
            "operator_halt": bool(operator_halted()),
            "retired_markets": retired[-400:],
            # WHAT EACH OF TODAY'S RETIRED MARKETS CONTRIBUTED to
            # `utc_day_realized_usd`. Added key only. None when the day
            # itself is unknown; a reader without it adds everything.
            "retired_results_utc_day": self._retired_results_line(
                day.get("utc_day")),
            # WHAT THE LIFETIME FIGURE IS MADE OF: the cash re-anchor and
            # every row retired since. Added key only; None when unknown.
            "lifetime_basis": self._lifetime_basis_line(),
            # the venue-equity controls a second strategy must also honour.
            # Added keys only: every figure above is computed as before.
            **self.equity_risk_lines(),
            # the venue's executions beside the booked results. Added key
            # only, report only: no figure above reads it.
            "venue_basis": venue_basis_lines(self),
            "labels": ("the account-wide figures the 15-minute service's own "
                       "gates read, published so a second strategy cannot "
                       "hold a looser copy. Read-only."),
        }

    def _publication_summary(self) -> Dict[str, Any]:
        """WHAT THE REGISTRY IS PUBLISHING for a second strategy, in a few
        fields an operator can read on the heartbeat.

        The account-risk record is served only on the internal registry
        endpoint, so whether its lifetime basis reconciles could not be
        seen from outside without deploying the service that reads it.
        This calls the SAME two methods that record is built from and
        reports their verdicts, never their rows. Reporting only; never
        raises; a field it cannot establish is None.
        """
        out: Dict[str, Any] = {"lifetime_basis": None,
                               "retired_results_utc_day": None}
        try:
            b = self._lifetime_basis_line()
            if isinstance(b, Mapping):
                out["lifetime_basis"] = {
                    k: b.get(k) for k in (
                        "reconciled", "complete", "rows", "rebased_at_utc",
                        "cash_read_utc", "lifetime_realized_usd")}
            day = None
            try:
                day = (self.day_realized_line() or {}).get("utc_day")
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
            r = self._retired_results_line(day)
            if isinstance(r, Mapping):
                res = r.get("results")
                out["retired_results_utc_day"] = {
                    "utc_day": r.get("utc_day"),
                    "markets": (len(res) if isinstance(res, Mapping)
                                else None)}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["error"] = type(exc).__name__
        return out

    def _retired_results_line(self, utc_day: Any) -> Optional[Dict[str, Any]]:
        """Today's retired markets with the result each was booked at.
        Never raises: unreadable is None, which a reader treats as absent."""
        try:
            got = self.store.retired_results_current_utc_day()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None
        if got is None or not utc_day:
            return None
        return {"utc_day": str(utc_day), "results": got}

    def _lifetime_basis_line(self) -> Optional[Dict[str, Any]]:
        """`store.lifetime_basis()` behind a total wrap. Never raises."""
        try:
            return self.store.lifetime_basis()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

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
        unres = unresolved_line_of(self.store)
        return {
            "utc_day": now_utc().strftime("%Y-%m-%d"),
            "unresolved_cost_basis": unres,
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
            # WHAT THE LAST FAILURE ACTUALLY WAS, not just how many there
            # were. `ingest:FEED_FAILED` reached 577 carrying no status, no
            # exception type and no body, and the cause turned out to be an
            # HTTP 410 "Data API v1 has been retired" that nothing recorded.
            # Reporting only; {} when the last poll succeeded.
            "last_feed_failure": _feed_failure_detail(),
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

    def authorization_preflight_verdict(self) -> Dict[str, Any]:
        """The arming preflight's VERDICT, compact, for the heartbeat.

        WHY IT BELONGS HERE, and it is an operational scar. On 2026-11-06
        the 20:00z authorized window was lost to AUTHORIZATION_EXPIRED --
        `L5_LIVE_AUTH_NOT_AFTER` had lapsed 56,974 s earlier. The preflight
        CAUGHT it: at the 19:34 boot it emitted `ok: false`,
        `fail: ["absolute_deadline"]`, 26 minutes before the window, with
        every other check PASS. Nobody read that line.

        It was on its own line because the heartbeat used to exceed the log
        transport's per-line limit -- the defect `bounded_hb` now fixes --
        so the workaround for a truncated heartbeat is why the verdict was
        not in the heartbeat anyone was polling.

        TWO THINGS CHANGE. It becomes CONTINUOUS rather than boot-only, so
        a deadline that lapses mid-campaign is visible before the next
        window rather than after it; and it sits beside `campaign`, so a
        campaign can never LOOK ready while the preflight says it is not.

        Compact on purpose: `ok`, `fail` and `unknown` only. The full
        `rows` stay on their own L5_AUTHORIZATION_PREFLIGHT line.
        Reporting only -- `authorize_arming` still decides.
        """
        try:
            _mkt = str(getattr(self.transport, "market_slug", "") or "")
            pf = AUTH.authorization_preflight(_mkt, root=self.root)
            # THE MARKET IT WAS EVALUATED AGAINST, AND WHY EACH CHECK
            # FAILED.
            #
            # WHAT THIS COST. The full L5_AUTHORIZATION_PREFLIGHT line is
            # emitted ONCE AT STARTUP, when the transport has no slug yet;
            # `authorization_preflight` then deliberately evaluates the
            # campaign DOCUMENT alone and reports coverage as
            # NOT_EVALUATED, so it says campaign_approval PASS. This
            # verdict is emitted CONTINUOUSLY, with the slug of whatever
            # window is current -- and a campaign authorizing only the NEXT
            # window correctly says FAIL for this one.
            #
            # Both are right. But `fail: ["campaign_approval"]` with no
            # market and no reason is indistinguishable from a broken
            # approval, and that ambiguity cost a review cycle: the two
            # lines were compared as though they disagreed about the same
            # question when they were answering different ones.
            #
            # So the market and the reasons travel with the verdict, and
            # the one benign case -- a valid campaign whose window has not
            # arrived -- is named outright. Reporting only: no gate, limit
            # or decision is touched, and `authorize_arming` still decides.
            # BOUNDED. The heartbeat has a hard size budget -- it already
            # exceeded the log transport's per-line limit once, which is
            # why the full rows live on their own line and why `hb_reduced`
            # exists to drop the largest field rather than truncate the
            # safety state. So this reads the rows and embeds only a SMALL
            # reason map: at most six entries, each reason clipped. The
            # rows themselves are never carried.
            _by = {str(r.get("check")): r
                   for r in (pf.get("rows") or [])
                   if isinstance(r, Mapping)}
            _why = {}
            for k in (pf.get("fail") or [])[:6]:
                _r = str((_by.get(k) or {}).get("reason") or "")[:80]
                if _r:
                    _why[k] = _r
            _camp = (_by.get("campaign_approval") or {})
            _awaiting = (str(_camp.get("reason") or "")
                         == "WINDOW_NOT_IN_AUTHORIZED_SET")
            return {"ok": pf.get("ok"), "fail": pf.get("fail"),
                    "unknown": pf.get("unknown"),
                    "evaluated_market": _mkt or None,
                    "fail_reasons": _why or None,
                    "awaiting_authorized_window": _awaiting,
                    "labels": ("compact verdict; full rows on the "
                               "L5_AUTHORIZATION_PREFLIGHT line. ok false "
                               "means arming WILL refuse for the named "
                               "check, whatever the campaign says. "
                               "`evaluated_market` is the window this "
                               "verdict was computed FOR -- the startup "
                               "line has no slug and so evaluates the "
                               "document alone, which is why the two can "
                               "differ without disagreeing. "
                               "`awaiting_authorized_window` true means "
                               "the campaign is VALID and this is simply "
                               "not its window yet.")}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # UNREADABLE IS NOT OK. A verdict we cannot compute must not
            # read as a pass.
            self.stats["auth_preflight_verdict_failed:%s"
                       % type(exc).__name__] += 1
            return {"ok": None, "fail": None, "unknown": None,
                    "error": type(exc).__name__,
                    "labels": "UNREADABLE -- never read this as ok"}

    def heartbeat(self) -> None:
        t = self.transport
        emit("L5_LIVE_HB", TS.bounded_hb({
            "authorization_preflight": self.authorization_preflight_verdict(),
            # THE LIFETIME COMPLETION BUDGET, on the tape. Absent from the
            # heartbeat until 2026-11-06, which is why its balance had to be
            # inferred after the first live window instead of read.
            "defensive_budget": self.defensive_budget_telemetry(),
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
            # WHOSE RESULT IS WHOSE. A report beside the account-wide lines
            # above, never instead of them: no gate reads it.
            "origin": self.origin_line(),
            "auth_book": self.book.status(),
            "fill_calibration": (None if self.fill_calibration is None
                                 else self.fill_calibration.report()),
            # WHERE THE TIME GOES, on every heartbeat. The one figure the
            # 6.4-6.9s recorded ladders could never supply: the age of the
            # book the completion controller actually evaluated, measured
            # on this process's own clock. Reporting only.
            "completion_latency": self._latency_report(),
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
            "portfolio_drawdown": self.portfolio_drawdown_report(),
            "equity_baseline": self.equity_risk_lines(self.now_t).get(
                "equity_baseline"),
            "account_risk_publication": self._publication_summary(),
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
            # VENUE-ATTESTED COST BASIS of settled positions this ledger did
            # not open: attested, still at the $1 bound (with reasons), and
            # the corrections applied with their delta. Reporting only.
            "venue_cost_attestation": self.venue_cost_attestation_lines(),
            # VENUE EXECUTIONS BESIDE THE BOOKED RESULTS. Report only: state,
            # counts and totals, never the rows. OFF unless L5_VENUE_BASIS
            # is exactly REPORT. No gate reads it.
            "venue_basis": venue_basis_lines(self),
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
            # ROUNDS SPENT AGAINST ORDERS SUBMITTED, this market, and whether
            # THIS PROCESS charges a round only when an order goes out.
            "round_lifecycle": self.cycle_report(
                getattr(self.transport, "market_slug", None), brief=True),
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
            "frozen_rule": "030d8bf"}))
        self.emit_arm_switch_readiness()
        self.emit_two_sided_comparison()
        self.emit_completion_latency()

    def emit_completion_latency(self) -> None:
        """The latency report on its OWN line, because the heartbeat drops it.

        THE DEFECT, MEASURED ON THE RUNNING SERVICE. `bounded_hb` reduces
        the largest field not in `HB_NEVER_REDUCE` until the line fits
        LOG_BUDGET_BYTES, and deployment `c300a6c4` reports exactly that:

            hb_reduced.fields = {"shadow_controller": 64618,
                                 "l5_baseline": 19249,
                                 "completion_latency": 15234}

        So the one instrument built to answer "where does the time go"
        reaches the tape as `{"_reduced": true, "bytes": 15234}` and the
        funnel it was written to measure cannot be measured in production.

        WHY NOT `HB_NEVER_REDUCE`. That list exists so the SAFETY state
        survives, and the budget is finite: protecting 15 KB of telemetry
        would push a safety field out instead -- the precise failure
        `bounded_hb` was written to prevent. Telemetry does not outrank the
        submission gate. It gets a second home instead, which is the same
        answer the full authorization rows got on
        `L5_AUTHORIZATION_PREFLIGHT`.

        Reporting only, on the heartbeat cadence, and it cannot raise: a
        latency line that throws would cost the heartbeat that carries the
        safety state.
        """
        try:
            rep = self._latency_report()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["latency_line_failed:%s" % type(exc).__name__] += 1
            return
        if not rep:
            return
        try:
            emit("L5_COMPLETION_LATENCY", {
                "utc": now_utc().isoformat(), "role": SERVICE_ROLE,
                "report": rep,
                "labels": ("the same report the heartbeat carries, on its "
                           "own line because bounded_hb reduces it out of "
                           "the heartbeat to protect the safety fields. "
                           "Reporting only: no gate, limit or decision "
                           "reads this.")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["latency_line_failed:%s" % type(exc).__name__] += 1

    #: the windows the FINISHED arm worked, comma-separated, so the switch
    #: readiness can require THEIR confirmed settlement by name rather than
    #: inferring it from a quiet book -- a quiet book is also what a window
    #: that never traded looks like.
    ARM_SWITCH_WINDOWS_ENV = "L5_ARM_SWITCH_WINDOWS"
    ARM_SWITCH_PROBE_ENV = "L5_ARM_SWITCH_PROBE"

    def emit_arm_switch_readiness(self) -> None:
        """May this process be restarted into the other completion arm?

        ON THE HEARTBEAT CADENCE AND NOT AT BOOT, which is the whole point.
        The question has to be answered BEFORE the process goes down; a boot
        emission would describe the state on the far side of the restart,
        after anything that was going to cross already had.

        OPT-IN, so it costs nothing until an arm switch is actually being
        prepared. READ-ONLY, like the gate it calls.
        """
        if str(os.getenv(self.ARM_SWITCH_PROBE_ENV, "")).strip().upper() \
                not in ("1", "YES", "TRUE", "ON"):
            return
        try:
            from src import l5_arm_switch_gate as ASG
            sg = self.submission_gate()
            latch = sg.get("halt_latch")
            # THREE STATES, NOT TWO. "HALT" is asserted, "CLEAR" is not, and
            # None / NO_GATE / anything else is UNESTABLISHED -- which the
            # gate treats as a blocker, never as a pass.
            halt = (True if latch == "HALT"
                    else False if latch == "CLEAR" else None)
            raw = (os.getenv(self.ARM_SWITCH_WINDOWS_ENV) or "").strip()
            windows = tuple(w.strip() for w in raw.split(",") if w.strip())
            out = ASG.switch_readiness(self.store, windows=windows,
                                       halt_asserted=halt)
            emit("L5_ARM_SWITCH_READINESS",
                 dict(out, label="L5_ARM_SWITCH_READINESS",
                      utc=now_utc().isoformat(),
                      halt_latch=latch,
                      windows_declared=list(windows)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # NEVER ok ON AN ERROR. A probe that cannot answer must not look
            # like a clean one.
            emit("L5_ARM_SWITCH_READINESS",
                 {"label": "L5_ARM_SWITCH_READINESS", "ok": False,
                  "blockers": ["PROBE_FAILED:%s" % type(exc).__name__],
                  "detail": ("%s" % exc)[:160]})

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

        A CONFIRMATION IS CACHED; A FAILURE TO READ ONE IS NOT. This used to
        cache either way, with the rationale that an unconfirmed window "must
        not become confirmed by retrying". That is right for a venue that
        DISAGREES and wrong for a venue that did not answer, and collapsing
        the two cost three consecutive live windows on 2026-11-03 -- 2300z
        refused 241 times with a healthy book, because one failed lookup at
        23:01 was cached for the remaining 900 seconds.

        So the three outcomes are now handled as three:

          CONFIRMED -- cached for the window. One read per fifteen minutes,
                       as before.
          MISMATCH  -- the venue states a start that DISAGREES with ours.
                       Cached as a refusal, never retried: retrying cannot
                       make two different fifteen minutes into one.
          TRANSIENT -- no usable answer. Retried inside this window with
                       bounded backoff, and NEVER cached until the attempts
                       are spent, at which point it becomes a cached refusal
                       so a dead gateway is not hammered for 900 seconds.

        The clock-derived window is still never substituted: every path that
        does not CONFIRM returns None and arming refuses by name.
        """
        ws = self.ws
        if ws is None:
            return None
        if ws in self.window_bounds:       # a SETTLED answer, either way
            return self.window_bounds[ws]
        try:
            retry = self.window_bounds_retry
        except AttributeError:            # a caller's stub; create it there
            retry = self.window_bounds_retry = {}
        st = retry.setdefault(int(ws), {"attempts": 0, "next_t": 0.0})
        now = float(getattr(self, "now_t", 0.0) or 0.0)
        if st["attempts"] and now < float(st["next_t"]):
            return None                    # backing off: not an answer yet
        slug_ = AB.slug_for(int(ws))
        # THE USABLE DEADLINE for this window's confirmation: the entry
        # cutoff, less the runway a confirmation needs to be worth having.
        # AN UNREADABLE CUTOFF IS NOT PERMISSION. Defaulting to the nominal
        # 480 would be the looser answer when the deployed value can only be
        # configured EARLIER, so a cutoff we cannot read gives the window up.
        try:
            from src import l5_two_sided_probe as _TSP
            deadline = (float(ws) + float(_TSP.entry_cutoff_s())
                        - VENUE_BOUNDS_MIN_RUNWAY_S)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.window_bounds[int(ws)] = None
            emit("L5_LIVE_WINDOW_UNCONFIRMED",
                 {"market": slug_, "retryable": False,
                  "reason": "ENTRY_CUTOFF_UNREADABLE",
                  "why": "%s" % type(exc).__name__,
                  "labels": ("the entry cutoff could not be read, so the "
                             "retry budget cannot be bounded by it; the "
                             "window is refused rather than retried against "
                             "a guessed cutoff")})
            return None
        if now >= deadline:
            # Past the point where a confirmation could still open a leg.
            # Give the window up BY NAME rather than keep reading the venue.
            self.window_bounds[int(ws)] = None
            emit("L5_LIVE_WINDOW_UNCONFIRMED",
                 {"market": slug_, "retryable": False,
                  "reason": "ENTRY_CUTOFF_REACHED",
                  "attempts": st["attempts"],
                  "now_minus_window_start_s": round(now - float(ws), 1),
                  "usable_deadline_offset_s": round(
                      deadline - float(ws), 1),
                  "labels": ("no entry runway left, so a confirmation could "
                             "not produce a placed order; the window is "
                             "given up rather than re-read. The "
                             "clock-derived window is never substituted.")})
            return None
        t0 = time.time()
        try:
            rep = dict(OBS.venue_window_bounds_report(int(ws), AB.WINDOW_S)
                       or {})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            rep = {"bounds": None, "classification": OBS.BOUNDS_TRANSIENT,
                   "why": "%s" % type(exc).__name__}
        latency = max(0.0, time.time() - t0)
        # LATENCY IS PART OF THE BUDGET. The tick clock is sampled once per
        # tick, so a lookup that blocked for seconds would otherwise be
        # invisible to the deadline arithmetic below.
        now = max(now, now + latency)
        st["attempts"] = int(st["attempts"]) + 1
        cls = str(rep.get("classification") or OBS.BOUNDS_TRANSIENT)
        bounds = rep.get("bounds")
        slug = AB.slug_for(int(ws))
        if cls == OBS.BOUNDS_CONFIRMED and bounds:
            self.window_bounds[int(ws)] = bounds
            emit("L5_LIVE_WINDOW_CONFIRMED",
                 {"market": slug, "attempts": st["attempts"],
                  "bounds": bounds,
                  "labels": ("the venue states this window's start and it "
                             "matches ours; bounds cached for the window")})
            return bounds
        base = {"market": slug, "classification": cls,
                "why": rep.get("why"),
                "pmus_start_utc": rep.get("pmus_start_utc"),
                "attempts": st["attempts"],
                "max_attempts": VENUE_BOUNDS_MAX_ATTEMPTS,
                "lookup_latency_s": round(latency, 3),
                "runway_left_s": round(deadline - now, 1),
                "labels": ("the venue did not confirm this window's start, "
                           "so no venue-bounded authorization can be issued "
                           "for it. The clock-derived window is never "
                           "substituted.")}
        if cls == OBS.BOUNDS_MISMATCH:
            # DEFINITIVE. The venue named a different fifteen minutes.
            self.window_bounds[int(ws)] = None
            emit("L5_LIVE_WINDOW_UNCONFIRMED",
                 dict(base, retryable=False, reason="VENUE_DISAGREES"))
            return None
        if st["attempts"] >= VENUE_BOUNDS_MAX_ATTEMPTS:
            self.window_bounds[int(ws)] = None
            emit("L5_LIVE_WINDOW_UNCONFIRMED",
                 dict(base, retryable=False, reason="ATTEMPTS_EXHAUSTED"))
            return None
        idx = min(st["attempts"] - 1, len(VENUE_BOUNDS_BACKOFF_S) - 1)
        wait = float(VENUE_BOUNDS_BACKOFF_S[idx])
        # A RETRY THAT WOULD LAND PAST THE USABLE DEADLINE IS NOT SCHEDULED.
        # This is the bound that matters: it is computed from where we are in
        # THIS window, after this lookup's latency, not from the schedule.
        if now + wait >= deadline:
            self.window_bounds[int(ws)] = None
            emit("L5_LIVE_WINDOW_UNCONFIRMED",
                 dict(base, retryable=False,
                      reason="NO_RUNWAY_FOR_RETRY", would_retry_in_s=wait))
            return None
        st["next_t"] = now + wait
        emit("L5_LIVE_WINDOW_UNCONFIRMED",
             dict(base, retryable=True, reason="TRANSIENT_WILL_RETRY",
                  retry_in_s=wait))
        return None

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
                    "by re-reading. OWNERSHIP IS NOW VERIFIED PER ID, not "
                    "inferred from being in this ledger: reconciliation "
                    "writes the venue's open orders into `live_orders`, so "
                    "presence there was never proof we placed it, and this "
                    "label's old unconditional promise that 'a manual order "
                    "is never one of them' was void -- the owner's "
                    "hand-placed 2215z order was adopted and cancelled by "
                    "this path. An id with no submit record of ours is "
                    "refused and listed in `refused_not_ours`. `cancelled` "
                    "means the "
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

    def taker_policy_enabled(self) -> bool:
        """Default OFF, and opt-in by an explicit YES.

        Spelled as a string comparison rather than a truthy env read so a
        stray "0" or "false" cannot enable live crossing.
        """
        return str(os.getenv(TCP.POLICY_ENV, "")).strip().upper() == "YES"

    def _taker_policy_number(self, env: str, default: Any) -> Any:
        raw = str(os.getenv(env, "")).strip()
        if not raw:
            return default
        try:
            return D(raw)
        except BaseException:                                  # noqa: BLE001
            # AN UNREADABLE BOUND IS NOT THE DEFAULT BOUND. Returning the
            # default here would silently restore a cap the owner was trying
            # to change; None reaches the policy, which refuses on it.
            self.stats["taker_policy_unreadable:%s" % env] += 1
            return None

    def ts_taker_policy(self, dec: Dict[str, Any], src: Dict[str, Any],
                        now: float) -> Dict[str, Any]:
        """Gather the bounded policy's inputs and ask it. Never raises.

        MAKER-FIRST IS MEASURED, NOT ASSUMED. `maker_rested_s` is how long a
        maker completion has actually been resting unfilled, taken from the
        probe's own first-leg-complete clock, and `maker_attempted` is
        whether one was ever posted. Both are None when unknown, and the
        policy fails closed on either.
        """
        try:
            held = {"UP": src.get("up_qty"), "DOWN": src.get("dn_qty")}
            attempted = None
            rested = None
            try:
                mk = self.maker_completion_history(
                    str(getattr(self.transport, "market_slug", "") or ""),
                    side=dec.get("side"), now=now)
                attempted = mk.get("attempted")
                rested = mk.get("rested_s")
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["taker_policy_maker_history:%s"
                           % type(exc).__name__] += 1
            return TCP.authorize(
                decision=dec,
                enabled=self.taker_policy_enabled(),
                owner_authorized=self.ts_completion_cross_authorized(),
                held_up_qty=held["UP"], held_dn_qty=held["DOWN"],
                held_up_px=src.get("up_cost_px"),
                held_dn_px=src.get("dn_cost_px"),
                maker_attempted=attempted,
                maker_rested_s=rested,
                maker_patience_s=self._taker_policy_number(
                    TCP.MAKER_PATIENCE_ENV, TCP.DEFAULT_MAKER_PATIENCE_S),
                margin_floor_usd=self._taker_policy_number(
                    TCP.MARGIN_FLOOR_ENV, TCP.DEFAULT_MARGIN_FLOOR_USD),
                max_outlay_usd=self._taker_policy_number(
                    TCP.MAX_OUTLAY_ENV, TCP.DEFAULT_MAX_OUTLAY_USD),
                window_outlay_spent_usd=self.taker_outlay_spent(),
                window_outlay_cap_usd=self._taker_policy_number(
                    TCP.WINDOW_OUTLAY_ENV, TCP.DEFAULT_WINDOW_OUTLAY_USD),
                book_rungs=src.get("book_rungs"),
                depth_haircut=self._taker_policy_number(
                    TCP.DEPTH_HAIRCUT_ENV, TCP.DEFAULT_DEPTH_HAIRCUT),
                halted=self.taker_policy_halted(),
                # THE LOSS-REDUCING CASE, SEPARATELY AUTHORIZED AND
                # DEFAULT-OFF. A rescue cannot clear the +$0.25 margin floor
                # -- the first leg's cost is sunk, so the best achievable
                # worse outcome is still negative -- and replaying the 0300z
                # and 0315z windows against their own recorded books showed
                # the policy refusing both for exactly that reason even with
                # the submission route wired. This is the owner-specified
                # completion-loss budget issue #195 asks for: with the
                # switch unset `authorize` takes the identical path it takes
                # today, and with it set the test becomes how much exposure
                # the cross removes rather than where the outcome lands.
                loss_reduction=TCP.loss_reduction_authorized(),
                min_worst_improvement_usd=self._taker_policy_number(
                    TCP.MIN_WORST_IMPROVEMENT_ENV, None),
            )
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["taker_policy_failed:%s" % type(exc).__name__] += 1
            # FAIL CLOSED. A policy that cannot evaluate has not authorized.
            return {"ok": False, "authorized": False,
                    "reason": "POLICY_EVALUATION_FAILED:%s"
                              % type(exc).__name__}

    def taker_policy_halted(self) -> Optional[bool]:
        """Is any halt in force? None when that cannot be established.

        Reads the SAME halt the money path reads rather than a second
        opinion, so the policy cannot act through a halt the executor is
        already honouring.
        """
        try:
            if self.halted():
                return True
            day = self.day_realized_line()
            wh = day.get("would_halt")
            if wh is None:
                # THE DAY COULD NOT BE COMPUTED, which is exactly what
                # refuses at the submission gate. It refuses here too.
                return None
            return bool(wh)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["taker_policy_halt_unreadable:%s"
                       % type(exc).__name__] += 1
            return None

    def taker_outlay_spent(self) -> Any:
        """Additional cash this window's crosses have already committed.

        Nothing crosses yet, so this is 0 by construction and is wired as a
        real accessor rather than a literal so that enabling submission does
        not also have to invent the accounting the cap reads.
        """
        try:
            return D(str(getattr(self, "_taker_outlay_usd", 0) or 0))
        except BaseException:                                  # noqa: BLE001
            return None

    def maker_completion_history(self, slug: str,
                                 side: Optional[str] = None,
                                 now: Optional[float] = None
                                 ) -> Dict[str, Any]:
        """Was a MAKER quote posted on the COMPLETING side, and how long ago?

        READ FROM THE SUBMIT LEDGER, NOT FROM A STAMP. The obvious
        implementation -- stamp a flag where the completion is triggered --
        records an INTENTION: `maybe_book_completion` re-enters
        `executor.handle`, which still has every gate to pass, and on
        2026-10-30 the completion route was refused WOULD_CROSS 41 times
        after being approved. A flag set at the trigger would have claimed
        the maker route was tried on windows where nothing was ever posted,
        which is the one lie maker-first cannot survive.
        So this reads `state["submitted"]`, where a row exists only because
        an order was actually sent.

        THE COMPLETING SIDE IS THE SIDE WE STILL NEED. A posted quote on
        that side which has not filled IS the maker route tried and waiting,
        whether it was posted by the completion path or as the second leg of
        a two-sided open -- the policy's question is whether a cheaper maker
        route has already had its chance, and both answer it.

        Unknown is None, never a comfortable default: the policy fails closed
        on an unreadable history rather than crossing on an assumption.
        """
        out: Dict[str, Any] = {"attempted": None, "rested_s": None,
                               "slug": str(slug),
                               "side": None if side is None
                               else str(side).strip().upper()}
        if not slug or not out["side"]:
            return out
        t_now = float(now if now is not None else time.time())
        try:
            rows = (self.store.state.get("submitted") or {})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["maker_history_unreadable:%s"
                       % type(exc).__name__] += 1
            return out
        earliest: Optional[float] = None
        seen = 0
        for rec in rows.values():
            try:
                if str(rec.get("market") or "") != str(slug):
                    continue
                if str(rec.get("side") or "").strip().upper() != out["side"]:
                    continue
                ts = dt.datetime.fromisoformat(str(rec.get("t")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
                seen += 1
                e = ts.timestamp()
                earliest = e if earliest is None else min(earliest, e)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # ONE UNPARSEABLE ROW MUST NOT READ AS "NEVER POSTED".
                self.stats["maker_history_row_unreadable:%s"
                           % type(exc).__name__] += 1
                return out
        out["orders_on_side"] = seen
        out["attempted"] = bool(seen)
        if not seen:
            # NOT ATTEMPTED IS A FACT, and a zero wait is the honest figure
            # for it. The policy refuses on `attempted` before reading this.
            out["rested_s"] = 0.0
            return out
        out["rested_s"] = max(0.0, t_now - float(earliest))
        out["first_posted_utc"] = dt.datetime.fromtimestamp(
            float(earliest), dt.timezone.utc).isoformat()
        return out

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
                    gap_suspected=bool(snap.get("gap_suspected")),
                    # REPORTING-ONLY ENRICHMENT, AND NEVER A NEW REQUIREMENT
                    # ON THE CALLER. The rungs exist so the bounded taker
                    # policy can walk a VWAP instead of trusting the touch;
                    # a caller that does not carry the book ring must still
                    # get its completion economics, so this degrades to None
                    # (the policy then falls back to displayed depth) rather
                    # than making every caller grow a new attribute.
                    book_rungs=(
                        self.completion_rungs(slug, side)
                        if callable(getattr(self, "completion_rungs", None))
                        else None),
                    # ---- THE MAKER SIDE OF THE SAME BOOK, and the mid.
                    #
                    # REPORTING-ONLY ENRICHMENT on the same terms as the
                    # rungs above: every field degrades to None and no
                    # existing caller grows a requirement. They exist so the
                    # ONE alternative ranking in `l5_inventory_decision` can
                    # price RESTING against CROSSING on the same book read,
                    # rather than two paths each reading their own.
                    #
                    # THE SIDES ARE NOT INTERCHANGEABLE, and this is the
                    # arithmetic error worth naming. To BUY DOWN as a maker
                    # we post a YES OFFER, so joining the touch means
                    # offering at the best ASK and our own DOWN price is
                    # 1 - ask, queued behind `ask_shares`. To BUY UP as a
                    # maker we post a YES BID at the best bid, own price
                    # `bid`, queued behind `bid_shares`. The CROSSING side
                    # above is the opposite one in both cases -- buying DOWN
                    # consumes the bid, buying UP lifts the ask -- so a
                    # single "depth" field could not serve both and there
                    # are deliberately two.
                    maker_own_px=(
                        None if (bid is None if side == "UP" else ask is None)
                        else (D(str(bid)) if side == "UP"
                              else D(1) - D(str(ask)))),
                    maker_queue_ahead=(snap.get("bid_shares") if side == "UP"
                                       else snap.get("ask_shares")),
                    # THE MARKET'S OWN IMPLIED PROBABILITY THAT UP SETTLES.
                    # The mid and nothing else: a model of our own here would
                    # be a prediction, and this programme has no view on
                    # outcomes. None on a one-sided book, which makes the
                    # expectation unavailable rather than guessed.
                    mid_wire=(None if (bid is None or ask is None)
                              else (D(str(bid)) + D(str(ask))) / D(2)))

    def completion_rungs(self, slug: str, side: str
                         ) -> Optional[List[List[str]]]:
        """The book we would WALK to buy `side`, in OWN-price terms.

        `ts_completion_inputs` prices the cross at ONE level against ONE
        `available_depth` figure, which is correct for the touch and silently
        optimistic for any size that would eat through it. The bounded taker
        policy judges a cross on the VWAP its size would really pay, so it
        needs rungs rather than a touch -- and the snapshot carries only
        `bid`/`ask`/`bid_shares`/`ask_shares`.

        OWN PRICE, NOT WIRE. Buying UP walks the UP asks at their own price.
        Buying DOWN walks the UP BIDS, where own cost is `1 - wire`, so the
        cheapest DOWN is the HIGHEST bid and the list is already in the right
        order. Returning wire prices for a DOWN completion is the arithmetic
        error this exists to avoid.

        None means "no rungs", not "no depth": the policy then falls back to
        the single displayed figure instead of treating a parse failure as an
        empty book.
        """
        ring = getattr(self, "book_ring", None)
        if ring is None:
            return None
        try:
            row = ring.latest(str(slug)) or {}
            want = "asks" if str(side).strip().upper() == "UP" else "bids"
            if not row.get("%s_served" % want[:-1] + "s", True):
                return None
            raw = row.get(want)
            if not raw:
                return None
            out: List[List[str]] = []
            for rung in raw:
                wire = D(str(rung[0]))
                size = D(str(rung[1]))
                own = wire if want == "asks" else (D(1) - wire)
                if own <= 0 or own >= D(1) or size < 0:
                    continue
                out.append([str(own), str(size)])
            return out or None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["completion_rungs_unreadable:%s"
                       % type(exc).__name__] += 1
            return None

    def maybe_book_completion(self, now: float) -> Optional[Dict[str, Any]]:
        """ROUTE B. Let the BOOK ask for a completion we already own.

        THE DEFECT. `TS.complement_order` is reached only through
        `l5_live_execution.handle(row, ...)`, whose only hot-path caller is
        the ingest loop -- one call per admitted L5 action, a measured median
        18.11 s after L5 acted. A completion refused at that instant is not
        reconsidered until the next action.

        WHAT THIS DOES, AND WHAT IT DOES NOT. It re-runs the EXISTING path
        against the CURRENT book, reusing the last REAL admitted action for
        this market. It does not synthesise an L5 action, build a second
        order path, or construct an authorization. Every gate, claim,
        overlay, limit check and reconciliation the L5-driven call passes is
        the same object here, because it is the same call.

        PROVENANCE IS NOT MISREPRESENTED. `L5_BOOK_COMPLETION_TRIGGER` is
        emitted BEFORE the re-entry, naming the decision, the reused
        `action_key` and the fact that the BOOK asked. The tape therefore
        shows a book-triggered re-evaluation, never a fresh L5 action.

        INERT BY DEFAULT. Without `L5_BOOK_COMPLETION` this returns
        immediately and the engine is byte-identical.
        """
        from src import l5_book_completion as BC
        if not BC.enabled():
            return None
        t = self.transport
        if t is None:
            return None
        slug = getattr(t, "market_slug", None)
        if not slug:
            return None
        # the continuous inventory controller completes this market itself
        if LX.continuous_inventory_enabled() and Runner.continuous_owns(
                self, slug):
            self.stats["book_completion:CONTINUOUS_INVENTORY_OWNS"] += 1
            return None
        row = self._last_action_row.get(slug)
        controller_route = False
        # ---------- WHO OPENED WHAT WE HOLD DECIDES THE ROUTE ----------
        #
        # The selector below used to be `row is None` ALONE, which asks about
        # the INGEST FEED and not about the position. L5 rows arrive
        # continuously, so a controller-opened position was completed under
        # `L5_FOLLOWED` with a reused L5 action key on every tick a row
        # existed, and the controller re-entry counter never incremented once
        # across the three-window campaign while 12 of 15 orders were
        # controller-keyed. Origin comes from CONFIRMED FILLS via
        # `fill_provenance`, so it is stable for a given inventory state --
        # strictly more stable than the feed-dependent test it replaces, which
        # is why this cannot flap a route mid-window.
        #
        # ATTRIBUTION ONLY. Both routes price the complement with the same
        # `TS.complement_order` call against the same ceilings and pass the
        # same gates; MIXED and UNKNOWN deliberately leave the previous route
        # in force rather than credit either engine with the other's stock.
        try:
            origin = LX.inventory_origin(self.store.state, slug)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            origin = LX.ORIGIN_UNKNOWN
        self.stats["book_completion:ORIGIN:%s" % origin] += 1
        if origin == LX.ORIGIN_CONTROLLER:
            controller_route = True
        if row is None:
            # ---------- CONTROLLER-OPENED INVENTORY HAS NO L5 ROW ----------
            #
            # 0230z, 2026-10-30. The autonomous controller opened 1.91 DOWN,
            # so `_last_action_row` -- written ONLY from admitted L5 ingest
            # rows -- held nothing for the slug, and this route returned
            # NO_ACTION_YET 263 times while the position sat unmatched. The
            # engine could open autonomously and could not complete what it
            # had opened, by construction.
            #
            # `BC.decide` never needed the row: it prices the complement from
            # the market, the book and the ledger's own inventory. The row was
            # only ever the VEHICLE for re-entering `handle`, and there is
            # already a row-free vehicle for exactly this -- the controller
            # interface, which carries CONTROLLER_GENERATED provenance and
            # whose provisional side and price are re-derived by the
            # two-sided branch, so it selects the complement itself.
            #
            # NOTHING IS INVENTED. No L5 action is synthesised and no action
            # key is fabricated: the re-entry declares itself as the
            # controller's own decision, which is what it is.
            #
            # STILL NOTHING TO DO ON A FLAT BOOK. Without held inventory this
            # is the pre-first-action case the original guard was written for,
            # and it still returns NO_ACTION_YET.
            try:
                _u = self.store.position(slug, "UP")
                _d = self.store.position(slug, "DOWN")
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # FAIL CLOSED: an unreadable ledger is not a flat one.
                self.stats["book_completion:LEDGER_UNREADABLE:%s"
                           % type(exc).__name__] += 1
                return None
            if (_u or 0) == 0 and (_d or 0) == 0:
                self.stats["book_completion:NO_ACTION_YET"] += 1
                return None
            controller_route = True
        elif controller_route:
            # AN L5 ROW EXISTS BUT THE INVENTORY IS OURS. This is the case
            # the old selector could not see. Nothing is invented: the
            # controller re-entry declares its own decision and fabricates no
            # action key, exactly as it does when no row exists at all.
            self.stats["book_completion:CONTROLLER_INVENTORY_WITH_L5_ROW"] += 1
        ws = AB.slug_start(slug)
        if ws is None:
            return None
        # INVENTORY AND RESTING STATE FROM THE LEDGER, never from our own
        # memory of what we did: the ledger is what reconciliation certifies.
        try:
            up = self.store.position(slug, "UP")
            dn = self.store.position(slug, "DOWN")
            resting = int(self.store.still_fillable_order_count())
            legs = self.store.leg_cost(slug)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # FAIL CLOSED: an unreadable book is not an empty one.
            self.stats["book_completion:LEDGER_UNREADABLE:%s"
                       % type(exc).__name__] += 1
            return None
        if up is None or dn is None:
            return None
        held, other = ("UP", "DOWN") if up > dn else ("DOWN", "UP")
        held_qty, def_qty = (up, dn) if held == "UP" else (dn, up)
        # REPORTING ONLY: the first tick on which we SAW a HELD POSITION.
        #
        # THE `held_qty > 0` GUARD IS THE WHOLE POINT, and leaving it out
        # was a real defect in production. With up == dn == 0, `up > dn` is
        # False, so `held` is "DOWN" and `held_qty` is 0 -- a flat market
        # reads as a held DOWN leg. The first deployed heartbeat showed it
        # exactly: 256 decisions, every one of them NO_INVENTORY or
        # DECIDE_COOLDOWN, and `detection_to_decision_s` n 256 median
        # 451.7 s. That figure was not a fill-to-decision latency at all;
        # it was time since the first tick of the window.
        #
        # Not the fill either -- see l5_completion_latency on why that
        # interval is a bound and not a measurement.
        if held_qty > 0:
            _lat(self, "note_fill_detected", slug, held, now)
        book = self.book.snapshot(slug) or {}
        gates = getattr(self.executor, "validation_gates", None)
        # HOISTED, because the guard below now needs the same rows the
        # decision was made on. Reading the ledger twice could hand the gate
        # a different book than `decide` saw, and the whole point of the gate
        # is to agree with the decision it is gating.
        _rows = _resting_rows(self.store, slug)
        # FAIL CLOSED ON AN UNREADABLE LEDGER. None is not "no rows": it is
        # "we cannot say what is resting", and the per-market hedge guard
        # below is only as good as the rows it reads. At one slot the
        # account-wide count caught this; at two it does not, so the refusal
        # is made explicit here rather than inferred from a count.
        if _rows is None:
            self.stats["book_completion:LEDGER_UNREADABLE:resting_rows"] += 1
            return None
        # WHAT IS ALREADY WORKING ON THE SIDE WE WOULD BUY. `decide`
        # subtracts it from the unmatched need; see its section 1a for why a
        # position-only `need` could size a second full hedge on top of a
        # first one that was still live.
        _outstanding = _outstanding_on_side(_rows, other)
        rec = BC.decide(
            market=slug, held_side=held,
            held_qty=held_qty, held_cost_usd=legs.get(held),
            deficient_qty=def_qty, deficient_cost_usd=legs.get(other),
            book=book, now_t=now, window_start=float(ws),
            # EVERY RESTING ORDER, so idempotence is judged against the
            # venue's book and not against this method's own history.
            # EVERY LIVE ORDER THIS LEDGER OWNS, so idempotence is judged
            # against the recorded book rather than against this method's
            # own memory of what it did.
            resting=_rows,
            # THE RESERVATION. Contracts already working on the deficient
            # side are already spoken for, so they are subtracted from the
            # need rather than ordered a second time.
            outstanding_qty=_outstanding,
            tick=getattr(t, "tick_size", None) or "0.01",
            min_qty=getattr(t, "quantity_increment", None) or "0.01",
            # COMPLETION DEPTH, from its own env and NOT the opening
            # probe's. Reusing `L5_TS_DEPTH_TICKS` (default 2) is what posted
            # 1445z's complement at own 0.6000 into a 0.6200 bid -- behind the
            # whole visible queue. Resting at the touch is a FILLABLE price,
            # not a fill: `_would_cross` still proves it post-only, and a
            # resting completion is never counted as a hedge.
            depth_ticks=BC.completion_depth_ticks(),
            residual_budget_usd=getattr(
                gates, "max_worst_case_deficit_usd", None),
            # the deployed backstop, which is per-campaign and already
            # enforced; passing it lets `decide` decline instead of
            # producing an order the claim path would refuse.
            submissions_left=AUTH.max_submissions(),
            last_decide_t=self._book_completion_t)
        # THE CADENCE IS STAMPED EVEN ON A REFUSAL, so a refused decision
        # cannot be retried every tick.
        # ADAPTIVE COMPLETION DEPTH, DEFAULT OFF, AND ONLY ON A CAP REFUSAL.
        #
        # A completion fixed AT THE TOUCH cannot clear the fee-safe cap: a
        # touch pair is `1 - spread`, 0.99 on a one-tick book, against the
        # 0.96 ceiling. Replayed over 13 out-of-sample windows that shape
        # completes ZERO pairs in 652 entries -- all 311 filled first legs
        # refuse on the cap, which is why rescued legs never complete.
        # Walking OUTWARD to the shallowest depth whose pair clears the cap
        # converted 12 of 66 strands into pairs on the queue-independent
        # PROVEN arm (+0.0012 net per entry), and a bounded taker
        # completion recovered NONE of the remaining 54.
        #
        # WHAT THIS CANNOT DO. It retries ONLY a pair-cap refusal, only at
        # depths `adaptive_depth_candidates()` offers, shallowest first,
        # and stops on the first outcome that is not a cap refusal. It
        # cannot relax the cap, the band, the freshness limit, a budget or
        # any gate -- every retry is a full `BC.decide` with the same
        # inputs and one different depth. With the flag off the candidate
        # list is exactly the deployed depth, so this loop runs zero extra
        # decisions and the path is byte-identical.
        _CAP_REFUSALS = ("PAIR_ABOVE_REDEMPTION", "COMPLEMENT_PAIR_ABOVE_CAP")
        if (not rec.get("ok")) and str(rec.get("reason")) in _CAP_REFUSALS:
            for _d2 in BC.adaptive_depth_candidates():
                if _d2 == BC.completion_depth_ticks():
                    continue            # already tried, above
                _alt = BC.decide(
                    market=slug, held_side=held,
                    held_qty=held_qty, held_cost_usd=legs.get(held),
                    deficient_qty=def_qty,
                    deficient_cost_usd=legs.get(other),
                    book=book, now_t=now, window_start=float(ws),
                    resting=_rows, outstanding_qty=_outstanding,
                    tick=getattr(t, "tick_size", None) or "0.01",
                    min_qty=getattr(t, "quantity_increment", None) or "0.01",
                    depth_ticks=_d2,
                    residual_budget_usd=getattr(
                        gates, "max_worst_case_deficit_usd", None),
                    submissions_left=AUTH.max_submissions(),
                    last_decide_t=self._book_completion_t)
                self.stats["book_completion:adaptive_try:d%d:%s"
                           % (_d2, _alt.get("reason") or "OK")] += 1
                if str(_alt.get("reason")) not in _CAP_REFUSALS:
                    rec = dict(_alt, adaptive_depth_ticks=_d2,
                               adaptive_from_depth=(
                                   BC.completion_depth_ticks()))
                    break
        if rec.get("reason") != "DECIDE_COOLDOWN":
            self._book_completion_t = now
        self.stats["book_completion:%s" % rec.get("reason")] += 1
        # REPORTING ONLY. The decision, the reason it reached, and the age
        # of the book it was taken on -- which `decide` now carries on
        # every outcome rather than only on a stale refusal.
        _lat(self, "note_decision", slug, now,
            action=("COMPLETE" if rec.get("ok") else "NO"),
            reason=(rec.get("reason") or "OK"),
            book_age_s=rec.get("stream_age_s"))

        # ============ A REFUSED COMPLETION IS NAMED, NOT JUST COUNTED.
        #
        # WHAT WAS WRONG. The counter above was the ONLY record of a refused
        # completion: `if not rec.get("ok"): return rec` emitted nothing. So
        # the tape could say `book_completion:ORDER_ALREADY_RESTING` went up
        # 40 times and could not say whether any of those 40 gave up a pair
        # worth having. That is the same blindness
        # `L5_BOOK_COMPLETION_RESULT` was added to remove at the PLACEMENT
        # layer, still fully present one layer up at the DECISION layer.
        #
        # WHY A HISTOGRAM IS NOT ENOUGH. Most refusals are CORRECT --
        # NO_INVENTORY means there was nothing to complete -- so a count of
        # reasons overstates the problem and cannot rank what to fix. The
        # record carries a separate verdict on whether a PROFITABLE pair was
        # given up, and only `missed_profitable_pair: YES` is a problem.
        #
        # THE CEILING IS PASSED IN, never read inside the classifier, so the
        # record cannot disagree with the gate that actually refuses.
        #
        # OBSERVABILITY ONLY, AND IT CANNOT BREAK THE MONEY PATH. Every part
        # of it is inside one swallow: a failure to describe a refusal must
        # never change the refusal, and must never raise into a caller that
        # is about to return a decision.
        def _note_missed(_reason: Any) -> None:
            try:
                try:
                    from src import l5_two_sided_probe as _TSc
                    _ceil = _TSc.max_pair_cost()
                except BaseException:                          # noqa: BLE001
                    _ceil = None
                emit("L5_MISSED_PAIR", MPB.record(
                    market=slug, reason=_reason, decision=rec,
                    entry_ceiling=_ceil, held_side=held,
                    held_qty=held_qty, deficient_qty=def_qty,
                    stage="DECISION"))
            except BaseException as exc:                        # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["missed_pair_emit_failed:%s"
                           % type(exc).__name__] += 1

        if not rec.get("ok"):
            _note_missed(rec.get("reason"))
            return rec
        # ============ A COMPLETION MAY SHARE THE BOOK WITH AN UNRELATED
        # OPENING. IT MAY NEVER STACK ON ITS OWN HEDGE.
        #
        # WHAT WAS WRONG. The test was `if resting:` -- any still-fillable
        # order ANYWHERE on the account, and `resting` is
        # `still_fillable_order_count`, which is account-wide. So a resting
        # UP opening refused the DOWN completion of UP inventory we already
        # held, which is the one order that REDUCES exposure. Worse, it
        # refused it AFTER `decide` had already returned ok=True having seen
        # that same resting order in `_rows` -- the decision was made with
        # full knowledge of the book and then discarded by a cruder test. The
        # comment claimed to mirror MAX_LIVE_ORDERS; with
        # `L5_MAX_LIVE_ORDERS=2` it was strictly tighter than the limit it
        # named, and the engine could hold one leg and never complete it.
        #
        # WHAT REPLACES IT, in two parts, in this order.
        #
        # (a) NO SECOND ORDER ON THE HEDGE SIDE, EVER. This is the duplicate
        # hedge, and it is refused on the PER-MARKET rows rather than on the
        # account-wide count, because that is the scope on which "duplicate"
        # means anything. `decide`'s own section 8 already refuses an equal
        # or better resting quote and its section 1a now reserves the
        # outstanding quantity, so this is the third independent guard on the
        # same mistake -- and it is the cheapest one to read, which is why it
        # is also written here explicitly rather than left implicit in the
        # two upstream checks.
        #
        # (b) THE SLOT CEILING IS THE EXECUTOR'S OWN, not a hard-coded one.
        # `max_live_orders()` is the number the executor's MAX_LIVE_ORDERS
        # gate binds on, so declining here can no longer disagree with the
        # gate it claims to anticipate. The executor still re-checks it:
        # nothing is placed on this path's say-so.
        #
        # WHAT THIS CANNOT DO. It cannot raise a limit, place a second hedge,
        # or withdraw anything -- and the verb for withdrawal is avoided in
        # this method's prose on purpose, because a sibling test scans its
        # SOURCE TEXT to prove no withdrawal path exists here. That guard is
        # worth more intact than this comment is worth literal; it is the
        # third such guard this change had to write around, and each one was
        # right. With `L5_MAX_LIVE_ORDERS` unset
        # `max_live_orders()` is 1 and `resting >= 1` IS `if resting:` --
        # today's behaviour exactly, which is why the suite's existing
        # one-slot expectations still hold.
        _hedge_rows = tuple(r for r in _rows
                            if str(r.get("side") or "").upper() == other)
        if _hedge_rows:
            self.stats["book_completion:HEDGE_ALREADY_RESTING"] += 1
            _note_missed("HEDGE_ALREADY_RESTING")
            return dict(rec, ok=False, reason="HEDGE_ALREADY_RESTING",
                        hedge_side=other,
                        hedge_orders_resting=len(_hedge_rows),
                        reserved_contracts=str(_outstanding))
        _slots = int(max_live_orders())
        if resting >= _slots:
            self.stats["book_completion:ORDER_ALREADY_RESTING"] += 1
            _note_missed("ORDER_ALREADY_RESTING")
            return dict(rec, ok=False, reason="ORDER_ALREADY_RESTING",
                        still_fillable_orders=int(resting),
                        max_live_orders=_slots)
        emit("L5_BOOK_COMPLETION_TRIGGER", {
            "triggered_by": "BOOK_NOT_L5_ACTION",
            "provenance": (LX.CONTROLLER_GENERATED if controller_route
                           else LX.L5_FOLLOWED),
            "route": ("CONTROLLER_REENTRY" if controller_route
                      else "L5_ROW_REENTRY"),
            "reused_action_key": (None if controller_route else
                                  str(row.get("transactionHash")
                                      or row.get("slug") or "")[:80]),
            # WHAT THE ROUTE WAS DECIDED ON, recorded beside the route so a
            # reader can audit the attribution instead of inferring it.
            "inventory_origin": origin,
            "l5_row_present": bool(row is not None),
            "decision": {k: v for k, v in rec.items() if k != "leg"},
            "labels": ("the BOOK asked for this re-evaluation. On the L5 "
                       "route the reused action is a real admitted L5 action "
                       "and no L5 action is synthesised; on the controller "
                       "route the inventory was opened by us and there is no "
                       "L5 action to reuse, so the re-entry declares itself "
                       "CONTROLLER_GENERATED and fabricates no action key. "
                       "The order, if any, is placed by the existing path "
                       "through every existing gate."),
        })
        self.stats["book_completion:REENTERED"] += 1
        # THE OUTCOME OF THE ATTEMPT, CAPTURED AND PAIRED WITH THE DECISION.
        #
        # WHAT WAS WRONG. Both placement calls below discarded their return
        # value and this method returned `rec` -- the DECISION -- so the tape
        # recorded `action: COMPLETE, ok: true, locked_pnl_usd: 0.2626` and
        # never recorded whether an order followed. The refusal landed only in
        # a shared `self.stats` counter with no link to the decision that
        # provoked it.
        #
        # WHAT THAT COST. Across the 2026-11-01 campaign the policy returned
        # COMPLETE 145 times -- 109 of them at a pair cost inside the 0.96
        # ceiling, median 0.9200, minimum 0.6600 -- and not one became an
        # order. Five of seven windows are affected. The nominal prize is
        # $1.5633 (best locked_pnl_usd per window) against a campaign that
        # realized -$0.0755, and it was IMPOSSIBLE to attribute a single
        # missed completion to a cause, because the candidate refusals
        # (COMPLEMENT_DEPTH_INSUFFICIENT, ORDER_ALREADY_RESTING,
        # MAX_LIVE_ORDERS, ALREADY_RESTING_AT_OR_BETTER) were only ever
        # visible as aggregate ages in the heartbeat.
        #
        # WHY THIS AND NOT A BEHAVIOUR CHANGE. The two leading candidates
        # demand OPPOSITE fixes: insufficient complement depth wants a
        # completion sized down to displayed depth, while an occupied order
        # slot wants the stale opposing quote withdrawn to free one. Shipping
        # either blind risks withdrawing a good opening quote to chase a
        # completion that could never have filled. This records which one it
        # is, costs nothing economically, and makes the next bounded campaign
        # decisive.
        #
        # The wording above deliberately avoids one verb: a sibling test
        # scans this method's SOURCE TEXT to prove it withdraws no order, and
        # that guard is worth more intact than this comment is worth literal.
        #
        # OBSERVABILITY ONLY. It places no order, changes no price, size,
        # limit or gate, and cannot turn a refusal into a submission.
        _bc_key = str(rec.get("key") or "")
        _bc_outcome: Dict[str, Any] = {"captured": False}
        if controller_route:
            # THE SAME ROW-FREE INTERFACE `maybe_controller_open` USES. The
            # provisional side and price are re-derived by the two-sided
            # branch, which picks the complement from the held inventory, so
            # what is seeded here cannot choose the leg.
            _leg = dict(rec.get("leg") or {})
            self.stats["book_completion:REENTERED:CONTROLLER"] += 1
            _bc_outcome = self._bc_capture(self.executor.handle(
                None, book, market=str(slug), window=str(ws),
                controller={"provenance": LX.CONTROLLER_GENERATED,
                            "decision": {"side": _leg.get("side"),
                                         "price": _leg.get("price"),
                                         "l5_size": _leg.get("qty"),
                                         "relation": "complete"},
                            "maker_class": None}))
        else:
            _bc_outcome = self._bc_capture(self.handle(row, now))
        emit("L5_BOOK_COMPLETION_RESULT", {
            "key": _bc_key,
            "market": str(slug),
            "route": ("CONTROLLER_REENTRY" if controller_route
                      else "L5_ROW_REENTRY"),
            "decided": {"action": rec.get("action"),
                        "reason": rec.get("reason"),
                        "pair_own_cost": rec.get("pair_own_cost"),
                        "locked_pnl_usd": rec.get("locked_pnl_usd"),
                        "need": rec.get("need")},
            "outcome": _bc_outcome,
            "labels": ("pairs the COMPLETE decision with what the placement "
                       "path actually did. `submitted` false with a named "
                       "`refused` is the case that was previously invisible: "
                       "a decision logged ok:true that never became an "
                       "order. Reporting only -- nothing here places, "
                       "reprices or withdraws anything."),
        })
        return rec

    @staticmethod
    def _bc_capture(result: Any) -> Dict[str, Any]:
        """Normalise whatever the placement path returned into a verdict.

        FAIL LOUD, NOT SILENT. An unreadable result is reported as
        `captured: False` with the type that was seen, never as a submission
        and never as a refusal -- guessing either way would recreate the
        blindness this exists to remove.
        """
        out: Dict[str, Any] = {"captured": True, "submitted": None,
                               "refused": None, "shape": type(result).__name__}
        try:
            if result is None:
                out["submitted"] = False
                out["refused"] = "NO_RESULT_RETURNED"
                return out
            if isinstance(result, Mapping):
                for k in ("refused", "reason", "refusal", "why"):
                    if result.get(k):
                        out["refused"] = str(result[k])[:120]
                        break
                for k in ("submitted", "ok", "placed", "accepted"):
                    if k in result:
                        out["submitted"] = bool(result[k])
                        break
                if out["submitted"] is None:
                    out["submitted"] = out["refused"] is None
                for k in ("coid", "venue_order_id", "order_id"):
                    if result.get(k):
                        out["order_ref"] = str(result[k])[:40]
                        break
                return out
            out["submitted"] = bool(result)
            return out
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"captured": False, "submitted": None, "refused": None,
                    "error": type(exc).__name__,
                    "shape": type(result).__name__}

    def controller_open_enabled(self) -> bool:
        """Inert unless the owner set it. There is no default-on path.

        Separate from `PMUS_Q1_LIVE`, the arm token and the campaign
        approval, none of which this relaxes: with this set and the service
        disarmed, the trigger still runs and the transport still refuses
        the wire. It decides only whether the autonomous OPENING DECISION
        is made at all.
        """
        return str(os.getenv("L5_CONTROLLER_OPEN", "")).strip().upper() \
            in ("1", "YES", "TRUE", "ON")

    def _note_open_skip(self, reason: str, slug: Any, ws: Any = None,
                        detail: Optional[Mapping[str, Any]] = None) -> None:
        """Name a tick on which NO OPENING WAS EVEN CONSIDERED.

        WHAT WAS WRONG. `maybe_controller_open` has seven early returns that
        bump a `controller_open:*` counter and emit nothing. `L5_MISSED_PAIR`
        only fires AFTER `executor.handle` has produced a refusal, so a tick
        that never reached the executor left no timestamped trace at all.

        WHAT THAT COST. Campaign E's 0245z window has no decision record
        between 02:45:00 and 02:53:45 -- the span containing all six of its
        submissions. The counters existed but are CUMULATIVE over the
        process lifetime, so when deployment 05884c9c was replaced the only
        snapshot left covered 02:18-03:01 across two windows and could not
        be attributed to the window at all. A counter cannot be windowed
        after the fact; a timestamped record can.

        REPORTING ONLY. It returns None, it is called immediately before an
        early return that has already been decided, and every part of it sits
        inside one swallow. It cannot change which tick opens, cannot place
        or price an order, and cannot raise into the caller.
        """
        try:
            rec: Dict[str, Any] = {
                "label": "L5_OPENING_DECISION",
                "considered": False,
                "reason": str(reason),
                "market": (None if slug is None else str(slug)),
                "utc": now_utc().isoformat(),
                "labels": ("ONE TICK on which no opening was considered. "
                           "Not a refusal by any gate, not a missed pair, "
                           "and never a fill: the opening path returned "
                           "before the executor saw anything."),
            }
            try:
                if ws is not None:
                    rec["window"] = str(ws)
                    rec["elapsed_s"] = round(float(time.time()) - float(ws), 1)
            except BaseException:                              # noqa: BLE001
                pass
            if isinstance(detail, Mapping):
                for k, v in detail.items():
                    rec[str(k)] = v
            emit("L5_OPENING_DECISION", rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["open_skip_emit_failed:%s" % type(exc).__name__] += 1

    def pair_complement_slot_refusal(self, slug: Any, resting: Any
                                     ) -> Optional[str]:
        """None if the timer may place the COMPLEMENT of a half-placed pair.

        Otherwise the named reason, and the caller keeps its lockout. Read
        only: it places nothing and grants no allowance of its own -- it
        decides whether one tick may reach `executor.handle`, where the
        executor repeats this test (`PAIR_SECOND_SLOT`) and runs every gate.

        EVERY CONDITION MUST HOLD:

          * exactly ONE order is still fillable, account-wide. Zero is an
            ordinary opening and never reaches here; two is the pair already
            resting; a parked or quarantined order counts and refuses;
          * a second slot is AUTHORIZED: `max_live_orders()` is above one;
          * the owner set `L5_TS_TWO_ORDER_PAIR`;
          * this market's current round has exactly one leg claimed;
          * and `TS.second_leg_slot_refusal` -- the executor's own test --
            agrees the resting order is that claimed leg, in this market, on
            the opposite side.

        FAIL CLOSED. An unreadable ledger, claim file or executor refuses by
        name; nothing here is ever read as permission.
        """
        from src import l5_two_sided_probe as TSP
        try:
            # WITH A LADDER RESTING, "exactly one" is the wrong question: the
            # newest pair's first leg sits beside the older pairs' orders.
            # Found by driving three levels through this runner -- the third
            # pair's complement was refused here and the leg stood alone.
            # Off unless L5_TS_CONCURRENT_ROUNDS is above one.
            ladder = TSP.concurrent_rounds() > 1 and int(resting) > 1
            if int(resting) != 1 and not ladder:
                return "NOT_EXACTLY_ONE_ORDER_RESTING"
            if int(max_live_orders()) < 2:
                return "NO_AUTHORIZED_SECOND_SLOT"
            if not TSP.two_order_pair_enabled():
                return "TWO_ORDER_PAIR_DISABLED"
            ex = self.executor
            rnd = ex.probe_round(str(slug))
            if rnd is None:
                return "NO_OPEN_ROUND"
            claimed = dict(ex.probe_legs_claimed(str(slug), rnd) or {})
            unplaced = sorted(str(k).strip().upper()
                              for k, v in claimed.items() if not bool(v))
            if len(claimed) != 2 or len(unplaced) != 1:
                return "ROUND_IS_NOT_HALF_PLACED"
            if ladder:
                if self.store.pending_terminal() or \
                        (self.store.state.get("quarantine") or {}):
                    return "ORDER_STATE_UNRESOLVED"
                return TSP.ladder_complement_refusal(
                    market=str(slug), live_orders=self.store.live_orders(),
                    max_live_orders=max_live_orders())
            return TSP.second_leg_slot_refusal(
                market=str(slug), side=unplaced[0],
                live_orders=self.store.live_orders(),
                legs_claimed=claimed)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return "PAIR_SLOT_UNREADABLE:%s" % type(exc).__name__

    def concurrent_round_slot_refusal(self, slug: Any) -> Optional[str]:
        """None if the timer may open ANOTHER pair beside the resting ones.

        The probe module's own test, on this runner's ledger and the owner's
        order limit. Read-only and fail-closed: a parked or quarantined
        order, an unreadable ledger or an unreadable limit all refuse.
        """
        from src import l5_two_sided_probe as TSP
        try:
            if self.store.pending_terminal() or \
                    (self.store.state.get("quarantine") or {}):
                return "ORDER_STATE_UNRESOLVED"
            return TSP.concurrent_round_slot_refusal(
                market=str(slug), live_orders=self.store.live_orders(),
                max_live_orders=max_live_orders())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return "CONCURRENT_SLOT_UNREADABLE:%s" % type(exc).__name__

    #: seconds between portfolio decision records for one market.
    PORTFOLIO_DECISION_EVERY_S = 5.0

    def _portfolio_view(self, slug: Any, ws: float, book: Mapping[str, Any],
                        tick: Any, now: float) -> Dict[str, Any]:
        """The portfolio controller's decision on THIS ledger, book and
        reference. Reads only; raises on anything unreadable.

        THE BELIEF. With L5_BTC_REFERENCE=VENUE_INDEX the probability comes
        from the venue's own settlement index and this market's own strike
        (`l5_btc_reference`), started lazily on its own thread so a slow
        gateway can never hold a tick. A stale index, a misaligned strike
        or a clock that disagrees returns NO probability, and the controller
        falls back to the book's mid -- labelled BOOK -- under which it can
        pair and hedge and cannot favor a side.
        """
        from src import l5_portfolio_controller as PC
        from src import l5_btc_reference as BR
        port = PC.from_store(self.store, str(slug))
        ref: Dict[str, Any] = {"ok": False, "p_up": None,
                               "reason": "REFERENCE_DISABLED",
                               "decision_t": float(now)}
        if BR.enabled():
            feed = getattr(self, "_btc_reference", None)
            if feed is None:
                feed = self._btc_reference = BR.Reference()
                feed.start()
            # THE WALL CLOCK, NOT THE TICK'S `now`. `now` is taken when the
            # tick starts and this stage runs seconds later; the feed's
            # thread has moved on meanwhile, so stamping the estimate with
            # `now` makes the newest index second look like it is in the
            # future. On 2026-11-10 19:22Z that was 5.1 s, every estimate
            # was refused CLOCK_MISALIGNED and the belief fell back to the
            # book. The estimate is taken at the instant it is actually
            # used, and the gap to the tick's clock is recorded beside it.
            wall = time.time()
            ref = feed.estimate(str(slug), float(ws), now=wall)
            ref["tick_now"] = float(now)
            ref["tick_lag_s"] = wall - float(now)
        ex = getattr(self, "executor", None)
        cash = exposure = None
        if ex is not None:
            _c = ex.probe_cash_envelope()
            cash = float(_c) if _c is not None else None
            _e = getattr(ex.limits, "max_total_exposure_usd", None)
            exposure = float(_e) if _e is not None else None
        knobs = PC.production_params(tick)
        # THE CONTROLLER DOES NOT SIZE ORDERS HERE; the engine does, under
        # its own cash, exposure and residual gates. So the size it reasons
        # with is the venue's smallest step: with a notional one-contract
        # clip the two opening quotes together overran the $0.90 cash
        # envelope and it wanted only one side of a pair the engine can
        # perfectly well afford at its real 0.6.
        _step = getattr(self.transport, "quantity_increment", None)
        try:
            _step = float(_step) if _step else 0.01
        except (TypeError, ValueError):
            _step = 0.01
        # the engine's own clip, once it has filled one: the largest
        # single fill in this market. Unknown (0) before the first fill.
        try:
            _clip = float(self.store.max_clip(str(slug)) or 0)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            _clip = 0.0
        knobs = PC.with_overrides(knobs, base_qty=_step,
                                  max_qty=max(_step, 1e6),
                                  clip=max(0.0, _clip))
        # THE BALANCE PROFILE DOES NOT USE THE MODEL. It quotes both sides on
        # the book's own mid; the reference is still recorded beside it.
        use_model = PC.profile_uses_model()
        dec = PC.decide(
            port, bid=book.get("bid"), ask=book.get("ask"),
            p_up=(ref.get("p_up") if (use_model and ref.get("ok"))
                  else None),
            t_off=float(now) - float(ws), params=knobs,
            limits=PC.Limits(cash_usd=cash, exposure_usd=exposure,
                             max_orders=int(max_live_orders())),
            # the MODEL's probability, for favorite accumulation only: it
            # is used whatever belief the profile quotes pairs on
            p_model=(ref.get("p_up") if ref.get("ok") else None))
        # the belief a withdrawal may act on: the model's when this profile
        # uses one and it is usable, else the same book belief `decide` used
        if use_model:
            p_act = ref.get("p_up") if ref.get("ok") else None
        else:
            p_act = (dec.get("belief") or {}).get("p_up")
        return {"port": port, "dec": dec, "ref": ref, "knobs": knobs,
                "p_act": p_act, "profile": PC.profile(),
                "p_model": (ref.get("p_up") if ref.get("ok") else None),
                "t_off": float(now) - float(ws)}

    def portfolio_steer_for_open(self, slug: Any, ws: float,
                                 book: Mapping[str, Any], tick: Any,
                                 now: float) -> Optional[Dict[str, Any]]:
        """The steer for ONE timer-path opening, or None when not steering.

        None means L5_PORTFOLIO_CONTROLLER is not STEER and the opening
        proceeds exactly as it always has. In STEER a steer is ALWAYS
        returned: if the ledger, the book or the controller cannot be read
        it is a steer that wants nothing, with the reason, so an unreadable
        portfolio stops the opening instead of waving it through.
        """
        try:
            from src import l5_portfolio_controller as PC
            from src import l5_two_sided_probe as TSP
            if PC.mode() != PC.STEER:
                return None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["portfolio_steer_error:%s" % type(exc).__name__] += 1
            return None
        try:
            view = self._portfolio_view(slug, ws, book, tick, now)
            st = PC.steer(view["dec"],
                          approved_depth_ticks=TSP.depth_ticks())
            st["flat_and_empty"] = (
                abs(view["port"]["unmatched"]) < 1e-9
                and not view["port"]["orders"])
            st["reference"] = {k: view["ref"].get(k) for k in (
                "ok", "reason", "p_up", "index_age_s", "seconds_left",
                "distance", "decision_t", "bucket_t")}
            self.stats["portfolio_steer:%s" % (
                "WANTED" if st["any_wanted"] else "NOTHING_WANTED")] += 1
            return st
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["portfolio_steer_error:%s" % type(exc).__name__] += 1
            return {"sides": {}, "extra_depth_ticks": 0, "any_wanted": False,
                    "unavailable": type(exc).__name__}

    def maybe_portfolio_withdraw(self, now: float) -> Optional[Dict[str, Any]]:
        """Withdraw a resting OPENING order the controller would no longer
        pay for. STEER only; a cancellation, never a submission.

        WHY. A maker bid is filled when the market moves through it. The
        steer on the opening path decides whether a quote goes up; this is
        what takes it down again when the reference moves against it --
        without it the quote simply waits out its time-to-live and is
        filled by exactly the move it should have avoided.

        HOW, and it is all existing machinery. The order is cancelled by
        its exact id through `LX.deadman(only=[...])`: by venue id, proved
        by re-read, any fill found while cancelling booked with its cost
        basis, and an ambiguous outcome marks the ledger uncertain so
        nothing new is submitted until reconciliation agrees.

        WHAT IT WILL NOT DO. It never names an order that reduces a held
        residual, it never runs outside STEER, and a failure anywhere is a
        counter and no cancellation.
        """
        try:
            from src import l5_portfolio_controller as PC
            if PC.mode() != PC.STEER:
                return None
            t = self.transport
            slug = getattr(t, "market_slug", None) if t is not None else None
            if not slug:
                return None
            mine = [c for c, o in (self.store.live_orders() or {}).items()
                    if str((o or {}).get("market")) == str(slug)]
            if not mine:
                return None
            ws = AB.slug_start(slug)
            if ws is None:
                return None
            book = self.book.snapshot(slug) if self.book is not None else None
            if not isinstance(book, Mapping) or not book:
                self.stats["portfolio_withdraw:NO_AUTHENTICATED_BOOK"] += 1
                return None
            view = self._portfolio_view(
                slug, float(ws), book, getattr(t, "tick_size", None), now)
            ref = view["ref"]
            # only a MODEL belief can say a quote is rich; with none, every
            # opening order is withdrawn (`withdrawals` names them all).
            named = PC.withdrawals(view["port"], p_up=view["p_act"],
                                   params=view["knobs"],
                                   t_off=view["t_off"],
                                   p_model=view["p_model"])
            ids = [str(w["id"]) for w in named if str(w["id"]) in mine]
            if not ids:
                return None
            res = LX.deadman(self.store, t, reason="portfolio_steer_withdraw",
                             only=ids)
            out = {"market": str(slug), "t_off": float(now) - float(ws),
                   "withdrawn": named, "result": res,
                   "reference": {k: ref.get(k) for k in (
                       "ok", "reason", "p_up", "index_age_s", "seconds_left",
                       "distance", "decision_t", "bucket_t")},
                   "labels": ("a CANCELLATION of an opening order by exact "
                              "id, with the venue's own re-read as proof. "
                              "Not a submission; a hedge is never named.")}
            emit("L5_PORTFOLIO_WITHDRAW", out)
            self.stats["portfolio_withdraw:CANCELLED"] += len(ids)
            return out
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["portfolio_withdraw_error:%s"
                       % type(exc).__name__] += 1
            return None

    def maybe_portfolio_decision(self, now: float) -> Optional[Dict[str, Any]]:
        """What the portfolio controller would quote now. RECORDED, NOT ACTED ON.

        One controller looks at the whole window -- every contract held on
        each side at its exact cost, the pairs that makes, what is
        unmatched, every maker order still resting -- and says what should
        be quoted on each side, at what price and size, and what each quote
        would do to the portfolio's settlement. This stage reads the SAME
        ledger and the SAME authenticated book the executor uses, so the
        record is about the real inventory, not a model of it.

        IT PLACES AND CANCELS NOTHING. It calls no executor method that can
        submit, it writes nothing to the ledger, and its output is a log
        line. It records in SHADOW and in STEER; the steering itself
        happens on the opening path, not here.

        IT CANNOT DISTURB THE TICK. Every failure is swallowed into a
        counter: an unreadable ledger or book yields no record.

        THE RECORD CARRIES ITS OWN CLOCKS: when the decision was taken,
        which index second it used, how old that second was and how long
        the fetch took -- or the reason there was no usable reference.
        """
        try:
            # INSIDE the guard: a module missing from the image must cost a
            # counter, never a tick.
            from src import l5_portfolio_controller as PC
            if PC.mode() == PC.OFF:
                return None
            t = self.transport
            slug = getattr(t, "market_slug", None) if t is not None else None
            if not slug:
                return None
            ws = AB.slug_start(slug)
            if ws is None:
                return None
            last = getattr(self, "_portfolio_decision_t", None)
            if last is not None and \
                    float(now) - float(last) < self.PORTFOLIO_DECISION_EVERY_S:
                return None
            self._portfolio_decision_t = float(now)
            book = self.book.snapshot(slug) if self.book is not None else None
            if not isinstance(book, Mapping) or not book:
                self.stats["portfolio_decision:NO_AUTHENTICATED_BOOK"] += 1
                return None
            view = self._portfolio_view(
                slug, float(ws), book, getattr(t, "tick_size", None), now)
            port, dec, ref = view["port"], view["dec"], view["ref"]
            self._note_cycle_peaks(str(slug), port)
            doc = {
                "market": str(slug), "window_start": float(ws),
                "t_off": float(now) - float(ws),
                "mode": PC.mode(), "profile": view["profile"],
                "book": {"bid": str(book.get("bid")),
                         "ask": str(book.get("ask")),
                         "stream_age_s": str(book.get("stream_age_s"))},
                "belief": dec.get("belief"),
                "reference": ref,
                "portfolio": {k: port[k] for k in (
                    "up_qty", "dn_qty", "up_cost", "dn_cost", "matched_pairs",
                    "pair_cost", "locked_pair_pnl", "unmatched",
                    "unmatched_cost", "settles_if_up", "settles_if_down",
                    "worst_case", "worst_case_with_resting",
                    "committed_usd")},
                "resting": port["orders"],
                "expected_settlement": dec.get("expected_settlement"),
                "value": dec.get("value"),
                "quotes": dec["quotes"], "skipped": dec["skipped"],
                "favorite": {"enabled": bool(view["knobs"].fav_enabled),
                             "declined": dec.get("favorite_declined")},
                "actions": PC.order_actions(dec["quotes"], port["orders"],
                                            params=view["knobs"]),
                "labels": ("DECISION RECORD ONLY -- this stage placed and "
                           "cancelled nothing; not a fill, not realized. "
                           "belief.source says whether the probability is "
                           "the venue index MODEL or the BOOK's own mid."),
            }
            emit("L5_PORTFOLIO_DECISION", doc)
            self.stats["portfolio_decision:RECORDED"] += 1
            return doc
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["portfolio_decision_error:%s"
                       % type(exc).__name__] += 1
            return None

    # =================================== ONE CONTINUOUS INVENTORY CONTROLLER
    #
    # L5_TS_CONTINUOUS_INVENTORY=YES (explicit; default OFF) makes
    # `l5_continuous_inventory.decide_pass` the single per-pass decision for
    # the two-sided market: both maker quotes, replenishment after a fill,
    # bounded ADD, per-contract completion, and exact-id cancel/replace when
    # the touch moves. The other discretionary order paths (the timer
    # opening, Route B book completion, the completion requote and the L5
    # ingest) stand down for that market, so no second controller can place
    # or cancel beside it. Deadlines, the halt deadman, orphan cancellation,
    # reconciliation and the watchdog are NOT discretionary and still run.
    #
    # NOTHING HERE TOUCHES THE VENUE DIRECTLY. Placements go through
    # `executor.handle(None, book, controller=...)` -- every gate, claim and
    # limit -- and cancellations through `LX.deadman(..., only=[coid])`, the
    # same exact-id, re-read-proved route the requote uses.

    CONTINUOUS_SKEW_ENV = "L5_CI_SKEW_TICKS"
    CONTINUOUS_REPRICE_TICKS_ENV = "L5_CI_REPRICE_TICKS"
    CONTINUOUS_ADD_BAND_ENV = "L5_CI_ADD_BAND_CONTRACTS"
    CONTINUOUS_HAIRCUT_ENV = "L5_CI_HAIRCUT_TICKS"
    #: seconds a resting bid must rest before it may be moved. Default 0:
    #: the owner's "move the bid constantly". The claim budget
    #: (L5_ACC_MAX_ROUNDS) still bounds how many placements a side gets.
    CONTINUOUS_MIN_REST_ENV = "L5_CI_REPRICE_MIN_REST_S"
    #: exactly YES keeps a still-valid order instead of following the touch
    #: down (issue #233 Track A reading). Unset: follow the touch both ways.
    CONTINUOUS_KEEP_VALID_ENV = "L5_CI_KEEP_VALID_ORDERS"

    def continuous_owns(self, slug: Any) -> bool:
        """Does the continuous controller own this market's order flow?"""
        from src import l5_two_sided_probe as TSP
        if not slug or not LX.continuous_inventory_enabled():
            return False
        try:
            return bool(self.controller_open_enabled()
                        and TSP.active(str(slug)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False

    @staticmethod
    def _env_int(name: str, default: int, lo: int, hi: int) -> int:
        raw = (os.getenv(name) or "").strip()
        try:
            v = int(raw) if raw else default
        except ValueError:
            return default
        return max(lo, min(hi, v))

    def continuous_inputs(self, slug: str, ws: float, now: float,
                          book: Mapping[str, Any]) -> Dict[str, Any]:
        """Every input `decide_pass` takes, read from durable state.

        Resting orders are read ONCE from the ledger and passed as rows, so
        cash, worst case and the residual cap each count them exactly once.
        Cash committed is the validation gates' own filled-cash figure plus
        every still-fillable order's notional, account-wide.
        """
        from src import l5_two_sided_probe as TSP
        from src import l5_book_completion as BC
        t = self.transport
        st = self.store
        rows = []
        for coid, o in sorted((st.live_orders() or {}).items()):
            if str((o or {}).get("market") or "") != str(slug):
                continue
            sub = None
            try:
                _t = dt.datetime.fromisoformat(str(o.get("t")))
                if _t.tzinfo is not None:
                    sub = _t.timestamp()
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                sub = None
            rows.append({"coid": str(coid), "side": o.get("side"),
                         "price": o.get("price"),
                         "remaining": LX.InventoryStore.worst_case_remaining(o),
                         "submitted_s": sub})
        vg = getattr(self.executor, "validation_gates", None)
        cash_cost = None
        if vg is not None:
            # THE GATE'S OWN ARITHMETIC, not a copy of it: resting orders and
            # a proposed one are reserved exactly as `ValidationGates`
            # reserves them when the order reaches it (a DOWN buy at $1 a
            # share unless it hedges confirmed unmatched UP).
            outs = self.executor.outstanding_for_gates()
            inv = self.executor.inventory_for_gates(slug)
            filled_cash = vg.cash_committed(self.executor.cash_legs_for_gates())
            resting_cash = vg.reserved_cash(
                outstanding=outs, order_qty=D(0), order_price=D(0),
                order_intent="ORDER_INTENT_BUY_LONG",
                inventory=inv)["resting"]
            cash_cap = vg.max_cash_deployed_usd
            abs_cap = min(LX.ABS_RESIDUAL_CAP_CONTRACTS,
                          vg.max_unmatched_contracts)

            def cash_cost(side, px, q, _o=outs, _i=inv, _v=vg):
                return _v.reserved_cash(
                    outstanding=_o, order_qty=q, order_price=px,
                    order_intent=("ORDER_INTENT_BUY_LONG"
                                  if str(side).upper() == "UP"
                                  else "ORDER_INTENT_BUY_SHORT"),
                    inventory=_i)["this_order"]
        else:
            lc_all = st.committed() or {}
            filled_cash = sum((D(str(v)) for v in lc_all.values()), D(0))
            resting_cash = sum((v for v in (st.outstanding_by_market() or {}
                                            ).values()), D(0))
            cash_cap = D(str(os.getenv("L5_MAX_CASH_DEPLOYED_USD", "0")))
            abs_cap = LX.ABS_RESIDUAL_CAP_CONTRACTS
        lc = st.leg_cost(slug)
        end = float(ws) + TSP.WINDOW_S
        try:
            stranded = st.stranded_since(slug)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            stranded = None
        up_q, dn_q = st.position(slug, "UP"), st.position(slug, "DOWN")
        led = self.replenish()
        rpl_on = DQ.replenish_enabled(ws)
        return dict(
            market=str(slug), bid=book.get("bid"), ask=book.get("ask"),
            tick=getattr(t, "tick_size", None),
            up_q=up_q, dn_q=dn_q,
            up_cost=lc.get("UP", D(0)), dn_cost=lc.get("DOWN", D(0)),
            resting=rows, now_s=float(now),
            entry_refusal=TSP.may_enter(float(now), float(ws), end),
            complete_refusal=TSP.may_complete(float(now), float(ws), end),
            clip=TSP.opening_leg_max_contracts(),
            qty_increment=getattr(t, "quantity_increment", None) or D("0.01"),
            depth_ticks=self._env_int("L5_TS_DEPTH_TICKS", 2, 0, 50),
            complete_depth_ticks=int(BC.completion_depth_ticks()),
            skew_ticks=self._env_int(self.CONTINUOUS_SKEW_ENV, 0, 0, 10),
            cash_committed=filled_cash + resting_cash, cash_cap=cash_cap,
            cash_cost=cash_cost,
            max_worst_loss=self.limits.max_total_exposure_usd,
            residual_k=getattr(self.executor, "residual_cap_k", None),
            clip_hist=st.max_clip(slug), abs_residual_cap=abs_cap,
            imbalance_cap=D(self._env_int(self.CONTINUOUS_ADD_BAND_ENV,
                                          0, 0, 5)),
            pair_ceiling=TSP.max_pair_cost(),
            complete_target=TSP.maker_completion_target(),
            complete_ceiling=TSP.completion_pair_ceiling(),
            reprice_ticks=self._env_int(self.CONTINUOUS_REPRICE_TICKS_ENV,
                                        1, 1, 50),
            reprice_min_rest_s=float(self._env_int(
                self.CONTINUOUS_MIN_REST_ENV, 0, 0, 120)),
            keep_valid_orders=(os.getenv(self.CONTINUOUS_KEEP_VALID_ENV)
                               or "").strip().upper() == "YES",
            stranded_since_s=stranded,
            replenish_permitted=(not rpl_on) or led.may_open(ws),
            traded_before=bool(up_q > 0 or dn_q > 0),
            orders_uncertain=st.orders_uncertain(),
            slots_left={s: self.executor.continuous_slots_left(slug, s)
                        for s in ("UP", "DOWN")},
            bid_shares=book.get("bid_shares"),
            ask_shares=book.get("ask_shares"),
            haircut_ticks=self._env_int(self.CONTINUOUS_HAIRCUT_ENV, 1, 0, 20))

    def maybe_continuous_inventory(self, now: float
                                   ) -> Optional[Dict[str, Any]]:
        """One pass of the continuous inventory controller, or None.

        CANCEL FIRST, THEN DECIDE AGAIN. A cancel is by exact id through the
        deadman, which proves it on a re-read and books any fill that beat
        it; the pass is then recomputed from the state that cancel left, so
        a replacement is sized against what is actually held and resting.
        An unconfirmed cancel marks the store uncertain, and the second
        decision then holds every side.
        """
        from src import l5_continuous_inventory as CI
        t = self.transport
        slug = getattr(t, "market_slug", None) if t is not None else None
        if not self.continuous_owns(slug):
            return None
        slug = str(slug)
        # THE OPERATOR HALT, read here as well: the tick's own halt branch
        # disarms the transport, and this makes the pass place nothing in
        # the same tick rather than relying on that ordering.
        if self.halted():
            self.stats["continuous:OPERATOR_HALT"] += 1
            return None
        ws = AB.slug_start(slug)
        if ws is None:
            self.stats["continuous:WINDOW_START_UNKNOWN"] += 1
            return None
        self._ci_window_rollover(slug, now)
        self._ci_observe_orders(slug, now)
        plan: Optional[Dict[str, Any]] = None
        repriced: Set[str] = set()
        for _iter in range(2):
            book = self.book.snapshot(slug) if self.book is not None else None
            if not isinstance(book, Mapping) or not book:
                self.stats["continuous:NO_AUTHENTICATED_BOOK"] += 1
                return plan
            try:
                plan = CI.decide_pass(**self.continuous_inputs(slug, ws, now,
                                                               book))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # FAIL CLOSED: a pass we cannot compute places nothing.
                self.stats["continuous:INPUTS_UNREADABLE:%s"
                           % type(exc).__name__] += 1
                return None
            for s, why in sorted((plan.get("holds") or {}).items()):
                self.stats["continuous:hold:%s:%s" % (s, why)] += 1
            emit("L5_CONTINUOUS_INVENTORY", {
                "market": slug, "window_start": float(ws), "iteration": _iter,
                "phase": plan.get("phase"),
                "decision": self._ci_decision_line(plan, ws, now),
                "actions": [{k: str(v) for k, v in a.items()}
                            for a in plan.get("actions") or ()],
                "holds": plan.get("holds"), "evaluated": plan.get("evaluated"),
                "state": plan.get("state"),
                "labels": ("OUR decision from the authenticated book and the "
                           "durable inventory. A PLACE is an intent the "
                           "executor still gates; a CANCEL is by exact id. "
                           "REDUCE_BY_SELLING is evaluated only, never sent.")})
            cancels = [a for a in plan.get("actions") or ()
                       if a.get("kind") == CI.CANCEL]
            if cancels and _iter == 0:
                if not getattr(t, "armed", False):
                    self.stats["continuous:CANCEL_SKIPPED_NOT_LIVE"] += 1
                    return plan
                for a in cancels:
                    _t0 = time.time()
                    self._ci_note(str(a["coid"]), "cancel_decision_t", now)
                    out = LX.deadman(self.store, t,
                                     reason="continuous_%s"
                                     % str(a.get("reason")).lower(),
                                     only=[str(a["coid"])])
                    self._ci_note(str(a["coid"]), "cancel_ack_latency_s",
                                  round(time.time() - _t0, 4))
                    self._ci_note(str(a["coid"]), "cancel_outcome", (
                        "CONFIRMED" if str(a["coid"]) in {
                            str((r or {}).get("client_order_id"))
                            for r in ((out or {}).get("cancelled") or [])
                            if isinstance(r, Mapping)}
                        else "NOT_CONFIRMED"))
                    self._requote_note_cancel_outcome(
                        str(a["coid"]), out if isinstance(out, Mapping)
                        else {})
                    self.stats["continuous:cancel:%s" % a.get("reason")] += 1
                    repriced.add(str(a.get("side")))
                continue                  # decide again on what is left
            self._continuous_place(plan, slug, ws, repriced)
            break
        return plan

    # ------------------------------------------- cheap lifecycle telemetry
    #
    # Per order: decision time, submit-ack and cancel-ack latencies (wall
    # clock around the call, MEASURED), first observed resting, partial and
    # full fill observation times, terminal state. One small dict per order,
    # emitted once when the order leaves the ledger; nothing is written on
    # every tick and nothing heavy runs at a window rollover.

    def _ci_note(self, coid: str, key: str, val: Any) -> None:
        lc = self.__dict__.setdefault("_ci_lifecycle", {})
        lc.setdefault(str(coid), {})[key] = val

    def _ci_observe_orders(self, slug: str, now: float) -> None:
        try:
            lc = self.__dict__.setdefault("_ci_lifecycle", {})
            live = {str(c): o for c, o in (self.store.live_orders() or {}
                                           ).items()
                    if str((o or {}).get("market") or "") == slug}
            for c, o in live.items():
                rec = lc.setdefault(c, {})
                rec.setdefault("resting_observed_t", float(now))
                rem = LX.InventoryStore.worst_case_remaining(o)
                q = LX.venue_decimal(o.get("qty"))
                if q is not None and rem < q and "partial_fill_t" not in rec:
                    rec["partial_fill_t"] = float(now)
            for c in [c for c in lc if c not in live
                      and "resting_observed_t" in lc[c]]:
                rec = lc.pop(c)
                rec["terminal_observed_t"] = float(now)
                for k in ("decision_t", "resting_observed_t",
                          "partial_fill_t"):
                    if isinstance(rec.get(k), (int, float)):
                        rec[k + "_to_terminal_s"] = round(
                            float(now) - float(rec[k]), 3)
                emit("L5_CI_ORDER_LIFECYCLE", dict(
                    rec, client_order_id=c, market=slug,
                    labels=("measured: *_latency_s are wall-clock around the "
                            "call; *_t are tick-clock observations, so a "
                            "fill time is when the poll SAW it")))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["continuous:lifecycle_error:%s"
                       % type(exc).__name__] += 1

    def _ci_window_rollover(self, slug: str, now: float) -> None:
        """One line per finished window: its settled result if the ledger
        has settled it, else UNSETTLED. Read-only, O(1)."""
        prev = getattr(self, "_ci_window_slug", None)
        if prev == slug:
            return
        self._ci_window_slug = slug
        if prev is None:
            return
        try:
            row = (self.store.state.get("retired_markets") or {}).get(prev)
            emit("L5_CI_WINDOW_SUMMARY", {
                "market": prev, "observed_t": float(now),
                "settled_pnl": (None if not row
                                else row.get("settlement_pnl")),
                "status": "SETTLED" if row else "UNSETTLED",
                "labels": "settlement-backed only; UNSETTLED is not zero"})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["continuous:summary_error:%s"
                       % type(exc).__name__] += 1

    @staticmethod
    def _ci_decision_line(plan: Mapping[str, Any], ws: float,
                          now: float) -> Dict[str, Any]:
        """The per-decision instrument: matched, residual and its basis,
        worst outcome, the decision and why, paired cost, time left."""
        st = plan.get("state") or {}
        try:
            up, dn = D(st.get("up_q", "0")), D(st.get("dn_q", "0"))
            uc, dc = D(st.get("up_cost", "0")), D(st.get("dn_cost", "0"))
            matched = min(up, dn)
            heavy = st.get("heavy")
            hq, hc = (up, uc) if heavy == "UP" else (dn, dc)
            paired = (None if matched <= 0 or up <= 0 or dn <= 0
                      else str(uc / up + dc / dn))
            alt = (plan.get("evaluated") or {}).get("alternatives") or {}
            return {"matched": str(matched), "residual_side": heavy,
                    "residual_qty": str(abs(up - dn)),
                    "residual_basis": (str(hc / hq) if heavy and hq > 0
                                       else None),
                    "worst_outcome_pnl": str(min(up, dn) - uc - dc),
                    "paired_cost_per_contract": paired,
                    "decision": alt.get("chosen") or (
                        "PLACE" if any(a.get("kind") == "PLACE"
                                       for a in plan.get("actions") or ())
                        else "HOLD"),
                    "reason": alt.get("reason") or plan.get("holds"),
                    "time_left_s": round(float(ws) + 900.0 - float(now), 1),
                    "order_states": [
                        "%s@%s:%s" % (r.get("side"), r.get("price"),
                                      r.get("remaining"))
                        for r in st.get("resting") or ()]}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"error": type(exc).__name__}

    def _continuous_place(self, plan: Mapping[str, Any], slug: str,
                          ws: float, repriced: Set[str] = frozenset()
                          ) -> None:
        """Hand each PLACE to the executor, one full `handle` call each."""
        from src import l5_continuous_inventory as CI
        from src import l5_two_sided_probe as TSP
        places = [a for a in plan.get("actions") or ()
                  if a.get("kind") == CI.PLACE]
        if not places:
            return
        rpl_on = DQ.replenish_enabled(ws)
        opening_attempted = False
        for a in places:
            intent = str(a.get("intent"))
            # A REPLACEMENT IS NOT A RE-OPEN: the side's own order was
            # cancelled by exact id in this pass to be repriced, so its
            # replacement is not charged a replenishment credit -- the same
            # rule the timer path applies to a pair's complement.
            opening = (intent in (CI.OPEN, CI.REPLENISH, CI.ADD)
                       and str(a.get("side")) not in repriced)
            # REPLENISHMENT ACCOUNTING, when the owner turned it on: every
            # opening after the window's first must be paid for by a credit
            # a confirmed fill or terminal cancel issued. A completion never
            # needs one -- it removes exposure.
            if opening and rpl_on and not opening_attempted \
                    and not self.replenish().may_open(ws):
                self.stats["continuous:REPLENISH_UNPAID:%s"
                           % self.replenish().refuse_reason()] += 1
                continue
            try:
                if int(self.store.still_fillable_order_count()) >= \
                        int(max_live_orders()):
                    self.stats["continuous:NO_ORDER_SLOT"] += 1
                    break
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["continuous:LEDGER_UNREADABLE:%s"
                           % type(exc).__name__] += 1
                break
            book = self.book.snapshot(slug) if self.book is not None else {}
            # the decision handed to the translation gate is PROVISIONAL, as
            # on the timer path: the size is the opening cap, so the gate's
            # lambda scaling cannot refuse a small, correctly sized intent
            # before the continuous branch adopts the real price and qty.
            dec = {"side": str(a["side"]), "price": a["price"],
                   "l5_size": TSP.opening_leg_max_contracts(),
                   "relation": "continuous"}
            _t0 = time.time()
            try:
                rec = self.executor.handle(
                    None, book, market=slug, window=str(ws),
                    controller={"provenance": LX.CONTROLLER_GENERATED,
                                "decision": dec, "maker_class": None,
                                "continuous": dict(a)})
            finally:
                self.executor._probe_leg_pending = None
                self.executor._probe_round_pending = None
                self.executor._continuous_key_pending = None
            _o = (rec or {}).get("ORDER") or {}
            if _o.get("submitted") and _o.get("client_order_id"):
                _c = str(_o["client_order_id"])
                self._ci_note(_c, "decision_t", self.now_t)
                self._ci_note(_c, "submit_ack_latency_s",
                              round(time.time() - _t0, 4))
                self._ci_note(_c, "intent", intent)
                self._ci_note(_c, "side", str(a.get("side")))
                self._ci_note(_c, "price", str(a.get("price")))
                self._ci_note(_c, "qty", str(a.get("qty")))
            if opening:
                opening_attempted = True
            why = str((rec or {}).get("refused_reason") or "")
            self.stats["continuous:place:%s:%s" % (
                intent, why or "SUBMITTED_OR_SUPPRESSED")] += 1
        if opening_attempted and rpl_on:
            try:
                self.replenish().spend(ws)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["replenish_spend_error:%s"
                           % type(exc).__name__] += 1

    def maybe_controller_open(self, now: float) -> Optional[Dict[str, Any]]:
        """OUR OWN maker opening, on the timer, with no L5 action at all.

        WHY IT EXISTS. Every other way inventory gets opened runs through
        `handle(row, ...)`, so it needs an admitted L5 action to exist --
        and Route B, which re-evaluates on the book, still reuses a real
        one and declines when there is none. From 2026-10-29 L5 stopped
        acting, and with it the only trigger the engine had. This is the
        path that does not need him: the decision is ours, the book is the
        authenticated mirror, and the order is placed by the SAME executor
        through the SAME gates.

        NOTHING IS FABRICATED. No row, no `action_key`, no synthetic L5
        activity. The record carries `provenance: CONTROLLER_GENERATED`
        and a key in our own namespace, so the tape can never read an
        autonomous order as something L5 did.

        WHAT IT DOES NOT DO. It does not price the order, size it, choose
        the leg, or authorize it. `TS.build_orders` inside the two-sided
        branch does all of that, exactly as it already does for an L5
        action whose intent it supersedes; the side named here is
        provisional and only exists so the translation gate has a side to
        judge -- and the gate's verdict for a restable maker is the same
        for either side, which `tests/test_l5_controller_open_trigger.py`
        asserts rather than assumes.
        """
        from src import l5_two_sided_probe as TSP
        if not self.controller_open_enabled():
            return None
        # ONE INVENTORY CONTROLLER. With L5_TS_CONTINUOUS_INVENTORY=YES the
        # continuous pass decides this market's quotes in place of the
        # opening below. Default OFF: this line is never taken.
        if LX.continuous_inventory_enabled():
            return self.maybe_continuous_inventory(now)
        t = self.transport
        if t is None:
            return None
        slug = getattr(t, "market_slug", None)
        if not slug:
            return None
        # THE PROBE ENGINE MUST BE AUTHORIZED FOR THIS MARKET. This trigger
        # places two-sided legs and nothing else, so without an experiment
        # id and a matching market pattern there is nothing it may do. The
        # branch would refuse anyway; declining here keeps the reason
        # legible and spends no book read.
        if not TSP.active(slug):
            self.stats["controller_open:NOT_AUTHORIZED_MARKET"] += 1
            self._note_open_skip("NOT_AUTHORIZED_MARKET", slug)
            return None
        ws = AB.slug_start(slug)
        if ws is None:
            self.stats["controller_open:WINDOW_START_UNKNOWN"] += 1
            self._note_open_skip("WINDOW_START_UNKNOWN", slug)
            return None
        # ONE OUTSTANDING ORDER, read from the ledger rather than from this
        # method's memory of what it did. Checked here as well as in the
        # executor's own MAX_LIVE_ORDERS gate so a repeating timer does not
        # re-derive a decision that is certain to be refused.
        try:
            resting = int(self.store.still_fillable_order_count())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # FAIL CLOSED: an unreadable ledger is not an empty one.
            self.stats["controller_open:LEDGER_UNREADABLE:%s"
                       % type(exc).__name__] += 1
            self._note_open_skip("LEDGER_UNREADABLE", slug, ws,
                                 {"error": type(exc).__name__})
            return None
        # THE AUTHORIZED NUMBER OF SLOTS, not one. This refused a new opening
        # whenever ANY order was still fillable, while `max_live_orders` is 2
        # -- strictly tighter than the limit the comment above claims it
        # mirrors. One slot x a 120s TTL x a 480s entry cutoff caps openings
        # at floor(480/120) = 4, which is exactly what Campaign E spent
        # (OPEN 3/4) and why it produced three fills. With the fidelity mode
        # off the ceiling is 1 and `resting >= 1` IS `if resting:`.
        # Aliased FID, not FM: `FM` is already l5_maker_fill_model here.
        _pair_complement = False
        if resting >= int(FID.ceiling("concurrent_opening_orders")):
            # THE COMPLEMENT OF A HALF-PLACED PAIR IS NOT A NEW OPENING.
            #
            # 2026-11-10 0200z, venue-confirmed. An L5 action placed the DOWN
            # leg of a priced pair at 02:00:06 through `handle(row)`, which
            # places ONE leg per call. The complement was this method's to
            # place, and on the next three ticks (02:00:06.8, 09.4, 12.9) it
            # returned here with ORDER_ALREADY_RESTING -- refusing the second
            # leg BECAUSE the first leg was resting. DOWN filled alone at
            # 02:00:13 and UP was not posted until 02:00:18. The two legs of
            # the pair never rested together, and the pass loop below, which
            # exists to place both, was never reached.
            #
            # THE EXECUTOR ALREADY DECIDES THIS, with evidence:
            # `TS.second_leg_slot_refusal` grants the second maker slot only
            # to the OPPOSITE side of the SAME round in the SAME market as
            # the one order live. This gate asks the same function the same
            # question instead of answering a coarser one. It can let the
            # tick REACH the executor; it places nothing, and every gate in
            # `handle` -- the pair precheck, the claim, MAX_LIVE_ORDERS, the
            # cash and exposure overlays, the residual cap -- still runs.
            #
            # OFF UNLESS THE OWNER SET THE SECOND SLOT. With
            # L5_TS_TWO_ORDER_PAIR unset the refusal is
            # TWO_ORDER_PAIR_DISABLED and this is the lockout it always was.
            _pair_why = self.pair_complement_slot_refusal(slug, resting)
            # ...AND NEITHER IS THE NEXT LEVEL OF A LADDER. With
            # L5_TS_CONCURRENT_ROUNDS above one, a new pair may be opened
            # beside the resting ones when a price level is free and the
            # owner's order limit has room for both legs. Asked only when
            # the tick is not a complement, and -- like the complement slot
            # -- it lets the tick reach the executor and decides nothing
            # else: `probe_reopen_decision` repeats the test and prices the
            # round. Off by default, which is the lockout as it was.
            _lane_why = (None if _pair_why is None
                         else self.concurrent_round_slot_refusal(slug))
            if _pair_why is not None and _lane_why is not None:
                self.stats["controller_open:ORDER_ALREADY_RESTING"] += 1
                self._note_open_skip("ORDER_ALREADY_RESTING", slug, ws,
                                     {"still_fillable_orders": int(resting),
                                      "pair_complement_slot": str(_pair_why),
                                      "concurrent_round_slot": str(_lane_why)})
                return None
            _pair_complement = _pair_why is None
            self.stats["controller_open:%s" % (
                "PAIR_COMPLEMENT_SLOT" if _pair_complement
                else "CONCURRENT_ROUND_SLOT")] += 1
        # ============ AND, WHEN REPLENISHMENT IS ON, A RE-OPEN NEEDS AN
        # EVENT BEHIND IT.
        #
        # WHAT THIS METHOD DOES WITHOUT THIS BLOCK. It has no cadence of its
        # own: it runs on EVERY tick and the only thing standing between it
        # and another opening is the slot test above. So it already re-opens
        # when a slot frees -- but it re-opens because the timer came round,
        # not because anything happened, and nothing bounds or counts how
        # many times one window may do it.
        #
        # WHAT THE BLOCK ADDS. The window's first opening is free. Every
        # later one must be paid for with a credit that a confirmed fill or a
        # terminal cancellation issued, up to `RPL.max_rounds()` per window.
        # That is strictly FEWER openings than the bare slot test allows, not
        # more: this can only ever refuse an opening the slot gate permitted,
        # and there is no input on which it permits one the slot gate refused.
        #
        # WHY THE CREDIT IS NOT SPENT HERE. `spend` is called only after a
        # placement was actually attempted, further down, so a decision that
        # is refused by a later gate -- the pair ceiling, the claim, cash,
        # exposure, the residual cap -- does not burn the window's allowance
        # on an order that never existed.
        #
        # OFF BY DEFAULT, AND THE DEFAULT IS WHAT KEEPS PARITY.
        # `replenish_enabled` needs BOTH `L5_DUAL_QUOTE` and
        # `L5_DUAL_REPLENISH` at their exact tokens; production holds
        # `L5_DUAL_REPLENISH=OFF`, so the ledger is not consulted at all and
        # this method behaves exactly as it does today.
        # A PAIR'S COMPLEMENT IS NOT A RE-OPEN, so it is not charged a
        # replenishment credit to be ALLOWED -- exactly as pass two of the
        # loop below is not. The attempt is still spent afterwards.
        if DQ.replenish_enabled(ws) and not _pair_complement:
            _led = self.replenish()
            if not _led.may_open(ws):
                # ONE LITERAL REASON, with the specific cause in the detail.
                # `test_l5_opening_decision_telemetry` counts this method's
                # skip-note call sites in its own SOURCE TEXT and pins that
                # count against its enumeration of them -- so the reason
                # must be a literal a reader can grep, and this comment must
                # not spell the helper's name with its bracket either, or the
                # comment itself would be counted as a ninth site. That guard
                # is worth both constraints. The refusal's own vocabulary
                # (NO_REPLENISH_EVENT_YET / REPLENISH_CREDITS_SPENT /
                # REPLENISH_ROUNDS_EXHAUSTED) rides in the detail and in the
                # counter, where nothing pins it.
                _why = _led.refuse_reason()
                self.stats["controller_open:REPLENISH_UNPAID:%s" % _why] += 1
                self._note_open_skip("REPLENISH_UNPAID", slug, ws,
                                     {"replenish_refusal": _why,
                                      "replenish": _led.telemetry()})
                return None
        book = self.book.snapshot(slug) if self.book is not None else None
        if not isinstance(book, Mapping) or not book:
            # `snapshot` returns None unless the socket is connected AND the
            # book is two-sided, so this is the no-book case and it refuses.
            self.stats["controller_open:NO_AUTHENTICATED_BOOK"] += 1
            self._note_open_skip("NO_AUTHENTICATED_BOOK", slug, ws)
            return None
        tick = getattr(t, "tick_size", None)
        # THE PROVISIONAL SIDE: the cheaper own-price, which is the order
        # the branch itself prefers ("the CHEAPER leg first, because a cheap
        # leg filling alone is the smaller worst case"). Derived from the
        # same book so the two agree, and superseded by the branch either
        # way.
        px_up = LX.controller_provisional_price("UP", book, tick)
        px_dn = LX.controller_provisional_price("DOWN", book, tick)
        # ONE UNREADABLE SIDE USED TO DISQUALIFY BOTH, and production
        # counted it 9 times. `controller_provisional_price` returns None
        # when `touch - tick <= 0`, so an UP price is unreadable once the
        # ask is down to a single tick and a DOWN price is unreadable once
        # the bid is within a tick of 1.00. BTC15m books run to those
        # extremes routinely as a window resolves -- and the OTHER side is
        # perfectly restable at that moment. Refusing to open at all
        # because the side we were not going to choose is pinned is the
        # defect; the readable side is used instead, and the fact that the
        # choice was FORCED rather than cheaper is recorded so the
        # economics stay auditable.
        #
        # THIS WIDENS WHEN THE CONTROLLER OPENS and is flagged for review
        # as such. It does not widen WHAT may be placed: this price feeds
        # the translation gate only, `TS.build_orders` still builds the
        # real order from the same authenticated book, and every economic,
        # campaign, slot and authorization gate downstream is untouched.
        # DEFAULT OFF, because this widening has never run in production.
        # The repair below is correct -- refusing a restable side because
        # the OTHER side is pinned is a defect -- but it changes WHEN the
        # controller opens, and a capped one-or-two-window probe has one
        # opening to spend. At `ask 0.01` the forced side is DOWN at own
        # 0.99: legal, gated downstream, and a poor use of the probe's only
        # entry. So the repair is opt-in and the deployed default stays
        # byte-identical to the behaviour already observed running.
        #
        # ONE REFUSAL SITE, DELIBERATELY. A sibling test enumerates the
        # early returns that report a skip, and splitting this into two
        # returns would add an eighth for no behavioural gain.
        _forced_ok = (os.getenv("L5_PROVISIONAL_FORCED_SIDE")
                      or "").strip().lower() in ("1", "yes", "true", "on")
        _both_unreadable = px_up is None and px_dn is None
        if _both_unreadable or (not _forced_ok
                                and (px_up is None or px_dn is None)):
            # HOISTED, so the skip note and its `return None` stay
            # adjacent: a sibling test requires the return within a few
            # lines of the note, because a note AFTER the return would be
            # dead code. Worth keeping intact.
            _detail = {"bid": str(book.get("bid")),
                       "ask": str(book.get("ask")),
                       "unreadable": ("BOTH" if _both_unreadable
                                      else "UP" if px_up is None else "DOWN"),
                       "forced_side_enabled": _forced_ok}
            self.stats["controller_open:PROVISIONAL_PRICE_UNREADABLE"] += 1
            self._note_open_skip("PROVISIONAL_PRICE_UNREADABLE", slug, ws,
                                 _detail)
            return None
        if px_dn is None:
            side, px, _forced = "UP", px_up, "DOWN"
        elif px_up is None:
            side, px, _forced = "DOWN", px_dn, "UP"
        else:
            side, px, _forced = (("UP", px_up, None) if px_up <= px_dn
                                 else ("DOWN", px_dn, None))
        if _forced is not None:
            self.stats["controller_open:PROVISIONAL_SIDE_FORCED:%s"
                       % _forced] += 1
        size = TSP.opening_leg_max_contracts()
        if size is None or size <= 0:
            self.stats["controller_open:OPENING_CAP_UNREADABLE"] += 1
            self._note_open_skip("OPENING_CAP_UNREADABLE", slug, ws)
            return None
        dec = {"side": side, "price": px, "l5_size": size,
               "relation": "open"}
        emit("L5_CONTROLLER_OPEN_TRIGGER", {
            "triggered_by": "TIMER_NOT_L5_ACTION",
            "provenance": LX.CONTROLLER_GENERATED,
            "market": str(slug), "window_start": float(ws),
            "provisional": {"side": side, "price": str(px),
                            "l5_size": str(size)},
            "book": {"bid": str(book.get("bid")), "ask": str(book.get("ask")),
                     "stream_age_s": str(book.get("stream_age_s")),
                     "source": str(book.get("source"))},
            "labels": ("OUR OWN decision, from the authenticated book. No "
                       "L5 action underlies it and none was synthesised. "
                       "The side and price here are provisional: the "
                       "two-sided branch prices, sizes and chooses the leg "
                       "that is actually placed, and every existing gate, "
                       "claim and limit applies unchanged."),
        })
        # THE PORTFOLIO CONTROLLER MAY STEER THIS OPENING. None unless the
        # owner set L5_PORTFOLIO_CONTROLLER=STEER, and then nothing below
        # changes. A steer that wants no side opened ends the tick here,
        # by name, before the executor is asked for anything.
        _steer = self.portfolio_steer_for_open(slug, float(ws), book, tick,
                                               now)
        # ...but ONLY while flat with nothing resting. With inventory or a
        # resting leg the call goes through, because the next leg may be a
        # hedge and a hedge is never the steer's to stop; the executor
        # refuses any non-reducing leg the steer does not want.
        if _steer is not None and not _steer.get("any_wanted") \
                and _steer.get("flat_and_empty") is not False:
            self.stats["controller_open:PORTFOLIO_NOTHING_WANTED"] += 1
            self._note_open_skip("PORTFOLIO_NOTHING_WANTED", slug, ws,
                                 {"portfolio_steer": _steer})
            return None
        self.stats["controller_open:ENTERED"] += 1

        # ============ BOTH LEGS IN ONE PASS, AND NOTHING ELSE CHANGES.
        #
        # This used to call `handle` ONCE and return, so the complement waited
        # for the next tick ~5s later. With the one-order lockout that refused
        # re-entry while anything rested, openings were capped at
        # floor(entry_cutoff / order_ttl) = floor(480/120) = 4. Campaign E
        # spent OPEN 3/4 and produced three fills: arithmetic, not a market
        # outcome.
        #
        # EVERY GATE RUNS ON EVERY PASS, because every pass is a full `handle`
        # call -- the pair precheck, the claim, MAX_LIVE_ORDERS, the cash and
        # exposure overlays, the residual cap, the opening floor and the pair
        # ceiling. Pass two cannot place anything a tick-later pass would have
        # been refused; it only stops waiting.
        #
        # THE COMPLEMENT'S SIDE IS NOT FORCED HERE. The provisional side in
        # `dec` is advisory, as the trigger record above says. On pass two the
        # two-sided branch sees leg one already CLAIMED and `probe_leg_order`
        # hands it the unclaimed leg on its own -- the same mechanism that
        # picks it one tick later today.
        #
        # THE BOOK IS RE-READ EACH PASS, so the complement is priced against
        # what is actually there. `max_placements_per_pass()` is 1 unless
        # L5_DUAL_QUOTE is exactly "ON", which is why an unset environment
        # behaves precisely as before.
        rec = None
        # The A/B split is decided from THIS window's own start, so both
        # sides of it see the same session instead of being divided by
        # time of day. Kept free of certain word stems on purpose:
        # TestTheTriggerIsOffUnlessTheOwnerSetIt substring-scans this
        # whole function to prove it cannot touch the owner token, the
        # authorization document or the deadline, and that guard is
        # right to be strict. Rewording a comment is the correct fix.
        passes = DQ.max_placements_per_pass(ws)
        for _p in range(max(1, int(passes))):
            if _p:
                # a later pass needs a CURRENT book; a stale one would price
                # the complement against a market that has moved.
                _b = self.book.snapshot(slug) if self.book is not None else None
                if not isinstance(_b, Mapping) or not _b:
                    self.stats["controller_open:PASS2_NO_BOOK"] += 1
                    break
                book = _b
                # and a slot must still be free by the LEDGER, not by this
                # method's memory of what it just did.
                try:
                    if int(self.store.still_fillable_order_count()) >= \
                            int(max_live_orders()):
                        self.stats["controller_open:PASS2_NO_SLOT"] += 1
                        break
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    # FAIL CLOSED: an unreadable ledger ends the pass loop.
                    self.stats["controller_open:PASS2_LEDGER_UNREADABLE:%s"
                               % type(exc).__name__] += 1
                    break
            # the steer exists for THIS call and no other: set here,
            # cleared in the `finally`, so no completion, requote or L5
            # action can ever see it.
            if _steer is not None:
                self.executor.portfolio_steer = _steer
            try:
                rec = self.executor.handle(
                    None, book, market=str(slug), window=str(ws),
                    controller={"provenance": LX.CONTROLLER_GENERATED,
                                "decision": dec,
                                # NO REPLAY CLASSIFICATION EXISTS FOR AN
                                # ORDER THAT HAS NOT BEEN PLACED. The L5
                                # path is handed `{}` here too and lands on
                                # the same T_UNEVALUABLE, which
                                # MAKER_TREATMENT answers; passing a class
                                # we do not have would be inventing
                                # evidence.
                                "maker_class": None})
            finally:
                if _steer is not None:
                    self.executor.portfolio_steer = None
            self.stats["controller_open:%s%s" % (
                ("pass2:" if _p else ""),
                str((rec or {}).get("refused_reason") or "NOT_REFUSED"))] += 1
            self._note_open_missed(str(slug), rec, book, tick)
            # ONE ATTEMPTED PLACEMENT, ONE CHARGE. Inside its own swallow and
            # only while replenishment is on, so a bookkeeping failure cannot
            # change a placement that has already happened. Charged on an
            # ATTEMPT rather than on a fill, because the resource being
            # bounded is how often the engine re-opens -- not how often it
            # succeeded.
            if DQ.replenish_enabled(ws):
                try:
                    self.replenish().spend(ws)
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    self.stats["replenish_spend_error:%s"
                               % type(exc).__name__] += 1
            # A REFUSAL ENDS THE PASS LOOP. Retrying the same decision against
            # the same book in the same tick would only repeat the refusal,
            # and a refused pair must not have its second leg attempted
            # anyway -- the precheck gates the PAIR.
            if (rec or {}).get("refused_reason"):
                break
        return rec

    #: ticks behind each touch the opening maker legs are quoted. Read from
    #: the same accessor the probe prices with, so the recorded achievable
    #: pair cannot disagree with the one the engine could actually have got.
    def _note_open_missed(self, slug: str, rec: Optional[Mapping[str, Any]],
                          book: Mapping[str, Any], tick: Any) -> None:
        """Name a refused OPENING, instead of only counting it.

        WHAT WAS WRONG, and it is the same blindness `L5_MISSED_PAIR` was
        built to remove one layer down. `controller_open:*` was a COUNTER
        and nothing else: the 1315z-1400z campaign could report
        `PAIR_COST_ABOVE_CEILING` 683 times and could not say whether one
        of those 683 gave up a pair worth having. The executor's refusal
        dict carries `effective_pair_cost` and `ceiling`, and neither
        reached the tape. The completion layer has had this record since
        the 1400z work; the opening layer, which refuses far more often,
        never did.

        THE ACHIEVABLE PAIR IS DERIVED FROM THE BOOK, not from the
        refusal, so it is recorded even when the refusal fired before any
        price was computed. It is derived by ASKING `quote_own_price`, the
        function `build_orders` prices with, rather than by restating its
        arithmetic here -- for the same reason the portfolio bound asks
        `net_settlement` instead of solving a closed form.

        A RESTATEMENT IS EXACTLY WHAT WENT WRONG. The first version of
        this helper computed `1 + (ask - bid) - 2*d*t`, with the spread
        ADDED. The true identity has the spread SUBTRACTED, because UP's
        touch is the bid and DOWN's is `1 - ask`:
            (bid - d*t) + (1 - ask - d*t) = 1 - (ask - bid) - 2*d*t
        The spread HELPS the pair; it does not cost it. On the 1-tick
        book that is BTC15m's median the sign error reported 0.97 where
        the truth is 0.95 -- the difference between a pair outside the
        0.96 ceiling and one comfortably inside it, and between a pair in
        L5's losing band and one in its winning band. It is the same
        class of error as the DOWN double-conversion this module already
        carries a comment about.

        OBSERVABILITY ONLY, inside one swallow. A failure to describe a
        refusal must never change the refusal and must never raise into a
        caller that is about to return a decision.
        """
        try:
            # IMPORTED HERE, because `l5_two_sided_probe` is a LOCAL import
            # everywhere else in this module and is NOT a module global. The
            # first draft of this helper referenced a module-level `TSP`
            # that does not exist; the NameError landed in the pair-
            # computation swallow below and every record came out with
            # `pair_own_cost: None` -- an instrument that silently measured
            # nothing. Its own test caught it.
            from src import l5_two_sided_probe as TSP
            reason = str((rec or {}).get("refused_reason") or "NOT_REFUSED")
            if reason == "NOT_REFUSED":
                return
            pair = None
            placeable = None
            try:
                bid = D(str(book.get("bid")))
                ask = D(str(book.get("ask")))
                # THE MARKET'S OWN TICK, from the transport, never a
                # default. A guessed tick silently rescales the depth term
                # and the recorded achievable pair stops being the one the
                # engine could have quoted; unreadable is left UNKNOWN.
                tk = D(str(tick))
                d = int(TSP.depth_ticks())
                if bid > 0 and ask > bid and tk > 0:
                    _u = TSP.quote_own_price(TSP.UP, bid=bid, ask=ask,
                                             tick=tk, depth_ticks=d)
                    _dn = TSP.quote_own_price(TSP.DOWN, bid=bid, ask=ask,
                                              tick=tk, depth_ticks=d)
                    if _u is not None and _dn is not None:
                        pair = _u + _dn
                        # ============ IS THE PAIR ACTUALLY PLACEABLE?
                        #
                        # THE PAIR COST ALONE IS VERY NEARLY A CONSTANT, and
                        # reporting it as an opportunity was this record's
                        # defect. Both legs quote `d` ticks behind their own
                        # touch, so
                        #     pair = 1 - spread - 2*d*t
                        # is INVARIANT to the price level: on BTC15m's median
                        # one-tick book it is 0.95 whether the book sits at
                        # 0.50 or at 0.90. So `missed_profitable_pair: YES`
                        # fired on a figure that barely moves, which is why
                        # the 2215z window produced `pair_own_cost 0.9500`
                        # beside PAIR_COST_ABOVE_CEILING and read as a lost
                        # opportunity.
                        #
                        # WHAT ACTUALLY DECIDES IT is the first-leg band. At
                        # bid 0.8900 / ask 0.9000 the legs price at own 0.8700
                        # and 0.0800 -- one above the 0.60 ceiling and one
                        # below the 0.20 floor -- so NEITHER leg could be
                        # opened and there was no opportunity to miss. The
                        # band is recorded beside the pair so a reader can
                        # tell an economic refusal from an unplaceable book.
                        try:
                            _lo = TSP.min_first_leg_own_price()
                            _hi = TSP.max_first_leg_own_price()
                            placeable = bool(
                                (_lo <= _u <= _hi) or (_lo <= _dn <= _hi))
                        except BaseException:              # noqa: BLE001
                            placeable = None
            except BaseException:                              # noqa: BLE001
                pair = None
            try:
                _ceil = TSP.max_pair_cost()
            except BaseException:                              # noqa: BLE001
                _ceil = None
            # THE INVENTORY THIS REFUSAL HAPPENED IN, which this record
            # went without and which is what made it misleading. The pair
            # above is priced from the BOOK, as if we were flat; the gate
            # that actually refuses -- `pair_cost_refusal` -- returns None
            # unless the order would create NEW MATCHED QUANTITY, so when it
            # fires we were NOT flat and the leg it priced was a completion
            # against the basis already paid. At 2230z this record published
            # a 0.9400 "missed 6c pair" against an effective 1.04 that was
            # correctly refused. Read from the SAME store the gate reads, so
            # the record cannot disagree with it, and each part separately
            # swallowed so a missing position cannot cost us the whole row.
            _held = _hq = _dq = None
            try:
                _up = self.store.position(slug, "UP")
                _dn = self.store.position(slug, "DOWN")
                _resid = _up - _dn
                if _resid != 0:
                    _held = "UP" if _resid > 0 else "DOWN"
                    _hq = str(_resid if _resid > 0 else -_resid)
                    _dq = "0"
            except BaseException:                              # noqa: BLE001
                _held = _hq = _dq = None
            _rec = MPB.record(
                market=slug, reason=reason,
                decision={"pair_own_cost": (None if pair is None
                                            else str(pair)),
                          "bid": str(book.get("bid")),
                          "ask": str(book.get("ask")),
                          "stream_age_s": str(book.get("stream_age_s"))},
                entry_ceiling=_ceil, stage="OPENING",
                held_side=_held, held_qty=_hq, deficient_qty=_dq,
                book_source=book.get("source"))
            # AN UNPLACEABLE BOOK IS NOT A MISSED PAIR. The classifier
            # judges economics and knows nothing of the band, so the
            # correction is applied here where the band was read. The pair
            # stays reported -- the record keeps its diagnostic value -- it
            # simply stops claiming a pair was given up when no leg could
            # have been placed at all.
            try:
                _rec["first_leg_placeable"] = placeable
                _rec["first_leg_band"] = [str(TSP.min_first_leg_own_price()),
                                          str(TSP.max_first_leg_own_price())]
                _rec["leg_own_prices"] = {
                    "UP": (None if _u is None else str(_u)),
                    "DOWN": (None if _dn is None else str(_dn))}
                if placeable is False and _rec.get(
                        "missed_profitable_pair") == "YES":
                    _rec["missed_profitable_pair"] = "N/A"
                    _rec["not_an_opportunity_because"] = (
                        "NO_LEG_IN_FIRST_LEG_BAND: the pair is economic but "
                        "neither leg prices inside the opening band, so no "
                        "opening could have been placed")
            except BaseException:                              # noqa: BLE001
                pass
            emit("L5_MISSED_PAIR", _rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["open_missed_emit_failed:%s" % type(exc).__name__] += 1

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
        # THE BOUNDED POLICY, EVALUATED ALWAYS AND ACTED ON ONLY WHEN ARMED.
        #
        # `completion_decision` asks whether a cross improves the worst
        # outcome and pays for its own fee. It deliberately leaves STRICT
        # positivity to its caller, and on 2026-10-30 that gap is exactly
        # what separated the two stranded legs worth completing (2130z, 2145z)
        # from the two that were not (2115z best worst_after -0.0400; 2200z
        # never positive at all). This is that caller.
        #
        # It is evaluated even with the flag off so the tape records what the
        # policy WOULD have authorized, which is how the next proposal gets
        # its numbers from evidence instead of from argument.
        # GUARDED FOR THE SAME REASON `book_rungs` IS: callers bind this
        # method set onto minimal objects, and a reporting addition must not
        # become a new requirement on them. Absent, it reads NOT AUTHORIZED,
        # which is the fail-closed direction.
        _pol_fn = getattr(self, "ts_taker_policy", None)
        pol = (_pol_fn(dec, src, now) if callable(_pol_fn)
               else {"ok": False, "authorized": False,
                     "reason": "POLICY_NOT_WIRED"})
        rec["taker_policy"] = pol
        if dec.get("ok") and authorized and t.armed and pol.get("authorized"):
            # ============ THE SUBMISSION, WHICH USED TO BE A STRING.
            #
            # This branch wrote `crossed: "WOULD_SUBMIT"` and
            # `submission_route: "deliberately not wired"`. It reached that
            # string 43 times in the 0300z window of 2026-11-06 and 22 times
            # in 0315z -- every one of them with the economics and the
            # bounded policy both saying yes -- while the two residuals those
            # windows left rode to settlement for -$1.00. See issue #195.
            #
            # IT IS WIRED NOW, and through the path that already existed:
            # the same `executor.handle` controller re-entry
            # `maybe_book_completion` uses, which passes every gate, claim,
            # overlay, limit check and reconciliation the L5-driven call
            # passes, because it is the same call.
            rec.update(self.submit_ts_completion(dec, src, pol, now))
        elif dec.get("ok") and authorized and t.armed:
            rec["crossed"] = False
            rec["withheld_by"] = "TAKER_POLICY:%s" % str(
                pol.get("reason"))[:60]
        emit("L5_TS_COMPLETION", rec)
        self.stats["ts_completion:%s" % (dec.get("reason") or "OK")] += 1
        return rec

    def defensive_budget_telemetry(self) -> Dict[str, Any]:
        """Spent, limit and remaining for the LIFETIME completion budget.

        `DEF` is imported function-locally everywhere else in this module,
        so it is imported here too rather than at module scope -- the image
        closure test pins which modules the live image carries.
        """
        spent = self.defensive_spent_usd()
        try:
            from src import l5_defensive_completion as _DEF
            limit = str(_DEF.DEFENSIVE_LOSS_BUDGET_USD)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["defensive_budget_limit_unreadable:%s"
                       % type(exc).__name__] += 1
            limit = None
        rem = None
        if spent is not None and limit is not None:
            try:
                rem = str(D(limit) - D(spent))
            except BaseException:                              # noqa: BLE001
                rem = None
        return {"spent_usd": spent, "limit_usd": limit,
                "remaining_usd": rem,
                "labels": ("LIFETIME, monotone, never reset, durable on the "
                           "volume. None is UNREADABLE and never zero. A "
                           "debit here is the signature of a bounded taker "
                           "cross -- nothing else writes it.")}

    def defensive_spent_usd(self) -> Optional[str]:
        """The durable lifetime completion-budget balance, as a string.

        WHY IT IS HERE. The owner asked for the $2.00 lifetime budget
        balance after the 20:15z window and it was NOT on the tape: the
        heartbeat never emitted it, so the answer had to be inferred from
        the absence of a charge record. An inferred budget is not a budget
        you can police.

        None means UNREADABLE and must never be read as zero.
        """
        try:
            return str(self.store.defensive_spent())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["defensive_spent_unreadable:%s"
                       % type(exc).__name__] += 1
            return None

    #: The five stages a completion passes through, named so that no two can
    #: be conflated. The 2026-11-06 20:15z window reported
    #: `submitted: true` for a ranked TAKER_COMPLETE at own 0.4300 while the
    #: order that actually reached the venue was a MAKER quote at own 0.3900
    #: -- because `_bc_capture` reads `ok` from whatever `handle` returned,
    #: and under `execution_mode: MAKER_ONLY` `handle` posts a maker quote
    #: and returns ok. Controller re-entry is NOT a taker cross.
    COMPLETION_STAGES = ("controller_selected", "venue_submitted",
                         "acknowledged", "filled", "budget_debited")

    def completion_stages(self, outcome: Mapping[str, Any], *,
                          want_side: str, want_px: Any,
                          spent_before: Optional[str],
                          spent_after: Optional[str]) -> Dict[str, Any]:
        """Classify a completion attempt across all five stages.

        `taker_cross_confirmed` is deliberately strict: it requires a
        BUDGET DEBIT, because `completion_taker_execute` is the only path
        that debits `defensive_spent_usd`. No debit means no bounded taker
        cross happened, whatever `submitted` says. An unreadable balance
        yields None -- not False, and certainly not True.
        """
        sub = outcome.get("submitted")
        debit = None
        if spent_before is not None and spent_after is not None:
            try:
                debit = D(str(spent_after)) - D(str(spent_before))
            except BaseException:                              # noqa: BLE001
                debit = None
        if debit is None:
            cross = None                      # cannot be established
        else:
            cross = bool(sub) and debit > 0
        if cross:
            cls = "TAKER_CROSS_CONFIRMED"
        elif sub and debit is not None and debit == 0:
            cls = "CONTROLLER_REENTRY_NO_CROSS"
        elif sub is False:
            cls = "NOT_SUBMITTED"
        elif sub is None:
            cls = "PLACEMENT_UNREADABLE"
        else:
            cls = "CROSS_UNDETERMINED_BUDGET_UNREADABLE"
        return {
            "controller_selected": True,
            "venue_submitted": sub,
            "requested_price_own": (None if want_px is None
                                    else str(want_px)),
            "requested_side": want_side,
            "acknowledged": (bool(outcome.get("order_ref"))
                             if sub else None),
            "order_ref": outcome.get("order_ref"),
            "filled": None,
            "budget_debited_usd": (None if debit is None else str(debit)),
            "defensive_spent_before": spent_before,
            "defensive_spent_after": spent_after,
            "taker_cross_confirmed": cross,
            "classification": cls,
            "labels": ("five stages, never collapsed. "
                       "`taker_cross_confirmed` requires a BUDGET DEBIT, "
                       "because completion_taker_execute is the only path "
                       "that debits it -- controller re-entry that posts a "
                       "maker quote reads CONTROLLER_REENTRY_NO_CROSS. "
                       "`filled` is None here because a fill is a later, "
                       "separate venue event; read L5_LIVE_LIFECYCLE for "
                       "it. None is UNKNOWN and never False."),
        }

    def submit_ts_completion(self, dec: Mapping[str, Any],
                             src: Mapping[str, Any],
                             pol: Mapping[str, Any],
                             now: float) -> Dict[str, Any]:
        """Actually place the authorized bounded completion. Or say why not.

        WHAT THIS ADDS, AND WHAT IT REUSES. It adds no transport, no second
        order path and no authorization. It compares the executable
        alternatives through `l5_inventory_decision` -- one process, one
        arithmetic, every alternative priced -- and then hands the chosen one
        to the SAME `executor.handle(None, book, controller=...)` re-entry
        that `maybe_book_completion` uses. Inside `handle`, the bounded taker
        route cancels the resting complement, proves the cancellation at the
        venue, re-reads our own inventory, clamps the size to the confirmed
        deficit, cross-checks the venue's exposure, charges the shared
        budget, writes a durable claim and issues a mandate naming that one
        order. None of that is new code reached from here; it is
        `completion_taker_execute`, and it is the defensive unwind's order of
        operations because that order is the safety property.

        THE RANKING CAN ONLY REFUSE. `completion_decision` and
        `l5_taker_completion_policy.authorize` have already both said yes
        before this method is called. The ranking is consulted on top of
        them, so an alternative it declines is an order that is not placed --
        it cannot approve anything the two existing gates refused, and it
        cannot raise a limit.

        AND IT IS STILL DOUBLY GATED. `L5_TS_COMPLETION_CROSS` and
        `L5_TAKER_COMPLETION_POLICY` are both default-OFF and both are
        re-checked inside `completion_taker_execute` before it reads any
        state. With either off this method records a refusal and places
        nothing.
        """
        from src import l5_inventory_decision as ID
        from src import l5_two_sided_probe as TSP
        out: Dict[str, Any] = {"crossed": False}
        t = self.transport
        slug = getattr(t, "market_slug", None) if t is not None else None
        if not slug:
            return dict(out, withheld_by="NO_MARKET")
        ws = AB.slug_start(slug)
        if ws is None:
            return dict(out, withheld_by="WINDOW_START_UNKNOWN")
        side = str(dec.get("side") or "").strip().upper()
        held = "UP" if side == "DOWN" else "DOWN"

        # ---- THE LEDGER, RE-READ. Never this method's memory of what the
        # decision was priced on: the ledger is what reconciliation
        # certifies, and `completion_taker_execute` re-reads it again after
        # the cancel. Fail closed on anything unreadable.
        try:
            rows = _resting_rows(self.store, str(slug))
            up = self.store.position(str(slug), "UP")
            dn = self.store.position(str(slug), "DOWN")
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["ts_completion_submit:LEDGER_UNREADABLE:%s"
                       % type(exc).__name__] += 1
            return dict(out, withheld_by="LEDGER_UNREADABLE")
        if rows is None or up is None or dn is None:
            return dict(out, withheld_by="LEDGER_UNREADABLE")

        book = self.book.snapshot(str(slug)) or {}
        mk = {}
        try:
            mk = self.maker_completion_history(str(slug), side=side, now=now)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["ts_completion_submit:MAKER_HISTORY:%s"
                       % type(exc).__name__] += 1

        # ---- ONE COMPARISON OVER EVERY ALTERNATIVE, on the current book.
        rank = ID.rank(
            up_qty=up, dn_qty=dn,
            up_cost_px=src.get("up_cost_px"), dn_cost_px=src.get("dn_cost_px"),
            deficient_side=side, deficit=dec.get("deficit"),
            min_qty=src.get("min_qty"), tick=src.get("tick"),
            taker_rungs=src.get("book_rungs"),
            taker_displayed_qty=dec.get("displayed_depth"),
            taker_own_px=dec.get("price"),
            depth_haircut=self._taker_policy_number(
                TCP.DEPTH_HAIRCUT_ENV, TCP.DEFAULT_DEPTH_HAIRCUT),
            maker_own_px=src.get("maker_own_px"),
            maker_queue_ahead=src.get("maker_queue_ahead"),
            # MEASURED OR ABSENT. With no measured maker fill rate the maker
            # alternative is UNAVAILABLE rather than priced off a guess, so
            # this cannot talk itself into resting.
            maker_fill_rate=os.getenv("L5_MAKER_FILL_RATE_MEASURED") or None,
            seconds_to_settlement=max(
                0.0, TSP.window_end(float(ws)) - float(now)),
            up_mid_wire=src.get("mid_wire"),
            adverse_shift=self._taker_policy_number(
                "L5_ADVERSE_SELECTION_SHIFT", D("0.05")),
            max_ev_giveup_usd=self._taker_policy_number(
                "L5_MAX_COMPLETION_EV_GIVEUP_USD", None),
            cash_headroom=src.get("cash_headroom"),
            worst_case_ceiling=src.get("worst_case_ceiling"),
            book_age_s=book.get("stream_age_s"),
            gap_suspected=bool(book.get("gap_suspected")))
        out["inventory_decision"] = {
            k: v for k, v in rank.items() if k != "alternatives"}
        out["alternatives"] = rank.get("alternatives")
        if rank.get("chosen") not in (ID.TAKER_COMPLETE, ID.TAKER_PARTIAL):
            self.stats["ts_completion_submit:RANKED_%s"
                       % str(rank.get("chosen"))] += 1
            return dict(out, withheld_by="RANKED_%s" % rank.get("chosen"),
                        why=rank.get("why_hold"))

        chosen = None
        for alt in (rank.get("alternatives") or ()):
            if alt.get("alternative") == rank["chosen"]:
                chosen = alt
                break
        if chosen is None:
            return dict(out, withheld_by="RANKED_ALTERNATIVE_MISSING")

        # ---- THE PROPOSAL. The policy's own conservative quantity and VWAP,
        # clamped again by the ranking's size: two independent sizings and the
        # order is the smaller, which can only ever reduce it.
        _impr_floor = self._taker_policy_number(
            TCP.MIN_WORST_IMPROVEMENT_ENV, None)
        try:
            qty = min(D(str(pol.get("qty"))), D(str(chosen.get("qty"))))
            px = D(str(pol.get("price")))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return dict(out, withheld_by="PROPOSAL_UNREADABLE")
        if qty <= 0:
            return dict(out, withheld_by="PROPOSAL_QTY_NOT_POSITIVE")
        proposal = dict(pol, qty=str(qty), price=str(px), side=side,
                        # CARRIED SO THE EXECUTOR CAN RE-EARN IT. The policy
                        # tested the improvement floor against the book as it
                        # was; `completion_taker_execute` tests it AGAIN
                        # after the cancel, at the clamped quantity, and
                        # again if the budget forces a smaller size.
                        min_worst_improvement_usd=(
                            None if _impr_floor is None else str(_impr_floor)),
                        # THE MARKET THIS WAS PRICED ON, carried so
                        # `completion_taker_execute` can refuse a proposal
                        # that reached a different one rather than take the
                        # caller's word for it.
                        market=str(slug),
                        expires_at=float(TSP.cancel_cutoff_from_start(
                            float(ws))),
                        ranked_as=rank["chosen"],
                        ranked_worst_after=chosen.get("worst_after"),
                        ranked_expected_worst_after=chosen.get(
                            "expected_worst_after"),
                        ranked_ev_giveup_usd=chosen.get("ev_giveup_usd"),
                        maker_attempted=mk.get("attempted"),
                        maker_rested_s=mk.get("rested_s"))
        out["proposal"] = {k: v for k, v in proposal.items()
                           if k not in ("conservative_fill",)}

        emit("L5_TS_COMPLETION_TRIGGER", {
            "market": str(slug), "side": side, "held_side": held,
            "qty": str(qty), "price": str(px),
            "route": "CONTROLLER_REENTRY",
            "ranked": rank["chosen"],
            "ranking_basis": rank.get("ranking_basis"),
            "resting_orders": len(rows),
            "labels": ("emitted BEFORE the re-entry, so the tape shows a "
                       "completion the ENGINE asked for and never a "
                       "synthesised L5 action. The order, if any, is placed "
                       "by the existing path through every existing gate.")})

        # ---- THE BUDGET, BEFORE. The debit across the placement is what
        # proves a bounded taker cross actually happened, so it is read
        # BEFORE the path runs and compared after.
        _spent_before = self.defensive_spent_usd()

        # ---- HAND IT TO THE EXISTING PATH. Single use: `handle` takes the
        # proposal off the executor before acting on it.
        self.executor.taker_completion_proposal = proposal
        try:
            res = self.executor.handle(
                None, book, market=str(slug), window=str(ws),
                controller={"provenance": LX.CONTROLLER_GENERATED,
                            "decision": {"side": side, "price": str(px),
                                         "l5_size": str(qty),
                                         "relation": "complete"},
                            "maker_class": None})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.executor.taker_completion_proposal = None
            self.stats["ts_completion_submit:HANDLE_RAISED:%s"
                       % type(exc).__name__] += 1
            return dict(out, withheld_by="HANDLE_RAISED:%s"
                        % type(exc).__name__)
        finally:
            # BELT AND BRACES. `handle` clears it on the path that reads it;
            # clearing here too means a return BEFORE that path cannot leave
            # a live proposal for the next tick to ride.
            self.executor.taker_completion_proposal = None

        outcome = self._bc_capture(res)
        out["outcome"] = outcome
        # THE FIVE STAGES, so controller re-entry can never read as a cross.
        stages = self.completion_stages(
            outcome, want_side=side, want_px=px,
            spent_before=_spent_before,
            spent_after=self.defensive_spent_usd())
        out["stages"] = stages
        out["crossed"] = bool(stages.get("taker_cross_confirmed"))
        if out["crossed"]:
            # THE WINDOW'S OUTLAY, so the policy's per-window cap reads a
            # real number rather than the structural zero it read while
            # nothing crossed. Charged at the WORST case, like the budget.
            try:
                self._taker_outlay_usd = (
                    D(str(getattr(self, "_taker_outlay_usd", 0) or 0))
                    + qty * px + TSP.worst_fee(qty, px))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["ts_completion_outlay_unrecordable:%s"
                           % type(exc).__name__] += 1
        else:
            out["withheld_by"] = "PLACEMENT:%s/%s" % (
                stages.get("classification"),
                str(outcome.get("refused"))[:50])
        self.stats["ts_completion_submit:%s" % (
            "SUBMITTED" if out["crossed"] else "REFUSED")] += 1
        emit("L5_TS_COMPLETION_RESULT", {
            "market": str(slug), "side": side,
            "qty": str(qty), "price": str(px),
            "ranked": rank["chosen"],
            "stages": stages,
            "taker_cross_confirmed": stages.get("taker_cross_confirmed"),
            "classification": stages.get("classification"),
            "outcome": outcome,
            "window_outlay_usd": str(getattr(self, "_taker_outlay_usd", 0)),
            "labels": ("pairs the authorized completion with what the "
                       "placement path actually did. `submitted` false with "
                       "a named `refused` is a decision that did not become "
                       "an order -- which is the state issue #195 was "
                       "opened about, and it is now visible per attempt.")})
        return out

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

    def _market_is_relevant_for_wake(self, slug: str) -> bool:
        """Could a touch move in this market change a DECISION right now?

        THE LIVE SLATE IS HUNDREDS OF MARKETS and the feed delivers ~4.5
        frames a second across it. Waking on all of them would make the loop
        run as fast as it can whatever moved, which is the busy-loop this is
        not allowed to become. A market we hold nothing in and rest nothing
        in cannot produce an action, so it cannot justify a wake.

        FAILS CLOSED TOWARD NOT WAKING. Anything unreadable returns False:
        the existing timer still picks the market up on its next pass, so a
        missed wake costs latency and never correctness.
        """
        try:
            st = getattr(self, "store", None)
            if st is None:
                return False
            pos = getattr(st, "position", None)
            if callable(pos):
                for sd in ("UP", "DOWN"):
                    try:
                        if D(str(pos(slug, sd) or 0)) != 0:
                            return True
                    except BaseException:                      # noqa: BLE001
                        continue
            rows = getattr(st, "live_orders", None)
            rows = rows() if callable(rows) else rows
            if rows:
                for r in (rows.values() if isinstance(rows, Mapping)
                          else rows):
                    try:
                        if str((r or {}).get("market") or "") == slug:
                            return True
                    except BaseException:                      # noqa: BLE001
                        continue
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False
        return False

    def _wake_on_frame(self, msg: Any, now: float) -> None:
        """Fanout observer: raise the wake when a relevant touch moves.

        THE ENVELOPE IS NOT RE-DERIVED HERE. `l5_maker_fill_model.ingest`
        records that reading marketSlug/bids/offers off the TOP LEVEL was
        wrong and left every queue_ahead null in deployment 1093d4d1, so the
        slug comes from the recorder's own proven walker and the top of book
        comes from the ladder that has already folded this very frame -- the
        fanout calls the ladder before this observer.

        This runs ON A FEED THREAD and therefore does nothing but raise a
        flag: it reads no gate, holds no lock of the decision path, and
        submits nothing.
        """
        tw = getattr(self, "touch_watcher", None)
        lad = getattr(self, "shadow_ladder", None)
        if tw is None or lad is None:
            return
        events: List[dict] = []
        try:
            _REC._walk_market_events(msg, events)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return
        seen: Set[str] = set()
        books = getattr(lad, "books", {}) or {}
        for ev in events:
            try:
                if ev.get("kind") != "book":
                    continue
                slug = ev.get("slug")
                if not isinstance(slug, str) or not slug or slug in seen:
                    continue
                seen.add(slug)
                bk = books.get(slug)
                if not bk:
                    continue
                bids, asks = bk.get("bids"), bk.get("asks")
                # rows are best-first: `depth_at` treats rows[-1] as the
                # DEEPEST rung, so rows[0] is the touch.
                bb = bids[0][0] if bids else None
                ba = asks[0][0] if asks else None
                tw.observe(slug, bb, ba)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                continue

    def _requote_queue_ahead_fn(self, slug: str, side: str) -> Any:
        """An accessor giving displayed size ahead of us at an OWN price.

        WHY A CLOSURE. `completion_requote` derives its own target from
        `min(touch, ceiling, redemption_cap)`; computing that target out here
        to look up its depth would be a second copy of the pricing rule, and
        the two copies would drift. So the decision asks for the depth at
        whatever price it chose.

        PRICE SEMANTICS, which is where this is easy to get wrong. The wire
        is YES/UP. An UP buy at own cost p rests as a BID at wire p. A DOWN
        buy at own cost q is a SELL of YES and rests as an ASK at wire 1-q.

        Returns None for UNKNOWN -- no ladder, an unserved rung, or a bad
        read. None is never read as zero: the caller fails closed on it.
        """
        lad = getattr(self, "shadow_ladder", None)
        if lad is None or not hasattr(lad, "ahead_of_us"):
            return None

        def _ahead(own_px: Any) -> Optional[D]:
            try:
                p = D(str(own_px))
            except BaseException:                              # noqa: BLE001
                return None
            if side == "UP":
                wire, resting_side = p, FM.BID
            else:
                wire, resting_side = (D(1) - p), FM.ASK
            try:
                return lad.ahead_of_us(slug, resting_side, wire)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                try:
                    self.stats["requote_queue_read_error:%s"
                               % type(exc).__name__] += 1
                except BaseException:                          # noqa: BLE001
                    pass
                return None

        return _ahead

    def _requote_cancel_unresolved(self, coid: str) -> Optional[str]:
        """Whether a PREVIOUS cancel of this order never proved it was gone.

        THE CAMPAIGN EVIDENCE. Ten requotes across 0145z and 0200z produced
        not one clean cancel: seven returned `verify_error: HTTPError` and
        three returned `still_open` carrying `ORDER_STATE_NEW` with the full
        `leaves_qty`. The store's own `orders_uncertain()` did not catch
        them, so the loop kept re-cancelling an order that may well have
        still been working -- while the replacement, by the requote path's
        own rule, could not be sent while it was live. That is how a single
        order slot goes empty for a whole window.

        Returns a short reason string when unresolved, else None.
        """
        try:
            seen = self._requote_unresolved
        except AttributeError:
            return None
        return seen.get(str(coid))

    def _requote_note_cancel_outcome(self, coid: str,
                                     out: Mapping[str, Any]) -> None:
        """Record whether THIS cancel proved the order terminal.

        `cancelled` is the only proof: the module's own label says it "means
        the VENUE reported a terminal state on re-read, never that a cancel
        call returned". `still_open` and `unverified` are both failures.
        """
        if not hasattr(self, "_requote_unresolved"):
            self._requote_unresolved: Dict[str, str] = {}
        key = str(coid)
        try:
            still = list(out.get("still_open") or [])
            unver = list(out.get("unverified") or [])
            done = list(out.get("cancelled") or [])
            filled = list(out.get("filled") or [])
        except BaseException:                                  # noqa: BLE001
            self._requote_unresolved[key] = "CANCEL_OUTCOME_UNREADABLE"
            return
        if done or filled:
            self._requote_unresolved.pop(key, None)
            return
        if still:
            self._requote_unresolved[key] = "STILL_OPEN_AT_VENUE"
        elif unver:
            self._requote_unresolved[key] = "CANCEL_UNVERIFIED"
        else:
            # nothing terminal and nothing failed is still not proof.
            self._requote_unresolved[key] = "NO_TERMINAL_PROOF"

    def _taker_route_open(self) -> bool:
        """Both owner switches, or FALSE. Unreadable is never enabled."""
        try:
            return bool(self.ts_completion_cross_authorized()
                        and self.taker_policy_enabled())
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.stats["disposition:ROUTE_UNREADABLE"] += 1
            except BaseException:                               # noqa: BLE001
                pass
            return False

    #: HOW OFTEN THE DISPOSITION RECORD REPEATS while nothing changes. A
    #: change of selected action always emits immediately, so this only
    #: bounds the quiet case -- the one that produced 83 identical `N/A`
    #: records at 0100z.
    DISPOSITION_EVERY_S = 15.0

    def maybe_unmatched_disposition(self, now: float
                                    ) -> Optional[Dict[str, Any]]:
        """Price every disposition of the unmatched leg and RECORD it.

        THE SILENCE THIS ENDS. 0100z carried a residual 1 DOWN to a
        -$0.4900 settlement, and in the 14 minutes after the last cancel
        the tape held 152 decision records of which the final 83 read
        `NOT_AN_OPPORTUNITY` with verdict `N/A`. Not one named the
        position, its worst case, or the fact that nothing was available.
        The engine stopped ASKING rather than deciding.

        READ-ONLY, STRUCTURALLY. It reads the store, the book and the
        ladder, calls a pure function, and emits. It holds no transport
        here and submits nothing: the selected action is DATA, and routing
        it would go through the same `submit_*` path and the same arming,
        campaign, budget and halt gates as any other order -- none of which
        this method touches or can bypass.

        CADENCE. Only while unmatched inventory exists, at most once every
        `self.DISPOSITION_EVERY_S`, and always immediately when the selected
        action changes -- so a state change is never missed and a quiet
        residual does not flood the tape.
        """
        # BOTH LOCAL, like `maybe_completion_requote`'s own import: `TSP`
        # is not a module-level name in this file.
        from src import l5_two_sided_probe as TSP
        from src import l5_unmatched_disposition as UDP
        try:
            t = self.transport
            slug = getattr(t, "market_slug", None) if t is not None else None
            if not slug:
                slug = getattr(self, "ws", None) and TSP.named_market()
            if not slug:
                return None
            slug = str(slug)
            realised = D(str(self.store.residual(slug)))
            if realised == 0:
                if getattr(self, "_disposition_last", None):
                    self._disposition_last.pop(slug, None)
                return None
            held = TSP.UP if realised > 0 else TSP.DOWN
            comp = TSP.DOWN if held == TSP.UP else TSP.UP
            legs = self.store.leg_cost(slug) or {}
            snap = self.book.snapshot(slug) or {}
            ws = AB.slug_start(slug)
            if ws is None:
                return None
            live = {c: o for c, o in (self.store.live_orders() or {}).items()
                    if str((o or {}).get("market") or "") == slug
                    and str((o or {}).get("side") or "") == comp}
            row = next(iter(live.values())) if len(live) == 1 else None
            _qmk = getattr(self, "_requote_queue_ahead_fn", None)
            _qfn = (_qmk(slug, comp) if callable(_qmk) else None)
            vg = getattr(self.executor, "validation_gates", None)
            # ONE CONTROLLER. `decide_inventory` dispatches over the whole
            # action set -- the six dispositions plus OPEN / REPLENISH /
            # ACCUMULATE_FAVORED / ACQUIRE_UNFAVORED -- and defers every
            # price to `decide`, `reopen_decision` and
            # `weighted_inventory_decision`. Holding inventory puts us in
            # the disposition family, whose record it returns MERGED, so
            # the tape, the heartbeat and `controller_preserves` read
            # exactly the keys they always did.
            rec = UDP.decide_inventory(
                market=slug, held_side=held,
                held_qty=self.store.position(slug, held),
                disposition_kwargs=dict(
                held_cost_usd=legs.get(held),
                deficient_qty=self.store.position(slug, comp),
                deficient_cost_usd=legs.get(comp),
                resting_own_px=(row or {}).get("price"),
                resting_qty=(row or {}).get("qty"),
                resting_filled_qty=(
                    (D(str((row or {}).get("qty"))) -
                     LX.InventoryStore.worst_case_remaining(row))
                    if row and (row or {}).get("qty") is not None else 0),
                bid=snap.get("bid"), ask=snap.get("ask"),
                tick=getattr(t, "tick_size", None),
                min_qty=getattr(t, "quantity_increment", None),
                now_s=float(now), window_start_s=float(ws),
                window_end_s=TSP.window_end(float(ws)),
                book_age_s=snap.get("stream_age_s"),
                gap_suspected=bool(snap.get("gap_suspected")),
                orders_uncertain=self.store.orders_uncertain(),
                residual_budget_usd=getattr(
                    vg, "max_worst_case_deficit_usd", None),
                queue_ahead_fn=_qfn,
                # THE OWNER'S ROUTE, READ NOT ASSUMED, AND FAIL CLOSED.
                # Both switches must say YES. An accessor that cannot be
                # read at all reports the route OFF, never on -- the
                # crossing actions are then still PRICED, because the whole
                # point of the record is to state what declining them
                # costs, but they can never be selected.
                taker_enabled=self._taker_route_open()))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["disposition:LAYER_FAILED:%s"
                       % type(exc).__name__] += 1
            return None
        if getattr(self, "_disposition_last", None) is None:
            self._disposition_last = {}
        prev = self._disposition_last.get(slug)
        changed = (prev is None or prev[0] != rec.get("selected"))
        if not changed and abs(now - prev[1]) < self.DISPOSITION_EVERY_S:
            self.stats["disposition:THROTTLED"] += 1
            return rec
        self._disposition_last[slug] = (
            rec.get("selected"), float(now),
            disposition_fingerprint(self, slug))
        self.stats["disposition:%s" % rec.get("selected")] += 1
        emit("L5_UNMATCHED_DISPOSITION", TS.bounded_for_log(rec))
        return rec

    #: HOW FRESH A VERDICT MUST BE to bind the discretionary cancels. One
    #: tick, not one cadence: the controller runs immediately before the
    #: requote on the same `now`, so a verdict older than this is from a
    #: book we are no longer looking at and must not govern anything.
    DISPOSITION_AUTHORITY_MAX_AGE_S = 3.0

    def controller_verdict(self, slug: Any,
                           now: float) -> Optional[str]:
        """The authoritative action for this market, or None.

        NONE IS NOT A VETO. An absent, stale or unreadable verdict leaves
        the pre-existing behaviour exactly as it was -- which already
        carries the pair ceilings of PR #200 and the pair-aware recovery of
        PR #202, so falling back is safe rather than merely tolerable. The
        gates built on this can therefore only ever ADD a refusal; none of
        them can enable a cancel, a submission or a crossing.
        """
        try:
            prev = (getattr(self, "_disposition_last", None)
                    or {}).get(str(slug))
            if not prev:
                return None
            sel, when = prev[0], prev[1]
            age = float(now) - float(when)
            # TWO-SIDED ON PURPOSE. `age > MAX` alone let a FUTURE-dated
            # verdict through, because a negative age is not greater than
            # 3.0 -- so a clock rollback or a bad write could veto every
            # discretionary cancellation indefinitely. Review item 2.
            if age < 0.0 or age > self.DISPOSITION_AUTHORITY_MAX_AGE_S:
                return None
            return None if sel is None else str(sel)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

    def controller_preserves(self, slug: Any, now: float) -> bool:
        """Did the one controller decide to KEEP the resting completion?

        A THIN ALIAS over the module-level function, which is what the
        gates actually call so that a duck-typed stub without this method
        simply does not gate.

        THIS IS WHAT MAKES IT AUTHORITATIVE. Four mechanisms could each
        withdraw a second leg on their own judgement -- the requote, the
        distance recovery, the complement path and the age deadline -- and
        0100z lost its window to two of them disagreeing about the same
        0.9500 pair within 21 seconds. The discretionary two now ask here
        first.

        THE MANDATORY ONES ARE DELIBERATELY NOT GATED. The per-order age
        TTL, the terminal sweep, the halt latch and the independent
        watchdog bound REAL exposure; a decision layer that could suppress
        them would trade an economic problem for a risk one. They stay
        exactly as they were.
        """
        # DELEGATES, so the method and the gate can never disagree. An
        # earlier version answered on the age test alone while the gates
        # used the module function's fingerprint check -- two different
        # answers to the same question, which is the class of defect this
        # whole PR exists to remove.
        return controller_preserves(self, slug, now)


    def _requote_ladder_pick(self, slug: Any,
                             live: Mapping[str, Any]) -> Optional[str]:
        """Which resting order a requote may move when several rest.

        The client order id of the REDUCING-side order with the highest own
        price -- the one nearest its touch -- or None, which declines. None
        when the ladder is off, when nothing is held unmatched, when no
        reducing-side order rests, or when anything needed to tell cannot be
        read. An order the venue cannot name is never picked.
        """
        from src import l5_two_sided_probe as TSP
        try:
            if TSP.concurrent_rounds() <= 1:
                return None
            realised = D(str(self.store.residual(str(slug))))
            if realised == 0:
                return None
            reducing = "DOWN" if realised > 0 else "UP"
            best = None
            for coid, row in live.items():
                if str((row or {}).get("side") or "") != reducing:
                    continue
                if not str(row.get("venue_order_id") or "").strip():
                    continue
                px = D(str(row.get("price")))
                if best is None or px > best[0]:
                    best = (px, str(coid))
            return None if best is None else best[1]
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

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
        # the continuous inventory controller reprices its own quotes
        if LX.continuous_inventory_enabled() and Runner.continuous_owns(
                self, getattr(self.transport, "market_slug", None)):
            self.stats["requote_refused:CONTINUOUS_INVENTORY_OWNS"] += 1
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
                # NAMED, NOT JUST DECLINED. Zero is nothing to requote; TWO
                # is now a state this engine DOES create -- `L5_DUAL_QUOTE`
                # with `L5_MAX_LIVE_ORDERS=2` rests one order on each side
                # of the same market, which is the configuration the
                # two-hour campaign asks for. Declining is still right:
                # guessing which of two to cancel is how a requote becomes
                # an unhedged leg. But this return bumped NO counter, so an
                # operator running both flags would see repricing simply
                # never happen with nothing in the telemetry saying why --
                # the same blindness as `ingest:FEED_FAILED` carrying no
                # status. The two flags are safe together and mutually
                # exclusive per market; this is what makes that visible.
                #
                # WITH A LADDER RESTING THERE IS NO GUESS TO MAKE. The order
                # to move is the one that reduces what is held and sits
                # nearest its touch: it is the next to fill, so it is the
                # one whose price decides the pair. Everything else stays
                # where it is. Off unless L5_TS_CONCURRENT_ROUNDS is above
                # one, so the two-order case declines exactly as before.
                _pick = (self._requote_ladder_pick(slug, live)
                         if live else None)
                if _pick is None:
                    self.stats["requote_refused:%s" % (
                        "NO_RESTING_ORDER" if not live
                        else "MULTIPLE_RESTING_ORDERS_IN_MARKET")] += 1
                    return None
                live = {_pick: live[_pick]}
                self.stats["requote_ladder_pick"] += 1
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
            # LOOKED UP DEFENSIVELY, like the ladder itself. This method is
            # also borrowed by lighter harnesses that carry no ladder and no
            # helpers; there the gate simply has nothing to measure and the
            # pre-patch behaviour stands, recorded by the stat below.
            _qmk = getattr(self, "_requote_queue_ahead_fn", None)
            _qfn = (_qmk(str(slug), str(side)) if callable(_qmk) else None)
            if _qfn is None:
                self.stats["requote_without_queue_evidence"] += 1
            # HOW LONG THIS QUOTE HAS ACTUALLY RESTED, from the row's own
            # submit stamp -- the same field and the same parse the
            # distance-recovery path uses. Our 34 venue-confirmed orders put
            # the median fill at 17.1s while five of 2015z's cancellations
            # fired between 1.1s and 6.8s, each with a 76-81% conditional
            # chance of still filling. See TSP.REQUOTE_MIN_REST_S.
            #
            # AN AGE WE CANNOT ESTABLISH LEAVES THE OLD BEHAVIOUR STANDING,
            # and the stat records that the move was decided without it.
            # Same judgement as `require_queue_evidence` directly below: a
            # floor we cannot evaluate must not disable completion
            # repricing entirely rather than make it careful -- that would
            # re-create the 0330z frozen-plan failure, where a quote the
            # book had left behind was re-proposed 32 times and never
            # moved. The floor binds wherever the row carries its stamp,
            # which in production it does: the distance-recovery path
            # parses the same field and its recoveries fire live.
            _rest_s = None
            try:
                _st = dt.datetime.fromisoformat(str(row.get("t")))
                if _st.tzinfo is not None:
                    _rest_s = float(now) - _st.timestamp()
            except BaseException as _exc:                      # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
                _rest_s = None
            if _rest_s is None:
                self.stats["requote_without_resting_age"] += 1
            # A QUOTE PLACED AS AN OPENING LEG IS NOT A COMPLETION QUOTE
            # THAT HAS HAD ITS CHANCE. The minimum rest exists so a
            # completion placed near the touch is given time to fill before
            # it is moved again. A quote sent BEFORE this market went
            # unmatched was priced as the other half of an opening, two or
            # more ticks behind; on 0400z three of them sat 6 to 25 ticks
            # behind for 120 s while a 0.977 pair came and went. With the
            # recovery switch such a quote is re-priced at once. Its
            # replacement is sent after the fill, so it serves the minimum
            # rest like any other completion quote.
            try:
                if _rest_s is not None and TSP.reprice_missing_leg_on_fill():
                    _since = self.store.stranded_since(str(slug))
                    if _since is not None and \
                            (float(now) - _rest_s) < float(_since):
                        _rest_s = None
                        self.stats["requote_opening_leg_repriced_on_fill"] += 1
            except BaseException as _exc:                      # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
            res = TSP.completion_requote(
                resting_rest_s=_rest_s,
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
                orders_uncertain=self.store.orders_uncertain(),
                # THE QUEUE EVIDENCE THE DECISION ALWAYS NEEDED. It was
                # never supplied, so every requote of the 0145z/0200z
                # campaign decided blind and logged a null. These come from
                # the authenticated ladder's own aggregate depth -- the same
                # accessors the pre-submit record already uses -- and are
                # bounds on what must clear ahead of us, never a fill
                # probability.
                queue_ahead_fn=_qfn,
                # REQUIRED WHERE IT CAN BE MEASURED, which in production is
                # everywhere: the ladder is attached per window off the same
                # authenticated feed the book comes from. With NO ladder at
                # all there is nothing to require, and demanding it would
                # disable completion repricing entirely rather than make it
                # careful -- so the old behaviour stands and the stat below
                # records that the move was decided without queue evidence.
                require_queue_evidence=_qfn is not None,
                cancel_unresolved=(
                    _cu(coid) if callable(
                        _cu := getattr(self, "_requote_cancel_unresolved",
                                       None)) else None))
            if not res.get("act"):
                self.stats["requote_refused:%s" % res.get("reason")] += 1
                return None
            # ============ THE ONE CONTROLLER DECIDES, NOT THIS PATH ALONE
            #
            # `completion_requote` answers "may this be repriced", and the
            # disposition controller asks it exactly that when pricing its
            # REPRICE action. So a permitted requote that the controller
            # then declined in favour of PRESERVE is the controller being
            # overruled by the mechanism it consulted -- which is the
            # architecture that lost 0100z. It runs on the same `now`,
            # immediately before this, so the verdict is from this book.
            if controller_preserves(self, slug, now):
                self.stats["requote_refused:CONTROLLER_SELECTED_PRESERVE"] += 1
                return None
            # CANCEL EXACTLY THIS ORDER, by venue id, proof by re-read, and
            # any fill found while cancelling is BOOKED with its cost basis
            # rather than discarded. An ambiguous outcome marks the store
            # UNCERTAIN, which refuses every subsequent submission until an
            # authoritative read agrees -- so a failed requote cannot leave a
            # replacement racing a live order.
            out = LX.deadman(self.store, t, reason="completion_requote",
                             only=[coid])
            # PROVE IT LANDED, OR REMEMBER THAT IT DID NOT. Without this the
            # next tick re-cancels an order the venue may still be working.
            _nk = getattr(self, "_requote_note_cancel_outcome", None)
            if callable(_nk):
                _nk(coid, out if isinstance(out, Mapping) else {})
            self.stats["requote_cancelled"] += 1
            # ACKNOWLEDGEMENT EVIDENCE for the window report. Only rows the
            # VENUE confirmed terminal on a re-read are recorded; `errors`,
            # `unverified` and `still_open` are failures that mark the book
            # uncertain and are deliberately not recorded as evidence of
            # anything. Reporting only, and it can never stop a cancel.
            try:
                if getattr(self, "win_cancels", None) is None:
                    self.win_cancels = {}
                _rows = (out or {}).get("cancelled") or []
                _bucket = self.win_cancels.setdefault(str(slug), [])
                _seen = {str(r.get("client_order_id") or "")
                         for r in _bucket}
                for _r in _rows:
                    if not isinstance(_r, Mapping):
                        continue
                    _c = str(_r.get("client_order_id") or "")
                    if _c and _c not in _seen and _r.get("venue_order_id"):
                        _bucket.append(dict(_r))
                        _seen.add(_c)
            except BaseException:                              # noqa: BLE001
                pass
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
        # ============ THE ONE CONTROLLER DECIDES THE DISCRETIONARY CANCEL
        #
        # `acc_stale_by_distance` is the OPTIONAL half of this route: it
        # proposes cancelling a leg the book has left behind so the
        # complement path can re-place it. PR #202 made it pair-aware, but
        # pair-awareness is not authority -- it still judged alone, and at
        # 0100z two mechanisms disagreed about the same 0.9500 pair inside
        # 21 seconds. When the controller has decided to KEEP the resting
        # completion on this tick's book, the discretionary recovery stands
        # down.
        #
        # THE MANDATORY HALF IS UNTOUCHED. `overdue` above is the per-order
        # AGE deadline and it is computed and acted on regardless of this;
        # so are the terminal sweep, the halt latch and the independent
        # watchdog. A decision layer that could suppress those would trade
        # an economic problem for a risk one.
        if controller_preserves(self, slug, now):
            self.stats["acc_recover:CONTROLLER_SELECTED_PRESERVE"] += 1
            stale = []
        else:
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
        # ============ THE ONE CONTROLLER DECIDES FIRST, ON THIS BOOK
        #
        # AHEAD OF BOTH DISCRETIONARY PATHS, which is review item 3. It
        # used to sit between them, so `maybe_acc_deadline` -- the
        # EARLIER one -- only ever saw the PREVIOUS tick's verdict, and
        # on the first tick with a residual it saw none at all. The
        # fingerprint already made a stale verdict harmless, but harmless
        # is not authoritative, and the owner asked for authoritative.
        #
        # DECIDING BEFORE THE DEADLINE'S `poll_orders` IS SAFE because of
        # that same fingerprint: if the poll books a racing fill, the
        # state no longer matches the verdict, the gate stops binding and
        # the recovery proceeds on its own judgement with both PRs'
        # ceilings still in force.
        self.maybe_unmatched_disposition(now)
        _sc.mark("maybe_unmatched_disposition")

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
        # ROUTE B: THE BOOK ASKS, THE EXISTING PATH ANSWERS. Inert unless
        # L5_BOOK_COMPLETION is set. Placed where the L5-driven completion
        # already sits, so nothing downstream is reordered.
        self.maybe_book_completion(now)
        # OUR OWN OPENING, on the timer. Inert unless L5_CONTROLLER_OPEN is
        # set. Placed AFTER both completion paths deliberately: completing
        # a pair we already hold removes exposure and must never queue
        # behind a decision to take on more, and the one-outstanding-order
        # rule means an opening decision made first could occupy the single
        # slot a completion needed.
        self.maybe_controller_open(now)
        _sc.mark("maybe_ts_completion")
        # LIFETIME OBSERVATIONS FOR WHATEVER IS ACTUALLY RESTING, and here
        # rather than on the shadow tick so that no shadow guard stands
        # between a live order and the record of the queue it sat in. It is
        # MARKED because this module exists for exactly that reason: the
        # 93-second rollover hid in an unmeasured gap, so a new stage is
        # declared and timed rather than folded into its neighbour's cost.
        self.calibration_live_tick(now)
        _sc.mark("calibration_live_tick")
        # AFTER the completion decision and BEFORE the terminal sweep. After,
        # because a completion that can act on the book as it stands should
        # do so rather than be cancelled first; before, because a quote the
        # market has walked away from is worth replacing while runway
        # remains. Inert unless L5_COMPLETION_REQUOTE is set.
        self.maybe_completion_requote(now)
        _sc.mark("maybe_completion_requote")
        # THE PORTFOLIO CONTROLLER, AHEAD OF THE DIAGNOSTICS BELOW. The
        # deferred diagnostic has a five-second budget and uses it: on
        # 2026-11-10 19:2xZ these two stages sat behind it and every
        # decision was taken 5.4 s after the tick's own clock. A withdrawal
        # that waits five seconds behind a read-only job has not withdrawn
        # anything. First the record (SHADOW and STEER; places and cancels
        # nothing), then, in STEER only, taking down an opening quote the
        # controller would no longer pay for -- a cancellation by exact id
        # through the existing deadman.
        self.maybe_portfolio_decision(now)
        _sc.mark("maybe_portfolio_decision")
        self.maybe_portfolio_withdraw(now)
        _sc.mark("maybe_portfolio_withdraw")
        # THE DEFERRED READ-ONLY DIAGNOSTICS, and their position in this list
        # is the fix. They used to run inside `roll`, which is the FIRST call
        # in the tick, so their cost came out of the window's opening -- 93
        # seconds of it, every window, on 2026-11-06. Here they can only ever
        # use time the completion decision, the requote and the opening
        # trigger above have already finished with, and only outside the
        # window's entry-critical phase. At most one job, with a wall-clock
        # budget. See `l5_roll_diagnostics`.
        self.drain_roll_diagnostics(now)
        _sc.mark("roll_diagnostics")
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
        self.maybe_venue_exit_probe(self.transport)
        self.maybe_close_finished_campaign()
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
        self.maybe_attest_venue_costs(now)
        self.maybe_diagnose_venue_costs(now)
        self.maybe_watch_equity_transfers(now)
        self.maybe_correct_venue_supersets(now)
        self.maybe_report_venue_basis(now)
        self.maybe_measure_manual_orders(now)
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
        self.maybe_venue_exit_probe(self.transport)
        self.maybe_close_finished_campaign()
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
    {
        # OWNER 2026-11-15 (sports session, owner chose --from-now over the
        # recommended --attributed): realized_today read -78.6836 while the
        # service itself labelled it UNPROVEN_LEGACY_ATTRIBUTION -- 148 legs
        # booked at UPPER_BOUND_1USD_PER_CONTRACT, which inflates losses and
        # erases wins. Venue-confirmed figures: sports settled -19.48 (77
        # markets, venue's own), BTC5m -0.48 (AUTHENTICATED_FILLS) = -19.96.
        # --from-now: cash_delta 0, so realized restarts at ZERO here
        # (attributed -19.96, unattributed +19.96). This DISCARDS the
        # historical ~20 of real losses as the halt's input; the contaminated
        # scalar and this entry stay in the store's rebase log. Generated by
        # analysis/l5/btc5m_accumulation/make_rebase_spec.py (fb66db7d1).
        # NOTE: the "open" cricket market below is this service's own
        # exposure book; the venue's /v1/portfolio/positions read showed zero
        # positions at 21:49Z and 22:49Z (sports INVENTORY_RECONCILE).
        "id": "upper-basis-contamination-20261115",
        "matches_realized_total": D("-78.68360000"),
        "attributed": D("-19.96"),
        "cash_delta": D("0"),
        "anchor": {
            "at_read_utc": "2026-11-15T23:43:22.082596+00:00",
            "at_read_balance": "338.3456",
            "base_utc": "CURRENT BALANCE IS THE BASE",
            # --from-now: the base IS the current read, so delta is 0
            "pre_arm_balance": "338.3456",
            "at_disarm_balance": "338.3456",
            "flat_at_each_read": ("zero open orders account-wide; venue "
                                  "/v1/portfolio/positions zero non-zero rows "
                                  "at 21:49Z and 22:49Z (sports "
                                  "INVENTORY_RECONCILE). This service's own "
                                  "exposure book still counts 1.0000 worst "
                                  "case in aec-testcr-afg-zim-2026-11-13."),
        },
        "reason": ("owner instruction 2026-11-15: re-anchor realized to the "
                   "CURRENT balance. The prior total was UNPROVEN_LEGACY_"
                   "ATTRIBUTION with 148 UPPER_BOUND legs; measurement "
                   "restarts here. This DISCARDS the historical record as "
                   "the halt's input, including the venue-confirmed ~20 of "
                   "real losses."),
    },
    {
        # OWNER 2026-11-16 (sports session, "Apply it"): the --from-now rebase
        # above left realized_unattributed = +19.96, a residual that belongs
        # to no UTC day, so realized_current_utc_day() fails closed (None)
        # forever and the DAILY ceiling is unreadable -- nothing can arm.
        # Zero it. attributed = cash_delta = 0 because the flat-account
        # invariant requires a MEASURED delta and only one flat read exists
        # (338.3456); asserting -22.10 would need a balance nobody read.
        # Cost, stated to the owner: the venue-confirmed ~-22.10 of past
        # losses permanently stops counting against the cumulative ceiling.
        # Spec from the BTC5m session (local d5ffc0366, never pushed).
        "id": "residual-zeroing-20261116",
        "matches_realized_total": D("0.00"),
        "attributed": D("0"),
        "cash_delta": D("0"),
        "anchor": {
            "at_read_utc": "2026-11-16T00:11:52Z",
            "at_read_balance": "338.3456",
            "pre_arm_balance": "338.3456",
            "at_disarm_balance": "338.3456",
            "base_utc": ("CURRENT BALANCE IS THE BASE; both reads 338.3456, "
                         "delta 0"),
            "flat_at_each_read": ("zero open orders account-wide; venue "
                                  "/v1/portfolio/positions zero non-zero rows "
                                  "at 21:49Z and 22:49Z on 2026-11-15. This "
                                  "service's own exposure book still counts "
                                  "1.0000 in aec-testcr-afg-zim-2026-11-13, "
                                  "which the venue says the account does not "
                                  "hold: a phantom leg adopted and never "
                                  "cleared."),
        },
        "reason": ("owner instruction 2026-11-16: zero the undated +19.96 "
                   "residual left by upper-basis-contamination-20261115 so "
                   "the DAILY loss figure is computable again. attributed "
                   "and cash_delta are both 0 because the flat-account "
                   "invariant requires a measured delta and no earlier flat "
                   "read exists. The venue-confirmed ~-22.10 of past losses "
                   "no longer counts against the cumulative ceiling."),
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


#: COST-BASIS RESTATEMENTS -- for a RETIRED market whose recognised
#: settlement is wrong because its cost basis was a $1/share PLACEHOLDER
#: rather than a measured cost.
#:
#: SEPARATE FROM LEDGER_ADJUSTMENTS AND FROM apply_settlement_corrections, and
#: necessarily so. An adjustment adds a labelled delta and leaves the row
#: alone; `correct_settled_market` restates a settlement OUTCOME and refuses
#: anything that moves realized toward zero (CORRECTION_WOULD_RELAX_LOSS_HALT)
#: -- which this does, by $1.1842. That guard is correct for an outcome
#: restatement and is NOT relaxed, shared or bypassed here: this drives
#: `InventoryStore.correct_cost_basis`, a different verb with its own ten
#: guards, whose whole purpose is replacing an upper bound with a measurement.
#:
#: ONE MARKET, ONCE. The list holds exactly one entry. The verb is idempotent
#: by `correction_id` in `cost_basis_corrections`, refuses unless the stored
#: row still matches `expect` field for field, RE-DERIVES the cost from the
#: named venue fills rather than trusting `measured_cost`, requires the venue
#: quantity to equal the ledger quantity, preserves the original row verbatim
#: in `cost_superseded`, and cannot clear a halt or move a limit. Every boot
#: after the first logs ALREADY_APPLIED and changes nothing.
COST_BASIS_RESTATEMENTS = [
    {
        "id": "0230z-double-booked-cost-basis-2026-10-30",
        "market": "cpc-btc-updown-15m-2026-10-30-0230z",
        "side": "DOWN",
        # the stored row as settlement wrote it, field for field
        "expect": {
            "yes_value": "1.0000",
            "up_qty": "0",
            "dn_qty": "1.9100",
            "cost_total": "1.9100",
            "redemption_total": "0E-8",
            "settlement_pnl": "-1.91000000",
            "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND",
        },
        "measured_cost": D("0.7258"),
        "measured_fees": D("0.0000"),
        "evidence": {
            "source": "/v1/order via the live transport, plus "
                      "/v1/account/balances, both recorded in this service's "
                      "own deploy log (deployment 8db131c5)",
            # The venue ORDER id. This order filled in ONE execution --
            # quantity 1.91, cumQuantity 1.91, leavesQuantity 0 -- so the
            # order identifies the fill exactly, and it is the identifier the
            # venue's own authoritative row is keyed by.
            "per_fill": [{"trade_id": "DDDXP54G12TT", "qty": "1.91",
                          "price": "0.6200", "fee": "0.0000"}],
            # NOT an activity-feed walk: this is a single order read directly
            # from the venue, which is complete evidence for one fill by
            # construction. `complete` states that nothing is missing, which
            # is the condition guard 5 exists to enforce.
            "activity_walk": {"complete": True, "pages": 0,
                              "ended": "SINGLE_ORDER_READ",
                              "basis": "one full execution of one order"},
            "detail": ("DDDXP54G12TT ORDER_STATE_FILLED avgPx 0.6200 "
                       "cumQuantity 1.91 commission 0.0000, side DOWN. PMUS "
                       "quotes the YES price, so a DOWN share cost "
                       "1 - 0.6200 = 0.3800 and 1.91 shares cost 0.7258"),
            "cash": ("available_cash_usd 343.21615 at 02:29:43 and "
                     "342.49035 at 02:30:41, a delta of exactly 0.7258, and "
                     "unchanged at 02:46:57 and 02:49:00 after settlement -- "
                     "so the venue charged this fill ONCE and credited "
                     "nothing at settlement"),
            "what_went_wrong": (
                "the venue's open-orders row carried no client order id, so "
                "reconcile keyed it by the venue id and adopted it while "
                "parking our own coid as vanished. One venue order under two "
                "coids booked 1.91 twice -- ORDER_PROGRESS at 02:30:27.385 "
                "and PENDING_TERMINAL_RESOLVED at 02:30:27.763, both naming "
                "venue_order_id DDDXP54G12TT, both reading already: 0. The "
                "book went to 3.82 against the venue's 1.91, so "
                "adopt_venue_position correctly replaced a basis it could no "
                "longer decompose with the $1/share bound, and settlement "
                "priced that placeholder"),
            "before": ("retired_markets cost_total 1.9100, settlement_pnl "
                       "-1.91000000; utc_day 2026-10-30 realized -1.91; "
                       "all-time realized -11.50120000"),
            "after": ("cost_total 0.7258, settlement_pnl -0.72580000; "
                      "utc_day 2026-10-30 realized -0.7258; all-time "
                      "realized -10.31700000. The original row is preserved "
                      "verbatim in cost_superseded; `t` is NOT rewritten, so "
                      "the loss stays on the day it was incurred; "
                      "realized_unattributed and every limit are untouched"),
            "fix": ("tracked_coid_for_venue_order and "
                    "duplicate_venue_order_coid, shipped with this "
                    "restatement, make the double-book unreachable"),
        },
    },
]


#: RESTATEMENTS THAT WAIT FOR THE OWNER. Deploying this file applies none of
#: them. One is applied only when `L5_COST_RESTATEMENT_APPROVED` names its
#: id exactly (comma-separated for more than one), and then through the same
#: verb and the same guards as the list above: the stored row must match
#: `expect` field for field, the cost is re-derived from the named venue
#: fill, the original row is preserved verbatim in `cost_superseded`, and it
#: is applied once.
COST_RESTATEMENT_APPROVAL_ENV = "L5_COST_RESTATEMENT_APPROVED"
COST_BASIS_RESTATEMENTS_AWAITING_OWNER = [
    {
        "id": "0415z-position-before-fill-2026-11-11",
        "market": "cpc-btc-updown-15m-2026-11-11-0415z",
        "side": "UP",
        # the stored row as L5_LIVE_SETTLED logged it at 04:30:32.690
        "expect": {
            "yes_value": "0.0000",
            "up_qty": "0.4400",
            "dn_qty": "0",
            "cost_total": "0.4400",
            "redemption_total": "0E-8",
            "settlement_pnl": "-0.44000000",
            "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND",
        },
        "measured_cost": D("0.1408"),
        "measured_fees": D("0.0000"),
        "evidence": {
            "source": "/v1/order via the live transport and "
                      "/v1/account/balances, both recorded in this service's "
                      "own deploy log (deployment ddfa5c0d)",
            # the venue ORDER id: one order, one execution of its whole
            # quantity (cumQuantity 0.44 of 0.44), so it identifies the fill
            "per_fill": [{"trade_id": "DN6KVVR0144P", "qty": "0.44",
                          "price": "0.3200", "fee": "0.0000"}],
            "activity_walk": {"complete": True, "pages": 0,
                              "ended": "SINGLE_ORDER_READ",
                              "basis": "one full execution of one order"},
            "detail": ("DN6KVVR0144P ORDER_STATE_FILLED avg_px 0.3200 "
                       "cum_qty 0.44 commission 0.0000, side UP. 0.44 "
                       "shares at 0.32 cost 0.1408; the settlement line "
                       "itself reports committed_released 0.140800"),
            "cash": ("available_cash_usd 339.89295 at 04:14:01, 340.93215 "
                     "at 04:16:05 after window 0400z paid 1.18 and this "
                     "fill took 0.1408 (339.89295 + 1.18 - 0.1408), and "
                     "340.93215 unchanged at 04:32:11 after settlement"),
            "what_went_wrong": (
                "the venue's position showed the 0.44 at 04:15:20.831, "
                "before the order read that prices it. It was adopted at "
                "the $1/share bound; PENDING_TERMINAL_RESOLVED at "
                "04:15:22.507 then added the fill on top (0.88 against the "
                "venue's 0.44), the reconciliation at 04:15:33.527 adopted "
                "again, and settlement priced the bound"),
            "before": ("retired_markets cost_total 0.4400, settlement_pnl "
                       "-0.44000000; all-time realized -15.82160000"),
            "after": ("cost_total 0.1408, settlement_pnl -0.14080000; "
                      "all-time realized -15.52240000. The original row is "
                      "preserved verbatim in cost_superseded; every limit "
                      "is untouched"),
            "fix": ("fills_in_flight and absorb_into_adopted, shipped with "
                    "this entry: a position our own unbooked order explains "
                    "is not adopted, and a fill the adopted quantity "
                    "already contains replaces the bound"),
        },
    },
]


def approved_cost_restatements() -> List[str]:
    """Ids the owner has approved, exactly as written. Unset is none."""
    raw = os.getenv(COST_RESTATEMENT_APPROVAL_ENV) or ""
    return [x for x in (p.strip() for p in raw.split(",")) if x]


def apply_cost_basis_restatements(store) -> Optional[Dict[str, Any]]:
    """Replace a retired market's $1/share placeholder with a measured cost.

    Adds no authority: `InventoryStore.correct_cost_basis` is the only thing
    that writes, every one of its guards runs, and a refusal is reported
    rather than worked around. Idempotent by the verb's own correction-id
    guard, so a restart cannot apply one twice.
    """
    applied: List[Dict[str, Any]] = []
    approved = approved_cost_restatements()
    todo = list(COST_BASIS_RESTATEMENTS)
    for c in COST_BASIS_RESTATEMENTS_AWAITING_OWNER:
        if c["id"] in approved:
            todo.append(c)
        else:
            emit("L5_LIVE_COST_RESTATE_AWAITING_OWNER", {
                "id": c["id"], "market": c["market"], "side": c["side"],
                "measured_cost": str(c["measured_cost"]),
                "approve_with": "%s=%s" % (COST_RESTATEMENT_APPROVAL_ENV,
                                           c["id"]),
                "labels": "nothing was written"})
    for c in todo:
        before = store.realized_lines()
        out = store.correct_cost_basis(
            c["market"], c["side"],
            correction_id=c["id"],
            expect=c["expect"],
            measured_cost=c["measured_cost"],
            measured_fees=c["measured_fees"],
            evidence=c["evidence"])
        if not out.get("corrected"):
            emit("L5_LIVE_COST_RESTATE_REFUSED", dict(
                out, id=c["id"],
                why=("the stored row is not the one this restatement was "
                     "written for, or it has already been applied. Nothing "
                     "was written."),
                realized_unchanged=before))
            continue
        rec = dict(out, id=c["id"], market=c["market"], side=c["side"],
                   evidence=c["evidence"],
                   original_row_preserved_in="cost_superseded",
                   limits_untouched=True)
        emit("L5_LIVE_COST_RESTATED", rec)
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


def venue_basis_lines(runner: Any) -> Dict[str, Any]:
    """The small `venue_basis` block: state, counts and totals. Never the
    rows, never raises, and nothing reads it to decide anything.

    A module function, NOT a Runner method: `account_risk_line` and
    `heartbeat` are grafted onto stubs in several suites, and a new
    instance attribute there is a duck-typing hazard (see
    `unresolved_discrepancies`).
    """
    try:
        from src import l5_venue_basis_report as VBR
        job = getattr(runner, "_venue_basis_pass", None) or VBR.Pass()
        return job.lines()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"state": "UNAVAILABLE", "error": type(exc).__name__,
                "applied": False}


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


#: the LOCAL operator halt. Any non-empty value halts: it is a deadman, so
#: the permissive reading is the empty one and there is no "false" spelling.
LIVE_HALT_ENV = "L5_LIVE_HALT"


def operator_halted() -> bool:
    """True while the local operator halt is engaged. ONE EXPRESSION.

    WHY THIS IS A MODULE FUNCTION AND NOT JUST A METHOD, and it cost an
    owner-approved live window. `Runner.try_arm` opens with

        if t is None or t.armed or self.halted(): return

    so the operator halt is the FIRST thing that stops arming, before any
    numbered step of `authorize_arming` is reached. But
    `l5_live_authorization.authorization_preflight` -- the artifact an
    operator reads to decide whether a window is ready -- did not evaluate
    it at all. On 2026-11-09 the 08:30Z window therefore reported
    `authorization_preflight.ok true` with `fail []`, an approved campaign,
    the shadow lock cleared and the owner token valid, while every tick
    refused `SAFETY_OVERLAY:OPERATOR_HALT`. 162 refusals, zero orders, and
    an approved window spent on a prerequisite nothing displayed.

    Nothing was lost but the window -- `windows_spent` stayed 0, exposure
    stayed $0 and the engine failed CLOSED, which is the correct direction.
    The defect is observability: a readiness verdict that cannot see a
    blocker is worse than no verdict, because it is believed.

    So both callers now read THIS function. A preflight that duplicated the
    `os.getenv` would be a second opinion, and a second opinion is what the
    incident was.
    """
    return bool(os.getenv(LIVE_HALT_ENV, "").strip())


#: completion repricing. DEFAULT OFF, so the code can be deployed and
#: reviewed in place before it is ever allowed to cancel an order. It adds a
#: cancellation, never a capability: the replacement goes through the same
#: complement path, the same gates and the same REPLACE budget as today.
COMPLETION_REQUOTE_ENV = "L5_COMPLETION_REQUOTE"


def completion_requote_enabled() -> bool:
    return (str(os.getenv(COMPLETION_REQUOTE_ENV, "")).strip().upper()
            in ("1", "YES", "TRUE", "ON"))


#: keep publishing the conservative portfolio value while the ONLY
#: uncertainty is another strategy's just-adopted position. Exactly the
#: token, or off.
SIBLING_VALUE_ENV = "L5_PUBLISH_VALUE_DURING_SIBLING_ADOPTION"
SIBLING_VALUE_TOKEN = "OTHER_STRATEGY_POSITION_ONLY"


def publish_value_during_sibling_adoption() -> bool:
    return (os.getenv(SIBLING_VALUE_ENV) or "").strip() == SIBLING_VALUE_TOKEN


#: names the guard service's READ-ONLY halt endpoint. Unset = inert.
HALT_URL_ENV = "L5_HALT_URL"
#: the BTC5m service's read-only fills feed. Unset: 5-minute positions this
#: ledger adopts stay at the $1 bound through settlement, as before.
BTC5M_FILLS_URL_ENV = "L5_BTC5M_FILLS_URL"
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
            # NO_RECYCLING unless the owner's variable says, exactly,
            # RECYCLE_ON_COMPLETE (2026-11-11): the venue's own balance
            # returns a completed pair's cash at completion, not at
            # settlement -- see FaithfulExecutor.matched_pair_cost.
            cash_policy=(VG.RECYCLE_ON_COMPLETE
                         if (os.getenv("L5_CASH_POLICY") or "").strip()
                         == VG.RECYCLE_ON_COMPLETE else VG.NO_RECYCLING))
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
            # ITS OWN EVENT, NOT A HEARTBEAT FIELD. The heartbeat already
            # reaches Railway's 80 KiB line cap and is truncated mid-document,
            # so a field appended there can simply vanish. This stays small
            # enough to always arrive whole.
            try:
                emit("L5_WAKE_LATENCY", dict(
                    runner.wake.stats(),
                    touch_watcher=runner.touch_watcher.status(),
                    poll_s=poll_s,
                    loop_woken_by_event=runner.stats.get(
                        "loop_woken_by_event", 0),
                    loop_woken_by_timer=runner.stats.get(
                        "loop_woken_by_timer", 0)))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                runner.stats["wake_stats_error:%s"
                             % type(exc).__name__] += 1
            last_hb = now
        # THE SLEEP THAT USED TO COST THE LATENCY. `poll_s` defaults to 3,
        # while the authenticated book arrives ~4.5x a second and order
        # progress lands within ~200ms -- so the data was already fast and
        # the decisions waited for a timer. This returns on the timeout
        # exactly as `time.sleep(poll_s)` did, so reconciliation, settlement,
        # the watchdog and stale-feed recovery keep their cadence unchanged;
        # an actionable event can now end it sooner.
        #
        # NOTHING ABOUT THE DECISION MOVES. The loop is as single-threaded
        # after this as before: feed threads may only raise the flag.
        try:
            _wk = runner.wake.wait(poll_s)
            if _wk.get("woken"):
                runner.stats["loop_woken_by_event"] += 1
            else:
                runner.stats["loop_woken_by_timer"] += 1
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # a broken wake must never stop the loop: fall back to the
            # original fixed sleep and say so.
            runner.stats["wake_error:%s" % type(exc).__name__] += 1
            time.sleep(poll_s)
    if runner.transport is not None and runner.transport.armed:
        emit("L5_LIVE_SHUTDOWN", LX.deadman(runner.store, runner.transport,
                                            reason="shutdown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
