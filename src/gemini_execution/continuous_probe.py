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
        actions.append({**base, "action": classify(r.outcome, inv),
                        "stage": DECIDED})
    return {"stop": None, "actions": actions, "holds": holds,
            "surface": got.get("report"),
            "inventory": inv.report(),
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
    if not b.get("readable") or not b.get("identity_consistent"):
        return {"market_key": symbol, "state_id": "unreadable",
                "t_utc": now if now is not None else _t.time(),
                "tte_s": tte_s, "cohort": "BTC5m",
                "inventory_known": False, "outcomes": {},
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
