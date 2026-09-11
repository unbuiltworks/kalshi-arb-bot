"""One durable row per shadow proposal, so the gate can be scored.

OWNER 2026-09-11: "Emit a durable per-proposal lifecycle row sufficient to
evaluate the pre-registered <=2-tick/high-flow hypothesis proposal-by-proposal
... Preserve explicit SHADOW / TRADE_THROUGH_PROXY semantics; never call these
fills or realized P&L."

WHY THIS IS ONE EMISSION AND NOT A NEW SERVICE
-----------------------------------------------
`ExecutionShadow.proposals` already holds every field this row needs, and
`save()` already persists them to the service's volume. What never existed was
a path from there to anywhere readable: the service logs SERVICE-COMPUTED
AGGREGATES -- medians by outcome, counts by band -- and an aggregate cannot be
re-cut. The pre-registered gate needs residual size, side, cost basis and the
market's resolution joined ON ONE ROW, and no aggregate can supply that.

So this module adds no measurement. It flattens what the shadow already knows
into a row that survives the process, and nothing else.

THE TWO WORDS THAT MUST NOT DRIFT
---------------------------------
SHADOW: no order was placed. Not one. These prices were never sent anywhere.

TRADE_THROUGH_PROXY: "would fill" means a trade printed through the resting
price in the hitting direction inside the follow window. It is an UPPER BOUND
-- we cannot see queue position, so a real order at this price might have been
behind the volume that printed. Every count here is therefore the best case,
and the gate must be read knowing that.

Neither is decoration. A row that loses them becomes indistinguishable from a
fill record, and this programme's accounting rules exist because that confusion
is expensive.

WHY "NEITHER" NEVER APPEARS IN two_sided_outcome
------------------------------------------------
A proposal is only created AFTER the replay's virtual first leg, so every row
has a first side by construction. The absence of a NEITHER class is a property
of where proposals come from, not a measurement that both sides always trade,
and the label says so on every row.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

SCHEMA = "pmus_shadow_proposal/v1"
EMIT_PREFIX = "PMUS_SHADOW_PROPOSAL"

# Terminal statuses worth a durable row. PENDING and WOULD_POST are emitted
# too -- the post itself is evidence of what the policy wanted -- but they are
# emitted once each, on transition, never on every cycle.
BOTH = "BOTH_SIDES_TRADED_THROUGH"
FIRST_ONLY = "FIRST_SIDE_ONLY"
UNRESOLVED = "UNRESOLVED"

SEMANTICS = (
    "SHADOW: no order was placed, ever. TRADE_THROUGH_PROXY: 'would fill' "
    "means a trade printed through the resting price in the hitting direction "
    "inside the follow window -- an UPPER BOUND, since queue position is "
    "unobservable. These are not fills and carry no realized P&L."
)

OUTCOME_NOTE = (
    "a proposal exists only after the replay's virtual first leg, so every row "
    "has a first side by construction; NEITHER cannot occur here and its "
    "absence is not evidence that both sides always trade."
)

RESOLUTION_NOTE = (
    "joined OFFLINE by slug. A proposal resolves inside the follow window, "
    "hours before its event settles, so resolution is null at emission by "
    "design rather than missing. residual_units, residual_side and "
    "surplus_avg_cost are the fields that make that join scorable."
)


QUEUE_NOTE = (
    "QUEUE POSITION. Nulls here are a PIPELINE gap, not a venue limitation: "
    "PMUS /v1/markets/{slug}/book serves full `bids` and `offers` ladders, and "
    "the collector's normalizer keeps only bids[0].qty / asks[0].qty as "
    "bid_touch_qty / ask_touch_qty and discards every level behind them. Until "
    "the ladder is retained, queue_ahead_qty is computable ONLY when our price "
    "IS the touch (nothing is ahead of the front of the queue except the "
    "resting size at that price itself)."
)


def queue_position(prop: Mapping[str, Any]) -> dict:
    """How much better-priced size sits between us and a trade.

    OWNER 2026-09-11, on a book showing ~211k bid at 81c: "displayed depth !=
    flow through our particular resting order. If we post at 79c while 211,000
    shares are already bidding 81c, nobody can reach us until all that
    better-priced demand disappears/trades."

    This is the quantity that would decide whether our non-fills are a
    queue-priority problem rather than a liquidity one, so the row carries it
    -- populated where the ladder survives, explicitly null and explained
    where it does not. It is never estimated from top-of-book: a guess here
    would answer the owner's question with a number we invented."""
    s = dict(prop.get("state_at_post") or {})
    levels = s.get("book_levels")
    at_touch = s.get("distance_to_touch")
    out = {
        "queue_ahead_qty": None,
        "size_at_our_price": None,
        "levels_better_than_us": None,
        "book_levels_available": bool(levels),
        "our_price_is_the_touch": (None if at_touch is None
                                   else abs(float(at_touch)) < 1e-9),
    }
    if not levels:
        # the one case answerable without the ladder
        if out["our_price_is_the_touch"] and s.get("bid_touch_qty") is not None:
            out["size_at_our_price"] = s.get("bid_touch_qty")
            out["queue_ahead_qty"] = 0.0
            out["levels_better_than_us"] = 0
        return out
    try:
        px = float(prop.get("px"))
        ahead, at, better = 0.0, None, 0
        for lvl in levels:
            lp, lq = float(lvl.get("px")), float(lvl.get("qty") or 0)
            if lp > px:
                ahead += lq
                better += 1
            elif lp == px:
                at = (at or 0.0) + lq
        out.update({"queue_ahead_qty": ahead, "size_at_our_price": at,
                    "levels_better_than_us": better})
    except Exception:                                   # noqa: BLE001
        pass
    return out


def two_sided_outcome(prop: Mapping[str, Any]) -> str:
    """BOTH when the complement traded through too; FIRST_ONLY when the
    follow window closed without it; UNRESOLVED while it is still open."""
    status = str(prop.get("status") or "")
    if status in ("VIRTUAL_MATCHED", "WOULD_FILL"):
        return BOTH
    if status == "NO_FILL":
        return FIRST_ONLY
    return UNRESOLVED


def _residual_side(prop: Mapping[str, Any]) -> Optional[str]:
    """Which side the surplus sits on, from the residual's sign.

    Positive residual means a surplus of the UP side. Returned as a word
    because a sign on its own has been misread here before."""
    r = prop.get("residual_after")
    if r is None:
        r = prop.get("residual_before")
    try:
        r = float(r)
    except (TypeError, ValueError):
        return None
    if r == 0:
        return "none"
    return "up" if r > 0 else "down"


def lifecycle_row(prop: Mapping[str, Any],
                  resolution: Optional[Mapping[str, Any]] = None) -> dict:
    """The flat, durable row. Absent is None; never zero."""
    s = dict(prop.get("state_at_post") or {})
    first = dict(prop.get("first_leg") or {})
    return {
        "schema": SCHEMA,
        "id": prop.get("id"),
        "slug": prop.get("slug"),
        "tier": prop.get("tier"),
        "status": prop.get("status"),
        "two_sided_outcome": two_sided_outcome(prop),

        # ---- decision-time state: everything the rule may be defined on
        "distance_to_touch": s.get("distance_to_touch"),
        "spread": s.get("spread"),
        "median_spread": s.get("median_spread"),
        "flow_usd_per_min": s.get("flow_usd_per_min"),
        "mid_stdev": s.get("mid_stdev"),
        "mid_range": s.get("mid_range"),
        "bid_touch_qty": s.get("bid_touch_qty"),
        "ask_touch_qty": s.get("ask_touch_qty"),
        **queue_position(prop),
        "mid": s.get("mid"),
        "band_position": s.get("band_position"),
        "in_band": s.get("in_band"),
        "sport": s.get("sport"),
        "market_type": s.get("market_type"),

        # ---- the first side, which the replay filled virtually
        "first_side": first.get("side"),
        "first_px": first.get("px"),
        "first_t": first.get("t"),

        # ---- the complement, followed forward against the tape
        "complement_side_yes": prop.get("side_yes"),
        "complement_px": prop.get("px"),
        "complement_t_post": prop.get("t_post"),
        "complement_t_trade_through": prop.get("t_fill"),
        "complement_latency_s": prop.get("latency_s"),
        "complement_touched": prop.get("touched"),
        "complement_volume_through": prop.get("traded_through"),
        "complement_qty_upper_bound": prop.get("fillable_qty"),
        "q1_size": prop.get("q1_size"),

        # ---- what the gate needs: residual proxy and pair economics
        "residual_before": prop.get("residual_before"),
        "residual_after": prop.get("residual_after"),
        "residual_units": prop.get("residual_after", prop.get("residual_before")),
        "residual_side": _residual_side(prop),
        "surplus_avg_cost": prop.get("surplus_avg"),
        "implied_pair_cost_at_post": s.get("implied_pair_cost"),
        "pair_cost": prop.get("pair_cost"),
        "max_pair_cost": prop.get("max_pair_cost"),
        "matched_after": prop.get("matched_after"),

        # ---- eventual resolution, joined offline
        "resolution": dict(resolution) if resolution else None,

        # ---- the labels that must never drift
        "semantics": SEMANTICS,
        "outcome_note": OUTCOME_NOTE,
        "queue_note": QUEUE_NOTE,
        "resolution_note": RESOLUTION_NOTE,
    }


def emit_key(prop: Mapping[str, Any]) -> tuple:
    """One row per proposal per STATE TRANSITION.

    Keyed on (id, status) rather than id alone: the post and its terminal
    verdict are both worth a row, and re-emitting an unchanged proposal on
    every 70-second cycle would bury them under thousands of duplicates --
    the same failure the risk-set snapshot's change-gate was added for."""
    return (prop.get("id"), prop.get("status"))


def render(row: Mapping[str, Any]) -> str:
    return EMIT_PREFIX + " " + json.dumps(dict(row), separators=(",", ":"),
                                          default=str)


def emit_new(proposals, already: set, echo=print) -> int:
    """Emit every proposal whose (id, status) has not been emitted. Returns
    the number written; `already` is mutated so the caller keeps the memo
    across cycles."""
    n = 0
    for prop in proposals or ():
        if not isinstance(prop, Mapping):
            continue
        key = emit_key(prop)
        if key in already:
            continue
        already.add(key)
        echo(render(lifecycle_row(prop)))
        n += 1
    return n
