"""H-TURN, implemented as a MECHANISM rather than a classifier. SHADOW ONLY.

WHAT THIS IS
------------
`analysis/l5/complete_vs_extend/FORENSIC.md` §3/§5 ends on a mechanism, not a
feature: L5's `complete` is an ARRIVAL. He runs a persistent same-side bid that
fills as the market comes to him -- that is the run -- and the turn fires when
the OTHER token's quote crosses his resting complement bid. 94.3% of the
clone's COMPLETE-called-EXTEND error sits in the single cell immediately after
an `extend`, and conditioned on that cell not one of ten fill-history
observables moves the mismatch rate off ~0.9 (standing imbalance is falsified
at AUC 0.488; every threshold override is net-negative in rows).

So this module does not predict the turn. It makes the turn impossible to miss:

    extend/rest one side, SIMULTANEOUSLY maintain a fee-safe complement
    resting on the other token, and let FILLS determine the turn.

Inventory is updated on EVERY PARTIAL FILL. That clause is not decoration:
a complement that is hit for 40 of 120 shares leaves 80 shares of residual,
and an implementation that only books complete fills misstates residual for
the rest of the window.

WHAT THIS IS NOT
----------------
NOT authorization to trade. NOT a new strategy. Nothing here places, signs,
previews, amends or cancels an order, and `assert_mutation_incapable()` is a
BLOCKING GATE, in the sense of `scripts/l5_exec1_boot_preflight.py` §2: it
FAILS if `src.l5_pmus_transport`, `src.pmus_q1_executor` or
`src.pmus_micro_executor` is importable. Because this analysis package lives in
the full repository -- where those modules are on disk -- the gate is made
true by CONSTRUCTION rather than by a flag: `install_mutation_barrier()` puts a
`sys.meta_path` finder in front of the import system that refuses those names
outright, and the gate then verifies the refusal. Remove the barrier and the
gate fails, which is exactly the behaviour the deliverable asks for and what
`tests/test_l5_hturn_shadow.py` pins. "Cannot" outranks "will not".

Every label is SHADOW. Nothing here is submitted, filled, settled or realized.

ECONOMICS: INHERITED, NOT INVENTED
----------------------------------
* maker-first, post-only. The complement is placed STRICTLY BELOW the
  complement's prevailing best ask, so it can never cross. The only fill event
  the mechanism recognises is the complement's ask coming DOWN to the resting
  bid -- the market arriving, per H-TURN.
* fee-safe pair completion at the frozen spring target, imported from
  `src.l5_shadow.two_sided.ADAPTED_EXECUTABLE_PAIR_CEILING` (0.96) rather than
  re-declared here. Near-$1 pair tolerance belongs to later incentive-era L5
  and stays LOCKED: `PAIR_CEILING_NEAR_PAR_IS_LOCKED` is not a parameter.
* the surplus side's cost basis is CASH, taken from the corpus's own
  `usdcSize` (so the fee L5 was actually charged is already inside it), and
  our own complement leg carries a fee ALLOWANCE equal to the measured
  worst-case per-contract fee on the corpus ($0.018,
  `analysis/l5/POLYMARKET_FEE_SOURCES.json`). A measured bound, not a model.
  The allowance is reported as a sensitivity arm, never tuned.
* rebates and rewards are $0. There is no rebate term in this module at all,
  so no modelled credit can make the mechanism look profitable.
* one complement order outstanding at any time. It is REPRICED when the
  surplus cost basis changes (that is one maintained order, which is the
  mechanism) and never duplicated, never retried after cancellation, and never
  replaced by a second concurrent complement.

PRICES STAY IN THEIR OWN OUTCOME'S TERMS
----------------------------------------
Every price in this module is in its own outcome's terms. Only a WIRE value
maps, and this module does not build wire values itself: `quote_repr()`
delegates to `src.l5_order_body`, the single proven serializer. No duplicate
serializer is written here.
"""
from __future__ import annotations

import bisect
import importlib
import sys
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "SHADOW"
NEVER = ("never submitted", "never filled", "never settled", "never realized")

#: order-capable modules that must NOT be importable from this process
FORBIDDEN_MODULES = ("src.l5_pmus_transport", "src.pmus_q1_executor",
                     "src.pmus_micro_executor")

TICK = D("0.01")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")

#: measured worst-case per-contract fee wedge on the L5 corpus. A BOUND read
#: off POLYMARKET_FEE_SOURCES.json (`fee_per_contract_fee_bearing.max`), used
#: as the allowance for OUR complement leg. Not an estimate of our fee.
FEE_ALLOWANCE_PER_SHARE = D("0.018")

PAIR_CEILING_NEAR_PAR_IS_LOCKED = True

# ---------------------------------------------------------------- unevaluable
#: an absent complement quote is UNEVALUABLE, never a default. A row the
#: mechanism could not have been run on is not a row where it did nothing.
U_NO_BOOK = "UNEVALUABLE_NO_COMPLEMENT_BOOK"
U_STALE = "UNEVALUABLE_COMPLEMENT_BOOK_BEYOND_CARRY"
U_NO_ASK = "UNEVALUABLE_COMPLEMENT_HAS_NO_ASK"
U_NOT_POSTABLE = "UNEVALUABLE_NO_FEE_SAFE_POSTABLE_COMPLEMENT_PRICE"
U_MIRROR = "UNEVALUABLE_MIRRORED_SURPLUS_FILL_NOT_POST_ONLY_ACHIEVABLE"
UNEVALUABLE_LABELS = (U_NO_BOOK, U_STALE, U_NO_ASK, U_NOT_POSTABLE, U_MIRROR)

EVALUABLE = "EVALUABLE"


class MutationBarrier:
    """A `sys.meta_path` finder that refuses the order-capable modules.

    Structural, not a flag: after `install_mutation_barrier()` an `import
    src.pmus_q1_executor` raises ImportError inside this process whatever is
    on disk, so the signed-POST path is not reachable from this entrypoint at
    all. `assert_mutation_incapable()` then VERIFIES that, and fails if any of
    them turns out to be importable.
    """

    def __init__(self, names: Sequence[str] = FORBIDDEN_MODULES) -> None:
        self.names = tuple(names)

    def _blocked(self, fullname: str) -> bool:
        return any(fullname == n or fullname.startswith(n + ".")
                   for n in self.names)

    def find_module(self, fullname: str, path: Any = None) -> None:   # py2 API
        self.find_spec(fullname, path)
        return None

    def find_spec(self, fullname: str, path: Any = None,
                  target: Any = None) -> None:
        if self._blocked(fullname):
            raise ImportError(
                "SHADOW MUTATION BARRIER: %s is order-capable and is refused "
                "by analysis.l5.hturn_shadow.mechanism. This shadow is "
                "mutation-incapable by construction." % fullname)
        return None


def install_mutation_barrier(names: Sequence[str] = FORBIDDEN_MODULES
                             ) -> MutationBarrier:
    """Install the barrier and evict anything forbidden already imported."""
    for mod in list(sys.modules):
        if any(mod == n or mod.startswith(n + ".") for n in names):
            del sys.modules[mod]
    for f in sys.meta_path:
        if isinstance(f, MutationBarrier) and tuple(f.names) == tuple(names):
            return f
    bar = MutationBarrier(names)
    sys.meta_path.insert(0, bar)
    return bar


def remove_mutation_barrier() -> int:
    """Only tests use this -- to prove the gate FAILS without the barrier."""
    n = 0
    for f in list(sys.meta_path):
        if isinstance(f, MutationBarrier):
            sys.meta_path.remove(f)
            n += 1
    return n


def mutation_incapable_gate() -> Dict[str, Any]:
    """The blocking gate. PASS only when no order-capable module imports.

    Shaped after `scripts/l5_exec1_boot_preflight.py :: gate
    "mutation_incapable"`. A gate that cannot be EVALUATED fails; unknown is
    never a pass.
    """
    importable: List[str] = []
    for mod in FORBIDDEN_MODULES:
        try:
            importlib.import_module(mod)
            importable.append(mod)
        except BaseException:                                  # noqa: BLE001
            pass
    return {"gate": "mutation_incapable", "blocking": True,
            "pass": not importable,
            "importable_order_capable_modules": importable,
            "detail": (importable or
                       "no order-capable module is importable from this "
                       "process"),
            "label": LABEL}


def assert_mutation_incapable() -> Dict[str, Any]:
    g = mutation_incapable_gate()
    if not g["pass"]:
        raise PermissionError(
            "BLOCKING GATE mutation_incapable FAILED: %s importable. The "
            "shadow refuses to run in a process that can place an order."
            % (", ".join(g["importable_order_capable_modules"]),))
    return g


def pair_ceiling() -> D:
    """The frozen spring pair target, imported and NOT re-declared."""
    from src.l5_shadow.two_sided import ADAPTED_EXECUTABLE_PAIR_CEILING
    return D(str(ADAPTED_EXECUTABLE_PAIR_CEILING))


def quote_repr(*, slug: str, outcome: str, price: D, qty: D) -> Dict[str, Any]:
    """How this quote WOULD be expressed, via the one proven serializer.

    Called only to record a representation on a shadow row. It builds a dict;
    it has no transport, no credentials and no endpoint, and `src.l5_order_body`
    is deliberately the module with none of those either. NOTHING in this
    package hands the result to anything.
    """
    from src import l5_order_body as OB
    side = {"Up": "UP", "Down": "DOWN"}[outcome]
    body = OB.build(slug=slug, side=side, price=price, qty=qty,
                    post_only=True)
    return {"SHADOW_ONLY_NEVER_SENT": True,
            "outcome_terms_price": str(price), "outcome": outcome,
            "wire": body}


def floor_tick(x: D) -> D:
    return D(x).quantize(TICK, rounding=ROUND_FLOOR)


# ------------------------------------------------------------------ inventory
class Inventory:
    """Per-outcome FIFO lots in CASH terms, updated on EVERY fill -- partial
    fills included, because a partial is simply a fill of its own size.

    `residual_signed` is Up minus Down, the corpus's own convention
    (`src/polymarket_l5/action_model.py :: _relation`). `matched` is the
    paired depth, `unmatched_avg_cost` the cash basis of the shares that are
    still naked -- which is what a fee-safe complement price is computed from.
    """

    def __init__(self) -> None:
        # each lot: [qty_remaining, cash_per_share]
        self.lots: Dict[str, List[List[D]]] = {"Up": [], "Down": []}
        self.filled_qty: Dict[str, D] = {"Up": D(0), "Down": D(0)}
        self.cash: Dict[str, D] = {"Up": D(0), "Down": D(0)}
        self.matched_qty: D = D(0)
        self.matched_cash: D = D(0)
        self.fills: int = 0

    # ---- reads
    def qty(self, outcome: str) -> D:
        return sum((l[0] for l in self.lots[outcome]), D(0))

    @property
    def residual_signed(self) -> D:
        return self.qty("Up") - self.qty("Down")

    @property
    def surplus_outcome(self) -> Optional[str]:
        r = self.residual_signed
        if r > 0:
            return "Up"
        if r < 0:
            return "Down"
        return None

    @property
    def deficit_outcome(self) -> Optional[str]:
        s = self.surplus_outcome
        return None if s is None else flip(s)

    def unmatched_avg_cost(self, outcome: str) -> Optional[D]:
        q = self.qty(outcome)
        if q <= 0:
            return None
        tot = sum((l[0] * l[1] for l in self.lots[outcome]), D(0))
        return (tot / q).quantize(D("0.000001"))

    # ---- the only mutator
    def apply_fill(self, outcome: str, qty: Any, cash_per_share: Any
                   ) -> Dict[str, Any]:
        """Book ONE fill of ANY size -- a partial is a fill of its own size.

        Returns the delta, so a caller can reconcile share-for-share.
        """
        q = D(str(qty))
        if q <= 0:
            raise ValueError("non-positive fill qty %r" % (qty,))
        c = D(str(cash_per_share))
        other = flip(outcome)
        self.fills += 1
        self.filled_qty[outcome] += q
        self.cash[outcome] += q * c
        before = self.residual_signed
        # match against the opposite side's open lots first, FIFO
        take = q
        matched_now = D(0)
        while take > 0 and self.lots[other]:
            lot = self.lots[other][0]
            use = min(take, lot[0])
            lot[0] -= use
            take -= use
            matched_now += use
            self.matched_qty += use
            self.matched_cash += use * (c + lot[1])
            if lot[0] <= 0:
                self.lots[other].pop(0)
        if take > 0:
            self.lots[outcome].append([take, c])
        return {"outcome": outcome, "qty": str(q), "cash_per_share": str(c),
                "matched_now": str(matched_now),
                "residual_before": str(before),
                "residual_after": str(self.residual_signed),
                "label": LABEL}

    def avg_pair_cost(self) -> Optional[D]:
        if self.matched_qty <= 0:
            return None
        return (self.matched_cash / self.matched_qty).quantize(D("0.000001"))

    def state(self) -> Dict[str, Any]:
        return {"up_qty": str(self.qty("Up")), "down_qty": str(self.qty("Down")),
                "residual_signed": str(self.residual_signed),
                "matched_qty": str(self.matched_qty),
                "avg_pair_cost": (None if self.avg_pair_cost() is None
                                  else str(self.avg_pair_cost())),
                "fills_booked": self.fills}


def flip(outcome: str) -> str:
    if outcome == "Up":
        return "Down"
    if outcome == "Down":
        return "Up"
    raise ValueError("unknown outcome %r" % (outcome,))


# -------------------------------------------------- the resting complement
class RestingComplement:
    """The ONE complement order the mechanism maintains, post-only.

    `price` is in the complement outcome's own terms. It is only ever placed
    STRICTLY BELOW the complement's best ask, so it cannot cross; the fill
    event is the ask coming down to it.
    """

    def __init__(self, outcome: str, price: D, qty: D, placed_t: float) -> None:
        self.outcome = outcome
        self.price = D(price)
        self.qty_open = D(qty)
        self.placed_t = float(placed_t)
        self.reprices = 0
        self.partial_fills: List[Dict[str, Any]] = []

    def reprice(self, price: D, qty: D, t: float) -> None:
        self.price = D(price)
        self.qty_open = D(qty)
        self.placed_t = float(t)
        self.reprices += 1

    def take(self, qty: D, t: float) -> D:
        got = min(self.qty_open, D(qty))
        if got <= 0:
            return D(0)
        self.qty_open -= got
        self.partial_fills.append({"t": t, "qty": str(got),
                                   "price": str(self.price),
                                   "remaining_after": str(self.qty_open),
                                   "label": LABEL})
        return got


def fee_safe_complement_price(surplus_avg_cash_cost: Optional[D],
                              complement_best_ask: Optional[Any],
                              *, ceiling: Optional[D] = None,
                              fee_allowance: D = FEE_ALLOWANCE_PER_SHARE
                              ) -> Dict[str, Any]:
    """The price the complement may rest at, or WHY there is none.

    pair_cost = surplus cash basis + our complement price, and the gate is

        surplus_basis + price + fee_allowance <= ceiling (0.96, frozen)

    then clamped STRICTLY BELOW the complement's best ask so the order is
    post-only by construction. If no such price is >= one tick there is NO
    fee-safe postable complement and the row is UNEVALUABLE -- it is not a
    row where the mechanism chose to wait, and it is never widened toward par.
    """
    ceil_ = pair_ceiling() if ceiling is None else D(str(ceiling))
    if surplus_avg_cash_cost is None:
        return {"status": U_NOT_POSTABLE, "why": "no surplus inventory"}
    budget = D(str(ceil_)) - D(str(surplus_avg_cash_cost)) - D(str(fee_allowance))
    q_target = floor_tick(budget)
    if complement_best_ask is None:
        return {"status": U_NO_ASK, "fee_safe_target": str(q_target),
                "why": "complement has no ask in this snapshot; an absent "
                       "complement quote is UNEVALUABLE, not a default"}
    ask = D(str(complement_best_ask))
    q = min(q_target, floor_tick(ask - TICK))
    if q < MIN_PRICE or q > MAX_PRICE:
        return {"status": U_NOT_POSTABLE, "fee_safe_target": str(q_target),
                "complement_best_ask": str(ask), "would_be_price": str(q),
                "why": ("no price at or above one tick keeps the pair at or "
                        "under the frozen ceiling %s; near-par tolerance is "
                        "LOCKED" % ceil_)}
    pair = D(str(surplus_avg_cash_cost)) + q
    return {"status": EVALUABLE, "price": str(q),
            "fee_safe_target": str(q_target),
            "complement_best_ask": str(ask),
            "pair_cost_if_filled": str(pair.quantize(D("0.000001"))),
            "pair_cost_with_fee_allowance": str(
                (pair + fee_allowance).quantize(D("0.000001"))),
            "ceiling": str(ceil_), "crosses": False, "post_only": True}


def crosses(outcome_price: Any, best_ask: Any) -> bool:
    """A buy at `price` crosses iff it is at or above the prevailing ask."""
    if best_ask is None:
        return False
    return D(str(outcome_price)) >= D(str(best_ask))


def ask_liquidity_at_or_below(snap: Optional[Dict[str, Any]], price: D) -> D:
    """Offered size a resting bid at `price` would be hit by, this snapshot.

    This is the mechanism's FILL MODEL and it is stated, not hidden: a resting
    bid is credited with the size offered at or below its own price in the
    complement's visible ladder. The depth is ANONYMOUS -- it is size at a
    price and is never asserted to be anyone's order, including L5's.
    """
    if not snap:
        return D(0)
    tot = D(0)
    for p, s in zip(snap.get("ask_prices") or [], snap.get("ask_sizes") or []):
        if D(str(p)) <= D(str(price)):
            tot += D(str(s))
    return tot
