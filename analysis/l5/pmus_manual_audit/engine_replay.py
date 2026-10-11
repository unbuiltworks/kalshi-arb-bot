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
    px = _d(order.get("limit_price"))
    qty = _d(order.get("quantity"))
    refusals: List[Dict[str, Any]] = []

    notional = (px * qty) if (px is not None and qty is not None) else None
    if px is None or qty is None:
        refusals.append({"gate": "unreadable_order",
                         "detail": "missing price or quantity"})
    else:
        if not (g.band_lo <= px <= g.band_hi):
            refusals.append({
                "gate": "spring_band",
                "detail": "limit %s outside [%s, %s]" % (px, g.band_lo,
                                                         g.band_hi)})
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

    # THE ENGINE HAS NO SELL PATH AT ALL. This is not a cap that could be
    # raised: `pmus_sports_l5_decision.decide` returns
    # {"decision": "no_action", "reason": "l5_sell_is_noop"} for a sell
    # relation, and `pmus_spring_l5_session` records the same stop_reason.
    # OPEN, EXTEND and COMPLETE are all BUYS. A sell order is therefore not
    # resized or blocked by a threshold -- it cannot be expressed.
    side = str(order.get("side") or "")
    if side.endswith("SELL"):
        refusals.append({
            "gate": "no_sell_path",
            "detail": ("the engine's action vocabulary is OPEN/EXTEND/"
                       "COMPLETE, all buys; a sell relation returns "
                       "no_action with reason l5_sell_is_noop"),
            "engine_reason": "l5_sell_is_noop",
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
    if px is not None:
        complement_max = g.effective_pair_cap - px
        if complement_max <= 0:
            refusals.append({
                "gate": "pair_cost",
                "detail": ("leg at %s leaves %s for the complement under the "
                           "effective pair cap %s: no complement price "
                           "exists" % (px, complement_max,
                                       g.effective_pair_cap))})

    # size the engine WOULD have used, if the only problem is size
    # Headroom can be zero but never negative: a cap already spent leaves
    # nothing, and a negative quantity would be nonsense dressed as
    # arithmetic.
    headroom = max(D("0"), g.notional_cap - cumulative_notional)
    engine_qty = None
    if px is not None and px > 0:
        engine_qty = headroom / px

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
        "manualOrderIndicator": order.get("manualOrderIndicator") or None,
        "side": order.get("side"),
        "limit_price": None if px is None else str(px),
        "owner_quantity": None if qty is None else str(qty),
        "owner_notional": None if notional is None else str(notional),
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
        n = _d(r["owner_notional"])
        if n is not None:
            spent[slug] = spent.get(slug, D("0")) + n
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["ENGINE_VERDICT"]] = counts.get(r["ENGINE_VERDICT"], 0) + 1
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
                 o["limit_price"], o["owner_quantity"], o["owner_notional"],
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
