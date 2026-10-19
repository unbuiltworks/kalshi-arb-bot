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
from typing import Any, Callable, Dict, Mapping, Optional

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
JUSTIFIED_REFUSALS = (
    "no_two_sided_book", "v2_off_distribution",
    "would_cross_not_a_maker_quote", "already_resting",
)


class ShadowController:
    """One inventory-driven decision per tick, for one market at a time."""

    def __init__(self, *, state_path: str,
                 model: Optional[Mapping[str, Any]] = None,
                 pair_cap: Optional[D] = None,
                 policy: str = BASELINE_POLICY,
                 emit: Optional[Callable[[str, Mapping[str, Any]], None]] = None
                 ) -> None:
        self.state_path = str(state_path)
        if policy not in POLICIES:
            raise ValueError("unknown policy: %r" % (policy,))
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
        self.state: Dict[str, Any] = {"v": STATE_VERSION, "markets": {}}
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
            for k in ("carry", "discontinuity"):
                if isinstance(doc.get(k), Mapping):
                    self.state[k] = dict(doc[k])
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

    # ---------------------------------------------------------- inventory
    @staticmethod
    def _fresh_scenario() -> Dict[str, Any]:
        return {"yes": 0, "no": 0, "yes_cost": "0", "no_cost": "0",
                "last_rel": "", "resting": None, "settled": None}

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
        order = (self._orders.get(slug) or {}).get(scenario)
        if order is None:
            return None
        if prints:
            order.advance(prints)
        if not order.filled.get(scenario):
            return None
        sc = self._scen(slug, scenario)
        inv = self.inventory_for(slug, scenario)
        inv.record(order.side == FM.UP, order.own_px)
        self._store_inventory(slug, inv, scenario)
        rest = sc.get("resting") or {}
        sc["resting"] = None
        sc["last_rel"] = str(rest.get("relation") or sc.get("last_rel") or "")
        self._orders.setdefault(slug, {})[scenario] = None
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
                "policy": self.policy,
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
        fill = self._advance_resting(slug, scenario, book, fresh)
        sc = self._scen(slug, scenario)
        inv = self.inventory_for(slug, scenario)
        rec = {**base, "scenario": scenario,
               "tte_s": round(float(tte), 1),
               "inventory": inv.snapshot(),
               "window_end_ts": window_end_ts,
               "modelled_fill": fill}
        prop = l5d._proposal(self.model, float(book["bid"]), inv,
                             str(sc.get("last_rel") or ""), tte_s=tte)
        rec["state"] = prop.get("state")
        if prop.get("off_distribution"):
            return self._out({**rec, "decision": "no_action",
                              "reason": "v2_off_distribution"})
        # ONE OUTSTANDING ORDER PER SCENARIO, as live allows one live order.
        # A scenario already resting does not re-quote: re-pricing a
        # resting maker order toward a newer price is chasing under another
        # name, and the deployed engine has no method for it.
        if (self._orders.get(slug) or {}).get(scenario) is not None:
            return self._out({**rec, "decision": "no_action",
                              "reason": "already_resting"})
        rel = self.policy_fn(prop, inv, book, tte)
        rec["model_argmax"] = prop.get("recommended_relation")
        plan = l5d.plan_action(rel, inv, book, pair_cap=self.pair_cap)
        rec = {**rec, **l5d._public(plan)}
        if plan.get("decision") != "quote":
            return self._out(rec)
        order = FM.HypoOrder(
            slug, FM.UP if plan["_side_yes"] else FM.DOWN,
            plan["_px"], D(1), float(now), ladder=ladder,
            bid=book.get("bid"), ask=book.get("ask"),
            coid="%s|%s|%s" % (self.policy, scenario, now))
        # A PASSIVE ORDER THAT WOULD CROSS IS NOT A MAKER ORDER. The model
        # answers that directly, and a non-restable quote is refused rather
        # than modelled as if it had rested.
        if not order.restable:
            return self._out({**rec, "decision": "no_action",
                              "relation": plan.get("relation"),
                              "reason": "would_cross_not_a_maker_quote"})
        self._orders.setdefault(slug, {})[scenario] = order
        sc["resting"] = {"side": plan["side"], "price": str(plan["_px"]),
                         "relation": plan.get("relation"), "t": now}
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
               "policy": self.policy, "fill_rule": FILL_RULE,
               "scenarios": {},
               "labels": ("STRANDED and PENDING SETTLEMENT, per scenario. "
                          "Matched pairs redeem $1 each whatever the "
                          "outcome; the residual is UNKNOWN until the venue "
                          "states a settlement price.")}
        for scenario in SCENARIOS:
            sc = slot["scen"][scenario]
            inv = self.inventory_for(slug, scenario)
            dropped, sc["resting"] = sc.get("resting"), None
            t = self._tally(scenario)
            t["windows_observed"] += 1
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
               "policy": self.policy, "yes_value": str(yes_value),
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
            per_window = [
                {"market": slug,
                 "net": str(D(str(ln.get("modelled_net") or 0))),
                 "pair_net": str(D(str(ln.get("pair_net") or 0))
                                 .quantize(D("0.0001"))),
                 "residual_net": str(D(str(ln.get("residual_net") or 0))
                                     .quantize(D("0.0001"))),
                 "capital_deployed": str(D(str(ln.get("cost_total") or 0))),
                 "matched_units": int(ln.get("matched_units") or 0),
                 "residual_units": int(ln.get("residual_units") or 0)}
                for _, slug, ln in lines]
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
            pair_net = D(str(c.get("pair_net") or 0)) + sum(
                (D(w["pair_net"]) for w in per_window), D(0))
            resid_net = D(str(c.get("residual_net") or 0)) + sum(
                (D(w["residual_net"]) for w in per_window), D(0))
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
                    "labels": ("a JUSTIFIED refusal protects money or admits "
                               "ignorance; a POSSIBLE MISS is the only "
                               "bucket that can hold forgone profit. "
                               "Neither is a loss until priced.")},
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
                "windows_observed": 0, "windows_zero_trade": 0}

    def _tally(self, scenario: str) -> Dict[str, Any]:
        t = self.state.setdefault("tally", {})
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
        elif reason in JUSTIFIED_REFUSALS:
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
        f = d.get("modelled_fill")
        if isinstance(f, Mapping):
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

    def report(self) -> Dict[str, Any]:
        return {"provenance": CONTROLLER_PROVENANCE,
                "policy": self.policy,
                "decisions": self.decisions,
                "scenario_records": self.records,
                "counts": dict(self.counts),
                "markets": len(self.state.get("markets") or {}),
                "pair_cap": str(self.pair_cap),
                "fill_rule": FILL_RULE,
                "state_path": self.state_path,
                "scenarios": list(SCENARIOS),
                "headline_scenario": HEADLINE_SCENARIO,
                "economics": self.economics(),
                "stranded": self.stranded(cap=STRANDED_REPORT_CAP),
                "state_discontinuity": self.discontinuity(),
                "boot": self.state.get("boot"),
                "resting_now": {sc: bool((self._orders.get(k) or {}).get(sc))
                                for sc in SCENARIOS
                                for k in list(self._orders)[:1]} or None,
                "labels": ("independent inventory-driven decision source; "
                           "holds no transport and defines no order path")}
