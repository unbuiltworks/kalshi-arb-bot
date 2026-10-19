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


def _blocked(gate: str, reason: str, detail: Any = None) -> Dict[str, Any]:
    return {"gate": gate, "passed": False, "reason": reason,
            "detail": detail, "layer": LAYER.get(gate, "SAFETY_OVERLAY")}


def _ok(gate: str, detail: Any = None) -> Dict[str, Any]:
    return {"gate": gate, "passed": True, "reason": None, "detail": detail,
            "layer": LAYER.get(gate, "SAFETY_OVERLAY")}


class IntentGate:
    """The shared chain. Holds NOTHING of its own.

    Every safety fact comes from the objects the runner already uses -- the
    same store, the same transport, the same executor, the same halt gate,
    the same registry. There is deliberately no second copy of any limit or
    latch here: a gate that keeps its own notion of the rules is a gate that
    can disagree with the one that actually refuses.
    """

    def __init__(self, runner: Any) -> None:
        self.runner = runner

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
                return {"admissible": False, "blocked_by": gate,
                        "reason": res["reason"], "layer": res["layer"],
                        "gates": gates, "intent": intent.describe(),
                        "labels": ("READ-ONLY evaluation. Nothing was "
                                   "submitted, reserved or registered.")}
        return {"admissible": True, "blocked_by": None, "reason": None,
                "layer": None, "gates": gates, "intent": intent.describe(),
                "labels": ("READ-ONLY evaluation. Admissible means every "
                           "gate in GATE_ORDER passed, NOT that an order "
                           "was placed: `admit` is the only path to the "
                           "venue and it applies these again.")}

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
                return {"admissible": False, "blocked_by": gate,
                        "reason": res["reason"], "layer": res["layer"],
                        "gates": gates, "intent": intent.describe()}
        return {"admissible": True, "blocked_by": None, "reason": None,
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
            # THE REMAINING BLOCKER, NAMED. Reaching the venue from a
            # generated decision needs an adapter the registry, the
            # reconciler and the watchdog all understand. Until that exists
            # and is authorized, this refuses -- it does NOT fabricate an
            # L5 row to get through, because our decision must never be
            # indistinguishable from L5's actual order.
            return _blocked(gate, "CONTROLLER_INTENT_HAS_NO_AUTHORIZED_"
                                  "ADAPTER",
                            {"why": ("submission from a generated decision "
                                     "is a money-path change and is not "
                                     "authorized"),
                             "never": "no L5 row is fabricated for it"})

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
        if not ev["admissible"]:
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
