"""ONE protected path to the venue, for L5-followed and self-generated
intents alike.

WHY THIS EXISTS. The deployed executor's entry point is row-shaped: it
takes L5's activity row and derives side, price and size from it
(`FT.l5_native(row)`, `dec["l5_size"] * self.lam`, `FT.pmus_translate`).
The runner then wraps it in three of its own gates. So there was no way to
ask "would this DECISION be allowed through" unless the decision came from
L5, and the independent controller's decisions had nowhere to go but a log.

THIS DOES NOT REWRITE THE EXECUTOR, and that is deliberate. L5's path still
reaches `l5_live_execution.handle` through the SAME call with the SAME
arguments, so its behaviour, its gate ordering, its risk limits and its
order registry are not merely preserved -- they are untouched. What is new
is a named boundary in front of it, shared by both provenances, and a
read-only evaluation of the safety state that either kind of intent can be
put through without submitting anything.

PROVENANCE IS STRUCTURAL. An intent carries its origin and the two origins
are never interchangeable. A controller-generated intent is NEVER given an
L5 row: fabricating one would make our own decision indistinguishable from
L5's actual order in the tape, the ledger and the provenance chain, which
is the error this programme has guarded against from the start. It is
therefore refused at the executor boundary by name
(CONTROLLER_INTENT_HAS_NO_AUTHORIZED_ADAPTER) rather than translated.

THAT REFUSAL IS THE HONEST STATE OF THE WORK, not a placeholder. Routing a
generated decision to the venue needs an adapter from an intent to a
submission that the registry, the reconciler and the watchdog all
understand, and building one is a money-path change. Until it exists and is
authorized, the boundary says so in one word instead of implying the path
is ready.

FAIL CLOSED, EVERYWHERE. An unknown provenance refuses. A missing executor,
transport, store or registry refuses. A safety reading that raises refuses,
and reports BLOCKED rather than contradicting the gate it failed to read.
No gate here can be satisfied by the absence of information.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

#: L5's own observed action, translated by the frozen rule.
L5_FOLLOWED = "L5_FOLLOWED"
#: our own inventory-driven controller's decision. NEVER an L5 order.
CONTROLLER_GENERATED = "CONTROLLER_GENERATED"
PROVENANCES = (L5_FOLLOWED, CONTROLLER_GENERATED)

#: THE ORDER THE GATES APPLY IN, pinned by test. It is the deployed order
#: with one addition at the front (provenance, which must be known before
#: anything else can be reasoned about) and one at the executor boundary.
#: Nothing was reordered: window, sell-scope and halt are where they were
#: in `Runner.handle`, and everything after EXECUTOR_BOUNDARY is the
#: executor's own chain, unchanged.
GATE_ORDER: Tuple[str, ...] = (
    "PROVENANCE_KNOWN",
    "CURRENT_WINDOW",
    "SELL_IN_PROBE_SCOPE",
    "OPERATOR_HALT",
    "SHADOW_ONLY_LOCK",
    "DURABLE_HALT_LATCH",
    "OWNER_ARM_TOKEN",
    "TRANSPORT_ARMED",
    "ACCOUNT_WIDE_EXPOSURE",
    "ORDER_REGISTRY_OWNERSHIP",
    "RECONCILIATION_CERTAIN",
    "EXECUTOR_BOUNDARY",
)

#: WHAT THIS CHAIN IS NOT: the complete executor chain.
#:
#: `l5_live_execution.handle` is 979 lines with THIRTY refusal sites. The
#: gates below are the runner's real pre-executor chain (exact), plus a
#: READ-ONLY SNAPSHOT of four facts the transport and halt gate enforce
#: themselves, plus two preconditions the runner owns. Everything in this
#: list is enforced elsewhere whether or not it is read here; nothing in
#: this list REPLACES an executor gate.
#:
#: These are the executor's own refusals, in its order, and this module
#: does NOT evaluate them. They are enumerated so that "cleared here" can
#: never be mistaken for "would submit", and so that a refusal added to
#: the executor shows up as a mismatch in
#: tests/test_l5_intent_gate.py::test_the_executor_refusal_inventory_is_
#: still_complete rather than silently widening the gap.
EXECUTOR_GATES_NOT_EVALUATED_HERE: Tuple[str, ...] = (
    "HALT_GATE:HALT",                 # durable latch, read FIRST in handle
    "L5_NATIVE:UNREADABLE_ROW",
    "PMUS_TRANSLATION:STALE_ACTION",
    "SAFETY_OVERLAY:TICK_SIZE_UNKNOWN",
    "PMUS_TRANSLATION:PRICE_BELOW_MIN_TICK",
    "PMUS_TRANSLATION:QUANTITY_BELOW_MIN_INCREMENT",
    "PMUS_TRANSLATION:TRANSLATION_REASON",
    "SAFETY_OVERLAY:CONFLICTING_EXPERIMENTS_TS_AND_QM",
    "TWO_SIDED_PROBE:WINDOW_START_UNKNOWN",
    "TWO_SIDED_PROBE:MAY_PLACE_LATE",
    "TWO_SIDED_PROBE:ACCUMULATION_EXHAUSTED",
    "TWO_SIDED_PROBE:PLAN_REASON",
    "TWO_SIDED_PROBE:LEG_INVALID",
    "TWO_SIDED_PROBE:ACCUMULATION_EXHAUSTED_2",
    "QUEUE_MEASUREMENT:BUILD_REASON",
    "SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER",
    "SAFETY_OVERLAY:QUANTITY_INCREMENT_UNKNOWN",
    "SAFETY_OVERLAY:QUANTITY_BELOW_MIN_INCREMENT_CMP",
    "SAFETY_OVERLAY:IMBALANCE_INCREASE_SUPPRESSED",
    "VALIDATION_GATES:RUN_SCOPED",
    "SAFETY_OVERLAY:ACCOUNT_RISK_REASON",
    "SAFETY_OVERLAY:DAY_REALIZED_UNAVAILABLE",
    "SAFETY_OVERLAY:SAFETY_LIMITS_REFUSE",
    "SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP",
    "SUBMISSION_CLAIM:CLAIM_REASON",
    "SAFETY_OVERLAY:INTENT_MUTATED_AFTER_GATES",
    "SAFETY_OVERLAY:REFUSED_BEFORE_ORDER_POST",
    "SAFETY_OVERLAY:SUBMIT_OUTCOME_UNKNOWN",
    "SAFETY_OVERLAY:ACK_WITHOUT_VENUE_ID",
    "SAFETY_OVERLAY:ACK_NOT_PERSISTED",
)

#: WHERE THIS LIST'S ORDER COMES FROM, so it is not invented here. Gates
#: 1-4 are `Runner.handle`'s own order, unchanged. Gates 5-8 are the order
#: `Runner.submission_gate` already reports -- its docstring says "in the
#: order the decision path applies them" -- and that is the deployed
#: definition, so matching it is the conservative choice. NOTE THE ONE
#: DIVERGENCE: inside `l5_live_execution.handle` the DURABLE latch is read
#: FIRST of all, before translation, whereas this list reads the shadow
#: lock before it. That can only change WHICH reason is reported when both
#: are tripped, never whether the intent is refused, because the shadow
#: lock is applied structurally at `roll()` before the executor is reached
#: at all.
GATE_ORDER_PROVENANCE = (
    "1-4: Runner.handle, unchanged",
    "5-8: Runner.submission_gate's own documented order",
    "9-11: runner-owned preconditions (exposure reader, registry, "
    "reconciliation)",
    "12: this module's own boundary",
)

#: the layer label a refusal is filed against, kept identical to the
#: deployed strings so the tape, the stats keys and every existing
#: dashboard keep meaning what they meant.
LAYER = {
    "PROVENANCE_KNOWN": "SAFETY_OVERLAY",
    "CURRENT_WINDOW": "PMUS_TRANSLATION",
    "SELL_IN_PROBE_SCOPE": "SAFETY_OVERLAY",
    "OPERATOR_HALT": "SAFETY_OVERLAY",
    "EXECUTOR_BOUNDARY": "SAFETY_OVERLAY",
}


class Intent:
    """One decision, with its origin attached and nothing inferred.

    `row` is L5's activity row and exists ONLY for L5_FOLLOWED. A
    controller intent has none and must never be given one; the executor
    boundary refuses it for exactly that reason rather than inventing a
    row to satisfy a signature.
    """

    __slots__ = ("provenance", "market", "window", "side", "price", "size",
                 "relation", "row", "source_ref", "policy", "scenario")

    def __init__(self, *, provenance: str, market: Optional[str],
                 window: Optional[str], side: Optional[str] = None,
                 price: Optional[D] = None, size: Optional[D] = None,
                 relation: Optional[str] = None,
                 row: Optional[Mapping[str, Any]] = None,
                 source_ref: Optional[str] = None,
                 policy: Optional[str] = None,
                 scenario: Optional[str] = None) -> None:
        self.provenance = provenance
        self.market, self.window = market, window
        self.side, self.price, self.size = side, price, size
        self.relation, self.row = relation, row
        self.source_ref, self.policy, self.scenario = (source_ref, policy,
                                                       scenario)

    @property
    def is_l5(self) -> bool:
        return self.provenance == L5_FOLLOWED

    def describe(self) -> Dict[str, Any]:
        """What this intent is, for the record. Never includes the L5 row:
        the row is reproduced by the money path's own L5_NATIVE block and
        duplicating it here would create a second, divergeable copy."""
        return {"provenance": self.provenance, "market": self.market,
                "window": self.window, "side": self.side,
                "price": None if self.price is None else str(self.price),
                "size": None if self.size is None else str(self.size),
                "relation": self.relation, "policy": self.policy,
                "scenario": self.scenario, "source_ref": self.source_ref,
                "carries_l5_row": self.row is not None}


def controller_coid(intent: Intent) -> str:
    """The deterministic client order id for a generated decision.

    DETERMINISTIC ON PURPOSE: the same decision retried produces the same
    id, so `already_submitted` can refuse the duplicate instead of placing
    a second order. The `ctl-` prefix means a human reading the venue's
    open orders can tell our own decision from an L5-followed one without
    consulting the ledger.
    """
    return "ctl-%s-%s-%s" % (intent.policy or "p", intent.scenario or "s",
                             intent.source_ref or "x")


def _blocked(gate: str, reason: str, detail: Any = None) -> Dict[str, Any]:
    return {"gate": gate, "passed": False, "reason": reason,
            "detail": detail, "layer": LAYER.get(gate, "SAFETY_OVERLAY")}


def _ok(gate: str, detail: Any = None) -> Dict[str, Any]:
    return {"gate": gate, "passed": True, "reason": None, "detail": detail,
            "layer": LAYER.get(gate, "SAFETY_OVERLAY")}


class ControllerLiveAuthorization:
    """EXPLICIT, UNFORGEABLE PERMISSION for a controller-generated intent to
    reach the executor. NOTHING IN PRODUCTION CONSTRUCTS ONE.

    THIS IS NOT AN ENVIRONMENT VARIABLE, and that is the whole point. Every
    other switch in this system is a string someone can set --
    L5_SHADOW_ONLY, PMUS_Q1_LIVE, L5_CANCEL_MECHANISM -- and each of those
    is a thing that can be flipped by accident, by a copied deploy
    template, or by a well-meaning operator who did not know what it
    guarded. A capability that only exists when a CALLER PASSES AN OBJECT
    cannot be turned on by configuration at all: `Runner` passes nothing,
    so in production the adapter is not disabled, it is absent.
    `tests/test_l5_intent_gate.py` asserts that no module under src/ ever
    constructs one.
    """

    __slots__ = ("granted_by", "market", "max_size", "note")

    def __init__(self, *, granted_by: str, market: str, max_size: D,
                 note: str = "") -> None:
        if not granted_by or not market:
            raise ValueError("an authorization must name its grantor and "
                             "its single market")
        if max_size <= 0:
            raise ValueError("an authorization must bound size")
        self.granted_by = str(granted_by)
        self.market = str(market)
        self.max_size = D(max_size)
        self.note = str(note)

    def covers(self, intent: "Intent") -> Optional[str]:
        """None when this authorization covers the intent, else why not.

        BOUND TO ONE MARKET AND ONE SIZE. An authorization that travelled
        to another market, or grew a size, would be a different grant than
        the one that was given.
        """
        if intent.provenance != CONTROLLER_GENERATED:
            return "AUTHORIZATION_IS_FOR_CONTROLLER_INTENTS_ONLY"
        if intent.market != self.market:
            return "AUTHORIZATION_BOUND_TO_MARKET"
        if intent.size is None or D(intent.size) <= 0:
            return "INTENT_HAS_NO_SIZE"
        if D(intent.size) > self.max_size:
            return "INTENT_EXCEEDS_AUTHORIZED_SIZE"
        return None

    def describe(self) -> Dict[str, Any]:
        return {"granted_by": self.granted_by, "market": self.market,
                "max_size": str(self.max_size), "note": self.note}


#: The modules that are allowed to reach the wire, because each spends a
#: single-use `claim_submission` in its own body before sending. This is a
#: MIRROR for reporting, not the authority: the authority is
#: `tests/test_l5_one_submission_claim.py`, which scans src/ and fails if a
#: third site appears. Named here so a refusal can say where a send would
#: have to live.
CLAIM_GATED_SUBMIT_SITES: Tuple[str, ...] = (
    "l5_live_execution.py",
    "l5_cancel_mechanism.py",
)


class IntentGate:
    """The shared chain. Holds NOTHING of its own.

    Every safety fact comes from the objects the runner already uses -- the
    same store, the same transport, the same executor, the same halt gate,
    the same registry. There is deliberately no second copy of any limit or
    latch here: a gate that keeps its own notion of the rules is a gate that
    can disagree with the one that actually refuses.
    """

    def __init__(self, runner: Any,
                 controller_auth: Optional[ControllerLiveAuthorization] = None
                 ) -> None:
        self.runner = runner
        #: ABSENT IN PRODUCTION. `Runner` constructs the gate with one
        #: argument, so this is None there and the adapter below cannot
        #: run at all. It is a constructor argument rather than a setting
        #: so that no configuration change can supply it.
        self.controller_auth = controller_auth

    # ------------------------------------------------------- read-only view
    def evaluate(self, intent: Intent) -> Dict[str, Any]:
        """Which gate would refuse this intent, in order. SUBMITS NOTHING.

        Runs for either provenance, which is the point: it is how a
        controller-generated decision can be shown to be subject to the
        same HALT, the same shadow lock, the same arm token, the same
        account-wide exposure and the same reconciliation certainty as an
        L5-followed one, without any of them having to be trusted on
        assertion.

        Stops at the FIRST refusal, exactly as the submission path does, so
        the reported blocker is the one that would actually fire.
        """
        gates: list = []
        r = self.runner
        for gate in GATE_ORDER:
            try:
                res = self._one(gate, intent)
            except BaseException as exc:                        # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # A SAFETY READING THAT RAISES IS A REFUSAL. Reporting it as
                # passed because we could not read it is the one failure
                # mode a gate must not have.
                res = _blocked(gate, "UNREADABLE:%s" % type(exc).__name__)
            gates.append(res)
            if not res["passed"]:
                return {"cleared_here": False, "blocked_by": gate,
                        "reason": res["reason"], "layer": res["layer"],
                        "gates": gates, "intent": intent.describe(),
                        "complete_chain": False,
                        "not_evaluated_here":
                            list(EXECUTOR_GATES_NOT_EVALUATED_HERE),
                        "labels": ("READ-ONLY. Nothing was submitted, "
                                   "reserved or registered.")}
        return {"cleared_here": True, "blocked_by": None, "reason": None,
                "layer": None, "gates": gates, "intent": intent.describe(),
                "complete_chain": False,
                "not_evaluated_here":
                    list(EXECUTOR_GATES_NOT_EVALUATED_HERE),
                "labels": ("READ-ONLY, and NOT a verdict that an order "
                           "would be placed. `cleared_here` means only the "
                           "gates in GATE_ORDER passed; the executor's own "
                           "%d refusals listed in not_evaluated_here are "
                           "applied by the executor and are NOT checked "
                           "here. `admit` is the only path toward the "
                           "venue."
                           % len(EXECUTOR_GATES_NOT_EVALUATED_HERE))}

    def evaluate_prefix(self, intent: Intent, *, upto: str) -> Dict[str, Any]:
        """The chain up to and including one named gate.

        `Runner.handle` needs exactly the first four -- provenance, window,
        sell scope and operator halt -- because everything after them is
        the executor's own chain and the executor applies it itself. Asking
        for a prefix keeps ONE definition of the order: there is no second
        list here that could be reordered independently.
        """
        if upto not in GATE_ORDER:
            raise ValueError("unknown gate: %r" % (upto,))
        stop = GATE_ORDER.index(upto)
        gates: list = []
        for gate in GATE_ORDER[:stop + 1]:
            try:
                res = self._one(gate, intent)
            except BaseException as exc:                        # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                res = _blocked(gate, "UNREADABLE:%s" % type(exc).__name__)
            gates.append(res)
            if not res["passed"]:
                return {"cleared_here": False, "blocked_by": gate,
                        "reason": res["reason"], "layer": res["layer"],
                        "gates": gates, "intent": intent.describe()}
        return {"cleared_here": True, "blocked_by": None, "reason": None,
                "layer": None, "gates": gates, "intent": intent.describe()}

    # ------------------------------------------------------------ one gate
    def _one(self, gate: str, intent: Intent) -> Dict[str, Any]:
        r = self.runner
        if gate == "PROVENANCE_KNOWN":
            if intent.provenance not in PROVENANCES:
                return _blocked(gate, "UNKNOWN_PROVENANCE",
                                {"got": intent.provenance})
            return _ok(gate, {"provenance": intent.provenance})

        if gate == "CURRENT_WINDOW":
            t, ex = r.transport, r.executor
            cur = None if r.ws is None else str(r.ws)
            if t is None or ex is None or intent.window != cur:
                return _blocked(gate, "NOT_CURRENT_WINDOW",
                                {"intent_window": intent.window,
                                 "current_window": cur,
                                 "transport": t is not None,
                                 "executor": ex is not None})
            # AN ADDED CHECK, AND A DECLARED ONE. `Runner.handle` compared
            # only the window; this also compares the market against the
            # transport's own slug. On the L5 path it is provably a no-op
            # because `OBS.pmus_slug_for(ws)` and `AB.slug_for(ws)` return
            # the same string -- pinned by
            # test_the_added_market_check_is_a_no_op_on_the_l5_path -- and
            # it is a tightening, never a loosening, for any other caller.
            if intent.market and intent.market != getattr(
                    t, "market_slug", None):
                return _blocked(gate, "NOT_CURRENT_WINDOW",
                                {"intent_market": intent.market,
                                 "transport_market": getattr(
                                     t, "market_slug", None)})
            return _ok(gate, {"window": cur})

        if gate == "SELL_IN_PROBE_SCOPE":
            is_sell = bool((intent.row or {}).get("is_sell")) if intent.is_l5 \
                else str(intent.side or "").upper().startswith("SELL")
            if is_sell:
                return _blocked(gate, "SELL_NOT_IN_PROBE_SCOPE")
            return _ok(gate)

        if gate == "OPERATOR_HALT":
            if r.halted():
                return _blocked(gate, "OPERATOR_HALT")
            return _ok(gate)

        # ---- the executor's own chain, read through the runner's own
        # ---- effective-gate reader so this can never disagree with it
        sg = r.submission_gate() or {}
        if gate == "SHADOW_ONLY_LOCK":
            if sg.get("shadow_lock") is not False:
                return _blocked(gate, "SHADOW_ONLY_LOCK",
                                {"shadow_lock": sg.get("shadow_lock")})
            return _ok(gate)
        if gate == "DURABLE_HALT_LATCH":
            if sg.get("halt_latch") != "CLEAR":
                return _blocked(gate, "HALT:%s" % (sg.get("halt_why")
                                                   or sg.get("halt_latch")),
                                {"halt_latch": sg.get("halt_latch")})
            return _ok(gate)
        if gate == "OWNER_ARM_TOKEN":
            if not sg.get("owner_arm_token"):
                return _blocked(gate, "NO_OWNER_ARM_TOKEN")
            return _ok(gate)
        if gate == "TRANSPORT_ARMED":
            if not sg.get("armed"):
                return _blocked(gate, "NOT_ARMED")
            return _ok(gate)

        if gate == "ACCOUNT_WIDE_EXPOSURE":
            # the runner's OWN exposure reader, not a second copy of the
            # limits: `account_exposure_lines` is what the heartbeat and the
            # refusal both already use.
            rep = (r.account_exposure_lines()
                   if hasattr(r, "account_exposure_lines") else None)
            if rep is None:
                return _blocked(gate, "EXPOSURE_UNREADABLE")
            if not rep.get("ok"):
                return _blocked(gate, "ACCOUNT_EXPOSURE_REFUSED", rep)
            return _ok(gate, {"capacity_usd": rep.get("capacity_usd")})

        if gate == "ORDER_REGISTRY_OWNERSHIP":
            # A RESTING ORDER THE WATCHDOG CANNOT PROVE IS OURS IS
            # UNCANCELLABLE ON A SHARED ACCOUNT. No registry, no submission.
            if getattr(r, "registry_server", None) is None:
                return _blocked(gate, "NO_ORDER_REGISTRY")
            if not hasattr(r.store, "owns_venue_order_id"):
                return _blocked(gate, "NO_OWNERSHIP_ORACLE")
            return _ok(gate)

        if gate == "RECONCILIATION_CERTAIN":
            unresolved = r.store.pending_terminal() if hasattr(
                r.store, "pending_terminal") else None
            if unresolved:
                return _blocked(gate, "PENDING_TERMINAL_UNRESOLVED",
                                {"count": len(unresolved)})
            return _ok(gate)

        if gate == "EXECUTOR_BOUNDARY":
            if intent.is_l5:
                if intent.row is None:
                    return _blocked(gate, "L5_INTENT_WITHOUT_ROW")
                return _ok(gate, {"adapter": "l5_live_execution.handle"})
            # A GENERATED DECISION NEEDS AN EXPLICIT GRANT, and in
            # production there is none to have: `Runner` constructs this
            # gate without one, so this refuses by absence rather than by
            # a setting. It does NOT fabricate an L5 row to get through,
            # because our decision must never be indistinguishable from
            # L5's actual order.
            auth = self.controller_auth
            if auth is None:
                return _blocked(gate, "CONTROLLER_INTENT_HAS_NO_AUTHORIZED_"
                                      "ADAPTER",
                                {"why": ("no ControllerLiveAuthorization was "
                                         "supplied; production supplies "
                                         "none"),
                                 "never": "no L5 row is fabricated for it"})
            why = auth.covers(intent)
            if why is not None:
                return _blocked(gate, why, auth.describe())
            return _ok(gate, {"adapter": "l5_intent_gate.submit_controller_"
                                         "intent",
                              "authorization": auth.describe()})

        return _blocked(gate, "UNKNOWN_GATE")

    # ------------------------------------------------------------ the path
    def admit(self, intent: Intent, *,
              open_markets: Sequence[str] = ()) -> Dict[str, Any]:
        """The ONLY route from an intent toward the venue.

        L5_FOLLOWED delegates to the deployed executor through the same
        call with the same arguments, so nothing about its behaviour
        changes. CONTROLLER_GENERATED cannot get past EXECUTOR_BOUNDARY and
        so never reaches a submission path at all.
        """
        ev = self.evaluate(intent)
        if not ev["cleared_here"]:
            return {"submitted": False, "refused_by_layer": ev["layer"],
                    "refused_reason": ev["reason"],
                    "blocked_by": ev["blocked_by"],
                    "intent": ev["intent"], "gates": ev["gates"]}
        if not intent.is_l5:                  # unreachable; belt and braces
            return {"submitted": False, "refused_by_layer": "SAFETY_OVERLAY",
                    "refused_reason": "CONTROLLER_INTENT_HAS_NO_AUTHORIZED_"
                                      "ADAPTER",
                    "blocked_by": "EXECUTOR_BOUNDARY",
                    "intent": ev["intent"], "gates": ev["gates"]}
        rec = self.runner.executor.handle(
            intent.row, {}, market=intent.market, window=intent.window,
            open_markets=tuple(open_markets))
        return dict(rec, intent=ev["intent"])

    # ------------------------------------------------ the controller adapter
    #: THE STORE API THIS ADAPTER NEEDS, checked before it is used. An
    #: earlier draft of this method invented `reserve`/`release`/
    #: `record_submitted`/`owns_client_order_id`, none of which exist on
    #: `l5_live_execution.InventoryStore`. It was unreachable in production
    #: so it would never have raised -- which is exactly why code like that
    #: is dangerous: it reads as ready. The names below are the REAL ones,
    #: and the precondition refuses by name if any goes missing.
    REQUIRED_STORE_API: Tuple[str, ...] = (
        "already_submitted",      # the one-shot guard against duplicates
        "record_submit",          # durable record BEFORE the wire
        "attach_venue_id",        # what a cancel is named by
        "mark_uncertain",         # a possibly-resting order poisons the book
        "owns_venue_order_id",    # the watchdog's ownership oracle
        "terminate_order",        # release on a venue-terminal state
        "save",
    )

    # -------------------------------------------------- the adapter
    #
    # THIS MODULE OWNS NO WIRE CALL, AND THAT IS THE CORRECTION.
    #
    # An earlier draft of this adapter ended in a transport send of its
    # own.
    # `tests/test_l5_one_submission_claim.py` failed it, and the test was
    # right: the account is protected by the rule that NO path reaches the
    # wire without spending a single-use `claim_submission` in the SAME
    # module as the send. That draft was a second, unclaimed door -- the
    # exact thing the invariant exists to prevent -- and it contradicted
    # this module's own stated position, that routing a generated decision
    # to the venue is a money-path change and is not authorized.
    #
    # So the wire is gone rather than registered. `SUBMIT_SITES` stays at
    # the two claim-gated routes it had. What is left here is everything
    # that is NOT the send: the chain, the registry preconditions, the
    # duplicate guard, the pre-send record, and the ambiguity semantics --
    # each a separate, testable step for the claim-gated route that will
    # one day call them, in a module that takes the claim.
    #
    # The practical consequence is stronger than the absent authorization:
    # production cannot submit a controller intent because THERE IS NO
    # ROUTE, not because a permission is withheld.

    def prepare_controller_submission(self, intent: Intent) -> Dict[str, Any]:
        """Everything that must hold BEFORE a send, and nothing that sends.

        Returns `prepared: True` with the wire payload, or a refusal. It
        reaches no venue and writes nothing.
        """
        ev = self.evaluate(intent)
        if not ev["cleared_here"]:
            return {"prepared": False, "provenance": CONTROLLER_GENERATED,
                    "refused_by_layer": ev["layer"],
                    "refused_reason": ev["reason"],
                    "blocked_by": ev["blocked_by"],
                    "intent": ev["intent"], "gates": ev["gates"]}
        r, auth = self.runner, self.controller_auth
        if auth is None:                      # unreachable; belt and braces
            return {"prepared": False, "provenance": CONTROLLER_GENERATED,
                    "refused_by_layer": "SAFETY_OVERLAY",
                    "refused_reason": "CONTROLLER_INTENT_HAS_NO_AUTHORIZED_"
                                      "ADAPTER",
                    "blocked_by": "EXECUTOR_BOUNDARY",
                    "intent": ev["intent"]}

        coid = controller_coid(intent)
        base = {"provenance": CONTROLLER_GENERATED, "client_order_id": coid,
                "intent": ev["intent"], "authorization": auth.describe(),
                "labels": ("a CONTROLLER-GENERATED order. It is NOT one of "
                           "L5's actions and must never be counted as "
                           "one.")}

        missing = [m for m in self.REQUIRED_STORE_API
                   if not callable(getattr(r.store, m, None))]
        if missing:
            return dict(base, prepared=False,
                        refused_by_layer="SAFETY_OVERLAY",
                        refused_reason="STORE_API_INCOMPLETE",
                        detail={"missing": missing}, halt_required=False)

        try:
            if r.store.already_submitted(coid):
                return dict(base, prepared=False,
                            refused_by_layer="SUBMISSION_CLAIM",
                            refused_reason="DUPLICATE_INTENT",
                            halt_required=False)
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return dict(base, prepared=False,
                        refused_by_layer="SUBMISSION_CLAIM",
                        refused_reason="DUPLICATE_CHECK_UNREADABLE:%s"
                                       % type(exc).__name__,
                        halt_required=False)

        return dict(base, prepared=True,
                    wire={"client_order_id": coid, "market": intent.market,
                          "side": intent.side, "price": str(intent.price),
                          "qty": str(intent.size),
                          "provenance": CONTROLLER_GENERATED})

    def record_controller_submission(self, coid: str,
                                     wire: Mapping[str, Any]
                                     ) -> Dict[str, Any]:
        """The durable record, which must exist BEFORE the wire.

        A crash between send and ack must leave something reconciliation and
        the watchdog can both find. The claim-gated route calls this, then
        takes its claim, then sends.
        """
        base = {"provenance": CONTROLLER_GENERATED, "client_order_id": coid}
        try:
            self.runner.store.record_submit(coid, dict(wire),
                                            mode=CONTROLLER_GENERATED)
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # NOTHING WAS SENT, so nothing is resting. Refuse without a
            # halt: an unrecorded order that never left is not a risk.
            return dict(base, recorded=False,
                        refused_by_layer="SAFETY_OVERLAY",
                        refused_reason="PRE_SEND_RECORD_FAILED:%s"
                                       % type(exc).__name__,
                        halt_required=False)
        return dict(base, recorded=True)

    def resolve_controller_submission(self, coid: str,
                                      outcome: Mapping[str, Any]
                                      ) -> Dict[str, Any]:
        """Classify what the venue did with a send THIS MODULE DID NOT MAKE.

        `outcome` is what the claim-gated route got back: `{"ack": {...}}`,
        or `{"error": exc}` with an optional `refused_before_post` flag that
        only the transport can set.

        Ambiguity is POSSIBLY RESTING, never "probably fine": the book is
        marked uncertain and the caller is told to halt.
        """
        r = self.runner
        base = {"provenance": CONTROLLER_GENERATED, "client_order_id": coid}
        if outcome.get("error") is not None:
            cls = type(outcome["error"]).__name__
            # DID IT REACH THE VENUE? Only the transport can say, and when
            # it cannot the answer is UNKNOWN, which is a halt.
            if outcome.get("refused_before_post"):
                return dict(base, submitted=False,
                            refused_by_layer="SAFETY_OVERLAY",
                            refused_reason="REFUSED_BEFORE_ORDER_POST:%s"
                                           % cls, halt_required=False)
            self._mark_uncertain("SUBMIT_OUTCOME_UNKNOWN:%s:%s" % (coid, cls))
            return dict(base, submitted=None,
                        refused_by_layer="SAFETY_OVERLAY",
                        refused_reason="SUBMIT_OUTCOME_UNKNOWN:%s" % cls,
                        halt_required=True)

        ack = outcome.get("ack") or {}
        vid = ack.get("order_id") or (ack.get("order") or {}).get("id")
        if not vid:
            # AN ACK WITHOUT AN ID IS AN ORDER WE CANNOT CANCEL. The venue
            # cancels by its own id; ours names nothing.
            self._mark_uncertain("ACK_WITHOUT_VENUE_ID:%s" % coid)
            return dict(base, submitted=None,
                        refused_by_layer="SAFETY_OVERLAY",
                        refused_reason="ACK_WITHOUT_VENUE_ID",
                        halt_required=True)
        try:
            r.store.attach_venue_id(coid, vid)
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._mark_uncertain("ACK_NOT_PERSISTED:%s:%s"
                                 % (coid, type(exc).__name__))
            return dict(base, submitted=None, venue_order_id=vid,
                        refused_by_layer="SAFETY_OVERLAY",
                        refused_reason="ACK_NOT_PERSISTED",
                        halt_required=True)

        # OWNERSHIP, PROVED AFTER THE ID EXISTS. Before the venue gives us
        # an id there is nothing for the oracle to recognise; the moment
        # there is, the watchdog must be able to prove the order is ours or
        # it cannot withdraw it on a shared account.
        try:
            owned = bool(r.store.owns_venue_order_id(vid))
        except BaseException:                                   # noqa: BLE001
            owned = False
        if not owned:
            self._mark_uncertain("OWNERSHIP_NOT_SERVABLE:%s" % coid)
            return dict(base, submitted=True, venue_order_id=vid,
                        refused_by_layer="SAFETY_OVERLAY",
                        refused_reason="OWNERSHIP_NOT_SERVABLE_AFTER_ACK",
                        halt_required=True)
        return dict(base, submitted=True, venue_order_id=vid, owned=True,
                    cancel_route=("transport.cancel(venue_order_id) with "
                                  "store.owns_venue_order_id as the oracle "
                                  "-- the same route the deadman uses"))

    #: the reason a prepared controller intent still does not reach the
    #: venue. Not a permission that could be granted from here: the send
    #: belongs to a module that spends a single-use submission claim, and
    #: this one does not have one to spend.
    NO_CLAIMED_ROUTE = "CONTROLLER_SUBMISSION_HAS_NO_CLAIM_GATED_ROUTE"

    def submit_controller_intent(self, intent: Intent) -> Dict[str, Any]:
        """Run every precondition, then refuse, because there is no send.

        WHY NOT `ex.handle` EITHER. The executor derives side, price and
        size from L5's row and stamps the record L5_NATIVE. Feeding it a
        generated decision needs a fabricated row and would file our own
        order under L5's name in the tape, the ledger and the registry.
        That is why a claim-gated route for CONTROLLER_GENERATED has to be
        built rather than borrowed -- and building it is the money-path
        change that is not authorized.

        So this is the honest end of the prepared path: everything that can
        be checked without touching the venue IS checked, and then it stops
        and says which single thing is missing.
        """
        pre = self.prepare_controller_submission(intent)
        if not pre.get("prepared"):
            return dict(pre, submitted=False)
        return dict({k: v for k, v in pre.items() if k != "prepared"},
                    submitted=False,
                    refused_by_layer="SAFETY_OVERLAY",
                    refused_reason=self.NO_CLAIMED_ROUTE,
                    blocked_by="EXECUTOR_BOUNDARY",
                    halt_required=False,
                    detail={"claimed_routes": sorted(CLAIM_GATED_SUBMIT_SITES),
                            "what_is_missing": (
                                "a send that spends claim_submission in its "
                                "own module, for CONTROLLER_GENERATED "
                                "provenance")})


    def _mark_uncertain(self, why: str) -> None:
        """A possibly-resting order makes our OWN book uncertain, and an
        uncertain book must refuse further submissions until the venue
        settles the question. Failing to mark it is worse than the
        ambiguity itself."""
        try:
            # the REAL signature: one reason string. The coid is folded
            # into it so an operator can find the order it refers to.
            self.runner.store.mark_uncertain(why)
        except BaseException:                                   # noqa: BLE001
            pass
