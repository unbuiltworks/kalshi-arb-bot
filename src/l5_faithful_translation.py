"""The faithful L5 engine: L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY.

    "Given each actual L5 decision, how closely can we reproduce L5's
     resulting sequential inventory path on PMUS using the market actually
     available to us at that instant?"

THREE LAYERS, AND EVERY MISS BELONGS TO EXACTLY ONE OF THEM.

  L5_NATIVE          what L5 actually chose. AUTHORITATIVE. It is read from
                     his executed trades and is never filtered, re-priced or
                     re-sized by anything of ours. It refuses nothing; the
                     only thing it can report is that the source row was
                     unreadable.
  PMUS_TRANSLATION   whether and how PMUS could execute that decision, using
                     ONLY what was observable at our receipt timestamp.
  SAFETY_OVERLAY     whether OUR hard risk controls would permit the order.

A SAFETY_OVERLAY refusal is a fact about our risk appetite. It is never
recorded as L5 behaviour and never as a PMUS inability. A PMUS_TRANSLATION
refusal is a fact about the venue or the habitat, and no change to our own
execution removes it.

=== THE TRANSLATION RULE IS PARAMETER-FREE, BY CONSTRUCTION ===

It has no threshold, no band, no ceiling and no discretionary branch, so
there is nothing in it that could be tuned against the five windows we
happen to hold. It is stated prospectively and applies unchanged to every
future window:

    reproduce every L5 action by the CLOSEST EXECUTABLE MEANS at our receipt
    instant, and MEASURE the cost of doing so rather than capping it.

  * If L5's price can rest without crossing, the closest executable means is
    a MAKER order AT L5'S OWN PRICE. Zero slippage by construction. It
    acquires only on authenticated STRICT evidence, because a resting order
    that nothing traded through did not fill -- that is an evidence
    standard, not a behavioural rule.
  * Otherwise the action is marketable, and the closest executable means is
    a TAKER order at the contemporaneous touch: UP at `ask`, DOWN at
    `1 - bid`, because buying NO at p is selling YES at 1-p. It fills
    immediately, capped at the DISPLAYED depth, and pays the taker fee.
    Slippage against L5's price is recorded, never limited.

Maker versus taker is therefore an EXECUTION TRANSLATION, chosen by what the
book permits, and never a behavioural rule. Nothing is backdated and no later
or more favourable price is ever consulted.

SIZING preserves L5's relative geometry: every action is `l5_size * lambda`
with ONE global lambda (see `l5_execution_translations.lambda_ref`), computed
from L5's sizes alone -- no price, fill, outcome or P&L enters it.

=== WHAT THIS ENGINE DELIBERATELY DOES NOT DO ===

It does not chase behavioural agreement. An earlier counterfactual
(proportional + crossing) crossed zero in 3 of 5 windows where L5_NATIVE
crossed in 1, and the temptation is to add a rule that suppresses the extra
crossings. That would be fitting our translation to five windows we have
already seen. The rule stays parameter-free and the disagreement is
REPORTED, in both directions: reproducing too much is as much a fidelity
failure as reproducing too little.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees
from src import l5_execution_translations as X

LAYERS = ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY")

#: PMUS_TRANSLATION refusals -- venue and habitat facts, not our choices
T_NO_BOOK = "NO_BOOK"
T_NO_DEPTH = "NO_DISPLAYED_DEPTH"
T_NO_INTERACTION = "NO_SUBSEQUENT_INTERACTION"
T_QUEUE_AMBIGUOUS = "QUEUE_AMBIGUOUS"
T_STALE = "STALE_OBSERVATION"
T_UNEVALUABLE = "STRICT_NOT_EVALUABLE"
TRANSLATION_REFUSALS = (T_NO_BOOK, T_NO_DEPTH, T_NO_INTERACTION,
                        T_QUEUE_AMBIGUOUS, T_STALE, T_UNEVALUABLE)

#: Inherited from the collector, not chosen here: the observer's own PMUS
#: snapshot cache has a 2.0s TTL, so anything older is a collection defect
#: rather than a market fact.
STALE_MAX_S = 2.0

MAKER, TAKER = "MAKER", "TAKER"


class SafetyLimits:
    """OUR hard risk controls. Explicit, and never folded into fidelity.

    These bound a future live probe. They are applied as a SEPARATE LAYER so
    that a refusal here is attributable to us and can never be mistaken for
    something L5 would not have done or something PMUS could not have done.
    `enabled=False` measures the venue alone.
    """

    def __init__(self, *, enabled: bool = True,
                 band: Optional[tuple] = (D("0.20"), D("0.80")),
                 max_committed_usd: Optional[D] = D("25"),
                 max_abs_residual: Optional[D] = D("25")) -> None:
        self.enabled = enabled
        self.band = band
        self.max_committed_usd = max_committed_usd
        self.max_abs_residual = max_abs_residual

    def refuse(self, price: D, qty: D, committed: D,
               residual_after: D) -> Optional[str]:
        if not self.enabled:
            return None
        if self.band is not None and not (self.band[0] <= price <= self.band[1]):
            return "BAND"
        if (self.max_committed_usd is not None
                and committed + price * qty > self.max_committed_usd):
            return "MAX_COMMITTED_USD"
        if (self.max_abs_residual is not None
                and abs(residual_after) > self.max_abs_residual):
            return "MAX_ABS_RESIDUAL"
        return None


def l5_native(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """L5's own decision, unfiltered. None only if the row is unreadable."""
    l5 = row.get("l5") or {}
    side = l5.get("pmus_side")
    px = X._d((row.get("copyability") or {}).get("l5_price")
              or l5.get("l5_price"))
    size = X._d(l5.get("l5_size"))
    if side is None or px is None or size is None or size <= 0:
        return None
    return {"side": side, "price": px, "l5_size": size,
            "relation": l5.get("relation_actual")}


def _translate_core(decision: Mapping[str, Any], *,
                    bbo: Mapping[str, Any],
                    snapshot_age_s: Any,
                    maker_class: Any,
                    want: D) -> Dict[str, Any]:
    """THE ONE implementation of the translation. Both callers share it.

    WHY IT EXISTS. `pmus_translate` read the book, the staleness age and the
    maker-class key out of an L5 ACTIVITY ROW, so an autonomous decision --
    which has no row and must never be given a fabricated one -- could not
    reach the three gates below. Extracting the body is what lets the
    row-free caller use the SAME gates instead of a second copy of them:
    a duplicated staleness bound is a bound that can drift from the one
    actually enforced.

    THE GATES ARE UNCHANGED AND IN THE SAME ORDER: staleness before book,
    book before mode, and the maker-class check only on the restable
    branch. `tests/test_l5_controller_translate_parity.py` runs this against
    the pre-refactor implementation on identical inputs and asserts the
    outputs are equal, field for field.

    The only difference between the two callers is WHERE the three inputs
    come from -- a row, or an authenticated book handed in directly.
    """
    bid, ask = X._d(bbo.get("bid")), X._d(bbo.get("ask"))
    age = snapshot_age_s
    side, l5px = decision["side"], decision["price"]

    if age is not None and float(age) > STALE_MAX_S:
        return {"ok": False, "reason": T_STALE, "snapshot_age_s": age}
    if bid is None or ask is None:
        return {"ok": False, "reason": T_NO_BOOK}

    our_touch = ask if side == "UP" else (D(1) - bid)
    restable = l5px < our_touch                # strictly inside: a true maker

    if restable:
        klass = maker_class
        if klass is None:
            return {"ok": False, "reason": T_UNEVALUABLE, "mode": MAKER}
        if klass not in X.STRICT_CLASSES:
            return {"ok": False, "mode": MAKER,
                    "reason": (T_NO_INTERACTION if klass == "NO_INTERACTION"
                               else T_QUEUE_AMBIGUOUS),
                    "observed_class": klass}
        return {"ok": True, "mode": MAKER, "price": l5px, "qty": want,
                "slippage": D(0), "depth": None,
                "fee": D(0), "rebate": fees.maker_rebate(l5px, want),
                "observed_class": klass}

    depth = X.displayed_size(side, bbo)
    if depth is None or depth <= 0:
        return {"ok": False, "reason": T_NO_DEPTH, "mode": TAKER,
                "price": our_touch}
    qty = min(want, depth)
    return {"ok": True, "mode": TAKER, "price": our_touch, "qty": qty,
            "slippage": our_touch - l5px, "depth": depth,
            "depth_capped": qty < want,
            "fee": fees.taker_fee(our_touch, qty), "rebate": D(0)}


def pmus_translate(decision: Mapping[str, Any], row: Mapping[str, Any],
                   authenticated: Mapping[str, Any], want: D
                   ) -> Dict[str, Any]:
    """The closest executable PMUS translation at our receipt instant.

    UNCHANGED BEHAVIOUR. This now resolves the three inputs out of the L5
    row exactly as it always did and hands them to `_translate_core`; the
    gates, their order and every returned field are the same. The
    maker-class lookup is resolved here rather than inside the restable
    branch, which cannot change an output because `Mapping.get` is pure.
    """
    bbo = ((row.get("pmus") or {}).get("bbo")) or {}
    got = authenticated.get(row.get("action_key"))
    klass = (got or {}).get("klass") if isinstance(got, Mapping) else got
    return _translate_core(decision, bbo=bbo,
                           snapshot_age_s=row.get("pmus_snapshot_age_s"),
                           maker_class=klass, want=want)


def controller_translate(decision: Mapping[str, Any], *,
                         bbo: Mapping[str, Any],
                         snapshot_age_s: Any,
                         maker_class: Any,
                         want: D) -> Dict[str, Any]:
    """The row-free sibling, for an AUTONOMOUS CONTROLLER decision.

    NO ROW, AND NONE IS INVENTED. The caller hands in the authenticated
    book, that book's own age, and the maker-class verdict for the rung it
    intends to rest on. Nothing here reads or needs an `action_key`, a
    `pmus` block or an L5 activity record, so an autonomous order can never
    be filed under L5's identity in the tape, the ledger or the provenance
    chain.

    IT IS NOT A SECOND SET OF RULES. It shares `_translate_core` with
    `pmus_translate`, so the 2.0 s staleness bound, the book-availability
    check, the strict maker-class set and both execution modes are the
    SAME code for both provenances. A parity test pins that.

    The caller is responsible for supplying a maker class it actually
    observed; `None` lands on `T_UNEVALUABLE` exactly as it does for an L5
    decision whose rung was never evaluated.
    """
    return _translate_core(decision, bbo=bbo, snapshot_age_s=snapshot_age_s,
                           maker_class=maker_class, want=want)


def run_window(rows: Sequence[Mapping[str, Any]],
               authenticated: Mapping[str, Any], *, lam: D,
               limits: SafetyLimits) -> Dict[str, Any]:
    """Walk one window's L5 actions through all three layers, in order."""
    acts = sorted(rows, key=lambda r: r["l5_event_utc"])
    refused = collections.Counter()
    by_layer = collections.Counter()
    per_action: List[Dict[str, Any]] = []

    up = D(0)
    dn = D(0)
    committed = D(0)
    cost_up = D(0)
    cost_dn = D(0)
    qty_up = D(0)
    qty_dn = D(0)
    fee_tot = D(0)
    reb_tot = D(0)
    slip_tot = D(0)
    mode_mix = collections.Counter()

    native_sides: List[str] = []
    native_imb: List[float] = []
    n_up = D(0)
    n_dn = D(0)
    track_sides: List[str] = []
    track_imb: List[float] = []
    matched_path: List[float] = []
    residual_path: List[float] = []
    pair_path: List[float] = []

    for i, row in enumerate(acts):
        dec = l5_native(row)
        if dec is None:
            by_layer["L5_NATIVE"] += 1
            refused["L5_NATIVE:UNREADABLE_ROW"] += 1
            per_action.append({"i": i, "layer_refused": "L5_NATIVE",
                               "reason": "UNREADABLE_ROW"})
            continue

        # L5_NATIVE path -- what he did, at his own relative sizes
        native_sides.append(dec["side"])
        if dec["side"] == "UP":
            n_up += dec["l5_size"] * lam
        else:
            n_dn += dec["l5_size"] * lam
        native_imb.append(float(n_up - n_dn))

        want = dec["l5_size"] * lam
        tr = pmus_translate(dec, row, authenticated, want)
        if not tr.get("ok"):
            by_layer["PMUS_TRANSLATION"] += 1
            refused["PMUS_TRANSLATION:" + tr["reason"]] += 1
            per_action.append({"i": i, "side": dec["side"],
                               "layer_refused": "PMUS_TRANSLATION",
                               "reason": tr["reason"],
                               "mode_attempted": tr.get("mode")})
            continue

        resid_after = (up + tr["qty"] - dn) if dec["side"] == "UP" \
            else (up - (dn + tr["qty"]))
        why = limits.refuse(tr["price"], tr["qty"], committed, resid_after)
        if why:
            by_layer["SAFETY_OVERLAY"] += 1
            refused["SAFETY_OVERLAY:" + why] += 1
            per_action.append({"i": i, "side": dec["side"],
                               "layer_refused": "SAFETY_OVERLAY",
                               "reason": why, "mode": tr["mode"],
                               "price": str(tr["price"]),
                               "note": ("our risk control, NOT L5 behaviour "
                                        "and NOT a PMUS inability")})
            continue

        # executed (SHADOW)
        mode_mix[tr["mode"]] += 1
        fee_tot += tr["fee"]
        reb_tot += tr["rebate"]
        slip_tot += tr["slippage"] * tr["qty"]
        committed += tr["price"] * tr["qty"]
        if dec["side"] == "UP":
            up += tr["qty"]
            cost_up += tr["price"] * tr["qty"]
            qty_up += tr["qty"]
        else:
            dn += tr["qty"]
            cost_dn += tr["price"] * tr["qty"]
            qty_dn += tr["qty"]
        track_sides.append(dec["side"])
        track_imb.append(float(up - dn))
        matched_path.append(float(min(up, dn)))
        residual_path.append(float(up - dn))
        if qty_up > 0 and qty_dn > 0:
            pair_path.append(float(cost_up / qty_up + cost_dn / qty_dn))
        per_action.append({
            "i": i, "side": dec["side"], "mode": tr["mode"],
            "l5_price": str(dec["price"]), "exec_price": str(tr["price"]),
            "slippage": str(tr["slippage"]), "qty": str(tr["qty"]),
            "want": str(want), "depth_capped": tr.get("depth_capped", False),
            "layer_refused": None,
        })

    return {
        "n_actions": len(native_sides), "reproduced": len(track_sides),
        "by_layer": dict(by_layer), "refused": dict(refused),
        "mode_mix": dict(mode_mix),
        "native_sides": native_sides, "track_sides": track_sides,
        "native_imbalance": native_imb, "track_imbalance": track_imb,
        "matched_path": matched_path, "residual_path": residual_path,
        "pair_cost_path": pair_path,
        "up": up, "dn": dn, "cost_up": cost_up, "cost_dn": cost_dn,
        "qty_up": qty_up, "qty_dn": qty_dn,
        "fees": fee_tot, "rebates": reb_tot,
        "slippage_usd": slip_tot, "committed_usd": committed,
        "per_action": per_action,
    }
