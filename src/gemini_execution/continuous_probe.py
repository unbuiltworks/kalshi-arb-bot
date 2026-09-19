"""GEMINI_L5_CONTINUOUS_PROBE_V1 -- the L5 surface controlling the proven
Gemini WS transport for ONE BTC5m window, and stopping.

WHAT THIS TESTS, AND WHAT IT DOES NOT. It is not another connectivity canary:
this account already has venue-confirmed FILLED maker-or-cancel BTC5m orders
through order.place. The open question is whether the L5 surface, driving that
transport continuously inside one window, reproduces the inventory behaviour
the historical work inferred, holds residual inside a hard cap, and produces
measurable settled economics.

THE BEHAVIOUR IT IS SIZED TO REPRODUCE, from L5_OPERATING_MODEL.md 2a
(5,546 windows, 693,916 fills, chronological TRAIN/OOS):

  * L5 is an inventory OSCILLATOR. 84.3% of imbalance episodes end by
    overshooting THROUGH zero; 1.2% land on it; exactly one window of 5,546
    ended flat. There is no completion controller driving inventory to zero,
    and matched inventory is a by-product of the oscillation.
  * P(next action = EXTEND) = 0.66 unconditionally, so the expected same-side
    run is about 1/(1-0.66) = 2.9 actions. Run length is the ONLY feature that
    separates the next action and keeps its bin ordering out of sample
    (Spearman 1.00; run 4+ -> 0.733 OOS).
  * Directional momentum is FALSIFIED: |dp| predicts extension, sign(dp) does
    not, so movement direction must never be used to pick a side.
  * Favored-side accumulation is FALSIFIED: P(next fill on the favored side)
    is 0.4919 TRAIN / 0.4847 OOS -- a coin flip.

WHICH SETS THE ENVELOPE, rather than a number being chosen for comfort. The
action size is the venue's own quantityMinimum, 0.01. A residual cap below
2 x 0.01 would suppress the widening side after the FIRST fill, so no EXTEND
could ever occur and the probe would degenerate into the one-order test this
exists to replace. A cap of 4 x 0.01 admits the modal run (2.9) and reaches
the run-4+ regime that was validated out of sample. So MAX_RESIDUAL = 0.04 is
the SMALLEST cap that can still exercise the oscillator.

AND A COST THAT MUST BE STATED, NOT DISCOVERED LATER. The same source measures
carried residual as a LOSS on both sides, favored included: +$1,631 TRAIN but
-$12,971 OOS, and median residual carried into settlement is worth -$144,065
across the sample. Reproducing this process faithfully means reproducing a
directional leg with negative measured expectancy. The residual cap is the
control on that, and at 0.04 contracts the whole directional leg is bounded by
about four cents. This probe is FIDELITY, not monetization; the ADAPTED
question of how much of the process is needed WITHOUT the residual economics
is a separate frozen workstream.

NOTHING HERE DECIDES A STRATEGY. Every quote comes from
`src.l5_surface.engine.desired_surface`, the existing venue-neutral L5
implementation, driven by the frozen L5_ADAPTED_GEMINI_CRYPTO lane set with
probe-scoped size and residual overrides that are marked OURS_ADAPTED and
never written back to the frozen artifact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.l5_surface import engine as ENG, params as PR

LABEL = "GEMINI_L5_CONTINUOUS_PROBE_V1"

# ---------------------------------------------------------------- envelope

#: the venue's own quantityMinimum AND quantityIncrement for BTC5m
ACTION_SIZE = D("0.01")

#: run 4+ is the out-of-sample validated persistence regime; the modal run is
#: about 2.9. Fewer than 2 makes an EXTEND impossible.
MAX_SAME_SIDE_RUN = 4
MAX_RESIDUAL = ACTION_SIZE * MAX_SAME_SIDE_RUN          # 0.04
MAX_IMBALANCE = MAX_RESIDUAL       # stated separately; same quantity here

#: one window at 300s with a 30s minimum rest admits about ten refresh
#: cycles. Twelve actions is enough to see a run, a suppression and a flip,
#: and small enough that the whole probe cannot run away.
MAX_ACTIONS = 12
MAX_YES = D("0.10")
MAX_NO = D("0.10")
#: worst case is every action filling at a price of 1.00
MAX_TOTAL_SPEND = D("0.15")

#: from the lane set's own minimum_rest_time
STALE_ORDER_S = 30.0
#: no market-state update for this long and the probe withdraws and stops
DEADMAN_S = 20.0

# ------------------------------------------------------- end of window
#
# THE LANE SET DOES NOT STOP QUOTING BEFORE EXPIRY. Its TTE_policy is
# [0, 300] and its withdrawal_policy is WITHDRAW_ON_TTE_FLOOR with
# tte_floor_s = 0, so the surface would stand right down to settlement. For a
# FIDELITY shadow that is correct -- it is what was measured. For a probe
# holding real inventory it is not, because an OPEN or EXTEND placed with
# seconds left creates residual that has no time left to be complemented, and
# carried residual is the leg with measured NEGATIVE out-of-sample expectancy
# (+$1,631 TRAIN, -$12,971 OOS; -$144,065 across the sample).
#
# So the probe adds an end-of-window floor. It is NOT a new strategy and not a
# tuned number: both values come from the lane's own minimum_rest_time.
#
#   below 2 x rest  a new OPEN or EXTEND cannot get one rest cycle for itself
#                   AND one for the complement that would match it, so it can
#                   only manufacture residual. OPEN and EXTEND stop.
#                   COMPLETE continues, because it REDUCES residual and is
#                   exactly what should still be trying to happen here.
#   below 1 x rest  nothing can meaningfully rest at all. Everything is
#                   withdrawn and nothing new is quoted.
#
# WHAT IS DELIBERATELY NOT DONE: no taker crossing is introduced to flatten.
# Whatever residual survives is CARRIED and labelled directional exposure. A
# forced cross would buy a flat book at the far touch, which is the most
# expensive exit historically measured and would be manufacturing a number
# rather than observing one.
NO_NEW_OPEN_EXTEND_TTE_S = 2 * STALE_ORDER_S     # 60.0
WITHDRAW_ALL_TTE_S = STALE_ORDER_S               # 30.0

WINDOW_OPEN = "WINDOW_OPEN"
WINDOW_COMPLETE_ONLY = "COMPLETE_ONLY"
WINDOW_WITHDRAWN = "WITHDRAWN"


def window_phase(tte_s: Any) -> str:
    """Which end-of-window phase this TTE is in. Unknown TTE withdraws."""
    if tte_s is None:
        return WINDOW_WITHDRAWN
    t = float(tte_s)
    if t < WITHDRAW_ALL_TTE_S:
        return WINDOW_WITHDRAWN
    if t < NO_NEW_OPEN_EXTEND_TTE_S:
        return WINDOW_COMPLETE_ONLY
    return WINDOW_OPEN

COHORT_PREFIX = "GEMI-BTC05M"

#: the engine's frozen outcome scope is ["Up", "Down"]. Handing it YES/NO
#: produced a surface with zero rungs once already; the mapping is explicit.
OUTCOME_TO_LEG = {"Up": "YES", "Down": "NO"}
LEG_TO_OUTCOME = {v: k for k, v in OUTCOME_TO_LEG.items()}

# lifecycle, in order. Nothing skips forward.
DECIDED, SUBMITTED, ACKNOWLEDGED, RESTING = (
    "DECIDED", "SUBMITTED", "ACKNOWLEDGED", "RESTING")
FILLED, PARTIAL, CANCELLED = "FILLED", "PARTIAL", "CANCELLED"
SETTLED, REALIZED = "SETTLED", "REALIZED"
LIFECYCLE = (DECIDED, SUBMITTED, ACKNOWLEDGED, RESTING, PARTIAL, FILLED,
             CANCELLED, SETTLED, REALIZED)

OPEN, EXTEND, COMPLETE, HOLD = "OPEN", "EXTEND", "COMPLETE", "HOLD"


class ProbeStop(Exception):
    """A hard bound was reached. The probe withdraws; it never adapts around
    a limit."""


def probe_params(lane: Optional[PR.SurfaceParams] = None) -> PR.SurfaceParams:
    """The lane set with probe-scoped size and residual, marked as OURS.

    The frozen L5_ADAPTED_GEMINI_CRYPTO artifact is READ and never written:
    its base/secondary quantity is 1 and its max_residual 3.00, which at an
    action size of 0.01 would need three hundred consecutive same-side fills
    before the oscillator's suppression could ever fire. Overriding them here
    keeps the frozen record honest about what was measured while letting the
    probe run at the venue minimum.
    """
    if lane is None:
        lane = PR.load_frozen()["L5_ADAPTED_GEMINI_CRYPTO"]
    over = {
        "base_parent_qty": str(ACTION_SIZE),
        "secondary_rung_qty": str(ACTION_SIZE),
        "max_residual": str(MAX_RESIDUAL),
    }
    out: Dict[str, PR.Param] = {}
    for f in PR.FIELDS:
        if f in over:
            out[f] = PR.Param(
                f, PR.OURS_ADAPTED, value=over[f],
                prior_evidence=(
                    "PROBE-SCOPED for %s. Sized from the venue's own "
                    "quantityMinimum and from the measured same-side run "
                    "distribution, NOT from anything observed about L5's "
                    "size. Never citable as an L5 finding." % LABEL))
        else:
            out[f] = lane.params[f]
    return PR.SurfaceParams(
        label=LABEL, kind="ADAPTED", params=out,
        notes=("%s derived from %s@%s; only size and residual are overridden "
               "and both are OURS_ADAPTED"
               % (LABEL, lane.label, lane.spec_sha256()[:16])))


# ------------------------------------------------------------- inventory

@dataclass
class Inventory:
    """Every component the accounting must keep SEPARATE, and nothing netted.

    Costs accumulate from venue-confirmed fills only. A resting quote, an
    acknowledgement and a theoretical fill all leave this untouched.
    """
    yes_qty: D = D("0")
    no_qty: D = D("0")
    yes_cost: D = D("0")
    no_cost: D = D("0")
    fills: List[Dict[str, Any]] = field(default_factory=list)
    #: consecutive fills on the same leg, the one OOS-stable predictor
    run_leg: Optional[str] = None
    run_len: int = 0

    def apply_fill(self, leg: str, qty: Any, price: Any,
                   *, source: str) -> None:
        if leg not in ("YES", "NO"):
            raise ValueError("leg must be YES or NO, got %r" % (leg,))
        q, p = D(str(qty)), D(str(price))
        if q <= 0:
            raise ValueError("a fill of %s is not a fill" % q)
        if leg == "YES":
            self.yes_qty += q
            self.yes_cost += q * p
        else:
            self.no_qty += q
            self.no_cost += q * p
        self.run_len = self.run_len + 1 if self.run_leg == leg else 1
        self.run_leg = leg
        self.fills.append({"leg": leg, "qty": str(q), "price": str(p),
                           "source": source, "stage": FILLED})

    # -- the ten components, each answerable on its own ------------------
    def matched(self) -> D:
        return min(self.yes_qty, self.no_qty)

    def residual(self) -> D:
        """Signed: positive is long YES, negative is long NO."""
        return self.yes_qty - self.no_qty

    def imbalance(self) -> D:
        return abs(self.residual())

    def avg_yes(self) -> Optional[D]:
        return (self.yes_cost / self.yes_qty) if self.yes_qty > 0 else None

    def avg_no(self) -> Optional[D]:
        return (self.no_cost / self.no_qty) if self.no_qty > 0 else None

    def effective_matched_pair_cost(self) -> Optional[D]:
        """avg YES + avg NO. Below 1.00 the matched leg is locked positive
        BEFORE fees; it is not realized until settlement pays."""
        a, b = self.avg_yes(), self.avg_no()
        return None if (a is None or b is None) else a + b

    def spend(self) -> D:
        return self.yes_cost + self.no_cost

    def report(self) -> Dict[str, Any]:
        pair = self.effective_matched_pair_cost()
        return {
            "yes_qty": str(self.yes_qty), "no_qty": str(self.no_qty),
            "matched_qty": str(self.matched()),
            "directional_residual": str(self.residual()),
            "inventory_imbalance": str(self.imbalance()),
            "avg_yes_cost": None if self.avg_yes() is None else str(self.avg_yes()),
            "avg_no_cost": None if self.avg_no() is None else str(self.avg_no()),
            "effective_matched_pair_cost": None if pair is None else str(pair),
            "total_spend_usd": str(self.spend()),
            "same_side_run": {"leg": self.run_leg, "len": self.run_len},
            "n_fills": len(self.fills),
            "labels": (
                "matched-pair economics, directional-residual P&L, fees, "
                "rebates and settlement cashflow are accounted SEPARATELY and "
                "never summed into one number. Nothing here is REALIZED: a "
                "cost basis is not a profit and settlement has not paid."),
        }


def classify(outcome: str, inv: Inventory) -> str:
    """OPEN / EXTEND / COMPLETE for a BUY on this outcome, from inventory.

    Not from price, not from momentum: sign(dp) is falsified as a side signal
    and favored-side accumulation is a coin flip, so neither may choose here.
    """
    leg = OUTCOME_TO_LEG.get(outcome)
    if leg is None:
        raise ValueError("unknown outcome %r; engine scope is Up/Down"
                         % (outcome,))
    res = inv.residual()
    if inv.yes_qty == 0 and inv.no_qty == 0:
        return OPEN
    if res == 0:
        return OPEN
    widening = (res > 0 and leg == "YES") or (res < 0 and leg == "NO")
    return EXTEND if widening else COMPLETE


# ----------------------------------------------------------------- gates

def risk_gates(inv: Inventory, actions_taken: int) -> List[str]:
    """Every hard bound, checked together so a report names all of them."""
    hit = []
    if actions_taken >= MAX_ACTIONS:
        hit.append("max_actions %d reached" % MAX_ACTIONS)
    if inv.imbalance() > MAX_IMBALANCE:
        hit.append("inventory_imbalance %s exceeds %s"
                   % (inv.imbalance(), MAX_IMBALANCE))
    if inv.yes_qty > MAX_YES:
        hit.append("yes inventory %s exceeds %s" % (inv.yes_qty, MAX_YES))
    if inv.no_qty > MAX_NO:
        hit.append("no inventory %s exceeds %s" % (inv.no_qty, MAX_NO))
    if inv.spend() > MAX_TOTAL_SPEND:
        hit.append("total spend %s exceeds %s"
                   % (inv.spend(), MAX_TOTAL_SPEND))
    return hit


def would_breach(leg: str, price: Any, inv: Inventory) -> Optional[str]:
    """Would filling THIS action breach a bound? Checked before quoting, so
    the probe never rests an order it could not afford to have filled."""
    q, p = ACTION_SIZE, D(str(price))
    yes = inv.yes_qty + (q if leg == "YES" else D("0"))
    no = inv.no_qty + (q if leg == "NO" else D("0"))
    if abs(yes - no) > MAX_IMBALANCE:
        return ("a fill would take imbalance to %s, past %s"
                % (abs(yes - no), MAX_IMBALANCE))
    if yes > MAX_YES:
        return "a fill would take YES to %s, past %s" % (yes, MAX_YES)
    if no > MAX_NO:
        return "a fill would take NO to %s, past %s" % (no, MAX_NO)
    if inv.spend() + q * p > MAX_TOTAL_SPEND:
        return ("a fill would take spend to %s, past %s"
                % (inv.spend() + q * p, MAX_TOTAL_SPEND))
    return None


# ------------------------------------------------------------- the cycle

def plan(state: Mapping[str, Any], inv: Inventory, *,
         sp: Optional[PR.SurfaceParams] = None,
         actions_taken: int = 0,
         now: Optional[float] = None) -> Dict[str, Any]:
    """ONE turn of the loop: market state -> L5 surface -> classified actions.

    The surface comes from the existing engine. This function adds no
    strategy; it labels each live rung against current inventory and applies
    the hard bounds. A rung the engine suppressed stays suppressed here with
    the engine's own reason, so the report says whether the OSCILLATOR
    withheld a quote or the ENVELOPE did.
    """
    sp = sp or probe_params()

    # UNKNOWN IS A STOP, NEVER AN ASSUMED FLAT.
    if state.get("inventory_known") is False:
        return {"stop": "inventory could not be established from authoritative "
                        "reads; unknown state holds, it never assumes flat",
                "actions": [], "holds": [], "surface": None}

    age = state.get("state_age_s")
    t = now if now is not None else state.get("t_utc")
    if age is None and t is not None and state.get("t_utc") is not None:
        age = float(t) - float(state["t_utc"])
    if age is not None and float(age) > DEADMAN_S:
        return {"stop": "DEADMAN: market state is %.1fs old, past %.1fs; "
                        "withdraw rather than quote into a stale book"
                        % (float(age), DEADMAN_S),
                "actions": [], "holds": [], "surface": None}

    hard = risk_gates(inv, actions_taken)
    if hard:
        return {"stop": "; ".join(hard), "actions": [], "holds": [],
                "surface": None}

    # THE ENGINE READS INVENTORY OUT OF THE STATE, and its suppression of a
    # widening rung is the entire oscillator. A caller that forgets to put
    # inventory in the state gets a surface computed as if flat: the envelope
    # would still refuse the over-size action, but the hold would be
    # attributed to ENVELOPE when the OSCILLATOR should have withheld it, and
    # the probe would report the wrong control doing the work. So the state
    # is built from the Inventory object here rather than trusted.
    state = dict(state)
    state["inventory"] = {LEG_TO_OUTCOME["YES"]: str(inv.yes_qty),
                          LEG_TO_OUTCOME["NO"]: str(inv.no_qty)}
    tte = state.get("tte_s")
    phase = window_phase(tte)
    if phase == WINDOW_WITHDRAWN:
        return {"stop": None, "actions": [], "holds": [],
                "window_phase": phase, "cancel_all": True,
                "surface": None,
                "why_no_surface": (
                    "tte %s is under one maker-rest cycle (%.1fs): withdraw "
                    "every working order and quote nothing. Residual that "
                    "survives is CARRIED and reported as directional "
                    "exposure; no taker cross is used to flatten it."
                    % (tte, WITHDRAW_ALL_TTE_S)),
                "inventory": inv.report()}

    got = ENG.desired_surface(state, sp)
    if not got.get("emitted"):
        return {"stop": None, "actions": [], "holds": [],
                "surface": got,
                "why_no_surface": got.get("reason"),
                "blocked_by": got.get("blocked_by")}

    surf = got["surface"]
    actions: List[Dict[str, Any]] = []
    holds: List[Dict[str, Any]] = []
    for r in surf.rungs:
        leg = OUTCOME_TO_LEG.get(r.outcome)
        base = {"outcome": r.outcome, "leg": leg,
                "price": r.max_acceptable_price,
                "qty": str(ACTION_SIZE), "rung_index": r.rung_index,
                "ticks_behind_bid": r.ticks_behind_bid}
        if r.suppressed_by is not None:
            holds.append({**base, "action": HOLD, "held_by": "OSCILLATOR",
                          "why": r.suppressed_by})
            continue
        breach = would_breach(leg, r.max_acceptable_price, inv)
        if breach:
            holds.append({**base, "action": HOLD, "held_by": "ENVELOPE",
                          "why": breach})
            continue
        act = classify(r.outcome, inv)
        # END OF WINDOW. A COMPLETE reduces residual and stays allowed until
        # nothing can rest at all; an OPEN or EXTEND with no time left for a
        # complement can only manufacture the leg with negative expectancy.
        if phase == WINDOW_COMPLETE_ONLY and act in (OPEN, EXTEND):
            holds.append({**base, "action": HOLD, "held_by": "END_OF_WINDOW",
                          "why": ("tte %.1fs is under %.1fs, which is two "
                                  "maker-rest cycles: a new %s could not be "
                                  "complemented before expiry"
                                  % (float(tte), NO_NEW_OPEN_EXTEND_TTE_S,
                                     act))})
            continue
        actions.append({**base, "action": act, "stage": DECIDED})
    return {"stop": None, "actions": actions, "holds": holds,
            "surface": got.get("report"),
            "inventory": inv.report(),
            "window_phase": phase,
            "cancel_all": phase == WINDOW_WITHDRAWN,
            "actions_remaining": MAX_ACTIONS - actions_taken}


def frames_for(plan_out: Mapping[str, Any], symbol: str,
               client_prefix: str = "l5cp") -> List[Dict[str, Any]]:
    """The exact WS frames the planned actions become. Builds NOTHING else.

    It routes through the same payload builder and the same dialect the
    proven transport uses, so a frame that would not pass the one-shot
    canary's validation cannot be produced here either.
    """
    from src.gemini_execution import payload as P, spec
    out = []
    for i, a in enumerate(plan_out.get("actions") or []):
        params = P.build_ws_order(
            symbol=symbol, price=str(a["price"]), quantity=str(a["qty"]),
            client_order_id=("%s-%s-%d" % (client_prefix, a["leg"].lower(), i)
                             )[:spec.WS_CLIENT_ID_MAX],
            outcome=(spec.WS_OUTCOME_YES if a["leg"] == "YES"
                     else spec.WS_OUTCOME_NO))
        rendered = P.sanitized_ws_frame(
            params, request_id="<uuid4 hex, per request>")
        out.append({"action": a["action"], "leg": a["leg"],
                    "frame": rendered["frame"]})
    return out


def stale_orders(resting: List[Mapping[str, Any]], now: float,
                 surface_prices: Mapping[str, str]) -> List[Dict[str, Any]]:
    """Which resting quotes must be pulled, and why.

    A quote is stale when the surface has moved off its price AND it has
    rested at least minimum_rest_time. The rest floor comes from the lane
    set: cancelling sooner would churn queue position, which is the one thing
    a passive surface cannot afford to keep doing.
    """
    out = []
    for o in resting:
        leg = str(o.get("leg") or "")
        rested = float(now) - float(o.get("resting_since") or now)
        want = surface_prices.get(leg)
        if want is not None and str(o.get("price")) == str(want):
            continue
        if rested < STALE_ORDER_S:
            out.append({"order": o, "cancel": False,
                        "why": "off-surface but only %.1fs rested; the lane's "
                               "minimum_rest_time is %.1fs"
                               % (rested, STALE_ORDER_S)})
            continue
        out.append({"order": o, "cancel": True,
                    "why": "surface moved to %s and it has rested %.1fs"
                           % (want, rested)})
    return out


def crossed(book: Mapping[str, Any]) -> bool:
    """Is the published book CROSSED -- a bid at or above its own ask?

    FOUND BY THE FIRST CONTINUOUS SHADOW SOAK, 2026-09-19. Two of 86 cycles
    on GEMI-BTC05M2609190305-UP published YES 0.95 bid against a 0.65 ask: a
    bid thirty cents ABOVE the ask, with a passive pair cost of 1.30 for a
    pair worth 1.00. The frozen controller quoted into it -- OPEN YES @ 0.95
    and OPEN NO @ 0.35 -- because nothing refused the book.

    THE EXISTING CHECK CANNOT CATCH THIS. `identity_consistent` verifies
    no_bid == 1 - yes_ask and no_ask == 1 - yes_bid, and those hold EXACTLY
    on a crossed book: crossing both sides preserves the complement identity
    by construction. All 2 crossed cycles reported identity_consistent True.

    THE MECHANISM, established rather than guessed: both crossed cycles were
    exactly the two cycles whose ASK LADDER WAS EMPTY. With no resting ask
    the contract object still publishes an ask price, and that number is not
    describing anything -- so the derived NO bid is not either.

    This is a READABILITY gate, the same kind already applied to an
    identity-inconsistent book, and not a pair-cost rule: it says nothing
    about what a pair may cost, only that a book we cannot read is not one to
    quote into. The owner's decision that NO pair-cost ceiling exists on this
    path is untouched.
    """
    try:
        for side in ("yes", "no"):
            s_ = book.get(side) or {}
            bid, ask = s_.get("bid"), s_.get("ask")
            if bid in (None, "") or ask in (None, ""):
                return True
            if D(str(bid)) >= D(str(ask)):
                return True
    except (ArithmeticError, TypeError, ValueError):
        return True
    return False


def state_from_venue(symbol: str, tte_s: float, inv: "Inventory", *,
                     now: Optional[float] = None,
                     book: Optional[Mapping[str, Any]] = None
                     ) -> Dict[str, Any]:
    """Market state for the engine, built from the venue's OWN per-outcome
    book rather than from the complement identity.

    The identity happens to hold exactly on this venue, but deriving the NO
    side from the YES side would put an inferred number where an observed one
    is published -- the substitution this whole program refuses. An
    inconsistent or unreadable book yields inventory_known=False, which STOPS
    the probe rather than quoting into something we cannot read.
    """
    from src.gemini_execution import metadata as MD
    import time as _t
    b = book if book is not None else MD.outcome_book(symbol)
    if not b.get("readable") or not b.get("identity_consistent") \
            or crossed(b):
        return {"market_key": symbol, "state_id": "unreadable",
                "t_utc": now if now is not None else _t.time(),
                "tte_s": tte_s, "cohort": "BTC5m",
                "inventory_known": False, "outcomes": {},
                "unreadable_why": (
                    "book not readable" if not b.get("readable")
                    else ("complement identity violated"
                          if not b.get("identity_consistent")
                          else "CROSSED book: a bid at or above its own ask")),
                "book": b}
    tick = (MD.terms_for(symbol) or {}).get("priceIncrement") or "0.01"
    t = now if now is not None else _t.time()
    return {
        "market_key": symbol, "state_id": "%s@%d" % (symbol, int(t)),
        "t_utc": t, "state_age_s": 0.0, "tte_s": tte_s, "cohort": "BTC5m",
        "inventory_known": True,
        "outcomes": {
            "Up": {"bid": b["yes"]["bid"], "ask": b["yes"]["ask"],
                   "tick": tick},
            "Down": {"bid": b["no"]["bid"], "ask": b["no"]["ask"],
                     "tick": tick}},
        "book": b,
    }


# ------------------------------------------------- one window, and then stop

PROBE_RUNNING, PROBE_STOPPED = "RUNNING", "STOPPED"


class WindowGuard:
    """Binds the probe to ONE BTC5m contract and makes a second impossible.

    WHY THIS EXISTS AS AN OBJECT. Until it did, "stops after exactly one
    contract" and "cannot roll residual into the next window" were properties
    of the ENVELOPE DESCRIPTION and of nothing that runs. `plan()` took an
    `actions_taken` count from a caller that did not exist. A description is
    not a control.

    It refuses rather than adapts: a different symbol, an exhausted action
    budget or a withdrawn window all move it to STOPPED, and STOPPED is
    terminal. Nothing here re-arms, re-authorizes or selects a next contract.
    """

    def __init__(self, symbol: str, *, auth_id: str) -> None:
        if not str(symbol).startswith(COHORT_PREFIX):
            raise ProbeStop("%s is not a %s contract" % (symbol, COHORT_PREFIX))
        self.symbol = str(symbol)
        self.auth_id = str(auth_id)
        self.state = PROBE_RUNNING
        self.actions_taken = 0
        self.stopped_because: Optional[str] = None
        self.log: List[Dict[str, Any]] = []

    def stop(self, why: str) -> None:
        if self.state != PROBE_STOPPED:
            self.state = PROBE_STOPPED
            self.stopped_because = why
            self.log.append({"event": "STOP", "why": why})

    def check_symbol(self, symbol: str) -> None:
        """A different contract is the ONE thing that must never be quietly
        accepted: it is how a one-window probe becomes a continuous one."""
        if str(symbol) != self.symbol:
            self.stop("symbol changed from %s to %s; this authorization is "
                      "for ONE contract and does not roll forward"
                      % (self.symbol, symbol))
            raise ProbeStop(self.stopped_because or "symbol changed")

    def record(self, action: Mapping[str, Any]) -> None:
        self.actions_taken += 1
        self.log.append({"event": DECIDED, "action": dict(action)})
        if self.actions_taken >= MAX_ACTIONS:
            self.stop("max_actions %d reached" % MAX_ACTIONS)

    def step(self, state: Mapping[str, Any], inv: Inventory,
             **kw: Any) -> Dict[str, Any]:
        """One turn, with the window binding enforced around it."""
        if self.state == PROBE_STOPPED:
            return {"stop": self.stopped_because or "stopped", "actions": [],
                    "holds": [], "surface": None, "probe_state": self.state}
        self.check_symbol(state.get("market_key") or "")
        out = plan(state, inv, actions_taken=self.actions_taken, **kw)
        if out.get("window_phase") == WINDOW_WITHDRAWN:
            self.stop("window withdrawn at tte %s; one contract only, no "
                      "continuation into the next" % state.get("tte_s"))
        if out.get("stop"):
            self.stop(out["stop"])
        out["probe_state"] = self.state
        out["actions_taken"] = self.actions_taken
        return out


def recover(auth_id: str, symbol: str, venue_fills: Optional[List[Mapping]],
            ) -> Tuple[Optional[Inventory], Optional[str]]:
    """Rebuild inventory from AUTHORITATIVE fills after a restart.

    A restart must never look flat. `venue_fills is None` means the read did
    not succeed, which is UNREADABLE, not empty -- and unreadable stops the
    probe. An empty LIST is a real answer: the venue says there were no fills.
    """
    if venue_fills is None:
        return None, ("venue fills unreadable on restart; inventory cannot be "
                      "established and a restart never assumes flat")
    inv = Inventory()
    for f in venue_fills:
        leg = str(f.get("leg") or
                  ("YES" if str(f.get("outcome", "")).lower() == "yes"
                   else "NO"))
        inv.apply_fill(leg, f.get("qty") or f.get("filledQuantity"),
                       f.get("price"), source="venue_restart")
    return inv, None


# --------------------------------------------------------- settlement

def settlement_report(inv: Inventory, resolution: Mapping[str, Any],
                      *, fees: Any = None,
                      rebates_confirmed: Any = None) -> Dict[str, Any]:
    """Matched-pair economics and directional-residual P&L, kept APART.

    The residual is labelled DIRECTIONAL_EXPOSURE and never folded into the
    matched leg. Nothing is REALIZED here: a settled contract states what it
    resolved to, and only a venue-confirmed cash movement makes it realized.
    """
    side = resolution.get("resolutionSide")
    settled = resolution.get("verdict") == "SETTLED"
    matched = inv.matched()
    pair = inv.effective_matched_pair_cost()
    res = inv.residual()

    matched_econ = None
    if matched > 0 and pair is not None:
        # a matched pair redeems at exactly 1.00 per unit whichever side wins
        matched_econ = {
            "matched_qty": str(matched),
            "effective_pair_cost": str(pair),
            "gross_per_unit_vs_par": str(D("1") - pair),
            "gross_usd": str((D("1") - pair) * matched),
            "status": ("SETTLED_NOT_YET_CASH_CONFIRMED" if settled
                       else "PENDING_SETTLEMENT"),
            "labels": ("a cost basis against par. NOT realized until the "
                       "venue's own cash movement is read back."),
        }

    residual_econ = {
        "directional_residual": str(res),
        "leg": (None if res == 0 else ("YES" if res > 0 else "NO")),
        "label": "DIRECTIONAL_EXPOSURE",
        "resolution_side": side,
        "outcome_known": settled,
        "labels": (
            "carried directional exposure, NEVER matched economics. The "
            "historical measurement of this leg is negative out of sample "
            "(+$1,631 TRAIN, -$12,971 OOS), so it is reported on its own and "
            "never netted into the matched result."),
    }
    if settled and res != 0 and side in ("yes", "no"):
        won = (res > 0 and side == "yes") or (res < 0 and side == "no")
        qty = abs(res)
        basis = (inv.avg_yes() if res > 0 else inv.avg_no()) or D("0")
        residual_econ["redeems_per_unit"] = "1" if won else "0"
        residual_econ["cost_basis_per_unit"] = str(basis)
        residual_econ["gross_usd"] = str(((D("1") if won else D("0")) - basis)
                                         * qty)
        residual_econ["status"] = "SETTLED_NOT_YET_CASH_CONFIRMED"

    return {
        "label": "%s/settlement" % LABEL,
        "contract": resolution.get("instrumentSymbol"),
        "settlement": {k: resolution.get(k) for k in
                       ("verdict", "status", "resolutionSide", "resolvedAt",
                        "settlementValue")},
        "inventory": inv.report(),
        "matched_pair_economics": matched_econ,
        "directional_residual_pnl": residual_econ,
        "fees": (None if fees is None else str(fees)),
        "rebates_rewards": (None if rebates_confirmed is None
                            else str(rebates_confirmed)),
        "settlement_cashflow": None,
        "realized": None,
        "accounting_labels": (
            "matched-pair economics, directional-residual P&L, fees, rebates "
            "and settlement cashflow are SEPARATE lines and are never summed "
            "into one number here. realized stays null until a venue-confirmed "
            "cash movement is read; a cost basis, a resolution side and a "
            "mark are none of them realized."),
    }
