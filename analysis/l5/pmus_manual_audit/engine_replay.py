"""What our existing sports engine would have done with the owner's orders.

READ-ONLY, OFFLINE. This module sends nothing, holds no credentials and does
not import an executor. It takes the venue's own order rows (from
`scripts/pmus_manual_order_audit.py`) and runs each one past the engine's
ACTUAL admission gates, reading the constants from the engine modules rather
than restating them, so the comparison cannot drift from the code that would
really run.

VERDICTS, one per order, in the engine's own vocabulary:

  SUBMITTED  every gate admits it, at the engine's own size
  RESIZED    admissible only after the engine shrinks it to its cap --
             recorded separately from SUBMITTED because a resize changes the
             economics of the trade the owner actually did
  BLOCKED    at least one gate refuses; every refusing gate is named
  REPRICED   the engine would quote a different price than the owner used
  CANCELLED  the engine would have pulled it before the owner's fill time

NO touch=fill ANYWHERE. Fills, prices, quantities, maker/taker and settlement
all come from the venue's own records. This module never decides that
something filled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

ONE = D("1")


@dataclass(frozen=True)
class Gates:
    """The engine's live admission envelope, read from the engine's modules.

    `SAFETY_SHELL_VERSION` in `pmus_spring_l5_session` states the shell as
    "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s"; each field
    below is the constant that string refers to.
    """
    band_lo: D
    band_hi: D
    min_trades_1h: int
    pair_cap: D
    hard_pair_bound: D
    notional_cap: D
    max_residual_units: int
    max_concurrent: int
    maker_only: bool
    rest_s: float
    fresh_book_ttl_s: float
    max_actions_live: int
    source: Dict[str, str] = field(default_factory=dict)

    @property
    def effective_pair_cap(self) -> D:
        """The tighter of the two always wins -- never the wider."""
        return min(self.pair_cap, self.hard_pair_bound)


def load_gates() -> Gates:
    """Import the engine's own constants. Fails loudly rather than guessing."""
    from src import pmus_micro_executor as micro
    from src import pmus_sports_l5_decision as dec
    from src import pmus_spring_l5_session as spring

    return Gates(
        band_lo=spring.SPRING_BAND_LO,
        band_hi=spring.SPRING_BAND_HI,
        min_trades_1h=int(spring.SPRING_MIN_TRADES_1H),
        pair_cap=dec.PAIR_OBSERVATION_CAP,
        hard_pair_bound=dec.hard_pair_bound(),
        notional_cap=micro.PER_MARKET_FIRST_LEG_CAP,
        max_residual_units=int(dec.MAX_RESIDUAL_UNITS),
        max_concurrent=1,
        maker_only=True,
        rest_s=30.0,
        fresh_book_ttl_s=float(dec.FRESH_BOOK_TTL_S),
        max_actions_live=6,
        source={
            "band": "pmus_spring_l5_session.SPRING_BAND_LO/HI",
            "min_trades_1h": "pmus_spring_l5_session.SPRING_MIN_TRADES_1H",
            "pair_cap": "pmus_sports_l5_decision.PAIR_OBSERVATION_CAP",
            "hard_pair_bound": "pmus_sports_l5_decision.hard_pair_bound()",
            "notional_cap": "pmus_micro_executor.PER_MARKET_FIRST_LEG_CAP",
            "max_residual_units": "pmus_sports_l5_decision.MAX_RESIDUAL_UNITS",
            "fresh_book_ttl_s": "pmus_sports_l5_decision.FRESH_BOOK_TTL_S",
            "shell": "pmus_spring_l5_session.SAFETY_SHELL_VERSION",
        },
    )


def _direction(order: Dict[str, Any]) -> str:
    """LONG / SHORT / UNKNOWN, from the VENUE's intent, via the engine's own
    reader. `l5_venue_accounting.direction` raises rather than guessing LONG,
    because guessing understates a short's settlement liability."""
    from src import l5_venue_accounting as va
    try:
        return va.direction({"intent": order.get("intent"),
                             "side": order.get("side"),
                             "outcomeSide": order.get("outcomeSide")})
    except Exception:                                          # noqa: BLE001
        return "UNKNOWN"


def _own_price(order: Dict[str, Any]) -> Optional[D]:
    """The venue's wire price translated into OUR outcome's own terms.

    `src/l5_order_body.venue_price` maps own -> wire and is its own inverse
    (1 - (1 - x) == x), so it is reused rather than restated: there is one
    definition of this convention in the codebase and this is not a second.
    """
    px = _d(order.get("limit_price"))
    if px is None:
        return None
    from src import l5_order_body as ob
    outcome = str(order.get("outcomeSide") or "")
    if not outcome:
        outcome = ("OUTCOME_SIDE_NO" if _direction(order) == "SHORT"
                   else "OUTCOME_SIDE_YES")
    return ob.venue_price(outcome, px)


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def judge(order: Dict[str, Any], g: Gates,
          cumulative_notional: D = D("0")) -> Dict[str, Any]:
    """One venue order against every gate. Names EVERY refusal, not the first.

    `cumulative_notional` is this market's already-counted spend, because the
    engine's cap is cumulative per market, not per order.
    """
    px = _d(order.get("limit_price"))          # the WIRE value, YES terms
    qty = _d(order.get("quantity"))
    refusals: List[Dict[str, Any]] = []
    own = _own_price(order)                    # the price in OUR terms
    # EVERY gate below is applied in OWN terms, because that is what the
    # engine's own constants are stated in and what the position actually
    # costs. For a short, wire 0.29 is own 0.71: using the wire price would
    # understate this order's cost by 2.4x and put it in a different band.
    notional = (own * qty) if (own is not None and qty is not None) else None
    if own is None or qty is None:
        refusals.append({"gate": "unreadable_order",
                         "detail": "missing price or quantity"})
    else:
        if not (g.band_lo <= own <= g.band_hi):
            refusals.append({
                "gate": "spring_band",
                "detail": ("own-terms price %s outside [%s, %s] "
                           "(wire %s)" % (own, g.band_lo, g.band_hi, px))})
        if cumulative_notional + notional > g.notional_cap:
            refusals.append({
                "gate": "per_market_notional_cap",
                "detail": ("proposed %s + already %s > cap %s (%.1fx over)"
                           % (notional, cumulative_notional, g.notional_cap,
                              float((cumulative_notional + notional)
                                    / g.notional_cap))),
                "proposed_notional": str(notional),
                "cap": str(g.notional_cap),
                "over_by": str(cumulative_notional + notional
                               - g.notional_cap)})
        if qty > g.max_residual_units:
            refusals.append({
                "gate": "max_residual_units",
                "detail": "qty %s > %s" % (qty, g.max_residual_units)})

    # ---------------------------------------------------------------- side
    # WHAT THE VENUE'S `side` ACTUALLY MEANS, and why an earlier draft of
    # this file was wrong about it.
    #
    # PMUS prices and sides are stated in LONG (YES) TOKEN terms. The venue
    # expresses "buy NO" as "sell YES", and `src/l5_order_body.py` encodes
    # the convention the hard way round: `price.value` is ALWAYS the long
    # side's price, and to buy NO at X the WIRE value is 1 - X. That was
    # learned live on 2026-09-05/06 when 24 of 24 NO-side post-only orders
    # priced in NO terms were rejected.
    #
    # So the triple ORDER_SIDE_SELL + OUTCOME_SIDE_NO + ORDER_INTENT_BUY_SHORT
    # is a PURCHASE of the NO outcome, not a liquidation. It is exactly what
    # the engine's own DOWN leg serializes to. An earlier version of this
    # module refused it as `no_sell_path` on `side.endswith("SELL")`, which
    # read the venue's YES-token view as a sell of inventory. That was a
    # misreading of the venue, not a finding about the engine, and it is
    # withdrawn.
    #
    # `l5_sell_is_noop` is real, but it is about a LIQUIDATING sell -- exiting
    # inventory already held. The venue cannot distinguish that from an
    # opening short at the order level, because both are ORDER_SIDE_SELL;
    # only inventory state at submission time can, so this module reports the
    # ambiguity instead of resolving it by assumption.
    direction = _direction(order)
    own_px = _own_price(order)
    engine_equivalent = None
    if direction == "SHORT":
        engine_equivalent = {
            "engine_side": "DOWN",
            "outcome_side": "OUTCOME_SIDE_NO",
            "own_terms_price": None if own_px is None else str(own_px),
            "wire_price_value": None if px is None else str(px),
            "serializer": "src.l5_order_body.build(side='DOWN', "
                          "price=<own terms>, qty=..., post_only=True)",
            "note": ("the engine already submits its DOWN leg this way: "
                     "OUTCOME_SIDE_NO with price.value = 1 - own price. On "
                     "the wire an engine DOWN order and this manual order "
                     "are the same shape."),
        }
    elif direction == "LONG":
        engine_equivalent = {
            "engine_side": "UP",
            "outcome_side": "OUTCOME_SIDE_YES",
            "own_terms_price": None if own_px is None else str(own_px),
            "wire_price_value": None if px is None else str(px),
            "serializer": "src.l5_order_body.build(side='UP', ...)",
        }
    else:
        refusals.append({
            "gate": "direction_unreadable",
            "detail": "neither intent nor side+outcomeSide states a "
                      "direction; never assumed LONG",
            "structural": True})

    # maker-only: the venue's own record of whether the order was passive
    pdi = order.get("participateDontInitiate")
    if g.maker_only and pdi is False:
        refusals.append({
            "gate": "maker_only",
            "detail": ("venue recorded participateDontInitiate=false, i.e. "
                       "the order was allowed to initiate; the engine only "
                       "ever posts post-only")})

    # the complement a pair would need, so the owner's leg is admissible
    complement_max = None
    if own is not None:
        complement_max = g.effective_pair_cap - own
        if complement_max <= 0:
            refusals.append({
                "gate": "pair_cost",
                "detail": ("own-terms leg at %s leaves %s for the "
                           "complement under the effective pair cap %s: no "
                           "complement price exists"
                           % (own, complement_max, g.effective_pair_cap))})

    # size the engine WOULD have used, if the only problem is size
    # Headroom can be zero but never negative: a cap already spent leaves
    # nothing, and a negative quantity would be nonsense dressed as
    # arithmetic.
    headroom = max(D("0"), g.notional_cap - cumulative_notional)
    engine_qty = None
    if own is not None and own > 0:
        engine_qty = headroom / own

    # ------------------------------------------------- the four categories
    CAPITAL_GATES = {"per_market_notional_cap"}
    POLICY_GATES = {"spring_band", "pair_cost", "max_residual_units",
                    "maker_only"}
    hit = {r["gate"] for r in refusals}
    if any(r.get("structural") for r in refusals):
        category = "1_UNSUPPORTED_ACTION"
    elif not hit:
        category = ("2_EXPRESSED_DIFFERENTLY" if direction == "SHORT"
                    else "0_DIRECTLY_SUPPORTED")
    elif hit & POLICY_GATES:
        category = "3_BLOCKED_BY_PRICING_OR_INVENTORY_POLICY"
    elif hit <= CAPITAL_GATES:
        category = "4_BLOCKED_SOLELY_BY_CAPITAL_LIMITS"
    else:
        category = "3_BLOCKED_BY_PRICING_OR_INVENTORY_POLICY"

    structural = any(r.get("structural") for r in refusals)
    only_size = (refusals and not structural
                 and all(r["gate"] == "per_market_notional_cap"
                         for r in refusals))
    if not refusals:
        verdict = "SUBMITTED"
    elif only_size and engine_qty is not None and engine_qty > 0:
        verdict = "RESIZED"
    else:
        verdict = "BLOCKED"

    mi = str(order.get("manualOrderIndicator") or "")
    origin = ("MANUAL" if mi.endswith("_MANUAL") else
              ("AUTOMATED" if mi.endswith("_AUTOMATIC") else "UNKNOWN"))
    return {
        "order_id": order.get("id"),
        "slug": order.get("slug"),
        #: the VENUE's own manual-vs-automated field. Authoritative, and it
        #: outranks the size heuristic: an engine order and a manual order
        #: can share a size, but they cannot share this.
        "ORIGIN": origin,
        "intent": order.get("intent"),
        "direction": direction,
        "own_terms_price": None if own is None else str(own),
        "wire_price_value": None if px is None else str(px),
        "CATEGORY": category,
        "engine_equivalent_action": engine_equivalent,
        "liquidation_ambiguity": (
            "a SHORT order is an opening NO purchase unless inventory was "
            "already long that outcome, in which case it is a liquidation "
            "and l5_sell_is_noop would apply. The venue does not state "
            "which; only inventory at submission time can."
            if direction == "SHORT" else None),
        "manualOrderIndicator": order.get("manualOrderIndicator") or None,
        "side": order.get("side"),
        "limit_price_own_terms": None if own is None else str(own),
        "owner_quantity": None if qty is None else str(qty),
        "owner_notional_own_terms": (None if notional is None
                                     else str(notional)),
        "venue_state": order.get("state"),
        "venue_cumQuantity": order.get("cumQuantity"),
        "venue_participateDontInitiate": pdi,
        "venue_resting_s": order.get("resting_s"),
        "ENGINE_VERDICT": verdict,
        "refusals": refusals,
        "engine_quantity_if_resized": (None if engine_qty is None
                                       else str(engine_qty)),
        "engine_notional_if_resized": (
            None if engine_qty is None else str(headroom)),
        "complement_price_ceiling": (None if complement_max is None
                                     else str(complement_max)),
        "size_ratio_owner_over_engine": (
            None if (engine_qty is None or qty is None or engine_qty <= 0)
            else str((qty / engine_qty).quantize(D("0.01")))),
    }


def replay(audit: Dict[str, Any]) -> Dict[str, Any]:
    """Judge every order in an audit blob, cumulating notional per market."""
    g = load_gates()
    spent: Dict[str, D] = {}
    rows: List[Dict[str, Any]] = []
    for o in audit.get("orders") or []:
        slug = str(o.get("slug"))
        r = judge(o, g, spent.get(slug, D("0")))
        rows.append(r)
        #: THE ENGINE'S CAP COUNTS ONLY THE ENGINE'S OWN SUBMISSIONS.
        #: `spring_cumulative_notional` sums this market's rows from the
        #: campaign ledger, and a manual order placed in the venue's UI is
        #: not in that ledger -- it never reaches the counter. Feeding manual
        #: notional into it would make the engine look blocked by the owner's
        #: own activity, which is not what would have happened.
        if r["ORIGIN"] == "MANUAL":
            continue
        n = _d(r["owner_notional_own_terms"])
        if n is not None:
            spent[slug] = spent.get(slug, D("0")) + n
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["ENGINE_VERDICT"]] = counts.get(r["ENGINE_VERDICT"], 0) + 1
    category_counts: Dict[str, int] = {}
    for r in rows:
        category_counts[r["CATEGORY"]] = \
            category_counts.get(r["CATEGORY"], 0) + 1
    manual_category_counts: Dict[str, int] = {}
    for r in rows:
        if r["ORIGIN"] == "MANUAL":
            manual_category_counts[r["CATEGORY"]] = \
                manual_category_counts.get(r["CATEGORY"], 0) + 1
    origin_counts: Dict[str, int] = {}
    for r in rows:
        origin_counts[r["ORIGIN"]] = origin_counts.get(r["ORIGIN"], 0) + 1
    manual = [r for r in rows if r["ORIGIN"] == "MANUAL"]
    manual_counts: Dict[str, int] = {}
    for r in manual:
        manual_counts[r["ENGINE_VERDICT"]] = \
            manual_counts.get(r["ENGINE_VERDICT"], 0) + 1
    manual_gate_hits: Dict[str, int] = {}
    for r in manual:
        for ref in r["refusals"]:
            manual_gate_hits[ref["gate"]] = \
                manual_gate_hits.get(ref["gate"], 0) + 1
    gate_hits: Dict[str, int] = {}
    for r in rows:
        for ref in r["refusals"]:
            gate_hits[ref["gate"]] = gate_hits.get(ref["gate"], 0) + 1
    return {
        "label": "PMUS_MANUAL_VS_ENGINE_REPLAY_V1",
        "accounting": ("offline, read-only. Fills, prices, maker/taker and "
                       "settlement are the VENUE's records. No touch=fill "
                       "assumption is made anywhere."),
        "walk_complete": (audit.get("activities_walk") or {}).get("complete"),
        "walk_ended": (audit.get("activities_walk") or {}).get("ended"),
        "gates": {k: str(v) for k, v in {
            "spring_band": "[%s, %s]" % (g.band_lo, g.band_hi),
            "min_trades_1h": g.min_trades_1h,
            "effective_pair_cap": g.effective_pair_cap,
            "per_market_notional_cap": g.notional_cap,
            "max_residual_units": g.max_residual_units,
            "max_concurrent": g.max_concurrent,
            "maker_only": g.maker_only,
            "rest_s": g.rest_s,
            "fresh_book_ttl_s": g.fresh_book_ttl_s,
            "max_actions_live": g.max_actions_live,
        }.items()},
        "gate_sources": g.source,
        "verdict_counts": counts,
        "origin_counts": origin_counts,
        "category_counts": category_counts,
        "manual_category_counts": manual_category_counts,
        "category_legend": {
            "0_DIRECTLY_SUPPORTED": "a LONG buy the engine admits as-is",
            "1_UNSUPPORTED_ACTION": "the engine has no way to express it",
            "2_EXPRESSED_DIFFERENTLY": ("supported economic exposure; the "
                                        "venue states it in long-token "
                                        "terms and the engine's DOWN leg "
                                        "serializes to the same wire order"),
            "3_BLOCKED_BY_PRICING_OR_INVENTORY_POLICY":
                "band, pair cost, residual or maker-only refuses it",
            "4_BLOCKED_SOLELY_BY_CAPITAL_LIMITS":
                "nothing but the per-market notional cap refuses it"},
        "manual_orders": len(manual),
        "manual_verdict_counts": manual_counts,
        "manual_refusing_gate_counts": manual_gate_hits,
        "refusing_gate_counts": gate_hits,
        #: judged on the MANUAL orders only -- the engine's treatment of its
        #: own orders is not a behavioural difference.
        "largest_behavioural_difference": (
            max(manual_gate_hits, key=lambda k: manual_gate_hits[k])
            if manual_gate_hits else None),
        "largest_difference_scope": "manual orders only",
        "orders": rows,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("audit_json", help="output of pmus_manual_order_audit")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    audit = json.loads(open(a.audit_json).read())
    r = replay(audit)
    print(json.dumps({k: v for k, v in r.items() if k != "orders"}, indent=2))
    print()
    hdr = ("%-14s %-42s %7s %9s %10s %-10s %s"
           % ("order", "slug", "px", "qty", "notional", "venue", "ENGINE"))
    print(hdr)
    print("-" * len(hdr))
    for o in r["orders"]:
        print("%-14s %-42s %7s %9s %10s %-10s %s"
              % (str(o["order_id"])[:14], str(o["slug"])[:42],
                 o["limit_price_own_terms"], o["owner_quantity"],
                 o["owner_notional_own_terms"],
                 str(o["venue_state"]).replace("ORDER_STATE_", "")[:10],
                 o["ENGINE_VERDICT"]
                 + ("  <- " + ", ".join(x["gate"] for x in o["refusals"])
                    if o["refusals"] else "")))
    if a.out:
        open(a.out, "w").write(json.dumps(r, indent=2, default=str))
        print("\nwrote %s" % a.out)
    if not r["walk_complete"]:
        print("\nWALK INCOMPLETE (%s): every count above is a LOWER BOUND "
              "and no absence may be read from it." % r["walk_ended"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
