"""Inventory-driven BTC15m shadow controller — STRUCTURALLY UNABLE TO TRADE.

WHAT THIS IS. An INDEPENDENT decision source, not a translator of L5's
trades. `l5_live_execution.handle` derives our side, price and size from
L5's own action (`want = dec["l5_size"] * self.lam`, `raw_px =
dec["price"]`, `dec["side"]` throughout), so the deployed engine follows
L5 rather than deciding. This object decides for itself from OUR inventory,
the live authenticated book and the ACTUAL time to expiry, and reaches no
order path at all.

The decision logic is NOT reimplemented here. It is
`pmus_sports_l5_decision.plan_action`, which is already inventory-driven:
the model's relation is overridden from our residual (`if inv.residual ==
0: rel = "open"`), a completion takes the DEFICIT side and is priced from
OUR OWN average cost through `complement_target`, an extend takes the
SURPLUS side, and accumulation stops at MAX_RESIDUAL_UNITS. This module
supplies the three things that layer needs and the runner has: our
inventory, the book, and the window's real clock.

THE SAFETY PROPERTY IS STRUCTURAL, NOT CONDITIONAL. This class holds no
transport, no executor and no credentials, and defines no method that
could place, cancel or amend an order. It is not disarmed by a flag that
something else could flip -- there is nothing here to arm. An arming
environment variable cannot change what an object does not have, and
`tests/test_l5_shadow_controller.py` asserts the absence rather than
trusting it.

PROVENANCE IS KEPT SEPARATE, DELIBERATELY. Every record carries
`provenance: CONTROLLER_GENERATED`. It is never written through
`FT.l5_native`, never labelled `rested_at_l5_price`, and never enters the
L5 decision tape. Mixing our generated decisions with L5's observed ones
is the error this programme has caught repeatedly; here the two cannot
meet because this path does not touch that tape.

WHAT A FILL MEANS HERE, AND WHAT IT DOES NOT. The runner hands this object
a BBO snapshot, not a ladder with queue depth. On BBO alone a fill cannot
be established: an existing quote is never a fill, and the touch arriving
at our price is executability, not execution. So inventory advances under
ONE named rule, `TOUCH_THROUGH_OPTIMISTIC` -- the opposite side traded
strictly THROUGH our resting price -- and every emission carries that
label. It is an UPPER BOUND on fills and the reported inventory is an
upper bound on inventory. The queue-aware three-scenario model already
exists in `l5_maker_fill_model` and is the upgrade path once this
controller is fed the recorder's ladder frames; it is not silently
approximated here.

READ-ONLY on the venue. Writes exactly one file: its own state.
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src import l5_maker_fill_model as FM
from src import pmus_l5_persistence_v2 as pv2
from src import pmus_sports_l5_decision as l5d

#: never L5_NATIVE. The two populations must stay separable at a glance.
CONTROLLER_PROVENANCE = "CONTROLLER_GENERATED"

#: the rule by which simulated inventory advances, named in every record
#: so a reader can never mistake it for a measured fill rate.
#:
#: REPLACED THE TOUCH-THROUGH GUESS. The first version credited a fill
#: whenever the opposite side printed strictly through our resting price,
#: on a BBO feed with no queue information at all. That is an upper bound
#: and it was labelled one, but an upper bound cannot support an economic
#: claim: replayed against L5's own history it manufactured a >=4%-per-pair
#: locked gain, 80-110x L5's measured 0.30% edge. The queue-aware model in
#: `l5_maker_fill_model` is now the source of fills, with its own three
#: scenarios, and it is fed the SAME authenticated ladder the executor's
#: modelling uses.
FILL_RULE = "QUEUE_AWARE_THREE_SCENARIO"

#: CONSERVATIVE, BASE, OPTIMISTIC -- taken from the fill model rather than
#: redefined here, so the two can never drift apart.
SCENARIOS = FM.SCENARIOS

#: the scenario whose economics are the headline. The owner's standard:
#: conservative net reported separately from optimistic, and promotion
#: decided on the conservative column.
HEADLINE_SCENARIO = FM.CONSERVATIVE

#: attribute names this class must NEVER acquire. Asserted by test, so a
#: future edit that wires an order path in has to delete this line first.
FORBIDDEN_ATTRS = ("submit", "place", "cancel", "amend", "transport",
                   "executor", "credentials", "arm", "disarm")

#: v3 splits each market's inventory BY SCENARIO. A fill that happens only
#: in the optimistic case must not create matched inventory in the
#: conservative one -- and because inventory drives the next decision, each
#: scenario is a separate trajectory, not a separate accounting view of one.
STATE_VERSION = 3

#: ============================ THE DECISION POLICIES ============================
#:
#: The deployed controller consulted the frozen v2 model by ARGMAX. The
#: model's mean probabilities across its on-distribution states are extend
#: 0.541 / complete 0.459, so the argmax turns a near-coin-flip into a
#: MONOTONE run: each extend sets last_rel=extend, which keeps extend as the
#: argmax until the tte bucket itself changes. Measured in replay over L5's
#: own 5,546 BTC15m windows, that gives an extend share of 0.90 with runs of
#: median 5, p90 63 and max 154 -- against L5's own 0.52 and median 2, p90 8,
#: p99 18. That gap is the defect.
#:
#: NO POLICY HERE TARGETS L5'S RATIO. Matching a summary statistic is not a
#: profit mechanism, and forcing one would be fitting the diagnostic. The
#: run-length distribution is reported beside the economics as a BEHAVIOURAL
#: DIAGNOSTIC and is never optimised toward.
#:
#: NO FREE PARAMETERS. Both policies are deterministic and use only
#: constants already in the codebase, so there is nothing to tune on an
#: evaluation sample -- which is what the COMPLETE re-quote campaign was
#: NO-GO'd for.


def policy_argmax(prop, inv, book, tte):
    """BASELINE, preserved for comparison: the model's argmax relation."""
    return prop.get("recommended_relation")


def policy_pair_available(prop, inv, book, tte):
    """CANDIDATE: complete whenever the pair can be locked, else extend.

    The only action that converts inventory into a determinate $1
    redemption is a completion whose combined pair cost clears the cap. So
    this asks the economic question directly -- is a fee-safe completion
    priceable from OUR OWN average cost against the deficit side's touch
    right now? -- and takes it when the answer is yes, whatever the argmax
    says. It never completes because a clock moved and never extends
    because a tte bucket boundary was crossed.

    It is NOT a claim that such a completion FILLS. Whether a patient quote
    inside the touch is ever executed is a queue question this controller
    cannot answer on a BBO feed; see FILL_RULE.
    """
    if inv.residual == 0:
        return "open"
    side_yes = inv.residual < 0                   # the DEFICIT side
    surplus_avg = inv.avg(not side_yes)
    if surplus_avg is None:
        return prop.get("recommended_relation")
    touch = l5d._touch_for(side_yes, book)
    px, _ = l5d.complement_target(surplus_avg, touch,
                                  pair_cap=l5d.BTC15M_PAIR_CAP)
    return "complete" if px is not None else "extend"


def policy_residual_urgency(prop, inv, book, tte):
    """CANDIDATE: never let inventory reach the cap; otherwise the model.

    THIS IS THE DEFECT, MEASURED. Replayed over L5's own 5,546 BTC15m
    windows, the argmax baseline refuses 56% (older half) to 74% (newer
    half) of all its decision opportunities, and EVERY refusal is
    `residual_cap:3>=3` -- 3,219 of 3,219 and 22,311 of 22,323. The argmax
    walks inventory up to the cap and then parks, because at the cap it can
    neither extend nor open and the model keeps recommending extend.
    Production shows exactly that, parked at 3>=3 through the 1600z window.

    So this completes ONE STEP BEFORE the cap and otherwise defers to the
    model. It is the smaller of the two corrections: it keeps EXTEND as a
    first-class action -- a 35% extend share in replay, against the
    operating model's "same-side EXTEND is not an error to flatten
    immediately" -- where `policy_pair_available` extends 0.1% of the time
    and is therefore a different strategy rather than a replication.

    The threshold is not tuned. It is MAX_RESIDUAL_UNITS - 1: the last
    state from which an extend is still legal.
    """
    if inv.residual == 0:
        return "open"
    if abs(inv.residual) >= l5d.MAX_RESIDUAL_UNITS - 1:
        return "complete"
    return prop.get("recommended_relation")


POLICIES = {"ARGMAX": policy_argmax,
            "PAIR_AVAILABLE": policy_pair_available,
            "RESIDUAL_URGENCY": policy_residual_urgency}

#: what the deployed money path would do, kept running beside any candidate
BASELINE_POLICY = "ARGMAX"

#: stranded markets named individually in a report. The COUNT is always
#: exact; the list is capped because this report goes in every heartbeat and
#: a long run of failed settlement reads would otherwise grow it without
#: bound -- a diagnostic that drowns the log it lives in stops being one.
STRANDED_REPORT_CAP = 5
#: settled windows kept in the state file. Their TOTALS are folded into a
#: running carry before the oldest are dropped, so the net is preserved
#: exactly and only the per-window detail ages out. 96 windows is a day of
#: BTC15m, so four days of individual windows stay readable.
SETTLED_KEEP = 384


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


def normalise_book(book: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Coerce a venue book snapshot to Decimal prices.

    THE ADAPTER BOUNDARY, AND A PRODUCTION DEFECT THIS FIXES.
    `AuthBook.snapshot` returns `bid`/`ask` as STRINGS -- the recorder's
    `_px` ends in `str(value)` -- while `plan_action` does Decimal
    arithmetic on them (`ONE - ask`, `_floor_tick(bid)`). Handing the raw
    snapshot through raised TypeError on every tick of the first window
    whose price band the model actually covers; the window before it was
    off-distribution and returned before `plan_action`, so the fault was
    invisible for eleven minutes of green-looking operation.

    Coercing HERE and not in `plan_action` is deliberate: that layer is
    shared with the sports path, whose callers already pass Decimals, and
    widening its contract would hide which side of the boundary owns the
    conversion. This is the side that reads the venue.
    """
    if not book:
        return None
    out = dict(book)
    out["bid"], out["ask"] = _d(book.get("bid")), _d(book.get("ask"))
    return out


#: WHY A DECISION DID NOT BECOME A QUOTE, split by whether the refusal is
#: ECONOMICALLY JUSTIFIED or a POSSIBLE MISS. The split is the point: a
#: controller that refuses 95% of ticks looks identical in the counts whether
#: it is declining bad prices or failing to find good ones, and only one of
#: those is a problem to fix.
#:
#: JUSTIFIED means the refusal protects money or admits ignorance:
#:   no_two_sided_book            no complement price, so no pair to price
#:   v2_off_distribution          the frozen model has no state here
#:   would_cross_not_a_maker_quote  resting there would pay the spread
#:   already_resting              one outstanding order per scenario, by design
#:   pair_cap / plan refusals     completion above the fee-safe cap
#: POSSIBLE_MISS means the model had somewhere to go and the policy declined,
#: which is the only bucket that can contain forgone profit.
#: WHERE WE REST RELATIVE TO THE TOUCH. Pre-registered in
#: analysis/l5/TICK_DEPTH_PREREGISTRATION.md.
#:
#: AT_TOUCH is what the three deployed policies do and what the loss
#: decomposition measured: 63 of 69 fills at ticks_behind_touch 0, sitting
#: behind a median 1,500 contracts, so fills arrive almost only when a
#: print sweeps the level -- which is when the market is leaving. 70%
#: adverse at -$0.0467 a unit against +$0.0050 of captured spread.
#:
#: ONE_TICK_BEHIND tests whether paying ourselves a wider spread beats
#: filling less. The offset is EXACTLY one tick and is not a free
#: parameter: it is not searched, not tuned, and not re-chosen after
#: results.
AT_TOUCH = "AT_TOUCH"
ONE_TICK_BEHIND = "ONE_TICK_BEHIND"

#: TWO_SIDED rests UP and DOWN INDEPENDENTLY, one order per side, at the
#: SAME one-tick offset as ONE_TICK_BEHIND -- so the only difference between
#: those two arms is two-sidedness, not depth. It exists to test one
#: hypothesis: that our pair cost is a market outcome rather than a quoting
#: parameter because `max_live_orders = 1` lets only one side rest, so an
#: adversely selected fill has no opposite leg already positioned to
#: complete it. 66,044 `already_resting` refusals is the mechanical
#: expression of that limit.
#:
#: IT IS A HYPOTHESIS, NOT A FINDING. No direct evidence shows L5 quotes a
#: fixed simultaneous offset; what is evidenced is that his pair cost is
#: invariant to price range (r = -0.0006) and to his own leg count
#: (r = +0.0094), and that 40.3% of his price levels fill over >= 30s.
#:
#: AND IT DOES NOT TEST THAT HYPOTHESIS, which matters more than it sounds.
#: This arm PERMITS a second resting side; it does not go looking for one.
#: The policy decides from inventory, so while inventory is flat it wants the
#: same side every tick and the arm rests ONE order and then reports
#: SAME_SIDE for the rest of the window -- measured, not reasoned: eight
#: ticks from flat give one quote and seven SAME_SIDE refusals. Two sides
#: rest only once inventory is non-flat and the policy wants the other one,
#: i.e. extending while a complement rests, or completing while an extend
#: rests. Those are real and frequent states, and freeing them is what the
#: owner authorized as the smallest safe change.
#:
#: Quoting both legs SIMULTANEOUSLY FROM FLAT is a different and larger
#: change: it needs a counterfactual second price for inventory we do not
#: hold, which is a new policy rather than a placement. Nothing here
#: measures it, and a positive result from this arm is NOT evidence for it.
TWO_SIDED = "TWO_SIDED"

#: AND THE ARM THAT ACTUALLY GOES LOOKING FOR THE SECOND SIDE.
#:
#: TWO_SIDED only PERMITS a second resting side; because the policy decides
#: from inventory, a flat arm wants the same side every tick and never holds
#: two legs. This placement quotes the FREE side on its own merits while
#: flat, which is the L5-style ladder the hypothesis is actually about.
#:
#: THE SECOND LEG IS PRICED FROM ITS OWN SIDE OF THE BOOK, not from the
#: first leg's fill -- there is no fill to price against, which is the whole
#: point. `complement_target` cannot be used for it: that function prices a
#: completion against the average cost already PAID on the surplus side, and
#: a ladder from flat has paid nothing.
#:
#: SO THE PAIR BOUND HAS TO BE ENFORCED HERE, and the arithmetic decides the
#: depth rather than a preference. With both legs d ticks behind their own
#: touch and a spread of s ticks, the bid cancels and the planned pair is
#: exactly 1 - s*t - 2*d*t. At the median one-tick spread d=1 gives 0.97,
#: ABOVE the 0.96 cap, so a one-tick ladder's second leg would be refused on
#: essentially every tick. d=2 gives 0.95. The depth is therefore computed
#: per tick from the live spread -- max(nominal, the depth the cap needs) --
#: and reported, so the arm is never silently quoting an unfee-safe pair and
#: never silently refusing to quote at all.
TWO_SIDED_LADDER = "TWO_SIDED_LADDER"
PLACEMENTS = (AT_TOUCH, ONE_TICK_BEHIND, TWO_SIDED, TWO_SIDED_LADDER)

#: offset in ticks, by placement. The ladder's is a NOMINAL FLOOR: the pair
#: bound may deepen it per tick and never shallows it.
PLACEMENT_TICKS = {AT_TOUCH: 0, ONE_TICK_BEHIND: 1, TWO_SIDED: 1,
                   TWO_SIDED_LADDER: 1}

#: may this arm rest both sides at once? Keyed by placement so the runner,
#: the state filename and the variant label all keep working unchanged.
PLACEMENT_TWO_SIDED = {AT_TOUCH: False, ONE_TICK_BEHIND: False,
                       TWO_SIDED: True, TWO_SIDED_LADDER: True}

#: does this arm QUOTE the free side while flat, rather than merely allow
#: it? Only the ladder. Everything else keeps its exact prior behaviour.
PLACEMENT_LADDER_FROM_FLAT = {AT_TOUCH: False, ONE_TICK_BEHIND: False,
                              TWO_SIDED: False, TWO_SIDED_LADDER: True}


def _utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


JUSTIFIED_REFUSALS = (
    # this module's own
    "no_two_sided_book", "would_cross_not_a_maker_quote", "already_resting",
    # the ladder's own: a pair above the cap is not fee-safe and quoting it
    # would be the error, not the refusal; the other two admit ignorance
    "ladder_pair_above_cap", "ladder_resting_price_unknown",
    "ladder_depth_not_priceable",
    # the frozen model admitting it has no state here
    "v2_off_distribution",
    # plan_action's protective refusals, read from its source rather than
    # guessed: each either protects money or admits ignorance
    "complete_without_surplus", "no_legal_maker_complement",
    "no_priceable_side", "one_sided_book", "no_fresh_book",
    "l5_sell_is_noop",
    # the placement arm admitting its own depth is not priceable here
    "offset_not_priceable",
)

#: reasons whose text carries a value, matched by PREFIX.
#: `residual_cap:3>=3` is the RISK LIMIT DOING ITS JOB, not a missed
#: opportunity. It was the single largest bucket in the first production
#: report -- 103 of ARGMAX's 178 refusals -- and classifying it as a
#: possible miss overstated forgone profit by more than half.
JUSTIFIED_REFUSAL_PREFIXES = ("residual_cap", "pair_cap")

#: THE TAXONOMY'S OWN VERSION. Bumped whenever a reason moves between the
#: justified and possible-miss buckets.
#:
#: WHY THIS EXISTS. Persisting the tally and then changing the
#: classification is a trap: the restored counts were bucketed by the OLD
#: rules, so the report adds them to counts bucketed by the new ones and
#: presents the sum as one measurement. Production showed it immediately --
#: 202 `residual_cap:3>=3` still sitting in possible_misses after the fix,
#: restored from a state file written before it, with a `counters_since`
#: stamped AFTER the restart and therefore describing the wrong period.
#:
#: So a tally whose taxonomy version does not match is DISCARDED rather
#: than merged, and the discard is reported.
TALLY_TAXONOMY = 2


def variant_label(policy: str, placement: str) -> str:
    """The name an arm is reported under.

    AT_TOUCH arms keep their bare policy name, so the three deployed
    simulations are labelled exactly as they were before the placement
    dimension existed and nothing downstream has to learn a new name.
    """
    return policy if placement == AT_TOUCH else "%s@%s" % (policy, placement)


def variant_state_filename(label: str) -> str:
    """One definition of the durable filename, used by the runner that
    writes it and the test that asserts where it lands -- so the two can
    never drift into disagreeing about the same file."""
    return "shadow_controller_%s.json" % label.lower().replace("@", "_at_")


#: THE CANONICAL ARM SET: (label, policy, placement).
#:
#: Three policies at the touch, plus TWO arms on the BASELINE policy that
#: vary placement alone: ONE_TICK_BEHIND, so a difference between it and
#: ARGMAX is a difference in RESTING DEPTH alone, and TWO_SIDED, which sits
#: at the SAME depth as ONE_TICK_BEHIND, so a difference between THOSE two
#: is a difference in TWO-SIDEDNESS alone. Each arm differs from exactly one
#: other arm in exactly one dimension, which is what makes either
#: comparison attributable. Fifteen simulations in all: five arms times
#: three fill scenarios.
SHADOW_VARIANTS = tuple(
    [(p, p, AT_TOUCH) for p in sorted(POLICIES)]
    + [(variant_label(BASELINE_POLICY, ONE_TICK_BEHIND),
        BASELINE_POLICY, ONE_TICK_BEHIND),
       # the two-sided arm, at the SAME offset as the arm above it, so the
       # pair is a controlled comparison of two-sidedness alone.
       (variant_label(BASELINE_POLICY, TWO_SIDED),
        BASELINE_POLICY, TWO_SIDED),
       # and the ladder, which quotes the free side while flat. Same
       # BASELINE policy, so behaviour AFTER a fill is inventory-aware and
       # identical to the other arms'; only the flat state differs.
       (variant_label(BASELINE_POLICY, TWO_SIDED_LADDER),
        BASELINE_POLICY, TWO_SIDED_LADDER)])

#: the arm pair the two-sided comparison is about, CONTROL FIRST.
#:
#: Both run the BASELINE policy at the SAME offset, so a difference between
#: them is a difference in two-sidedness alone. ARGMAX at the touch is a
#: different depth and is NOT a valid control for this question.
TWO_SIDED_CONTROL = variant_label(BASELINE_POLICY, ONE_TICK_BEHIND)
TWO_SIDED_TREATMENT = variant_label(BASELINE_POLICY, TWO_SIDED)
#: the arm that actually quotes both sides while flat
LADDER_TREATMENT = variant_label(BASELINE_POLICY, TWO_SIDED_LADDER)
TWO_SIDED_ARMS = (TWO_SIDED_CONTROL, TWO_SIDED_TREATMENT)

#: WHAT EACH PAIRING ISOLATES, stated so a difference is never attributed
#: to more than the one thing that actually varies. Both treatments share
#: the control's policy and nominal offset.
TWO_SIDED_COMPARISONS = (
    (TWO_SIDED_TREATMENT,
     "PERMISSION to rest a second side (the arm never seeks one)"),
    (LADDER_TREATMENT,
     "QUOTING both sides while flat, plus that permission"),
)

#: the buckets `already_resting` splits into, which is requirement 1
RESTING_SPLIT = ("already_resting_opposite_side",
                 "already_resting_same_side",
                 "already_resting_side_unknown")

#: below this many COMMON settled windows the comparison is preliminary and
#: authorizes nothing. The baseline measurement ran over 475 windows; a
#: handful of windows cannot separate a policy from the weather.
TWO_SIDED_MIN_COMMON_WINDOWS = 200


def _num(v: Any) -> D:
    """A missing or unrecorded number is NOT a zero measurement, but it
    contributes nothing to a sum. Callers that must distinguish the two read
    the `windows_without_split` counts beside the totals."""
    if v is None or v == "NOT_RECORDED":
        return D(0)
    return D(str(v))


def _q(v: Optional[D], places: str = "0.0001") -> Optional[str]:
    return None if v is None else str(v.quantize(D(places)))


def _arm_scenario(report: Mapping[str, Any],
                  scenario: str) -> Mapping[str, Any]:
    return (((report.get("economics") or {}).get("per_scenario") or {})
            .get(scenario) or {})


def _arm_windows(e: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {w["market"]: w for w in (e.get("per_window") or [])
            if w.get("market")}


def _fold_windows(e: Mapping[str, Any],
                  slugs: List[str]) -> Dict[str, Any]:
    """Sum ONE arm over EXACTLY the given windows.

    Pair acquisition cost is EXACT rather than inferred: `settle` writes
    pair_net = matched - pair_cost against a pair redemption of exactly $1
    per unit, so the cost per pair is 1 - pair_net/matched. That is the
    figure comparable to the pair cap; pair_net alone is not, because it
    scales with volume.
    """
    have = _arm_windows(e)
    rows = [have[k] for k in slugs if k in have]
    split = [r for r in rows if r.get("pair_net") != "NOT_RECORDED"]
    matched_split = sum(int(r.get("matched_units") or 0) for r in split)
    pair_net = sum((_num(r.get("pair_net")) for r in split), D(0))
    net = sum((_num(r.get("net")) for r in rows), D(0))
    run = peak = dd = D(0)
    for r in rows:
        run += _num(r.get("net"))
        peak = max(peak, run)
        dd = max(dd, peak - run)
    return {
        "windows": len(rows),
        "windows_without_split": len(rows) - len(split),
        "net": net,
        "pair_net": pair_net,
        "residual_net": sum((_num(r.get("residual_net")) for r in split),
                            D(0)),
        "matched_units": sum(int(r.get("matched_units") or 0) for r in rows),
        "matched_units_with_split": matched_split,
        "residual_units_abs": sum(abs(int(r.get("residual_units") or 0))
                                  for r in rows),
        "capital_deployed": sum((_num(r.get("capital_deployed"))
                                 for r in rows), D(0)),
        "net_per_window": (None if not rows else net / D(len(rows))),
        "pair_acquisition_cost_per_matched": (
            None if not matched_split
            else D(1) - pair_net / D(matched_split)),
        "windows_matching_nothing": sum(
            1 for r in rows if not int(r.get("matched_units") or 0)),
        "resid_3plus_windows": sum(
            1 for r in rows if abs(int(r.get("residual_units") or 0)) >= 3),
        "max_drawdown": dd}


def _fold_execution(e: Mapping[str, Any]) -> Dict[str, Any]:
    """LIFETIME execution counters. They are not kept per window and so
    CANNOT be restricted to the common set; every caller labels them."""
    x = dict(e.get("execution") or {})
    o = dict(e.get("opportunities") or {})
    fills = int(x.get("filled") or 0)
    adverse = _num(x.get("adverse_usd"))
    swept = int(x.get("swept_fills") or 0)
    # from `already_resting_split`, NOT from `refusal_reasons`: the reason
    # string is unchanged by design, so the split lives at the tally's top
    # level and `economics` lifts it into its own key.
    split = dict(o.get("already_resting_split") or {})
    episodes = int(o.get("already_resting_opposite_side_episodes") or 0)
    opp_windows = int(o.get("windows_with_opposite_side_want") or 0)
    reasons = dict(o.get("reasons") or {})
    return {
        "counters_since": x.get("counters_since"),
        "quotes_placed": int(x.get("quotes_placed") or 0),
        "filled": fills,
        "unfilled_at_expiry": int(x.get("unfilled_at_expiry") or 0),
        "fill_rate": x.get("fill_rate"),
        "adverse_rate": x.get("adverse_rate"),
        "adverse_usd": str(adverse),
        "adverse_per_fill": (None if not fills
                             else _q(adverse / D(fills))),
        "swept_share": (None if not fills else _q(D(swept) / D(fills))),
        "controller_errors": int(x.get("controller_errors") or 0),
        "windows_observed": int(e.get("windows_observed") or 0),
        "already_resting": int(o.get("already_resting_total") or 0),
        "resting_split": {k: int(split.get(k) or 0) for k in RESTING_SPLIT},
        "opposite_side_episodes": episodes,
        "windows_with_opposite_side_want": opp_windows,
        # THE LADDER'S MECHANICAL VALIDITY, which requirement 8 asks be
        # checked EARLY: if it never quotes a second leg, or never holds
        # two sides in a window, it is not testing anything and should be
        # rejected rather than left running.
        "ladder_second_legs_quoted": int(
            o.get("ladder_second_legs_quoted") or 0),
        "windows_with_both_sides_resting": int(
            o.get("windows_with_both_sides_resting") or 0),
        "ladder_depth_hist": dict(o.get("ladder_depth_hist") or {}),
        "ladder_refused_pair_above_cap": int(
            reasons.get("ladder_pair_above_cap") or 0),
        "ladder_refused_depth_not_priceable": int(
            reasons.get("ladder_depth_not_priceable") or 0),
        "already_resting_split_accounts_for_the_bucket": (
            sum(int(split.get(k) or 0) for k in RESTING_SPLIT)
            == int(o.get("already_resting_total") or 0)),
        "both_sides_filled_same_tick": int(
            x.get("both_sides_filled_same_tick") or 0)}


def _req1(x: Mapping[str, Any]) -> Dict[str, Any]:
    """Requirement 1 for one arm, with the two denominators kept apart.

    `lifetime_bucket` has been counted since `counters_since`; the SPLIT has
    only been counted since the split existed. Dividing one by the other
    would understate the opposite-side share by however long the bucket ran
    unclassified, so the share is taken over the classified refusals and the
    mismatch is reported rather than hidden.
    """
    split = dict(x.get("resting_split") or {})
    classified = sum(split.values())
    opp = int(split.get("already_resting_opposite_side") or 0)
    wins = int(x.get("windows_observed") or 0)
    opp_wins = int(x.get("windows_with_opposite_side_want") or 0)
    return dict(split, **{
        "classified_ticks": classified,
        "lifetime_bucket": int(x.get("already_resting") or 0),
        "split_covers_whole_bucket": bool(
            x.get("already_resting_split_accounts_for_the_bucket")),
        # KEPT, and labelled for what it is. It was the first number I
        # reported and it is the one that moves on its own.
        "opposite_side_share_of_classified_TICKS": (
            None if not classified else _q(D(opp) / D(classified))),
        # THE DENOMINATORS A RATE CAN ACTUALLY BE READ AGAINST
        "opposite_side_episodes": int(x.get("opposite_side_episodes") or 0),
        "windows_with_opposite_side_want": opp_wins,
        "windows_observed": wins,
        "share_of_windows_wanting_the_other_side": (
            None if not wins else _q(D(opp_wins) / D(wins))),
        "counters_since": x.get("counters_since")})


def fee_safe_ladder_depth(book: Mapping[str, Any], cap: D,
                          floor_ticks: int = 1) -> Optional[int]:
    """How many ticks behind EACH touch a two-sided ladder must rest for the
    planned pair to clear `cap`.

    With both legs d ticks behind their own touch and bid/ask b and a, the
    planned pair is (b - d*t) + (1 - a - d*t) = 1 - (a - b) - 2*d*t. The bid
    CANCELS, so this is an identity in the spread alone, not an average over
    books: at one tick of spread d=1 plans 0.97 and d=2 plans 0.95.

    Returns None when the book has no two-sided touch to measure.
    """
    bid, ask = _d(book.get("bid")), _d(book.get("ask"))
    if bid is None or ask is None:
        return None
    t = l5d.TICK
    need = D(0)
    # smallest d with 1 - (ask-bid) - 2*d*t <= cap, in exact ticks
    gap = (D(1) - (ask - bid) - cap)
    if gap > 0:
        need = (gap / (2 * t))
        # ceiling in Decimal, without float
        if need != need.to_integral_value():
            need = need.to_integral_value(rounding="ROUND_CEILING")
    return max(int(floor_ticks), int(need))


def _common_windows(reports: Mapping[str, Any], scenario: str,
                    treatment: str = TWO_SIDED_TREATMENT
                    ) -> Tuple[List[str], Dict[str, Any]]:
    """The slugs BOTH arms report a settled line for.

    THE RESTRICTION IS THE WHOLE POINT. Each arm keeps its own durable
    state and folds older windows into a carry TOTAL, so the two arms'
    lifetime figures can cover different window sets and differencing them
    would compare different weather.
    """
    have = {a: _arm_windows(_arm_scenario(reports.get(a) or {}, scenario))
            for a in TWO_SIDED_ARMS}
    c, t = have[TWO_SIDED_CONTROL], have[TWO_SIDED_TREATMENT]
    return sorted(set(c) & set(t)), {
        "control_settled": len(c),
        "treatment_settled": len(t),
        "control_only": len(set(c) - set(t)),
        "treatment_only": len(set(t) - set(c)),
        "control_folded_into_carry": _arm_scenario(
            reports.get(TWO_SIDED_CONTROL) or {},
            scenario).get("windows_folded_into_carry"),
        "treatment_folded_into_carry": _arm_scenario(
            reports.get(treatment) or {},
            scenario).get("windows_folded_into_carry"),
        "labels": ("a window present for only one arm is EXCLUDED from "
                   "every figure here; a carry total cannot be restricted "
                   "to common windows at all, which is why lifetime totals "
                   "are context and never the comparison")}


def compare_two_sided(reports: Mapping[str, Any],
                      treatment: str = TWO_SIDED_TREATMENT,
                      isolates: str = "") -> Dict[str, Any]:
    """ARGMAX@ONE_TICK_BEHIND versus ARGMAX@TWO_SIDED, same states.

    Takes `{label: report()}` and answers, in MONEY, what two-sidedness is
    worth -- plus requirement 1, how many of the ONE-SIDED arm's own
    `already_resting` refusals wanted the side it was not resting.

    WHAT THIS CANNOT SAY. Every fill behind these numbers is MODELLED by
    the queue-aware model under %s, where an unknown queue fills only when
    a print sweeps the level. A touch is not a fill. Nothing here is
    realized, filled or even submitted.
    """ % HEADLINE_SCENARIO
    _arms = (TWO_SIDED_CONTROL, treatment)
    missing = [a for a in _arms if a not in reports]
    out: Dict[str, Any] = {
        "control": TWO_SIDED_CONTROL,
        "treatment": treatment,
        "isolates": isolates,
        "arms_missing": missing,
        "two_sided_flag": {a: (reports.get(a) or {}).get("two_sided")
                           for a in _arms},
        "offset_ticks": {a: (reports.get(a) or {}).get("offset_ticks")
                         for a in _arms},
        "fill_rule": FILL_RULE,
        "headline_scenario": HEADLINE_SCENARIO}
    if missing:
        out["verdict"] = "ARM_NOT_RUNNING"
        return out
    # THE CONTROLLED-COMPARISON PRECONDITION, asserted rather than assumed:
    # if the two arms ever stop sharing an offset, or both report the same
    # two_sidedness, the pair no longer isolates two-sidedness and the
    # difference below is not attributable to it.
    offs = set(out["offset_ticks"].values())
    flags = set(out["two_sided_flag"].values())
    out["controlled"] = (len(offs) == 1 and flags == {False, True})
    per = {}
    for scenario in SCENARIOS:
        slugs, cov = _common_windows(reports, scenario,
                                     treatment)
        ec = _arm_scenario(reports[TWO_SIDED_CONTROL], scenario)
        et = _arm_scenario(reports[treatment], scenario)
        c, t = _fold_windows(ec, slugs), _fold_windows(et, slugs)
        xc, xt = _fold_execution(ec), _fold_execution(et)
        inc = t["net"] - c["net"]
        dcap = t["capital_deployed"] - c["capital_deployed"]
        per[scenario] = {
            "common_windows": len(slugs),
            "coverage": cov,
            # REQUIREMENT 1, answered on the CONTROL arm: the question is
            # how many of the ONE-SIDED policy's refusals a two-sided arm
            # could have acted on. The treatment's own count sits beside it
            # and should be far smaller -- that is the change working.
            "requirement_1_already_resting": {
                "control": _req1(xc),
                "treatment": _req1(xt),
                "labels": ("OPPOSITE_SIDE is the only bucket a two-sided "
                           "arm could have acted on. SAME_SIDE is a "
                           "re-quote the one-order limit was right to "
                           "refuse and SIDE_UNKNOWN is admitted ignorance; "
                           "neither is a forgone opportunity. The SHARE is "
                           "over the CLASSIFIED refusals, never over the "
                           "lifetime bucket: the bucket has been counted "
                           "since the counters began and the split only "
                           "since the split existed, so "
                           "`split_covers_whole_bucket` is expected to be "
                           "false until the two share a start and the "
                           "share must not be read against "
                           "`lifetime_bucket`. AND THE TICK SHARE IS THE "
                           "WRONG RATE: a tick count measures how long an "
                           "order sat there, so OPPOSITE_SIDE froze at 17 "
                           "while SAME_SIDE ran 60->76->95 over three "
                           "minutes and the share fell 0.2208->0.1518 with "
                           "nothing changing. Read "
                           "`share_of_windows_wanting_the_other_side` and "
                           "`opposite_side_episodes` instead.")},
            "economic_value": {
                "control_net": str(c["net"]),
                "treatment_net": str(t["net"]),
                "incremental_net": str(inc),
                "incremental_net_per_common_window": _q(
                    None if not slugs else inc / D(len(slugs))),
                "control_net_per_window": _q(c["net_per_window"]),
                "treatment_net_per_window": _q(t["net_per_window"]),
                "labels": ("THIS is the incremental economic value. Order "
                           "count and completion rate are below and are "
                           "NOT it.")},
            "matched": {
                "control_matched_units": c["matched_units"],
                "treatment_matched_units": t["matched_units"],
                "incremental_matched_units": (t["matched_units"]
                                              - c["matched_units"]),
                "control_windows_matching_nothing":
                    c["windows_matching_nothing"],
                "treatment_windows_matching_nothing":
                    t["windows_matching_nothing"],
                "control_pair_acquisition_cost_per_matched": _q(
                    c["pair_acquisition_cost_per_matched"]),
                "treatment_pair_acquisition_cost_per_matched": _q(
                    t["pair_acquisition_cost_per_matched"]),
                "control_matched_units_with_split":
                    c["matched_units_with_split"],
                "treatment_matched_units_with_split":
                    t["matched_units_with_split"],
                "labels": ("matched units are MODELLED executable pairs "
                           "under the conservative queue rule, not venue "
                           "fills. Acquisition cost is per PAIR on the "
                           "average-cost basis, comparable to the pair "
                           "cap, over the windows that HAVE a split.")},
            "unmatched_inventory": {
                "control_residual_units_abs": c["residual_units_abs"],
                "treatment_residual_units_abs": t["residual_units_abs"],
                "control_resid_3plus_windows": c["resid_3plus_windows"],
                "treatment_resid_3plus_windows": t["resid_3plus_windows"],
                "control_residual_net": str(c["residual_net"]),
                "treatment_residual_net": str(t["residual_net"]),
                "labels": ("the 3+ stratum is where the measured loss "
                           "lives; two-sidedness must not merely move "
                           "windows into it")},
            "pair_economics": {
                "control_pair_net": str(c["pair_net"]),
                "treatment_pair_net": str(t["pair_net"]),
                "control_windows_without_split": c["windows_without_split"],
                "treatment_windows_without_split":
                    t["windows_without_split"]},
            "risk": {
                "control_capital_deployed": str(c["capital_deployed"]),
                "treatment_capital_deployed": str(t["capital_deployed"]),
                "control_max_drawdown": str(c["max_drawdown"]),
                "treatment_max_drawdown": str(t["max_drawdown"]),
                "incremental_capital_deployed": str(dcap),
                "incremental_net_per_incremental_dollar": _q(
                    None if dcap == 0 else inc / dcap)},
            "adverse_selection": {
                "control_adverse_rate": xc["adverse_rate"],
                "treatment_adverse_rate": xt["adverse_rate"],
                "control_adverse_per_fill": xc["adverse_per_fill"],
                "treatment_adverse_per_fill": xt["adverse_per_fill"],
                "control_swept_share": xc["swept_share"],
                "treatment_swept_share": xt["swept_share"],
                "labels": ("LIFETIME, not restricted to common windows. "
                           "Measured from PLACEMENT to window close "
                           "against our own outcome. A high swept share "
                           "means the conservative assumption binds.")},
            "fill_model": {
                "control": {k: v for k, v in xc.items()
                            if k != "resting_split"},
                "treatment": {k: v for k, v in xt.items()
                              if k != "resting_split"},
                "labels": "LIFETIME counters, read as rates."}}
    # THE LABELS ARE IDENTICAL IN EVERY SCENARIO, and repeating them three
    # times per comparison took the document to 34,083 characters -- which
    # is how the heartbeat got to a quarter of a megabyte in the first
    # place. They are kept in FULL on the headline scenario, which is the
    # one promotion is judged on and therefore the one that gets read, and
    # replaced by a pointer elsewhere. No figure is dropped.
    for scen, block in per.items():
        if scen != HEADLINE_SCENARIO:
            _point_labels(block, HEADLINE_SCENARIO)
    head = per[HEADLINE_SCENARIO]
    n = head["common_windows"]
    inc = D(head["economic_value"]["incremental_net"])
    out["per_scenario"] = per
    out["verdict"] = (
        "ARMS_NOT_CONTROLLED" if not out["controlled"] else
        "INSUFFICIENT_COMMON_WINDOWS" if n < TWO_SIDED_MIN_COMMON_WINDOWS
        else "TWO_SIDED_BETTER" if inc > 0 else "TWO_SIDED_NOT_BETTER")
    out["min_common_windows"] = TWO_SIDED_MIN_COMMON_WINDOWS
    out["labels"] = (
        "MODELLED, queue-aware, headline %s. Not realized, not filled, "
        "not submitted. A positive incremental figure below %d common "
        "windows is PRELIMINARY and authorizes nothing."
        % (HEADLINE_SCENARIO, TWO_SIDED_MIN_COMMON_WINDOWS))
    return out


def _point_labels(node: Any, headline: str) -> None:
    """Replace every `labels` string in a nested block with a pointer to
    the headline scenario's copy, in place. Only prose moves; not one
    number is touched."""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k == "labels" and isinstance(v, str):
                node[k] = "see %s" % headline
            else:
                _point_labels(v, headline)
    elif isinstance(node, list):
        for v in node:
            _point_labels(v, headline)


def compare_all(reports: Mapping[str, Any]) -> Dict[str, Any]:
    """Both pairings against the one control, in one emittable document.

    The owner asked for the new policy to be compared with the EXISTING
    control, and that is the `ARGMAX@TWO_SIDED_LADDER` entry. The
    `ARGMAX@TWO_SIDED` entry is kept beside it because the two treatments
    differ from each other by the SEEKING alone, so the pair of
    comparisons separates permission from pursuit instead of confounding
    them in a single number.

    `mechanics` is the requirement-8 early check, hoisted to the top of
    the document so it is read before any economics: if the ladder never
    quotes a second leg or never holds two sides in a window, it is
    testing nothing and should be rejected rather than left running.
    """
    out: Dict[str, Any] = {"control": TWO_SIDED_CONTROL,
                           "comparisons": {}}
    for treatment, isolates in TWO_SIDED_COMPARISONS:
        out["comparisons"][treatment] = compare_two_sided(
            reports, treatment, isolates)
    lad = _fold_execution(_arm_scenario(reports.get(LADDER_TREATMENT) or {},
                                        HEADLINE_SCENARIO))
    ctl = _fold_execution(_arm_scenario(reports.get(TWO_SIDED_CONTROL) or {},
                                        HEADLINE_SCENARIO))
    legs = lad["ladder_second_legs_quoted"]
    both = lad["windows_with_both_sides_resting"]
    out["mechanics"] = {
        "ladder_arm_present": LADDER_TREATMENT in reports,
        "ladder_from_flat": (reports.get(LADDER_TREATMENT)
                             or {}).get("ladder_from_flat"),
        "ladder_second_legs_quoted": legs,
        "windows_with_both_sides_resting": both,
        "ladder_refused_pair_above_cap": lad["ladder_refused_pair_above_cap"],
        "ladder_refused_depth_not_priceable":
            lad["ladder_refused_depth_not_priceable"],
        "ladder_quotes_placed": lad["quotes_placed"],
        # the second leg's DEPTH, because the pair bound usually puts it
        # deeper than the control's single leg and a deeper leg fills less
        # often by arithmetic. Without this a lower fill rate on the second
        # leg would read as a property of two-sidedness.
        "ladder_depth_hist": lad["ladder_depth_hist"],
        "control_offset_ticks": (reports.get(TWO_SIDED_CONTROL)
                                 or {}).get("offset_ticks"),
        "control_quotes_placed": ctl["quotes_placed"],
        "ladder_windows_observed": lad["windows_observed"],
        "control_second_legs_quoted": ctl["ladder_second_legs_quoted"],
        "verdict": (
            "ARM_NOT_RUNNING" if LADDER_TREATMENT not in reports else
            "NO_DATA_YET" if not lad["quotes_placed"] else
            # it quoted, and never got a second leg down: either the cap
            # refuses it everywhere or the state never arises. Both mean
            # the arm is not testing the hypothesis.
            "MECHANICALLY_INERT" if not legs else
            "QUOTES_BOTH_SIDES" if both else "SECOND_LEGS_BUT_NEVER_BOTH"),
        "labels": ("REQUIREMENT 8, read FIRST. A ladder that never quotes "
                   "a second leg tests nothing, however good its "
                   "economics look. `control_second_legs_quoted` must be "
                   "0: a non-zero value means the control is not the "
                   "one-sided arm it is supposed to be.")}
    return out


class ShadowController:
    """One inventory-driven decision per tick, for one market at a time."""

    def __init__(self, *, state_path: str,
                 model: Optional[Mapping[str, Any]] = None,
                 pair_cap: Optional[D] = None,
                 policy: str = BASELINE_POLICY,
                 placement: str = AT_TOUCH,
                 emit: Optional[Callable[[str, Mapping[str, Any]], None]] = None
                 ) -> None:
        self.state_path = str(state_path)
        if policy not in POLICIES:
            raise ValueError("unknown policy: %r" % (policy,))
        if placement not in PLACEMENTS:
            raise ValueError("unknown placement: %r" % (placement,))
        #: AT_TOUCH by default, so the three deployed arms are unchanged by
        #: the existence of this dimension. The comparison arm is a FOURTH
        #: controller with its own durable state, not a new axis threaded
        #: through theirs.
        self.placement = str(placement)
        self.offset_ticks = PLACEMENT_TICKS[self.placement]
        self.two_sided = PLACEMENT_TWO_SIDED[self.placement]
        self.ladder_from_flat = PLACEMENT_LADDER_FROM_FLAT[self.placement]
        #: THE VARIANT'S OWN NAME, on every record. `policy` alone cannot
        #: identify a variant once two share a policy: the placement
        #: variant IS ARGMAX, and a consumer filtering on policy would
        #: silently merge it with the touch variant and compare the
        #: comparison against itself.
        #:
        #: NOT CALLED `arm`. In this codebase "arm" means arming the
        #: transport, and FORBIDDEN_ATTRS reserves the word on this object
        #: precisely so that an order-path capability cannot hide behind
        #: it. An experiment arm is a different thing wearing the same
        #: word, so it gets a different one. The prose still says "arm".
        self.variant = variant_label(str(policy), self.placement)
        #: named on every record, and each policy keeps its OWN inventory in
        #: its OWN state file, so two of these can run side by side without
        #: one's simulated fills contaminating the other's decisions.
        self.policy = str(policy)
        self.policy_fn = POLICIES[self.policy]
        self._model = model
        # BTC15m completes at the spring target, not the incentive-era
        # near-$1 tolerance. None would mean the sports cap.
        self.pair_cap = l5d.BTC15M_PAIR_CAP if pair_cap is None else pair_cap
        self._emit = emit
        self.state: Dict[str, Any] = {"v": STATE_VERSION, "markets": {},
                                      "tally_taxonomy": TALLY_TAXONOMY}
        self.decisions = 0
        self.records = 0
        self.counts: Dict[str, int] = {}
        #: LIVE ORDER OBJECTS, DELIBERATELY NOT PERSISTED. A HypoOrder's
        #: value is its queue position as captured at placement, and that
        #: cannot be reconstructed after a restart. Persisting a summary and
        #: resuming it would mean crediting a fill against a queue we can no
        #: longer see, so a restart drops every resting order instead and
        #: the next tick quotes afresh. Dropping is the conservative
        #: direction: a dropped order can never become a fill.
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._seen: Dict[str, set] = {}
        self._load()

    # ------------------------------------------------------------ the model
    @property
    def model(self) -> Mapping[str, Any]:
        """Loaded lazily and cached: a missing artifact must not stop the
        runner's tick, it must stop only this controller."""
        if self._model is None:
            self._model = pv2.load_v2()
        return self._model

    # -------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(doc, Mapping):
            return
        if doc.get("v") == STATE_VERSION:
            self.state = {"v": STATE_VERSION,
                          "markets": dict(doc.get("markets") or {})}
            # `tally` MUST be restored. _save writes it and _load used to
            # drop it, so every deploy silently reset the execution
            # counters while `windows_settled` kept persisting -- leaving
            # fill_rate and adverse_rate describing a few minutes while the
            # settled windows beside them described hours. Two numbers over
            # two different periods in one report is worse than no numbers.
            for k in ("carry", "discontinuity", "tally", "counters_since"):
                if isinstance(doc.get(k), Mapping):
                    self.state[k] = dict(doc[k])
                elif k == "counters_since" and doc.get(k):
                    self.state[k] = doc[k]
            if int(doc.get("tally_taxonomy") or 0) != TALLY_TAXONOMY:
                # counts bucketed by a different taxonomy are not comparable
                # with counts bucketed by this one, and summing them would
                # present two definitions as one number.
                dropped = self.state.pop("tally", None)
                self.state.pop("counters_since", None)
                self.state["tally_reset"] = {
                    "from_taxonomy": int(doc.get("tally_taxonomy") or 0),
                    "to_taxonomy": TALLY_TAXONOMY,
                    "dropped_scenarios": sorted(dropped or {}),
                    "reason": ("the refusal taxonomy changed, so restored "
                               "counts were bucketed by rules this build no "
                               "longer uses. Discarded rather than merged. "
                               "Settled-window economics are unaffected: "
                               "they are priced from inventory, not from "
                               "these counters."),
                }
            self.state["tally_taxonomy"] = TALLY_TAXONOMY
            self.state["boot"] = self._drop_stale_resting()
            return
        # A STATE DISCONTINUITY, RECORDED RATHER THAN ABSORBED.
        #
        # v2 kept ONE inventory per market under the touch-through rule.
        # Carrying it into v3 would mean seeding the CONSERVATIVE column
        # with inventory that only an OPTIMISTIC rule ever credited --
        # overstating exactly the number promotion is decided on. Seeding
        # only OPTIMISTIC would leave the three scenarios measured over
        # different window sets, which breaks the comparison the owner
        # asked for.
        #
        # So all three scenarios start empty, and what was there is
        # preserved as a summary so nothing is destroyed silently.
        if doc.get("v") == 2:
            prior = {}
            for slug, slot in (doc.get("markets") or {}).items():
                prior[slug] = {
                    "yes": slot.get("yes"), "no": slot.get("no"),
                    "yes_cost": slot.get("yes_cost"),
                    "no_cost": slot.get("no_cost"),
                    "settled": bool(slot.get("settled"))}
            self.state["discontinuity"] = {
                "from_version": 2, "to_version": STATE_VERSION,
                "reason": ("v2 inventory was credited by the touch-through "
                           "upper bound; seeding the conservative scenario "
                           "from it would overstate the column promotion is "
                           "decided on, and seeding only optimistic would "
                           "leave the scenarios non-comparable"),
                "markets_discarded": len(prior),
                "prior_state": prior,
                "carry_discarded": dict(doc.get("carry") or {}),
                "labels": ("simulated inventory only. Nothing realized, "
                           "submitted or owned was affected: this object "
                           "has never held a position.")}

    def _drop_stale_resting(self) -> Dict[str, Any]:
        """A restart drops the ORDER OBJECTS; this drops the record of them.

        `_orders` is deliberately not persisted, because a HypoOrder's
        whole value is the queue position captured at placement and that
        cannot be reconstructed. The next tick therefore quotes afresh --
        `already_resting` is keyed on `_orders`, not on this record, so
        nothing is frozen.

        But the per-scenario `resting` record DID persist, and a stale one
        is a reporting lie waiting to happen: the next `window_close` would
        publish it as `dropped_resting`, attributing to expiry a quote the
        RESTART dropped. Inventory is untouched -- only the unreconstructable
        record goes -- and the count is published in the heartbeat so the
        drop is visible rather than silent. Dropping is the conservative
        direction: a dropped order can never become a fill.
        """
        dropped = []
        for slug, slot in (self.state.get("markets") or {}).items():
            for scenario in SCENARIOS:
                sc = (slot.get("scen") or {}).get(scenario)
                if isinstance(sc, dict) and sc.get("resting") is not None:
                    dropped.append("%s|%s" % (slug, scenario))
                    sc["resting"] = None
                    sc["resting_sides"] = None
        return {"dropped_resting_records": len(dropped),
                "dropped": sorted(dropped)[:STRANDED_REPORT_CAP],
                "inventory_carried": True,
                "labels": ("a restart cannot reconstruct a queue position, "
                           "so the resting RECORD is cleared with its order "
                           "object. Simulated inventory and costs carry "
                           "across unchanged; nothing was credited and "
                           "nothing was discarded.")}

    def _prune(self) -> None:
        """Keep the state file bounded WITHOUT losing a cent of any
        scenario's net.

        One slot per 15-minute window is 96 a day forever. Dropping the
        oldest outright would quietly shrink every scenario's modelled net
        toward zero -- the same error as zeroing a stale record -- so each
        dropped window's totals are folded into a PER-SCENARIO carry that
        `economics` counts alongside the windows still held. Only
        per-window detail ages out, and the report says how much.
        """
        mk = self.state.get("markets") or {}
        done = [(k, v) for k, v in mk.items()
                if all(((v.get("scen") or {}).get(sc) or {}).get("settled")
                       for sc in SCENARIOS)]
        if len(done) <= SETTLED_KEEP:
            return
        done.sort(key=lambda kv: (float(kv[1].get("window_end_ts") or 0.0),
                                  kv[0]))
        carry = self.state.setdefault("carry", {})
        for key, slot in done[:len(done) - SETTLED_KEEP]:
            for sc in SCENARIOS:
                line = ((slot.get("scen") or {}).get(sc) or {}).get("settled")
                if not line:
                    continue
                c = carry.setdefault(sc, {"windows": 0, "cost_total": "0",
                                          "redemption_total": "0",
                                          "matched_units": 0,
                                          "residual_units_abs": 0})
                for k in ("pair_net", "residual_net"):
                    c.setdefault(k, "0")
                    c[k] = str(D(c[k]) + D(str(line.get(k) or 0)))
                c["windows"] = int(c["windows"]) + 1
                c["cost_total"] = str(D(c["cost_total"])
                                      + D(str(line.get("cost_total") or 0)))
                c["redemption_total"] = str(
                    D(c["redemption_total"])
                    + D(str(line.get("redemption_total") or 0)))
                c["matched_units"] = (int(c["matched_units"])
                                      + int(line.get("matched_units") or 0))
                c["residual_units_abs"] = (
                    int(c["residual_units_abs"])
                    + abs(int(line.get("residual_units") or 0)))
            mk.pop(key, None)

    def _save(self) -> None:
        """Atomic: a crash mid-write must not leave unreadable state, which
        would silently reset simulated inventory and flatter the record."""
        self._prune()
        d = os.path.dirname(self.state_path) or "."
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
        except OSError:
            pass                      # state is a diagnostic, never a gate

    # ------------------------------------------------- per-side resting
    #
    # `sc["resting"]` IS PERSISTED, and older state files hold either None or
    # a single {"side":...,"price":...} dict from when one order could rest.
    # Both shapes are migrated on read rather than discarded, because the
    # inventory beside them is real.
    @staticmethod
    def _resting_map(sc: Dict[str, Any]) -> Dict[str, Any]:
        """Per-side resting metadata, keyed by FM.UP / FM.DOWN.

        `sc["resting"]` KEEPS ITS ORIGINAL SHAPE -- one dict or None -- so
        every existing reader, report and test sees exactly what it saw
        before. The per-side map lives beside it under `resting_sides` and is
        migrated from the legacy single on first read, because the inventory
        next to it is real and must not be discarded.
        """
        m = sc.get("resting_sides")
        if isinstance(m, Mapping) and m:
            return {str(k): v for k, v in m.items() if v is not None}
        r = sc.get("resting")
        if isinstance(r, Mapping) and r.get("_side_key"):
            return {str(r["_side_key"]): dict(r)}
        if isinstance(r, Mapping):                    # legacy, side unknown
            return {FM.UP if str(r.get("side", "")).upper() in ("YES", "UP")
                    else FM.DOWN: dict(r)}
        return {}

    @staticmethod
    def _set_resting(sc: Dict[str, Any], m: Mapping[str, Any]) -> None:
        clean = {k: v for k, v in m.items() if v is not None}
        sc["resting_sides"] = clean or None
        # the legacy single view: the most recently placed side, or None.
        if not clean:
            sc["resting"] = None
        else:
            newest = max(clean.values(), key=lambda v: (v or {}).get("t") or 0)
            sc["resting"] = dict(newest)

    def _orders_for(self, slug: str, scenario: str) -> Dict[str, Any]:
        """This scenario's live order objects, keyed by side. Not persisted."""
        per = self._orders.setdefault(slug, {})
        cur = per.get(scenario)
        if cur is None:
            cur = {}
            per[scenario] = cur
        elif not isinstance(cur, dict):           # legacy single order object
            cur = {getattr(cur, "side", FM.UP): cur}
            per[scenario] = cur
        return cur

    # ---------------------------------------------------------- inventory
    @staticmethod
    def _fresh_scenario() -> Dict[str, Any]:
        return {"yes": 0, "no": 0, "yes_cost": "0", "no_cost": "0",
                "last_rel": "", "resting": None, "resting_sides": None,
                # episode bookkeeping for the opposite-side want. Per
                # MARKET, so a fresh window starts a fresh count and
                # `windows_with_opposite_side_want` counts windows.
                "last_resting_diag": "", "opp_want_seen_this_window": False,
                "both_sides_seen_this_window": False,
                "settled": None}

    def _slot(self, slug: str) -> Dict[str, Any]:
        m = self.state.setdefault("markets", {})
        slot = m.setdefault(slug, {"window_end_ts": None,
                                   "window_source": None, "scen": {}})
        scen = slot.setdefault("scen", {})
        for sc in SCENARIOS:
            scen.setdefault(sc, self._fresh_scenario())
        return slot

    def _scen(self, slug: str, scenario: str) -> Dict[str, Any]:
        return self._slot(slug)["scen"][scenario]

    def inventory_for(self, slug: str,
                      scenario: str = HEADLINE_SCENARIO) -> l5d.Inventory:
        s = self._scen(slug, scenario)
        inv = l5d.Inventory()
        inv.yes, inv.no = int(s["yes"]), int(s["no"])
        inv.yes_cost, inv.no_cost = D(s["yes_cost"]), D(s["no_cost"])
        return inv

    def _store_inventory(self, slug: str, inv: l5d.Inventory,
                         scenario: str = HEADLINE_SCENARIO) -> None:
        s = self._scen(slug, scenario)
        s["yes"], s["no"] = inv.yes, inv.no
        s["yes_cost"], s["no_cost"] = str(inv.yes_cost), str(inv.no_cost)

    # ------------------------------------------------------- the fill rule
    def _consume_prints(self, slug: str, prints: Any) -> list:
        """New trade prints since our last read, by ROW IDENTITY.

        Both obvious cursors are wrong and `l5_maker_fill_model` documents
        why: an INDEX cursor freezes once the recorder's rolling 500-row
        list hits its cap, and a TIMESTAMP cursor drops every print after
        the first in each frame because the recorder stamps frame arrival.
        So rows are identified, and `FM.ModelSession.row_id` is reused
        rather than a second identity function invented here.

        A seen-set cannot detect a buffer that rolled past us, so coverage
        is not claimed to be complete; what it does guarantee is that no
        print is counted twice, which is the direction that matters for a
        fill.
        """
        if not prints:
            return []
        seen = self._seen.setdefault(slug, set())
        fresh = []
        for row in prints:
            try:
                rid = FM.ModelSession.row_id(row)
            except BaseException:                              # noqa: BLE001
                continue
            if rid in seen:
                continue
            seen.add(rid)
            fresh.append(row)
        if len(seen) > 4000:                 # bounded; oldest identity wins
            self._seen[slug] = set(list(seen)[-2000:])
        return fresh

    def _ladder_leg(self, free: str, sc: Dict[str, Any],
                    book: Mapping[str, Any]) -> Dict[str, Any]:
        """Quote the FREE side on its own merits, while flat.

        A plan-shaped dict, so the normal placement and order-construction
        path below handles it unchanged -- the only thing that differs is
        that the side was chosen because it is FREE rather than because the
        inventory policy asked for it.

        PRICED FROM ITS OWN SIDE OF THE BOOK. `complement_target` is not
        usable here and is deliberately not used: it prices a completion
        against the average cost already PAID on the surplus side, and a
        ladder from flat has paid nothing. So the price is this side's own
        touch, set back by the depth the PAIR BOUND requires, and the
        resulting planned pair is checked against the cap explicitly rather
        than assumed to be safe.
        """
        depth = fee_safe_ladder_depth(book, self.pair_cap, self.offset_ticks)
        if depth is None:
            return {"decision": "no_action", "reason": "no_two_sided_book"}
        side_yes = (free == FM.UP)
        touch = l5d._touch_for(side_yes, book)
        if touch is None:
            return {"decision": "no_action", "reason": "one_sided_book"}
        px = touch - (l5d.TICK * depth)
        # THE RESTING LEG'S OWN PRICE, as recorded when it was placed --
        # never re-derived from the current book, which has moved.
        other = (self._resting_map(sc)
                 .get(FM.UP if free == FM.DOWN else FM.DOWN) or {})
        rp = _d(other.get("price"))
        if rp is None:
            return {"decision": "no_action",
                    "reason": "ladder_resting_price_unknown"}
        pair = rp + px
        common = {"ladder_leg": True, "ladder_depth_ticks": depth,
                  "ladder_resting_leg_price": str(rp),
                  "ladder_planned_pair": str(pair),
                  "max_pair_cost": str(self.pair_cap)}
        if px < l5d.TICK:
            return {"decision": "no_action",
                    "reason": "ladder_depth_not_priceable", **common}
        if pair > self.pair_cap:
            # NOT a missed opportunity: a pair above the cap is not fee-safe
            # and quoting it would be the error. At one tick of spread this
            # is what a one-tick ladder would hit on every tick.
            return {"decision": "no_action",
                    "reason": "ladder_pair_above_cap", **common}
        # relation stays in the KNOWN vocabulary -- it is an open, on a side
        # we hold nothing of -- because `last_rel` feeds the v2 proposal and
        # an invented relation string would reach the model as a state it
        # has never seen.
        return {"decision": "quote", "relation": "open",
                "side": "YES" if side_yes else "NO",
                "target_price": str(px), "q1_size": 1,
                "inventory_effect": ("+1 YES" if side_yes else "+1 NO"),
                "_px": touch, "_side_yes": side_yes, "_offset_ticks": depth,
                **common}

    def _advance_resting(self, slug: str, scenario: str, book: Mapping,
                         prints: list) -> Optional[dict]:
        """Does THIS scenario's resting order fill, per the queue model?

        The order object is held in memory and carries its queue position
        AS CAPTURED AT PLACEMENT. Only trade volume advances it: a level
        shrinking is not advancement, because a cancellation ahead of us is
        indistinguishable from a fill ahead of us in the data we get, and
        assuming cancels help us is the easiest way to manufacture a fill.
        An unknown queue NEVER fills except on a sweep.
        """
        orders = self._orders_for(slug, scenario)
        if not orders:
            return []
        out = []
        for side in list(orders):
            order = orders.get(side)
            if order is None:
                orders.pop(side, None)
                continue
            if prints:
                order.advance(prints)
            if not order.filled.get(scenario):
                continue
            got = self._book_one_fill(slug, scenario, side, order, book)
            if got is not None:
                out.append(got)
        return out

    def _book_one_fill(self, slug, scenario, side, order, book):
        """One modelled fill: inventory, state, and its record. Only the
        side that filled is cleared -- the opposite side keeps resting, which
        is the whole point of the two-sided arm."""
        sc = self._scen(slug, scenario)
        inv = self.inventory_for(slug, scenario)
        inv.record(order.side == FM.UP, order.own_px)
        self._store_inventory(slug, inv, scenario)
        m = self._resting_map(sc)
        rest = m.pop(str(side), None) or {}
        self._set_resting(sc, m)
        sc["last_rel"] = str(rest.get("relation") or sc.get("last_rel") or "")
        self._orders_for(slug, scenario).pop(str(side), None)
        rec = order.record()
        return {"filled_by": FILL_RULE, "scenario": scenario,
                "side": "YES" if order.side == FM.UP else "NO",
                "price": str(order.own_px),
                "relation": rest.get("relation"),
                "queue_ahead_at_placement": rec["queue_ahead_at_placement"],
                "volume_through_level": rec["volume_through_level"],
                "ticks_behind_touch": rec["ticks_behind_touch"],
                "swept": rec["swept"],
                "adverse_selection": FM.adverse_selection(
                    order, scenario, _d(book.get("bid"))),
                "inventory_after": inv.snapshot(),
                "labels": ("MODELLED by the queue-aware model on the "
                           "authenticated ladder. Queue position frozen at "
                           "placement; only prints advance it; an unknown "
                           "queue fills only on a sweep. Still NOT a "
                           "confirmed fill and NOT realized P&L.")}

    # ------------------------------------------------------------- the tick
    def step(self, now: float, *, slug: str, book: Optional[Mapping[str, Any]],
             window_end_ts: Optional[float],
             window_source: Optional[str] = None,
             prints: Any = None,
             ladder: Any = None) -> Dict[str, Any]:
        """One decision PER SCENARIO. Returns the headline record.

        THREE TRAJECTORIES, NOT THREE VIEWS. Each scenario keeps its own
        inventory, because inventory drives the next decision: a fill that
        happens only optimistically must not make the conservative
        trajectory choose a completion it never earned. So the scenarios
        diverge in what they hold AND in what they do, and only the
        conservative one is used to judge the policy.

        `ladder` and `prints` are the authenticated book's own ladder and
        trade prints. WITHOUT THEM NOTHING DEGRADES SILENTLY: a missing
        ladder leaves queue_ahead None, and the fill model never fills an
        unknown queue except on a sweep. That is the model's rule, not a
        fallback invented here.

        Never raises into the runner.
        """
        base = {"provenance": CONTROLLER_PROVENANCE, "market": slug,
                "variant": self.variant,
                "policy": self.policy, "placement": self.placement,
                "offset_ticks": self.offset_ticks,
                "fill_rule": FILL_RULE, "window_source": window_source}
        try:
            book = normalise_book(book)
            if not book or book.get("bid") is None or book.get("ask") is None:
                return self._out({**base, "scenario": None,
                                  "decision": "no_action",
                                  "reason": "no_two_sided_book"})
            fresh = self._consume_prints(slug, prints)
            slot = self._slot(slug)
            slot["window_end_ts"] = window_end_ts
            slot["window_source"] = window_source
            tte = l5d.tte_at(window_end_ts, now)
            out = None
            for scenario in SCENARIOS:
                rec = self._step_one(now, slug, scenario, book, fresh,
                                     ladder, tte, base, window_end_ts)
                if scenario == HEADLINE_SCENARIO:
                    out = rec
            self._save()
            return out if out is not None else self._out(
                {**base, "scenario": HEADLINE_SCENARIO,
                 "decision": "no_action", "reason": "no_scenario_decided"})
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return self._out({**base, "scenario": None,
                              "decision": "no_action",
                              "reason": "controller_error:%s"
                                        % type(exc).__name__})

    def _step_one(self, now, slug, scenario, book, fresh, ladder, tte,
                  base, window_end_ts) -> Dict[str, Any]:
        """This scenario's own decision, over its own inventory."""
        fills = self._advance_resting(slug, scenario, book, fresh)
        sc = self._scen(slug, scenario)
        inv = self.inventory_for(slug, scenario)
        rec = {**base, "scenario": scenario,
               "tte_s": round(float(tte), 1),
               "inventory": inv.snapshot(),
               "window_end_ts": window_end_ts,
               # `modelled_fill` keeps its old shape -- the first fill, or
               # None -- so every existing reader is unaffected. Two sides
               # CAN fill in one tick, so the full list rides alongside and
               # the tally counts from it.
               "modelled_fill": (fills[0] if fills else None),
               "modelled_fills": fills}
        prop = l5d._proposal(self.model, float(book["bid"]), inv,
                             str(sc.get("last_rel") or ""), tte_s=tte)
        rec["state"] = prop.get("state")
        if prop.get("off_distribution"):
            return self._out({**rec, "decision": "no_action",
                              "reason": "v2_off_distribution"})
        # ONE OUTSTANDING ORDER PER SIDE -- or, on a one-sided arm, per
        # scenario, exactly as before. A scenario already resting does not
        # RE-QUOTE THE SAME SIDE: re-pricing a resting maker order toward a
        # newer price is chasing under another name, and the deployed engine
        # has no method for it. What the two-sided arm adds is the OPPOSITE
        # side, which is a different order, not a re-price.
        lad_plan: Optional[Dict[str, Any]] = None
        resting = dict(self._orders_for(slug, scenario))
        if resting:
            # WHICH side would we have wanted? The old counter could not say,
            # because this gate fired before the side was decided -- so
            # 66,044 `already_resting` refusals could not be split into
            # genuine opposite-side opportunities and mere same-side
            # re-quotes. Computed for the diagnostic only, and wrapped: a
            # modelling failure here must not become a controller_error that
            # the old code would never have raised.
            want = ""
            try:
                _rel = self.policy_fn(prop, inv, book, tte)
                _plan = l5d.plan_action(_rel, inv, book,
                                        pair_cap=self.pair_cap)
                if _plan.get("decision") == "quote":
                    want = FM.UP if _plan.get("_side_yes") else FM.DOWN
            except Exception:                              # noqa: BLE001
                want = ""
            diag = ("SIDE_UNKNOWN" if not want else
                    ("SAME_SIDE" if want in resting else "OPPOSITE_SIDE"))
            # PER-TICK COUNTS ARE THE WRONG DENOMINATOR, and the first
            # production reading showed it: over three consecutive minutes
            # OPPOSITE_SIDE sat frozen at 17 while SAME_SIDE ran 60 -> 76 ->
            # 95, so the share fell 0.2208 -> 0.1828 -> 0.1518 without a
            # single thing changing. A tick count measures HOW LONG AN ORDER
            # SAT THERE, which is a function of our poll rate; once a side
            # rests and inventory is flat, every later tick in the window is
            # SAME_SIDE by construction.
            #
            # So the episode is counted too: a new OPPOSITE_SIDE episode only
            # on the TRANSITION into it, and the window is counted once
            # however many ticks it lasts. Those are the denominators an
            # opportunity rate can be read against. The per-tick counts stay,
            # because they are what the earlier 66,044 figure was, and
            # dropping them would make the two unreconcilable.
            prev = str(sc.get("last_resting_diag") or "")
            sc["last_resting_diag"] = diag
            if diag == "OPPOSITE_SIDE" and prev != "OPPOSITE_SIDE":
                t = self._tally(scenario)
                t["already_resting_opposite_side_episodes"] += 1
                if not sc.get("opp_want_seen_this_window"):
                    sc["opp_want_seen_this_window"] = True
                    t["windows_with_opposite_side_want"] += 1
            # THE LADDER ARM GOES LOOKING. While flat with exactly one
            # side resting, the inventory policy wants that same side
            # again, so TWO_SIDED alone refuses here forever and never
            # holds two legs. This arm instead quotes the FREE side on its
            # own merits -- which is the two-sided ladder the hypothesis is
            # about, rather than permission to hold one.
            #
            # Only while FLAT. With inventory on the books the baseline
            # policy's complete/extend logic is the inventory-aware
            # behaviour we want and is left strictly alone.
            if (self.ladder_from_flat and inv.residual == 0
                    and len(resting) == 1):
                lad_plan = self._ladder_leg(
                    FM.DOWN if FM.UP in resting else FM.UP, sc, book)
                if lad_plan.get("decision") != "quote":
                    return self._out({**rec, **lad_plan,
                                      "already_resting_side": diag,
                                      "resting_sides": sorted(resting)})
            elif (not self.two_sided) or (not want) or (want in resting):
                return self._out({**rec, "decision": "no_action",
                                  "reason": "already_resting",
                                  "already_resting_side": diag,
                                  "resting_sides": sorted(resting)})
            # two-sided, and the side we want is free: fall through. The
            # episode run is deliberately NOT cleared here -- falling
            # through means we are about to place that opposite side, which
            # ends the refusal run on its own.
        else:
            # nothing resting, so the next OPPOSITE_SIDE refusal begins a
            # NEW episode rather than continuing the last one
            sc["last_resting_diag"] = ""
        if lad_plan is not None:
            # the free-side leg already IS the plan; the inventory policy
            # would only have re-chosen the side we are already resting
            plan = lad_plan
        else:
            rel = self.policy_fn(prop, inv, book, tte)
            rec["model_argmax"] = prop.get("recommended_relation")
            plan = l5d.plan_action(rel, inv, book, pair_cap=self.pair_cap)
        rec = {**rec, **l5d._public(plan)}
        if plan.get("decision") != "quote":
            return self._out(rec)
        # ----------------------------------------------------- placement
        # The ONE pre-registered change: rest one tick worse in our own
        # outcome. Applied AFTER plan_action so the decision -- side,
        # relation, cap arithmetic -- is identical to AT_TOUCH's and only
        # the resting price differs. A cheaper price can only LOWER pair
        # cost, so the 0.96 cap stays satisfied; and it rests further from
        # crossing, so `restable` cannot be made worse by it.
        px = plan["_px"]
        # THE LADDER'S DEPTH IS COMPUTED PER TICK from the live spread, so
        # the offset comes from the plan when it carries one and from the
        # placement otherwise. Every other arm is unaffected.
        offset = int(plan.get("_offset_ticks", self.offset_ticks))
        if offset:
            px = px - (l5d.TICK * offset)
            if px < l5d.TICK:
                # NEVER rounded back to the touch: an arm that cannot quote
                # at its own depth must be visible as that, not as a low
                # fill rate.
                return self._out({**rec, "decision": "no_action",
                                  "relation": plan.get("relation"),
                                  "reason": "offset_not_priceable",
                                  "target_price": str(plan["_px"])})
            rec["target_price"] = str(px)
            rec["at_touch_price"] = str(plan["_px"])
            rec["offset_ticks_applied"] = offset
        order = FM.HypoOrder(
            slug, FM.UP if plan["_side_yes"] else FM.DOWN,
            px, D(1), float(now), ladder=ladder,
            bid=book.get("bid"), ask=book.get("ask"),
            coid="%s|%s|%s" % (self.policy, scenario, now))
        # A PASSIVE ORDER THAT WOULD CROSS IS NOT A MAKER ORDER. The model
        # answers that directly, and a non-restable quote is refused rather
        # than modelled as if it had rested.
        if not order.restable:
            return self._out({**rec, "decision": "no_action",
                              "relation": plan.get("relation"),
                              "reason": "would_cross_not_a_maker_quote"})
        self._orders_for(slug, scenario)[str(order.side)] = order
        m = self._resting_map(sc)
        m[str(order.side)] = {"side": plan["side"], "price": str(px),
                              "relation": plan.get("relation"), "t": now,
                              "_side_key": str(order.side)}
        self._set_resting(sc, m)
        if plan.get("ladder_leg"):
            t = self._tally(scenario)
            t["ladder_second_legs_quoted"] += 1
            dk = str(plan.get("ladder_depth_ticks"))
            t["ladder_depth_hist"][dk] = \
                int(t["ladder_depth_hist"].get(dk, 0)) + 1
            if (len(m) >= 2
                    and not sc.get("both_sides_seen_this_window")):
                sc["both_sides_seen_this_window"] = True
                t["windows_with_both_sides_resting"] += 1
        sc["last_rel"] = str(plan.get("relation") or "")
        rec["queue_ahead_at_placement"] = (
            None if order.queue_ahead is None else str(order.queue_ahead))
        rec["ticks_behind_touch"] = order.ticks_behind_touch()
        return self._out(rec)

    # ------------------------------------------------------- window close
    def window_close(self, slug: str) -> Dict[str, Any]:
        """The window has rolled. Report what each scenario is left holding.

        A resting order does not survive expiry, so every scenario's order
        is dropped -- NOT credited. Whatever inventory a scenario
        accumulated stays on its books, unsettled, until the VENUE prices
        it. Nothing here is marked to an expired book.
        """
        slot = self._slot(slug)
        out = {"provenance": CONTROLLER_PROVENANCE, "market": slug,
               "variant": self.variant,
               "policy": self.policy, "placement": self.placement,
               "fill_rule": FILL_RULE, "scenarios": {},
               "labels": ("STRANDED and PENDING SETTLEMENT, per scenario. "
                          "Matched pairs redeem $1 each whatever the "
                          "outcome; the residual is UNKNOWN until the venue "
                          "states a settlement price.")}
        for scenario in SCENARIOS:
            sc = slot["scen"][scenario]
            inv = self.inventory_for(slug, scenario)
            dropped_map = self._resting_map(sc)
            legacy_dropped = sc.get("resting")
            sc["resting"] = None
            sc["resting_sides"] = None
            t = self._tally(scenario)
            t["windows_observed"] += 1
            for _ in range(max(0, len(dropped_map) - 1)):
                # a two-sided arm can leave TWO unfilled quotes at expiry,
                # and counting one would flatter its fill rate.
                t["unfilled_at_expiry"] += 1
            dropped = legacy_dropped
            if dropped is not None:
                # A quote that never filled and cannot survive expiry. This
                # is the denominator half of the fill rate: counting fills
                # without counting these would read as a 100% fill rate.
                t["unfilled_at_expiry"] += 1
            if not (inv.yes or inv.no):
                # A ZERO-TRADE WINDOW, counted so the per-window average is
                # taken over every window we watched and not only the ones
                # where something happened.
                t["windows_zero_trade"] += 1
            out["scenarios"][scenario] = {
                "inventory": inv.snapshot(),
                "cost_total": str(inv.yes_cost + inv.no_cost),
                "dropped_resting": dropped,
                "settled": bool(sc.get("settled"))}
        self._orders.pop(slug, None)
        self._seen.pop(slug, None)
        self._save()
        return out

    def pending_settlement(self, now: float, *,
                           grace_s: float = 60.0) -> list:
        """Closed windows where ANY scenario still holds inventory, for the
        CALLER to price from the venue.

        This object reads no price and guesses none. Without the venue's own
        number the window stays unsettled and is reported stranded, because
        an expired book's last price reaching a ledger as a payout is a
        defect this programme has already paid for.
        """
        out = []
        for slug, slot in (self.state.get("markets") or {}).items():
            end = slot.get("window_end_ts")
            if end is None or float(now) < float(end) + float(grace_s):
                continue
            for scenario in SCENARIOS:
                sc = (slot.get("scen") or {}).get(scenario) or {}
                if sc.get("settled"):
                    continue
                if int(sc.get("yes") or 0) or int(sc.get("no") or 0):
                    out.append(slug)
                    break
        return sorted(out)

    def settle(self, slug: str, yes_value: D) -> Dict[str, Any]:
        """Each scenario's matched and residual economics, at the VENUE's
        settlement price.

        SEPARATE LINES, NEVER SUMMED. A matched pair redeems $1 whatever
        happens; a residual pays 1 or 0 and is a bet. Collapsing them is
        how a directional loss hides inside a pair profit.

        FEES AND REBATES ARE REPORTED, NOT MODELLED. The PMUS maker fee is
        CONFIRMED $0.0000 across 293 actual fills on this account, so zero
        is a measurement here and not an assumption. The maker rebate is a
        credited line on the venue's schedule, but at q=1 it has never been
        observed to credit anything on this account, so it is reported as
        UNCREDITED rather than accrued: a rebate is not income until the
        venue pays it.
        """
        slot = self._slot(slug)
        out = {"provenance": CONTROLLER_PROVENANCE, "market": slug,
               "variant": self.variant,
               "policy": self.policy, "placement": self.placement,
               "yes_value": str(yes_value),
               "fill_rule": FILL_RULE, "scenarios": {},
               "maker_fee_usd": "0.0000",
               "maker_fee_provenance": ("CONFIRMED zero over 293 actual "
                                        "maker fills on this account"),
               "maker_rebate_usd": "0.0000",
               "maker_rebate_provenance": ("UNCREDITED: never observed to "
                                           "credit at q=1 on this account; "
                                           "not accrued until the venue "
                                           "pays it"),
               "labels": ("MODELLED per scenario under the queue-aware "
                          "model. NOT realized P&L: no order was ever "
                          "submitted, and no fill was ever confirmed.")}
        for scenario in SCENARIOS:
            sc = slot["scen"][scenario]
            inv = self.inventory_for(slug, scenario)
            matched, resid = inv.matched, inv.residual
            cost = inv.yes_cost + inv.no_cost
            redeem = (D(matched) + D(abs(resid))
                      * (yes_value if resid > 0 else (D(1) - yes_value)))
            # SEPARATED, NEVER SUMMED INTO ONE NUMBER. A matched pair
            # redeems $1 whatever happens; the residual is a bet with no
            # demonstrated skill behind it. The AVERAGE-COST basis is used
            # because that is what `complement_target` prices against, so
            # the split and the decisions agree with each other. FIFO
            # re-pairing moves the split and not the total; the total is
            # the number that does not depend on the convention.
            ay = (inv.yes_cost / D(inv.yes)) if inv.yes else D(0)
            an = (inv.no_cost / D(inv.no)) if inv.no else D(0)
            pair_cost = D(matched) * (ay + an)
            resid_cost = D(abs(resid)) * (ay if resid > 0 else an)
            resid_redeem = (D(abs(resid))
                            * (yes_value if resid > 0 else (D(1) - yes_value)))
            line = {"matched_units": matched, "residual_units": resid,
                    "cost_total": str(cost),
                    "redemption_total": str(redeem),
                    "modelled_net": str(redeem - cost),
                    "pair_cost": str(pair_cost),
                    "pair_redemption": str(D(matched)),
                    "pair_net": str(D(matched) - pair_cost),
                    "residual_cost": str(resid_cost),
                    "residual_redemption": str(resid_redeem),
                    "residual_net": str(resid_redeem - resid_cost),
                    "capital_deployed": str(cost),
                    "avg_yes_cost": str(ay), "avg_no_cost": str(an),
                    "window_end_ts": slot.get("window_end_ts"),
                    "basis": "AVERAGE_COST"}
            out["scenarios"][scenario] = line
            sc["settled"] = line
            sc.update(self._fresh_scenario())
            sc["settled"] = line
        self._orders.pop(slug, None)
        self._save()
        return out

    def stranded(self, *, cap: Optional[int] = None) -> Dict[str, Any]:
        """Inventory on the books with no settlement applied, by market, for
        the HEADLINE scenario -- the one promotion is judged on.

        `cap` limits how many are NAMED, for a report that ships in every
        heartbeat. The count is always exact; see `stranded_count`.
        """
        out = {}
        for slug in sorted(self.state.get("markets") or {}):
            slot = (self.state.get("markets") or {})[slug]
            sc = (slot.get("scen") or {}).get(HEADLINE_SCENARIO) or {}
            if sc.get("settled"):
                continue
            inv = self.inventory_for(slug, HEADLINE_SCENARIO)
            if inv.yes or inv.no:
                out[slug] = {"inventory": inv.snapshot(),
                             "cost_total": str(inv.yes_cost + inv.no_cost),
                             "window_end_ts": slot.get("window_end_ts")}
        if cap is not None and len(out) > cap:
            keep = sorted(out)[-cap:]
            trimmed = {k: out[k] for k in keep}
            trimmed["_not_shown"] = len(out) - cap
            return trimmed
        return out

    def stranded_count(self) -> int:
        return len(self.stranded())

    def economics(self) -> Dict[str, Any]:
        """MODELLED economics across settled windows, PER SCENARIO.

        Conservative is reported beside optimistic and never merged with
        it: the spread between them is the honest width of our ignorance
        about queue position, and a single blended number would hide it.
        """
        carry = self.state.get("carry") or {}
        per = {}
        for scenario in SCENARIOS:
            c = carry.get(scenario) or {}
            tot = {"windows_settled": int(c.get("windows") or 0),
                   "cost": D(str(c.get("cost_total") or 0)),
                   "redeem": D(str(c.get("redemption_total") or 0)),
                   "matched": int(c.get("matched_units") or 0),
                   "resid": int(c.get("residual_units_abs") or 0)}
            for slot in (self.state.get("markets") or {}).values():
                line = ((slot.get("scen") or {}).get(scenario)
                        or {}).get("settled")
                if not line:
                    continue
                tot["windows_settled"] += 1
                tot["cost"] += D(str(line.get("cost_total") or 0))
                tot["redeem"] += D(str(line.get("redemption_total") or 0))
                tot["matched"] += int(line.get("matched_units") or 0)
                tot["resid"] += abs(int(line.get("residual_units") or 0))
            # PER-WINDOW LINES, EVERY SETTLED WINDOW, ordered by the
            # venue's own window end. Not a sample and not the favourable
            # ones: the list is every window still held in state, and
            # `windows_folded_into_carry` says how many older ones are
            # represented only by their totals.
            lines = []
            for slug, slot in (self.state.get("markets") or {}).items():
                ln = ((slot.get("scen") or {}).get(scenario)
                      or {}).get("settled")
                if ln:
                    lines.append((float(slot.get("window_end_ts") or 0.0),
                                  slug, ln))
            lines.sort()
            # A WINDOW SETTLED BEFORE THE SPLIT EXISTED HAS NO SPLIT, and
            # saying 0.0000 for it would be a missing value wearing the
            # costume of a measurement. The first production report did
            # exactly that: seven of eight windows read pair=0.0000, which
            # invited the conclusion that pair economics contributed
            # nothing, when the truth was that nobody had recorded them.
            per_window = []
            no_split = 0
            for _, slug, ln in lines:
                has = ln.get("pair_net") is not None
                no_split += 0 if has else 1
                per_window.append({
                    "market": slug,
                    "net": str(D(str(ln.get("modelled_net") or 0))),
                    "pair_net": (str(D(str(ln["pair_net"]))
                                     .quantize(D("0.0001"))) if has
                                 else "NOT_RECORDED"),
                    "residual_net": (str(D(str(ln.get("residual_net") or 0))
                                         .quantize(D("0.0001"))) if has
                                     else "NOT_RECORDED"),
                    "capital_deployed": str(D(str(ln.get("cost_total") or 0))),
                    "matched_units": int(ln.get("matched_units") or 0),
                    "residual_units": int(ln.get("residual_units") or 0)})
            # DRAWDOWN over the settled sequence. Peak-to-trough of the
            # cumulative modelled net, which is what a run of losing
            # windows would cost before any recovery.
            run = D(str(c.get("pair_net") or 0)) + D(str(c.get("residual_net")
                                                         or 0))
            peak, dd = run, D(0)
            for w in per_window:
                run += D(w["net"])
                peak = max(peak, run)
                dd = max(dd, peak - run)
            split = [w for w in per_window if w["pair_net"] != "NOT_RECORDED"]
            pair_net = D(str(c.get("pair_net") or 0)) + sum(
                (D(w["pair_net"]) for w in split), D(0))
            resid_net = D(str(c.get("residual_net") or 0)) + sum(
                (D(w["residual_net"]) for w in split), D(0))
            t = (self.state.get("tally") or {}).get(scenario) or {}
            quotes = int(t.get("quotes_placed") or 0)
            fills = int(t.get("fills") or 0)
            nfill = fills + int(t.get("unfilled_at_expiry") or 0)
            refusals = (int(t.get("justified_refusals") or 0)
                        + int(t.get("possible_misses") or 0))
            net_total = tot["redeem"] - tot["cost"]
            per[scenario] = {
                "windows_settled": tot["windows_settled"],
                "windows_folded_into_carry": int(c.get("windows") or 0),
                "per_window": per_window,
                "windows_observed": int(t.get("windows_observed") or 0),
                "windows_zero_trade": int(t.get("windows_zero_trade") or 0),
                # quantized: an average cost is a repeating decimal and a
                # P&L line with 27 places reads as precision it has not got
                "pair_net": str(pair_net.quantize(D("0.0001"))),
                "residual_net": str(resid_net.quantize(D("0.0001"))),
                # the split totals cover only the windows that HAVE a
                # split; this says how many they leave out, so the pair and
                # residual lines can never be read as covering every
                # settled window when they do not
                "split_covers_windows": len(split),
                "windows_without_split": no_split,
                "max_drawdown": str(dd),
                "capital_deployed_total": str(tot["cost"]),
                "capital_efficiency": (
                    None if tot["cost"] == 0
                    else str((net_total / tot["cost"]).quantize(D("0.0001")))),
                "net_per_settled_window": (
                    None if not tot["windows_settled"]
                    else str((net_total / D(tot["windows_settled"]))
                             .quantize(D("0.0001")))),
                "execution": {
                    "counters_since": self.state.get("counters_since"),
                    "counters_cover_all_settled_windows": (
                        int(t.get("windows_observed") or 0)
                        >= tot["windows_settled"]),
                    "quotes_placed": quotes,
                    "filled": fills,
                    "unfilled_at_expiry": int(t.get("unfilled_at_expiry")
                                              or 0),
                    "fill_rate": (None if not nfill
                                  else str((D(fills) / D(nfill))
                                           .quantize(D("0.0001")))),
                    "adverse_fills": int(t.get("adverse_fills") or 0),
                    "adverse_rate": (None if not fills
                                     else str((D(int(t.get("adverse_fills")
                                                     or 0)) / D(fills))
                                              .quantize(D("0.0001")))),
                    "adverse_usd": str(t.get("adverse_usd") or "0"),
                    "swept_fills": int(t.get("swept_fills") or 0),
                    # a two-sided arm can fill both sides in one tick; on a
                    # one-sided arm this is structurally always 0
                    "both_sides_filled_same_tick": int(
                        t.get("both_sides_filled_same_tick") or 0),
                    "controller_errors": int(t.get("controller_errors") or 0)},
                "opportunities": {
                    "justified_refusals": int(t.get("justified_refusals")
                                              or 0),
                    "possible_misses": int(t.get("possible_misses") or 0),
                    "possible_miss_rate": (
                        None if not refusals
                        else str((D(int(t.get("possible_misses") or 0))
                                  / D(refusals)).quantize(D("0.0001")))),
                    "reasons": dict(t.get("refusal_reasons") or {}),
                    # REQUIREMENT 1. These are counted at the tally's top
                    # level, NOT inside `refusal_reasons`, because the reason
                    # STRING is deliberately unchanged -- so they have to be
                    # lifted here explicitly or they are counted and never
                    # reported, which is exactly what happened: the first
                    # version of the comparison read them out of `reasons`
                    # and would have printed three zeros forever.
                    "already_resting_split": {k: int(t.get(k) or 0)
                                              for k in RESTING_SPLIT},
                    # the denominators a RATE can be read against; the
                    # per-tick split above measures dwell, not opportunity
                    "already_resting_opposite_side_episodes": int(
                        t.get("already_resting_opposite_side_episodes") or 0),
                    "windows_with_opposite_side_want": int(
                        t.get("windows_with_opposite_side_want") or 0),
                    "ladder_second_legs_quoted": int(
                        t.get("ladder_second_legs_quoted") or 0),
                    "windows_with_both_sides_resting": int(
                        t.get("windows_with_both_sides_resting") or 0),
                    "ladder_depth_hist": dict(
                        t.get("ladder_depth_hist") or {}),
                    "already_resting_total": int(
                        (t.get("refusal_reasons") or {})
                        .get("already_resting") or 0),
                    "labels": ("a JUSTIFIED refusal protects money or admits "
                               "ignorance; a POSSIBLE MISS is the only "
                               "bucket that can hold forgone profit. "
                               "Neither is a loss until priced. The "
                               "already_resting SPLIT says how many of "
                               "those refusals wanted the side we were not "
                               "resting, which is the only bucket a "
                               "two-sided arm could have acted on.")},
                "data_completeness": {
                    "records": int(t.get("records") or 0),
                    "ticks_with_book": int(t.get("ticks_with_book") or 0),
                    "ticks_without_book": int(t.get("ticks_without_book")
                                              or 0),
                    "fills_with_known_queue": int(t.get("queue_known_fills")
                                                  or 0),
                    "fills_with_unknown_queue": int(
                        t.get("queue_unknown_fills") or 0),
                    "labels": ("an unknown queue can only fill on a sweep, "
                               "so the known/unknown split is the width of "
                               "our ignorance about execution")},
                "matched_units": tot["matched"],
                "residual_units_abs": tot["resid"],
                "cost_total": str(tot["cost"]),
                "redemption_total": str(tot["redeem"]),
                "modelled_net": str(tot["redeem"] - tot["cost"])}
        return {"per_scenario": per,
                "headline_scenario": HEADLINE_SCENARIO,
                "headline_net": per[HEADLINE_SCENARIO]["modelled_net"],
                "stranded_markets": self.stranded_count(),
                "fill_rule": FILL_RULE,
                "labels": ("MODELLED by the queue-aware model from venue "
                           "settlement prices. NOT realized, NOT filled, "
                           "NOT submitted. Promotion is judged on "
                           "%s." % HEADLINE_SCENARIO)}

    # ---------------------------------------------------------- reporting
    # ------------------------------------------------- execution quality
    @staticmethod
    def _fresh_tally() -> Dict[str, Any]:
        return {"records": 0, "quotes_placed": 0, "fills": 0,
                "unfilled_at_expiry": 0,
                "justified_refusals": 0, "possible_misses": 0,
                "refusal_reasons": {}, "controller_errors": 0,
                "adverse_fills": 0, "adverse_usd": "0",
                "queue_known_fills": 0, "queue_unknown_fills": 0,
                "swept_fills": 0,
                "ticks_with_book": 0, "ticks_without_book": 0,
                "windows_observed": 0, "windows_zero_trade": 0,
                # REQUIREMENT 1: split `already_resting` into the refusals a
                # two-sided arm could actually have acted on and the ones it
                # could not. The reason string is deliberately UNCHANGED, so
                # TALLY_TAXONOMY stays at 2 and the bucket remains
                # comparable across this change.
                "already_resting_opposite_side": 0,
                "already_resting_same_side": 0,
                "already_resting_side_unknown": 0,
                # ... and the denominators a RATE can be read against, since
                # the per-tick counts above measure dwell, not opportunity
                "already_resting_opposite_side_episodes": 0,
                "windows_with_opposite_side_want": 0,
                # the ladder's own second legs, and the windows in which it
                # actually held two sides at once
                "ladder_second_legs_quoted": 0,
                "windows_with_both_sides_resting": 0,
                # BY DEPTH, because the pair bound often puts the second leg
                # DEEPER than the control's single leg (d=2 against d=1 at a
                # one-tick spread). A lower fill rate on a deeper leg is
                # arithmetic, not a finding, and without this the two could
                # not be told apart.
                "ladder_depth_hist": {},
                # a two-sided arm can fill both sides in one tick
                "both_sides_filled_same_tick": 0}

    def _tally(self, scenario: str) -> Dict[str, Any]:
        t = self.state.setdefault("tally", {})
        if not self.state.get("counters_since"):
            # stamped once, when the counters begin. Reported beside them so
            # a fill rate over three quotes is never mistaken for the fill
            # rate of the eight settled windows sitting next to it.
            self.state["counters_since"] = _utc_now()
        cur = t.setdefault(scenario, self._fresh_tally())
        for k, v in self._fresh_tally().items():       # forward-compatible
            cur.setdefault(k, v)
        return cur

    def _tally_record(self, d: Mapping[str, Any]) -> None:
        """Tally ONE decision record, at the single funnel every record
        passes through, so no branch can be missed by being forgotten."""
        scen = d.get("scenario")
        if scen not in SCENARIOS:
            return
        t = self._tally(scen)
        t["records"] += 1
        reason = str(d.get("reason") or "")
        if reason == "no_two_sided_book":
            t["ticks_without_book"] += 1
        else:
            t["ticks_with_book"] += 1
        if reason.startswith("controller_error"):
            t["controller_errors"] += 1
        elif d.get("decision") == "quote":
            t["quotes_placed"] += 1
        elif (reason in JUSTIFIED_REFUSALS
              or reason.startswith(JUSTIFIED_REFUSAL_PREFIXES)):
            t["justified_refusals"] += 1
            t["refusal_reasons"][reason] = \
                int(t["refusal_reasons"].get(reason, 0)) + 1
        else:
            # The model had somewhere to go and no protective reason fired.
            # This is the only bucket that can hold forgone profit.
            t["possible_misses"] += 1
            key = reason or "policy_declined"
            t["refusal_reasons"][key] = \
                int(t["refusal_reasons"].get(key, 0)) + 1
        side_diag = d.get("already_resting_side")
        if side_diag == "OPPOSITE_SIDE":
            t["already_resting_opposite_side"] += 1
        elif side_diag == "SAME_SIDE":
            t["already_resting_same_side"] += 1
        elif side_diag == "SIDE_UNKNOWN":
            t["already_resting_side_unknown"] += 1
        # COUNT EVERY FILL, not just the first. On a one-sided arm the list
        # holds 0 or 1 and this is identical to what it replaced.
        many = d.get("modelled_fills")
        fills = [x for x in (many if isinstance(many, list) else [])
                 if isinstance(x, Mapping)]
        if not fills and isinstance(d.get("modelled_fill"), Mapping):
            fills = [d["modelled_fill"]]
        if len(fills) > 1:
            t["both_sides_filled_same_tick"] += 1
        for f in fills:
            t["fills"] += 1
            if f.get("swept"):
                t["swept_fills"] += 1
            if f.get("queue_ahead_at_placement") in (None, ""):
                t["queue_unknown_fills"] += 1
            else:
                t["queue_known_fills"] += 1
            adv = f.get("adverse_selection") or {}
            if isinstance(adv, Mapping):
                if adv.get("adverse"):
                    t["adverse_fills"] += 1
                try:
                    t["adverse_usd"] = str(D(t["adverse_usd"])
                                           + D(str(adv.get("moved_against_us")
                                                   or 0)))
                except (ArithmeticError, ValueError, TypeError):
                    pass

    def _out(self, rec: Mapping[str, Any]) -> Dict[str, Any]:
        d = dict(rec)
        scen = d.get("scenario")
        key = "%s|%s" % (scen or "-",
                         str(d.get("relation") or d.get("decision")
                             or "?").upper())
        self.counts[key] = self.counts.get(key, 0) + 1
        try:
            self._tally_record(d)
        except BaseException:                              # noqa: BLE001
            pass          # a counter must never cost a decision
        # DECISIONS COUNTS TICKS, not scenario-records. Every tick produces
        # one record per scenario; counting all of them would treble the
        # headline number for no gain. The per-scenario detail is in
        # `counts`, keyed by scenario.
        self.records += 1
        if scen is None or scen == HEADLINE_SCENARIO:
            self.decisions += 1
        if self._emit is not None:
            try:
                self._emit("L5_SHADOW_DECISION", d)
            except BaseException:                           # noqa: BLE001
                pass
        return d

    def discontinuity(self) -> Optional[Dict[str, Any]]:
        """Any recorded break in durable simulated inventory, or None.

        Reported on every heartbeat rather than only at the boot that
        caused it: a discontinuity an operator has to catch in one log line
        is one that gets missed.
        """
        d = self.state.get("discontinuity")
        return dict(d) if isinstance(d, Mapping) else None

    def outstanding(self) -> Dict[str, Any]:
        """EVERY outstanding shadow leg, and what it obliges if it fills.

        REQUIREMENT 4. A two-sided arm can have BOTH sides resting, so a
        figure that reports one of them understates the obligation by the
        other -- and the arm that introduces the second leg is exactly the
        one whose exposure must not be read off the first.

        Three numbers per scenario, because they answer different
        questions:

          cash_if_all_fill   every resting leg fills: the cash that leaves.
                             This is the obligation, and for a ladder it is
                             BOTH legs, not the larger of them.
          worst_residual     the imbalance if the WORST single side fills
                             and the other does not -- the ladder's real
                             hazard, since a filled pair is flat and a
                             half-filled ladder is not.
          worst_residual_if_all_fill
                             the imbalance if everything fills, which for a
                             balanced ladder is SMALLER than one side
                             filling alone. Reported so the two cannot be
                             confused.

        Held inventory is included, because an obligation on top of
        existing inventory is what the residual cap exists to bound.
        MODELLED: these are hypothetical legs, never submitted orders.
        """
        out: Dict[str, Any] = {}
        for scenario in SCENARIOS:
            legs = []
            cash = D(0)
            for slug in list(self._orders):
                for side, order in (self._orders_for(slug, scenario)
                                    or {}).items():
                    # OWN-OUTCOME cost, which is what we actually pay per
                    # contract. `wire_px` is the price it rests at on the
                    # wire and for a DOWN leg the two differ (wire = 1 -
                    # own), so summing wire prices would report a DOWN
                    # leg's obligation as 1 - its cost.
                    px = _d(getattr(order, "own_px", None))
                    qty = _d(getattr(order, "qty", None)) or D(1)
                    if px is None:
                        continue
                    legs.append({"market": slug, "side": str(side),
                                 "own_px": str(px), "qty": str(qty),
                                 "wire_px": str(
                                     getattr(order, "wire_px", px))})
                    cash += px * qty
            # inventory the legs land on top of
            resid = 0
            for slug in list(self._orders):
                try:
                    resid += int(self.inventory_for(slug, scenario)
                                 .snapshot().get("residual") or 0)
                except Exception:                          # noqa: BLE001
                    pass
            up = sum((_d(l["qty"]) or D(0) for l in legs
                      if l["side"] == str(FM.UP)), D(0))
            dn = sum((_d(l["qty"]) or D(0) for l in legs
                      if l["side"] == str(FM.DOWN)), D(0))
            out[scenario] = {
                "legs": legs,
                "legs_resting": len(legs),
                "cash_if_all_fill": str(cash),
                "held_residual": resid,
                "worst_residual": str(max(
                    abs(D(resid) + up), abs(D(resid) - dn))),
                "worst_residual_if_all_fill": str(abs(D(resid) + up - dn)),
                "labels": ("MODELLED hypothetical legs, never submitted. "
                           "cash_if_all_fill counts EVERY resting leg, so a "
                           "two-sided arm's obligation is not read off one "
                           "side. worst_residual is the single worst side "
                           "filling alone, which for a balanced ladder is "
                           "LARGER than everything filling.")}
        return out

    def report(self, compact: bool = False) -> Dict[str, Any]:
        """The arm's full state. `compact` drops the per-window lists.

        WHY COMPACT EXISTS. The per-window lists are 96% of this document:
        one arm with 369 settled windows reports 212,565 characters of
        which 204,795 are per_window, so six arms on a once-a-minute
        heartbeat is 1.28 MB a minute -- about 1.8 GB of logs a day, for
        rows that are already durable in the state file and already
        aggregated into `L5_SHADOW_TWO_SIDED`. Compact is 67 MB a day.

        NOTHING IS LOST, and the count of what was omitted is reported so
        a reader can never mistake a compact document for an arm that has
        settled no windows. The comparison itself always gets the FULL
        report -- it is computed in-process and never round-trips through
        a log -- because it restricts to windows both arms settled and
        needs the per-window rows to do it.
        """
        out = self._report_full()
        if not compact:
            return out
        for scen in (out.get("economics") or {}).get("per_scenario", {}
                                                     ).values():
            rows = scen.pop("per_window", None)
            scen["per_window_omitted"] = len(rows or [])
            scen["per_window_note"] = (
                "omitted from this COMPACT report; durable in the state "
                "file and aggregated in L5_SHADOW_TWO_SIDED")
        out["compact"] = True
        return out

    def _report_full(self) -> Dict[str, Any]:
        return {"provenance": CONTROLLER_PROVENANCE,
                "policy": self.policy,
                "decisions": self.decisions,
                "scenario_records": self.records,
                "counts": dict(self.counts),
                "markets": len(self.state.get("markets") or {}),
                "pair_cap": str(self.pair_cap),
                "variant": self.variant,
                "placement": self.placement,
                "offset_ticks": self.offset_ticks,
                "fill_rule": FILL_RULE,
                "state_path": self.state_path,
                "scenarios": list(SCENARIOS),
                "headline_scenario": HEADLINE_SCENARIO,
                "economics": self.economics(),
                "stranded": self.stranded(cap=STRANDED_REPORT_CAP),
                "state_discontinuity": self.discontinuity(),
                "boot": self.state.get("boot"),
                "tally_reset": self.state.get("tally_reset"),
                "two_sided": self.two_sided,
                "ladder_from_flat": self.ladder_from_flat,
                "outstanding": self.outstanding(),
                "resting_now": {sc: sorted(
                    ((self._orders.get(k) or {}).get(sc) or {}))
                    for sc in SCENARIOS
                    for k in list(self._orders)[:1]} or None,
                "labels": ("independent inventory-driven decision source; "
                           "holds no transport and defines no order path")}
