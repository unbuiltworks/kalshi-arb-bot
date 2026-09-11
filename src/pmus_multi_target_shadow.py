"""The same market state, priced at several pair targets at once.

OWNER 2026-09-11: "Have it post virtual complements at several simultaneous
targets against the same market state: 0.98, 0.985, 0.99, 0.995, optionally
touch itself as a reference. Then follow every virtual price against the exact
same live tape... How much fill probability do we buy by giving up 0.5c, 1c, or
1.5c of pair edge?"

WHY THIS EXPERIMENT AND NOT A COHORT COMPARISON
-----------------------------------------------
Our live policy pins the pair target: 120 of 121 proposals carried
implied_pair_cost_at_post 0.98, because the complement price is
min(ceiling - avg_cost, hard_bound, opp_ask - tick). There is no natural
variation to study, so the variation has to be CREATED. Every arm here prices
the same first-leg state against the same tape at the same instant, which makes
the comparison a paired one rather than a comparison of cohorts that differ in
market, time and inventory as well as in price.

THE ARITHMETIC THE ARMS EXPLORE
--------------------------------
Joining both touches costs `1 - spread`. On a half-cent book that is 0.995,
while our target is 0.98 -- so the complement must sit about three ticks back,
and three ticks back is where ~200,000 shares sit in front of us. Each arm
relaxes that by half a cent and moves toward the front of the queue.

WHAT THIS CANNOT MEASURE, AND IT IS HALF THE OWNER'S GATE
---------------------------------------------------------
The owner's bar is: extra completed-pair edge + rebates > additional residual
losses caused by more first-side fills. **This experiment holds the first leg
FIXED.** Every arm inherits the same virtual first fill, so it isolates the
complement effect cleanly -- and therefore says NOTHING about whether a relaxed
policy would take more first legs and strand more of them. That half of the
gate needs a different experiment and is not answered here.

STRICT SEMANTICS, INHERITED
---------------------------
No order is placed. A "fill" is a trade printing through the resting price in
the hitting direction inside the follow window -- an upper bound, since queue
position is unobservable to the fill rule even where queue-ahead is measured.
The arms carry SHADOW / TRADE_THROUGH_PROXY labels exactly as the policy arm
does, and nothing here is realized P&L.
"""
from __future__ import annotations

import json
from decimal import Decimal as D, ROUND_DOWN
from typing import Any, Iterable, Mapping, Optional, Sequence

from src import pmus_execution_shadow as ex

SCHEMA = "pmus_multi_target/v1"
EMIT_PREFIX = "PMUS_SHADOW_TARGET_ARM"

# The owner's ladder, plus the touch as a reference arm.
TARGETS = (D("0.98"), D("0.985"), D("0.99"), D("0.995"))
TOUCH_ARM = "touch"

SEMANTICS = (
    "SHADOW: no order was placed. TRADE_THROUGH_PROXY: a trade printed through "
    "the resting price in the hitting direction -- an UPPER BOUND, queue "
    "position is not enforced by the fill rule. Not fills; no realized P&L."
)
FIRST_LEG_NOTE = (
    "every arm shares ONE virtual first leg, so this isolates the complement "
    "effect and says nothing about whether a relaxed policy would take more "
    "first legs and strand more of them -- half the owner's gate, unanswered "
    "here by construction."
)


def _floor_tick(px: D) -> D:
    return (px / ex.TICK).to_integral_value(rounding=ROUND_DOWN) * ex.TICK


def target_price(target: D, surplus_avg, opp_ask, side_yes: bool) -> Optional[D]:
    """The complement price implied by a pair target, made legal as a maker.

    `target - surplus_avg` is what we would pay to reach that pair. It is then
    capped below the opposing ask so the order rests rather than crosses -- an
    arm that crossed would be measuring a taker, which is a different strategy
    and would flatter the relaxed targets for the wrong reason."""
    if surplus_avg is None:
        return None
    try:
        px = D(str(target)) - D(str(surplus_avg))
    except Exception:                                   # noqa: BLE001
        return None
    if opp_ask is not None:
        try:
            px = min(px, D(str(opp_ask)) - ex.TICK)
        except Exception:                               # noqa: BLE001
            pass
    px = _floor_tick(px)
    if px < ex.TICK or px > (D("1") - ex.TICK):
        return None
    return px


def touch_price(row: Mapping[str, Any], side_yes: bool) -> Optional[D]:
    """The price that JOINS the touch on the side we are buying.

    A YES buy joins the YES bid. A NO buy joins the NO bid, which is
    `1 - yes_ask`. Same framing as the queue computation, and getting it wrong
    would make the reference arm the most misleading row in the table."""
    q = row.get("bid") if side_yes else row.get("ask")
    if q is None:
        return None
    try:
        px = D(str(q)) if side_yes else (D("1") - D(str(q)))
    except Exception:                                   # noqa: BLE001
        return None
    return _floor_tick(px)


def arms_for(slug: str, plan: Mapping[str, Any], t_post: float,
             ranked_row: Optional[Mapping[str, Any]], residual_before,
             surplus_avg, tier=None,
             first_leg: Optional[Mapping[str, Any]] = None,
             targets: Sequence[D] = TARGETS,
             include_touch: bool = True) -> list:
    """One proposal per target, all sharing the first leg and the instant."""
    row = dict(ranked_row or {})
    side_yes = bool(plan.get("_side_yes", plan.get("side") == "YES"))
    opp_ask = row.get("ask") if side_yes else (
        None if row.get("bid") is None else 1.0 - float(row["bid"]))
    out = []
    wanted = [(str(t), target_price(t, surplus_avg, opp_ask, side_yes))
              for t in targets]
    if include_touch:
        wanted.append((TOUCH_ARM, touch_price(row, side_yes)))
    for name, px in wanted:
        if px is None:
            continue
        p = ex.open_proposal(slug, {**plan, "target_price": str(px)}, t_post,
                             row, residual_before, surplus_avg, tier=tier,
                             first_leg=first_leg)
        p["arm"] = name
        p["id"] = f"{p['id']}|{name}"
        p["arm_pair_target"] = (str(surplus_avg) if name == TOUCH_ARM
                                else name)
        p["implied_pair_at_arm"] = (
            None if surplus_avg is None
            else str(D(str(surplus_avg)) + D(str(px))))
        out.append(p)
    return out


class MultiTargetArm:
    """Holds and advances the sibling arms, separately from the policy arm.

    SEPARATE ON PURPOSE. Mixing these into ExecutionShadow.proposals would
    change every aggregate the policy arm reports -- completion rate, cohort
    metric, distance bands -- and those are frozen evidence. The arms are
    additive or they are nothing."""

    def __init__(self) -> None:
        self.arms: dict = {}

    def record(self, **kw) -> int:
        made = arms_for(**kw)
        for p in made:
            self.arms.setdefault(p["id"], p)
        return len(made)

    def advance(self, obs_by_slug: Mapping[str, Any], now: float) -> int:
        n = 0
        for pid, p in list(self.arms.items()):
            if p.get("status") in ex.TERMINAL:
                continue
            self.arms[pid] = ex.follow(p, obs_by_slug.get(p["slug"]) or [], now)
            n += 1
        return n

    def report(self) -> dict:
        return by_arm(self.arms.values())


def by_arm(props: Iterable[Mapping[str, Any]]) -> dict:
    """Trade-through rate and timing per target, over RESOLVED arms only."""
    groups: dict = {}
    for p in props or ():
        groups.setdefault(str(p.get("arm")), []).append(p)
    out = {}
    for name, ps in sorted(groups.items()):
        resolved = [p for p in ps if p.get("status") in ex.TERMINAL]
        through = [p for p in resolved
                   if p.get("status") in (ex.WOULD_FILL, ex.VIRTUAL_MATCHED)]
        lat = sorted(float(p["latency_s"]) for p in through
                     if p.get("latency_s") is not None)
        ticks = [p["state_at_post"].get("ticks_from_touch") for p in ps
                 if (p.get("state_at_post") or {}).get("ticks_from_touch")
                 is not None]
        ahead = [q for q in (_queue_ahead(p) for p in ps) if q is not None]
        out[name] = {
            "n": len(ps), "resolved": len(resolved),
            "trade_through": len(through),
            "trade_through_rate": (round(len(through) / len(resolved), 4)
                                   if resolved else None),
            "median_latency_s": (lat[len(lat) // 2] if lat else None),
            "median_ticks_from_touch": (sorted(ticks)[len(ticks) // 2]
                                        if ticks else None),
            "median_queue_ahead": (sorted(ahead)[len(ahead) // 2]
                                   if ahead else None),
            "implied_pair": _mode(p.get("implied_pair_at_arm") for p in ps),
        }
    return {"kind": "multi_target_arm_report", "by_arm": out,
            "semantics": SEMANTICS, "first_leg_note": FIRST_LEG_NOTE,
            "paired_note": ("every arm prices the SAME first-leg state at the "
                            "same instant against the same tape, so the "
                            "comparison is paired rather than across cohorts")}


def _queue_ahead(p: Mapping[str, Any]):
    from src import pmus_shadow_proposal_log as plog
    return plog.queue_position(p).get("queue_ahead_qty")


def _mode(vals: Iterable[Any]):
    seen: dict = {}
    for v in vals:
        if v is None:
            continue
        seen[str(v)] = seen.get(str(v), 0) + 1
    if not seen:
        return None
    return max(seen.items(), key=lambda kv: kv[1])[0]


def emit_new(arms: Iterable[Mapping[str, Any]], already: set, echo=print) -> int:
    """One row per arm per state transition, same discipline as the policy
    arm's rows."""
    from src import pmus_shadow_proposal_log as plog
    n = 0
    for p in arms or ():
        key = (p.get("id"), p.get("status"))
        if key in already:
            continue
        already.add(key)
        row = plog.lifecycle_row(p)
        row.update({"schema": SCHEMA, "arm": p.get("arm"),
                    "arm_pair_target": p.get("arm_pair_target"),
                    "implied_pair_at_arm": p.get("implied_pair_at_arm"),
                    "first_leg_note": FIRST_LEG_NOTE})
        echo(EMIT_PREFIX + " " + json.dumps(row, separators=(",", ":"),
                                            default=str))
        n += 1
    return n
