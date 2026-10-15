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
from typing import Any, Dict, List, Mapping, Optional

from src import l5_faithful_translation as FT
from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX
from src import l5_maker_fill_model as FM
from src import l5_two_sided_shadow as TS
from src import l5_pmus_auth_book as AB
from src import l5_pmus_realtime_observer as OBS
from src import l5_pmus_transport as T
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


PROBE_LIMITS = dict(max_usd_per_market=D("5"), max_abs_residual=D("10"),
                    max_total_exposure_usd=D("5"), max_concurrent_markets=1,
                    max_live_orders=max_live_orders(),
                    daily_realized_loss_halt_usd=D("5"))

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


def emit(kind: str, doc: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(doc, default=str,
                                      separators=(",", ":"))), flush=True)


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
        self.seen: set = set()
        self.seeded = False
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
        self.stats = collections.Counter()
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
        # a claim is for ONE window; it does not carry into the next one.
        self.arm_claim = None
        self.ws = ws
        slug = AB.slug_for(ws)
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
            self.book.frame_observer = self.model.ingest_frame
        else:
            self.model = None
            self.book.frame_observer = None
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
            "validation_gates": (self.executor.validation_gates.as_dict()
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
        try:
            rep = t.preflight(
                expect_account=os.getenv("L5_LIVE_EXPECT_ACCOUNT", "").strip()
                or None)
        except T.PreflightFailed as exc:
            rep = json.loads(str(exc))
        except Exception as exc:                               # noqa: BLE001
            rep = {"preflight": "FAILED", "market_slug": t.market_slug,
                   "fatal": T.redact("%s: %s" % (type(exc).__name__, exc))}
        self.preflight_status = rep.get("preflight", "FAILED")
        blockers = rep.get("blocking_failures")
        # log every PASS, and a FAIL only when what blocks it changes
        if self.preflight_status == "PASSED" or blockers != self.last_blockers:
            emit("L5_LIVE_PREFLIGHT", rep)
        self.last_blockers = blockers
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
        for market in self.store.markets_with_exposure():
            if market == t.market_slug:
                continue                       # the one we are working now
            # ONE READ PER MARKET PER SETTLEMENT_POLL_S. This loop used to
            # run on every 3s tick against every market still in the book,
            # and with 2115z stuck there by the retirement defect that was a
            # settlement GET every 3 seconds forever.
            if (self.now_t - self.last_settlement_poll.get(market, 0.0)
                    < SETTLEMENT_POLL_S):
                continue
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
                emit("L5_LIVE_SETTLED", dict(
                    out, venue_state=rep["state"],
                    cash_deployed=self.store.cash_deployed_usd(market)))
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
            claim = AUTH.authorize_arming(
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
        emit("L5_LIVE_WINDOW", out)

    # ------------------------------------------------------------ actions
    def poll(self, now: float) -> None:
        rows = self.feed()
        if rows is None:
            self.stats["feed_errors"] += 1
            return
        fresh = []
        for r in rows:
            k = OBS.action_key(r)
            if k in self.seen:
                continue
            self.seen.add(k)
            fresh.append(r)
        if not self.seeded:
            # the first read is history, not a signal: act only on what
            # arrives after this process began watching
            self.seeded = True
            self.stats["seeded"] = len(fresh)
            return
        for r in sorted(fresh, key=lambda x: float(x.get("timestamp") or 0)):
            if str(r.get("slug") or "").startswith("btc-updown-15m-"):
                self.handle(r, now)

    def handle(self, r: Mapping[str, Any], recv: float) -> None:
        ws = OBS.l5_window_start(r.get("slug", ""))
        t, ex = self.transport, self.executor
        slug = OBS.pmus_slug_for(ws) if ws else None
        book = self.book.snapshot(slug) if slug else None
        row = engine_row(r, book, recv)
        act = row["l5"]
        if ws != self.ws or t is None or ex is None:
            rec = {"action_key": row["action_key"], "market": slug,
                   "refused_by_layer": "PMUS_TRANSLATION",
                   "refused_reason": "NOT_CURRENT_WINDOW",
                   "L5_NATIVE": FT.l5_native(row)}
        elif act.get("is_sell"):
            # L5's BTC15m corpus holds ZERO sells; the probe does not
            # translate one. Our choice, so it is filed against SAFETY.
            rec = {"action_key": row["action_key"], "market": slug,
                   "refused_by_layer": "SAFETY_OVERLAY",
                   "refused_reason": "SELL_NOT_IN_PROBE_SCOPE",
                   "L5_NATIVE": FT.l5_native(row)}
        else:
            if self.halted():
                rec = {"action_key": row["action_key"], "market": slug,
                       "refused_by_layer": "SAFETY_OVERLAY",
                       "refused_reason": "OPERATOR_HALT",
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
        doc = {"t": now_utc().isoformat(), "row": row, "record": rec,
               "armed": bool(t and t.armed), "preflight": self.preflight_status}
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
            "order": {k: order.get(k) for k in
                      ("client_order_id", "submitted", "reason", "venue_order_id",
                       "state", "error")} if order else None,
            "intent": order.get("intent"),
            "armed": bool(t and t.armed)})
        # recv, not a fresh clock read: the placement time is when we
        # OBSERVED the action, which is what the queue model must date.
        self.model_place(order.get("intent"), row, recv, rec=rec,
                         refused_reason=rec.get("refused_reason"))

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
            emit("L5_LIVE_LIFECYCLE", dict(
                e, market=(m if m else None),
                market_identity=("PRESENT" if m else "MISSING")))

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

    def heartbeat(self) -> None:
        t = self.transport
        emit("L5_LIVE_HB", {
            "utc": now_utc().isoformat(), "role": SERVICE_ROLE,
            "market": t.market_slug if t else None,
            "armed": bool(t and t.armed), "preflight": self.preflight_status,
            "owner_arm_token": Q1.armed(),
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
            "auth_book": self.book.status(),
            "book_now": self.book.snapshot(t.market_slug) if t else None,
            "stats": dict(self.stats),
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

    def tick(self, now: float) -> None:
        self.now_t = now
        self.roll(now)
        if self.halted() and self.transport is not None and self.transport.armed:
            emit("L5_LIVE_HALT", LX.deadman(self.store, self.transport,
                                            reason="operator_halt"))
            self.transport.disarm("operator_halt")
        # the INDEPENDENT latch, which the env var above is not: it is
        # written by a process we do not control and survives our restart.
        self.maybe_halt_deadman(now)
        # and the claim's own venue-bounded expiry, same cancellation route
        self.maybe_expire_authorization(now)
        # and, when NOT armed, withdraw anything this ledger still owns: a
        # crash leaves exactly that state and no other path covers it.
        self.maybe_cancel_orphans(now)
        # MODELLED fills advance BEFORE the cool-off check, for the same
        # reason the cool-off branch still calls poll(): this is local
        # bookkeeping over the websocket feed and costs the venue nothing.
        # Below the check it would stall during a cool-off, and the
        # recorder's trade list is a rolling 500 -- a long enough stall
        # drops prints the model never sees.
        self.model_advance(now)
        self.two_sided_advance(now)
        self.two_sided_settle(now)
        # one question per tick instead of one per caller
        cooling = self.venue_cooling()
        if cooling > 0:
            self.stats["ticks_skipped_in_cooloff"] += 1
            self.win["ticks_skipped_in_cooloff"] += 1
            self.poll(now)          # the websocket feed costs the venue nothing
            return
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


#: names the guard service's READ-ONLY halt endpoint. Unset = inert.
HALT_URL_ENV = "L5_HALT_URL"
#: "0" disables the validation gates; anything else (including unset) is ON
GATES_ENV = "L5_VALIDATION_GATES"


def validation_gates():
    """The run-scoped additive gates, or None when explicitly disabled."""
    if str(os.getenv(GATES_ENV, "1")).strip() == "0":
        return None
    try:
        from src import l5_live_validation_gates as VG
        return VG.ValidationGates(
            max_cash_deployed_usd=D(str(os.getenv(
                "L5_MAX_CASH_DEPLOYED_USD", "10"))),
            max_unmatched_contracts=D(str(os.getenv(
                "L5_MAX_UNMATCHED_CONTRACTS", "5"))),
            max_cumulative_loss_usd=D(str(os.getenv(
                "L5_MAX_CUMULATIVE_LOSS_USD", "20"))),
            cash_policy=VG.NO_RECYCLING)
    except Exception as exc:                                   # noqa: BLE001
        # a gate we cannot build is announced, never silently skipped
        emit("L5_LIVE_ALARM", {
            "kind": "validation_gates_unavailable",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
            "labels": ("the additive gates are NOT active. Pre-existing "
                       "limits are unaffected.")})
        return None


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
                           "close_margin_s": AUTH.close_margin_s(),
                           "lambda": str(lam), "limits": runner.limits.as_dict(),
                           "worst_case_usd": str(runner.limits.worst_case_loss_usd()),
                           "frozen_rule": "030d8bf"})
    last_hb = 0.0
    while not stop["now"]:
        now = time.time()
        try:
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
